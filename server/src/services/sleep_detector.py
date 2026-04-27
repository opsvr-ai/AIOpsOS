"""SleepDetector — background service that monitors session activity and
triggers memory consolidation for idle sessions.

Polls every 60 seconds:
  1. Finds sessions where sleep_status='awake' AND last_active_at < now() - 5min
  2. Marks them as sleep_status='sleeping'
  3. If auto_consolidate=true AND memory_status='unconsolidated', triggers consolidation

Also provides wake_session() for the chat endpoint to reactivate sleeping sessions.
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


class SleepDetector:
    """Background sleep detection and memory consolidation orchestrator."""

    def __init__(self) -> None:
        self._running: bool = False
        self._task: _asyncio.Task | None = None

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
        threshold = datetime.now(UTC) - timedelta(minutes=IDLE_THRESHOLD_MINUTES)

        async with async_session_factory() as db:
            result = await db.execute(
                select(Session.id, Session.user_id)
                .where(
                    Session.sleep_status == "awake",
                    Session.last_active_at < threshold,
                )
                .limit(20)
            )
            idle_sessions = list(result.fetchall())

        if not idle_sessions:
            return

        logger.info("SleepDetector: %d idle session(s) detected", len(idle_sessions))

        for row in idle_sessions:
            sid = str(row.id)
            uid = str(row.user_id)
            await self._put_to_sleep(sid)
            await self._maybe_consolidate(sid, uid)

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
    async def _maybe_consolidate(session_id: str, user_id: str) -> None:
        """Check if auto-consolidation should run and trigger it."""
        async with async_session_factory() as db:
            result = await db.execute(
                select(Session.auto_consolidate, Session.memory_status)
                .where(Session.id == session_id)
            )
            row = result.one_or_none()
            if not row:
                return

            auto_consolidate, memory_status = row

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
