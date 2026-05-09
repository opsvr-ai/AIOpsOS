"""PBT: DLQ replay is idempotent per ``original_message_id``.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 4.9 / R-5.5.

**Validates: Requirements 5.5** — "重放 SHALL 幂等（相同 id 重放产生相同业
务效果或幂等跳过）".

Property statement (informal):
Given any set S of N distinct DLQ entry ids (1 ≤ N ≤ 20),
let ``replay(S)`` be invoked twice on the same manager with the same backing
store. Then:
  1. The first replay produces exactly N messages to the target topic.
  2. The second replay produces 0 additional messages (all ids are skipped).
  3. The Redis dedupe key ``dlq:replayed:{id}`` exists for every id in S.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from src.services.kafka.dlq import KafkaDLQManager


@dataclass
class _FakeRecord:
    topic: str
    partition: int
    offset: int
    key: bytes | None
    value: bytes | None
    headers: tuple[tuple[str, bytes], ...] = ()


def _record_for(entry_id: str, dlq_topic: str = "ops.agent.trajectory.dlq") -> _FakeRecord:
    payload = {
        "id": entry_id,
        "original_topic": "ops.agent.trajectory",
        "original_partition": 0,
        "original_offset": 0,
        "original_value": {"payload": entry_id},
        "failure_reason": "x",
        "failed_at": "2026-05-04T00:00:00Z",
        "attempt_count": 1,
        "tags": {},
    }
    return _FakeRecord(
        topic=dlq_topic,
        partition=0,
        offset=0,
        key=entry_id.encode("utf-8"),
        value=json.dumps(payload).encode("utf-8"),
    )


def _make_consumer_factory(records: list[_FakeRecord]):
    """Return a consumer_factory callable that serves ``records`` once per call."""
    def _factory(topic: str):
        return _make_fake_consumer(records, topic)

    return _factory


def _make_fake_consumer(records: list[_FakeRecord], topic: str) -> Any:
    from aiokafka.structs import TopicPartition

    consumer = AsyncMock()
    consumer.start = AsyncMock()
    consumer.stop = AsyncMock()
    consumer.assign = MagicMock()
    consumer.seek_to_beginning = AsyncMock()
    consumer.partitions_for_topic = MagicMock(return_value={0})
    consumer.topics = AsyncMock(return_value=set())
    tp = TopicPartition(topic, 0)
    consumer.end_offsets = AsyncMock(return_value={tp: len(records)})

    batches = iter([{tp: records}]) if records else iter([])

    async def _getmany(timeout_ms=0, max_records=None):
        try:
            return next(batches)
        except StopIteration:
            return {}

    consumer.getmany = _getmany
    consumer.position = MagicMock(return_value=len(records))
    return consumer


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    entry_ids=st.lists(
        st.text(
            alphabet=st.characters(
                min_codepoint=ord("a"), max_codepoint=ord("z")
            ),
            min_size=4,
            max_size=10,
        ),
        min_size=1,
        max_size=20,
        unique=True,
    )
)
def test_replay_is_idempotent(entry_ids: list[str]) -> None:
    """First replay produces N; second replay skips N; redis keys exist."""

    async def _run() -> None:
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        producer = AsyncMock()
        producer.send_and_wait = AsyncMock()

        records = [_record_for(eid) for eid in entry_ids]
        mgr = KafkaDLQManager(
            bootstrap_servers="mock:9092",
            redis_client=redis,
            producer=producer,
            consumer_factory=_make_consumer_factory(records),
        )

        with patch.object(
            mgr,
            "_resolve_dlq_topics",
            AsyncMock(return_value=["ops.agent.trajectory.dlq"]),
        ):
            first = await mgr.replay(entry_ids)
            # Rebuild the consumer_factory because each consumer's batch iter
            # is consumed after a single pass.
            mgr._consumer_factory = _make_consumer_factory(records)
            second = await mgr.replay(entry_ids)

        # Property 1: first call replays exactly N
        assert first.replayed == len(entry_ids), (
            f"expected {len(entry_ids)} replays, got {first.replayed}"
        )
        assert first.skipped == 0
        assert producer.send_and_wait.await_count == len(entry_ids)

        # Property 2: second call replays 0 and skips N
        assert second.replayed == 0
        assert second.skipped == len(entry_ids)
        # Total produce calls unchanged
        assert producer.send_and_wait.await_count == len(entry_ids)

        # Property 3: every id has a Redis dedupe key
        for eid in entry_ids:
            assert await redis.exists(f"dlq:replayed:{eid}") == 1

        await redis.aclose()

    asyncio.run(_run())
