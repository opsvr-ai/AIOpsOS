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
        )
        db.add(report)
        await db.commit()
        rid = str(report.id)

    return _json.dumps({
        "report_id": rid,
        "url": f"{settings.public_url}/pub/reports/{rid}",
        "title": title,
        "status": "published",
    }, ensure_ascii=False)


save_report_tool = StructuredTool.from_function(
    name="save_report",
    description=(
        "Save a generated HTML report to the database. "
        "Use after generating complete HTML report content. "
        "Parameters: title (report title), html_content (complete HTML), "
        "description (optional summary), theme (one of: ink, indigo, forest, kraft, dune). "
        "The current user, session, and space are automatically linked."
    ),
    coroutine=_save_report,
)
