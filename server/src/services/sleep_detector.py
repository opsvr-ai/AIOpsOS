"""SleepDetector — background service that monitors session activity and
triggers memory consolidation and skill review for idle sessions.

Polls every 60 seconds:
  1. Finds sessions where sleep_status='awake' AND last_active_at < now() - 5min
  2. Marks them as sleep_status='sleeping'
  3. If auto_consolidate=true AND memory_status='unconsolidated', triggers consolidation
  4. If skill_review_due=true, spawns SkillReviewAgent for background skill extraction
"""

from __future__ import annotations

import asyncio as _asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from src.models.base import async_session_factory
from src.models.session import Session

logger = logging.getLogger(__name__)

IDLE_THRESHOLD_MINUTES: int = 5
POLL_INTERVAL_SECONDS: int = 60
REVIEW_INTERVAL_TURNS: int = 15


async def _run_skill_review(agent, session_id: str) -> None:
    """Fire-and-forget wrapper for skill review with error handling."""
    try:
        result = await agent.review(session_id)
        logger.info("Skill review complete for session %s: %s", session_id, result)
    except Exception:
        logger.exception("Skill review failed for session %s", session_id)


class SleepDetector:
    """Background sleep detection and memory consolidation orchestrator."""

    def __init__(self) -> None:
        self._running: bool = False
        self._task: _asyncio.Task | None = None
        self._tick_count: int = 0

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Start the background detection loop."""
        if self._running:
            return
        self._running = True
        self._task = _asyncio.create_task(self._loop())
        logger.info("SleepDetector started (poll=%ds, idle_threshold=%dmin)",
                     POLL_INTERVAL_SECONDS, IDLE_THRESHOLD_MINUTES)

    async def stop(self) -> None:
        """Stop the background detection loop gracefully."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except _asyncio.CancelledError:
                pass
        logger.info("SleepDetector stopped")

    async def _loop(self) -> None:
        """Main detection loop."""
        while self._running:
            try:
                await self._tick()
            except Exception:
                logger.exception("SleepDetector tick failed")
            await _asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _tick(self) -> None:
        """Single detection tick: find idle sessions and process them."""
        self._tick_count += 1
        if self._tick_count % 10 == 0:
            logger.info("SleepDetector heartbeat #%d", self._tick_count)

        threshold = datetime.now(UTC) - timedelta(minutes=IDLE_THRESHOLD_MINUTES)

        async with async_session_factory() as db:
            result = await db.execute(
                select(Session.id, Session.user_id,
                       Session.auto_consolidate, Session.memory_status)
                .where(
                    Session.last_active_at < threshold,
                    (Session.sleep_status == "awake") | (Session.sleep_status.is_(None)),
                )
                .limit(20)
            )
            idle_sessions = list(result.fetchall())

        # Skill review runs every tick, independent of idle detection
        await self._maybe_skill_review()

        if not idle_sessions:
            return

        logger.info("SleepDetector: %d idle session(s) detected", len(idle_sessions))

        for row in idle_sessions:
            sid = str(row.id)
            uid = str(row.user_id)
            await self._put_to_sleep(sid)
            await self._maybe_consolidate(sid, uid, row.auto_consolidate, row.memory_status)

    @staticmethod
    async def _maybe_skill_review() -> None:
        """Check for sessions flagged for skill review and spawn background reviews."""
        async with async_session_factory() as db:
            result = await db.execute(
                select(Session.id, Session.user_id)
                .where(Session.skill_review_due == True)
                .limit(5)
            )
            due_sessions = list(result.fetchall())

        if not due_sessions:
            return

        logger.info("SleepDetector: %d session(s) due for skill review", len(due_sessions))

        for row in due_sessions:
            sid = str(row.id)
            try:
                from src.agent.sub_agents.skill_review_agent import SkillReviewAgent

                agent = SkillReviewAgent()
                # Fire-and-forget: review runs in background, resets flag on completion
                _asyncio.create_task(_run_skill_review(agent, sid))
            except Exception:
                logger.exception("Failed to spawn SkillReviewAgent for session %s", sid)

    @staticmethod
    async def _put_to_sleep(session_id: str) -> None:
        """Mark a session as sleeping."""
        async with async_session_factory() as db:
            await db.execute(
                update(Session)
                .where(Session.id == session_id)
                .values(sleep_status="sleeping")
            )
            await db.commit()

    @staticmethod
    async def _maybe_consolidate(session_id: str, user_id: str,
                                 auto_consolidate: bool = True,
                                 memory_status: str = "unconsolidated") -> None:
        """Trigger memory consolidation if the session is eligible."""
        if not auto_consolidate or memory_status == "consolidated":
            logger.debug("Session %s: skip consolidation (auto=%s, status=%s)",
                         session_id, auto_consolidate, memory_status)
            return

        try:
            from src.agent.sub_agents.memory_consolidation_agent import (
                MemoryConsolidationAgent,
            )
            agent = MemoryConsolidationAgent()
            result = await agent.consolidate(session_id, user_id)
            logger.info("Session %s auto-consolidated: %s", session_id, result)
        except Exception:
            logger.exception("Auto-consolidation failed for session %s", session_id)

    @staticmethod
    async def wake_session(session_id: str) -> None:
        """Wake a sleeping session and update its last_active_at timestamp."""
        async with async_session_factory() as db:
            await db.execute(
                update(Session)
                .where(Session.id == session_id)
                .values(
                    sleep_status="awake",
                    last_active_at=datetime.now(UTC),
                )
            )
            await db.commit()
        logger.debug("Session %s awakened", session_id)


sleep_detector = SleepDetector()
