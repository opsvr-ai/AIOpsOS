"""End-to-end wiring tests for task 16.3 — ExecutorAgentPool → ToolDispatcher.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 16.3 /
Requirements R-1.7.

Complementary to :mod:`test_tool_node_wrapper` (wrapper contract in
isolation) and :mod:`test_executor_pool_dispatcher` (assert the pool
hands wrapped tools to ``create_deep_agent``). This module closes the
loop by asserting that once the pool has built an executor, *any*
tool-call emitted against the wrapped tools actually flows through a
dispatcher instance — i.e. the wrapper retains its injected dispatcher
reference across the LangGraph build step, without short-circuiting
via the module-level singleton.

To avoid depending on a live LLM or LangGraph state, the tests capture
the ``tools`` kwarg that ``create_deep_agent`` receives (the only place
LangGraph's ``ToolNode`` will ultimately call our proxies) and then
invoke those tool handles directly — the exact path a real LangGraph
``ToolNode`` exercises when an LLM emits a ``tool_call``.

Covered scenarios (R-1.7):

* Each wrapped tool's ``_arun`` triggers exactly one
  :meth:`ToolDispatcher.dispatch_batch` call with a single-element
  batch carrying the emitted args.
* A batch of three parallel-safe calls produces three ``OK`` results
  with outputs in the right positions.
* A destructive call issued with no ``session_id`` is ``REJECTED``
  without the tool ever running.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from src.services.agent_runtime.dispatcher import (
    ToolCall,
    ToolCallResult,
    ToolCallStatus,
)
from src.services.agent_runtime.executor_pool import (
    ExecutorAgentPool,
    _reset_singleton_for_tests,
)
from src.services.agent_runtime.tool_node_wrapper import _DispatchedTool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_pool_singleton():
    _reset_singleton_for_tests()
    yield
    _reset_singleton_for_tests()


class _StubTool:
    """Cheap BaseTool stand-in — pool only reads ``name``/``description``."""

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description or name
        self.args_schema = None


class _CapturingDeepAgent:
    """Stand-in for ``create_deep_agent`` that snapshots its kwargs."""

    def __init__(self) -> None:
        self.captured: dict[str, Any] = {}

    def __call__(self, **kwargs: Any) -> Any:
        self.captured = dict(kwargs)
        return "fake-graph"


class _RecordingDispatcher:
    """Mock dispatcher: records every ``dispatch_batch`` invocation.

    Each recorded entry is ``(list[ToolCall], session_id)``. The test
    customises outcomes by passing in a ``response_builder`` that
    maps each :class:`ToolCall` to a :class:`ToolCallResult` —
    covering the parallel-safe OK path, the destructive REJECTED path
    under R-1.7, and any error shape the wrapper must translate.
    """

    def __init__(self, response_builder=None) -> None:
        self.calls: list[tuple[list[ToolCall], str | None]] = []
        self._response_builder = response_builder or (
            lambda c: ToolCallResult(
                call_id=c.call_id,
                name=c.name,
                status=ToolCallStatus.OK,
                output=f"ok:{c.name}",
            )
        )

    async def dispatch_batch(
        self,
        calls: list[ToolCall],
        *,
        session_id: str | None = None,
    ) -> list[ToolCallResult]:
        # Copy so later test mutation of ``calls`` doesn't poison assertions.
        self.calls.append((list(calls), session_id))
        return [self._response_builder(c) for c in calls]


class _FakeFlagService:
    """Always-on feature flag stub for the dispatcher gate."""

    def __init__(self, value: bool = True) -> None:
        self.value = value

    def is_enabled(self, key: str, user_id: Any = None) -> bool:  # noqa: ARG002
        return self.value


def _patch_flags(monkeypatch, value: bool = True) -> _FakeFlagService:
    svc = _FakeFlagService(value)

    async def _get_feature_flags():
        return svc

    monkeypatch.setattr(
        "src.services.feature_flags.get_feature_flags", _get_feature_flags
    )
    return svc


async def _model_builder() -> str:
    return "fake-model"


def _backend_builder() -> str:
    return "fake-backend"


def _skills_provider() -> list[str] | None:
    return None


def _make_pool_with_dispatcher(
    *,
    dispatcher: _RecordingDispatcher,
    tool_names: list[str],
) -> ExecutorAgentPool:
    """Build a pool that swaps in our recording dispatcher per-wrap call.

    The executor pool imports :mod:`tool_node_wrapper` lazily and calls
    :func:`wrap_tools_for_dispatcher` without a dispatcher override —
    so we need to monkey-patch the wrap helper to inject the
    dispatcher. This is done inside :func:`_wire_dispatcher` via
    ``monkeypatch.setattr``.
    """
    return ExecutorAgentPool(
        model_builder=_model_builder,
        backend_builder=_backend_builder,
        skills_provider=_skills_provider,
        tool_provider=lambda _names: [_StubTool(n) for n in tool_names],
        subagents_provider=lambda _sub: None,
        dispatcher_enabled=True,
    )


def _wire_dispatcher(monkeypatch, dispatcher: _RecordingDispatcher) -> None:
    """Rewire :func:`wrap_tools_for_dispatcher` to inject ``dispatcher``.

    The executor pool calls ``wrap_tools_for_dispatcher`` without an
    explicit ``dispatcher=`` kwarg — in production that falls back to
    ``get_tool_dispatcher()``. Tests want a fresh recording instance
    per scenario, so we patch the symbol the pool imports.
    """
    from src.services.agent_runtime import tool_node_wrapper as wrapper_mod

    original = wrapper_mod.wrap_tools_for_dispatcher

    def _wrap(tools, **kwargs):
        kwargs.setdefault("dispatcher", dispatcher)
        return original(tools, **kwargs)

    monkeypatch.setattr(
        "src.services.agent_runtime.tool_node_wrapper.wrap_tools_for_dispatcher",
        _wrap,
    )


# ---------------------------------------------------------------------------
# 1. A single LLM tool_call lands on dispatch_batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_tool_call_routes_through_dispatcher(monkeypatch):
    """Invoking a wrapped tool's ``_arun`` reaches the injected dispatcher.

    This is the "LLM emits a tool_call → ToolNode._arun → dispatcher"
    wiring, minus the LLM step. We capture the tools handed to
    ``create_deep_agent``, then drive one of them the same way a
    LangGraph ``ToolNode`` would.
    """
    _patch_flags(monkeypatch, True)
    dispatcher = _RecordingDispatcher()
    _wire_dispatcher(monkeypatch, dispatcher)

    pool = _make_pool_with_dispatcher(
        dispatcher=dispatcher, tool_names=["grep_kb"]
    )
    capture = _CapturingDeepAgent()

    with patch("deepagents.create_deep_agent", capture):
        assert await pool.build_for(["grep_kb"], None) == "fake-graph"

    tools = capture.captured.get("tools", [])
    assert len(tools) == 1
    wrapped = tools[0]
    assert isinstance(wrapped, _DispatchedTool)
    assert wrapped.name == "grep_kb"

    # Simulate what LangGraph's ToolNode would do for a single tool_call.
    out = await wrapped._arun(query="hello")

    assert out == "ok:grep_kb"
    assert len(dispatcher.calls) == 1
    sent_calls, sent_session = dispatcher.calls[0]
    assert len(sent_calls) == 1
    assert sent_calls[0].name == "grep_kb"
    assert sent_calls[0].args == {"query": "hello"}
    # No session context set → provider returns None.
    assert sent_session is None


# ---------------------------------------------------------------------------
# 2. Batch of parallel-safe tool_calls — every call lands through dispatcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_of_parallel_safe_calls_all_run(monkeypatch):
    """Three concurrent tool_calls from one LLM turn each reach dispatch_batch.

    LangGraph's ``ToolNode`` fans tool_calls out as separate ``_arun``
    invocations. Each one builds its own size-1 batch; the dispatcher
    therefore sees three separate ``dispatch_batch`` calls and returns
    three OK results. Asserts R-1.7's parallel-safe-all-run contract.
    """
    _patch_flags(monkeypatch, True)
    dispatcher = _RecordingDispatcher()
    _wire_dispatcher(monkeypatch, dispatcher)

    pool = _make_pool_with_dispatcher(
        dispatcher=dispatcher,
        tool_names=["grep_kb", "read_file", "list_dir"],
    )
    capture = _CapturingDeepAgent()

    with patch("deepagents.create_deep_agent", capture):
        await pool.build_for(["grep_kb", "read_file", "list_dir"], None)

    tools = capture.captured["tools"]
    assert len(tools) == 3
    assert all(isinstance(t, _DispatchedTool) for t in tools)

    # Simulate ToolNode invoking each in turn (ToolNode is sequential
    # by default within a single turn — the parallelism win lands at
    # the dispatcher's internal fan-out, exercised in the dispatcher
    # unit tests).
    import asyncio as _aio

    outputs = await _aio.gather(
        tools[0]._arun(q="a"),
        tools[1]._arun(path="/x"),
        tools[2]._arun(path="/y"),
    )

    assert outputs == ["ok:grep_kb", "ok:read_file", "ok:list_dir"]
    assert len(dispatcher.calls) == 3
    # Each dispatch carries a single call — preserves LangGraph's
    # per-tool_call routing semantics.
    for batch, _sid in dispatcher.calls:
        assert len(batch) == 1
    names = [batch[0].name for batch, _ in dispatcher.calls]
    assert names == ["grep_kb", "read_file", "list_dir"]


# ---------------------------------------------------------------------------
# 3. Destructive tool_call without session → REJECTED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_destructive_tool_call_without_approval_is_rejected(monkeypatch):
    """R-1.7 destructive-approval path: no session → rejection marker.

    The dispatcher is set up so that any ``ToolCall`` it receives
    returns a ``REJECTED`` result — mirroring the production
    dispatcher's behaviour when ``session_id is None`` on a destructive
    call. The wrapper must translate that into a human-readable
    marker string rather than raising, so the LangGraph loop can
    continue processing subsequent turns.
    """
    _patch_flags(monkeypatch, True)

    def _reject_all(call: ToolCall) -> ToolCallResult:
        return ToolCallResult(
            call_id=call.call_id,
            name=call.name,
            status=ToolCallStatus.REJECTED,
            output="",
            error="no_session_for_approval",
        )

    dispatcher = _RecordingDispatcher(response_builder=_reject_all)
    _wire_dispatcher(monkeypatch, dispatcher)

    pool = _make_pool_with_dispatcher(
        dispatcher=dispatcher, tool_names=["execute"]
    )
    capture = _CapturingDeepAgent()

    with patch("deepagents.create_deep_agent", capture):
        await pool.build_for(["execute"], None)

    tools = capture.captured["tools"]
    wrapped = tools[0]
    assert isinstance(wrapped, _DispatchedTool)

    out = await wrapped._arun(cmd="rm -rf /")

    assert len(dispatcher.calls) == 1
    # Rejection is reflected in the returned string — not raised.
    assert "rejected" in out.lower()
    assert "execute" in out
    assert "no_session_for_approval" in out


# ---------------------------------------------------------------------------
# 4. Flag off → dispatcher.dispatch_batch is never invoked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_off_bypasses_dispatcher(monkeypatch):
    """When ``tool_dispatcher_enabled`` is off, tools are not wrapped.

    Complements ``test_executor_pool_dispatcher`` by asserting the
    *behavioural* consequence — if the pool hands the raw tool to
    ``create_deep_agent``, LangGraph's ``ToolNode`` will invoke the
    original ``_arun`` directly and our recording dispatcher stays
    untouched. Key fallback safety for R-1.7.
    """
    _patch_flags(monkeypatch, False)
    dispatcher = _RecordingDispatcher()
    _wire_dispatcher(monkeypatch, dispatcher)

    pool = _make_pool_with_dispatcher(
        dispatcher=dispatcher, tool_names=["grep_kb"]
    )
    capture = _CapturingDeepAgent()

    with patch("deepagents.create_deep_agent", capture):
        await pool.build_for(["grep_kb"], None)

    tools = capture.captured["tools"]
    assert len(tools) == 1
    # The raw stub has no ``_arun`` — that's fine; the point is the
    # pool did not insert a dispatcher wrapper.
    assert not isinstance(tools[0], _DispatchedTool)
    assert dispatcher.calls == []
