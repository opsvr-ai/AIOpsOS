"""save_report tool — persists generated HTML reports to the database."""

from __future__ import annotations

import json as _json
import logging
import uuid

from langchain_core.tools import StructuredTool

from src.agent.context import get_current_space, get_current_user
from src.config import settings
from src.models.base import async_session_factory
from src.models.report import Report

logger = logging.getLogger(__name__)


def _get_report_url(report_id: str) -> str:
    """Generate the public URL for a report.
    
    In containerized deployments, PUBLIC_URL should be set to the frontend URL
    (e.g., http://your-domain.com) since nginx proxies /pub/ to the backend.
    
    If PUBLIC_URL is not configured or points to localhost:8000 (backend default),
    we return a relative path that works when accessed through the frontend.
    """
    public_url = settings.public_url or ""
    
    # If PUBLIC_URL is the default backend URL or empty, use relative path
    # This works because nginx proxies /pub/ to the backend
    if not public_url or public_url in ("http://localhost:8000", "http://127.0.0.1:8000"):
        # Return relative URL - works when accessed through frontend
        return f"/pub/reports/{report_id}"
    
    # Remove trailing slash if present
    public_url = public_url.rstrip("/")
    return f"{public_url}/pub/reports/{report_id}"


async def _save_report(
    title: str,
    html_content: str,
    description: str = "",
    theme: str = "ink",
) -> str:
    """Save a generated HTML report to the database.

    Args:
        title: Report title (keep under 100 chars).
        html_content: Complete self-contained HTML with embedded CSS.
        description: Short summary shown in report list.
        theme: One of ink, indigo, forest, kraft, dune.

    Returns:
        JSON with report_id, url, and title.
    """
    ctx = get_current_user()
    user_id = ctx.get("user_id", "")
    session_id = ctx.get("session_id", "")
    space = get_current_space()
    space_id = space.get("space_id", "")

    def _safe_uuid(v: str) -> any:
        try:
            return uuid.UUID(v)
        except (ValueError, TypeError, AttributeError):
            return None

    async with async_session_factory() as db:
        report = Report(
            id=uuid.uuid4(),
            user_id=_safe_uuid(user_id),
            space_id=_safe_uuid(space_id),
            session_id=_safe_uuid(session_id),
            title=title,
            description=description or "",
            html_content=html_content,
            theme=theme,
            status="published",
            # Set visibility to public by default so the link works without login
            visibility="public",
        )
        db.add(report)
        await db.commit()
        rid = str(report.id)

    report_url = _get_report_url(rid)
    
    return _json.dumps({
        "report_id": rid,
        "url": report_url,
        "title": title,
        "status": "published",
        "visibility": "public",
    }, ensure_ascii=False)


save_report_tool = StructuredTool.from_function(
    name="save_report",
    description=(
        "Save a generated HTML report to the database. "
        "Use after generating complete HTML report content. "
        "Parameters: title (report title), html_content (complete HTML), "
        "description (optional summary), theme (one of: ink, indigo, forest, kraft, dune). "
        "The current user, session, and space are automatically linked. "
        "Returns the report URL that can be shared with others."
    ),
    coroutine=_save_report,
)
