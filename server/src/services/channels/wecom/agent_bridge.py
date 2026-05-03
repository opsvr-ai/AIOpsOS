"""WeCom message → AIOpsOS agent bridge.

Routes incoming WeCom messages to the AIOpsOS LangGraph agent and streams
replies back via WeCom WebSocket (reply_stream) or accumulates for webhook.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid as _uuid
from datetime import UTC, datetime

import aiohttp

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agent.context import set_current_user, set_current_space
from src.agent.deep_agent import get_deep_agent
from src.models.base import async_session_factory
from src.models.session import Message, Session
from src.models.user import User
from src.services.memory_provider import MemoryManager
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .const import REPLY_SEND_TIMEOUT
from .message_parser import ParsedMessage
from .message_sender import reply_stream, reply_stream_non_blocking, send_message

logger = logging.getLogger(__name__)

# TTL cache for tool reload — avoid DB hit on every WeCom message
_last_tool_reload: float = 0.0
_TOOL_RELOAD_TTL: float = 60.0


async def _resolve_user(channel_config: dict) -> User:
    """Find or create the internal user for WeCom message attribution."""
    bot_user_id = channel_config.get("bot_user_id")
    async with async_session_factory() as db:
        if bot_user_id:
            result = await db.execute(select(User).where(User.id == bot_user_id).options(selectinload(User.roles)))
            user = result.scalar_one_or_none()
            if user and user.is_active:
                return user

        # Look for existing wecom_bot system user
        result = await db.execute(select(User).where(User.username == "wecom_bot").options(selectinload(User.roles)))
        user = result.scalar_one_or_none()
        if user:
            return user

        # Create a minimal system user for WeCom messages
        import hashlib, secrets
        bot_id = channel_config.get("bot_id", "wecom")
        email = f"wecom-{hashlib.md5(bot_id.encode()).hexdigest()[:8]}@aiopsos.local"
        user = User(
            username="wecom_bot",
            email=email,
            hashed_password=f"$wecom_dummy${secrets.token_hex(32)}",
            display_name="WeCom Bot",
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        logger.info("Created wecom_bot system user: %s", user.id)
        return user


def _make_title(text: str, max_chars: int = 60) -> str:
    t = text.replace("\n", " ").strip()
    if len(t) <= max_chars:
        return t
    cut = t.rfind(" ", 0, max_chars)
    if cut > max_chars // 2:
        return t[:cut]
    return t[: max_chars - 1]


async def handle_wecom_message(
    parsed_msg: ParsedMessage,
    channel_config: dict,
    ws: aiohttp.ClientWebSocketResponse | None = None,
    frame: dict | None = None,
) -> str:
    """Handle an incoming WeCom message by routing it to the AIOpsOS agent.

    If `ws` and `frame` are provided, replies are streamed back via WeCom
    WebSocket reply_stream. Otherwise the full reply text is returned.

    Returns the complete agent reply text.
    """
    if not parsed_msg.text:
        return ""

    chatid = parsed_msg.chatid
    sender = parsed_msg.sender_userid
    logger.info("[wecom-bridge] msg from %s chatid=%s text_len=%d", sender, chatid, len(parsed_msg.text))

    async with async_session_factory() as db:
        # 1. Resolve user
        user = await _resolve_user(channel_config)

        # 2. Find or create session
        result = await db.execute(
            select(Session).where(
                Session.source_platform == "wecom",
                Session.source_chat_id == chatid,
            )
        )
        session = result.scalar_one_or_none()

        if session is None:
            session = Session(
                id=_uuid.uuid4(),
                user_id=user.id,
                title=_make_title(parsed_msg.text),
                source_platform="wecom",
                source_chat_id=chatid,
                sleep_status="awake",
                auto_consolidate=True,
                memory_status="unconsolidated",
                last_active_at=datetime.now(UTC),
            )
            db.add(session)
            await db.commit()
            await db.refresh(session)
        else:
            was_sleeping = session.sleep_status == "sleeping"
            session.sleep_status = "awake"
            session.last_active_at = datetime.now(UTC)
            if was_sleeping:
                session.memory_status = "unconsolidated"
            await db.commit()

        session_id = str(session.id)

        # 3. Save user message
        user_msg = Message(session_id=session.id, role="user", content=parsed_msg.text)
        db.add(user_msg)
        session.turn_count = (session.turn_count or 0) + 1
        await db.commit()

    # 4. Set agent context
    set_current_user(
        user_id=str(user.id),
        session_id=session_id,
        username=user.username,
        email=user.email or "",
        roles=[r.name for r in (user.roles or [])],
    )
    set_current_space(space_id=str(session.space_id) if session.space_id else "")

    # 5. Initialize memory
    mm = MemoryManager()
    mm.initialize(
        session_id,
        user_id=str(user.id),
        platform="wecom",
        space_id=str(session.space_id) if session.space_id else "",
    )

    # 6. Build agent messages — parallelize prefetch + system block + agent init
    recall_context, memory_block, _agent = await asyncio.gather(
        mm.prefetch(parsed_msg.text),
        mm.system_prompt_block(),
        get_deep_agent(),
    )

    agent_messages: list = []
    if memory_block:
        agent_messages.append(SystemMessage(content=memory_block))
    if recall_context:
        agent_messages.append(SystemMessage(content=recall_context))
    agent_messages.append(HumanMessage(content=parsed_msg.text))

    # 7. Run agent with streaming (astream_events v2, same pattern as /chat/stream)
    stream_id = ""
    full_reply = ""

    try:
        async for event in _agent.astream_events(
            {"messages": agent_messages},
            version="v2",
            config={"recursion_limit": 100},
        ):
            if event.get("event") != "on_chat_model_stream":
                continue
            chunk = event.get("data", {}).get("chunk")
            content = (
                getattr(chunk, "content", None)
                if hasattr(chunk, "content")
                else chunk.get("content", None) if isinstance(chunk, dict) else None
            )
            if content:
                full_reply += content
                if ws and frame:
                    if not stream_id:
                        stream_id = event.get("run_id", _uuid.uuid4().hex)
                        try:
                            await reply_stream(ws, frame, stream_id, content, finish=False)
                        except Exception:
                            pass
                    else:
                        try:
                            await reply_stream_non_blocking(ws, frame, stream_id, content, finish=False)
                        except Exception:
                            pass
    except Exception as exc:
        logger.exception("[wecom-bridge] agent error: %s", exc)
        full_reply = full_reply or f"处理消息时出错: {exc}"

    # 8. Finalize stream (must send finish=True to display the message)
    if ws and frame and stream_id:
        try:
            await reply_stream_non_blocking(ws, frame, stream_id, "", finish=True)
        except Exception:
            pass

    reply_text = full_reply or "Agent produced no output."

    # 9. Fallback: if streaming produced no output, send via aibot_send_msg
    if ws and parsed_msg.chatid and not stream_id:
        try:
            await send_message(ws, parsed_msg.chatid, msgtype="markdown", content=reply_text)
        except Exception:
            logger.exception("[wecom-bridge] send_message failed")

    # 9. Save assistant message
    async with async_session_factory() as db:
        assistant_msg = Message(
            session_id=_uuid.UUID(session_id),
            role="assistant",
            content=reply_text,
            extra_metadata={"source": "wecom"},
        )
        db.add(assistant_msg)
        await db.commit()

    # 10. Background memory sync
    try:
        await mm.sync_turn(parsed_msg.text, reply_text)
    except Exception:
        logger.exception("[wecom-bridge] sync_turn failed")

    logger.info("[wecom-bridge] reply sent: session=%s len=%d", session_id, len(reply_text))
    return reply_text
