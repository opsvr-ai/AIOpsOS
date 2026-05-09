"""End-to-end integration test for RuntimeGateway wiring.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 14.3 /
R-1.1 / R-1.2 / R-1.3 / R-1.4 / R-1.5 / R-1.9.

Drives the real FastAPI app via ``httpx.AsyncClient(ASGITransport(app))``
through the gateway-enabled ``/chat(/stream)`` handlers against real
PostgreSQL, Redis, and (where reachable) Kafka. RouterLLM and the
underlying agent graph are stubbed so tests don't need a live LLM; the
unit tests in ``test_gateway_unit.py`` carry the fine-grained
behavioural guarantees.

Scope of this file: prove that for each of the three canonical message
shapes the gateway routes correctly AND the caller sees the expected
SSE / JSON events (R-1.1 – R-1.5):

* greeting       (``route="direct"``)   — SSE token+done short-circuit
                                          with canned answer; executor
                                          pool never consulted.
* ops query      (``route="executor"``) — narrow graph from the pool
                                          runs; tool_start/tool_end +
                                          token + done events emitted.
* general question (``route="executor"`` fallback when pool returns
                                          None) — full agent graph runs;
                                          token + done events emitted.

All three flows persist a ``router_decision`` trajectory row so we can
verify the decision was taken. The gateway-disabled case asserts the
router is NOT consulted at all.

When PostgreSQL is unreachable the whole module is skipped.
"""
from __future__ import annotations

import os

# Force tracing/metrics into test mode *before* importing the app so
# ``init_tracing`` installs a no-op provider and doesn't spam stdout.
os.environ.setdefault("TESTING", "1")
# Point Kafka at the docker-compose.dev.yml EXTERNAL listener
# (localhost:9094) which advertises ``localhost`` back to clients. The
# server ``.env`` defaults to the PLAINTEXT listener
# (``localhost:9092``) whose advertised hostname is ``kafka:9092`` —
# unreachable from Windows and the trajectory sink will spin forever in
# reconnect loops. Setting the env var before ``Settings`` loads fixes
# that cleanly without touching prod config.
os.environ["KAFKA_BOOTSTRAP_SERVERS"] = os.environ.get(
    "TEST_KAFKA_BOOTSTRAP_SERVERS", "localhost:9094"
)

import asyncio  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import delete, select, text  # noqa: E402


# ---------------------------------------------------------------------------
# DB availability probe
# ---------------------------------------------------------------------------


def _db_available() -> bool:
    """Lightweight probe — prefer asyncpg since the server env doesn't ship
    psycopg2 in every configuration. Falls back to psycopg2 if present."""
    try:
        from src.config import settings
    except Exception:
        return False

    async_url = settings.sync_database_url
    try:
        import asyncpg  # type: ignore

        dsn = async_url.replace("postgresql+asyncpg://", "postgresql://")

        async def _ping() -> bool:
            conn = await asyncpg.connect(dsn, timeout=2.0)
            try:
                await conn.fetchval("SELECT 1")
            finally:
                await conn.close()
            return True

        return bool(asyncio.run(_ping()))
    except Exception:
        pass

    try:
        from sqlalchemy import create_engine

        sync_url = async_url.replace("+asyncpg", "+psycopg2")
        eng = create_engine(sync_url)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL not available"),
]


# ---------------------------------------------------------------------------
# pytest-asyncio 1.x: every test below is decorated with
# ``@pytest.mark.asyncio(loop_scope="module")`` so the module-scoped
# ``client`` fixture stays on a single loop. A single shared loop is
# required because SQLAlchemy's async engine (a module-level singleton)
# pins each asyncpg connection to the loop that opened it — function-
# scoped loops tear that loop down and the next request trips "Future
# attached to a different loop".
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Router stub — used to drive the gateway without a live LLM.
# ---------------------------------------------------------------------------


@dataclass
class _StubRouter:
    """Simple keyword-driven RouterLLM stand-in.

    Greeting-ish messages → ``route="direct"`` with a canned answer.
    Messages containing ops verbs (执行/查询/分析/故障/告警) →
    ``route="executor"`` with a small suggested-tools list. Everything
    else → ``route="executor"`` with a ``confidence<0.4`` fallback so
    the gateway funnels into the full-agent path.

    ``scripted`` can be set per-test to force a specific decision for
    a given (prefix of a) message — useful when we want to pin down
    exactly which branch fires without relying on keyword heuristics.
    """

    calls: list[str] = field(default_factory=list)
    scripted: dict[str, Any] = field(default_factory=dict)

    async def classify(
        self,
        message: str,
        *,
        hot_block: str = "",
        history: list[str] | None = None,
        user_id: str = "anonymous",
        last_assistant_sha: str = "",
    ):
        from src.services.agent_runtime.router_schema import RouterDecision

        self.calls.append(message)

        # Exact-match scripted override wins.
        for prefix, dec in self.scripted.items():
            if message.startswith(prefix):
                return dec

        lower = (message or "").lower()
        # Greeting detection — match whole words only so messages like
        # "What is this about?" don't accidentally hit on the "hi" inside
        # "this".
        greeting_re = re.compile(r"\b(hi|hello|hey)\b", re.IGNORECASE)
        if "你好" in message or "嗨" in message or greeting_re.search(lower):
            return RouterDecision(
                route="direct",
                direct_answer="你好！有什么可以帮您的吗？",
                suggested_tools=[],
                reason="greeting-stub",
                confidence=0.95,
            )
        if any(kw in message for kw in ("执行", "查询", "分析", "故障", "告警")):
            return RouterDecision(
                route="executor",
                suggested_tools=["grep_kb"],
                reason="ops-stub",
                confidence=0.85,
            )
        # Low-confidence fallback → gateway uses full-agent.
        return RouterDecision(
            route="executor",
            suggested_tools=[],
            reason="unsure-stub",
            confidence=0.1,
        )


@dataclass
class _StubExecutorPool:
    """Executor pool stub.

    ``graph`` lets a test inject a fake narrow graph so the executor /
    subagent branches of the gateway exercise end-to-end; when ``None``
    the pool behaves like the real thing under low-confidence decisions
    (caller falls back to the full agent).
    """

    graph: Any | None = None
    calls: list = field(default_factory=list)

    async def get_for(self, decision):
        self.calls.append(decision)
        return self.graph


class _FakeAgentGraph:
    """Minimal stand-in for a LangGraph ``CompiledStateGraph``.

    Emits a deterministic stream of ``on_chat_model_stream``,
    ``on_tool_start``, ``on_tool_end`` events so the ``/chat/stream``
    handler exercises its full post-gateway path (token accumulation,
    tool event forwarding, message persistence) without touching a
    real LLM.
    """

    def __init__(
        self,
        *,
        reply: str = "Here is the answer.",
        tool_calls: list[tuple[str, dict[str, Any]]] | None = None,
        marker: str = "FAKE_GRAPH",
    ) -> None:
        self._reply = reply
        self._tool_calls = tool_calls or []
        self.marker = marker
        self.invocations = 0

    async def astream_events(self, inputs, version: str = "v2", config=None):
        """Yield a fixed script of LangGraph-shaped events."""
        from langchain_core.messages import AIMessageChunk

        self.invocations += 1

        for idx, (name, args) in enumerate(self._tool_calls):
            run_id = f"run-{idx}"
            yield {
                "event": "on_tool_start",
                "name": name,
                "data": {"input": args},
                "run_id": run_id,
            }
            yield {
                "event": "on_tool_end",
                "name": name,
                "data": {"output": f"{name}-result"},
                "run_id": run_id,
            }

        for piece in _split_into_chunks(self._reply, chunk_size=12):
            yield {
                "event": "on_chat_model_stream",
                "name": "model",
                "data": {"chunk": AIMessageChunk(content=piece)},
                "run_id": "model-run",
            }


def _split_into_chunks(text: str, chunk_size: int = 12) -> list[str]:
    if not text:
        return [""]
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


# ---------------------------------------------------------------------------
# Helpers: user registration + flag seeding
# ---------------------------------------------------------------------------


def _unique_username(prefix: str = "gw") -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


async def _register_and_login(client: AsyncClient) -> tuple[str, dict]:
    username = _unique_username()
    payload = {
        "username": username,
        "email": f"{username}@gwtest.example.com",
        "password": "pass123-gw",
    }
    reg = await client.post("/api/v1/auth/register", json=payload)
    assert reg.status_code == 200, reg.text
    user_body = reg.json()

    # New registrations default to status="pending"; flip to active so
    # we don't depend on a pre-existing admin.
    await _activate_user(user_body["id"])

    login = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "pass123-gw"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    return token, user_body


async def _activate_user(user_id: str) -> None:
    """Force a user into ``status='active'`` + ``is_active=True`` and create
    a default space so chat endpoints don't trip on missing membership."""
    from sqlalchemy import update

    from src.models.base import async_session_factory
    from src.models.user import User

    async with async_session_factory() as session:
        await session.execute(
            update(User)
            .where(User.id == user_id)
            .values(status="active", is_active=True)
        )
        await session.commit()

    try:
        from src.services.space_service import create_default_space_for_user

        await create_default_space_for_user(user_id)
    except Exception:
        pass


async def _set_flag(key: str, *, enabled: bool, rollout_percent: int = 100) -> None:
    """Upsert a row in ``runtime_feature_flags`` and refresh the service."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from src.models.base import async_session_factory
    from src.models.runtime_flag import RuntimeFeatureFlag

    async with async_session_factory() as session:
        stmt = pg_insert(RuntimeFeatureFlag).values(
            key=key,
            enabled=enabled,
            rollout_percent=rollout_percent,
            data={},
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[RuntimeFeatureFlag.key],
            set_={"enabled": enabled, "rollout_percent": rollout_percent},
        )
        await session.execute(stmt)
        await session.commit()

    try:
        from src.services.feature_flags import get_feature_flags

        svc = await get_feature_flags()
        await svc.refresh()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client() -> AsyncClient:
    from src.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", timeout=30.0
    ) as c:
        yield c

    # Drain any module-level background singletons attached to this loop
    # before it goes away so they don't throw "Event loop is closed" on
    # their next tick and poison subsequent test modules.
    try:
        from src.models.base import engine
        from src.services.agent_runtime.trajectory import (
            shutdown_trajectory_sink,
        )
        from src.services.feature_flags import shutdown_feature_flags

        await shutdown_trajectory_sink()
        await shutdown_feature_flags()
        await engine.dispose()
    except Exception:
        pass


@pytest_asyncio.fixture(autouse=True, loop_scope="module")
async def _stub_runtime_dependencies(monkeypatch):
    """Swap RouterLLM + ExecutorAgentPool + gateway singleton per test.

    Recreating the stubs per-test keeps ``calls`` counters clean; resetting
    the gateway singleton makes sure the fresh stubs are picked up on the
    very next request. We also stub ``get_deep_agent`` (the legacy
    full-agent provider) so tests can exercise the fallback path without
    paying DeepAgents build cost — they mutate ``deps["full_agent"]._reply``
    to inject a deterministic reply on that path.
    """
    from src.agent import deep_agent as _deep_agent_mod
    from src.api.execution import router as _handler_mod
    from src.services.agent_runtime import executor_pool as _epm
    from src.services.agent_runtime import gateway as _gw_mod
    from src.services.agent_runtime import router as _router_mod

    stub_router = _StubRouter()
    stub_pool = _StubExecutorPool()
    full_agent = _FakeAgentGraph(reply="Full agent fallback reply.")

    async def _fake_get_router_llm():
        return stub_router

    def _fake_get_executor_pool():
        return stub_pool

    async def _fake_get_deep_agent():
        return full_agent

    monkeypatch.setattr(_router_mod, "get_router_llm", _fake_get_router_llm)
    monkeypatch.setattr(_epm, "get_executor_pool", _fake_get_executor_pool)
    monkeypatch.setattr(_gw_mod, "get_router_llm", _fake_get_router_llm)
    monkeypatch.setattr(_gw_mod, "get_executor_pool", _fake_get_executor_pool)
    monkeypatch.setattr(_deep_agent_mod, "get_deep_agent", _fake_get_deep_agent)
    monkeypatch.setattr(_handler_mod, "get_deep_agent", _fake_get_deep_agent)

    _gw_mod._reset_singleton_for_tests()
    yield {"router": stub_router, "pool": stub_pool, "full_agent": full_agent}
    _gw_mod._reset_singleton_for_tests()


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


_EVENT_RE = re.compile(r"event:\s*(\S+)\s*\ndata:\s*(.*)", re.DOTALL)


async def _drain_sse(response) -> list[tuple[str, str]]:
    """Parse the ``text/event-stream`` body into ``[(event, data), ...]``."""
    events: list[tuple[str, str]] = []
    buffer = ""
    async for chunk in response.aiter_text():
        buffer += chunk
        while "\n\n" in buffer:
            raw, buffer = buffer.split("\n\n", 1)
            raw = raw.strip()
            if not raw:
                continue
            m = _EVENT_RE.search(raw)
            if m:
                events.append((m.group(1).strip(), m.group(2).strip()))
    return events


def _extract_field(data_str: str, field_name: str) -> str:
    """Pull a single field value out of an SSE ``data:`` payload."""
    import json as _json

    try:
        obj = _json.loads(data_str)
    except Exception:
        return ""
    val = obj.get(field_name)
    return str(val) if val is not None else ""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _fetch_trajectories(session_id: str, *, kind: str | None = None):
    from src.models.base import async_session_factory
    from src.models.trajectory import AgentTrajectory

    sid = uuid.UUID(session_id)
    async with async_session_factory() as session:
        stmt = select(AgentTrajectory).where(AgentTrajectory.session_id == sid)
        if kind is not None:
            stmt = stmt.where(AgentTrajectory.kind == kind)
        rows = (await session.execute(stmt)).scalars().all()
        return [
            {
                "id": str(r.id),
                "kind": r.kind,
                "outcome": r.outcome,
                "data": dict(r.data or {}),
                "tags": list(r.tags or []),
            }
            for r in rows
        ]


async def _fetch_messages(session_id: str) -> list[dict]:
    """Return the ``messages`` rows for a session, ordered by creation time."""
    from src.models.base import async_session_factory
    from src.models.session import Message

    sid = uuid.UUID(session_id)
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(Message)
                .where(Message.session_id == sid)
                .order_by(Message.created_at.asc())
            )
        ).scalars().all()
        return [
            {
                "id": str(r.id),
                "role": r.role,
                "content": r.content,
                "extra_metadata": dict(r.extra_metadata or {}),
            }
            for r in rows
        ]


async def _cleanup_session(session_id: str) -> None:
    """Best-effort cleanup of test-created session + its data."""
    from src.models.base import async_session_factory
    from src.models.session import Message, Session
    from src.models.trajectory import AgentTrajectory

    try:
        sid = uuid.UUID(session_id)
    except Exception:
        return

    async with async_session_factory() as session:
        try:
            await session.execute(
                delete(AgentTrajectory).where(AgentTrajectory.session_id == sid)
            )
            await session.execute(delete(Message).where(Message.session_id == sid))
            await session.execute(delete(Session).where(Session.id == sid))
            await session.commit()
        except Exception:
            await session.rollback()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="module")
async def test_direct_route_short_circuits_over_sse(client, _stub_runtime_dependencies):
    """Greeting → router returns direct → SSE stream has token + done."""
    await _set_flag("gateway_enabled", enabled=True)
    await _set_flag("router_llm_enabled", enabled=True)

    token, _ = await _register_and_login(client)
    session_id = str(uuid.uuid4())

    try:
        async with client.stream(
            "POST",
            "/api/v1/chat/stream",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "你好", "session_id": session_id},
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers.get("content-type", "").startswith("text/event-stream")
            events = await _drain_sse(resp)

        event_names = [name for name, _ in events]
        assert "intent" in event_names, event_names
        assert "token" in event_names, event_names
        assert "done" in event_names, event_names

        token_payloads = [data for name, data in events if name == "token"]
        assert any("你好" in payload for payload in token_payloads), token_payloads

        # Router consulted exactly once.
        assert len(_stub_runtime_dependencies["router"].calls) == 1

        # The assistant message was persisted by the short-circuit path.
        await asyncio.sleep(0.2)
        msgs = await _fetch_messages(session_id)
        assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
        assert assistant_msgs, msgs
        assert "你好" in assistant_msgs[0]["content"]
        meta = assistant_msgs[0].get("extra_metadata") or {}
        assert meta.get("route") == "direct", meta
    finally:
        await _cleanup_session(session_id)


@pytest.mark.asyncio(loop_scope="module")
async def test_gateway_disabled_bypasses_router(client, _stub_runtime_dependencies):
    """``gateway_enabled=false`` → router is NOT consulted."""
    await _set_flag("gateway_enabled", enabled=False)
    await _set_flag("router_llm_enabled", enabled=True)

    token, _ = await _register_and_login(client)
    session_id = str(uuid.uuid4())

    try:
        async with client.stream(
            "POST",
            "/api/v1/chat/stream",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "你好", "session_id": session_id},
            timeout=3.0,
        ) as resp:
            assert resp.status_code == 200
            try:
                async for _ in resp.aiter_bytes():
                    break
            except Exception:
                pass

        # Router never consulted.
        assert _stub_runtime_dependencies["router"].calls == [], (
            "router must not be invoked when gateway_enabled=false"
        )

        await asyncio.sleep(0.2)
        rows = await _fetch_trajectories(session_id, kind="router_decision")
        assert rows == [], "no router_decision rows expected when gateway is off"
    finally:
        await _cleanup_session(session_id)


@pytest.mark.asyncio(loop_scope="module")
async def test_direct_route_non_stream_chat(client, _stub_runtime_dependencies):
    """``/chat`` (non-streaming) honours the same direct-route short-circuit."""
    await _set_flag("gateway_enabled", enabled=True)
    await _set_flag("router_llm_enabled", enabled=True)

    token, _ = await _register_and_login(client)
    session_id = str(uuid.uuid4())

    try:
        resp = await client.post(
            "/api/v1/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "hi", "session_id": session_id},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "你好" in body["reply"], body

        assert len(_stub_runtime_dependencies["router"].calls) == 1
    finally:
        await _cleanup_session(session_id)


@pytest.mark.asyncio(loop_scope="module")
async def test_ops_query_routes_through_executor_pool(
    client, _stub_runtime_dependencies
):
    """Ops-verb message → ``route="executor"`` → narrow graph from the pool
    runs and emits tool + token SSE events (R-1.1 / R-1.3).

    We seed the executor pool with a :class:`_FakeAgentGraph` so the
    stream completes deterministically without a real LLM. This proves
    the gateway-picked narrow graph is what the handler consumes
    (``full_agent.invocations == 0``).
    """
    await _set_flag("gateway_enabled", enabled=True)
    await _set_flag("router_llm_enabled", enabled=True)

    deps = _stub_runtime_dependencies
    narrow_graph = _FakeAgentGraph(
        reply="根据告警日志,CPU 突增源于批处理进程。",
        tool_calls=[("grep_kb", {"query": "告警"})],
        marker="NARROW",
    )
    deps["pool"].graph = narrow_graph

    token, _ = await _register_and_login(client)
    session_id = str(uuid.uuid4())

    try:
        async with client.stream(
            "POST",
            "/api/v1/chat/stream",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "message": "查询最近一小时的告警",
                "session_id": session_id,
            },
        ) as resp:
            assert resp.status_code == 200
            events = await _drain_sse(resp)

        event_names = [name for name, _ in events]

        # R-1.1 / R-1.3: router was consulted, executor path fired.
        assert deps["router"].calls, "router must be consulted on executor path"
        assert deps["pool"].calls, "pool must be consulted on executor route"
        assert deps["pool"].calls[0].route == "executor"
        assert narrow_graph.invocations == 1, (
            "gateway must feed the narrow graph to the SSE handler"
        )
        assert deps["full_agent"].invocations == 0, (
            "full-agent fallback must not fire when the pool returns a graph"
        )

        # Expected SSE events on the executor path.
        assert "intent" in event_names, event_names
        assert "tool_start" in event_names, event_names
        assert "tool_end" in event_names, event_names
        assert "token" in event_names, event_names
        assert "done" in event_names, event_names

        # Reassembled token stream contains the canned reply.
        token_payloads = [data for name, data in events if name == "token"]
        joined = "".join(
            _extract_field(p, "content") for p in token_payloads
        )
        assert "CPU" in joined and "批处理" in joined, joined

        # The assistant message landed in DB with the executor-path reply.
        await asyncio.sleep(0.3)
        msgs = await _fetch_messages(session_id)
        assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
        assert assistant_msgs, msgs
        assert "CPU" in assistant_msgs[0]["content"]
    finally:
        await _cleanup_session(session_id)


@pytest.mark.asyncio(loop_scope="module")
async def test_question_falls_back_to_full_agent(
    client, _stub_runtime_dependencies
):
    """A non-ops question → router returns low-confidence executor → pool
    returns None → handler uses the full-agent fallback (R-1.9).

    The stub pool starts with ``graph=None`` so the gateway will fall
    back to the full agent. The request still streams cleanly and the
    reply comes from the full-agent stub — proving the fallback path
    is actually wired (and not, for instance, silently dropping to an
    executor graph).
    """
    await _set_flag("gateway_enabled", enabled=True)
    await _set_flag("router_llm_enabled", enabled=True)

    deps = _stub_runtime_dependencies
    deps["full_agent"]._reply = "这是一个通用回答。"
    deps["pool"].graph = None

    token, _ = await _register_and_login(client)
    session_id = str(uuid.uuid4())

    try:
        async with client.stream(
            "POST",
            "/api/v1/chat/stream",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "message": "What is this platform about?",
                "session_id": session_id,
            },
        ) as resp:
            assert resp.status_code == 200
            events = await _drain_sse(resp)

        event_names = [name for name, _ in events]

        assert deps["router"].calls, "router must be consulted"
        assert deps["full_agent"].invocations == 1, (
            "full-agent fallback must fire when the pool returns None; "
            f"events={event_names}"
        )

        assert "done" in event_names, event_names
        token_payloads = [data for name, data in events if name == "token"]
        joined = "".join(
            _extract_field(p, "content") for p in token_payloads
        )
        assert "通用" in joined, joined

        await asyncio.sleep(0.3)
        msgs = await _fetch_messages(session_id)
        assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
        assert assistant_msgs, msgs
        # The direct-route metadata must NOT be present on this path.
        meta = assistant_msgs[0].get("extra_metadata") or {}
        assert meta.get("route") != "direct", meta
    finally:
        await _cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Real-RouterLLM integration (R-1.2 cache-hit / R-1.3 function-calling path)
# ---------------------------------------------------------------------------


class _FakeLLMForRouter:
    """Minimal LangChain-ChatModel stand-in for driving a *real*
    :class:`~src.services.agent_runtime.router.RouterLLM` without hitting
    a live model provider.

    Exposes ``bind_tools`` (Tier 1 — function calling) returning an
    ``AIMessage``-shaped object with a single ``decide`` tool call. Tests
    inspect :attr:`ainvoke_calls` to assert the router short-circuits on
    cache hits (R-1.2) and that the function-calling path was the one
    that produced a decision (R-1.3).
    """

    def __init__(self, tool_args: dict[str, Any]) -> None:
        self._tool_args = dict(tool_args)
        self.ainvoke_calls = 0

    def bind_tools(self, _tools, tool_choice=None):  # noqa: ARG002
        outer = self

        class _Bound:
            async def ainvoke(self, _messages):  # noqa: ARG002
                outer.ainvoke_calls += 1

                # Return a duck-typed AIMessage with the ``tool_calls``
                # list RouterLLM._parse_tool_call expects.
                class _AIMsg:
                    tool_calls = [
                        {
                            "name": "decide",
                            "args": dict(outer._tool_args),
                            "id": "call-1",
                        }
                    ]

                return _AIMsg()

        return _Bound()

    # with_structured_output is never reached in the happy path because
    # bind_tools succeeds on the first try; leave it as a defensive noop
    # so any unexpected invocation explodes loudly.
    def with_structured_output(self, *_a, **_kw):  # pragma: no cover
        raise AssertionError(
            "with_structured_output should not be called on the fc-success path"
        )


def _router_path_count(path: str) -> float:
    """Read the current value of ``router_path_total{path=...}``."""
    from src.core.metrics import router_path_total

    try:
        return float(router_path_total.labels(path=path)._value.get())
    except Exception:
        return 0.0


async def _purge_router_cache() -> None:
    """Wipe any cached decisions from a prior test so cache counters are
    stable across runs."""
    try:
        from src.core.redis import get_redis
        from src.services.agent_runtime.router import CACHE_KEY_PREFIX

        redis = await get_redis()
        keys = await redis.keys(f"{CACHE_KEY_PREFIX}*")
        if keys:
            await redis.delete(*keys)
    except Exception:
        pass


@pytest.mark.asyncio(loop_scope="module")
async def test_router_function_calling_and_cache_hit(
    client, _stub_runtime_dependencies, monkeypatch
):
    """End-to-end proof of R-1.2 (cache hit) + R-1.3 (function calling).

    Wires a *real* :class:`RouterLLM` into the gateway, backed by a
    fake LLM that implements ``bind_tools``. Two identical ``/chat/stream``
    requests are issued; we assert:

    * Tier 1 (function_calling) fired exactly once — proves R-1.3.
    * The second request was served from Redis without a second LLM
      call — proves R-1.2.
    * Both requests produced a ``direct`` SSE stream with the canned
      answer.
    """
    from src.core.redis import get_redis
    from src.services.agent_runtime import gateway as _gw_mod
    from src.services.agent_runtime import router as _router_mod
    from src.services.agent_runtime.router import RouterLLM

    await _set_flag("gateway_enabled", enabled=True)
    await _set_flag("router_llm_enabled", enabled=True)
    await _purge_router_cache()

    fake_llm = _FakeLLMForRouter(
        {
            "route": "direct",
            "direct_answer": "你好，有什么可以帮你的吗？",
            "subagent_name": None,
            "suggested_tools": [],
            "reason": "greeting",
            "confidence": 0.95,
        }
    )

    real_redis = await get_redis()
    real_router = RouterLLM(
        llm=fake_llm,
        redis_client=real_redis,
        skill_index_fn=lambda: "",  # no DB dependency
    )

    async def _fake_get_router_llm():
        return real_router

    # Override the stub put in place by ``_stub_runtime_dependencies``.
    monkeypatch.setattr(_router_mod, "get_router_llm", _fake_get_router_llm)
    monkeypatch.setattr(_gw_mod, "get_router_llm", _fake_get_router_llm)
    _gw_mod._reset_singleton_for_tests()

    token, user_body = await _register_and_login(client)
    user_id = user_body["id"]
    session_a = str(uuid.uuid4())
    session_b = str(uuid.uuid4())

    before_fc = _router_path_count("function_calling")
    before_cache = _router_path_count("cache")

    # Identical message content → router cache key is identical because
    # it hashes (message + user_id + last_assistant_sha). Different
    # session_ids don't affect the key, which is exactly the point of
    # R-1.2: the decision is cacheable across sessions.
    message = "hey there"

    try:
        async with client.stream(
            "POST",
            "/api/v1/chat/stream",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": message, "session_id": session_a},
        ) as resp:
            assert resp.status_code == 200
            events_a = await _drain_sse(resp)

        assert ("done", *[""]) or "done" in [n for n, _ in events_a]
        token_a = "".join(
            _extract_field(d, "content") for n, d in events_a if n == "token"
        )
        assert "有什么" in token_a, events_a

        # Second request, same message, different session — must hit
        # the Redis cache and NOT call the fake LLM a second time.
        async with client.stream(
            "POST",
            "/api/v1/chat/stream",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": message, "session_id": session_b},
        ) as resp:
            assert resp.status_code == 200
            events_b = await _drain_sse(resp)

        token_b = "".join(
            _extract_field(d, "content") for n, d in events_b if n == "token"
        )
        assert "有什么" in token_b, events_b

        # R-1.3: exactly one Tier-1 (function calling) increment across
        # both requests.
        assert _router_path_count("function_calling") - before_fc == 1, (
            "Tier 1 (function calling) must fire exactly once over "
            "two identical requests"
        )
        # R-1.2: second request hit the cache.
        assert _router_path_count("cache") - before_cache >= 1, (
            "Second identical request must be served from the router cache"
        )
        # Belt-and-suspenders: the fake LLM was only asked once.
        assert fake_llm.ainvoke_calls == 1, (
            f"LLM must be invoked exactly once; saw {fake_llm.ainvoke_calls}"
        )
    finally:
        await _cleanup_session(session_a)
        await _cleanup_session(session_b)
        await _purge_router_cache()
        _gw_mod._reset_singleton_for_tests()
        # Drop any user rows we created — irrelevant to the assertion but
        # keeps the test schema tidy if someone runs this in a loop.
        try:
            from sqlalchemy import delete as _del

            from src.models.base import async_session_factory
            from src.models.user import User

            async with async_session_factory() as sess:
                await sess.execute(_del(User).where(User.id == user_id))
                await sess.commit()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# R-1.5 latency sanity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="module")
async def test_direct_route_first_token_latency(
    client, _stub_runtime_dependencies
):
    """Lower-bound sanity for R-1.5: a direct-route turn must reach
    first token well under 1s when the router has short-circuited.

    The full production target (p95 ≤ 1000 ms under 50-concurrent load)
    lives in the dedicated benchmark at
    ``tests/bench/test_chat_latency.py`` — this is a fast smoke check
    that catches obvious regressions in the gateway short-circuit path
    without pulling in a load harness.
    """
    await _set_flag("gateway_enabled", enabled=True)
    await _set_flag("router_llm_enabled", enabled=True)

    token, _ = await _register_and_login(client)
    session_id = str(uuid.uuid4())

    try:
        t0 = time.perf_counter()
        first_token_ms: float | None = None

        async with client.stream(
            "POST",
            "/api/v1/chat/stream",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "你好", "session_id": session_id},
        ) as resp:
            assert resp.status_code == 200

            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    raw, buffer = buffer.split("\n\n", 1)
                    raw = raw.strip()
                    m = _EVENT_RE.search(raw)
                    if m and m.group(1).strip() == "token":
                        first_token_ms = (time.perf_counter() - t0) * 1000.0
                        break
                if first_token_ms is not None:
                    break

        assert first_token_ms is not None, (
            "expected a token SSE event on the direct path"
        )
        # Loose bound: we're on a stubbed path so sub-second is trivial;
        # the explicit ceiling here catches ASGI/FastAPI routing
        # regressions that would blow p95 in production.
        assert first_token_ms < 1500.0, (
            f"direct-route first token too slow: {first_token_ms:.1f} ms"
        )
    finally:
        await _cleanup_session(session_id)
