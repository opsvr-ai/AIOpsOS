"""RuntimeGateway — thin orchestration layer in front of ``/chat``.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 14.1 /
R-1.1 / R-1.2 / R-1.4 / R-1.5 / R-1.9.

Design contract
---------------
The gateway is **deliberately small**. It owns three things and three
things only:

* **Feature-flag gating.** ``gateway_enabled`` → we participate;
  ``router_llm_enabled`` → we call :class:`RouterLLM`. Anything else
  returns a ``route="full_agent"`` result that the caller treats
  exactly like the legacy path.
* **Route selection.** Via :class:`RouterLLM` we pick one of
  ``direct`` / ``executor`` / ``subagent`` / ``full_agent``. ``direct``
  short-circuits with a ready-made answer; ``executor`` / ``subagent``
  produce a narrowed DeepAgents graph via :class:`ExecutorAgentPool`;
  ``full_agent`` falls back to ``get_deep_agent()``.
* **Trajectory emission.** One ``router_decision`` event on dispatch,
  one ``turn`` event on finish (via :meth:`emit_turn`). Both are
  fire-and-forget and never block the request.

Things the gateway **does not** own — these stay in the handler:

* Session / Message persistence.
* Memory prefetch (the handler already runs ``mm.prefetch`` +
  ``mm.system_prompt_block`` in parallel before calling us).
* SSE framing / A2UI buffering / interrupt handling.
* Post-turn ``sync_turn`` + title generation.

This keeps the handler diff surgical (< 80 lines) and the gateway
easy to test end-to-end with mocked deps.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from src.core.tracing import tracer
from src.schemas.trajectory import TrajectoryEvent
from src.services.agent_runtime.executor_pool import (
    ExecutorAgentPool,
    get_executor_pool,
)
from src.services.agent_runtime.router import RouterLLM, get_router_llm
from src.services.agent_runtime.router_schema import RouterDecision
from src.services.agent_runtime.trajectory import (
    TrajectorySink,
    get_trajectory_sink,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


RouteLiteral = Literal["direct", "executor", "subagent", "full_agent"]


@dataclass(slots=True)
class GatewayContext:
    """Per-request identity + routing hints.

    This is purposefully **flat** — the caller already has access to
    User / Session objects, but we want the gateway to depend only on
    primitives so tests don't have to construct full ORM instances.
    """

    user_id: str
    username: str
    email: str
    session_id: str
    space_id: str | None = None
    platform: str = "web"


@dataclass(slots=True)
class GatewayResult:
    """Outcome of :meth:`RuntimeGateway.handle`.

    Three shapes are possible depending on ``route``:

    * ``route="direct"`` + ``direct_answer`` set → caller short-circuits
      and streams the answer as a single token.
    * ``route in ("executor", "subagent", "full_agent")`` +
      ``agent_graph`` set → caller runs ``agent_graph.astream_events``.
    * ``decision`` may be ``None`` on the short ``full_agent`` paths
      that skipped the router entirely (flag disabled / router disabled).
    """

    route: RouteLiteral
    trajectory_id: uuid.UUID
    started_at: float
    decision: RouterDecision | None = None
    direct_answer: str | None = None
    agent_graph: Any | None = None
    reason: str = ""
    # Latency breakdown — populated inside :meth:`handle` so callers can
    # forward useful timing metadata to their own metrics/log lines.
    router_latency_ms: int | None = None
    extras: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# RuntimeGateway
# ---------------------------------------------------------------------------


class RuntimeGateway:
    """Feature-flagged dispatcher over RouterLLM + ExecutorAgentPool.

    Construction is cheap: we keep references to the injected deps
    but resolve each missing one lazily inside :meth:`handle` so that
    importing this module never spins up Redis / DeepAgents / the
    trajectory sink.

    Parameters
    ----------
    router, executor_pool, trajectory_sink
        Injectable collaborators. Any of them ``None`` → resolve via
        the module-level accessors on first use. Tests overwhelmingly
        want to inject stubs here.
    flags
        ``FeatureFlagService``-like object exposing
        ``is_enabled(key, user_id)``. When ``None`` the gateway
        resolves the service via ``get_feature_flags()`` on demand.
    full_agent_provider
        Async callable returning the legacy ``get_deep_agent()`` graph.
        Tests can inject a stub so they don't pay DeepAgents build cost.
    """

    def __init__(
        self,
        router: RouterLLM | None = None,
        executor_pool: ExecutorAgentPool | None = None,
        trajectory_sink: TrajectorySink | None = None,
        flags: Any | None = None,
        *,
        full_agent_provider: Any | None = None,
    ) -> None:
        self._router = router
        self._executor_pool = executor_pool
        self._trajectory_sink = trajectory_sink
        self._flags = flags
        self._full_agent_provider = full_agent_provider

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle(
        self,
        ctx: GatewayContext,
        message: str,
        *,
        hot_block: str = "",
        history: list[str] | None = None,
        last_assistant_sha: str = "",
    ) -> GatewayResult:
        """Dispatch one user message.

        The caller has already persisted the ``Message`` row and computed
        ``hot_block`` / ``history`` from its memory manager. This method
        only returns a :class:`GatewayResult`; it does not stream.

        Never raises. Any collaborator failure → ``full_agent`` fallback.
        """
        trajectory_id = uuid.uuid4()
        started_at = time.time()

        with tracer.start_as_current_span("gateway.handle") as span:
            span.set_attribute("user_id", ctx.user_id)
            span.set_attribute("session_id", ctx.session_id)

            # ---- Flag gate 1: gateway_enabled ------------------------
            if not await self._flag_is_enabled("gateway_enabled", ctx.user_id):
                graph = await self._resolve_full_agent()
                result = GatewayResult(
                    route="full_agent",
                    trajectory_id=trajectory_id,
                    started_at=started_at,
                    decision=None,
                    direct_answer=None,
                    agent_graph=graph,
                    reason="gateway_disabled",
                )
                _set_span_summary(span, result, started_at)
                return result

            # ---- Flag gate 2: router_llm_enabled ---------------------
            if not await self._flag_is_enabled("router_llm_enabled", ctx.user_id):
                graph = await self._resolve_full_agent()
                result = GatewayResult(
                    route="full_agent",
                    trajectory_id=trajectory_id,
                    started_at=started_at,
                    decision=None,
                    direct_answer=None,
                    agent_graph=graph,
                    reason="router_llm_disabled",
                )
                _set_span_summary(span, result, started_at)
                return result

            # ---- Classify via RouterLLM ------------------------------
            t_router_start = time.perf_counter()
            decision = await self._classify(
                message,
                ctx=ctx,
                hot_block=hot_block,
                history=history,
                last_assistant_sha=last_assistant_sha,
            )
            router_latency_ms = int(
                (time.perf_counter() - t_router_start) * 1000
            )

            # Fire off the router_decision trajectory event early — it's
            # cheap and keeps observability alive even if downstream
            # graph resolution fails.
            self._emit_router_decision(
                ctx, trajectory_id=trajectory_id, decision=decision,
                latency_ms=router_latency_ms,
            )

            # ---- Route selection -------------------------------------
            if decision.route == "direct" and decision.direct_answer:
                result = GatewayResult(
                    route="direct",
                    trajectory_id=trajectory_id,
                    started_at=started_at,
                    decision=decision,
                    direct_answer=decision.direct_answer,
                    agent_graph=None,
                    reason="router_direct",
                    router_latency_ms=router_latency_ms,
                )
                _set_span_summary(span, result, started_at)
                return result

            # executor / subagent → narrow graph from the pool
            if decision.route in ("executor", "subagent"):
                pool = self._resolve_executor_pool()
                graph: Any | None = None
                try:
                    graph = await pool.get_for(decision)
                except Exception:
                    logger.debug(
                        "gateway: executor_pool.get_for raised; falling back",
                        exc_info=True,
                    )
                    graph = None

                if graph is not None:
                    result = GatewayResult(
                        route=decision.route,
                        trajectory_id=trajectory_id,
                        started_at=started_at,
                        decision=decision,
                        direct_answer=None,
                        agent_graph=graph,
                        reason="router_" + decision.route,
                        router_latency_ms=router_latency_ms,
                    )
                    _set_span_summary(span, result, started_at)
                    return result

            # Anything else → full-agent fallback (covers:
            #   * direct route without a direct_answer
            #   * executor/subagent that the pool couldn't narrow
            #   * router fallback decisions (confidence=0.0)
            # ). The legacy graph exists for exactly this reason.
            graph = await self._resolve_full_agent()
            result = GatewayResult(
                route="full_agent",
                trajectory_id=trajectory_id,
                started_at=started_at,
                decision=decision,
                direct_answer=None,
                agent_graph=graph,
                reason="router_fallback",
                router_latency_ms=router_latency_ms,
            )
            _set_span_summary(span, result, started_at)
            return result

    async def emit_turn(
        self,
        ctx: GatewayContext,
        *,
        trajectory_id: uuid.UUID,
        started_at: float,
        outcome: str,
        message_preview: str,
        route: RouteLiteral,
        model: str | None = None,
    ) -> None:
        """Emit the per-turn trajectory event after the handler is done.

        Fire-and-forget. The caller already owns the full
        happy/error accounting for SSE; this is just the one-line
        ``agent_trajectories(kind='turn')`` row.
        """
        sink = await self._resolve_sink()
        if sink is None:
            return
        try:
            ev = TrajectoryEvent(
                id=uuid.uuid4(),
                session_id=_as_uuid(ctx.session_id),
                user_id=_as_uuid(ctx.user_id),
                space_id=_as_uuid_or_none(ctx.space_id),
                parent_id=trajectory_id,
                kind="turn",
                ts=datetime.now(tz=timezone.utc),
                outcome=_coerce_outcome(outcome),
                latency_ms=max(0, int((time.time() - started_at) * 1000)),
                model=model,
                data={
                    "message_preview": (message_preview or "")[:200],
                    "route": route,
                },
                tags=[f"platform:{ctx.platform}", f"route:{route}"],
                metadata={},
            )
            sink.emit(ev)
        except Exception:
            logger.debug("gateway: emit_turn failed", exc_info=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _classify(
        self,
        message: str,
        *,
        ctx: GatewayContext,
        hot_block: str,
        history: list[str] | None,
        last_assistant_sha: str,
    ) -> RouterDecision:
        """Call the router — on any failure return a safe fallback."""
        router = await self._resolve_router()
        if router is None:
            return RouterDecision.fallback_executor("router_unavailable")
        try:
            return await router.classify(
                message,
                hot_block=hot_block,
                history=history,
                user_id=ctx.user_id or "anonymous",
                last_assistant_sha=last_assistant_sha or "",
            )
        except Exception:
            logger.debug("gateway: router.classify raised", exc_info=True)
            return RouterDecision.fallback_executor("router_exception")

    def _emit_router_decision(
        self,
        ctx: GatewayContext,
        *,
        trajectory_id: uuid.UUID,
        decision: RouterDecision,
        latency_ms: int,
    ) -> None:
        """Non-blocking router_decision event."""
        # Resolve sink lazily — if it hasn't been primed yet we swallow.
        sink = self._trajectory_sink
        if sink is None:
            # We can't `await` here (this method is sync for the caller),
            # so we skip the decision trace when no sink was injected and
            # none has been primed yet. `emit_turn` later will have a
            # chance to warm it up. This is deliberate — we prefer losing
            # one diagnostic event to adding latency to the request path.
            return
        try:
            ev = TrajectoryEvent(
                id=trajectory_id,
                session_id=_as_uuid(ctx.session_id),
                user_id=_as_uuid(ctx.user_id),
                space_id=_as_uuid_or_none(ctx.space_id),
                kind="router_decision",
                ts=datetime.now(tz=timezone.utc),
                outcome="ok",
                latency_ms=max(0, int(latency_ms)),
                data={
                    "route": decision.route,
                    "confidence": decision.confidence,
                    "reason": decision.reason[:200],
                    "suggested_tools": list(decision.suggested_tools),
                    "subagent_name": decision.subagent_name,
                    "direct_answer_len": len(decision.direct_answer or ""),
                },
                tags=[f"platform:{ctx.platform}", f"route:{decision.route}"],
                metadata={},
            )
            sink.emit(ev)
        except Exception:
            logger.debug("gateway: emit_router_decision failed", exc_info=True)

    async def _flag_is_enabled(self, key: str, user_id: str) -> bool:
        svc = await self._resolve_flags()
        if svc is None:
            return False
        try:
            return bool(svc.is_enabled(key, user_id or None))
        except Exception:
            logger.debug("gateway: flag lookup failed", exc_info=True)
            return False

    async def _resolve_router(self) -> RouterLLM | None:
        if self._router is not None:
            return self._router
        try:
            self._router = await get_router_llm()
        except Exception:
            logger.debug("gateway: get_router_llm failed", exc_info=True)
            return None
        return self._router

    def _resolve_executor_pool(self) -> ExecutorAgentPool:
        if self._executor_pool is not None:
            return self._executor_pool
        self._executor_pool = get_executor_pool()
        return self._executor_pool

    async def _resolve_sink(self) -> TrajectorySink | None:
        if self._trajectory_sink is not None:
            return self._trajectory_sink
        try:
            self._trajectory_sink = await get_trajectory_sink()
        except Exception:
            logger.debug("gateway: get_trajectory_sink failed", exc_info=True)
            return None
        return self._trajectory_sink

    async def _resolve_flags(self) -> Any | None:
        if self._flags is not None:
            return self._flags
        try:
            from src.services.feature_flags import get_feature_flags

            self._flags = await get_feature_flags()
        except Exception:
            logger.debug("gateway: get_feature_flags failed", exc_info=True)
            return None
        return self._flags

    async def _resolve_full_agent(self) -> Any:
        """Return the legacy ``get_deep_agent()`` graph (or injected stub)."""
        if self._full_agent_provider is not None:
            try:
                result = self._full_agent_provider()
                if hasattr(result, "__await__"):
                    return await result
                return result
            except Exception:
                logger.exception("gateway: full_agent_provider failed")
                # fall through to default lookup
        from src.agent.deep_agent import get_deep_agent

        return await get_deep_agent()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_uuid(value: Any) -> uuid.UUID:
    """Coerce ``value`` to :class:`uuid.UUID`, tolerating strings."""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except Exception:
        # TrajectoryEvent requires a UUID; synthesise a deterministic
        # one from the string so we don't crash the request path.
        return uuid.uuid5(uuid.NAMESPACE_DNS, f"gateway:{value}")


def _as_uuid_or_none(value: Any) -> uuid.UUID | None:
    """Parse a permissive UUID input, returning ``None`` on any parse error.

    Unlike :func:`_as_uuid` we do **not** synthesise a UUID from garbage
    input — ``None`` is the right answer for missing / malformed
    ``space_id`` so downstream trajectory rows keep ``space_id`` NULL.
    """
    if value in (None, "", b""):
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except Exception:
        return None


def _coerce_outcome(outcome: str) -> str:
    """Constrain an outcome string to the schema's enum set."""
    allowed = {"ok", "error", "timeout", "rejected"}
    if outcome in allowed:
        return outcome
    return "ok" if outcome == "success" else "error"


def _set_span_summary(span: Any, result: GatewayResult, started_at: float) -> None:
    try:
        span.set_attribute("route", result.route)
        span.set_attribute(
            "confidence",
            result.decision.confidence if result.decision is not None else 0.0,
        )
        span.set_attribute(
            "duration_ms", int((time.time() - started_at) * 1000)
        )
        if result.reason:
            span.set_attribute("reason", result.reason)
    except Exception:  # pragma: no cover - tracing is best-effort
        logger.debug("gateway: span attribute set failed", exc_info=True)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


_SINGLETON: RuntimeGateway | None = None


async def get_runtime_gateway() -> RuntimeGateway:
    """Return the process-wide default gateway (lazy-constructed).

    The default instance pulls dependencies from their own singletons
    on first use; tests that want full control should instantiate
    :class:`RuntimeGateway` directly.
    """
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = RuntimeGateway()
    return _SINGLETON


def _reset_singleton_for_tests() -> None:
    """Test-only escape hatch — drop the cached singleton."""
    global _SINGLETON
    _SINGLETON = None


__all__ = [
    "GatewayContext",
    "GatewayResult",
    "RuntimeGateway",
    "get_runtime_gateway",
    "_reset_singleton_for_tests",
]
