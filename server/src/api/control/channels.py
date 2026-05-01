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
