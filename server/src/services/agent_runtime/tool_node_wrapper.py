"""LangChain ``BaseTool`` proxy that routes calls through :class:`ToolDispatcher`.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 16.3 /
Requirements R-1.7.

Why a per-tool wrapper (rather than replacing LangGraph's ``ToolNode``)?
----------------------------------------------------------------------
DeepAgents (via LangGraph) wires tool execution through a private
``ToolNode`` subgraph whose internals are unstable across releases.
Monkey-patching it would tightly couple us to a specific DeepAgents
revision. Instead we replace each ``BaseTool`` in the executor's
``tools_subset`` with a thin :class:`_DispatchedTool` proxy whose
``_arun`` delegates to :meth:`ToolDispatcher.dispatch_batch` with a
batch of size 1.

Trade-offs of the batch-of-1 v1:

* The ``ToolNode`` invokes its tools sequentially by default, so the
  dispatcher's parallel-safe fan-out doesn't kick in at the
  *within-node* level. That parallelism is recovered at the
  *executor-pool* level when the LLM emits multiple tool_calls in a
  single turn — LangGraph dispatches each one through our proxy and
  each proxy's single call still participates in the dispatcher's
  safety partitioning, result cache, and approval gate. The bulk
  parallel-across-calls win is deferred to a later optimisation that
  would reconstruct the ``ToolNode`` wholesale (out of scope here).
* The proxy preserves ``name``, ``description``, and ``args_schema``
  so the LLM's tool-calling planner still sees the real signature.

Pydantic v2 notes (this file fights the BaseTool pydantic model):

* Private-attribute storage goes through ``object.__setattr__`` *after*
  ``super().__init__(**kw)`` — the same pattern ``_BuiltinTool`` uses
  in :mod:`tool_manager`. Pydantic v2 strips underscore-prefixed
  kwargs from ``__init__``; ``object.__setattr__`` bypasses the
  pydantic validator for those already-declared private slots.
* ``args_schema`` on BaseTool accepts ``type[BaseModel] | dict | None``
  (see ``langchain_core.tools.base``). We pass through the wrapped
  tool's value verbatim — *don't* synthesise a schema here since
  ``create_schema_from_function(self.name, self._run)`` is the fallback
  the parent class already uses when ``args_schema is None``.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from langchain_core.tools import BaseTool

from src.services.agent_runtime.dispatcher import (
    ToolCall,
    ToolCallStatus,
    ToolDispatcher,
    get_tool_dispatcher,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session-id resolution — ContextVar → fallback
# ---------------------------------------------------------------------------


def _session_id_from_context() -> str | None:
    """Return the session id from the request-scoped user context, if any.

    Import is kept lazy so test fixtures that don't set up
    ``src.agent.context`` don't pay the import cost just for
    wrapping a tool. ``None`` is returned whenever the context isn't
    populated or the lookup raises — the dispatcher already handles
    "no session id" on destructive calls by rejecting them with a
    clear error code.
    """
    try:
        from src.agent.context import get_current_user

        ctx = get_current_user() or {}
        sid = ctx.get("session_id")
        return sid or None
    except Exception:  # pragma: no cover - defensive
        logger.debug(
            "tool_node_wrapper: session_id lookup failed", exc_info=True
        )
        return None


# ---------------------------------------------------------------------------
# Proxy BaseTool
# ---------------------------------------------------------------------------


class _DispatchedTool(BaseTool):
    """``BaseTool`` that delegates to :class:`ToolDispatcher`.

    Stores the wrapped tool + dispatcher references as real Python
    attributes set via ``object.__setattr__`` — Pydantic v2 strips
    underscore-prefixed constructor kwargs and would otherwise reject
    assignment on a frozen model.
    """

    def __init__(
        self,
        /,
        *,
        name: str,
        description: str,
        args_schema: Any = None,
        wrapped: BaseTool,
        dispatcher: ToolDispatcher | None = None,
        session_id_provider: Callable[[], str | None] | None = None,
    ) -> None:
        # BaseTool is a pydantic v2 model — only pass the declared
        # fields, and let our private attrs be set post-init.
        kwargs: dict[str, Any] = {"name": name, "description": description}
        if args_schema is not None:
            kwargs["args_schema"] = args_schema
        super().__init__(**kwargs)
        object.__setattr__(self, "_wrapped", wrapped)
        object.__setattr__(self, "_dispatcher", dispatcher)
        object.__setattr__(self, "_session_id_provider", session_id_provider)

    # ------------------------------------------------------------------
    # BaseTool interface
    # ------------------------------------------------------------------

    def _run(self, **kwargs: Any) -> str:
        # Sync path is never taken by DeepAgents (ToolNode always uses
        # the async path). Mirror the fallback used by _BuiltinTool so
        # the LLM sees a stable marker rather than an exception when a
        # caller stubs in a sync path.
        return f"[{self.name} async-only — use _arun]"

    async def _arun(self, **kwargs: Any) -> str:
        dispatcher: ToolDispatcher | None = getattr(self, "_dispatcher", None)
        provider: Callable[[], str | None] | None = getattr(
            self, "_session_id_provider", None
        )

        session_id: str | None = None
        if provider is not None:
            try:
                session_id = provider()
            except Exception:
                logger.debug(
                    "tool_node_wrapper: session_id_provider raised",
                    exc_info=True,
                )
                session_id = None
        if session_id is None:
            session_id = _session_id_from_context()

        disp = dispatcher or get_tool_dispatcher()
        call = ToolCall(
            name=self.name,
            args=dict(kwargs),
            call_id=str(uuid.uuid4()),
        )
        results = await disp.dispatch_batch([call], session_id=session_id)
        if not results:  # pragma: no cover - dispatcher guarantees 1:1
            return f"[tool {self.name} error: dispatcher returned empty batch]"
        result = results[0]

        if result.status == ToolCallStatus.REJECTED:
            return f"[tool {self.name} rejected: {result.error or 'unknown'}]"
        if result.status == ToolCallStatus.ERROR:
            return f"[tool {self.name} error: {result.error or 'unknown'}]"
        # OK / CACHED — dispatcher already applied the output budget
        # and coerced the payload to a string.
        return result.output


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def wrap_tool_for_dispatcher(
    tool: BaseTool,
    *,
    dispatcher: ToolDispatcher | None = None,
    session_id_provider: Callable[[], str | None] | None = None,
) -> BaseTool:
    """Return a dispatcher-backed proxy of ``tool``.

    The returned proxy keeps the wrapped tool's ``name``,
    ``description``, and ``args_schema`` so the LLM's tool-calling
    planner still sees the correct invocation signature. ``_arun``
    builds a one-element :class:`ToolCall` batch and sends it through
    :meth:`ToolDispatcher.dispatch_batch`, mapping the result back to
    a stringly-typed return value:

    * ``OK`` / ``CACHED`` → ``result.output`` (already budget-capped).
    * ``REJECTED`` → ``"[tool {name} rejected: {error}]"``.
    * ``ERROR`` → ``"[tool {name} error: {error}]"``.

    Parameters
    ----------
    tool:
        The original :class:`BaseTool` to proxy.
    dispatcher:
        Optional dispatcher override — defaults to the process-wide
        :func:`get_tool_dispatcher` singleton.
    session_id_provider:
        Optional callable that returns a session id string (or
        ``None``). When provided it takes precedence over the request
        ContextVar; useful for tests or background tasks that need to
        pin a session id outside an HTTP request.
    """
    args_schema = getattr(tool, "args_schema", None)
    return _DispatchedTool(
        name=tool.name,
        description=tool.description or tool.name,
        args_schema=args_schema,
        wrapped=tool,
        dispatcher=dispatcher,
        session_id_provider=session_id_provider,
    )


def wrap_tools_for_dispatcher(
    tools: list[BaseTool],
    *,
    dispatcher: ToolDispatcher | None = None,
    session_id_provider: Callable[[], str | None] | None = None,
) -> list[BaseTool]:
    """Vectorised :func:`wrap_tool_for_dispatcher` — ``None`` items are dropped.

    Order is preserved so the executor pool's cache key remains stable
    (it already freezes the set, but the tool list passed into
    ``create_deep_agent`` benefits from deterministic ordering for
    downstream logging / tracing).
    """
    out: list[BaseTool] = []
    for t in tools:
        if t is None:
            continue
        out.append(
            wrap_tool_for_dispatcher(
                t,
                dispatcher=dispatcher,
                session_id_provider=session_id_provider,
            )
        )
    return out


__all__ = [
    "wrap_tool_for_dispatcher",
    "wrap_tools_for_dispatcher",
]
