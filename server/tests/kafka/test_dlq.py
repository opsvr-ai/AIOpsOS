"""Unit tests for :class:`KafkaDLQManager`.

Uses :class:`fakeredis.aioredis.FakeRedis` for the Redis side and
``unittest.mock.AsyncMock`` for the Kafka producer / consumer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest
import pytest_asyncio

from src.services.kafka.dlq import (
    DISCARDED_SET_KEY,
    DLQEntry,
    KafkaDLQManager,
    ReplayReport,
)


@dataclass
class _FakeRecord:
    topic: str
    partition: int
    offset: int
    key: bytes | None
    value: bytes | None
    headers: tuple[tuple[str, bytes], ...] = ()


def _make_record(entry_id: str, original_topic: str = "ops.agent.trajectory", **overrides: Any) -> _FakeRecord:
    payload = {
        "id": entry_id,
        "original_topic": original_topic,
        "original_partition": 0,
        "original_offset": 42,
        "original_key": overrides.get("original_key"),
        "original_value": overrides.get("original_value", {"hello": "world"}),
        "original_headers": overrides.get("original_headers", {}),
        "failure_reason": overrides.get("failure_reason", "SchemaRejected"),
        "failed_at": overrides.get("failed_at", "2026-05-04T12:00:00Z"),
        "attempt_count": overrides.get("attempt_count", 3),
        "tags": overrides.get("tags", {}),
    }
    return _FakeRecord(
        topic=f"{original_topic}.dlq",
        partition=0,
        offset=overrides.get("dlq_offset", 0),
        key=entry_id.encode("utf-8"),
        value=json.dumps(payload).encode("utf-8"),
    )


def _make_fake_consumer(records: list[_FakeRecord], topic: str):
    consumer = AsyncMock()
    consumer.start = AsyncMock()
    consumer.stop = AsyncMock()
    consumer.assign = MagicMock()
    consumer.seek_to_beginning = AsyncMock()
    consumer.partitions_for_topic = MagicMock(return_value={0})
    consumer.topics = AsyncMock(return_value=set())
    from aiokafka.structs import TopicPartition

    tp = TopicPartition(topic, 0)
    end_offset = len(records)
    consumer.end_offsets = AsyncMock(return_value={tp: end_offset})
    batches = iter([{tp: records}]) if records else iter([])

    async def _getmany(timeout_ms=0, max_records=None):
        try:
            return next(batches)
        except StopIteration:
            return {}

    consumer.getmany = _getmany

    # For position tracking, just return the end_offset after the first batch.
    positions: dict[TopicPartition, int] = {tp: 0}

    def _position(partition):
        return end_offset  # after reading batch, caller sees tail

    consumer.position = _position
    return consumer


@pytest_asyncio.fixture
async def redis_stub():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_list_entries_deserializes_and_filters_discarded(redis_stub):
    # Prepare broker fixtures
    r1 = _make_record("id-1", tags={"cause": "schema"})
    r2 = _make_record("id-2", tags={"cause": "timeout"})
    r3 = _make_record("id-3", tags={"cause": "schema"})

    # Pre-mark id-2 as discarded
    await redis_stub.sadd(DISCARDED_SET_KEY, "id-2")

    mgr = KafkaDLQManager(
        bootstrap_servers="mock:9092",
        redis_client=redis_stub,
        consumer_factory=lambda t: _make_fake_consumer([r1, r2, r3], t),
    )

    with patch.object(
        mgr, "_resolve_dlq_topics", AsyncMock(return_value=["ops.agent.trajectory.dlq"])
    ):
        entries = await mgr.list_entries(
            topic="ops.agent.trajectory.dlq", tag_filter={"cause": "schema"}
        )

    assert [e.id for e in entries] == ["id-1", "id-3"]
    assert entries[0].failure_reason == "SchemaRejected"
    assert entries[0].original_topic == "ops.agent.trajectory"


@pytest.mark.asyncio
async def test_list_entries_since_filter(redis_stub):
    old = _make_record("id-old", failed_at="2020-01-01T00:00:00Z")
    new = _make_record("id-new", failed_at="2026-05-04T00:00:00Z")
    mgr = KafkaDLQManager(
        bootstrap_servers="mock:9092",
        redis_client=redis_stub,
        consumer_factory=lambda t: _make_fake_consumer([old, new], t),
    )
    cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with patch.object(
        mgr, "_resolve_dlq_topics", AsyncMock(return_value=["ops.agent.trajectory.dlq"])
    ):
        entries = await mgr.list_entries(
            topic="ops.agent.trajectory.dlq", since=cutoff
        )
    assert [e.id for e in entries] == ["id-new"]


@pytest.mark.asyncio
async def test_replay_produces_to_original_topic(redis_stub):
    r1 = _make_record("id-1")
    r2 = _make_record("id-2")

    producer = AsyncMock()
    producer.send_and_wait = AsyncMock()

    mgr = KafkaDLQManager(
        bootstrap_servers="mock:9092",
        redis_client=redis_stub,
        producer=producer,
        consumer_factory=lambda t: _make_fake_consumer([r1, r2], t),
    )
    with patch.object(
        mgr, "_resolve_dlq_topics", AsyncMock(return_value=["ops.agent.trajectory.dlq"])
    ):
        report = await mgr.replay(["id-1", "id-2"])

    assert isinstance(report, ReplayReport)
    assert report.replayed == 2
    assert report.skipped == 0
    assert report.errors == []
    # Both rows produced to the ORIGINAL topic, not the dlq topic
    produced_topics = [call.args[0] for call in producer.send_and_wait.call_args_list]
    assert produced_topics == ["ops.agent.trajectory", "ops.agent.trajectory"]
    # Redis dedupe keys set
    assert await redis_stub.exists("dlq:replayed:id-1")
    assert await redis_stub.exists("dlq:replayed:id-2")


@pytest.mark.asyncio
async def test_replay_second_call_skips_everything(redis_stub):
    r1 = _make_record("id-1")
    producer = AsyncMock()
    producer.send_and_wait = AsyncMock()
    # Pre-set the dedupe key as if a first replay already happened.
    await redis_stub.set("dlq:replayed:id-1", "1", ex=60)

    mgr = KafkaDLQManager(
        bootstrap_servers="mock:9092",
        redis_client=redis_stub,
        producer=producer,
        consumer_factory=lambda t: _make_fake_consumer([r1], t),
    )
    with patch.object(
        mgr, "_resolve_dlq_topics", AsyncMock(return_value=["ops.agent.trajectory.dlq"])
    ):
        report = await mgr.replay(["id-1"])

    assert report.replayed == 0
    assert report.skipped == 1
    producer.send_and_wait.assert_not_called()


@pytest.mark.asyncio
async def test_replay_target_topic_override(redis_stub):
    r1 = _make_record("id-1")
    producer = AsyncMock()
    producer.send_and_wait = AsyncMock()
    mgr = KafkaDLQManager(
        bootstrap_servers="mock:9092",
        redis_client=redis_stub,
        producer=producer,
        consumer_factory=lambda t: _make_fake_consumer([r1], t),
    )
    with patch.object(
        mgr, "_resolve_dlq_topics", AsyncMock(return_value=["ops.agent.trajectory.dlq"])
    ):
        await mgr.replay(["id-1"], target_topic="ops.agent.trajectory.retry")
    produced_topics = [call.args[0] for call in producer.send_and_wait.call_args_list]
    assert produced_topics == ["ops.agent.trajectory.retry"]


@pytest.mark.asyncio
async def test_replay_missing_ids_report_errors(redis_stub):
    r1 = _make_record("id-1")
    producer = AsyncMock()
    producer.send_and_wait = AsyncMock()
    mgr = KafkaDLQManager(
        bootstrap_servers="mock:9092",
        redis_client=redis_stub,
        producer=producer,
        consumer_factory=lambda t: _make_fake_consumer([r1], t),
    )
    with patch.object(
        mgr, "_resolve_dlq_topics", AsyncMock(return_value=["ops.agent.trajectory.dlq"])
    ):
        report = await mgr.replay(["id-1", "missing-id"])
    assert report.replayed == 1
    assert report.skipped == 0
    assert any("missing-id" in e for e in report.errors)


@pytest.mark.asyncio
async def test_discard_adds_to_set_and_produces_tombstone(redis_stub):
    producer = AsyncMock()
    producer.send_and_wait = AsyncMock()
    mgr = KafkaDLQManager(
        bootstrap_servers="mock:9092",
        redis_client=redis_stub,
        producer=producer,
    )
    n = await mgr.discard(["id-1", "id-2"])
    assert n == 2
    members = await redis_stub.smembers(DISCARDED_SET_KEY)
    assert members == {"id-1", "id-2"}
    # Two tombstones produced
    assert producer.send_and_wait.await_count == 2


@pytest.mark.asyncio
async def test_mark_handled_noop_for_empty_list(redis_stub):
    mgr = KafkaDLQManager(bootstrap_servers="mock:9092", redis_client=redis_stub)
    assert await mgr.mark_handled([]) == 0


@pytest.mark.asyncio
async def test_mark_handled_adds_to_set(redis_stub):
    mgr = KafkaDLQManager(bootstrap_servers="mock:9092", redis_client=redis_stub)
    n = await mgr.mark_handled(["id-a", "id-b"])
    assert n == 2
    members = await redis_stub.smembers("dlq:handled:all")
    assert members == {"id-a", "id-b"}
