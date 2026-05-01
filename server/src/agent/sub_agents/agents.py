"""Sub-agent implementations: monitor, ops, analysis — with real DB data access."""

from typing import Any

from sqlalchemy import select, func
from sqlalchemy.sql import text

from src.agent.sub_agents.base import BaseSubAgent
from src.models.alert import Alert
from src.models.base import async_session_factory
from src.models.schedule import Schedule


class MonitorAgent(BaseSubAgent):
    """Analyzes alerts, triages incidents, and summarizes system health."""

    name = "monitor"
    description = "Analyze alerts, triage incidents, and summarize system health"
    system_prompt = (
        "You are the vigilant sentinel of the digital realm — the first to hear the "
        "whisper of trouble before it becomes a roar.\n"
        "Like a seasoned lighthouse keeper reading the sea, you scan the horizon of "
        "alerts, trace patterns in the blink of warning lights, and separate the "
        "signal from the noise with calm, practiced eyes.\n\n"
        "Your role:\n"
        "1. Analyze alert patterns and identify root causes\n"
        "2. Triage incidents by severity and impact\n"
        "3. Summarize system health status\n"
        "4. Recommend remediation steps\n\n"
        "Be concise and data-driven. Always cite specific alert details when available."
    )

    async def _fetch_real_data(self, task: str) -> str:
        """Fetch real-time alert data from the database."""
        parts: list[str] = []
        async with async_session_factory() as db:
            count = await db.scalar(
                select(func.count(Alert.id)).where(Alert.status == "active")
            )
            parts.append(f"Active alerts: {count or 0}")

            sev_result = await db.execute(
                select(Alert.severity, func.count(Alert.id).label("cnt"))
                .where(Alert.status == "active")
                .group_by(Alert.severity)
                .order_by(text("cnt DESC"))
            )
            for row in sev_result:
                parts.append(f"  {row.severity}: {row.cnt}")

            recent = await db.execute(
                select(Alert)
                .where(Alert.status == "active")
                .order_by(Alert.created_at.desc())
                .limit(10)
            )
            for r in recent.scalars():
                parts.append(f"  [{r.created_at}] {r.title} ({r.severity})")

        return "\n".join(parts)

    async def __call__(self, task: str, context: dict[str, Any] | None = None) -> str:
        real_data = await self._fetch_real_data(task)
        enriched_context = dict(context or {})
        enriched_context["real-time data"] = real_data
        msgs = self._build_messages(task, enriched_context)
        llm = await self._get_llm()
        resp = await llm.ainvoke(msgs)
        return str(resp.content)


class OpsAgent(BaseSubAgent):
    """Handles operational tasks: config checks, deployment status, and runbook queries."""

    name = "ops"
    description = "Execute operational tasks: config checks, deployments, runbooks"
    system_prompt = (
        "You are the steady hand on the tiller of infrastructure — the operator who "
        "turns intention into action with the precision of a master craftsman.\n"
        "Like a pilot in the cockpit scanning every gauge and switch, you navigate "
        "through configuration, deployment, and maintenance with methodical grace.\n\n"
        "Your role:\n"
        "1. Check configuration status and consistency\n"
        "2. Query deployment status and pipeline health\n"
        "3. Execute runbook procedures\n"
        "4. Validate system parameters\n\n"
        "Provide step-by-step results. If a step fails, explain why and suggest fixes."
    )

    async def _fetch_real_data(self, task: str) -> str:
        """Fetch schedule/task data and system info."""
        parts: list[str] = []
        async with async_session_factory() as db:
            count = await db.scalar(select(func.count(Schedule.id)))
            parts.append(f"Scheduled tasks: {count or 0}")

            active = await db.scalar(
                select(func.count(Schedule.id)).where(Schedule.is_active == True)
            )
            parts.append(f"  Active: {active or 0}")

            recent = await db.execute(
                select(Schedule).order_by(Schedule.created_at.desc()).limit(5)
            )
            for r in recent.scalars():
                parts.append(f"  [{r.cron_expr}] {r.name} (active={r.is_active})")

        return "\n".join(parts)

    async def __call__(self, task: str, context: dict[str, Any] | None = None) -> str:
        real_data = await self._fetch_real_data(task)
        enriched_context = dict(context or {})
        enriched_context["real-time data"] = real_data
        msgs = self._build_messages(task, enriched_context)
        llm = await self._get_llm()
        resp = await llm.ainvoke(msgs)
        return str(resp.content)


class AnalysisAgent(BaseSubAgent):
    """Data analysis and reasoning specialist."""

    name = "analysis"
    description = "Perform data analysis, trend identification, and reporting"
    system_prompt = (
        "You are the astronomer of the system — the mind that finds constellations "
        "in the scattered stars of data.\n"
        "Like a detective who reads the silent language of numbers and trends, you "
        "trace correlations across sources, uncover the hidden story within metrics, "
        "and illuminate the path forward with evidence as your compass.\n\n"
        "Your role:\n"
        "1. Analyze metrics and identify trends\n"
        "2. Correlate events across different data sources\n"
        "3. Generate summary reports\n"
        "4. Provide data-driven recommendations\n\n"
        "Use quantitative evidence when possible. Structure your output clearly."
    )

    async def _fetch_real_data(self, task: str) -> str:
        """Gather cross-source data for analysis."""
        parts: list[str] = []
        async with async_session_factory() as db:
            total_alerts = await db.scalar(select(func.count(Alert.id)))
            parts.append(f"Total alerts: {total_alerts or 0}")

            by_severity = await db.execute(
                select(Alert.severity, func.count(Alert.id).label("cnt"))
                .group_by(Alert.severity)
                .order_by(text("cnt DESC"))
            )
            parts.append("Alert breakdown:")
            for row in by_severity:
                parts.append(f"  {row.severity}: {row.cnt}")

            by_status = await db.execute(
                select(Alert.status, func.count(Alert.id).label("cnt"))
                .group_by(Alert.status)
                .order_by(text("cnt DESC"))
            )
            parts.append("By status:")
            for row in by_status:
                parts.append(f"  {row.status}: {row.cnt}")

        return "\n".join(parts)

    async def __call__(self, task: str, context: dict[str, Any] | None = None) -> str:
        real_data = await self._fetch_real_data(task)
        enriched_context = dict(context or {})
        enriched_context["real-time data"] = real_data
        msgs = self._build_messages(task, enriched_context)
        llm = await self._get_llm()
        resp = await llm.ainvoke(msgs)
        return str(resp.content)
