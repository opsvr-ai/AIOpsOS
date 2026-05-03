"""ITSM search API — query ITSM tickets with filters, detail, and service timeline."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select

from src.api.deps import DbSession, get_current_user, get_optional_space_id
from src.models.itsm import ItsmTicket

itsm_search_router = APIRouter(prefix="/api/v1/itsm", tags=["ITSM Search"])


@itsm_search_router.get("/tickets")
async def search_tickets(
    service: str | None = Query(None),
    ticket_type: str | None = Query(None),
    status: str | None = Query(None),
    keyword: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: DbSession = None,
    space_id: str | None = Depends(get_optional_space_id),
    _=Depends(get_current_user),
):
    query = select(ItsmTicket)
    if space_id:
        query = query.where(
            (ItsmTicket.space_id == space_id) | (ItsmTicket.space_id.is_(None))
        )
    if service:
        query = query.where(ItsmTicket.affected_service == service)
    if ticket_type:
        query = query.where(ItsmTicket.ticket_type == ticket_type)
    if status:
        query = query.where(ItsmTicket.status == status)
    if keyword:
        query = query.where(
            or_(
                ItsmTicket.title.ilike(f"%{keyword}%"),
                ItsmTicket.raw_data.cast(str).ilike(f"%{keyword}%"),
            )
        )

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(
        query.order_by(ItsmTicket.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    tickets = result.scalars().all()

    return {
        "items": [
            {
                "id": str(t.id),
                "external_id": t.external_id,
                "ticket_type": t.ticket_type,
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "affected_service": t.affected_service,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
                "linked_alert_ids": [str(aid) for aid in (t.linked_alert_ids or [])],
            }
            for t in tickets
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@itsm_search_router.get("/tickets/{ticket_id}")
async def get_ticket_detail(
    ticket_id: str,
    db: DbSession = None,
    space_id: str | None = Depends(get_optional_space_id),
    _=Depends(get_current_user),
):
    from uuid import UUID

    base_query = select(ItsmTicket)
    if space_id:
        base_query = base_query.where(
            (ItsmTicket.space_id == space_id) | (ItsmTicket.space_id.is_(None))
        )
    try:
        uid = UUID(ticket_id)
        result = await db.execute(base_query.where(ItsmTicket.id == uid))
    except ValueError:
        result = await db.execute(base_query.where(ItsmTicket.external_id == ticket_id))

    ticket = result.scalar_one_or_none()
    if not ticket:
        return {"error": "Ticket not found"}

    return {
        "id": str(ticket.id),
        "external_id": ticket.external_id,
        "ticket_type": ticket.ticket_type,
        "title": ticket.title,
        "status": ticket.status,
        "priority": ticket.priority,
        "affected_service": ticket.affected_service,
        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
        "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        "raw_data": ticket.raw_data,
        "linked_alert_ids": [str(aid) for aid in (ticket.linked_alert_ids or [])],
    }


@itsm_search_router.get("/service-timeline")
async def get_service_timeline(
    service: str = Query(...),
    time_start: str | None = Query(None),
    time_end: str | None = Query(None),
    db: DbSession = None,
    space_id: str | None = Depends(get_optional_space_id),
    _=Depends(get_current_user),
):
    query = select(ItsmTicket).where(ItsmTicket.affected_service == service)
    if space_id:
        query = query.where(
            (ItsmTicket.space_id == space_id) | (ItsmTicket.space_id.is_(None))
        )
    if time_start:
        query = query.where(ItsmTicket.created_at >= datetime.fromisoformat(time_start))
    if time_end:
        query = query.where(ItsmTicket.created_at <= datetime.fromisoformat(time_end))

    result = await db.execute(query.order_by(ItsmTicket.created_at.asc()))
    tickets = result.scalars().all()

    return {
        "service": service,
        "items": [
            {
                "id": str(t.id),
                "external_id": t.external_id,
                "ticket_type": t.ticket_type,
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in tickets
        ],
    }
