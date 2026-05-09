"""SleepScheduler — Redis-ZSET driven consolidation dispatcher.

Spec: .kiro/specs/agent-runtime-optimization-evolution, tasks 11.1–11.3 /
R-2.10 / R-2.11 / P-Sleep-1 / P-Sleep-2.

The sole consolidation dispatcher (the legacy polling
``sleep_detector`` was removed in task 25.1). Design goals:

* **Non-blocking dispatch** — tick every 5 seconds, pull due sessions
  from ``sleep:queue`` (ZSET scored by "when consolidation should start"),
  fire Celery tasks via ``.delay``, release the ZSET slot. No LLM calls
  inside the scheduler.
* **Global concurrency cap** — at most ``max_concurrent_consolidations``
  (default 4) inflight at once, tracked via a Redis counter
  ``sleep:inflight`` that each dispatch increments. The ConsolidationWorker
  decrements on completion — but because the scheduler is best-effort we
  also TTL-expire stale slots.
* **Per-session throttle** — ``sleep:throttle:{sid}`` key lives for
  30 minutes after a dispatch so rapid turn bursts on the same session
  don't re-dispatch before the first one even finishes.
* **Daily token budget per space** — Redis INCRBY on
  ``sleep:budget:{space}:{YYYY-MM-DD}`` with ``EXPIREAT`` end-of-day; the
  per-dispatch cost is a fixed 200-token estimate (matches the compressed
  DIFF_EXTRACTION_PROMPT). Over-budget spaces get skipped for the rest of
  the day.
* **Backpressure degradation** — when ``ZCARD sleep:queue > 500``, flip
  the scheduler into degraded mode and pass ``degraded=True`` down to
  the Celery task; the ConsolidationWorker skips the embedding step and
  bumps ``consolidation_degraded_total``.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.metrics import sleep_queue_depth

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

QUEUE_KEY = "sleep:queue"
INFLIGHT_KEY = "sleep:inflight"
THROTTLE_PREFIX = "sleep:throttle"
BUDGET_PREFIX = "sleep:budget"

DEFAULT_TICK_SECONDS = 5.0
DEFAULT_MAX_CONCURRENT = 4
DEFAULT_BATCH_LIMIT = 20
THROTTLE_TTL_SECONDS = 1800
BACKPRESSURE_THRESHOLD = 500
APPROX_TOKEN_COST_PER_DISPATCH = 200
DEFAULT_DAILY_TOKEN_BUDGET = 200_000


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class SleepScheduler:
    """Asyncio-loop scheduler that drains ``sleep:queue`` into Celery."""

    def __init__(
        self,
        *,
        redis_client: Any | None = None,
        task_sender: Any | None = None,
        flag_service: Any | None = None,
        tick_seconds: float = DEFAULT_TICK_SECONDS,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        batch_limit: int = DEFAULT_BATCH_LIMIT,
    ) -> None:
        self._redis_client = redis_client
        self._task_sender = task_sender  # callable(session_id, *, degraded) -> task_id
        self._flag_service = flag_service
        self._tick_seconds = float(tick_seconds)
        self._max_concurrent = int(max_concurrent)
        self._batch_limit = int(batch_limit)
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._degraded_mode: bool = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def degraded_mode(self) -> bool:
        return self._degraded_mode

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="sleep-scheduler")
        logger.info(
            "SleepScheduler started (tick=%ss max_concurrent=%s)",
            self._tick_seconds,
            self._max_concurrent,
        )

    async def stop(self) -> None:
        self._stop_event.set()
        task, self._task = self._task, None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
                try:
                    await task
                except Exception:
                    pass
        logger.info("SleepScheduler stopped")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.tick()
            except Exception:
                logger.exception("SleepScheduler tick failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._tick_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def tick(self) -> dict[str, int]:
        """Execute one scheduling tick.

        Returns a summary dict ``{dispatched, skipped_budget, skipped_throttled,
        skipped_inflight}`` for observability / tests.
        """
        redis = await self._resolve_redis()
        now_ts = datetime.now(UTC).timestamp()

        # Update degraded mode + gauge first so even "empty tick" keeps the
        # metric fresh.
        depth = int(await redis.zcard(QUEUE_KEY) or 0)
        try:
            sleep_queue_depth.set(depth)
        except Exception:
            logger.debug("sleep_queue_depth gauge update failed", exc_info=True)
        self._degraded_mode = depth > BACKPRESSURE_THRESHOLD

        due = await redis.zrangebyscore(
            QUEUE_KEY, 0, now_ts, start=0, num=self._batch_limit
        )
        if not due:
            return {
                "dispatched": 0,
                "skipped_budget": 0,
                "skipped_throttled": 0,
                "skipped_inflight": 0,
            }

        dispatched = 0
        skipped_budget = 0
        skipped_throttled = 0
        skipped_inflight = 0

        for raw_sid in due:
            sid = (
                raw_sid.decode() if isinstance(raw_sid, (bytes, bytearray)) else str(raw_sid)
            )

            # 1) global inflight cap
            current_inflight = int(await redis.get(INFLIGHT_KEY) or 0)
            if current_inflight >= self._max_concurrent:
                skipped_inflight += 1
                # Leave the session in the queue so it'll be picked up on
                # the next tick once capacity frees up.
                continue

            # 2) per-session throttle
            if await redis.exists(f"{THROTTLE_PREFIX}:{sid}"):
                # Remove from queue so we don't re-scan it every tick; the
                # ConsolidationWorker will re-add via sync_turn when a new
                # turn arrives after the throttle window.
                await redis.zrem(QUEUE_KEY, sid)
                skipped_throttled += 1
                continue

            # 3) daily token budget (per space). We don't know the space_id
            # without a DB lookup, so the budget is charged to a single
            # "global" key unless a future refactor passes space_id in the
            # ZSET member encoding.
            space_key = "global"
            daily_budget = await self._daily_budget()
            date_str = datetime.now(UTC).strftime("%Y-%m-%d")
            budget_key = f"{BUDGET_PREFIX}:{space_key}:{date_str}"
            spent = int(await redis.get(budget_key) or 0)
            if spent + APPROX_TOKEN_COST_PER_DISPATCH > daily_budget:
                # Budget blown: drop from queue, log.
                await redis.zrem(QUEUE_KEY, sid)
                skipped_budget += 1
                logger.info(
                    "SleepScheduler: daily budget exhausted (%s/%s), session %s skipped",
                    spent,
                    daily_budget,
                    sid,
                )
                continue

            # 4) dispatch
            try:
                await self._dispatch(sid)
            except Exception:
                logger.exception(
                    "SleepScheduler: dispatch failed for session %s", sid
                )
                continue

            # 5) side-effects after successful dispatch
            try:
                pipe = redis.pipeline()
                pipe.zrem(QUEUE_KEY, sid)
                pipe.incr(INFLIGHT_KEY)
                pipe.expire(INFLIGHT_KEY, 900)
                pipe.setex(
                    f"{THROTTLE_PREFIX}:{sid}", THROTTLE_TTL_SECONDS, "1"
                )
                pipe.incrby(budget_key, APPROX_TOKEN_COST_PER_DISPATCH)
                # End-of-day expireat for the budget key. ``expireat`` is
                # idempotent so setting it every dispatch is fine.
                end_of_day = (
                    datetime.now(UTC).replace(hour=23, minute=59, second=59)
                )
                pipe.expireat(budget_key, int(end_of_day.timestamp()))
                await pipe.execute()
            except Exception:
                logger.exception(
                    "SleepScheduler: post-dispatch bookkeeping failed for %s", sid
                )
            dispatched += 1

        # Refresh the gauge to reflect post-drain depth.
        try:
            sleep_queue_depth.set(int(await redis.zcard(QUEUE_KEY) or 0))
        except Exception:
            logger.debug("sleep_queue_depth gauge update failed", exc_info=True)

        return {
            "dispatched": dispatched,
            "skipped_budget": skipped_budget,
            "skipped_throttled": skipped_throttled,
            "skipped_inflight": skipped_inflight,
        }

    async def _dispatch(self, session_id: str) -> None:
        sender = self._task_sender or _default_task_sender
        # ``sender`` may be sync (Celery ``.delay``) or async; support both.
        result = sender(session_id, degraded=self._degraded_mode)
        if asyncio.iscoroutine(result):
            await result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _resolve_redis(self) -> Any:
        if self._redis_client is not None:
            return self._redis_client
        from src.core.redis import get_redis

        return await get_redis()

    async def _daily_budget(self) -> int:
        if self._flag_service is not None:
            svc = self._flag_service
        else:
            try:
                from src.services.feature_flags import get_feature_flags

                svc = await get_feature_flags()
            except Exception:
                return DEFAULT_DAILY_TOKEN_BUDGET

        try:
            snap = svc.get("consolidation_worker_enabled")
        except Exception:
            return DEFAULT_DAILY_TOKEN_BUDGET
        if snap is None:
            return DEFAULT_DAILY_TOKEN_BUDGET
        data = getattr(snap, "data", None) or {}
        cons_cfg = data.get("consolidation") or {}
        budget = cons_cfg.get("daily_token_budget_per_space")
        try:
            return int(budget) if budget is not None else DEFAULT_DAILY_TOKEN_BUDGET
        except (TypeError, ValueError):
            return DEFAULT_DAILY_TOKEN_BUDGET


# ---------------------------------------------------------------------------
# Default task sender — uses Celery .delay()
# ---------------------------------------------------------------------------


def _default_task_sender(session_id: str, *, degraded: bool) -> Any:
    """Lazy import so the module is usable without celery installed."""
    from src.workers.tasks.memory_consolidation import run_consolidation

    return run_consolidation.delay(session_id, degraded=degraded)


# ---------------------------------------------------------------------------
# Singleton accessor + lifecycle hooks
# ---------------------------------------------------------------------------


_INSTANCE: SleepScheduler | None = None


def get_sleep_scheduler() -> SleepScheduler:
    """Return (creating if needed) the process-wide scheduler singleton."""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = SleepScheduler()
    return _INSTANCE


async def start_sleep_scheduler() -> SleepScheduler | None:
    svc = get_sleep_scheduler()
    await svc.start()
    return svc


async def stop_sleep_scheduler() -> None:
    global _INSTANCE
    svc, _INSTANCE = _INSTANCE, None
    if svc is not None:
        await svc.stop()


def _reset_singleton_for_tests() -> None:
    global _INSTANCE
    _INSTANCE = None


__all__ = [
    "SleepScheduler",
    "get_sleep_scheduler",
    "start_sleep_scheduler",
    "stop_sleep_scheduler",
    "QUEUE_KEY",
    "INFLIGHT_KEY",
    "THROTTLE_PREFIX",
    "BUDGET_PREFIX",
    "BACKPRESSURE_THRESHOLD",
    "APPROX_TOKEN_COST_PER_DISPATCH",
    "DEFAULT_DAILY_TOKEN_BUDGET",
]
