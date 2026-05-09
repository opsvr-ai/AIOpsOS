"""Operations analytics — aggregated stats for the admin analytics page."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from src.api.deps import get_current_user
from src.models.base import async_session_factory

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/admin/analytics/overview")
async def get_analytics_overview(user=Depends(get_current_user)):
    """Summary cards: users, sessions, messages, spaces, feedback."""
    today = datetime.now(UTC).date()

    async with async_session_factory() as db:
        # ── users ──
        user_total = await db.scalar(text("SELECT COUNT(*) FROM users"))
        user_active, user_pending, user_invited = 0, 0, 0
        user_rows = await db.execute(
            text("SELECT status, source, COUNT(*) FROM users GROUP BY status, source")
        )
        for r in user_rows.fetchall():
            c = r.count
            if r.status == "active":
                user_active += c
            elif r.status == "pending":
                user_pending += c
            if r.source == "invited":
                user_invited += c

        # ── sessions ──
        sess_total = await db.scalar(text("SELECT COUNT(*) FROM sessions"))
        sess_active = await db.scalar(
            text("SELECT COUNT(*) FROM sessions WHERE status = 'active'")
        )
        sess_today = await db.scalar(
            text("SELECT COUNT(*) FROM sessions WHERE created_at >= :today"),
            {"today": today},
        )

        # ── messages ──
        msg_total = await db.scalar(text("SELECT COUNT(*) FROM messages"))

        # ── spaces ──
        space_total = await db.scalar(text("SELECT COUNT(*) FROM spaces"))

        # ── feedback ──
        fb_bugs = await db.scalar(
            text("SELECT COUNT(*) FROM feedbacks WHERE type = 'bug'")
        )
        fb_features = await db.scalar(
            text("SELECT COUNT(*) FROM feedbacks WHERE type = 'feature'")
        )
        fb_open_bugs = await db.scalar(
            text(
                "SELECT COUNT(*) FROM feedbacks WHERE type = 'bug' "
                "AND status NOT IN ('已修复', '已上线', '驳回')"
            )
        )

    return {
        "users": {
            "total": user_total or 0,
            "active": user_active,
            "pending": user_pending,
            "invited": user_invited,
        },
        "sessions": {
            "total": sess_total or 0,
            "active": sess_active or 0,
            "today": sess_today or 0,
        },
        "messages": {"total": msg_total or 0},
        "spaces": {"total": space_total or 0},
        "feedback": {
            "bugs": fb_bugs or 0,
            "features": fb_features or 0,
            "open_bugs": fb_open_bugs or 0,
        },
    }


@router.get("/admin/analytics/trends")
async def get_analytics_trends(
    days: int = Query(30, ge=7, le=365),
    user=Depends(get_current_user),
):
    """Time-series data for the last N days, plus top-N rankings."""

    async with async_session_factory() as db:
        # ── daily series using generate_series ──
        rows = await db.execute(
            text(
                """
                WITH d AS (
                    SELECT generate_series(
                        (CURRENT_DATE - CAST(:days AS integer) + 1)::date,
                        CURRENT_DATE,
                        '1 day'::interval
                    )::date AS day
                )
                SELECT
                    d.day,
                    COALESCE(u.cnt, 0) AS registrations,
                    COALESCE(s.cnt, 0) AS sessions,
                    COALESCE(m.cnt, 0) AS messages,
                    COALESCE(fb_bug.cnt, 0) AS feedback_bugs,
                    COALESCE(fb_feat.cnt, 0) AS feedback_features
                FROM d
                LEFT JOIN (
                    SELECT created_at::date AS day, COUNT(*) AS cnt
                    FROM users GROUP BY day
                ) u ON u.day = d.day
                LEFT JOIN (
                    SELECT created_at::date AS day, COUNT(*) AS cnt
                    FROM sessions GROUP BY day
                ) s ON s.day = d.day
                LEFT JOIN (
                    SELECT created_at::date AS day, COUNT(*) AS cnt
                    FROM messages GROUP BY day
                ) m ON m.day = d.day
                LEFT JOIN (
                    SELECT created_at::date AS day, COUNT(*) AS cnt
                    FROM feedbacks WHERE type = 'bug' GROUP BY day
                ) fb_bug ON fb_bug.day = d.day
                LEFT JOIN (
                    SELECT created_at::date AS day, COUNT(*) AS cnt
                    FROM feedbacks WHERE type = 'feature' GROUP BY day
                ) fb_feat ON fb_feat.day = d.day
                ORDER BY d.day
                """
            ),
            {"days": days},
        )
        trends = [
            {
                "day": str(r.day),
                "registrations": r.registrations,
                "sessions": r.sessions,
                "messages": r.messages,
                "feedback_bugs": r.feedback_bugs,
                "feedback_features": r.feedback_features,
            }
            for r in rows.fetchall()
        ]

        # ── top users by total turn_count ──
        top_users_rows = await db.execute(
            text(
                """
                SELECT u.id, u.username, u.display_name,
                       COALESCE(SUM(s.turn_count), 0)::int AS total_turns,
                       COUNT(s.id) AS session_count
                FROM users u
                LEFT JOIN sessions s ON s.user_id = u.id
                GROUP BY u.id
                ORDER BY total_turns DESC
                LIMIT 10
                """
            )
        )
        top_users = [
            {
                "id": str(r.id),
                "username": r.username,
                "display_name": r.display_name,
                "total_turns": r.total_turns,
                "session_count": r.session_count,
            }
            for r in top_users_rows.fetchall()
        ]

        # ── top spaces by session count ──
        top_spaces_rows = await db.execute(
            text(
                """
                SELECT sp.id, sp.name,
                       COUNT(s.id) AS session_count,
                       MAX(s.last_active_at) AS last_active
                FROM spaces sp
                LEFT JOIN sessions s ON s.space_id = sp.id
                GROUP BY sp.id
                ORDER BY session_count DESC
                LIMIT 10
                """
            )
        )
        top_spaces = [
            {
                "id": str(r.id),
                "name": r.name,
                "session_count": r.session_count,
                "last_active": r.last_active.isoformat() if r.last_active else None,
            }
            for r in top_spaces_rows.fetchall()
        ]

    return {
        "trends": trends,
        "top_users": top_users,
        "top_spaces": top_spaces,
    }


@router.get("/admin/analytics/spaces")
async def get_analytics_spaces(user=Depends(get_current_user)):
    """Per-space stats: members, admins, sessions, last activity."""

    async with async_session_factory() as db:
        rows = await db.execute(
            text(
                """
                SELECT
                    sp.id,
                    sp.name,
                    sp.created_at,
                    COUNT(DISTINCT sm.user_id) AS member_count,
                    COUNT(DISTINCT CASE WHEN sm.role = 'admin' THEN sm.user_id END) AS admin_count,
                    COUNT(DISTINCT s.id) AS session_count,
                    MAX(s.last_active_at) AS last_active
                FROM spaces sp
                LEFT JOIN space_members sm ON sm.space_id = sp.id
                LEFT JOIN sessions s ON s.space_id = sp.id
                GROUP BY sp.id
                ORDER BY session_count DESC
                """
            )
        )
        spaces = [
            {
                "id": str(r.id),
                "name": r.name,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "member_count": r.member_count,
                "admin_count": r.admin_count,
                "session_count": r.session_count,
                "last_active": r.last_active.isoformat() if r.last_active else None,
            }
            for r in rows.fetchall()
        ]

    return {"spaces": spaces}
