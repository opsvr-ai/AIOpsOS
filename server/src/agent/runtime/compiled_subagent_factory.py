"""Factory that assembles :class:`CompiledSubAgent` instances with live prompts.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 19.2,
requirements R-3.21.

Background
----------
Stock DeepAgents ``SubAgent`` dicts embed the ``system_prompt`` at
graph-compile time. That's the exact shape we're trying to avoid —
every prompt promotion would force a ``_deep_agent`` rebuild (R-3.23).
The framework *does* accept an alternative payload, :class:`CompiledSubAgent`,
whose ``runnable`` slot is an already-compiled LangGraph the caller
owns. The :class:`SubAgentMiddleware` skips ``create_agent()`` for these
entries and just reuses the runnable.

That escape hatch is our hook. We build a runnable via
``create_agent(system_prompt=_SENTINEL_PROMPT, ...)`` so LangChain
installs a deterministic :class:`SystemMessage` carrying the sentinel,
and we place :class:`DynamicSystemPromptMiddleware` at **position 0**
of the middleware stack. On every model call that middleware swaps the
sentinel for the registry's live prompt (see task 19.1 module docstring
for the full contract, including P-HotReload-6/7/8).

Middleware order
~~~~~~~~~~~~~~~~
We mirror the stack DeepAgents would build for a declarative
``SubAgent`` (see ``deepagents.middleware.subagents``) but prepend
:class:`DynamicSystemPromptMiddleware`:

1. ``DynamicSystemPromptMiddleware`` — replaces sentinel on every call
   (required at index 0 per R-3.22).
2. ``TodoListMiddleware`` — exposes ``write_todos`` tool.
3. ``FilesystemMiddleware`` — virtual fs tools (ls/read/write/…).
4. ``create_summarization_middleware(model, backend)`` — history
   compression under token pressure.
5. ``PatchToolCallsMiddleware`` — DeepSeek tool-call repair.
6. (optional) ``SkillsMiddleware`` when ``skills`` is provided.
7. (optional) caller-supplied ``extra_middleware`` appended.
8. ``AnthropicPromptCachingMiddleware`` with ``unsupported_model_behavior="ignore"``
   so non-Anthropic models see a no-op.

Why keep the sentinel as a ``str`` (not ``None``)?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
``create_agent(system_prompt=None)`` leaves ``request.system_message``
unset, forcing every consumer to special-case ``None``. The sentinel
string keeps the anchor stable and downstream code uniform — the
middleware recognises it, strips it, and writes the live prompt plus
any suffix appended by outer middleware (R-3.25).

Why add :class:`AnthropicPromptCachingMiddleware` unconditionally?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The middleware self-detects the provider and short-circuits when the
model isn't Anthropic (``unsupported_model_behavior="ignore"``). Adding
it here — instead of making callers remember — means Anthropic-backed
deployments automatically benefit from caching without any code change.
"""

from __future__ import annotations

import logging
from typing import Any

from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from deepagents.middleware.subagents import CompiledSubAgent
from deepagents.middleware.summarization import (
    create_summarization_middleware,
)
from langchain.agents import create_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware

from src.agent.runtime.dynamic_prompt_middleware import (
    DynamicSystemPromptMiddleware,
    _SENTINEL_PROMPT,
)
from src.agent.runtime.message_order_middleware import MessageOrderMiddleware

logger = logging.getLogger(__name__)


def build_dynamic_subagent(
    *,
    name: str,
    description: str,
    model: Any,
    tools: list[Any],
    registry: Any,
    backend: Any,
    skills: list[str] | None = None,
    extra_middleware: list[Any] | None = None,
) -> CompiledSubAgent:
    """Assemble a :class:`CompiledSubAgent` with a runtime-resolved prompt.

    The returned dict conforms to ``deepagents.middleware.subagents.CompiledSubAgent``
    — it carries ``name`` / ``description`` / ``runnable`` keys and is a
    drop-in replacement for a declarative :class:`~deepagents.SubAgent`
    entry in ``create_deep_agent(subagents=...)``.

    Args:
        name: sub-agent identifier (e.g. ``"knowledge"``). Passed to
            :class:`DynamicSystemPromptMiddleware` so it resolves the
            right lane in the registry, and to ``create_agent(name=…)``
            so the compiled graph is tagged for tracing.
        description: sub-agent summary shown to the orchestrator LLM
            when it decides whether to delegate via ``task()``. Stored
            verbatim on the returned :class:`CompiledSubAgent`.
        model: LangChain chat model. Must be the same one
            :func:`create_summarization_middleware` expects. Passed
            through to :func:`langchain.agents.create_agent`.
        tools: sub-agent-specific tools (in addition to the built-ins
            the filesystem/todolist middleware expose).
        registry: :class:`SubAgentPromptRegistry` that
            :class:`DynamicSystemPromptMiddleware` will consult on
            every model call. Borrowed — the factory never mutates it.
        backend: DeepAgents backend protocol instance
            (e.g. :class:`deepagents.backends.LocalShellBackend`).
            Shared between filesystem / summarization / skills
            middleware.
        skills: optional list of skill source directories. When
            supplied, a :class:`SkillsMiddleware` is appended to the
            stack so the sub-agent can progressively load ``SKILL.md``
            files under these roots.
        extra_middleware: optional caller-supplied middleware appended
            after skills but before the (always-on)
            :class:`AnthropicPromptCachingMiddleware`. Used by tests
            and by future tasks (e.g. trajectory-capturing probes for
            P-HotReload-8).

    Returns:
        A :class:`CompiledSubAgent` ``TypedDict`` — DeepAgents will use
        it verbatim, skipping its own ``create_agent`` call.

    Notes:
        :class:`DynamicSystemPromptMiddleware` is always inserted at
        index 0 regardless of ``extra_middleware`` (R-3.22). Callers
        cannot shift it, because that would let outer middleware see
        the sentinel instead of the live prompt — breaking R-3.25's
        suffix-preservation guarantee from the other side.
    """
    middleware: list[Any] = [
        DynamicSystemPromptMiddleware(name=name, registry=registry),
        TodoListMiddleware(),
        FilesystemMiddleware(backend=backend),
        create_summarization_middleware(model, backend),
        MessageOrderMiddleware(),  # Fix message order after summarization
        PatchToolCallsMiddleware(),
    ]
    if skills:
        middleware.append(
            SkillsMiddleware(backend=backend, sources=skills)
        )
    if extra_middleware:
        middleware.extend(extra_middleware)
    # Anthropic-only optimisation; no-op for other providers.
    middleware.append(
        AnthropicPromptCachingMiddleware(
            unsupported_model_behavior="ignore",
        )
    )

    runnable = create_agent(
        model,
        system_prompt=_SENTINEL_PROMPT,
        tools=tools,
        middleware=middleware,
        name=name,
    )
    logger.debug(
        "build_dynamic_subagent[%s]: compiled with %d middleware, "
        "%d tools, skills=%s",
        name,
        len(middleware),
        len(tools),
        skills,
    )

    return CompiledSubAgent(
        name=name,
        description=description,
        runnable=runnable,
    )


__all__ = ["build_dynamic_subagent"]
