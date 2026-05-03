"""Log search API — query log_events with filters, error context, and aggregation."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from src.api.deps import DbSession, get_current_user
from src.models.log import LogEvent

log_search_router = APIRouter(prefix="/api/v1/logs", tags=["Log Search"])


@log_search_router.get("/search")
async def search_logs(
    service: str | None = Query(None),
    level: str | None = Query(None),
    keyword: str | None = Query(None),
    trace_id: str | None = Query(None),
    limit: int = Query(100, le=1000),
    db: DbSession = None,
    _=Depends(get_current_user),
):
    query = select(LogEvent)
    if service:
        query = query.where(LogEvent.service == service)
    if level:
        query = query.where(LogEvent.level == level.upper())
    if keyword:
        query = query.where(LogEvent.message.ilike(f"%{keyword}%"))
    if trace_id:
        query = query.where(LogEvent.trace_id == trace_id)

    query = query.order_by(LogEvent.ingested_at.desc()).limit(limit)
    result = await db.execute(query)
    logs = result.scalars().all()

    return {
        "items": [
            {
                "id": str(log.id),
                "ingested_at": log.ingested_at.isoformat() if log.ingested_at else None,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "service": log.service,
                "host": log.host,
                "level": log.level,
                "trace_id": log.trace_id,
                "message": log.message,
            }
            for log in logs
        ],
    }


@log_search_router.get("/error-context")
async def get_error_context(
    trace_id: str = Query(...),
    before_seconds: int = Query(30),
    after_seconds: int = Query(30),
    db: DbSession = None,
    _=Depends(get_current_user),
):
    target = await db.execute(
        select(LogEvent).where(LogEvent.trace_id == trace_id).limit(1)
    )
    target_log = target.scalar_one_or_none()
    if not target_log or not target_log.timestamp:
        return {"items": [], "message": "No log found for this trace_id"}

    window_start = target_log.timestamp - timedelta(seconds=before_seconds)
    window_end = target_log.timestamp + timedelta(seconds=after_seconds)

    result = await db.execute(
        select(LogEvent)
        .where(
            LogEvent.service == target_log.service,
            LogEvent.timestamp.between(window_start, window_end),
        )
        .order_by(LogEvent.timestamp.asc())
        .limit(200)
    )
    logs = result.scalars().all()

    return {
        "items": [
            {
                "id": str(log.id),
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "service": log.service,
                "host": log.host,
                "level": log.level,
                "trace_id": log.trace_id,
                "message": log.message,
            }
            for log in logs
        ],
        "target_timestamp": target_log.timestamp.isoformat() if target_log.timestamp else None,
    }


@log_search_router.get("/count")
async def count_logs(
    service: str | None = Query(None),
    level: str | None = Query(None),
    db: DbSession = None,
    _=Depends(get_current_user),
):
    query = select(LogEvent.level, func.count()).select_from(LogEvent)
    if service:
        query = query.where(LogEvent.service == service)
    if level:
        query = query.where(LogEvent.level == level.upper())
    query = query.group_by(LogEvent.level)

    result = await db.execute(query)
    counts = {row[0]: row[1] for row in result.all()}
    return {"counts": counts, "total": sum(counts.values())}
