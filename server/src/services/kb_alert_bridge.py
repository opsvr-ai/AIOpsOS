"""Alert → Knowledge bridge — extracts wiki pages from alert analysis results.

When an alert is confirmed, this service generates a knowledge wiki page
from the alert's analysis_result JSONB, writes it to the wiki directory,
and updates the alert's knowledge_entry_id with a wikilink reference.
"""

import logging
import os
import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from src.config import settings
from src.models.alert import Alert
from src.models.base import async_session_factory

logger = logging.getLogger(__name__)


def _wiki_dir() -> str:
    return os.path.join(settings.wiki_path, "wiki")


def _safe_slug(title: str) -> str:
    s = title.lower().replace(" ", "-")
    s = re.sub(r"[^a-z0-9一-鿿_-]", "", s)
    return s[:80]


async def extract_alert_knowledge(alert_id: str) -> dict:
    """Extract knowledge from an alert and create a wiki page.

    Returns dict with ok, wiki_page_name, alert_title, content.
    """
    try:
        uid = UUID(alert_id)
    except ValueError:
        return {"ok": False, "error": f"Invalid alert ID: {alert_id}"}

    async with async_session_factory() as db:
        result = await db.execute(select(Alert).where(Alert.id == uid))
        alert = result.scalar_one_or_none()
        if alert is None:
            return {"ok": False, "error": "Alert not found"}

        analysis = alert.analysis_result or {}
        title = alert.title or "Untitled Alert"
        severity = alert.severity or "info"
        source = alert.source or "unknown"

        summary = analysis.get("summary", analysis.get("description", ""))
        root_cause = analysis.get("root_cause", analysis.get("cause", ""))
        recommendations = analysis.get("recommendations", analysis.get("suggestions", []))
        if isinstance(recommendations, str):
            recommendations = [recommendations]
        if not recommendations and "recommendation" in analysis:
            recommendations = [str(analysis["recommendation"])]

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        page_name = f"Alert-{_safe_slug(title)}-{alert_id[:8]}"

        content = (
            f"---\n"
            f"title: {title}\n"
            f"created: {today}\n"
            f"updated: {today}\n"
            f"type: query\n"
            f"tags: [告警, {severity}, {source}]\n"
            f"sources: []\n"
            f"alert_id: {alert_id}\n"
            f"---\n\n"
            f"# {title}\n\n"
            f"## 告警概要\n\n"
            f"- **严重程度**: {severity}\n"
            f"- **来源**: {source}\n"
            f"- **状态**: {alert.status}\n"
            f"- **确认时间**: {today}\n\n"
        )

        if summary:
            content += f"## 分析摘要\n\n{summary}\n\n"
        if root_cause:
            content += f"## 根因分析\n\n{root_cause}\n\n"
        if recommendations:
            content += "## 处理建议\n\n"
            for i, rec in enumerate(recommendations, 1):
                content += f"{i}. {rec}\n"
            content += "\n"

        content += (
            f"## 相关信息\n\n"
            f"- 原始告警 ID: `{alert_id}`\n"
            f"- 原始事件 ID: `{alert.event_id or 'N/A'}`\n"
        )

        os.makedirs(_wiki_dir(), exist_ok=True)
        wiki_path = os.path.join(_wiki_dir(), f"{page_name}.md")
        with open(wiki_path, "w", encoding="utf-8") as f:
            f.write(content)

        alert.knowledge_entry_id = page_name
        alert.updated_at = datetime.now(UTC)
        await db.commit()

        logger.info("Alert→Knowledge bridge: created %s for alert %s", page_name, alert_id)

        return {
            "ok": True,
            "wiki_page_name": page_name,
            "alert_title": title,
            "content": content,
        }
