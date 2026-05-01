"""Cron job scheduler — polls DB for due jobs and schedules and executes via agent."""

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone

try:
    from croniter import croniter
    HAS_CRONITER = True
except ImportError:
    HAS_CRONITER = False

from sqlalchemy import select

from src.config import settings
from src.models.base import async_session_factory
from src.models.cron_job import CronJob
from src.models.schedule import Schedule, ScheduleExecution
from src.models.agent import Scenario

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "cron_output")
POLL_INTERVAL = 60


def _parse_duration(s: str) -> int:
    """Parse duration string into minutes. Supports 30m, 2h, 1d."""
    s = s.strip().lower()
    m = re.match(r'^(\d+)\s*(m|min|h|hr|d|day)$', s)
    if not m:
        raise ValueError(f"Invalid duration: {s}")
    value = int(m.group(1))
    unit = m.group(2)[0]
    return value * {"m": 1, "h": 60, "d": 1440}[unit]


def compute_next_run(schedule: str, last_run: datetime | None = None) -> datetime | None:
    """Compute the next run time for a schedule string."""
    now = datetime.now(timezone.utc)
    schedule = schedule.strip()

    if schedule.lower() == "once":
        return None if last_run else now

    try:
        minutes = _parse_duration(schedule)
        base = last_run or now
        return base + timedelta(minutes=minutes)
    except ValueError:
        pass

    if HAS_CRONITER and len(schedule.split()) >= 5:
        try:
            cron = croniter(schedule, now)
            return cron.get_next(datetime)
        except Exception:
            pass

    return None


def _build_job_prompt(job: CronJob) -> str:
    """Build the effective prompt for a cron job, including skills."""
    prompt = job.prompt or ""

    cron_hint = (
        "[SYSTEM: You are running as a scheduled cron job. "
        "Your final response will be saved as the job output. "
        "Do not attempt to send messages to users — just produce your report.]\n\n"
    )
    prompt = cron_hint + prompt

    skills = job.skills or []
    if skills:
        parts = []
        for skill_name in skills:
            parts.append(
                f'[SYSTEM: Loaded skill "{skill_name}". Follow its instructions.]'
            )
        prompt = "\n".join(parts) + "\n\n" + prompt

    return prompt


async def _execute_job(job: CronJob) -> None:
    """Execute a single cron job via the deep agent."""
    from src.agent.deep_agent import get_deep_agent

    logger.info("Running cron job '%s' (%s)", job.name, job.id)

    try:
        agent = await get_deep_agent()
        prompt = _build_job_prompt(job)
        result = await agent.ainvoke({"messages": [("user", prompt)]})

        messages = result.get("messages", [])
        output = ""
        for m in messages:
            if hasattr(m, "content") and isinstance(m.content, str):
                output += m.content + "\n"

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(OUTPUT_DIR, f"{job.id}_{timestamp}.md")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# {job.name}\n\n**Run:** {timestamp}\n\n{output}")

        logger.info("Cron job '%s' completed, output: %s", job.name, output_path)

        async with async_session_factory() as db:
            result_row = await db.get(CronJob, job.id)
            if result_row:
                result_row.last_output = output[:8192]
                result_row.last_run = datetime.now(timezone.utc)
                result_row.next_run = compute_next_run(result_row.schedule, result_row.last_run)
                if result_row.schedule.lower() == "once":
                    result_row.enabled = False
                await db.commit()

    except Exception:
        logger.exception("Cron job '%s' failed", job.name)
        async with async_session_factory() as db:
            result_row = await db.get(CronJob, job.id)
            if result_row:
                result_row.last_run = datetime.now(timezone.utc)
                result_row.next_run = compute_next_run(result_row.schedule, result_row.last_run)
                await db.commit()


async def _execute_schedule(sched: Schedule) -> None:
    """Execute a scheduled scenario run via the deep agent."""
    from src.agent.deep_agent import get_deep_agent

    logger.info("Running schedule '%s' (%s)", sched.name, sched.id)

    async with async_session_factory() as db:
        execution = ScheduleExecution(
            schedule_id=sched.id,
            status="running",
            result={},
        )
        db.add(execution)
        await db.commit()

    try:
        async with async_session_factory() as db:
            scenario = await db.get(Scenario, sched.scenario_id)
            scenario_name = scenario.name if scenario else "unknown"

        agent = await get_deep_agent()
        prompt = (
            f"[SYSTEM: You are running as a scheduled automation. "
            f"Your final response will be saved as the execution result.]\n\n"
            f"Schedule: {sched.name}\n"
            f"Scenario: {scenario_name}\n"
            f"Params: {sched.params}\n\n"
            f"Execute the scenario and report results."
        )

        result = await agent.ainvoke({"messages": [("user", prompt)]})
        messages = result.get("messages", [])
        output = ""
        for m in messages:
            if hasattr(m, "content") and isinstance(m.content, str):
                output += m.content + "\n"

        async with async_session_factory() as db:
            exec_row = await db.get(ScheduleExecution, execution.id)
            if exec_row:
                exec_row.status = "success"
                exec_row.result = {"output": output[:8192]}
                await db.commit()

        logger.info("Schedule '%s' completed", sched.name)

    except Exception:
        logger.exception("Schedule '%s' failed", sched.name)
        async with async_session_factory() as db:
            exec_row = await db.get(ScheduleExecution, execution.id)
            if exec_row:
                exec_row.status = "failed"
                exec_row.result = {"error": str(Exception)}
                await db.commit()

    finally:
        now = datetime.now(timezone.utc)
        async with async_session_factory() as db:
            sched_row = await db.get(Schedule, sched.id)
            if sched_row:
                sched_row.next_run = compute_next_run(sched_row.cron_expression, now)
                await db.commit()


class CronScheduler:
    """Background scheduler that polls for due cron jobs and schedules every 60s."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("Cron scheduler started")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("Cron scheduler stopped")

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception:
                logger.exception("Cron scheduler tick error")
            await asyncio.sleep(POLL_INTERVAL)

    async def _tick(self) -> int:
        now = datetime.now(timezone.utc)
        total = 0

        # Cron jobs
        async with async_session_factory() as db:
            job_result = await db.execute(
                select(CronJob).where(
                    CronJob.enabled == True,
                    CronJob.next_run <= now,
                )
            )
            due_jobs = job_result.scalars().all()

        if due_jobs:
            logger.info("Cron tick: %d job(s) due", len(due_jobs))
            for job in due_jobs:
                await _execute_job(job)
            total += len(due_jobs)

        # Schedules
        async with async_session_factory() as db:
            sched_result = await db.execute(
                select(Schedule).where(
                    Schedule.is_active == True,
                    Schedule.next_run <= now,
                )
            )
            due_schedules = sched_result.scalars().all()

        if due_schedules:
            logger.info("Cron tick: %d schedule(s) due", len(due_schedules))
            for sched in due_schedules:
                await _execute_schedule(sched)
            total += len(due_schedules)

        return total


cron_scheduler = CronScheduler()
