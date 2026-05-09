"""Unit + property tests for :class:`ExecutorAgentPool`.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — tasks 15.1
through 15.3 (Phase G Part 2) / Requirements R-1.6 / R-1.9.

The tests exercise:

* LRU hit/miss semantics and order invariance of the cache key.
* Eviction when ``cache_size`` is exceeded.
* In-flight build coalescing (multiple concurrent callers for the
  same key share a single ``create_deep_agent`` invocation).
* The :meth:`ExecutorAgentPool.get_for` contract — low-confidence
  / null-tools-subset decisions return ``None`` so the caller falls
  back to the legacy full agent.

No real LLM / LangChain / DeepAgents dependencies are required: we
patch ``deepagents.create_deep_agent`` and inject trivial builders
for model / backend / skills so the tests run in milliseconds.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st

from src.core.metrics import executor_pool_cache_total
from src.services.agent_runtime import executor_pool as exec_pool_mod
from src.services.agent_runtime.executor_pool import (
    ExecutorAgentPool,
    _reset_singleton_for_tests,
)
from src.services.agent_runtime.router_schema import RouterDecision


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_pool_singleton():
    """Guarantee a fresh module-level singleton between tests."""
    _reset_singleton_for_tests()
    yield
    _reset_singleton_for_tests()


class _GraphCounter:
    """Stand-in for ``create_deep_agent`` that returns sentinel strings.

    Each invocation bumps an internal counter and returns a unique
    string keyed on (tools, subagents) so tests can assert both:

    * ``.calls`` — how many distinct builds happened.
    * Equality of graph identities across multiple ``build_for`` calls.
    """

    def __init__(self) -> None:
        self.calls = 0

    def __call__(
        self,
        *,
        model: Any,
        tools: Any,
        system_prompt: Any,
        subagents: Any,
        backend: Any,
        skills: Any,
        debug: bool,
    ) -> str:
        self.calls += 1
        tool_names = tuple(getattr(t, "name", "?") for t in (tools or []))
        subagent_names = tuple(sa["name"] for sa in (subagents or [])) if subagents else ()
        return f"graph-{self.calls}:tools={tool_names}:subs={subagent_names}"


class _StubTool:
    """Minimal BaseTool-like object — the pool only reads ``.name``."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"_StubTool({self.name!r})"


def _make_tool_provider() -> Any:
    """Return a provider that maps names → ``_StubTool`` objects."""

    def _provider(tools_subset: list[str] | None) -> list[_StubTool]:
        if not tools_subset:
            return []
        return [_StubTool(name) for name in tools_subset]

    return _provider


def _make_subagents_provider(
    known: dict[str, dict] | None = None,
) -> Any:
    """Return a subagent provider backed by an in-memory map.

    ``known`` defaults to a small table of fake sub-agent dicts that
    mirror the shape of real ``deepagents.SubAgent`` TypedDicts.
    """
    if known is None:
        known = {
            "monitor": {"name": "monitor", "system_prompt": "mon"},
            "ops": {"name": "ops", "system_prompt": "ops"},
            "knowledge": {"name": "knowledge", "system_prompt": "kb"},
        }

    def _provider(subagents_subset: list[str] | None) -> list[dict] | None:
        if not subagents_subset:
            return None
        resolved = [known[n] for n in subagents_subset if n in known]
        return resolved or None

    # Expose ``calls`` for tests to inspect how the provider was invoked.
    _provider.calls = []  # type: ignore[attr-defined]

    def _tracking_provider(
        subagents_subset: list[str] | None,
    ) -> list[dict] | None:
        _provider.calls.append(list(subagents_subset or []))  # type: ignore[attr-defined]
        return _provider(subagents_subset)

    _tracking_provider.calls = _provider.calls  # type: ignore[attr-defined]
    return _tracking_provider


async def _model_builder() -> str:
    return "fake-model"


def _backend_builder() -> str:
    return "fake-backend"


def _skills_provider() -> list[str] | None:
    return None


def _evict_count() -> float:
    try:
        return float(
            executor_pool_cache_total.labels(result="evicted")._value.get()
        )
    except Exception:  # pragma: no cover - defensive
        return 0.0


def _make_pool(
    *,
    cache_size: int = 32,
    tool_provider: Any | None = None,
    subagents_provider: Any | None = None,
) -> ExecutorAgentPool:
    return ExecutorAgentPool(
        model_builder=_model_builder,
        backend_builder=_backend_builder,
        skills_provider=_skills_provider,
        tool_provider=tool_provider or _make_tool_provider(),
        subagents_provider=subagents_provider or _make_subagents_provider(),
        cache_size=cache_size,
    )


# ---------------------------------------------------------------------------
# 15.1/15.3 — cache semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_returns_same_graph():
    """Repeat ``build_for`` call for same key reuses the cached graph.

    **Validates: Requirements R-1.6.**
    """
    counter = _GraphCounter()
    pool = _make_pool()

    with patch("deepagents.create_deep_agent", counter):
        g1 = await pool.build_for(["a", "b"], None)
        g2 = await pool.build_for(["a", "b"], None)

    assert g1 == g2
    assert counter.calls == 1


@pytest.mark.asyncio
async def test_cache_is_order_invariant():
    """Permutations of the same tool set hit the same cache entry."""
    counter = _GraphCounter()
    pool = _make_pool()

    with patch("deepagents.create_deep_agent", counter):
        g_ab = await pool.build_for(["a", "b"], None)
        g_ba = await pool.build_for(["b", "a"], None)

    assert g_ab == g_ba
    assert counter.calls == 1


@pytest.mark.asyncio
async def test_cache_evicts_lru_after_size():
    """Filling the cache beyond ``cache_size`` evicts the oldest entry.

    The LRU semantics mean: after touching A, B, C, D in that order
    with ``cache_size=3``, A must be gone; B/C/D should still hit.
    """
    counter = _GraphCounter()
    pool = _make_pool(cache_size=3)

    evict_before = _evict_count()

    with patch("deepagents.create_deep_agent", counter):
        await pool.build_for(["a"], None)
        await pool.build_for(["b"], None)
        await pool.build_for(["c"], None)
        # This fourth key should evict ``{a}``.
        await pool.build_for(["d"], None)
        assert counter.calls == 4

        # Touch the survivors — they should still be cached.
        await pool.build_for(["b"], None)
        await pool.build_for(["c"], None)
        await pool.build_for(["d"], None)
        assert counter.calls == 4

        # The evicted entry must be rebuilt.
        await pool.build_for(["a"], None)
        assert counter.calls == 5

    assert _evict_count() - evict_before >= 1


@pytest.mark.asyncio
async def test_concurrent_build_coalesces_to_single_call():
    """Five concurrent ``build_for`` calls for the same key → 1 build.

    **Validates: Requirements R-1.6 (in-flight deduplication).**
    """
    counter = _GraphCounter()

    # Slow the build slightly so all five callers queue up while the
    # first one is still awaiting.
    async def _slow_model_builder() -> str:
        await asyncio.sleep(0.05)
        return "slow-fake-model"

    pool = ExecutorAgentPool(
        model_builder=_slow_model_builder,
        backend_builder=_backend_builder,
        skills_provider=_skills_provider,
        tool_provider=_make_tool_provider(),
        subagents_provider=_make_subagents_provider(),
    )

    with patch("deepagents.create_deep_agent", counter):
        results = await asyncio.gather(
            *(pool.build_for(["x"], None) for _ in range(5))
        )

    # All five callers receive the same graph instance.
    assert len(set(results)) == 1
    # And ``create_deep_agent`` ran exactly once.
    assert counter.calls == 1


# ---------------------------------------------------------------------------
# 15.2 — fallback contract in ``get_for``
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_low_confidence_returns_none_via_get_for():
    """A decision below :data:`CONFIDENCE_FLOOR` yields ``None`` (fallback)."""
    pool = _make_pool()
    counter = _GraphCounter()

    decision = RouterDecision(
        route="executor",
        direct_answer=None,
        subagent_name=None,
        suggested_tools=["grep_kb"],
        reason="unsure",
        confidence=0.2,
    )

    with patch("deepagents.create_deep_agent", counter):
        result = await pool.get_for(decision)

    assert result is None
    assert counter.calls == 0


@pytest.mark.asyncio
async def test_null_tools_subset_returns_none():
    """``build_for(None, None)`` is the "no narrowing" sentinel."""
    pool = _make_pool()
    counter = _GraphCounter()

    with patch("deepagents.create_deep_agent", counter):
        result = await pool.build_for(None, None)

    assert result is None
    assert counter.calls == 0


@pytest.mark.asyncio
async def test_executor_route_with_empty_tools_returns_none():
    """An executor decision with no suggested_tools falls back."""
    pool = _make_pool()
    counter = _GraphCounter()

    decision = RouterDecision(
        route="executor",
        suggested_tools=[],
        reason="model gave up",
        confidence=0.9,
    )

    with patch("deepagents.create_deep_agent", counter):
        result = await pool.get_for(decision)

    assert result is None
    assert counter.calls == 0


@pytest.mark.asyncio
async def test_direct_route_returns_none_via_get_for():
    """``route="direct"`` never uses the pool."""
    pool = _make_pool()
    counter = _GraphCounter()

    decision = RouterDecision(
        route="direct",
        direct_answer="hello",
        suggested_tools=[],
        reason="chit chat",
        confidence=0.9,
    )

    with patch("deepagents.create_deep_agent", counter):
        result = await pool.get_for(decision)

    assert result is None
    assert counter.calls == 0


@pytest.mark.asyncio
async def test_subagent_route_passes_filtered_subagents():
    """``route="subagent"`` forwards essentials-only tools + the named subagent.

    Asserts:
      * tool_provider is called with ``[]`` (essentials only).
      * subagents_provider is called with ``["monitor"]``.
      * ``create_deep_agent`` is invoked exactly once.
    """
    counter = _GraphCounter()
    subagents_provider = _make_subagents_provider()

    # Track what the tool provider was called with.
    tool_calls: list[list[str] | None] = []

    def _tracking_tool_provider(
        tools_subset: list[str] | None,
    ) -> list[_StubTool]:
        tool_calls.append(list(tools_subset) if tools_subset is not None else None)
        return _make_tool_provider()(tools_subset)

    pool = _make_pool(
        tool_provider=_tracking_tool_provider,
        subagents_provider=subagents_provider,
    )

    decision = RouterDecision(
        route="subagent",
        subagent_name="monitor",
        suggested_tools=[],
        reason="user wants monitoring",
        confidence=0.85,
    )

    with patch("deepagents.create_deep_agent", counter):
        result = await pool.get_for(decision)

    assert result is not None
    assert counter.calls == 1
    assert tool_calls == [[]]
    assert subagents_provider.calls == [["monitor"]]


@pytest.mark.asyncio
async def test_get_for_swallows_build_exceptions():
    """If ``build_for`` raises, ``get_for`` degrades to ``None``."""
    pool = _make_pool()

    async def _boom(**kwargs: Any) -> str:
        raise RuntimeError("DeepAgents went bang")

    decision = RouterDecision(
        route="executor",
        suggested_tools=["grep_kb"],
        reason="normal",
        confidence=0.9,
    )

    # Patch via module path because create_deep_agent is looked up
    # inside _build's scope.
    def _raise(**kwargs: Any) -> Any:
        raise RuntimeError("build exploded")

    with patch("deepagents.create_deep_agent", _raise):
        result = await pool.get_for(decision)

    assert result is None


@pytest.mark.asyncio
async def test_subagent_route_without_name_returns_none():
    """``route="subagent"`` with no name falls back to the legacy agent."""
    pool = _make_pool()
    counter = _GraphCounter()

    decision = RouterDecision(
        route="subagent",
        subagent_name=None,
        suggested_tools=[],
        reason="ambiguous",
        confidence=0.9,
    )

    with patch("deepagents.create_deep_agent", counter):
        result = await pool.get_for(decision)

    assert result is None
    assert counter.calls == 0


# ---------------------------------------------------------------------------
# Hypothesis PBT — cache is order- and duplicate-invariant
# ---------------------------------------------------------------------------


@pytest.mark.property
@hsettings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    names=st.lists(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("Lu", "Ll", "Nd"),
                whitelist_characters="_-",
            ),
            min_size=1,
            max_size=8,
        ),
        min_size=1,
        max_size=8,
    )
)
def test_order_invariance_and_deduplication(names: list[str]) -> None:
    """Any permutation / duplication of a name list hits the same entry.

    **Validates: Requirements R-1.6.**
    """

    async def _run() -> None:
        counter = _GraphCounter()
        pool = _make_pool()

        # Variant 1: original ordering.
        # Variant 2: reversed.
        # Variant 3: with each name duplicated twice.
        variant_a = list(names)
        variant_b = list(reversed(names))
        variant_c = list(names) + list(names)

        with patch("deepagents.create_deep_agent", counter):
            g1 = await pool.build_for(variant_a, None)
            g2 = await pool.build_for(variant_b, None)
            g3 = await pool.build_for(variant_c, None)

        assert g1 == g2 == g3
        assert counter.calls == 1

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Singleton plumbing
# ---------------------------------------------------------------------------


def test_singleton_is_lazy_and_resettable():
    """``get_executor_pool`` returns a shared instance; reset drops it."""
    a = exec_pool_mod.get_executor_pool()
    b = exec_pool_mod.get_executor_pool()
    assert a is b

    exec_pool_mod._reset_singleton_for_tests()
    c = exec_pool_mod.get_executor_pool()
    assert c is not a


def test_invalidate_clears_cache():
    """``invalidate`` drops every cached graph but keeps the pool alive."""

    async def _run() -> None:
        counter = _GraphCounter()
        pool = _make_pool()
        with patch("deepagents.create_deep_agent", counter):
            await pool.build_for(["a"], None)
            await pool.build_for(["a"], None)
            assert counter.calls == 1

            pool.invalidate()

            await pool.build_for(["a"], None)
            assert counter.calls == 2

    asyncio.run(_run())
