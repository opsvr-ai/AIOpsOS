import asyncio
import json
import logging
import os as _os
import time
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agent.context import set_current_space, set_current_user
from src.agent.deep_agent import get_deep_agent, set_session_model
from src.core.model_factory import _build_model_from_provider
from src.agent.human_interrupt import parse_interrupt_marker, INTERRUPT_MARKER
from src.services.interrupt_manager import interrupt_manager
from src.api.deps import get_current_user, get_db, get_optional_space_id
from src.models.alert import Alert
from src.models.base import async_session_factory
from src.models.session import Message, Session
from src.models.space import Space, SpaceMember
from src.schemas.alert import AlertActionRequest, AlertCreate, AlertListParams, AlertOut, BatchActionRequest
from src.schemas.chat import ChatRequest, ChatResponse, ChatEvent, MessageOut, SessionDetailOut, SessionOut
from src.services.memory_provider import MemoryManager
from src.services.sleep_detector import sleep_detector
from src.services.tool_manager import tool_manager

logger = logging.getLogger(__name__)

# TTL cache for tool_manager.reload() — avoid DB hit on every request
_last_tool_reload: float = 0.0
_TOOL_RELOAD_TTL: float = 60.0

# Keyword-based intent classifier — sub-microsecond, no LLM call needed
_INTENT_PATTERNS: list[tuple[str, list[str]]] = [
    ("知识库查询", ["知识库", "wiki", "文档", "查", "搜索", "找", "资料", "知识"]),
    ("执行运维命令", ["执行", "运行", "命令", "部署", "重启", "启动", "停止", "bash", "shell"]),
    ("故障排查", ["故障", "报错", "错误", "bug", "异常", "问题", "排查", "修复", "失败"]),
    ("数据分析", ["分析", "统计", "趋势", "报表", "图表", "数据", "指标"]),
    ("监控告警", ["监控", "告警", "预警", "报警", "alert", "状态", "健康", "检查"]),
    ("文件操作", ["文件", "读写", "创建", "删除", "编辑", "保存", "目录"]),
    ("系统配置", ["配置", "设置", "参数", "环境", "变量", "config"]),
    ("定时任务", ["定时", "cron", "周期", "调度", "计划", "例行"]),
    ("记忆检索", ["记忆", "history", "之前", "上次", "上回", "记录", "过往"]),
    ("消息发送", ["发送", "通知", "推送", "消息", "提醒"]),
]


def _classify_intent_fast(text: str) -> str:
    """Classify user intent by keyword matching. Returns in microseconds."""
    lower = text.lower()
    for intent, keywords in _INTENT_PATTERNS:
        for kw in keywords:
            if kw in lower:
                return intent
    return "通用对话"


async def _reload_tools_if_stale() -> None:
    """Reload tools only if TTL has expired."""
    global _last_tool_reload
    now = time.monotonic()
    if now - _last_tool_reload > _TOOL_RELOAD_TTL:
        await tool_manager.reload()
        _last_tool_reload = now


async def _increment_turn(session_id: str) -> None:
    """Increment session turn_count and set skill_review_due every N turns."""
    from src.services.sleep_detector import REVIEW_INTERVAL_TURNS

    async with async_session_factory() as db:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()
        if session is None:
            return
        session.turn_count = (session.turn_count or 0) + 1
        if session.turn_count >= REVIEW_INTERVAL_TURNS:
            session.skill_review_due = True
            session.turn_count = 0
        await db.commit()


def _get_user_context() -> SystemMessage | None:
    """Build a SystemMessage with current user profile for personalization."""
    from src.agent.deep_agent import build_user_context_message

    msg = build_user_context_message()
    if msg:
        return SystemMessage(content=msg)
    return None


async def _resolve_space_context(user_id: str, space_id: str | None) -> None:
    """Resolve space name and member role from space_id, then set context."""
    if not space_id:
        return
    async with async_session_factory() as db:
        result = await db.execute(
            select(Space.name, SpaceMember.role)
            .join(SpaceMember, SpaceMember.space_id == Space.id)
            .where(Space.id == space_id, SpaceMember.user_id == user_id)
        )
        row = result.one_or_none()
        if row:
            set_current_space(space_id=str(space_id), space_name=row[0], space_role=row[1])


router = APIRouter(prefix="/api/v1")


async def _generate_title(user_msg: str, reply: str) -> str:
    """Use LLM to generate a concise session title."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from src.core.model_factory import get_default_model
    llm = await get_default_model()
    resp = await llm.ainvoke([
        SystemMessage(content="Generate a short title (max 40 chars, in Chinese) for this conversation. Reply with ONLY the title."),
        HumanMessage(content=f"User: {user_msg[:200]}\nAssistant: {reply[:500]}"),
    ])
    title = resp.content.strip().strip('"').strip("'").strip()
    if len(title) > 60:
        title = title[:57] + "..."
    return title or _make_title(user_msg)


def _make_title(text: str, max_chars: int = 60) -> str:
    """Clean and truncate a session title from message text."""
    import re as _re

    t = text.strip()
    # strip common markdown
    t = _re.sub(r"[#*_~`>|-]{1,3}", "", t)
    t = _re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)  # links -> text
    t = t.replace("\n", " ").replace("\r", "")
    t = _re.sub(r"\s+", " ", t).strip()

    if len(t) <= max_chars:
        return t
    cut = t.rfind(" ", 0, max_chars)
    if cut > max_chars // 2:
        return t[:cut] + "…"
    return t[: max_chars - 1] + "…"


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from datetime import UTC, datetime

    space_id = body.space_id

    if body.session_id:
        result = await db.execute(select(Session).where(Session.id == body.session_id))
        session = result.scalar_one_or_none()
        if session is None:
            session = Session(id=body.session_id, user_id=user.id,
                              title=_make_title(body.message),
                              space_id=space_id,
                              sleep_status="awake", auto_consolidate=True,
                              memory_status="unconsolidated",
                              last_active_at=datetime.now(UTC))
            db.add(session)
            await db.commit()
            await db.refresh(session)
        else:
            was_sleeping = session.sleep_status == "sleeping"
            session.sleep_status = "awake"
            session.last_active_at = datetime.now(UTC)
            if was_sleeping:
                session.memory_status = "unconsolidated"
            if space_id and not session.space_id:
                session.space_id = space_id
            await db.commit()
    else:
        session = Session(user_id=user.id, title=_make_title(body.message),
                          space_id=space_id,
                          sleep_status="awake", auto_consolidate=True,
                          memory_status="unconsolidated",
                          last_active_at=datetime.now(UTC))
        db.add(session)
        await db.commit()
        await db.refresh(session)

    user_msg = Message(session_id=session.id, role="user", content=body.message)
    db.add(user_msg)
    await db.commit()

    await _reload_tools_if_stale()

    # Propagate user context for memory tools
    set_current_user(
        user_id=str(user.id),
        session_id=str(session.id),
        username=user.username,
        email=user.email,
        roles=[r.name for r in (user.roles or [])],
    )

    await _resolve_space_context(str(user.id), space_id)

    model_provider_id = body.model_provider_id or (session.model_provider_id if session else None)
    if model_provider_id:
        from src.models.model_provider import ModelProvider
        result = await db.execute(select(ModelProvider).where(ModelProvider.id == model_provider_id))
        provider = result.scalar_one_or_none()
        if provider:
            session.model_provider_id = provider.id
            await db.commit()
            set_session_model(_build_model_from_provider(provider))

    agent = await get_deep_agent()
    messages: list = []
    user_ctx = _get_user_context()
    if user_ctx:
        messages.append(user_ctx)
    resolved_message = await _resolve_file_refs(body.message, str(session.id))
    messages.append(HumanMessage(content=resolved_message))
    result = await agent.ainvoke({"messages": messages})
    reply = result["messages"][-1].content if result.get("messages") else "Agent produced no output."

    assistant_msg = Message(session_id=session.id, role="assistant", content=reply,
                            extra_metadata={"execution_steps": []})
    db.add(assistant_msg)
    await db.commit()

    # Sync per-turn memories (LLM-based, dual personal+team scope)
    try:
        mm = MemoryManager()
        mm.initialize(str(session.id), user_id=str(user.id))
        await mm.sync_turn(body.message, reply)
    except Exception:
        logger.exception("sync_turn failed in /chat")

    # improve session title via LLM summarization if it is still short (background)
    if len(session.title or "") < 10:
        asyncio.create_task(_update_session_title(str(session.id), body.message, reply))

    # Increment turn counter and flag for periodic skill review
    await _increment_turn(str(session.id))

    return ChatResponse(session_id=str(session.id), reply=reply)


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    space_id: str | None = Depends(get_optional_space_id),
):
    query = select(Session).where(Session.user_id == user.id)
    if space_id:
        query = query.where(Session.space_id == space_id)
    result = await db.execute(query.order_by(Session.updated_at.desc()))
    return result.scalars().all()


# ── Dynamic recommendations ────────────────────────────────────────────────────


@router.get("/sessions/recommendations")
async def get_recommendations(
    user=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
):
    from langchain_core.messages import SystemMessage, HumanMessage

    suggestions = []

    try:
        async with async_session_factory() as db:
            # Recent user sessions
            if space_id:
                result = await db.execute(
                    select(Session.title)
                    .where(Session.space_id == space_id, Session.title.isnot(None))
                    .order_by(Session.last_active_at.desc())
                    .limit(5)
                )
                titles = [row[0] for row in result.all() if row[0]]
                if titles:
                    suggestions.append(f"空间最近对话: {'; '.join(titles)}")

            # Knowledge base hot items
            try:
                from src.models.knowledge import KnowledgeDocument
                result = await db.execute(
                    select(KnowledgeDocument.title)
                    .order_by(KnowledgeDocument.updated_at.desc())
                    .limit(5)
                )
                docs = [row[0] for row in result.all() if row[0]]
                if docs:
                    suggestions.append(f"知识库最新文档: {'; '.join(docs)}")
            except Exception:
                pass
    except Exception:
        logger.exception("Failed to gather recommendation context")

    if not suggestions:
        return [
            {"label": "检查系统状态", "prompt": "查看系统当前运行状态"},
            {"label": "查看告警信息", "prompt": "查看最近告警列表"},
            {"label": "执行自动化任务", "prompt": "帮我执行一个自动化运维任务"},
            {"label": "搜索运维知识", "prompt": "搜索运维相关知识库"},
        ]

    # Use LLM to generate personalized recommendations
    try:
        from src.core.model_factory import get_default_model

        llm = await get_default_model()
        prompt = f"""Based on the following context about a user in an IT operations platform, generate 4 short, actionable Chinese-language suggestion labels (max 8 chars each) and prompts (max 30 chars each):

Context:
{chr(10).join(suggestions)}

Return ONLY a JSON array: [{{"label": "...", "prompt": "..."}}, ...]. No other text."""

        resp = await llm.ainvoke([
            SystemMessage(content="You are a helpful assistant. Output only valid JSON."),
            HumanMessage(content=prompt),
        ])

        import json as _json

        text = resp.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("\n```", 1)[0]
        parsed = _json.loads(text)
        if isinstance(parsed, list) and len(parsed) > 0:
            return parsed
    except Exception:
        pass

    return [
        {"label": "检查系统状态", "prompt": "查看系统当前运行状态"},
        {"label": "查看告警信息", "prompt": "查看最近告警列表"},
        {"label": "执行自动化任务", "prompt": "帮我执行一个自动化运维任务"},
        {"label": "搜索运维知识", "prompt": "搜索运维相关知识库"},
    ]


@router.get("/sessions/{session_id}", response_model=SessionDetailOut)
async def get_session(
    session_id: str, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    msg_result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at)
    )
    msgs = msg_result.scalars().all()
    return SessionDetailOut(
        id=str(session.id),
        user_id=str(session.user_id),
        agent_id=str(session.agent_id) if session.agent_id else None,
        title=session.title,
        status=session.status,
        created_at=session.created_at,
        updated_at=session.updated_at,
        messages=[MessageOut(
            id=str(m.id),
            session_id=str(m.session_id),
            role=m.role,
            content=m.content,
            message_type=m.message_type,
            extra_metadata=m.extra_metadata,
            created_at=m.created_at,
        ) for m in msgs],
    )


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if str(session.user_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Not your session")

    # Trigger end-of-session memory extraction before deleting
    try:
        msgs_result = await db.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at.asc())
        )
        session_msgs = [
            {"role": m.role, "content": m.content}
            for m in msgs_result.scalars().all()
        ]
        mm = MemoryManager()
        mm.initialize(session_id, user_id=str(user.id))
        await mm.on_session_end(session_msgs)
    except Exception:
        logger.exception("on_session_end failed for session %s", session_id)

    await db.execute(sa_delete(Message).where(Message.session_id == session_id))
    await db.execute(sa_delete(Session).where(Session.id == session_id))
    await db.commit()
    return {"detail": "deleted"}


# ── Alerts ────────────────────────────────────────────

@router.get("/alerts", response_model=list[AlertOut])
async def list_alerts(
    params: AlertListParams = Depends(),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Alert)
    if params.status:
        query = query.where(Alert.status == params.status)
    if params.severity:
        query = query.where(Alert.severity == params.severity)
    if params.source:
        query = query.where(Alert.source == params.source)
    if params.search:
        query = query.where(Alert.title.ilike(f"%{params.search}%"))
    if params.space_id:
        query = query.where(Alert.space_id == params.space_id)
    order_col = getattr(Alert, params.sort_by, Alert.created_at)
    query = query.order_by(order_col.desc() if params.sort_order == "desc" else order_col.asc())
    query = query.offset((params.page - 1) * params.page_size).limit(params.page_size)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/alerts", response_model=AlertOut)
async def create_alert(
    body: AlertCreate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ingest a new alert. Auto-analysis is triggered asynchronously."""
    from datetime import UTC, datetime

    alert = Alert(
        event_id=body.event_id,
        title=body.title,
        source=body.source,
        severity=body.severity,
        raw_event=body.raw_event,
        status="pending",
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)

    # Fire-and-forget: match triggers and run auto-analysis
    try:
        import asyncio
        asyncio.create_task(_auto_analyze_alert(str(alert.id)))
    except Exception:
        logger.exception("Failed to spawn auto-analysis for alert %s", alert.id)

    return alert


@router.get("/alerts/{alert_id}", response_model=AlertOut)
async def get_alert(alert_id: str, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@router.post("/alerts/{alert_id}/action")
async def alert_action(
    alert_id: str, body: AlertActionRequest, user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from src.services.alert_state import validate_action, ACTION_TO_STATUS

    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    if not validate_action(alert.status, body.action):
        raise HTTPException(
            status_code=400,
            detail=f"Action '{body.action}' not allowed in status '{alert.status}'",
        )

    if body.action == "analyze":
        alert.status = "analyzing"
        await db.commit()
        await db.refresh(alert)
        import asyncio
        asyncio.create_task(_auto_analyze_alert(str(alert.id)))
        return {"detail": "ok", "status": alert.status}

    if body.action == "confirm":
        alert.status = "confirmed"
        alert.confirmed_by = user.username
    elif body.action == "close":
        alert.status = "closed"
    else:
        alert.status = ACTION_TO_STATUS.get(body.action, body.action)

    await db.commit()
    return {"detail": "ok", "status": alert.status}


@router.post("/alerts/batch-action")
async def batch_alert_action(
    body: BatchActionRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from src.services.alert_state import validate_action

    updated = 0
    failed: list[str] = []

    for alert_id in body.alert_ids:
        result = await db.execute(select(Alert).where(Alert.id == alert_id))
        alert = result.scalar_one_or_none()
        if alert is None:
            failed.append(alert_id)
            continue
        if not validate_action(alert.status, body.action):
            failed.append(alert_id)
            continue

        if body.action == "confirm":
            alert.status = "confirmed"
            alert.confirmed_by = user.username
        elif body.action == "dismiss":
            alert.status = "dismissed"
        else:
            alert.status = body.action

        updated += 1

    await db.commit()
    return {"detail": "ok", "updated": updated, "failed": failed}


async def _auto_analyze_alert(alert_id: str) -> None:
    """Run trigger matching and agent-based auto-analysis for an alert."""
    from src.models.base import async_session_factory
    from src.services.trigger_engine import match_triggers
    from src.services.alert_analyzer import analyze

    async with async_session_factory() as _db:
        result = await _db.execute(select(Alert).where(Alert.id == alert_id))
        alert = result.scalar_one_or_none()
        if alert is None:
            return

        try:
            triggers = await match_triggers(_db, alert)
            if triggers:
                await analyze(alert, triggers)
                await _db.commit()
        except Exception:
            logger.exception("Auto-analysis failed for alert %s", alert_id)


# ── SSE streaming chat ────────────────────────────────

def _sse_event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _classify_tool(name: str) -> str:
    """Classify a tool by its name for frontend icon/type display."""
    if name == "task":
        return "sub_agent"
    # DeepAgents built-in filesystem + shell + planning tools
    if name in ("ls", "read_file", "write_file", "edit_file", "glob", "grep",
                "execute", "write_todos"):
        return "builtin"
    # Knowledge base tools
    if name in ("get_config", "grep_kb", "read_wiki", "list_wiki",
                "write_wiki", "write_raw"):
        return "builtin"
    # MCP tools carry mcp__ prefix or namespaced name
    if name.startswith("mcp__") or "__" in name:
        return "mcp"
    # Everything else is a skill-provided tool
    return "skill"


def _safe_truncate(val, max_len: int = 500) -> str:
    s = str(val)
    if len(s) > max_len:
        return s[:max_len] + "…"
    return s


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from datetime import UTC, datetime

    space_id = body.space_id

    if body.session_id:
        result = await db.execute(select(Session).where(Session.id == body.session_id))
        session = result.scalar_one_or_none()
        if session is None:
            session = Session(id=body.session_id, user_id=user.id,
                              title=_make_title(body.message),
                              space_id=space_id,
                              sleep_status="awake", auto_consolidate=True,
                              memory_status="unconsolidated",
                              last_active_at=datetime.now(UTC))
            db.add(session)
            await db.commit()
            await db.refresh(session)
        else:
            was_sleeping = session.sleep_status == "sleeping"
            session.sleep_status = "awake"
            session.last_active_at = datetime.now(UTC)
            if was_sleeping:
                session.memory_status = "unconsolidated"
            if space_id and not session.space_id:
                session.space_id = space_id
            await db.commit()
    else:
        session = Session(user_id=user.id, title=_make_title(body.message),
                          space_id=space_id,
                          sleep_status="awake", auto_consolidate=True,
                          memory_status="unconsolidated",
                          last_active_at=datetime.now(UTC))
        db.add(session)
        await db.commit()
        await db.refresh(session)

    user_msg = Message(session_id=session.id, role="user", content=body.message)
    db.add(user_msg)
    await db.commit()

    # Propagate user context for memory tools
    set_current_user(
        user_id=str(user.id),
        session_id=str(session.id),
        username=user.username,
        email=user.email,
        roles=[r.name for r in (user.roles or [])],
    )

    await _resolve_space_context(str(user.id), space_id)

    # Apply per-session model override if specified
    model_provider_id = body.model_provider_id or (session.model_provider_id if session else None)
    if model_provider_id:
        from src.models.model_provider import ModelProvider
        result = await db.execute(select(ModelProvider).where(ModelProvider.id == model_provider_id))
        provider = result.scalar_one_or_none()
        if provider:
            session.model_provider_id = provider.id
            await db.commit()
            set_session_model(_build_model_from_provider(provider))

    # Initialize memory providers for this session
    mm = MemoryManager()
    mm.initialize(str(session.id), user_id=str(user.id), platform="web")

    # Load session message history for conversational context
    history_messages: list = []
    if body.session_id:
        result = await db.execute(
            select(Message)
            .where(Message.session_id == session.id)
            .order_by(Message.created_at.asc())
            .limit(30)
        )
        for m in result.scalars().all():
            if m.role == "user":
                history_messages.append(HumanMessage(content=m.content))
            elif m.role == "assistant":
                history_messages.append(AIMessage(content=m.content))

    async def event_stream():
        session_id = str(session.id)
        final_answer = ""
        pending_tokens: list[str] = []
        step_counter = 0
        tool_step_map: dict[str, int] = {}  # run_id -> step number
        seen_task_ids: set[str] = set()  # dedupe sub-agent events
        collected_steps: list[dict] = []  # persisted to extra_metadata

        # ── Resume check: inject interrupt response if pending ──
        pending_interrupt = interrupt_manager.get_pending_for_session(session_id)
        if pending_interrupt:
            logger.info("Resuming session %s with interrupt %s", session_id, pending_interrupt.id)
            response_data: dict[str, Any]
            if pending_interrupt.type == "approval":
                approved = body.message.strip().lower() in ("yes", "是", "同意", "确认", "approve", "ok", "y", "true", "1")
                response_data = {"approved": approved, "message": body.message}
            else:
                response_data = {"values": body.message, "message": body.message}
            interrupt_manager.resolve(pending_interrupt.id, response_data)

        # ── Phase 1: Intent recognition (fast keyword-based, no LLM) ──
        intent_text = _classify_intent_fast(body.message)
        yield _sse_event("status", {"message": "正在理解意图...", "session_id": session_id})
        yield _sse_event("intent", {
            "intent": intent_text,
            "session_id": session_id,
        })
        yield _sse_event("status", {"message": "正在规划任务...", "session_id": session_id})

        try:
            # Parallelize: memory prefetch + tool reload + agent init
            recall_context, _, _agent = await asyncio.gather(
                mm.prefetch(body.message),
                _reload_tools_if_stale(),
                get_deep_agent(),
            )
            agent_messages = list(history_messages)
            user_ctx = _get_user_context()
            if user_ctx:
                agent_messages.insert(0, user_ctx)
            if recall_context:
                from langchain_core.messages import SystemMessage
                agent_messages.append(SystemMessage(content=recall_context))

            # Inject interrupt resolution context
            pending = interrupt_manager.get_pending_for_session(session_id)
            if pending is None:
                # Check if current message is a response to a now-resolved interrupt
                # The interrupt was resolved above; inject context so agent continues
                last_assistant = None
                for m in reversed(history_messages):
                    if isinstance(m, AIMessage):
                        last_assistant = m
                        break
                if last_assistant and hasattr(last_assistant, 'content') and '[等待人工介入]' in str(last_assistant.content):
                    from langchain_core.messages import SystemMessage
                    agent_messages.append(SystemMessage(
                        content=f"[系统通知] 用户已响应你的介入请求。用户回复: {body.message}\n请根据用户回复继续执行任务。如果用户同意，继续原计划；如果用户拒绝，停止操作并解释原因。"
                    ))

            resolved_message = await _resolve_file_refs(body.message, str(session.id))
            agent_messages.append(HumanMessage(content=resolved_message))

            async for event in _agent.astream_events(
                {"messages": agent_messages},
                version="v2",
            ):
                if await request.is_disconnected():
                    break

                etype = event.get("event", "")
                name = event.get("name", "")
                data = event.get("data", {})
                run_id = event.get("run_id", "")

                if etype == "on_chat_model_stream":
                    chunk = data.get("chunk")
                    content = (
                        getattr(chunk, "content", None)
                        if hasattr(chunk, "content")
                        else chunk.get("content", None) if isinstance(chunk, dict) else None
                    )
                    if content:
                        pending_tokens.append(content)
                        final_answer += content
                        yield _sse_event("token", {
                            "content": content,
                            "session_id": session_id,
                        })

                elif etype == "on_tool_start":
                    step_counter += 1
                    if run_id:
                        tool_step_map[run_id] = step_counter
                    tool_type = _classify_tool(name)
                    tool_input = data.get("input", {})
                    is_retrieval = name in ("get_config", "grep_kb", "read_wiki", "list_wiki",
                                            "retrieve_knowledge", "search_knowledge")

                    if is_retrieval:
                        yield _sse_event("retrieve_start", {
                            "type": "knowledge",
                            "name": name,
                            "query": _safe_truncate(tool_input, 200),
                            "session_id": session_id,
                        })

                    yield _sse_event("tool_start", {
                        "name": name,
                        "tool_type": tool_type,
                        "input": _safe_truncate(tool_input),
                        "step": step_counter,
                        "session_id": session_id,
                    })

                    # Collect for persistence
                    from datetime import UTC, datetime as _dt
                    collected_steps.append({
                        "id": f"{name}-{run_id[:8] if run_id else step_counter}",
                        "type": tool_type,
                        "name": name,
                        "input": _safe_truncate(tool_input),
                        "output": "",
                        "status": "running",
                        "timestamp": _dt.now(UTC).timestamp(),
                        "stepNumber": step_counter,
                    })

                    # Emit sub_agent_start only once per task call (deduped)
                    if name == "task" and run_id not in seen_task_ids:
                        seen_task_ids.add(run_id)
                        sa_name = ""
                        if isinstance(tool_input, dict):
                            sa_name = str(tool_input.get("subagent_type", "") or "")
                        yield _sse_event("sub_agent_start", {
                            "name": sa_name or "sub-agent",
                            "input": _safe_truncate(tool_input.get("description", "") if isinstance(tool_input, dict) else tool_input),
                            "step": step_counter,
                            "session_id": session_id,
                        })

                elif etype == "on_tool_end":
                    tool_type = _classify_tool(name)
                    tool_output = data.get("output", "")
                    is_retrieval = name in ("get_config", "grep_kb", "read_wiki", "list_wiki",
                                            "retrieve_knowledge", "search_knowledge")
                    step = tool_step_map.get(run_id, 0)

                    if is_retrieval:
                        result_count = 0
                        if isinstance(tool_output, str):
                            result_count = tool_output.count("\\n") + 1 if tool_output.strip() else 0
                        elif isinstance(tool_output, (list, dict)):
                            result_count = len(tool_output) if isinstance(tool_output, list) else 1
                        yield _sse_event("retrieve_end", {
                            "type": "knowledge",
                            "name": name,
                            "result_count": result_count,
                            "session_id": session_id,
                        })

                    yield _sse_event("tool_end", {
                        "name": name,
                        "tool_type": tool_type,
                        "output": _safe_truncate(tool_output),
                        "step": step,
                        "session_id": session_id,
                    })

                    # Emit sub_agent_end once (deduped)
                    if name == "task" and run_id in seen_task_ids:
                        yield _sse_event("sub_agent_end", {
                            "name": "",
                            "output": _safe_truncate(tool_output),
                            "step": step,
                            "session_id": session_id,
                        })

                    # Update collected step with output
                    step_id = f"{name}-{run_id[:8] if run_id else step}"
                    for s in collected_steps:
                        if s["id"] == step_id:
                            s["output"] = _safe_truncate(tool_output)
                            s["status"] = "error" if "Error" in str(tool_output) else "done"
                            break

                    # Check for human interrupt marker
                    if name in ("request_approval", "request_input"):
                        interrupt_data = parse_interrupt_marker(str(tool_output))
                        if interrupt_data:
                            yield _sse_event("interrupt", {
                                "interrupt_id": interrupt_data["interrupt_id"],
                                "type": interrupt_data["type"],
                                "data": interrupt_data["data"],
                                "session_id": session_id,
                            })
                            # Persist partial message before yielding
                            async with async_session_factory() as _db:
                                partial_msg = Message(
                                    session_id=session_id, role="assistant",
                                    content=final_answer or "[等待人工介入]",
                                    extra_metadata={
                                        "execution_steps": collected_steps,
                                        "interrupt_pending": True,
                                        "interrupt_id": interrupt_data["interrupt_id"],
                                    },
                                )
                                _db.add(partial_msg)
                                await _db.commit()
                            yield _sse_event("done", {
                                "session_id": session_id,
                                "reply": final_answer or "[等待人工介入]",
                                "interrupt_pending": True,
                            })
                            return  # end stream, wait for user response

            # Flush any remaining tokens
            if pending_tokens:
                final_answer = "".join(
                    pending_tokens[i] for i in range(len(pending_tokens))
                )
                # Already accumulated above

            if not final_answer:
                final_answer = body.message

            # Persist assistant reply with execution steps
            async with async_session_factory() as _db:
                assistant_msg = Message(
                    session_id=session_id, role="assistant", content=final_answer,
                    extra_metadata={"execution_steps": collected_steps},
                )
                _db.add(assistant_msg)
                result = await _db.execute(select(Session).where(Session.id == session_id))
                s = result.scalar_one_or_none()
                if s and (not s.title or len(s.title) < 10):
                    # Fire-and-forget title generation — don't block the done event
                    asyncio.create_task(_update_session_title(
                        str(s.id), body.message, final_answer,
                    ))
                await _db.commit()

            # Sync per-turn memories (LLM-based, dual personal+team scope)
            try:
                await mm.sync_turn(body.message, final_answer)
            except Exception:
                logger.exception("sync_turn failed in chat_stream")

            # Increment turn counter and flag for periodic skill review
            await _increment_turn(session_id)

            yield _sse_event("done", {
                "session_id": session_id,
                "reply": final_answer,
            })

        except Exception as exc:
            import traceback as _tb
            logger.error("SSE chat error: %s\n%s", exc, _tb.format_exc())
            yield _sse_event("error", {
                "message": str(exc),
                "session_id": session_id,
            })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Session file management ───────────────────────────────────────────────────

from src.models.session import SessionFile
from src.schemas.chat import SessionFileOut

_UPLOAD_ROOT = "uploads/sessions"


@router.get("/sessions/{session_id}/files", response_model=list[SessionFileOut])
async def list_session_files(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SessionFile)
        .where(SessionFile.session_id == session_id)
        .order_by(SessionFile.created_at.desc())
    )
    return result.scalars().all()


@router.post("/sessions/{session_id}/files", response_model=SessionFileOut)
async def upload_session_file(
    session_id: str,
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
):
    # Verify session exists
    result = await db.execute(select(Session).where(Session.id == session_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Session not found")

    # Save file to disk
    upload_dir = _os.path.join(_UPLOAD_ROOT, session_id)
    _os.makedirs(upload_dir, exist_ok=True)
    file_id = str(_uuid.uuid4())
    dest_path = _os.path.join(upload_dir, f"{file_id}_{file.filename}")

    contents = await file.read()
    with open(dest_path, "wb") as f:
        f.write(contents)

    session_file = SessionFile(
        id=file_id,
        session_id=session_id,
        filename=file.filename or "untitled",
        file_path=dest_path,
        file_size=len(contents),
        mime_type=file.content_type,
    )
    db.add(session_file)

    # Parse document content with markitdown
    from src.services.document_parser import is_parsable, parse_document_async, SYNC_PARSE_SIZE_LIMIT

    if is_parsable(file.content_type):
        if len(contents) <= SYNC_PARSE_SIZE_LIMIT:
            text = await parse_document_async(dest_path, file.content_type)
            if text:
                session_file.content_text = text
        else:
            # Large file: schedule background parse
            async def _bg_parse(sf_id: str, path: str, mime: str | None):
                text = await parse_document_async(path, mime)
                if text:
                    async with async_session_factory() as _db:
                        result = await _db.execute(select(SessionFile).where(SessionFile.id == sf_id))
                        sf = result.scalar_one_or_none()
                        if sf:
                            sf.content_text = text
                            await _db.commit()

            asyncio.create_task(_bg_parse(str(session_file.id), dest_path, file.content_type))

    await db.commit()
    await db.refresh(session_file)
    return session_file


@router.delete("/sessions/{session_id}/files/{file_id}")
async def delete_session_file(
    session_id: str,
    file_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SessionFile).where(
            SessionFile.id == file_id,
            SessionFile.session_id == session_id,
        )
    )
    sf = result.scalar_one_or_none()
    if not sf:
        raise HTTPException(status_code=404, detail="File not found")

    # Remove from disk
    if _os.path.exists(sf.file_path):
        _os.remove(sf.file_path)

    await db.delete(sf)
    await db.commit()
    return {"ok": True}


@router.get("/sessions/{session_id}/files/{file_id}/download")
async def download_session_file(
    session_id: str,
    file_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SessionFile).where(
            SessionFile.id == file_id,
            SessionFile.session_id == session_id,
        )
    )
    sf = result.scalar_one_or_none()
    if not sf:
        raise HTTPException(status_code=404, detail="File not found")
    if not _os.path.exists(sf.file_path):
        raise HTTPException(status_code=404, detail="File missing on disk")

    return FileResponse(sf.file_path, filename=sf.filename, media_type=sf.mime_type or "application/octet-stream")


async def _resolve_file_refs(message: str, session_id: str) -> str:
    """Replace @[filename](ref:file_id) references with file content."""
    import re as _re

    refs = _re.findall(r'@\[([^\]]+)\]\(ref:([^)]+)\)', message)
    if not refs:
        return message

    async with async_session_factory() as db:
        file_ids = [ref[1] for ref in refs]
        result = await db.execute(select(SessionFile).where(SessionFile.id.in_(file_ids)))
        files = {str(sf.id): sf for sf in result.scalars().all()}

    resolved = message
    for filename, file_id in refs:
        sf = files.get(file_id)
        if sf and sf.content_text:
            ctx = f"\n\n--- 文件: {filename} ---\n{sf.content_text[:8000]}\n--- 文件结束 ---"
            resolved = resolved.replace(f'@[{filename}](ref:{file_id})', ctx)
        else:
            resolved = resolved.replace(f'@[{filename}](ref:{file_id})', f'[已引用文件: {filename}]')

    return resolved


async def _update_session_title(session_id: str, user_msg: str, reply: str) -> None:
    """Background task: update session title via LLM without blocking the response."""
    try:
        title = await _generate_title(user_msg, reply)
        async with async_session_factory() as db:
            result = await db.execute(select(Session).where(Session.id == session_id))
            s = result.scalar_one_or_none()
            if s:
                s.title = title
                await db.commit()
    except Exception:
        logger.exception("Background title generation failed")
