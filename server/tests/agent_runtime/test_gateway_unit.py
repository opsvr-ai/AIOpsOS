"""Unit tests for :class:`RuntimeGateway`.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 14.1 /
R-1.1 / R-1.2 / R-1.4 / R-1.5 / R-1.9.

All tests use injectable stubs — no real RouterLLM, no real
ExecutorAgentPool, no real Kafka / Redis / DB. Each test exercises a
single decision-tree branch in :meth:`RuntimeGateway.handle`.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.services.agent_runtime.gateway import (
    GatewayContext,
    RuntimeGateway,
    _reset_singleton_for_tests,
    get_runtime_gateway,
)
from src.services.agent_runtime.router_schema import RouterDecision


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


@dataclass
class _StubFlags:
    """Minimal ``FeatureFlagService``-like stub."""

    values: dict[str, bool] = field(default_factory=dict)
    calls: list[tuple[str, str | None]] = field(default_factory=list)
    raise_on_lookup: bool = False

    def is_enabled(self, key: str, user_id: str | None = None) -> bool:
        self.calls.append((key, user_id))
        if self.raise_on_lookup:
            raise RuntimeError("flag service exploded")
        return bool(self.values.get(key, False))


@dataclass
class _StubRouter:
    """Router stub with a programmable classify() result."""

    decision: RouterDecision | None = None
    side_effect: BaseException | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def classify(
        self,
        message: str,
        *,
        hot_block: str = "",
        history: list[str] | None = None,
        user_id: str = "anonymous",
        last_assistant_sha: str = "",
    ) -> RouterDecision:
        self.calls.append(
            {
                "message": message,
                "hot_block": hot_block,
                "history": list(history or []),
                "user_id": user_id,
                "last_assistant_sha": last_assistant_sha,
            }
        )
        if self.side_effect is not None:
            raise self.side_effect
        if self.decision is None:
            return RouterDecision.fallback_executor("no-decision")
        return self.decision


@dataclass
class _StubExecutorPool:
    """ExecutorAgentPool stub.

    ``graph`` is returned on matching (executor/subagent) routes, ``None``
    means "fallback". Raise-by-flag path lets tests exercise the
    try/except branch.
    """

    graph: Any | None = None
    raise_on_get: bool = False
    calls: list[RouterDecision] = field(default_factory=list)

    async def get_for(self, decision: RouterDecision) -> Any | None:
        self.calls.append(decision)
        if self.raise_on_get:
            raise RuntimeError("pool exploded")
        return self.graph


@dataclass
class _StubSink:
    """TrajectorySink stub — captures every emit()."""

    events: list[Any] = field(default_factory=list)
    raise_on_emit: bool = False

    def emit(self, event: Any) -> None:
        if self.raise_on_emit:
            raise RuntimeError("sink exploded")
        self.events.append(event)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_ctx(**overrides: Any) -> GatewayContext:
    defaults = {
        "user_id": str(uuid.uuid4()),
        "username": "alice",
        "email": "alice@test.local",
        "session_id": str(uuid.uuid4()),
        "space_id": None,
        "platform": "web",
    }
    defaults.update(overrides)
    return GatewayContext(**defaults)  # type: ignore[arg-type]


def _enabled_flags(**extra: bool) -> _StubFlags:
    values = {"gateway_enabled": True, "router_llm_enabled": True}
    values.update(extra)
    return _StubFlags(values=values)


def _full_agent_stub(marker: str = "FULL_AGENT") -> Any:
    """Return ``(provider, sentinel)`` for injecting a fake full agent."""

    class _Sentinel:
        def __init__(self, m: str) -> None:
            self.marker = m

        def __repr__(self) -> str:  # pragma: no cover - debug aid
            return f"_FullAgentSentinel({self.marker!r})"

    sentinel = _Sentinel(marker)

    async def _provider() -> object:
        return sentinel

    return _provider, sentinel


@pytest.fixture(autouse=True)
def _reset_gateway_singleton():
    _reset_singleton_for_tests()
    yield
    _reset_singleton_for_tests()


# ---------------------------------------------------------------------------
# Flag gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_disabled_returns_full_agent():
    """``gateway_enabled=False`` → full-agent route, no router invocation."""
    flags = _StubFlags(values={"gateway_enabled": False, "router_llm_enabled": True})
    router = _StubRouter()
    provider, sentinel = _full_agent_stub()

    gw = RuntimeGateway(
        router=router,
        executor_pool=_StubExecutorPool(),
        trajectory_sink=_StubSink(),
        flags=flags,
        full_agent_provider=provider,
    )

    result = await gw.handle(_make_ctx(), "hi")

    assert result.route == "full_agent"
    assert result.reason == "gateway_disabled"
    assert result.agent_graph is sentinel
    assert result.decision is None
    assert result.direct_answer is None
    # Router never consulted.
    assert router.calls == []
    # Only the first gate was read — router_llm_enabled not even queried.
    keys = [k for k, _ in flags.calls]
    assert "gateway_enabled" in keys
    assert "router_llm_enabled" not in keys


@pytest.mark.asyncio
async def test_router_disabled_returns_full_agent():
    """``router_llm_enabled=False`` short-circuits to full-agent."""
    flags = _StubFlags(values={"gateway_enabled": True, "router_llm_enabled": False})
    router = _StubRouter(decision=RouterDecision.fallback_executor("should-not-run"))
    provider, sentinel = _full_agent_stub()

    gw = RuntimeGateway(
        router=router,
        executor_pool=_StubExecutorPool(),
        trajectory_sink=_StubSink(),
        flags=flags,
        full_agent_provider=provider,
    )

    result = await gw.handle(_make_ctx(), "hi")

    assert result.route == "full_agent"
    assert result.reason == "router_llm_disabled"
    assert result.agent_graph is sentinel
    assert router.calls == []


@pytest.mark.asyncio
async def test_flag_service_exception_degrades_to_full_agent():
    """If the flag service raises we treat every flag as False."""
    flags = _StubFlags(raise_on_lookup=True)
    router = _StubRouter()
    provider, sentinel = _full_agent_stub()

    gw = RuntimeGateway(
        router=router,
        executor_pool=_StubExecutorPool(),
        trajectory_sink=_StubSink(),
        flags=flags,
        full_agent_provider=provider,
    )

    result = await gw.handle(_make_ctx(), "hi")

    assert result.route == "full_agent"
    assert result.reason == "gateway_disabled"
    assert result.agent_graph is sentinel


# ---------------------------------------------------------------------------
# Route: direct
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_route_short_circuits():
    """``route=direct`` with a direct_answer → no pool, no agent_graph."""
    sink = _StubSink()
    router = _StubRouter(
        decision=RouterDecision(
            route="direct",
            direct_answer="你好！",
            suggested_tools=[],
            reason="greeting",
            confidence=0.9,
        )
    )
    pool = _StubExecutorPool()

    gw = RuntimeGateway(
        router=router,
        executor_pool=pool,
        trajectory_sink=sink,
        flags=_enabled_flags(),
    )

    ctx = _make_ctx()
    result = await gw.handle(ctx, "hi", hot_block="hot", history=["h1", "h2"])

    assert result.route == "direct"
    assert result.direct_answer == "你好！"
    assert result.agent_graph is None
    assert result.decision is not None and result.decision.route == "direct"
    assert result.reason == "router_direct"
    # Pool never consulted.
    assert pool.calls == []
    # Router got our prefetched block + history.
    assert router.calls[0]["hot_block"] == "hot"
    assert router.calls[0]["history"] == ["h1", "h2"]
    # A router_decision trajectory event was emitted.
    assert len(sink.events) == 1
    assert sink.events[0].kind == "router_decision"
    assert sink.events[0].data["route"] == "direct"


@pytest.mark.asyncio
async def test_direct_route_without_answer_falls_back_to_full_agent():
    """``route=direct`` but empty direct_answer → full-agent fallback."""
    provider, sentinel = _full_agent_stub()
    router = _StubRouter(
        decision=RouterDecision(
            route="direct",
            direct_answer=None,
            suggested_tools=[],
            reason="confused",
            confidence=0.9,
        )
    )
    pool = _StubExecutorPool()

    gw = RuntimeGateway(
        router=router,
        executor_pool=pool,
        trajectory_sink=_StubSink(),
        flags=_enabled_flags(),
        full_agent_provider=provider,
    )

    result = await gw.handle(_make_ctx(), "hi")

    assert result.route == "full_agent"
    assert result.agent_graph is sentinel
    assert result.direct_answer is None
    assert result.reason == "router_fallback"
    assert pool.calls == []


# ---------------------------------------------------------------------------
# Route: executor / subagent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_route_uses_narrow_graph():
    narrow = object()
    sink = _StubSink()
    router = _StubRouter(
        decision=RouterDecision(
            route="executor",
            suggested_tools=["grep_kb"],
            reason="ops query",
            confidence=0.85,
        )
    )
    pool = _StubExecutorPool(graph=narrow)

    gw = RuntimeGateway(
        router=router,
        executor_pool=pool,
        trajectory_sink=sink,
        flags=_enabled_flags(),
    )

    result = await gw.handle(_make_ctx(), "grep the wiki please")

    assert result.route == "executor"
    assert result.agent_graph is narrow
    assert result.direct_answer is None
    assert result.decision.route == "executor"
    assert result.reason == "router_executor"
    assert len(pool.calls) == 1


@pytest.mark.asyncio
async def test_subagent_route_uses_narrow_graph():
    narrow = object()
    router = _StubRouter(
        decision=RouterDecision(
            route="subagent",
            subagent_name="monitor",
            suggested_tools=[],
            reason="monitor request",
            confidence=0.9,
        )
    )
    pool = _StubExecutorPool(graph=narrow)

    gw = RuntimeGateway(
        router=router,
        executor_pool=pool,
        trajectory_sink=_StubSink(),
        flags=_enabled_flags(),
    )

    result = await gw.handle(_make_ctx(), "check the monitor")

    assert result.route == "subagent"
    assert result.agent_graph is narrow
    assert result.reason == "router_subagent"
    assert len(pool.calls) == 1


@pytest.mark.asyncio
async def test_executor_route_without_graph_falls_back():
    """When the pool returns ``None`` we fall back to the full agent."""
    provider, sentinel = _full_agent_stub()
    router = _StubRouter(
        decision=RouterDecision(
            route="executor",
            suggested_tools=["grep_kb"],
            reason="ok",
            confidence=0.2,  # below floor → pool returns None
        )
    )
    pool = _StubExecutorPool(graph=None)

    gw = RuntimeGateway(
        router=router,
        executor_pool=pool,
        trajectory_sink=_StubSink(),
        flags=_enabled_flags(),
        full_agent_provider=provider,
    )

    result = await gw.handle(_make_ctx(), "query something")

    assert result.route == "full_agent"
    assert result.agent_graph is sentinel
    assert result.reason == "router_fallback"


@pytest.mark.asyncio
async def test_pool_exception_falls_back_to_full_agent():
    """Pool errors never surface — caller gets the legacy graph."""
    provider, sentinel = _full_agent_stub()
    router = _StubRouter(
        decision=RouterDecision(
            route="executor",
            suggested_tools=["grep_kb"],
            reason="ok",
            confidence=0.9,
        )
    )
    pool = _StubExecutorPool(raise_on_get=True)

    gw = RuntimeGateway(
        router=router,
        executor_pool=pool,
        trajectory_sink=_StubSink(),
        flags=_enabled_flags(),
        full_agent_provider=provider,
    )

    result = await gw.handle(_make_ctx(), "say hi")

    assert result.route == "full_agent"
    assert result.agent_graph is sentinel


# ---------------------------------------------------------------------------
# Router failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_exception_falls_back_to_full_agent():
    """A raising router becomes a ``route=executor, confidence=0`` fallback
    which the gateway funnels into the full-agent branch."""
    provider, sentinel = _full_agent_stub()
    router = _StubRouter(side_effect=RuntimeError("router exploded"))
    pool = _StubExecutorPool(graph=None)  # low-confidence → pool returns None

    gw = RuntimeGateway(
        router=router,
        executor_pool=pool,
        trajectory_sink=_StubSink(),
        flags=_enabled_flags(),
        full_agent_provider=provider,
    )

    result = await gw.handle(_make_ctx(), "question")

    assert result.route == "full_agent"
    assert result.agent_graph is sentinel
    assert result.decision is not None
    assert "router_exception" in result.decision.reason


@pytest.mark.asyncio
async def test_sink_exception_never_breaks_dispatch():
    """If emit() raises we must still return a valid result."""
    router = _StubRouter(
        decision=RouterDecision(
            route="direct",
            direct_answer="你好",
            suggested_tools=[],
            reason="greet",
            confidence=0.9,
        )
    )
    sink = _StubSink(raise_on_emit=True)

    gw = RuntimeGateway(
        router=router,
        executor_pool=_StubExecutorPool(),
        trajectory_sink=sink,
        flags=_enabled_flags(),
    )

    result = await gw.handle(_make_ctx(), "hi")

    assert result.route == "direct"
    assert result.direct_answer == "你好"


# ---------------------------------------------------------------------------
# emit_turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_turn_emits_well_formed_trajectory_event():
    sink = _StubSink()
    gw = RuntimeGateway(
        router=_StubRouter(),
        executor_pool=_StubExecutorPool(),
        trajectory_sink=sink,
        flags=_enabled_flags(),
    )

    ctx = _make_ctx(space_id=str(uuid.uuid4()))
    trajectory_id = uuid.uuid4()

    await gw.emit_turn(
        ctx,
        trajectory_id=trajectory_id,
        started_at=0.0,  # will produce a big latency — that's fine
        outcome="ok",
        message_preview="hi" * 500,
        route="executor",
        model="deepseek-chat",
    )

    assert len(sink.events) == 1
    ev = sink.events[0]
    assert ev.kind == "turn"
    assert ev.outcome == "ok"
    assert ev.model == "deepseek-chat"
    assert ev.parent_id == trajectory_id
    assert ev.data["route"] == "executor"
    # Preview is trimmed to 200 chars.
    assert len(ev.data["message_preview"]) <= 200
    assert "platform:web" in ev.tags
    assert "route:executor" in ev.tags


@pytest.mark.asyncio
async def test_emit_turn_coerces_invalid_outcome_to_error():
    sink = _StubSink()
    gw = RuntimeGateway(
        router=_StubRouter(),
        executor_pool=_StubExecutorPool(),
        trajectory_sink=sink,
        flags=_enabled_flags(),
    )
    await gw.emit_turn(
        _make_ctx(),
        trajectory_id=uuid.uuid4(),
        started_at=0.0,
        outcome="bananas",  # unknown
        message_preview="x",
        route="direct",
    )
    assert sink.events[0].outcome == "error"


@pytest.mark.asyncio
async def test_emit_turn_is_noop_when_sink_unresolvable():
    """With no sink injected and the singleton unavailable, emit_turn
    must not raise."""
    gw = RuntimeGateway(
        router=_StubRouter(),
        executor_pool=_StubExecutorPool(),
        trajectory_sink=None,
        flags=_enabled_flags(),
    )
    # Force the lazy sink resolver to fail: we monkey-patch the accessor
    # via the bound method.

    async def _fake_resolve() -> None:
        return None

    gw._resolve_sink = _fake_resolve  # type: ignore[assignment]

    # Should be a no-op — no raise.
    await gw.emit_turn(
        _make_ctx(),
        trajectory_id=uuid.uuid4(),
        started_at=0.0,
        outcome="ok",
        message_preview="x",
        route="direct",
    )


# ---------------------------------------------------------------------------
# Singleton plumbing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_runtime_gateway_is_singleton():
    a = await get_runtime_gateway()
    b = await get_runtime_gateway()
    assert a is b
    _reset_singleton_for_tests()
    c = await get_runtime_gateway()
    assert c is not a


# ---------------------------------------------------------------------------
# Context / latency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_latency_is_measured():
    """``router_latency_ms`` is populated whenever RouterLLM runs."""
    router = _StubRouter(
        decision=RouterDecision(
            route="direct",
            direct_answer="hi",
            suggested_tools=[],
            reason="greet",
            confidence=0.9,
        )
    )

    async def _slow_classify(*args, **kwargs):
        await asyncio.sleep(0.02)
        return router.decision

    router.classify = _slow_classify  # type: ignore[assignment]

    gw = RuntimeGateway(
        router=router,
        executor_pool=_StubExecutorPool(),
        trajectory_sink=_StubSink(),
        flags=_enabled_flags(),
    )

    result = await gw.handle(_make_ctx(), "hi")

    assert result.router_latency_ms is not None
    assert result.router_latency_ms >= 10  # ~20ms sleep


@pytest.mark.asyncio
async def test_started_at_populated():
    router = _StubRouter(
        decision=RouterDecision(
            route="direct",
            direct_answer="hi",
            suggested_tools=[],
            reason="greet",
            confidence=0.9,
        )
    )
    gw = RuntimeGateway(
        router=router,
        executor_pool=_StubExecutorPool(),
        trajectory_sink=_StubSink(),
        flags=_enabled_flags(),
    )
    result = await gw.handle(_make_ctx(), "hi")
    assert result.started_at > 0


# ---------------------------------------------------------------------------
# Sink-less direct path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_route_without_sink_still_succeeds():
    """When no sink was injected we simply skip the router_decision event
    rather than blocking the request to resolve one."""
    router = _StubRouter(
        decision=RouterDecision(
            route="direct",
            direct_answer="hi",
            suggested_tools=[],
            reason="greet",
            confidence=0.9,
        )
    )
    gw = RuntimeGateway(
        router=router,
        executor_pool=_StubExecutorPool(),
        trajectory_sink=None,
        flags=_enabled_flags(),
    )

    result = await gw.handle(_make_ctx(), "hi")

    assert result.route == "direct"
    assert result.direct_answer == "hi"


# ---------------------------------------------------------------------------
# Smoke: space_id handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_space_id_does_not_crash_emit_turn():
    """A non-UUID space_id is coerced to None on the trajectory event."""
    sink = _StubSink()
    gw = RuntimeGateway(
        router=_StubRouter(),
        executor_pool=_StubExecutorPool(),
        trajectory_sink=sink,
        flags=_enabled_flags(),
    )
    await gw.emit_turn(
        _make_ctx(space_id="not-a-uuid"),
        trajectory_id=uuid.uuid4(),
        started_at=0.0,
        outcome="ok",
        message_preview="x",
        route="direct",
    )
    assert len(sink.events) == 1
    assert sink.events[0].space_id is None
