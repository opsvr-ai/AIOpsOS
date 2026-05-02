"""Dashboard summary — aggregated stats for the overview page."""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text

from src.api.deps import get_current_user, get_optional_space_id
from src.models.base import async_session_factory

logger = logging.getLogger(__name__)

router = APIRouter()


def _space_filter(space_id: str | None, col: str = "space_id") -> str:
    """Generate a SQL filter that includes NULL space_ids."""
    if space_id:
        return f"({col} = CAST(:sp AS uuid) OR {col} IS NULL)"
    return "1=1"


@router.get("/dashboard/summary")
async def get_dashboard_summary(
    user=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
):
    """Aggregated dashboard stats in a single response."""

    params: dict = {}
    if space_id:
        params["sp"] = space_id

    async with async_session_factory() as db:

        # ── alerts by severity ──
        alert_rows = await db.execute(
            text(
                f"""SELECT severity, COUNT(*) FROM alerts
                 WHERE {_space_filter(space_id)}
                 GROUP BY severity"""
            ),
            params,
        )
        alert_map = {r.severity: r.count for r in alert_rows.fetchall()}

        # ── agents ──
        agent_total = await db.scalar(
            text(f"SELECT COUNT(*) FROM agents WHERE {_space_filter(space_id)}"),
            params,
        )
        agent_online = await db.scalar(
            text(
                f"""SELECT COUNT(*) FROM agent_profiles ap
                 JOIN agents a ON a.id = ap.agent_id
                 WHERE ap.online = true AND {_space_filter(space_id, 'a.space_id')}"""
            ),
            params,
        )

        # ── sessions ──
        sess_active = await db.scalar(
            text(
                f"""SELECT COUNT(*) FROM sessions
                 WHERE status = 'active' AND {_space_filter(space_id)}"""
            ),
            params,
        )
        sess_sleeping = await db.scalar(
            text(
                f"""SELECT COUNT(*) FROM sessions
                 WHERE sleep_status = 'sleeping' AND {_space_filter(space_id)}"""
            ),
            params,
        )
        sess_unconsolidated = await db.scalar(
            text(
                f"""SELECT COUNT(*) FROM sessions
                 WHERE memory_status = 'unconsolidated' AND status = 'active'
                 AND {_space_filter(space_id)}"""
            ),
            params,
        )

        # ── cron jobs ──
        cron_total = await db.scalar(
            text(f"SELECT COUNT(*) FROM cron_jobs WHERE {_space_filter(space_id)}"),
            params,
        )
        cron_enabled = await db.scalar(
            text(
                f"""SELECT COUNT(*) FROM cron_jobs
                 WHERE enabled = true AND {_space_filter(space_id)}"""
            ),
            params,
        )

        # ── knowledge ──
        kb_docs = await db.scalar(
            text(f"SELECT COUNT(*) FROM knowledge_documents WHERE {_space_filter(space_id)}"),
            params,
        )
        kb_memories = await db.scalar(
            text(f"SELECT COUNT(*) FROM agent_memories WHERE {_space_filter(space_id)}"),
            params,
        )

        # ── CMDB ──
        cmdb_nodes = await db.scalar(
            text(f"SELECT COUNT(*) FROM cmdb_nodes WHERE {_space_filter(space_id)}"),
            params,
        )
        cmdb_pending = await db.scalar(
            text(
                f"""SELECT COUNT(*) FROM cmdb_review_items
                 WHERE status = 'pending' AND {_space_filter(space_id)}"""
            ),
            params,
        )

        # ── DataSources ──
        ds_total = await db.scalar(
            text(f"SELECT COUNT(*) FROM datasources WHERE {_space_filter(space_id)}"),
            params,
        )
        ds_enabled = await db.scalar(
            text(
                f"""SELECT COUNT(*) FROM datasources
                 WHERE is_enabled = true AND {_space_filter(space_id)}"""
            ),
            params,
        )
        ds_last = await db.execute(
            text(
                f"""SELECT MAX(last_ingested_at) FROM datasources
                 WHERE last_ingested_at IS NOT NULL AND {_space_filter(space_id)}"""
            ),
            params,
        )
        last_ingestion = ds_last.scalar()

        # ── ITSM ──
        itsm_total = await db.scalar(
            text(f"SELECT COUNT(*) FROM itsm_tickets WHERE {_space_filter(space_id)}"),
            params,
        )
        itsm_open = await db.scalar(
            text(
                f"""SELECT COUNT(*) FROM itsm_tickets
                 WHERE status IN ('open', 'in_progress', 'pending')
                 AND {_space_filter(space_id)}"""
            ),
            params,
        )

        # ── Recent activities ──
        activities: list[dict] = []

        alert_rows2 = await db.execute(
            text(
                f"""SELECT 'alert' AS kind, title, severity, created_at FROM alerts
                 WHERE {_space_filter(space_id)}
                 ORDER BY created_at DESC LIMIT 5"""
            ),
            params,
        )
        for r in alert_rows2.fetchall():
            activities.append({
                "kind": "alert", "title": r.title,
                "severity": r.severity,
                "time": r.created_at.isoformat() if r.created_at else "",
            })

        mem_rows = await db.execute(
            text(
                f"""SELECT 'consolidation' AS kind, title, created_at FROM agent_memories
                 WHERE tags @> '["session-summary"]'::jsonb AND {_space_filter(space_id)}
                 ORDER BY created_at DESC LIMIT 3"""
            ),
            params,
        )
        for r in mem_rows.fetchall():
            activities.append({
                "kind": "consolidation",
                "title": r.title or "会话记忆巩固完成",
                "time": r.created_at.isoformat() if r.created_at else "",
            })

        sync_rows = await db.execute(
            text(
                f"""SELECT 'cmdb_sync' AS kind, status AS title, started_at AS created_at
                 FROM cmdb_sync_logs
                 WHERE {_space_filter(space_id)}
                 ORDER BY started_at DESC LIMIT 3"""
            ),
            params,
        )
        for r in sync_rows.fetchall():
            activities.append({
                "kind": "cmdb_sync",
                "title": f"CMDB 同步: {r.title}",
                "time": r.created_at.isoformat() if r.created_at else "",
            })

        activities.sort(key=lambda x: x["time"], reverse=True)
        activities = activities[:8]

        # ── overall status ──
        alert_critical = alert_map.get("critical", 0)
        overall = "healthy"
        if alert_critical > 0:
            overall = "critical"
        elif alert_map.get("warning", 0) > 3:
            overall = "warning"
        elif ds_enabled == 0 and ds_total and ds_total > 0:
            overall = "warning"

    return {
        "system_status": {
            "overall": overall,
            "online_agents": agent_online or 0,
            "active_sessions": sess_active or 0,
            "last_ingestion": last_ingestion.isoformat() if last_ingestion else None,
        },
        "alerts_summary": {
            "critical": alert_map.get("critical", 0),
            "warning": alert_map.get("warning", 0),
            "info": alert_map.get("info", 0),
            "total": sum(alert_map.values()),
        },
        "agents_summary": {
            "total": agent_total or 0,
            "online": agent_online or 0,
        },
        "sessions_summary": {
            "active": sess_active or 0,
            "sleeping": sess_sleeping or 0,
            "unconsolidated": sess_unconsolidated or 0,
        },
        "cron_summary": {
            "total": cron_total or 0,
            "enabled": cron_enabled or 0,
        },
        "knowledge_summary": {
            "documents": kb_docs or 0,
            "memories": kb_memories or 0,
        },
        "data_pipeline": {
            "cmdb": {"nodes": cmdb_nodes or 0, "pending_reviews": cmdb_pending or 0},
            "datasources": {"total": ds_total or 0, "enabled": ds_enabled or 0},
            "itsm": {"total": itsm_total or 0, "open": itsm_open or 0},
        },
        "recent_activities": activities,
    }
