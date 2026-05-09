"""AnalyticsReportAgent — generates operational analytics reports with LLM analysis."""

from __future__ import annotations

import logging
from datetime import date

from src.agent.sub_agents.base import BaseSubAgent
from src.models.base import async_session_factory

logger = logging.getLogger(__name__)

_ANALYTICS_QUERY = """
WITH d AS (
    SELECT generate_series(
        (:start_date)::date,
        (:end_date)::date,
        '1 day'::interval
    )::date AS day
)
SELECT d.day, COALESCE(u.cnt,0) AS registrations,
       COALESCE(s.cnt,0) AS sessions,
       COALESCE(m.cnt,0) AS messages,
       COALESCE(fb_bug.cnt,0) AS bugs,
       COALESCE(fb_feat.cnt,0) AS features
FROM d
LEFT JOIN (
    SELECT created_at::date AS day, COUNT(*) AS cnt FROM users GROUP BY day
) u ON u.day = d.day
LEFT JOIN (
    SELECT created_at::date AS day, COUNT(*) AS cnt FROM sessions GROUP BY day
) s ON s.day = d.day
LEFT JOIN (
    SELECT created_at::date AS day, COUNT(*) AS cnt FROM messages GROUP BY day
) m ON m.day = d.day
LEFT JOIN (
    SELECT created_at::date AS day, COUNT(*) AS cnt
    FROM feedbacks WHERE type='bug' GROUP BY day
) fb_bug ON fb_bug.day = d.day
LEFT JOIN (
    SELECT created_at::date AS day, COUNT(*) AS cnt
    FROM feedbacks WHERE type='feature' GROUP BY day
) fb_feat ON fb_feat.day = d.day
ORDER BY d.day
"""

_SYSTEM_PROMPT = """\
你是一位资深的平台运营分析师。请基于提供的运营数据，撰写一份专业的分析报告的**正文内容**。

要求：
1. 使用中文，语气专业但易读
2. 不要重复原始数据，而要给出洞察和解读
3. 使用以下 ## 标题标记每个部分：
   ## 执行摘要
   ## 核心指标
   ## 趋势洞察
   ## 用户分析
   ## 空间分析
   ## 反馈分析
   ## 运营建议
4. 每个部分 3-6 句话，要点用 - 列表
5. 运营建议给 3-5 条具体可落地的建议，用 1. 2. 3. 编号
6. 如果数据量较小，给出早期阶段的增长建议
7. 如果反馈中有较多未关闭 Bug，提出改进建议
只输出报告内容，不要有任何前缀或后缀说明。"""


class AnalyticsReportAgent(BaseSubAgent):
    name = "analytics_report_agent"
    description = "Generates operational analytics reports with data-driven LLM analysis"
    system_prompt = _SYSTEM_PROMPT

    def __init__(self, model=None):
        super().__init__(model=model)

    async def __call__(self, task: str, context: dict | None = None) -> str:
        llm = await self._get_llm()
        msgs = self._build_messages(task, context)
        resp = await llm.ainvoke(msgs)
        return resp.content if hasattr(resp, "content") else str(resp)

    async def generate(
        self, start_date: str, end_date: str, feedback: str | None = None
    ) -> tuple[str, dict]:
        """Generate a full analysis for the given date range.

        Returns (analysis_markdown, data_summary_dict).
        """
        data = await self._fetch_data(start_date, end_date)
        data_text = self._format_data(data, start_date, end_date)

        if feedback:
            task = (
                f"以下是上一版分析报告的反馈意见：\n{feedback}\n\n"
                f"请根据反馈重新分析以下数据并生成改进后的报告：\n\n{data_text}"
            )
        else:
            task = f"请分析以下平台运营数据并生成报告:\n\n{data_text}"

        analysis = await self(task)
        return analysis, data

    async def _fetch_data(self, start_date: str, end_date: str) -> dict:
        from sqlalchemy import text

        start_dt = date.fromisoformat(start_date)
        end_dt = date.fromisoformat(end_date)

        async with async_session_factory() as db:
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

            sess_total = await db.scalar(text("SELECT COUNT(*) FROM sessions"))
            sess_active = await db.scalar(
                text("SELECT COUNT(*) FROM sessions WHERE status = 'active'")
            )
            sess_today = await db.scalar(
                text("SELECT COUNT(*) FROM sessions WHERE created_at >= CURRENT_DATE")
            )
            msg_total = await db.scalar(text("SELECT COUNT(*) FROM messages"))
            space_total = await db.scalar(text("SELECT COUNT(*) FROM spaces"))
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

            trend_rows = await db.execute(
                text(_ANALYTICS_QUERY), {"start_date": start_dt, "end_date": end_dt}
            )
            trends = [
                {
                    "day": str(r.day),
                    "registrations": r.registrations,
                    "sessions": r.sessions,
                    "messages": r.messages,
                    "feedback_bugs": r.bugs,
                    "feedback_features": r.features,
                }
                for r in trend_rows.fetchall()
            ]

            top_u = await db.execute(text("""
                SELECT u.username, u.display_name,
                       COALESCE(SUM(s.turn_count),0)::int AS tt,
                       COUNT(s.id) AS sc
                FROM users u
                LEFT JOIN sessions s ON s.user_id = u.id
                GROUP BY u.id ORDER BY tt DESC LIMIT 5
            """))
            top_users = [
                {
                    "name": r.display_name or r.username,
                    "total_turns": r.tt,
                    "session_count": r.sc,
                }
                for r in top_u.fetchall()
            ]

            top_sp = await db.execute(text("""
                SELECT sp.name, COUNT(s.id) AS sc
                FROM spaces sp
                LEFT JOIN sessions s ON s.space_id = sp.id
                GROUP BY sp.id ORDER BY sc DESC LIMIT 5
            """))
            top_spaces = [
                {"name": r.name, "session_count": r.sc}
                for r in top_sp.fetchall()
            ]

            sp_rows = await db.execute(text("""
                SELECT sp.name, sp.created_at,
                       COUNT(DISTINCT sm.user_id) AS mc,
                       COUNT(DISTINCT CASE WHEN sm.role='admin' THEN sm.user_id END) AS ac,
                       COUNT(DISTINCT s.id) AS sc
                FROM spaces sp
                LEFT JOIN space_members sm ON sm.space_id = sp.id
                LEFT JOIN sessions s ON s.space_id = sp.id
                GROUP BY sp.id ORDER BY sc DESC
            """))
            spaces = [
                {
                    "name": r.name,
                    "created_at": r.created_at.isoformat()[:10] if r.created_at else None,
                    "member_count": r.mc,
                    "admin_count": r.ac,
                    "session_count": r.sc,
                }
                for r in sp_rows.fetchall()[:15]
            ]

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
            "trends": trends,
            "top_users": top_users,
            "top_spaces": top_spaces,
            "space_details": spaces,
        }

    @staticmethod
    def _format_data(data: dict, start_date: str, end_date: str) -> str:
        u = data["users"]
        s = data["sessions"]
        fb = data["feedback"]
        trends = data["trends"]
        total_reg = sum(t["registrations"] for t in trends)
        total_sess = sum(t["sessions"] for t in trends)
        total_msg = sum(t["messages"] for t in trends)
        days = len(trends)
        active_pct = (
            f"{u['active'] / max(u['total'], 1) * 100:.0f}%"
        )

        top_users = "\n".join(
            f"- {u['name']}: {u['total_turns']} 轮次, {u['session_count']} 会话"
            for u in data["top_users"]
        )
        top_spaces = "\n".join(
            f"- {s['name']}: {s['session_count']} 会话"
            for s in data["top_spaces"]
        )
        space_details = "\n".join(
            f"- {s['name']}: {s['member_count']} 成员 ({s['admin_count']} 管理员), "
            f"{s['session_count']} 会话"
            for s in data["space_details"]
        )

        if trends:
            peak_msg = max(trends, key=lambda t: t["messages"])
            peak_sess = max(trends, key=lambda t: t["sessions"])
            peaks = (
                f"- 消息峰值日: {peak_msg['day']}（{peak_msg['messages']} 条）\n"
                f"- 会话峰值日: {peak_sess['day']}（{peak_sess['sessions']} 个）\n"
            )
        else:
            peaks = ""

        return f"""AIOpsOS 平台运营数据（{start_date} ~ {end_date}，共 {days} 天）:

## 用户数据
- 总用户: {u['total']}，活跃: {u['active']}（{active_pct}），
  待激活: {u['pending']}，邀请注册: {u['invited']}

## 会话数据
- 总会话: {s['total']}，活跃会话: {s['active']}，今日会话: {s['today']}

## 消息与空间
- 总消息: {data['messages']['total']}
- 总空间: {data['spaces']['total']}

## 反馈数据
- Bug 总数: {fb['bugs']}，未关闭 Bug: {fb['open_bugs']}，Feature 请求: {fb['features']}

## {days} 天趋势汇总
- 新增注册: {total_reg}，新会话: {total_sess}，消息: {total_msg}
{peaks}
## 最活跃用户 Top 5
{top_users}

## 最活跃空间 Top 5
{top_spaces}

## 空间详情 ({len(data['space_details'])} 个，仅列出前15)
{space_details}
"""
