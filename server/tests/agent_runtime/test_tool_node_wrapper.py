"""Unit tests for :mod:`tool_node_wrapper`.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 16.3 /
Requirements R-1.7.

Every test here mocks the dispatcher so we exercise only the wrapper's
contract:

* Preservation of ``name``, ``description``, ``args_schema`` so the
  LLM's tool-calling planner still sees the right signature.
* Correct single-element batch construction and session-id resolution.
* Status → string marker translation for ``REJECTED`` / ``ERROR``.

The ``BaseTool`` stubs are deliberately cheap (no pydantic BaseModel
args schema) — we test the pydantic path explicitly in
``test_wrap_preserves_args_schema``.
"""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, Field

from src.services.agent_runtime.dispatcher import (
    ToolCall,
    ToolCallResult,
    ToolCallStatus,
)
from src.services.agent_runtime.tool_node_wrapper import (
    _DispatchedTool,
    wrap_tool_for_dispatcher,
    wrap_tools_for_dispatcher,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeBaseTool:
    """Tiny BaseTool-like object.

    The wrapper reads ``.name``, ``.description``, and
    ``.args_schema`` — it never calls the original's ``_arun``
    because dispatch goes through :class:`ToolDispatcher` instead, so
    we only need the public surface mirrored here.
    """

    def __init__(
        self,
        *,
        name: str = "echo",
        description: str = "echo tool",
        args_schema: Any = None,
    ) -> None:
        self.name = name
        self.description = description
        self.args_schema = args_schema


class _EchoArgs(BaseModel):
    """Sample pydantic schema for the args_schema preservation test."""

    query: str = Field(..., description="a query")
    limit: int = 10


class _RecordingDispatcher:
    """Fake dispatcher that records every invocation.

    ``response_builder`` maps a :class:`ToolCall` to a
    :class:`ToolCallResult`. Tests can inject rejection / error paths
    without fighting the real dispatcher's Redis or approval plumbing.
    """

    def __init__(
        self,
        response_builder=None,
    ) -> None:
        self.calls: list[tuple[list[ToolCall], str | None]] = []
        self._response_builder = response_builder or (
            lambda call: ToolCallResult(
                call_id=call.call_id,
                name=call.name,
                status=ToolCallStatus.OK,
                output=f"out:{call.name}:{call.args}",
                latency_ms=1,
            )
        )

    async def dispatch_batch(
        self,
        calls: list[ToolCall],
        *,
        session_id: str | None = None,
    ) -> list[ToolCallResult]:
        # Record a fresh list so assertions don't see later mutation.
        self.calls.append((list(calls), session_id))
        return [self._response_builder(c) for c in calls]


# ---------------------------------------------------------------------------
# 1. Preservation
# ---------------------------------------------------------------------------


def test_wrap_preserves_name_description():
    """Wrapped tool keeps the original ``name`` and ``description``."""
    original = _FakeBaseTool(name="echo", description="e")
    disp = _RecordingDispatcher()

    wrapped = wrap_tool_for_dispatcher(original, dispatcher=disp)

    assert wrapped.name == "echo"
    assert wrapped.description == "e"
    # Identity is the wrapper, not the original.
    assert isinstance(wrapped, _DispatchedTool)


def test_wrap_preserves_args_schema():
    """Pydantic ``args_schema`` is passed through verbatim.

    Critical for LLM tool calling — the planner introspects the schema
    to build the function-call signature. Losing it would either make
    the LLM emit string-only args or fall back to a reflected schema
    derived from ``_run``'s signature, which has no meaningful fields.
    """
    original = _FakeBaseTool(name="q", description="q tool", args_schema=_EchoArgs)
    disp = _RecordingDispatcher()

    wrapped = wrap_tool_for_dispatcher(original, dispatcher=disp)

    assert wrapped.args_schema is _EchoArgs


def test_wrap_handles_missing_args_schema():
    """Tools with ``args_schema=None`` wrap cleanly (no pydantic crash)."""
    original = _FakeBaseTool(name="n", description="n", args_schema=None)
    disp = _RecordingDispatcher()

    wrapped = wrap_tool_for_dispatcher(original, dispatcher=disp)

    # BaseTool defaults ``args_schema`` to ``None`` — the wrapper must
    # not synthesise anything.
    assert wrapped.args_schema is None


# ---------------------------------------------------------------------------
# 2. Dispatch delegation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arun_routes_through_dispatcher():
    """``_arun`` builds a size-1 batch and returns ``result.output``."""
    original = _FakeBaseTool(name="echo", description="e")
    disp = _RecordingDispatcher(
        response_builder=lambda call: ToolCallResult(
            call_id=call.call_id,
            name=call.name,
            status=ToolCallStatus.OK,
            output="pong",
            latency_ms=2,
        )
    )

    wrapped = wrap_tool_for_dispatcher(original, dispatcher=disp)
    out = await wrapped._arun(query="x")

    assert out == "pong"
    assert len(disp.calls) == 1
    sent_calls, sent_session = disp.calls[0]
    assert len(sent_calls) == 1
    sent = sent_calls[0]
    assert sent.name == "echo"
    assert sent.args == {"query": "x"}
    # call_id must be a non-empty UUID-ish string.
    assert isinstance(sent.call_id, str) and sent.call_id
    # Default: no session provider + no context → None.
    assert sent_session is None


@pytest.mark.asyncio
async def test_arun_cached_status_returns_output():
    """``CACHED`` results flow through just like ``OK``."""
    original = _FakeBaseTool(name="echo", description="e")
    disp = _RecordingDispatcher(
        response_builder=lambda call: ToolCallResult(
            call_id=call.call_id,
            name=call.name,
            status=ToolCallStatus.CACHED,
            output="cached-42",
            cache_hit=True,
        )
    )

    wrapped = wrap_tool_for_dispatcher(original, dispatcher=disp)
    out = await wrapped._arun(q="y")
    assert out == "cached-42"


@pytest.mark.asyncio
async def test_arun_rejected_returns_marker():
    """``REJECTED`` result → human-readable marker string."""
    original = _FakeBaseTool(name="nuke", description="scary")
    disp = _RecordingDispatcher(
        response_builder=lambda call: ToolCallResult(
            call_id=call.call_id,
            name=call.name,
            status=ToolCallStatus.REJECTED,
            output="",
            error="approval_rejected",
        )
    )

    wrapped = wrap_tool_for_dispatcher(original, dispatcher=disp)
    out = await wrapped._arun(target="prod")

    assert "rejected" in out.lower()
    assert "approval_rejected" in out
    assert "nuke" in out


@pytest.mark.asyncio
async def test_arun_error_returns_marker():
    """``ERROR`` result → ``[tool <name> error: <msg>]``."""
    original = _FakeBaseTool(name="broken", description="oops")
    disp = _RecordingDispatcher(
        response_builder=lambda call: ToolCallResult(
            call_id=call.call_id,
            name=call.name,
            status=ToolCallStatus.ERROR,
            output="",
            error="boom",
        )
    )

    wrapped = wrap_tool_for_dispatcher(original, dispatcher=disp)
    out = await wrapped._arun()

    assert "error" in out.lower()
    assert "boom" in out
    assert "broken" in out


# ---------------------------------------------------------------------------
# 3. Session id resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_id_from_context(monkeypatch):
    """Session id is read from ``src.agent.context`` when no provider is set."""
    from src.agent import context as agent_ctx

    # Seed the ContextVar with a session id — the wrapper's helper is
    # a thin wrapper over ``get_current_user()`` so setting the ctx
    # directly is enough.
    agent_ctx._current_user_ctx.set({"user_id": "u", "session_id": "sess-xyz"})

    original = _FakeBaseTool(name="echo", description="e")
    disp = _RecordingDispatcher()

    wrapped = wrap_tool_for_dispatcher(original, dispatcher=disp)
    await wrapped._arun(x=1)

    assert len(disp.calls) == 1
    _, sent_session = disp.calls[0]
    assert sent_session == "sess-xyz"


@pytest.mark.asyncio
async def test_session_id_provider_overrides_context(monkeypatch):
    """An explicit provider beats the request ContextVar."""
    from src.agent import context as agent_ctx

    agent_ctx._current_user_ctx.set(
        {"user_id": "u", "session_id": "from-context"}
    )

    original = _FakeBaseTool(name="echo", description="e")
    disp = _RecordingDispatcher()

    wrapped = wrap_tool_for_dispatcher(
        original,
        dispatcher=disp,
        session_id_provider=lambda: "pinned",
    )
    await wrapped._arun()

    _, sent_session = disp.calls[0]
    assert sent_session == "pinned"


@pytest.mark.asyncio
async def test_session_id_provider_none_falls_back_to_context(monkeypatch):
    """If the provider returns ``None``, the ContextVar is consulted."""
    from src.agent import context as agent_ctx

    agent_ctx._current_user_ctx.set(
        {"user_id": "u", "session_id": "fallback-sid"}
    )

    original = _FakeBaseTool(name="echo", description="e")
    disp = _RecordingDispatcher()

    wrapped = wrap_tool_for_dispatcher(
        original,
        dispatcher=disp,
        session_id_provider=lambda: None,
    )
    await wrapped._arun()

    _, sent_session = disp.calls[0]
    assert sent_session == "fallback-sid"


# ---------------------------------------------------------------------------
# 4. wrap_tools_for_dispatcher
# ---------------------------------------------------------------------------


def test_wrap_tools_for_dispatcher_filters_none():
    """``None`` entries are silently dropped; order is preserved."""
    disp = _RecordingDispatcher()
    tools = [
        _FakeBaseTool(name="a", description="a"),
        None,
        _FakeBaseTool(name="b", description="b"),
        None,
        _FakeBaseTool(name="c", description="c"),
    ]

    wrapped = wrap_tools_for_dispatcher(tools, dispatcher=disp)

    assert [t.name for t in wrapped] == ["a", "b", "c"]
    assert all(isinstance(t, _DispatchedTool) for t in wrapped)


def test_wrap_tools_empty_list_returns_empty():
    """No tools → empty list (never ``None``)."""
    assert wrap_tools_for_dispatcher([]) == []


# ---------------------------------------------------------------------------
# 5. UUID uniqueness across calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_arun_has_unique_call_id():
    """Every ``_arun`` invocation generates a fresh call_id."""
    original = _FakeBaseTool(name="echo", description="e")
    disp = _RecordingDispatcher()

    wrapped = wrap_tool_for_dispatcher(original, dispatcher=disp)
    await wrapped._arun(n=1)
    await wrapped._arun(n=2)

    ids = [sent[0][0].call_id for sent in disp.calls]
    assert len(ids) == 2
    assert ids[0] != ids[1]
