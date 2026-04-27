import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from langchain_core.messages import AIMessage, HumanMessage

from src.agent.deep_agent import get_deep_agent, set_current_user
from src.config import settings
from src.api.deps import get_current_user, get_db
from src.models.alert import Alert
from src.models.base import async_session_factory
from src.models.session import Message, Session
from src.schemas.alert import AlertActionRequest, AlertListParams, AlertOut
from src.schemas.chat import ChatRequest, ChatResponse, ChatEvent, MessageOut, SessionDetailOut, SessionOut
from src.services.memory_provider import MemoryManager
from src.services.sleep_detector import sleep_detector
from src.services.tool_manager import tool_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


async def _generate_title(user_msg: str, reply: str) -> str:
    """Use LLM to generate a concise session title."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model="deepseek-v4-flash",
        temperature=0.3,
    )
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

    if body.session_id:
        result = await db.execute(select(Session).where(Session.id == body.session_id))
        session = result.scalar_one_or_none()
        if session is None:
            session = Session(id=body.session_id, user_id=user.id, title=_make_title(body.message))
            db.add(session)
            await db.commit()
            await db.refresh(session)
        else:
            if getattr(session, "sleep_status", "awake") == "sleeping":
                session.sleep_status = "awake"
            session.last_active_at = datetime.now(UTC)
            await db.commit()
    else:
        session = Session(user_id=user.id, title=_make_title(body.message))
        db.add(session)
        await db.commit()
        await db.refresh(session)

    user_msg = Message(session_id=session.id, role="user", content=body.message)
    db.add(user_msg)
    await db.commit()

    await tool_manager.reload()

    # Propagate user context for memory tools
    set_current_user(user_id=str(user.id), session_id=str(session.id))

    agent = await get_deep_agent()
    result = await agent.ainvoke({
        "messages": [HumanMessage(content=body.message)],
    })
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

    # improve session title via LLM summarization if it is still short
    if len(session.title or "") < 10:
        try:
            session.title = await _generate_title(body.message, reply)
            await db.commit()
        except Exception:
            pass

    return ChatResponse(session_id=str(session.id), reply=reply)


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Session).where(Session.user_id == user.id).order_by(Session.updated_at.desc())
    )
    return result.scalars().all()


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
    order_col = getattr(Alert, params.sort_by, Alert.created_at)
    query = query.order_by(order_col.desc() if params.sort_order == "desc" else order_col.asc())
    query = query.offset((params.page - 1) * params.page_size).limit(params.page_size)
    result = await db.execute(query)
    return result.scalars().all()


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
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    if body.action == "confirm":
        alert.status = "confirmed"
        alert.confirmed_by = user.username
    elif body.action == "dismiss":
        alert.status = "dismissed"
    else:
        raise HTTPException(status_code=400, detail="Invalid action")
    await db.commit()
    return {"detail": "ok", "status": alert.status}


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

    if body.session_id:
        result = await db.execute(select(Session).where(Session.id == body.session_id))
        session = result.scalar_one_or_none()
        if session is None:
            session = Session(id=body.session_id, user_id=user.id, title=_make_title(body.message))
            db.add(session)
            await db.commit()
            await db.refresh(session)
        else:
            if getattr(session, "sleep_status", "awake") == "sleeping":
                session.sleep_status = "awake"
            session.last_active_at = datetime.now(UTC)
            await db.commit()
    else:
        session = Session(user_id=user.id, title=_make_title(body.message))
        db.add(session)
        await db.commit()
        await db.refresh(session)

    user_msg = Message(session_id=session.id, role="user", content=body.message)
    db.add(user_msg)
    await db.commit()

    await tool_manager.reload()

    # Propagate user context for memory tools
    set_current_user(user_id=str(user.id), session_id=str(session.id))

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

        # ── Phase 1: Intent recognition ──────────────────
        yield _sse_event("status", {"message": "正在理解意图...", "session_id": session_id})
        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import SystemMessage
            intent_llm = ChatOpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                model="deepseek-v4-flash",
                temperature=0,
            )
            intent_resp = await intent_llm.ainvoke([
                SystemMessage(content="Classify the user's intent in ONE short phrase (max 20 chars Chinese). Be specific: e.g. '查询知识库', '执行运维命令', '故障排查', '数据分析', '文件操作', '系统配置'. Reply ONLY with the phrase."),
                HumanMessage(content=body.message),
            ])
            intent_text = intent_resp.content.strip().strip('"').strip("'").strip()[:30]
            yield _sse_event("intent", {
                "intent": intent_text or "通用对话",
                "session_id": session_id,
            })
        except Exception:
            yield _sse_event("intent", {
                "intent": "分析中...",
                "session_id": session_id,
            })
        yield _sse_event("status", {"message": "正在规划任务...", "session_id": session_id})

        try:
            # Inject relevant memories into agent context
            recall_context = await mm.prefetch(body.message)
            agent_messages = list(history_messages)
            if recall_context:
                from langchain_core.messages import SystemMessage
                agent_messages.append(SystemMessage(content=recall_context))
            agent_messages.append(HumanMessage(content=body.message))

            agent = await get_deep_agent()
            async for event in agent.astream_events(
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
                    s.title = await _generate_title(body.message, final_answer)
                await _db.commit()

            # Sync per-turn memories (LLM-based, dual personal+team scope)
            try:
                await mm.sync_turn(body.message, final_answer)
            except Exception:
                logger.exception("sync_turn failed in chat_stream")

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
