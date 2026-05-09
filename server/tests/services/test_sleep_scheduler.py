"""Unit tests for :class:`SleepScheduler`.

Spec: .kiro/specs/agent-runtime-optimization-evolution, tasks 11.1–11.2 /
11.5 / R-2.10 / R-2.11 / P-Sleep-2.

All tests use ``fakeredis`` + a fake Celery ``.delay`` sender so nothing
touches a real broker.
"""
from __future__ import annotations

from datetime import UTC, datetime

import fakeredis.aioredis
import pytest

from src.services.sleep_scheduler import (
    BACKPRESSURE_THRESHOLD,
    BUDGET_PREFIX,
    INFLIGHT_KEY,
    QUEUE_KEY,
    SleepScheduler,
    THROTTLE_PREFIX,
)


# ---------------------------------------------------------------------------
# Fake senders / flag service
# ---------------------------------------------------------------------------


class _FakeSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, session_id: str, *, degraded: bool):
        self.calls.append((session_id, {"degraded": degraded}))
        return {"task_id": f"task-{len(self.calls)}"}


class _FakeFlagSvc:
    def __init__(self, *, daily_budget: int | None = None) -> None:
        self._daily_budget = daily_budget

    def get(self, key: str):
        if key != "consolidation_worker_enabled" or self._daily_budget is None:
            return None

        class _Snap:
            data = {"consolidation": {"daily_token_budget_per_space": self._daily_budget}}

        snap = _Snap()
        snap.data = {"consolidation": {"daily_token_budget_per_space": self._daily_budget}}
        return snap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _enqueue(redis, session_id: str, *, score: float | None = None) -> None:
    score = score if score is not None else (datetime.now(UTC).timestamp() - 1)
    await redis.zadd(QUEUE_KEY, {session_id: score})


# ---------------------------------------------------------------------------
# Task 11.1 — basic tick dispatches to Celery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_dispatches_due_sessions() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sender = _FakeSender()
    svc = SleepScheduler(redis_client=redis, task_sender=sender)

    await _enqueue(redis, "sess-1")
    await _enqueue(redis, "sess-2")

    summary = await svc.tick()

    assert summary["dispatched"] == 2
    assert {c[0] for c in sender.calls} == {"sess-1", "sess-2"}
    # Both are removed from the queue.
    assert await redis.zcard(QUEUE_KEY) == 0
    # Inflight counter bumped.
    assert int(await redis.get(INFLIGHT_KEY) or 0) == 2
    # Throttle keys set.
    assert await redis.exists(f"{THROTTLE_PREFIX}:sess-1") == 1
    assert await redis.exists(f"{THROTTLE_PREFIX}:sess-2") == 1


@pytest.mark.asyncio
async def test_tick_skips_future_sessions() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sender = _FakeSender()
    svc = SleepScheduler(redis_client=redis, task_sender=sender)

    # Score = now + 60s → not yet due.
    await _enqueue(redis, "future", score=datetime.now(UTC).timestamp() + 60)

    summary = await svc.tick()
    assert summary["dispatched"] == 0
    assert len(sender.calls) == 0


@pytest.mark.asyncio
async def test_tick_respects_inflight_cap() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sender = _FakeSender()
    svc = SleepScheduler(
        redis_client=redis, task_sender=sender, max_concurrent=2
    )

    for i in range(5):
        await _enqueue(redis, f"s{i}", score=i)

    # Pre-set inflight to the cap.
    await redis.set(INFLIGHT_KEY, 2)

    summary = await svc.tick()
    assert summary["dispatched"] == 0
    assert summary["skipped_inflight"] == 5
    # Queue not drained when over cap.
    assert await redis.zcard(QUEUE_KEY) == 5


@pytest.mark.asyncio
async def test_tick_skips_throttled_sessions() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sender = _FakeSender()
    svc = SleepScheduler(redis_client=redis, task_sender=sender)

    await _enqueue(redis, "busy")
    # Pretend we just dispatched ``busy`` half a second ago.
    await redis.setex(f"{THROTTLE_PREFIX}:busy", 1800, "1")

    summary = await svc.tick()
    assert summary["dispatched"] == 0
    assert summary["skipped_throttled"] == 1
    # Session removed from queue so we don't busy-loop on it.
    assert await redis.zcard(QUEUE_KEY) == 0


# ---------------------------------------------------------------------------
# Task 11.2 — backpressure degraded mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backpressure_sets_degraded_and_dispatches_degraded_true() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sender = _FakeSender()
    svc = SleepScheduler(redis_client=redis, task_sender=sender)

    # Fill the queue past the backpressure threshold. Use distant-future
    # scores for everything except the first item, which is due.
    now_ts = datetime.now(UTC).timestamp()
    mapping = {f"sess-{i}": now_ts + 3600 for i in range(BACKPRESSURE_THRESHOLD + 5)}
    # Our due session so the tick actually dispatches.
    mapping["due-now"] = now_ts - 1
    await redis.zadd(QUEUE_KEY, mapping)

    summary = await svc.tick()

    assert svc.degraded_mode is True
    assert summary["dispatched"] == 1
    assert sender.calls == [("due-now", {"degraded": True})]


@pytest.mark.asyncio
async def test_non_backpressure_dispatches_with_degraded_false() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sender = _FakeSender()
    svc = SleepScheduler(redis_client=redis, task_sender=sender)
    await _enqueue(redis, "s")

    summary = await svc.tick()

    assert svc.degraded_mode is False
    assert summary["dispatched"] == 1
    assert sender.calls == [("s", {"degraded": False})]


# ---------------------------------------------------------------------------
# Task 11.5 — daily token budget (P-Sleep-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_token_budget_caps_dispatches() -> None:
    """**Validates: Requirements 2.10 (P-Sleep-2)**.

    5 due sessions, 5 ticks, budget = 400 tokens = 2 dispatches.
    Remaining 3 are skipped with budget error.
    """
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sender = _FakeSender()
    flags = _FakeFlagSvc(daily_budget=400)
    svc = SleepScheduler(
        redis_client=redis, task_sender=sender, flag_service=flags,
        max_concurrent=100,
        batch_limit=1,  # one dispatch per tick, like a 1-session-per-5s cadence
    )

    # Mark every session as not-throttled already (no-op) and due now.
    now_ts = datetime.now(UTC).timestamp()
    for i in range(5):
        await redis.zadd(QUEUE_KEY, {f"s{i}": now_ts - 10 - i})

    dispatched_total = 0
    skipped_budget_total = 0
    for _ in range(5):
        # Prevent throttle from blocking subsequent ticks by wiping the
        # throttle namespace between ticks (represents "different sessions").
        summary = await svc.tick()
        dispatched_total += summary["dispatched"]
        skipped_budget_total += summary["skipped_budget"]

    assert dispatched_total == 2, f"expected 2 dispatches, got {dispatched_total}"
    assert skipped_budget_total == 3, (
        f"expected 3 budget-skipped, got {skipped_budget_total}"
    )
    # Check the budget counter reflects reality.
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    budget_val = int(
        await redis.get(f"{BUDGET_PREFIX}:global:{date_str}") or 0
    )
    assert budget_val == 400, f"expected 400 spent, got {budget_val}"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_stop_idempotent() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    svc = SleepScheduler(redis_client=redis, task_sender=_FakeSender(), tick_seconds=0.05)

    assert svc.running is False
    await svc.start()
    assert svc.running is True
    # calling start twice is a no-op
    await svc.start()
    await svc.stop()
    assert svc.running is False
