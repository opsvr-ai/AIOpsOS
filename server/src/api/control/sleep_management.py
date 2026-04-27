"""Sleep management API — session sleep/wake status, memory consolidation control."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func as sa_func, select, update

from src.api.deps import get_current_user
from src.models.base import async_session_factory
from src.models.session import Session

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/sleep-management/sessions")
async def list_sleep_sessions(
    user=Depends(get_current_user),
):
    """List user sessions with sleep and memory consolidation status."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(Session)
            .where(Session.user_id == user.id)
            .order_by(Session.last_active_at.desc())
            .limit(50)
        )
        sessions = list(result.scalars().all())

    return [
        {
            "id": str(s.id),
            "title": s.title or "新对话",
            "status": s.status,
            "sleep_status": s.sleep_status,
            "memory_status": s.memory_status,
            "auto_consolidate": s.auto_consolidate,
            "last_active_at": s.last_active_at.isoformat() if s.last_active_at else None,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        }
        for s in sessions
    ]


@router.get("/sleep-management/stats")
async def get_sleep_stats(
    user=Depends(get_current_user),
):
    """Get sleep and consolidation statistics for the current user."""
    async with async_session_factory() as db:
        total = await db.scalar(
            select(sa_func.count(Session.id))
            .where(Session.user_id == user.id)
        )
        sleeping = await db.scalar(
            select(sa_func.count(Session.id))
            .where(Session.user_id == user.id)
            .where(Session.sleep_status == "sleeping")
        )
        unconsolidated = await db.scalar(
            select(sa_func.count(Session.id))
            .where(Session.user_id == user.id)
            .where(Session.memory_status == "unconsolidated")
        )

    return {
        "total": total or 0,
        "sleeping": sleeping or 0,
        "unconsolidated": unconsolidated or 0,
    }


@router.post("/sleep-management/sessions/{session_id}/toggle")
async def toggle_auto_consolidate(
    session_id: str,
    user=Depends(get_current_user),
):
    """Toggle the auto_consolidate switch for a session."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(Session.auto_consolidate)
            .where(Session.id == session_id)
            .where(Session.user_id == user.id)
        )
        row = result.one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Session not found")

        new_value = not row[0]
        await db.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(auto_consolidate=new_value)
        )
        await db.commit()
        return {"auto_consolidate": new_value}


@router.post("/sleep-management/sessions/{session_id}/consolidate")
async def manual_consolidate(
    session_id: str,
    user=Depends(get_current_user),
):
    """Manually trigger memory consolidation for a session."""
    from src.agent.sub_agents.memory_consolidation_agent import (
        MemoryConsolidationAgent,
    )

    try:
        agent = MemoryConsolidationAgent()
        result = await agent.consolidate(session_id, str(user.id))
        return {"ok": True, **result}
    except Exception as e:
        logger.exception("Manual consolidation failed for session %s", session_id)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sleep-management/sessions/{session_id}/wake")
async def wake_session(
    session_id: str,
    user=Depends(get_current_user),
):
    """Manually wake a sleeping session."""
    from src.services.sleep_detector import sleep_detector

    async with async_session_factory() as db:
        result = await db.execute(
            select(Session.id)
            .where(Session.id == session_id)
            .where(Session.user_id == user.id)
        )
        if result.one_or_none() is None:
            raise HTTPException(status_code=404, detail="Session not found")

    await sleep_detector.wake_session(session_id)
    return {"detail": "awakened"}
