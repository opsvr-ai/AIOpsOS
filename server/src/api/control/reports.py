"""Report CRUD + share endpoints."""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text

from src.api.deps import get_current_user, get_optional_space_id
from src.models.base import async_session_factory
from src.models.report import Report
from src.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()


class ReportCreate(BaseModel):
    title: str = "Untitled Report"
    description: str | None = None
    html_content: str
    theme: str = "ink"
    session_id: str | None = None


class ReportUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    html_content: str | None = None
    theme: str | None = None
    visibility: str | None = None


class ReportOut(BaseModel):
    id: str
    title: str
    description: str | None
    theme: str
    status: str
    visibility: str
    report_type: str
    date_range_start: str | None
    date_range_end: str | None
    session_id: str | None
    html_content: str
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


@router.post("/reports", response_model=ReportOut)
async def create_report(
    body: ReportCreate,
    user: User = Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
):
    async with async_session_factory() as db:
        report = Report(
            id=uuid.uuid4(),
            user_id=user.id,
            space_id=uuid.UUID(space_id) if space_id else None,
            session_id=uuid.UUID(body.session_id) if body.session_id else None,
            title=body.title,
            description=body.description,
            html_content=body.html_content,
            theme=body.theme,
            status="published",
        )
        db.add(report)
        await db.commit()
    return _to_out(report)


@router.get("/reports", response_model=list[ReportOut])
async def list_reports(
    user: User = Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
    skip: int = 0,
    limit: int = 20,
):
    async with async_session_factory() as db:
        conditions = ["user_id = CAST(:uid AS uuid)"]
        params: dict = {"uid": user.id, "limit": limit, "offset": skip}
        if space_id:
            conditions.append("(space_id = CAST(:sp AS uuid) OR space_id IS NULL)")
            params["sp"] = space_id
        where = " AND ".join(conditions)
        sql = (
            f"SELECT * FROM reports WHERE {where} "
            "ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
        )
        rows = await db.execute(text(sql), params,
        )
        reports = rows.fetchall()
    return [_row_to_out(r) for r in reports]


@router.get("/reports/{report_id}", response_model=ReportOut)
async def get_report(
    report_id: str,
    user: User = Depends(get_current_user),
):
    async with async_session_factory() as db:
        report = await db.get(Report, uuid.UUID(report_id))
        if not report:
            raise HTTPException(404, "Report not found")
        if report.visibility == "private" and str(report.user_id) != str(user.id):
            raise HTTPException(403, "Access denied")
        if report.visibility == "space" and str(report.user_id) != str(user.id):
            from src.models.space import SpaceMember
            result = await db.execute(
                select(SpaceMember).where(
                    SpaceMember.space_id == report.space_id,
                    SpaceMember.user_id == user.id,
                )
            )
            if not result.scalar_one_or_none():
                raise HTTPException(403, "Access denied")
    return _to_out(report)


@router.put("/reports/{report_id}", response_model=ReportOut)
async def update_report(
    report_id: str,
    body: ReportUpdate,
    user: User = Depends(get_current_user),
):
    async with async_session_factory() as db:
        report = await db.get(Report, uuid.UUID(report_id))
        if not report or str(report.user_id) != str(user.id):
            raise HTTPException(404, "Report not found")
        for field in ("title", "description", "html_content", "theme", "visibility"):
            val = getattr(body, field, None)
            if val is not None:
                setattr(report, field, val)
        report.updated_at = datetime.now(UTC)
        await db.commit()
    return _to_out(report)


@router.delete("/reports/{report_id}")
async def delete_report(
    report_id: str,
    user: User = Depends(get_current_user),
):
    async with async_session_factory() as db:
        report = await db.get(Report, uuid.UUID(report_id))
        if not report or str(report.user_id) != str(user.id):
            raise HTTPException(404, "Report not found")
        await db.delete(report)
        await db.commit()
    return {"ok": True}


def _to_out(r: Report) -> dict:
    return {
        "id": str(r.id),
        "title": r.title,
        "description": r.description,
        "theme": r.theme,
        "status": r.status,
        "visibility": r.visibility,
        "report_type": r.report_type,
        "date_range_start": r.date_range_start,
        "date_range_end": r.date_range_end,
        "session_id": str(r.session_id) if r.session_id else None,
        "html_content": r.html_content,
        "created_at": r.created_at.isoformat() if r.created_at else "",
        "updated_at": r.updated_at.isoformat() if r.updated_at else "",
    }


def _row_to_out(r) -> dict:
    return {
        "id": str(r.id),
        "title": r.title,
        "description": r.description,
        "theme": r.theme,
        "status": r.status,
        "visibility": r.visibility,
        "report_type": r.report_type,
        "date_range_start": r.date_range_start,
        "date_range_end": r.date_range_end,
        "session_id": str(r.session_id) if r.session_id else None,
        "html_content": r.html_content,
        "created_at": r.created_at.isoformat() if r.created_at else "",
        "updated_at": r.updated_at.isoformat() if r.updated_at else "",
    }
