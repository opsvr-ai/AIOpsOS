"""Unit tests for :class:`KafkaMetricsCollector`.

Stubs the admin service + consumer to avoid touching a real broker. Each test
drives one ``_tick()`` call and asserts the Prometheus gauges were updated.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.services.kafka.admin import (
    ConsumerGroupDetail,
    ConsumerGroupInfo,
    PartitionLag,
    TopicInfo,
)
from src.services.kafka.metrics import KafkaMetricsCollector


class _StubAdmin:
    """Enough of :class:`KafkaAdminService` to drive the collector."""

    def __init__(self, topics: list[TopicInfo], groups: list[ConsumerGroupInfo], details: dict[str, ConsumerGroupDetail]):
        self._topics = topics
        self._groups = groups
        self._details = details
        self.close = AsyncMock()
        self.start = AsyncMock()

    async def list_topics(self, include_internal: bool = False) -> list[TopicInfo]:
        return list(self._topics)

    async def list_consumer_groups(self) -> list[ConsumerGroupInfo]:
        return list(self._groups)

    async def describe_group(self, group_id: str) -> ConsumerGroupDetail:
        return self._details[group_id]


@pytest.mark.asyncio
async def test_tick_publishes_lag_per_partition():
    topics = [TopicInfo(name="demo", partitions=2, replication_factor=1, configs={})]
    groups = [ConsumerGroupInfo(group_id="g1", state="Stable", protocol_type="consumer")]
    details = {
        "g1": ConsumerGroupDetail(
            group_id="g1",
            state="Stable",
            lags=[
                PartitionLag(topic="demo", partition=0, current_offset=10, end_offset=40, lag=30),
                PartitionLag(topic="demo", partition=1, current_offset=5, end_offset=5, lag=0),
            ],
        )
    }
    stub = _StubAdmin(topics, groups, details)
    collector = KafkaMetricsCollector(admin=stub, bootstrap_servers="mock:9092")

    from src.core.metrics import kafka_lag

    await collector._tick()

    # The Prometheus gauge exposes the most recent observation; read it back
    # via the private API on the metric.
    got_30 = kafka_lag.labels(group="g1", topic="demo", partition="0")._value.get()
    got_0 = kafka_lag.labels(group="g1", topic="demo", partition="1")._value.get()
    assert got_30 == 30.0
    assert got_0 == 0.0


@pytest.mark.asyncio
async def test_tick_publishes_dlq_rate():
    topics = [
        TopicInfo(name="ops.agent.trajectory", partitions=3, replication_factor=1, configs={}),
        TopicInfo(name="ops.agent.trajectory.dlq", partitions=2, replication_factor=1, configs={}),
    ]
    stub = _StubAdmin(topics, groups=[], details={})
    collector = KafkaMetricsCollector(admin=stub, bootstrap_servers="mock:9092")

    # Patch end-offset fetching with monotonic counts so the rolling window
    # sees growth between ticks.
    sequence = iter([
        {"ops.agent.trajectory.dlq": 100},
        {"ops.agent.trajectory.dlq": 160},  # +60 events
    ])

    async def fake_end_offsets(topics_list):
        return next(sequence)

    # Also freeze monotonic time so we know elapsed = 10s exactly.
    base = 1000.0
    time_seq = iter([base, base + 10.0])

    def fake_monotonic():
        return next(time_seq)

    with patch.object(collector, "_fetch_end_offsets_by_topic", new=fake_end_offsets), \
         patch("src.services.kafka.metrics.time.monotonic", new=fake_monotonic):
        await collector._tick()
        await collector._tick()

    from src.core.metrics import kafka_dlq_rate

    rate = kafka_dlq_rate.labels(topic="ops.agent.trajectory.dlq")._value.get()
    # 60 events over 10s = 6/s = 360/min
    assert rate == pytest.approx(360.0, rel=0.01)


@pytest.mark.asyncio
async def test_tick_single_sample_reports_zero_rate():
    topics = [TopicInfo(name="ops.agent.trajectory.dlq", partitions=1, replication_factor=1, configs={})]
    stub = _StubAdmin(topics, groups=[], details={})
    collector = KafkaMetricsCollector(admin=stub, bootstrap_servers="mock:9092")

    async def fake_end_offsets(topics_list):
        return {"ops.agent.trajectory.dlq": 42}

    with patch.object(collector, "_fetch_end_offsets_by_topic", new=fake_end_offsets):
        await collector._tick()

    from src.core.metrics import kafka_dlq_rate

    # One sample → rate cannot be computed; expect 0.
    rate = kafka_dlq_rate.labels(topic="ops.agent.trajectory.dlq")._value.get()
    assert rate == 0.0


@pytest.mark.asyncio
async def test_start_stop_lifecycle_runs_background_task():
    topics = [TopicInfo(name="demo", partitions=1, replication_factor=1, configs={})]
    stub = _StubAdmin(topics, groups=[], details={})
    collector = KafkaMetricsCollector(
        admin=stub, bootstrap_servers="mock:9092", poll_interval_s=0.01
    )
    # Patch DLQ fetcher so we don't hit any real broker code
    async def fake_end_offsets(topics_list):
        return {t: 0 for t in topics_list}

    with patch.object(collector, "_fetch_end_offsets_by_topic", new=fake_end_offsets):
        await collector.start()
        # give the loop a chance to tick once
        import asyncio

        await asyncio.sleep(0.05)
        await collector.stop()
    # Second stop is a no-op (task already None)
    await collector.stop()


@pytest.mark.asyncio
async def test_tick_errors_in_describe_group_are_swallowed():
    topics = [TopicInfo(name="demo", partitions=1, replication_factor=1, configs={})]
    groups = [ConsumerGroupInfo(group_id="g-bad", state=None, protocol_type=None)]

    class _Flaky(_StubAdmin):
        async def describe_group(self, group_id: str):
            raise RuntimeError("boom")

    stub = _Flaky(topics, groups, {})
    collector = KafkaMetricsCollector(admin=stub, bootstrap_servers="mock:9092")

    # Should not raise
    await collector._tick()
