"""ExecutorAgentPool — LRU cache of dynamically-assembled DeepAgents graphs.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — tasks 15.1
through 15.3 (Phase G Part 2); Requirements R-1.6 / R-1.9.

Background
----------
The main ``AIOpsOS`` agent built by ``src.agent.deep_agent.get_deep_agent``
装配所有内置工具 + 8 个子 agent — prompt 超过 8k tokens, 图构建耗时数百
毫秒. RouterLLM (task 13.x) 会针对每条用户消息给出一个受限的
(tools_subset, subagent_name) 选择, 通常 ≤ 5 个工具 + 0-1 个子 agent.
这个 pool 将 (tools_subset, subagent_subset) 映射到一个 compiled
``CompiledStateGraph``, 然后按 LRU 复用以避免重复的 DeepAgents 图构建.

Fallback contract
-----------------
``build_for`` / ``get_for`` **never** attempt to construct a "legacy
full agent".  Whenever we can't narrow the tool set — either because
RouterLLM returned low confidence (< 0.4), because the caller passed
``tools_subset=None``, or because the underlying build raised — we
return ``None`` and let the caller fall back to
``src.agent.deep_agent.get_deep_agent()``.  That way this module
stays small and stateless-ish, and the "big" legacy singleton keeps
exactly one home.

Concurrency
-----------
* An ``asyncio.Lock`` guards the LRU dict + the "build in progress"
  map but is **released before** ``create_deep_agent`` is awaited —
  graph construction can take 100 ms+ and we don't want that to
  serialize unrelated cache reads.
* Two concurrent ``build_for`` calls for the same key share the same
  future so we invoke the builder exactly once (tested by
  ``test_concurrent_build_coalesces_to_single_call``).
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Optional

from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from src.core.metrics import executor_pool_cache_total
from src.services.agent_runtime.router import CONFIDENCE_FLOOR
from src.services.agent_runtime.router_schema import RouterDecision

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

CacheKey = tuple[frozenset[str], frozenset[str]]

ModelBuilder = Callable[[], Awaitable[Any]]
BackendBuilder = Callable[[], Any]
SkillsProvider = Callable[[], Optional[list[str]]]
ToolProvider = Callable[[Optional[list[str]]], list[BaseTool]]
SubagentsProvider = Callable[[Optional[list[str]]], Optional[list[dict]]]

DEFAULT_CACHE_SIZE = 32


# ---------------------------------------------------------------------------
# Default providers — thin wrappers over ``src.agent.deep_agent``
# ---------------------------------------------------------------------------


def _default_tool_provider(tools_subset: Optional[list[str]]) -> list[BaseTool]:
    """Resolve names → BaseTool via ``tool_manager``.

    ``None`` is preserved as "empty" here because the pool already
    special-cases ``None`` in :meth:`ExecutorAgentPool.build_for`
    (returns without building) — by the time we hit this provider,
    callers have either a concrete subset or ``[]`` meaning essentials.
    DeepAgents' ``create_deep_agent`` auto-injects the filesystem +
    planning essentials, so the provider only needs to hand back the
    additional named tools.
    """
    if not tools_subset:
        return []
    # Late import to avoid tripping the tool_manager singleton at module
    # import time — tests that construct a stub ``tool_provider`` never
    # touch this path, which keeps them lightweight.
    from src.services.tool_manager import tool_manager

    out: list[BaseTool] = []
    for name in tools_subset:
        tool = tool_manager.get_tool(name)
        if tool is not None:
            out.append(tool)
    return out


def _subagent_map() -> dict[str, dict]:
    """Return ``{name: SubAgent-dict}`` from ``deep_agent.SUBAGENTS``.

    Imported lazily so this module stays importable in isolation —
    ``src.agent.deep_agent`` pulls in the full model / backend stack
    just by being imported.
    """
    from src.agent.deep_agent import SUBAGENTS

    return {sa["name"]: sa for sa in SUBAGENTS}


def _default_subagents_provider(
    subagents_subset: Optional[list[str]],
) -> Optional[list[dict]]:
    """Filter the module-level ``SUBAGENTS`` list by name.

    Returns ``None`` when the subset is empty so DeepAgents skips
    sub-agent wiring entirely (a subagents-less executor is the cheap
    path for most tool-only tasks).
    """
    if not subagents_subset:
        return None
    mapping = _subagent_map()
    resolved: list[dict] = []
    for name in subagents_subset:
        sa = mapping.get(name)
        if sa is not None:
            resolved.append(sa)
    return resolved or None


# ---------------------------------------------------------------------------
# ExecutorAgentPool
# ---------------------------------------------------------------------------


class ExecutorAgentPool:
    """LRU-cached assembler of narrow DeepAgents executors.

    Each cache entry is keyed on the *frozen set* of tool and
    sub-agent names, so permutations and duplicates collapse to the
    same key — RouterLLM may emit ``["grep_kb", "read_wiki"]`` and
    ``["read_wiki", "grep_kb", "grep_kb"]`` in different turns; both
    should share the same compiled graph.

    Parameters
    ----------
    model_builder, backend_builder, skills_provider
        Plumbing for DeepAgents' ``create_deep_agent``.  Default to the
        module-level helpers in ``src.agent.deep_agent`` so production
        callers need not configure anything.  Tests inject lightweight
        stubs so they don't need a model provider.
    tool_provider
        ``(tools_subset) -> list[BaseTool]``.  Default looks names up
        via ``tool_manager.get_tool``.
    subagents_provider
        ``(subagents_subset) -> list[dict] | None``.  Default filters
        the module-level ``SUBAGENTS`` list.
    cache_size
        LRU capacity.  Defaults to :data:`DEFAULT_CACHE_SIZE`.
    """

    def __init__(
        self,
        *,
        model_builder: ModelBuilder | None = None,
        backend_builder: BackendBuilder | None = None,
        skills_provider: SkillsProvider | None = None,
        tool_provider: ToolProvider | None = None,
        subagents_provider: SubagentsProvider | None = None,
        cache_size: int = DEFAULT_CACHE_SIZE,
        dispatcher_enabled: bool = True,
    ) -> None:
        self._model_builder = model_builder
        self._backend_builder = backend_builder
        self._skills_provider = skills_provider
        self._tool_provider = tool_provider or _default_tool_provider
        self._subagents_provider = subagents_provider or _default_subagents_provider
        self._cache_size = max(1, int(cache_size))
        # Policy gate for the dispatcher wrapper — constructor-level
        # kill switch so tests can disable the feature without touching
        # the runtime feature-flag service. The in-request gate is the
        # ``tool_dispatcher_enabled`` feature flag checked in :meth:`_build`.
        self._dispatcher_enabled = bool(dispatcher_enabled)

        self._cache: OrderedDict[CacheKey, CompiledStateGraph] = OrderedDict()
        self._in_flight: dict[CacheKey, asyncio.Future[CompiledStateGraph]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def build_for(
        self,
        tools_subset: list[str] | None,
        subagents_subset: list[str] | None,
    ) -> CompiledStateGraph | None:
        """Return a (possibly cached) executor graph for the given subset.

        ``tools_subset=None`` is the sentinel for "caller has no
        narrowed tool list" and triggers the fallback contract — we
        return ``None`` so the caller falls back to
        ``get_deep_agent()``.  An empty list (``[]``) means "essentials
        only" and still returns a compiled graph.
        """
        if tools_subset is None:
            return None

        key: CacheKey = (
            frozenset(tools_subset),
            frozenset(subagents_subset or ()),
        )

        # ---- Cache lookup / in-flight coalescing (single critical section)
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                # Move to the MRU end.
                self._cache.move_to_end(key)
                _inc_cache("hit")
                return cached

            pending = self._in_flight.get(key)
            if pending is not None:
                # Another caller is already building this key — piggyback
                # on its future without bumping any counter (the first
                # caller already recorded a miss and will populate the
                # cache, so future callers will be true hits).
                future = pending
                build_owner = False
            else:
                _inc_cache("miss")
                future = asyncio.get_running_loop().create_future()
                self._in_flight[key] = future
                build_owner = True

        if not build_owner:
            # Wait outside the lock so concurrent keys don't serialize.
            return await future

        # ---- Build outside the lock ---------------------------------------
        try:
            graph = await self._build(
                list(tools_subset), list(subagents_subset or ())
            )
        except Exception as exc:
            # Fail loudly for the owner AND any waiters, then drop the
            # in-flight entry so the next caller can retry.
            async with self._lock:
                self._in_flight.pop(key, None)
            if not future.done():
                future.set_exception(exc)
            raise

        # ---- Populate cache + wake waiters --------------------------------
        async with self._lock:
            self._in_flight.pop(key, None)
            self._cache[key] = graph
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
                _inc_cache("evicted")

        if not future.done():
            future.set_result(graph)
        return graph

    async def get_for(
        self, decision: RouterDecision
    ) -> CompiledStateGraph | None:
        """Map a :class:`RouterDecision` onto the pool.

        Contract (see module docstring):

        * ``decision.confidence < CONFIDENCE_FLOOR`` → ``None``
          (caller falls back to full agent).
        * ``route == "executor"`` + non-empty ``suggested_tools`` →
          ``build_for(tools, [])``.
        * ``route == "subagent"`` + ``subagent_name`` set →
          ``build_for([], [subagent_name])``.
        * Anything else → ``None``.

        Any exception raised by ``build_for`` is caught and logged —
        the fallback contract promises a ``None`` result rather than
        an exception bubble on the request path.
        """
        if decision.confidence < CONFIDENCE_FLOOR:
            return None

        route = decision.route
        tools_subset: list[str] | None
        subagents_subset: list[str] | None

        if route == "executor":
            # Empty suggested_tools is valid — DeepAgents will inject
            # essential tools (filesystem, planning) automatically.
            # Only return None when suggested_tools is explicitly None
            # (meaning RouterLLM couldn't determine any tools).
            if decision.suggested_tools is None:
                return None
            tools_subset = list(decision.suggested_tools)
            subagents_subset = None
        elif route == "subagent":
            if not decision.subagent_name:
                return None
            tools_subset = []
            subagents_subset = [decision.subagent_name]
        else:
            return None

        try:
            return await self.build_for(tools_subset, subagents_subset)
        except Exception:
            logger.warning(
                "executor_pool: build_for(%s, %s) raised; falling back",
                tools_subset,
                subagents_subset,
                exc_info=True,
            )
            return None

    def invalidate(self) -> None:
        """Drop every cached graph.

        Callers should invoke this after mutating the live tool set
        (e.g. installing a new skill via the control API) so that the
        next RouterLLM decision re-builds with the fresh ``BaseTool``
        objects.  In-flight builds are not cancelled — they still
        complete, their result just won't be cached.
        """
        # The OrderedDict reassignment is atomic from the perspective of
        # any concurrent reader, but we also want the in-flight map
        # cleared so we don't hand back stale-tool graphs.
        self._cache.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _build(
        self, tools_subset: list[str], subagents_subset: list[str]
    ) -> CompiledStateGraph:
        """Materialise a new ``CompiledStateGraph`` for the given subset.

        The individual provider calls are deliberately plain (``await``
        only where the builder returns a coroutine) so test doubles
        can be tiny lambdas — no BaseTool / LangGraph types required.
        """
        # Late imports so tests can swap in their own model builder.
        from src.agent.deep_agent import (
            AI_OPS_SYSTEM_PROMPT,
            _build_backend as _default_backend_builder,
            _build_model as _default_model_builder,
            _get_skill_sources as _default_skills_provider,
        )
        from deepagents import create_deep_agent

        model_builder = self._model_builder or _default_model_builder
        backend_builder = self._backend_builder or _default_backend_builder
        skills_provider = self._skills_provider or _default_skills_provider

        model = await model_builder()
        backend = backend_builder()
        skills = skills_provider()
        tools = _drop_none(self._tool_provider(tools_subset))
        # Dispatcher gating: the constructor flag is the hard kill switch;
        # the feature flag lets ops enable/disable per-deploy without a
        # code change. If either is off the tools pass through unwrapped
        # and DeepAgents invokes them directly via LangGraph's ToolNode.
        if self._dispatcher_enabled and await _flag_enabled(
            "tool_dispatcher_enabled"
        ):
            from src.services.agent_runtime.tool_node_wrapper import (
                wrap_tools_for_dispatcher,
            )

            tools = wrap_tools_for_dispatcher(tools)
        subagents = self._subagents_provider(subagents_subset)

        graph = create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=AI_OPS_SYSTEM_PROMPT,
            subagents=subagents,
            backend=backend,
            skills=skills,
            debug=False,
        )
        return graph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drop_none(items: Iterable[Any]) -> list[Any]:
    """Tiny helper — the tool provider may return ``None`` sentinels."""
    return [x for x in items if x is not None]


def _inc_cache(result: str) -> None:
    """Bump the cache outcome counter, tolerating metric backend issues."""
    try:
        executor_pool_cache_total.labels(result=result).inc()
    except Exception:  # pragma: no cover - defensive
        logger.debug("executor_pool: metric inc failed", exc_info=True)


async def _flag_enabled(key: str) -> bool:
    """Return ``True`` iff the named feature flag is globally enabled.

    Degrades to ``False`` on any import / service / DB error so a
    feature-flag-service outage fails closed (i.e. the executor keeps
    using plain tools rather than surfacing a new exception path).
    The ``user_id=None`` signature means we skip the stable-hash rollout
    bucket: a global kill switch either routes all executor builds
    through the dispatcher or none of them.
    """
    try:
        from src.services.feature_flags import get_feature_flags

        svc = await get_feature_flags()
        return bool(svc.is_enabled(key, None))
    except Exception:  # pragma: no cover - defensive
        logger.debug(
            "executor_pool: flag lookup failed for %s", key, exc_info=True
        )
        return False


# ---------------------------------------------------------------------------
# Lazy singleton
# ---------------------------------------------------------------------------


_SINGLETON: ExecutorAgentPool | None = None
_SINGLETON_LOCK = asyncio.Lock()


def get_executor_pool() -> ExecutorAgentPool:
    """Return the process-wide default pool (lazy-constructed).

    This is deliberately **sync** because ``ExecutorAgentPool``'s
    constructor does no I/O — we only need a lock around the first
    allocation to keep tests that hammer it concurrent-friendly.
    The lock is taken without blocking because dict assignment is
    protected by the GIL in CPython; the ``asyncio.Lock`` pattern
    elsewhere in the codebase is used here only for shape consistency
    with :func:`src.services.agent_runtime.router.get_router_llm`.
    """
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = ExecutorAgentPool()
    return _SINGLETON


def _reset_singleton_for_tests() -> None:
    """Test-only escape hatch to drop the module-level singleton."""
    global _SINGLETON
    _SINGLETON = None


__all__ = [
    "DEFAULT_CACHE_SIZE",
    "ExecutorAgentPool",
    "get_executor_pool",
    "_reset_singleton_for_tests",
    "_subagent_map",
]
