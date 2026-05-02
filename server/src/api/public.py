"""Public routes — no API prefix, serves shareable content directly."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from src.api.deps import get_current_user_optional, get_db
from src.models.report import Report

logger = logging.getLogger(__name__)
router = APIRouter(tags=["public"])


@router.get("/pub/reports/{report_id}", response_class=HTMLResponse)
async def view_report_public(
    report_id: str,
    request: Request,
    db=Depends(get_db),
):
    """Serve a published report as a standalone HTML page.

    visibility=private: owner only.
    visibility=space: authenticated users in the same space.
    visibility=public: anyone.
    """
    result = await db.execute(
        select(Report).where(Report.id == uuid.UUID(report_id))
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(404, "Report not found")

    if report.visibility == "private":
        user = await get_current_user_optional(request, db)
        if not user or str(user.id) != str(report.user_id):
            raise HTTPException(403, "You do not have access to this report")
    elif report.visibility == "space":
        user = await get_current_user_optional(request, db)
        if not user:
            raise HTTPException(401, "Login required to view this report")
        if str(user.id) != str(report.user_id):
            from src.models.space import SpaceMember
            result = await db.execute(
                select(SpaceMember).where(
                    SpaceMember.space_id == report.space_id,
                    SpaceMember.user_id == user.id,
                )
            )
            if not result.scalar_one_or_none():
                raise HTTPException(403, "You do not have access to this report")

    return HTMLResponse(content=report.html_content)
