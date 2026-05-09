"""Unit tests for :class:`ToolDispatcher`.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 16.2 /
Requirements R-1.7 / R-1.8 / R-8.3.

These tests exercise the dispatcher contract end-to-end without
touching Redis or the LLM layer:

* ``cache_get`` / ``cache_set`` are patched at the dispatcher-module
  level so we can inject an in-memory store keyed by the real Redis
  key format.
* :class:`~src.services.tool_manager.ToolManager` instances are built
  fresh per test and wired into ``ToolDispatcher(tool_manager_=...)``
  so one test's safety classification or output budget can never
  leak into another.
* The :class:`~src.services.interrupt_manager.Interrupt` flow is
  swapped via ``interrupt_manager_=...`` with a hand-rolled stub
  that resolves interrupts synchronously.

The suite is intentionally focused on behavioural properties — partial
coverage of the safety partitioning, cache semantics, approval gate,
and result-ordering invariants. Property-based tests for the deeper
invariants (P-Dispatcher-1…3) land in follow-up task 16.4+.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from src.services.agent_runtime import dispatcher as dispatcher_mod
from src.services.agent_runtime.dispatcher import (
    ToolCall,
    ToolCallStatus,
    ToolDispatcher,
    cache_key_for,
    canonical_args_sha,
)
from src.services.tool_manager import (
    DESTRUCTIVE,
    SAFE_PARALLEL,
    SEQUENTIAL,
    ToolManager,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


class _FakeTool:
    """Minimal LangChain-BaseTool-like stand-in.

    The real :class:`BaseTool` subclasses in this codebase expose
    ``ainvoke(args)`` as their primary surface; we mirror that while
    keeping room for ``_arun(**args)`` via inheritance in other tests
    if ever needed.
    """

    def __init__(self, name: str, fn) -> None:
        self.name = name
        self._fn = fn
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, args: dict[str, Any]) -> Any:
        # ``args`` may be ``None`` for tools that take no arguments.
        args = args or {}
        self.calls.append(args)
        return await self._fn(args)


def _make_tool_manager() -> ToolManager:
    """Return a fresh :class:`ToolManager` with an empty tool registry."""
    tm = ToolManager()
    # Freshly constructed ToolManagers still seed the built-in safety
    # table in ``reload()`` — we skip reload here so the only safety
    # entries that exist are the ones the test explicitly registers.
    return tm


def _register_tool(
    tm: ToolManager,
    name: str,
    fn,
    *,
    safety: str = SEQUENTIAL,
    output_budget: int | None = None,
) -> _FakeTool:
    tool = _FakeTool(name, fn)
    tm._tools[name] = tool  # type: ignore[attr-defined]
    tm.set_safety(name, safety)
    if output_budget is not None:
        tm.set_output_budget(name, output_budget)
    return tool


class _StubInterruptManager:
    """Stand-in for ``interrupt_manager`` with a scripted response."""

    def __init__(self, response: dict[str, Any] | None, *, delay_s: float = 0.0) -> None:
        self._response = response
        self._delay_s = float(delay_s)
        self.create_calls: list[tuple[str, str, dict[str, Any]]] = []

    def create(self, session_id: str, interrupt_type: str, data: dict[str, Any]):
        self.create_calls.append((session_id, interrupt_type, dict(data)))
        response = self._response
        delay_s = self._delay_s

        class _Interrupt:
            async def wait(self, timeout: float = 300) -> dict[str, Any] | None:  # noqa: ARG002
                if delay_s:
                    await asyncio.sleep(delay_s)
                return response

        return _Interrupt()


class _CacheStore(dict):
    """A dict with an attached ``ttls`` attribute for TTL assertions."""

    ttls: dict[str, int]

    def __init__(self) -> None:
        super().__init__()
        self.ttls = {}


@pytest.fixture
def cache_store(monkeypatch):
    """Patch ``cache_get`` / ``cache_set`` with an in-memory dict.

    The stubs mirror the real ``cache_get`` / ``cache_set`` contract:
    ``cache_get`` returns the value handed to ``cache_set`` verbatim
    (no wrapper). Callers can also manually seed the store by writing
    ``cache_store[key] = value``.

    ``cache_store.ttls[key]`` captures the TTL passed to each
    ``cache_set`` so tests can assert the 60 s TTL without extra
    plumbing.
    """
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
# 1. Canonical hash helper
# ---------------------------------------------------------------------------


def test_canonical_args_sha_order_invariant():
    a = {"a": 1, "b": 2}
    b = {"b": 2, "a": 1}
    c = {"a": 1, "b": 3}

    assert canonical_args_sha(a) == canonical_args_sha(b)
    assert canonical_args_sha(a) != canonical_args_sha(c)


def test_cache_key_for_format():
    key = cache_key_for("grep_kb", {"query": "x"})
    assert key.startswith("tool:result:grep_kb:")
    assert len(key.split(":")[-1]) == 64  # sha256 hex


# ---------------------------------------------------------------------------
# 2. Parallel-safe — cache hit skips invocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_safe_uses_cache_on_hit(cache_store):
    tm = _make_tool_manager()

    async def _echo(args):
        return json.dumps(args)

    tool = _register_tool(tm, "echo", _echo, safety=SAFE_PARALLEL)

    # Seed the cache as if a previous call already populated it. The
    # ``cache_store`` fixture's ``cache_get`` stub returns values
    # verbatim, so we write the dict payload the dispatcher expects.
    seeded_key = cache_key_for("echo", {"x": 1})
    cache_store[seeded_key] = {"output": "cached-output"}

    dispatcher = ToolDispatcher(tool_manager_=tm)
    calls = [
        ToolCall(name="echo", args={"x": 1}, call_id="c1"),
        ToolCall(name="echo", args={"x": 1}, call_id="c2"),
    ]
    results = await dispatcher.dispatch_batch(calls)

    assert [r.call_id for r in results] == ["c1", "c2"]
    assert all(r.status == ToolCallStatus.CACHED for r in results)
    assert all(r.cache_hit for r in results)
    assert all(r.output == "cached-output" for r in results)
    # Both calls were cache hits → the tool was never invoked.
    assert tool.calls == []


# ---------------------------------------------------------------------------
# 3. Parallel-safe — miss then hit on same dispatcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_safe_cache_miss_invokes_and_writes(cache_store):
    tm = _make_tool_manager()
    invocation_count = 0

    async def _echo(args):
        nonlocal invocation_count
        invocation_count += 1
        return f"ok:{json.dumps(args, sort_keys=True)}"

    _register_tool(tm, "echo", _echo, safety=SAFE_PARALLEL)

    dispatcher = ToolDispatcher(tool_manager_=tm)

    # 1st call — miss, invokes the tool, writes to cache.
    r1 = (
        await dispatcher.dispatch_batch(
            [ToolCall(name="echo", args={"q": "a"}, call_id="c1")]
        )
    )[0]
    assert r1.status == ToolCallStatus.OK
    assert not r1.cache_hit
    assert invocation_count == 1

    expected_key = cache_key_for("echo", {"q": "a"})
    assert expected_key in cache_store
    # TTL must be 60 seconds (design.md § ToolDispatcher).
    assert cache_store.ttls[expected_key] == 60

    # 2nd call with identical args — hit.
    r2 = (
        await dispatcher.dispatch_batch(
            [ToolCall(name="echo", args={"q": "a"}, call_id="c2")]
        )
    )[0]
    assert r2.status == ToolCallStatus.CACHED
    assert r2.cache_hit
    assert invocation_count == 1  # tool not re-invoked


# ---------------------------------------------------------------------------
# 4. Sequential calls run in order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sequential_runs_in_order(cache_store):
    tm = _make_tool_manager()

    timestamps: list[tuple[str, float]] = []

    async def _record(args):
        timestamps.append((args["tag"], time.perf_counter()))
        # tiny nap so ordering is observable
        await asyncio.sleep(0.005)
        return "done"

    _register_tool(tm, "seq_tool", _record, safety=SEQUENTIAL)

    dispatcher = ToolDispatcher(tool_manager_=tm, redis_enabled=False)
    calls = [
        ToolCall(name="seq_tool", args={"tag": "a"}, call_id="c1"),
        ToolCall(name="seq_tool", args={"tag": "b"}, call_id="c2"),
        ToolCall(name="seq_tool", args={"tag": "c"}, call_id="c3"),
    ]
    results = await dispatcher.dispatch_batch(calls)

    assert [r.call_id for r in results] == ["c1", "c2", "c3"]
    tags = [t for t, _ in timestamps]
    assert tags == ["a", "b", "c"]
    # Strictly increasing timestamps → no interleaving.
    ts_values = [ts for _, ts in timestamps]
    assert all(ts_values[i] < ts_values[i + 1] for i in range(len(ts_values) - 1))


# ---------------------------------------------------------------------------
# 5. Parallel-safe calls truly run concurrently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_calls_truly_parallel(cache_store):
    tm = _make_tool_manager()

    async def _slow(args):
        await asyncio.sleep(0.05)
        return args.get("tag", "")

    _register_tool(tm, "slow_parallel", _slow, safety=SAFE_PARALLEL)

    dispatcher = ToolDispatcher(tool_manager_=tm)
    calls = [
        ToolCall(name="slow_parallel", args={"tag": str(i)}, call_id=f"c{i}")
        for i in range(4)
    ]

    t0 = time.perf_counter()
    results = await dispatcher.dispatch_batch(calls)
    elapsed = time.perf_counter() - t0

    assert [r.call_id for r in results] == [c.call_id for c in calls]
    assert all(r.status == ToolCallStatus.OK for r in results)
    # 4 × 0.05s serial would be ~0.2s; parallel should be well under 0.15s.
    assert elapsed < 0.15, f"elapsed={elapsed!r} — expected < 0.15s"


# ---------------------------------------------------------------------------
# 6. Destructive — no session → immediate rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_destructive_blocked_without_session():
    tm = _make_tool_manager()

    async def _boom(args):  # pragma: no cover - must not be called
        raise AssertionError("tool should not have been invoked without approval")

    tool = _register_tool(tm, "execute", _boom, safety=DESTRUCTIVE)

    dispatcher = ToolDispatcher(
        tool_manager_=tm,
        interrupt_manager_=_StubInterruptManager(response=None),
        redis_enabled=False,
    )

    results = await dispatcher.dispatch_batch(
        [ToolCall(name="execute", args={"cmd": "rm -rf /"}, call_id="d1")]
    )

    assert len(results) == 1
    assert results[0].status == ToolCallStatus.REJECTED
    assert results[0].error == "no_session_for_approval"
    assert tool.calls == []


# ---------------------------------------------------------------------------
# 7. Destructive — user rejects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_destructive_rejected_by_user():
    tm = _make_tool_manager()

    async def _boom(args):  # pragma: no cover
        raise AssertionError("tool should not run after rejection")

    tool = _register_tool(tm, "execute", _boom, safety=DESTRUCTIVE)

    im = _StubInterruptManager(response={"approved": False})
    dispatcher = ToolDispatcher(
        tool_manager_=tm,
        interrupt_manager_=im,
        redis_enabled=False,
    )

    results = await dispatcher.dispatch_batch(
        [ToolCall(name="execute", args={"cmd": "rm -rf /"}, call_id="d1")],
        session_id="sess-42",
    )

    assert results[0].status == ToolCallStatus.REJECTED
    assert results[0].error == "approval_rejected"
    assert tool.calls == []
    assert im.create_calls and im.create_calls[0][0] == "sess-42"
    assert im.create_calls[0][1] == "approval"
    assert im.create_calls[0][2]["risk_level"] == "high"


# ---------------------------------------------------------------------------
# 8. Destructive — user approves
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_destructive_approved_by_user_invokes_tool():
    tm = _make_tool_manager()

    async def _run(args):
        return f"ran:{args.get('cmd','')}"

    tool = _register_tool(tm, "execute", _run, safety=DESTRUCTIVE)

    im = _StubInterruptManager(response={"approved": True})
    dispatcher = ToolDispatcher(
        tool_manager_=tm,
        interrupt_manager_=im,
        redis_enabled=False,
    )

    results = await dispatcher.dispatch_batch(
        [ToolCall(name="execute", args={"cmd": "ls"}, call_id="d1")],
        session_id="sess-42",
    )

    assert len(results) == 1
    assert results[0].status == ToolCallStatus.OK
    assert results[0].output == "ran:ls"
    assert [c["cmd"] for c in tool.calls] == ["ls"]


# ---------------------------------------------------------------------------
# 9. Destructive — wait() times out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_destructive_timeout_rejected():
    tm = _make_tool_manager()

    async def _boom(args):  # pragma: no cover
        raise AssertionError("tool should not run on timeout")

    tool = _register_tool(tm, "execute", _boom, safety=DESTRUCTIVE)

    im = _StubInterruptManager(response=None)  # wait() returns None → timeout
    dispatcher = ToolDispatcher(
        tool_manager_=tm,
        interrupt_manager_=im,
        redis_enabled=False,
        approval_timeout_s=0.05,
    )

    results = await dispatcher.dispatch_batch(
        [ToolCall(name="execute", args={"cmd": "ls"}, call_id="d1")],
        session_id="sess-42",
    )

    assert results[0].status == ToolCallStatus.REJECTED
    assert results[0].error == "approval_timeout"
    assert tool.calls == []


# ---------------------------------------------------------------------------
# 10. Result list keeps input order across mixed safety classes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_result_list_ordering_matches_input(cache_store):
    tm = _make_tool_manager()

    async def _p(args):
        return "p-ok"

    async def _s(args):
        return "s-ok"

    async def _d(args):
        return "d-ok"

    _register_tool(tm, "p_tool", _p, safety=SAFE_PARALLEL)
    _register_tool(tm, "s_tool", _s, safety=SEQUENTIAL)
    _register_tool(tm, "d_tool", _d, safety=DESTRUCTIVE)

    im = _StubInterruptManager(response={"approved": True})
    dispatcher = ToolDispatcher(
        tool_manager_=tm, interrupt_manager_=im
    )

    calls = [
        ToolCall(name="p_tool", args={"i": 0}, call_id="c0"),
        ToolCall(name="d_tool", args={"i": 1}, call_id="c1"),
        ToolCall(name="s_tool", args={"i": 2}, call_id="c2"),
        ToolCall(name="p_tool", args={"i": 3}, call_id="c3"),
    ]
    results = await dispatcher.dispatch_batch(calls, session_id="sess-42")

    assert [r.call_id for r in results] == [c.call_id for c in calls]
    assert [r.name for r in results] == [c.name for c in calls]
    assert all(r.status == ToolCallStatus.OK for r in results)


# ---------------------------------------------------------------------------
# 11. Output budget is applied to tool output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_output_budget_applied(cache_store, monkeypatch):
    tm = _make_tool_manager()

    big = "x" * 5_000

    async def _huge(args):
        return big

    _register_tool(tm, "huge_tool", _huge, safety=SEQUENTIAL, output_budget=100)

    dispatcher = ToolDispatcher(tool_manager_=tm, redis_enabled=False)
    results = await dispatcher.dispatch_batch(
        [ToolCall(name="huge_tool", args={}, call_id="c1")]
    )

    assert len(results) == 1
    assert results[0].status == ToolCallStatus.OK
    assert len(results[0].output) <= 100
    assert "[... output truncated at 100 chars," in results[0].output


# ---------------------------------------------------------------------------
# 12. Unknown tool → ERROR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_tool_returns_error(cache_store):
    tm = _make_tool_manager()
    dispatcher = ToolDispatcher(tool_manager_=tm, redis_enabled=False)

    results = await dispatcher.dispatch_batch(
        [ToolCall(name="does_not_exist", args={"a": 1}, call_id="c1")]
    )

    assert len(results) == 1
    assert results[0].status == ToolCallStatus.ERROR
    assert results[0].error == "unknown_tool"
    assert results[0].cache_hit is False


# ---------------------------------------------------------------------------
# 13. Tool exception → ERROR but other calls in the batch still succeed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_exception_returns_error_result(cache_store):
    tm = _make_tool_manager()

    async def _ok(args):
        return "ok"

    async def _bad(args):
        raise RuntimeError("boom")

    _register_tool(tm, "good_tool", _ok, safety=SAFE_PARALLEL)
    _register_tool(tm, "bad_tool", _bad, safety=SAFE_PARALLEL)

    dispatcher = ToolDispatcher(tool_manager_=tm)
    calls = [
        ToolCall(name="good_tool", args={}, call_id="g"),
        ToolCall(name="bad_tool", args={}, call_id="b"),
    ]
    results = await dispatcher.dispatch_batch(calls)

    by_id = {r.call_id: r for r in results}
    assert by_id["g"].status == ToolCallStatus.OK
    assert by_id["g"].output == "ok"
    assert by_id["b"].status == ToolCallStatus.ERROR
    assert "boom" in (by_id["b"].error or "")
