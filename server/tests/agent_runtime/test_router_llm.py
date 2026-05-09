"""Unit + property tests for :class:`RouterLLM`.

Spec: .kiro/specs/agent-runtime-optimization-evolution, tasks 13.3–13.5 /
R-1.3 / R-1.4 / R-1.9 / R-10.4 / R-10.5 / R-10.6.

The tests use:

* ``fakeredis.aioredis.FakeRedis(decode_responses=True)`` for the cache
  layer so nothing needs a running Redis.
* A hand-rolled :class:`_FakeLLM` with explicit ``bind_tools`` /
  ``with_structured_output`` surfaces so we can simulate each tier
  independently without mocking LangChain internals.

All ``classify`` callers rely on the invariant: *it never raises* —
the bulk of the suite is therefore about exercising the three
fallback tiers and asserting the resulting decision + metric shape.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import fakeredis.aioredis
import pytest
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st

from src.core.metrics import router_path_total, router_timeout_total
from src.services.agent_runtime.router import (
    CONFIDENCE_FLOOR,
    RouterLLM,
    _reset_singleton_for_tests,
)
from src.services.agent_runtime.router_schema import (
    OPS_KEYWORDS,
    RouterDecision,
    promote_if_ops_keyword,
)


# ---------------------------------------------------------------------------
# Fake LLM
# ---------------------------------------------------------------------------


def _aimessage(tool_args: dict[str, Any] | None) -> SimpleNamespace:
    """Return a minimal AIMessage-like object with ``tool_calls``.

    ``tool_args=None`` → empty ``tool_calls`` (simulates a model that
    refused to invoke the tool). A non-None dict → a single tool call
    named ``decide`` carrying those args.
    """
    if tool_args is None:
        return SimpleNamespace(tool_calls=[])
    return SimpleNamespace(
        tool_calls=[{"name": "decide", "args": dict(tool_args), "id": "call-1"}]
    )


class _FakeLLM:
    """Minimal LangChain-ChatModel stand-in used by every test.

    Each tier gets its own knob:

    * ``fc_response`` / ``fc_side_effect`` / ``fc_delay_s`` — Tier 1
      (function calling via ``bind_tools``).
    * ``json_response`` / ``json_side_effect`` / ``json_delay_s`` —
      Tier 2 (``with_structured_output(method="json_mode")``).

    The ``.ainvoke_calls`` counter lets tests assert that the second
    ``classify`` of a cached query did NOT hit the LLM (task 13.3).
    """

    def __init__(
        self,
        *,
        fc_response: Any | None = None,
        fc_side_effect: BaseException | None = None,
        fc_delay_s: float = 0.0,
        json_response: Any | None = None,
        json_side_effect: BaseException | None = None,
        json_delay_s: float = 0.0,
    ) -> None:
        self._fc_response = fc_response
        self._fc_side_effect = fc_side_effect
        self._fc_delay_s = float(fc_delay_s)
        self._json_response = json_response
        self._json_side_effect = json_side_effect
        self._json_delay_s = float(json_delay_s)
        self.ainvoke_calls = 0  # total across both surfaces

    def bind_tools(self, _tools, tool_choice=None):  # noqa: ARG002
        outer = self

        class _Bound:
            async def ainvoke(self, _messages):  # noqa: ARG002
                outer.ainvoke_calls += 1
                if outer._fc_delay_s > 0:
                    await asyncio.sleep(outer._fc_delay_s)
                if outer._fc_side_effect is not None:
                    raise outer._fc_side_effect
                return outer._fc_response

        return _Bound()

    def with_structured_output(self, _schema, method=None):  # noqa: ARG002
        outer = self

        class _Structured:
            async def ainvoke(self, _messages):  # noqa: ARG002
                outer.ainvoke_calls += 1
                if outer._json_delay_s > 0:
                    await asyncio.sleep(outer._json_delay_s)
                if outer._json_side_effect is not None:
                    raise outer._json_side_effect
                return outer._json_response

        return _Structured()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def _path_count(path: str) -> float:
    try:
        return float(router_path_total.labels(path=path)._value.get())
    except Exception:  # pragma: no cover - defensive
        return 0.0


def _timeout_count() -> float:
    try:
        return float(router_timeout_total._value.get())
    except Exception:  # pragma: no cover - defensive
        return 0.0


def _valid_direct_args() -> dict[str, Any]:
    return {
        "route": "direct",
        "direct_answer": "你好，有什么可以帮你？",
        "subagent_name": None,
        "suggested_tools": [],
        "reason": "greeting",
        "confidence": 0.9,
    }


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure each test starts from a clean module-level state."""
    _reset_singleton_for_tests()
    yield
    _reset_singleton_for_tests()


# ---------------------------------------------------------------------------
# 13.3 — P-Router-1 idempotency (cache hit skips LLM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_second_call_does_not_invoke_llm():
    """Two identical calls within the TTL → second hits the cache only."""
    redis = _make_fake_redis()
    llm = _FakeLLM(fc_response=_aimessage(_valid_direct_args()))
    router = RouterLLM(
        llm=llm,
        redis_client=redis,
        skill_index_fn=lambda: "",
    )

    before_cache = _path_count("cache")
    before_fc = _path_count("function_calling")

    # First call — populates the cache.
    d1 = await router.classify(
        "你好啊", user_id="user-42", last_assistant_sha="sha-hello"
    )
    # Second call with the SAME (message, user_id, last_assistant_sha).
    d2 = await router.classify(
        "你好啊", user_id="user-42", last_assistant_sha="sha-hello"
    )

    assert d1.route == "direct"
    assert d2.route == "direct"
    assert d1.direct_answer == d2.direct_answer

    # Exactly one LLM invocation across both classify calls.
    assert llm.ainvoke_calls == 1
    # One function_calling increment, one cache increment.
    assert _path_count("function_calling") - before_fc == 1
    assert _path_count("cache") - before_cache == 1


# ---------------------------------------------------------------------------
# 13.4 — P-Router-2 degradation safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_timeout_falls_back_to_executor():
    """A Tier-1 timeout > 500 ms budget falls back to the executor."""
    redis = _make_fake_redis()
    llm = _FakeLLM(
        fc_delay_s=1.0,  # well beyond the 500ms budget
        fc_response=_aimessage(_valid_direct_args()),
    )
    router = RouterLLM(
        llm=llm,
        redis_client=redis,
        timeout_ms=200,  # tighter budget → faster test
        skill_index_fn=lambda: "",
    )

    before_timeout = _timeout_count()
    before_fallback = _path_count("fallback_executor")

    decision = await router.classify(
        "帮我查一下主库延迟", user_id="u-1", last_assistant_sha=""
    )

    assert decision.route == "executor"
    assert decision.suggested_tools == []
    assert decision.confidence == 0.0
    assert "timeout" in decision.reason

    # Timeout must have been counted AND the fallback path recorded.
    assert _timeout_count() > before_timeout
    assert _path_count("fallback_executor") - before_fallback == 1


@pytest.mark.asyncio
async def test_router_function_calling_parse_error_falls_back():
    """Empty ``tool_calls`` → Tier 2; Tier 2 errors → fallback."""
    redis = _make_fake_redis()
    llm = _FakeLLM(
        fc_response=_aimessage(None),  # empty tool_calls → parse_error
        json_side_effect=RuntimeError("json mode blew up"),
    )
    router = RouterLLM(
        llm=llm,
        redis_client=redis,
        timeout_ms=500,
        skill_index_fn=lambda: "",
    )

    before_fallback = _path_count("fallback_executor")

    decision = await router.classify(
        "部署一下 redis", user_id="u-2", last_assistant_sha=""
    )

    assert decision.route == "executor"
    assert decision.confidence == 0.0
    # Both tiers were attempted.
    assert llm.ainvoke_calls == 2
    assert _path_count("fallback_executor") - before_fallback == 1


@pytest.mark.asyncio
async def test_router_llm_exception_falls_back():
    """Arbitrary exceptions in both tiers → fallback, never raised."""
    redis = _make_fake_redis()
    llm = _FakeLLM(
        fc_side_effect=RuntimeError("provider refused"),
        json_side_effect=RuntimeError("provider refused"),
    )
    router = RouterLLM(
        llm=llm,
        redis_client=redis,
        timeout_ms=500,
        skill_index_fn=lambda: "",
    )

    before_fallback = _path_count("fallback_executor")

    decision = await router.classify("say hi", user_id="u-3")

    assert decision.route == "executor"
    assert decision.confidence == 0.0
    assert _path_count("fallback_executor") - before_fallback == 1


@pytest.mark.asyncio
async def test_low_confidence_forces_executor():
    """A valid decision with ``confidence < 0.4`` is promoted to executor."""
    redis = _make_fake_redis()
    low_conf_args = {
        "route": "direct",
        "direct_answer": "大概吧",
        "subagent_name": None,
        "suggested_tools": [],
        "reason": "unsure",
        "confidence": 0.2,
    }
    llm = _FakeLLM(fc_response=_aimessage(low_conf_args))
    router = RouterLLM(llm=llm, redis_client=redis, skill_index_fn=lambda: "")

    decision = await router.classify("hmm", user_id="u-lc")

    assert decision.route == "executor"
    assert decision.direct_answer is None
    assert decision.suggested_tools == []
    # The original low-confidence value is preserved for diagnostics.
    assert decision.confidence < CONFIDENCE_FLOOR


# ---------------------------------------------------------------------------
# 13.5 — P-Router-3 direct-route-no-tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_route_preserves_direct_answer():
    """A plain greeting stays on the direct route with its answer intact."""
    redis = _make_fake_redis()
    llm = _FakeLLM(fc_response=_aimessage(_valid_direct_args()))
    router = RouterLLM(llm=llm, redis_client=redis, skill_index_fn=lambda: "")

    decision = await router.classify("你好", user_id="u-g")

    assert decision.route == "direct"
    assert decision.direct_answer == "你好，有什么可以帮你？"
    assert decision.suggested_tools == []


@pytest.mark.asyncio
async def test_ops_keyword_promotes_direct_to_executor():
    """A ``direct`` decision on an ops-verb message is promoted (R-10.6)."""
    redis = _make_fake_redis()
    # The model tries to chit-chat but the user's message says "执行".
    direct_args = {
        "route": "direct",
        "direct_answer": "好的",
        "subagent_name": None,
        "suggested_tools": [],
        "reason": "greeting",
        "confidence": 0.9,
    }
    llm = _FakeLLM(fc_response=_aimessage(direct_args))
    router = RouterLLM(llm=llm, redis_client=redis, skill_index_fn=lambda: "")

    decision = await router.classify("执行一下重启脚本", user_id="u-ops")

    assert decision.route == "executor"
    assert decision.direct_answer is None


@pytest.mark.asyncio
async def test_direct_route_does_not_invoke_tool_dispatcher():
    """When ``route=="direct"``, suggested_tools must be empty.

    The ToolDispatcher itself lives in Phase H (task 16); until it
    lands we can still enforce the invariant indirectly: no tool
    dispatcher will ever be reachable if the router either (a) did not
    produce ``direct`` or (b) produced ``direct`` with an empty tool
    subset.
    """
    redis = _make_fake_redis()
    llm = _FakeLLM(fc_response=_aimessage(_valid_direct_args()))
    router = RouterLLM(llm=llm, redis_client=redis, skill_index_fn=lambda: "")

    decision = await router.classify("嗨", user_id="u-d")

    assert decision.route != "direct" or decision.suggested_tools == []


# ---------------------------------------------------------------------------
# Hypothesis PBT — classify never raises
# ---------------------------------------------------------------------------


# Hypothesis strategy: pick one of four failure modes so every example
# exercises a different tier of the 3-tier algorithm.
_FAILURE_MODES = st.sampled_from(
    ["timeout", "exception", "bad_tool_call", "valid"]
)


def _llm_for_mode(mode: str) -> _FakeLLM:
    if mode == "timeout":
        # Only Tier 1 actually times out in the 200ms window; Tier 2
        # resolves synchronously.
        return _FakeLLM(
            fc_delay_s=1.0,
            fc_response=_aimessage(_valid_direct_args()),
            json_side_effect=RuntimeError("json mode disabled"),
        )
    if mode == "exception":
        return _FakeLLM(
            fc_side_effect=RuntimeError("fc blew up"),
            json_side_effect=RuntimeError("json blew up"),
        )
    if mode == "bad_tool_call":
        return _FakeLLM(
            fc_response=_aimessage(None),
            json_side_effect=RuntimeError("json refused"),
        )
    # "valid" — Tier 1 succeeds.
    return _FakeLLM(fc_response=_aimessage(_valid_direct_args()))


@pytest.mark.property
@hsettings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(message=st.text(min_size=0, max_size=2000), mode=_FAILURE_MODES)
def test_classify_never_raises(message: str, mode: str) -> None:
    """P-Router-2: ``classify`` always returns a valid RouterDecision.

    **Validates: Requirements R-1.3, R-10.4.**
    """

    async def _run() -> None:
        redis = _make_fake_redis()
        llm = _llm_for_mode(mode)
        router = RouterLLM(
            llm=llm,
            redis_client=redis,
            timeout_ms=200,
            skill_index_fn=lambda: "",
        )

        decision = await router.classify(message, user_id="pbt-user")

        # Must be a real RouterDecision with a legal route + valid confidence.
        assert isinstance(decision, RouterDecision)
        assert decision.route in {"direct", "executor", "subagent"}
        assert 0.0 <= decision.confidence <= 1.0
        # Low-confidence promotion means most failure modes end up on executor.
        if mode != "valid":
            assert decision.route == "executor"
            assert decision.suggested_tools == []

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Hypothesis PBT — ops-keyword promotion invariant
# ---------------------------------------------------------------------------


@pytest.mark.property
@hsettings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    prefix=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",)), max_size=20
    ),
    suffix=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",)), max_size=20
    ),
    keyword=st.sampled_from(OPS_KEYWORDS),
)
def test_ops_keyword_promotion_invariant(
    prefix: str, suffix: str, keyword: str
) -> None:
    """P-Router-3 / R-10.6: a ``direct`` decision on an ops-keyword message
    is always promoted to ``executor``.

    **Validates: Requirements R-1.4, R-10.6.**
    """
    message = f"{prefix}{keyword}{suffix}"

    async def _run() -> None:
        redis = _make_fake_redis()
        # High-confidence direct decision — would normally stay direct.
        llm = _FakeLLM(fc_response=_aimessage(_valid_direct_args()))
        router = RouterLLM(
            llm=llm,
            redis_client=redis,
            skill_index_fn=lambda: "",
        )
        decision = await router.classify(message, user_id="pbt-ops")
        assert decision.route == "executor"
        assert decision.direct_answer is None

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Sanity unit tests for schema helpers
# ---------------------------------------------------------------------------


def test_promote_if_ops_keyword_leaves_executor_alone():
    d = RouterDecision(
        route="executor", suggested_tools=[], reason="r", confidence=0.8
    )
    assert promote_if_ops_keyword(d, "执行一下") is d


def test_suggested_tools_are_capped_and_deduped():
    d = RouterDecision(
        route="executor",
        suggested_tools=["a", "b", "a", "c", "d", "e", "f", "g"],
        reason="r",
        confidence=0.8,
    )
    # Five unique names, preserving order of first occurrence.
    assert d.suggested_tools == ["a", "b", "c", "d", "e"]


def test_fallback_executor_is_low_confidence_executor():
    d = RouterDecision.fallback_executor("boom")
    assert d.route == "executor"
    assert d.confidence == 0.0
    assert d.direct_answer is None
    assert d.suggested_tools == []
    assert "boom" in d.reason
