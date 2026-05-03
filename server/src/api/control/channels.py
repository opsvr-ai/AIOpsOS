import uuid
import logging
from datetime import datetime, UTC

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from pydantic import BaseModel
from src.api.deps import DbSession, get_current_user, require_perm
from src.services.channel_manager import channel_manager
from src.models.channel import AgentProfile, NotificationChannel
from src.schemas.channel import (
    AgentProfileCreate, AgentProfileOut, AgentProfileUpdate,
    ChannelCreate, ChannelUpdate, ChannelOut, TaskDispatchRequest, AgentMetrics,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/channels", response_model=list[ChannelOut])
async def list_channels(db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(
        select(NotificationChannel).order_by(NotificationChannel.created_at.desc())
    )
    return result.scalars().all()


@router.post("/channels", response_model=ChannelOut)
async def create_channel(
    body: ChannelCreate, db: DbSession, _=Depends(require_perm("channels", "create"))
):
    try:
        channel = NotificationChannel(**body.model_dump())
        db.add(channel)
        await db.commit()
        await db.refresh(channel)
        return channel
    except Exception as exc:
        logger.exception("Failed to create channel: %s", exc)
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/channels/{channel_id}")
async def delete_channel(
    channel_id: str, db: DbSession, _=Depends(require_perm("channels", "delete"))
):
    result = await db.execute(
        select(NotificationChannel).where(NotificationChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    await db.delete(channel)
    await db.commit()
    return {"detail": "deleted"}


@router.patch("/channels/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: str, body: ChannelUpdate, db: DbSession,
    _=Depends(require_perm("channels", "update"))
):
    result = await db.execute(
        select(NotificationChannel).where(NotificationChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(channel, field, value)
    await db.commit()
    await db.refresh(channel)
    return channel



class TestChannelRequest(BaseModel):
    channel_type: str
    config: dict = {}

@router.post("/channels/test")
async def test_channel(body: TestChannelRequest, _=Depends(get_current_user)):
    try:
        ok, msg = await channel_manager.test(body.channel_type, body.config)
        return {"ok": ok, "message": msg}
    except Exception as exc:
        logger.exception("Channel test failed: %s", exc)
        return {"ok": False, "message": f"测试异常: {exc}"}

@router.get("/channels/types")
async def list_channel_types(_=Depends(get_current_user)):
    return {"types": channel_manager.list_types()}



# ── Monitor lifecycle ────────────────────────────────────────────────

@router.get("/channels/{channel_id}/monitor/status")
async def get_monitor_status(channel_id: str, db: DbSession, _=Depends(get_current_user)):
    """Get WebSocket monitor connection status for a WeCom channel."""
    result = await db.execute(
        select(NotificationChannel).where(NotificationChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    if channel.channel_type != "wecom":
        raise HTTPException(status_code=400, detail="Monitor only supported for WeCom channels")

    sub_type = channel.config.get("wecom_sub_type", "bot_webhook")
    if sub_type != "bot_websocket":
        return {"connected": False, "account_id": "", "reason": "Not a WebSocket channel"}

    from src.services.channels.wecom.monitor import get_monitor
    monitor = get_monitor("default")
    connected = monitor is not None and monitor.is_connected
    return {
        "connected": connected,
        "account_id": "default",
        "channel_id": channel_id,
    }


@router.post("/channels/{channel_id}/monitor/start")
async def start_monitor_endpoint(channel_id: str, db: DbSession, _=Depends(get_current_user)):
    """Start WebSocket monitor for a WeCom bot_websocket channel."""
    result = await db.execute(
        select(NotificationChannel).where(NotificationChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    config = channel.config
    if config.get("wecom_sub_type") != "bot_websocket":
        raise HTTPException(status_code=400, detail="Start only supported for bot_websocket channels")

    bot_id = config.get("bot_id", "")
    bot_secret = config.get("bot_secret", "")
    if not bot_id or not bot_secret:
        raise HTTPException(status_code=400, detail="bot_id and bot_secret required")

    from src.services.channels.wecom.monitor import get_monitor, start_monitor
    from src.services.channels.wecom.agent_bridge import handle_wecom_message

    existing = get_monitor("default")
    if existing and existing.is_connected:
        return {"ok": True, "message": "Monitor already connected"}

    async def _bridge_callback(parsed_msg, ws, frame):
        await handle_wecom_message(parsed_msg, config, ws, frame)

    await start_monitor(
        bot_id=bot_id,
        bot_secret=bot_secret,
        ws_url=config.get("ws_api_base") or "",
        account_id="default",
        on_message=_bridge_callback,
    )
    return {"ok": True, "message": "Monitor started"}


@router.post("/channels/{channel_id}/monitor/stop")
async def stop_monitor_endpoint(channel_id: str, db: DbSession, _=Depends(get_current_user)):
    """Stop WebSocket monitor for a WeCom channel."""
    result = await db.execute(
        select(NotificationChannel).where(NotificationChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    from src.services.channels.wecom.monitor import stop_monitor, get_monitor
    monitor = get_monitor("default")
    if not monitor:
        return {"ok": True, "message": "No monitor running"}
    await stop_monitor("default")
    return {"ok": True, "message": "Monitor stopped"}

# ── WeChat Work App API ──────────────────────────────────────────────


def _resolve_wecom_api_base(config: dict) -> str:
    """解析企业微信 API 基础 URL。私有部署使用 api_base_url, 否则用云端地址。"""
    from src.services.channels.wecom.const import CLOUD_API_BASE
    if config.get("deployment_mode") == "private" and config.get("api_base_url"):
        return str(config["api_base_url"]).rstrip("/")
    return CLOUD_API_BASE


class AppSendRequest(BaseModel):
    msgtype: str = "text"
    content: str = ""
    touser: str = ""
    toparty: str = ""
    totag: str = ""


@router.post("/channels/{channel_id}/app/send")
async def app_send_message(channel_id: str, body: AppSendRequest, db: DbSession, _=Depends(get_current_user)):
    """发送企业微信应用消息 — 支持 text/markdown, 可指定 touser/toparty/totag."""
    result = await db.execute(
        select(NotificationChannel).where(NotificationChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    if channel.channel_type != "wecom":
        raise HTTPException(status_code=400, detail="App API only supported for WeCom channels")

    config = channel.config
    if config.get("wecom_sub_type") != "app":
        raise HTTPException(status_code=400, detail="Channel is not an app type")

    from src.services.channels.wecom.app_client import send_message as app_send
    corp_id = config.get("corp_id", "")
    corp_secret = config.get("corp_secret", "")
    agent_id = config.get("agent_id", 0)
    api_base = _resolve_wecom_api_base(config)

    if not corp_id or not corp_secret or not agent_id:
        raise HTTPException(status_code=400, detail="corp_id/corp_secret/agent_id required")

    try:
        result_data = await app_send(
            corp_id=corp_id, corp_secret=corp_secret, agent_id=int(agent_id),
            api_base=api_base, msgtype=body.msgtype, content=body.content,
            touser=body.touser, toparty=body.toparty, totag=body.totag,
        )
        return {"ok": result_data.get("errcode") == 0, "data": result_data}
    except Exception as exc:
        logger.exception("WeCom app send failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


class AppChatCreateRequest(BaseModel):
    name: str
    owner: str
    userlist: list[str]
    chatid: str = ""


@router.post("/channels/{channel_id}/app/chat/create")
async def app_create_chat(channel_id: str, body: AppChatCreateRequest, db: DbSession, _=Depends(get_current_user)):
    """创建企业微信应用群聊."""
    result = await db.execute(
        select(NotificationChannel).where(NotificationChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    config = channel.config
    if config.get("wecom_sub_type") != "app":
        raise HTTPException(status_code=400, detail="Channel is not an app type")

    from src.services.channels.wecom.app_client import create_app_chat
    corp_id = config.get("corp_id", "")
    corp_secret = config.get("corp_secret", "")
    agent_id = config.get("agent_id", 0)
    api_base = _resolve_wecom_api_base(config)

    try:
        result_data = await create_app_chat(
            corp_id=corp_id, corp_secret=corp_secret, agent_id=int(agent_id),
            api_base=api_base, name=body.name, owner=body.owner,
            userlist=body.userlist, chatid=body.chatid,
        )
        return {"ok": result_data.get("errcode") == 0, "data": result_data}
    except Exception as exc:
        logger.exception("WeCom app create_chat failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


class AppChatSendRequest(BaseModel):
    chatid: str
    msgtype: str = "text"
    content: str = ""


@router.post("/channels/{channel_id}/app/chat/send")
async def app_send_chat_message(channel_id: str, body: AppChatSendRequest, db: DbSession, _=Depends(get_current_user)):
    """向企业微信应用群聊发送消息."""
    result = await db.execute(
        select(NotificationChannel).where(NotificationChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    config = channel.config
    if config.get("wecom_sub_type") != "app":
        raise HTTPException(status_code=400, detail="Channel is not an app type")

    from src.services.channels.wecom.app_client import send_app_chat_message
    corp_id = config.get("corp_id", "")
    corp_secret = config.get("corp_secret", "")
    api_base = _resolve_wecom_api_base(config)

    try:
        result_data = await send_app_chat_message(
            corp_id=corp_id, corp_secret=corp_secret,
            api_base=api_base, chatid=body.chatid, msgtype=body.msgtype, content=body.content,
        )
        return {"ok": result_data.get("errcode") == 0, "data": result_data}
    except Exception as exc:
        logger.exception("WeCom app send_chat failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/channels/{channel_id}/app/chat/{chatid}")
async def app_get_chat(channel_id: str, chatid: str, db: DbSession, _=Depends(get_current_user)):
    """获取企业微信应用群聊信息."""
    result = await db.execute(
        select(NotificationChannel).where(NotificationChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    config = channel.config
    if config.get("wecom_sub_type") != "app":
        raise HTTPException(status_code=400, detail="Channel is not an app type")

    from src.services.channels.wecom.app_client import get_app_chat
    corp_id = config.get("corp_id", "")
    corp_secret = config.get("corp_secret", "")
    api_base = _resolve_wecom_api_base(config)

    try:
        result_data = await get_app_chat(
            corp_id=corp_id, corp_secret=corp_secret, chatid=chatid, api_base=api_base,
        )
        return {"ok": result_data.get("errcode") == 0, "data": result_data}
    except Exception as exc:
        logger.exception("WeCom app get_chat failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/agent-profiles", response_model=list[AgentProfileOut])
async def list_profiles(db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(
        select(AgentProfile).order_by(AgentProfile.created_at.desc())
    )
    return result.scalars().all()


@router.post("/agent-profiles", response_model=AgentProfileOut)
async def create_profile(
    body: AgentProfileCreate, db: DbSession,
    _=Depends(require_perm("agent_profiles", "create"))
):
    profile = AgentProfile(**body.model_dump(by_alias=True))
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


# ── In-memory stores (replace with DB tables in production) ──────────

_task_store: dict[str, list[dict]] = {}
_metrics_store: dict[str, list[dict]] = {}


# ── Agent Profile CRUD ──────────────────────────────────────────────

@router.get("/agent-profiles/{profile_id}", response_model=AgentProfileOut)
async def get_profile(profile_id: str, db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(
        select(AgentProfile).where(AgentProfile.id == profile_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="Agent profile not found")
    return profile


@router.patch("/agent-profiles/{profile_id}", response_model=AgentProfileOut)
async def update_profile(
    profile_id: str, body: AgentProfileUpdate, db: DbSession,
    _=Depends(require_perm("agent_profiles", "update"))
):
    result = await db.execute(
        select(AgentProfile).where(AgentProfile.id == profile_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="Agent profile not found")
    for field, value in body.model_dump(exclude_unset=True, by_alias=True).items():
        setattr(profile, field, value)
    await db.commit()
    await db.refresh(profile)
    return profile


@router.delete("/agent-profiles/{profile_id}")
async def delete_profile(
    profile_id: str, db: DbSession,
    _=Depends(require_perm("agent_profiles", "delete"))
):
    result = await db.execute(
        select(AgentProfile).where(AgentProfile.id == profile_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="Agent profile not found")
    await db.delete(profile)
    await db.commit()
    return {"detail": "deleted"}


# ── Task Dispatch ───────────────────────────────────────────────────

@router.post("/agent-profiles/{profile_id}/dispatch")
async def dispatch_task(
    profile_id: str, body: TaskDispatchRequest, db: DbSession,
    _=Depends(require_perm("agent_profiles", "update"))
):
    result = await db.execute(
        select(AgentProfile).where(AgentProfile.id == profile_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="Agent profile not found")
    if not profile.online:
        raise HTTPException(status_code=409, detail="Agent is offline")

    task_id = uuid.uuid4().hex
    task_entry = {
        "task_id": task_id,
        "type": body.type,
        "content": body.content,
        "status": "pending",
        "output": None,
        "created_at": datetime.now(UTC).isoformat(),
    }
    _task_store.setdefault(str(profile.id), []).append(task_entry)

    return {"task_id": task_id, "status": "pending"}


@router.get("/agent-profiles/{profile_id}/tasks")
async def get_profile_tasks(
    profile_id: str, db: DbSession, _=Depends(get_current_user)
):
    result = await db.execute(
        select(AgentProfile).where(AgentProfile.id == profile_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Agent profile not found")
    return _task_store.get(profile_id, [])


@router.get("/agent-profiles/{profile_id}/metrics")
async def get_profile_metrics(
    profile_id: str, db: DbSession, _=Depends(get_current_user)
):
    result = await db.execute(
        select(AgentProfile).where(AgentProfile.id == profile_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Agent profile not found")
    return _metrics_store.get(profile_id, [])
