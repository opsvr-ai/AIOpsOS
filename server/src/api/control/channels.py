from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from src.api.deps import DbSession, get_current_user, require_perm
from src.models.channel import AgentProfile, NotificationChannel
from src.schemas.channel import AgentProfileCreate, AgentProfileOut, ChannelCreate, ChannelOut

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
    channel = NotificationChannel(**body.model_dump())
    db.add(channel)
    await db.commit()
    await db.refresh(channel)
    return channel


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
    profile = AgentProfile(**body.model_dump())
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile
