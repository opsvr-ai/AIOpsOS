"""Property-based tests for :class:`ToolDispatcher`.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — tasks
16.4 / 16.5 / 16.6; correctness properties P-Dispatcher-1 /
P-Dispatcher-2 / P-Dispatcher-3 (design.md § Correctness Properties).

Each property is formulated as a Hypothesis test:

* **P-Dispatcher-1** (order-invariance) — for any permutation of a list
  of parallel-safe calls, the result *set* (keyed by call_id) is
  identical. The per-call output must not depend on input position.

* **P-Dispatcher-2** (destructive-needs-approval) — for any batch
  containing at least one destructive call, the tool is NOT invoked
  unless an approval ``{"approved": True}`` response is explicitly
  returned. Explicit rejection, timeout, or missing session id all
  yield :attr:`ToolCallStatus.REJECTED` with no tool invocation.

* **P-Dispatcher-3** (no cross-talk) — concurrent calls to a stateless
  parallel-safe tool produce results whose ``(call_id, output)``
  mapping matches the deterministic pre-image of the tool. Shuffling,
  duplicating, or arbitrary argument shapes do not poison one call
  with another's output.

All properties run against an in-memory :class:`_CacheStore` so they
neither contact Redis nor pay LLM latency. ``tool_dispatch_total`` may
still receive label increments during these runs; that's harmless —
Prometheus counters are process-wide and test-local resets aren't
required by any assertion here.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st

from src.services.agent_runtime import dispatcher as dispatcher_mod
from src.services.agent_runtime.dispatcher import (
    ToolCall,
    ToolCallStatus,
    ToolDispatcher,
    cache_key_for,
)
from src.services.tool_manager import (
    DESTRUCTIVE,
    SAFE_PARALLEL,
    SEQUENTIAL,
    ToolManager,
)


# ---------------------------------------------------------------------------
# Fixtures — shared with the unit test module in spirit, but free of
# ``pytest_asyncio`` fixtures so they work under Hypothesis' sync runner.
# ---------------------------------------------------------------------------


class _FakeTool:
    """Records every invocation so tests can assert side effects."""

    def __init__(self, name: str, fn) -> None:
        self.name = name
        self._fn = fn
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, args: dict[str, Any]) -> Any:
        args = args or {}
        self.calls.append(args)
        return await self._fn(args)


class _CacheStore(dict):
    """Redis stand-in — captures TTL for assertion convenience."""

    ttls: dict[str, int]

    def __init__(self) -> None:
        super().__init__()
        self.ttls = {}


class _StubInterruptManager:
    """Scripted interrupt manager.

    ``response_map`` maps (call.name, frozenset(call.args.items())) to
    the dict returned from ``Interrupt.wait``. Tests can pass a plain
    dict for "same response for everything", or a callable for more
    complex shaping.
    """

    def __init__(self, response: Any) -> None:
        self._response = response
        self.create_calls: list[tuple[str, str, dict]] = []

    def create(self, session_id, interrupt_type, data):
        self.create_calls.append((session_id, interrupt_type, dict(data)))
        response = self._response

        class _Interrupt:
            async def wait(self, timeout: float = 300):  # noqa: ARG002
                if callable(response):
                    return response(data)
                return response

        return _Interrupt()


def _make_tool_manager() -> ToolManager:
    return ToolManager()


def _register_tool(
    tm: ToolManager,
    name: str,
    fn,
    *,
    safety: str = SEQUENTIAL,
) -> _FakeTool:
    tool = _FakeTool(name, fn)
    tm._tools[name] = tool  # type: ignore[attr-defined]
    tm.set_safety(name, safety)
    return tool


def _install_cache_store(monkeypatch) -> _CacheStore:
    store = _CacheStore()

    async def _fake_get(key: str):
        return store.get(key)

    async def _fake_set(key: str, value: Any, ttl: int = 300) -> None:
        store[key] = value
        store.ttls[key] = ttl

    monkeypatch.setattr(
        "src.services.agent_runtime.dispatcher.cache_get", _fake_get
    )
    monkeypatch.setattr(
        "src.services.agent_runtime.dispatcher.cache_set", _fake_set
    )
    return store


@pytest.fixture(autouse=True)
def _reset_singleton():
    dispatcher_mod._reset_singleton_for_tests()
    yield
    dispatcher_mod._reset_singleton_for_tests()


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Small alphabet keeps hashes cheap and args deterministic.
_scalar = st.one_of(
    st.integers(min_value=-50, max_value=50),
    st.text(
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
        min_size=0,
        max_size=8,
    ),
    st.booleans(),
    st.none(),
)


def _arg_dict(max_size: int = 4):
    return st.dictionaries(
        keys=st.text(
            alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
            min_size=1,
            max_size=4,
        ),
        values=_scalar,
        max_size=max_size,
    )


def _call_strategy(name_choices: list[str]):
    return st.builds(
        lambda name, args, cid: ToolCall(name=name, args=args, call_id=cid),
        name=st.sampled_from(name_choices),
        args=_arg_dict(),
        cid=st.uuids().map(str),
    )


# ---------------------------------------------------------------------------
# P-Dispatcher-1 — parallel-safe order invariance (task 16.4)
# ---------------------------------------------------------------------------


@pytest.mark.property
@hsettings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)
@given(
    calls=st.lists(
        _call_strategy(["tool_a", "tool_b", "tool_c"]),
        min_size=1,
        max_size=6,
        unique_by=lambda c: c.call_id,
    ),
    shuffle_seed=st.integers(min_value=0, max_value=1_000_000),
)
def test_pdispatcher1_parallel_order_invariant(monkeypatch, calls, shuffle_seed):
    """``dispatch_batch`` is invariant under permutation for parallel-safe calls.

    Property:
        ∀ (calls : list[ToolCall]) where ∀c. safety(c.name) == parallel-safe
          ⇒ results_by_id(calls) == results_by_id(shuffle(calls))

    Because the dispatcher preserves input order in the returned list,
    we compare *by call_id*, not by positional index — two permutations
    of the same input *must* produce the same ``{call_id → output}``
    map but may produce different ``list[i]`` orderings.
    """
    _install_cache_store(monkeypatch)

    import random

    async def _run():
        tm = _make_tool_manager()

        async def _echo(args):
            # Deterministic, pure function of args only — no shared state.
            return f"echo:{json.dumps(args, sort_keys=True)}"

        for name in ("tool_a", "tool_b", "tool_c"):
            _register_tool(tm, name, _echo, safety=SAFE_PARALLEL)

        d_orig = ToolDispatcher(tool_manager_=tm)
        res_orig = await d_orig.dispatch_batch(list(calls))
        by_id_orig = {r.call_id: r.output for r in res_orig}

        # Shuffle with fresh dispatcher so cache effects don't couple
        # the two runs (cache is populated in the first call; the
        # second dispatcher hits a fresh store).
        shuffled = list(calls)
        rng = random.Random(shuffle_seed)
        rng.shuffle(shuffled)

        d_shuf = ToolDispatcher(tool_manager_=tm, redis_enabled=False)
        res_shuf = await d_shuf.dispatch_batch(shuffled)
        by_id_shuf = {r.call_id: r.output for r in res_shuf}

        # P-Dispatcher-1 — the per-call-id output map is identical.
        assert by_id_orig == by_id_shuf

        # Sanity: every input appears in the result.
        for c in calls:
            assert c.call_id in by_id_orig
            assert c.call_id in by_id_shuf

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# P-Dispatcher-2 — destructive needs approval (task 16.5)
# ---------------------------------------------------------------------------


@pytest.mark.property
@hsettings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)
@given(
    args=_arg_dict(),
    approval=st.sampled_from(["reject", "timeout", "no_session"]),
)
def test_pdispatcher2_destructive_needs_approval(
    monkeypatch, args, approval
):
    """Every non-approval outcome yields ``REJECTED`` with no tool invocation.

    Property:
        ∀ (call: ToolCall) where safety(call.name) == destructive
                        AND approval_response ∉ {approved=True}
          ⇒ tool NOT invoked AND result.status == REJECTED
    """
    _install_cache_store(monkeypatch)

    async def _run():
        tm = _make_tool_manager()
        invocations: list[dict] = []

        async def _never_run(a):  # pragma: no cover
            invocations.append(a)
            raise AssertionError("destructive tool invoked without approval!")

        _register_tool(tm, "destroy", _never_run, safety=DESTRUCTIVE)

        if approval == "no_session":
            # No interrupt manager call expected; session_id=None trips
            # the preflight rejection inside the dispatcher.
            disp = ToolDispatcher(
                tool_manager_=tm,
                interrupt_manager_=_StubInterruptManager(
                    response={"approved": False}
                ),
                redis_enabled=False,
            )
            session_id = None
        elif approval == "reject":
            disp = ToolDispatcher(
                tool_manager_=tm,
                interrupt_manager_=_StubInterruptManager(
                    response={"approved": False}
                ),
                redis_enabled=False,
            )
            session_id = "sess-ok"
        else:  # timeout
            disp = ToolDispatcher(
                tool_manager_=tm,
                interrupt_manager_=_StubInterruptManager(response=None),
                redis_enabled=False,
                approval_timeout_s=0.01,
            )
            session_id = "sess-ok"

        call = ToolCall(name="destroy", args=args, call_id="d-1")
        results = await disp.dispatch_batch([call], session_id=session_id)

        assert len(results) == 1
        assert results[0].status == ToolCallStatus.REJECTED
        assert invocations == []

        # Error string should clearly indicate why we rejected.
        error = (results[0].error or "").lower()
        if approval == "no_session":
            assert "session" in error
        elif approval == "reject":
            assert "rejected" in error
        else:
            assert "timeout" in error

    asyncio.run(_run())


@pytest.mark.property
@hsettings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)
@given(args=_arg_dict())
def test_pdispatcher2_destructive_approved_runs_tool(monkeypatch, args):
    """Sanity counterpart: on ``{approved: True}`` the tool DOES run.

    Without this, P-Dispatcher-2 could be satisfied trivially by a
    dispatcher that always rejects destructive calls.
    """
    _install_cache_store(monkeypatch)

    async def _run():
        tm = _make_tool_manager()
        invocations: list[dict] = []

        async def _run_fn(a):
            invocations.append(dict(a))
            return f"ran:{json.dumps(a, sort_keys=True)}"

        _register_tool(tm, "destroy", _run_fn, safety=DESTRUCTIVE)

        disp = ToolDispatcher(
            tool_manager_=tm,
            interrupt_manager_=_StubInterruptManager(
                response={"approved": True}
            ),
            redis_enabled=False,
        )
        call = ToolCall(name="destroy", args=args, call_id="d-1")
        results = await disp.dispatch_batch([call], session_id="sess-ok")

        assert len(results) == 1
        assert results[0].status == ToolCallStatus.OK
        assert len(invocations) == 1
        assert invocations[0] == args

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# P-Dispatcher-3 — no state cross-talk (task 16.6)
# ---------------------------------------------------------------------------


@pytest.mark.property
@hsettings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)
@given(
    calls=st.lists(
        _call_strategy(["stateless"]),
        min_size=1,
        max_size=8,
        unique_by=lambda c: c.call_id,
    ),
)
def test_pdispatcher3_no_state_crosstalk(monkeypatch, calls):
    """Concurrent dispatch of a stateless parallel-safe tool is deterministic.

    Property:
        For a pure function ``f(args) -> output``, dispatching
        concurrent ``[f(a1), f(a2), ..., f(aN)]`` produces results
        where each ``result.output == f(args)``, regardless of
        scheduler order.

    We verify by computing the pre-image ``{call_id → f(args)}``
    synchronously, then running the batch and asserting the same
    mapping.
    """
    _install_cache_store(monkeypatch)

    async def _run():
        tm = _make_tool_manager()

        # Deterministic pure function — output is a canonical hash of args.
        async def _stateless(args):
            return f"h:{json.dumps(args, sort_keys=True)}"

        _register_tool(tm, "stateless", _stateless, safety=SAFE_PARALLEL)

        # Compute the expected mapping synchronously BEFORE dispatching.
        # The dispatcher also applies ``apply_output_budget``, which is a
        # no-op for outputs under the default 100k-char budget.
        expected = {
            c.call_id: f"h:{json.dumps(c.args, sort_keys=True)}"
            for c in calls
        }

        # First run through a concurrent dispatch.
        disp = ToolDispatcher(tool_manager_=tm, redis_enabled=False)
        results = await disp.dispatch_batch(list(calls))

        got = {r.call_id: r.output for r in results}
        assert got == expected

        # Sanity: every call actually made it through; none of them
        # inherited a neighbour's output.
        for c in calls:
            assert got[c.call_id].startswith("h:")

    asyncio.run(_run())


@pytest.mark.property
@hsettings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)
@given(
    distinct_args=st.lists(
        _arg_dict(),
        min_size=2,
        max_size=6,
    ),
)
def test_pdispatcher3_cache_key_partitions_args(monkeypatch, distinct_args):
    """Distinct argument payloads always hash to distinct cache keys.

    This is a helper property for P-Dispatcher-3 — if two concurrent
    calls with different ``args`` collided in the cache, the second
    would silently read the first's output (cross-talk via the
    cache). Dedupe the generated list by canonical JSON form so we
    only assert about genuinely-different payloads.
    """
    # Deduplicate by canonical JSON so the property only holds for
    # genuinely distinct argument dicts.
    seen: dict[str, dict] = {}
    for a in distinct_args:
        key = json.dumps(a, sort_keys=True)
        seen.setdefault(key, a)
    unique = list(seen.values())
    if len(unique) < 2:
        return  # not enough distinct values

    keys = {cache_key_for("tool", a) for a in unique}
    # All cache keys should be unique — sha256 over different payloads.
    assert len(keys) == len(unique)
