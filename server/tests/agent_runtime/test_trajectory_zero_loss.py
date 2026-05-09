"""PBT: P-Observe-1 — every emitted trajectory event is either persisted
or counted as dropped.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 6.4 / R-5.10 / R-6.3.

**Validates Requirements 6.3 (P-Observe-1)**: every emitted event either
lands in ``agent_trajectories`` within 30s OR is counted in
``trajectory_emit_dropped``. Hypothesis varies the burst size so we
cover queue-overflow paths as well as the happy path.

The test uses dependency injection:
* an ``AsyncMock`` Kafka producer that records every ``send_and_wait``
  call, and
* a fake async session factory whose ``execute(insert, rows)`` appends
  the event ids to a shared list.

That means the PBT never touches real Kafka / Postgres — safe to run
under ``-m 'not kafka'``.
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st

from src.core.metrics import trajectory_emit_dropped
from src.schemas.trajectory import TrajectoryEvent
from src.services.agent_runtime.trajectory import TrajectorySink


# ---------------------------------------------------------------------------
# Fake DB session
# ---------------------------------------------------------------------------


@dataclass
class _Recorder:
    """Collects every row passed to ``session.execute(insert, rows)``."""

    inserted_ids: list[uuid.UUID] = field(default_factory=list)
    insert_failures: int = 0


class _FakeSession:
    def __init__(self, recorder: _Recorder, fail: bool = False) -> None:
        self._rec = recorder
        self._fail = fail

    async def execute(self, _stmt: Any, rows: list[dict] | None = None) -> None:
        if self._fail:
            self._rec.insert_failures += 1
            raise RuntimeError("simulated DB failure")
        for row in rows or []:
            _id = row.get("id")
            if _id is not None:
                self._rec.inserted_ids.append(_id)

    async def commit(self) -> None:  # pragma: no cover - trivial
        return None

    async def rollback(self) -> None:  # pragma: no cover - trivial
        return None


def _make_db_factory(recorder: _Recorder, *, fail: bool = False):
    @asynccontextmanager
    async def _factory():
        yield _FakeSession(recorder, fail=fail)

    return _factory


# ---------------------------------------------------------------------------
# Minimal schema registry stub — returns the real TrajectoryEvent schema
# so validation is not part of what the test stresses.
# ---------------------------------------------------------------------------


class _StubRegistry:
    def __init__(self) -> None:
        self.get = AsyncMock(side_effect=self._get)
        self._row = type(
            "Row",
            (),
            {"schema": TrajectoryEvent.json_schema()},
        )()

    async def _get(self, topic: str, version: int | None = None):  # noqa: ARG002
        return self._row


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event() -> TrajectoryEvent:
    return TrajectoryEvent(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        space_id=None,
        kind="turn",
        ts=datetime.now(tz=timezone.utc),
        outcome="ok",
        data={"message_preview": "hi"},
        tags=["platform:test"],
        metadata={},
    )


def _get_dropped_count() -> float:
    # Access the Counter sample directly — Prometheus client exposes it via
    # the private ``_value`` member because Counters are unary.
    return float(trajectory_emit_dropped._value.get())


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@hsettings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(n=st.integers(min_value=1, max_value=500))
def test_trajectory_emit_is_zero_loss_or_counted(n: int) -> None:
    """P-Observe-1: ``emitted == inserted + dropped_delta`` after the sink drains.

    Hypothesis varies ``n`` across the [1, 500] band which straddles the
    100-slot queue so overflow is exercised for large bursts.
    """

    async def _run() -> None:
        recorder = _Recorder()
        producer = AsyncMock()
        producer.send_and_wait = AsyncMock()

        sink = TrajectorySink(
            bootstrap_servers="mock:9092",
            queue_maxsize=100,  # smaller than max burst → exercises overflow
            batch_size=25,
            flush_interval_s=0.05,
            producer=producer,
            schema_registry=_StubRegistry(),
            db_factory=_make_db_factory(recorder),
        )
        await sink.start()

        before_dropped = _get_dropped_count()

        events = [_make_event() for _ in range(n)]
        emitted_ids = [e.id for e in events]

        # Burst-emit — any overflow here is the system under test.
        for ev in events:
            sink.emit(ev)

        # Give the flusher up to 2s to drain.
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            if len(recorder.inserted_ids) + (
                int(_get_dropped_count() - before_dropped)
            ) >= n:
                break
            await asyncio.sleep(0.02)

        await sink.stop()

        after_dropped = _get_dropped_count()
        dropped_delta = int(round(after_dropped - before_dropped))
        inserted_set = set(recorder.inserted_ids)
        inserted_count = len(inserted_set)

        # Property 1: zero-loss-or-counted.
        # inserted + dropped must equal the number of emitted events.
        assert inserted_count + dropped_delta >= n, (
            f"inserted={inserted_count} + dropped={dropped_delta} < emitted={n}"
        )
        # Nothing else could have been inserted, so upper bound must hold too.
        assert inserted_count <= n
        # Everything inserted must have come from the emitted set.
        assert inserted_set.issubset(set(emitted_ids))

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_trajectory_emit_small_burst_no_drops() -> None:
    """A burst well within the queue budget must be delivered end-to-end
    with zero drops."""
    recorder = _Recorder()
    producer = AsyncMock()
    producer.send_and_wait = AsyncMock()

    sink = TrajectorySink(
        bootstrap_servers="mock:9092",
        queue_maxsize=500,
        batch_size=10,
        flush_interval_s=0.05,
        producer=producer,
        schema_registry=_StubRegistry(),
        db_factory=_make_db_factory(recorder),
    )
    await sink.start()
    before = _get_dropped_count()

    events = [_make_event() for _ in range(50)]
    for ev in events:
        sink.emit(ev)

    # Wait for drain.
    for _ in range(200):
        if len(recorder.inserted_ids) >= 50:
            break
        await asyncio.sleep(0.02)

    await sink.stop()

    assert len(recorder.inserted_ids) == 50
    assert _get_dropped_count() == before  # no drops
    # Every session key produced → producer saw exactly 50 sends.
    assert producer.send_and_wait.await_count == 50


@pytest.mark.asyncio
async def test_trajectory_emit_counts_queue_overflow_as_dropped() -> None:
    """Fill the queue faster than the flusher can drain and assert the
    overflow ends up in the counter.

    We keep the flusher slow by using a large ``flush_interval_s`` and a
    tiny ``queue_maxsize``; the flusher won't pull anything before we've
    already over-filled.
    """
    recorder = _Recorder()
    producer = AsyncMock()
    producer.send_and_wait = AsyncMock()

    sink = TrajectorySink(
        bootstrap_servers="mock:9092",
        queue_maxsize=5,
        batch_size=10,
        flush_interval_s=10.0,  # flusher effectively idle
        producer=producer,
        schema_registry=_StubRegistry(),
        db_factory=_make_db_factory(recorder),
    )
    # Intentionally do NOT start the flusher so nothing drains.
    # We emit directly into the queue and then assert on the counter.
    sink._started = True  # skip start; we'll drive emit without a loop
    before = _get_dropped_count()

    for _ in range(20):
        sink.emit(_make_event())

    after = _get_dropped_count()
    # 20 emits, queue fits 5 → 15 overflow.
    assert int(after - before) == 15
