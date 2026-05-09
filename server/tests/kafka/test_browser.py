"""Unit tests for :class:`KafkaBrowser`.

The consumer is fully mocked. We exercise:
  * offset semantics (earliest / latest / absolute int / negative tail)
  * regex filtering for key / value / header
  * limit + empty-tail timeout
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.kafka.browser import BrowserMessage, KafkaBrowser


@dataclass
class _FakeRecord:
    topic: str
    partition: int
    offset: int
    timestamp: int | None
    key: bytes | None
    value: bytes | None
    headers: tuple[tuple[str, bytes], ...] = ()


def _make_consumer(partitions: set[int], batches: list[dict[Any, list[_FakeRecord]]]):
    """Create an AsyncMock configured as a fake consumer."""
    consumer = AsyncMock()
    consumer.start = AsyncMock()
    consumer.stop = AsyncMock()
    consumer.assign = MagicMock()
    consumer.seek = MagicMock()
    consumer.seek_to_beginning = AsyncMock()
    consumer.seek_to_end = AsyncMock()
    consumer.partitions_for_topic = MagicMock(return_value=partitions)
    consumer.topics = AsyncMock(return_value=set())
    # Feed batches; after exhaustion return an empty dict to simulate tail.
    batch_iter = iter(batches)

    async def _getmany(timeout_ms=0, max_records=None):
        try:
            return next(batch_iter)
        except StopIteration:
            return {}

    consumer.getmany = _getmany
    return consumer


@pytest.mark.asyncio
async def test_fetch_earliest_returns_messages_up_to_limit():
    browser = KafkaBrowser(bootstrap_servers="mock:9092", default_timeout_s=0.5)

    from aiokafka.structs import TopicPartition

    tp0 = TopicPartition("demo", 0)
    records = [
        _FakeRecord("demo", 0, 0, 1000, b"k0", b"v0"),
        _FakeRecord("demo", 0, 1, 1001, b"k1", b"v1"),
        _FakeRecord("demo", 0, 2, 1002, b"k2", b"v2"),
    ]
    consumer = _make_consumer({0}, [{tp0: records}])

    with patch("aiokafka.AIOKafkaConsumer", return_value=consumer):
        msgs = await browser.fetch("demo", start_offset="earliest", limit=2)

    assert [m.offset for m in msgs] == [0, 1]
    assert msgs[0].key == "k0"
    assert msgs[0].value == "v0"
    consumer.seek_to_beginning.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_latest_seeks_to_end_and_no_messages_returns_empty():
    browser = KafkaBrowser(bootstrap_servers="mock:9092", default_timeout_s=0.05)
    consumer = _make_consumer({0, 1}, [])  # no batches -> empty tail

    with patch("aiokafka.AIOKafkaConsumer", return_value=consumer):
        msgs = await browser.fetch("demo", start_offset="latest", limit=10)

    assert msgs == []
    consumer.seek_to_end.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_absolute_int_offset_seeks_all_partitions():
    browser = KafkaBrowser(bootstrap_servers="mock:9092", default_timeout_s=0.1)
    from aiokafka.structs import TopicPartition

    consumer = _make_consumer({0, 1}, [])
    with patch("aiokafka.AIOKafkaConsumer", return_value=consumer):
        await browser.fetch("demo", start_offset=42, limit=5)

    # Two partitions → two seeks
    assert consumer.seek.call_count == 2
    called_positions = {call.args[1] for call in consumer.seek.call_args_list}
    assert called_positions == {42}
    called_tps = {call.args[0] for call in consumer.seek.call_args_list}
    assert TopicPartition("demo", 0) in called_tps
    assert TopicPartition("demo", 1) in called_tps


@pytest.mark.asyncio
async def test_fetch_negative_offset_tails_end():
    browser = KafkaBrowser(bootstrap_servers="mock:9092", default_timeout_s=0.1)
    from aiokafka.structs import TopicPartition

    consumer = _make_consumer({0}, [])
    consumer.end_offsets = AsyncMock(return_value={TopicPartition("demo", 0): 100})

    with patch("aiokafka.AIOKafkaConsumer", return_value=consumer):
        await browser.fetch("demo", start_offset=-10, limit=5)

    # 100 - 10 = 90
    consumer.seek.assert_called_once_with(TopicPartition("demo", 0), 90)


@pytest.mark.asyncio
async def test_fetch_regex_filters_key_value_headers():
    browser = KafkaBrowser(bootstrap_servers="mock:9092", default_timeout_s=0.2)
    from aiokafka.structs import TopicPartition

    records = [
        _FakeRecord("demo", 0, 0, None, b"user:alice", b"login", (("trace", b"1"),)),
        _FakeRecord("demo", 0, 1, None, b"user:bob", b"logout", (("trace", b"2"),)),
        _FakeRecord("demo", 0, 2, None, b"admin:root", b"deploy", (("tier", b"prod"),)),
    ]
    consumer = _make_consumer({0}, [{TopicPartition("demo", 0): records}])

    with patch("aiokafka.AIOKafkaConsumer", return_value=consumer):
        msgs = await browser.fetch(
            "demo",
            start_offset="earliest",
            limit=10,
            key_regex=r"^user:",
        )

    assert [m.offset for m in msgs] == [0, 1]

    consumer = _make_consumer({0}, [{TopicPartition("demo", 0): records}])
    with patch("aiokafka.AIOKafkaConsumer", return_value=consumer):
        msgs = await browser.fetch(
            "demo",
            start_offset="earliest",
            limit=10,
            value_regex=r"log",
        )
    assert [m.value for m in msgs] == ["login", "logout"]

    consumer = _make_consumer({0}, [{TopicPartition("demo", 0): records}])
    with patch("aiokafka.AIOKafkaConsumer", return_value=consumer):
        msgs = await browser.fetch(
            "demo",
            start_offset="earliest",
            limit=10,
            header_regex=r"tier=prod",
        )
    assert [m.offset for m in msgs] == [2]


@pytest.mark.asyncio
async def test_fetch_invalid_start_offset_raises():
    browser = KafkaBrowser(bootstrap_servers="mock:9092")
    consumer = _make_consumer({0}, [])
    with patch("aiokafka.AIOKafkaConsumer", return_value=consumer):
        with pytest.raises(ValueError):
            await browser.fetch("demo", start_offset="invalid-value")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_fetch_unknown_partition_raises_lookup():
    browser = KafkaBrowser(bootstrap_servers="mock:9092")
    consumer = _make_consumer({0}, [])
    with patch("aiokafka.AIOKafkaConsumer", return_value=consumer):
        with pytest.raises(LookupError):
            await browser.fetch("demo", partition=9, start_offset="earliest")


@pytest.mark.asyncio
async def test_fetch_returns_decoded_messages():
    browser = KafkaBrowser(bootstrap_servers="mock:9092", default_timeout_s=0.1)
    from aiokafka.structs import TopicPartition

    # Non-utf8 bytes should fall back to replacement.
    records = [
        _FakeRecord(
            "demo",
            0,
            0,
            1700000000000,
            b"\xff\xfe",
            b'{"k":"v"}',
            (("h1", b"x"),),
        ),
    ]
    consumer = _make_consumer({0}, [{TopicPartition("demo", 0): records}])
    with patch("aiokafka.AIOKafkaConsumer", return_value=consumer):
        msgs = await browser.fetch("demo", start_offset="earliest", limit=1)
    assert len(msgs) == 1
    msg = msgs[0]
    assert isinstance(msg, BrowserMessage)
    assert msg.value == '{"k":"v"}'
    assert msg.timestamp == 1700000000000
    assert msg.headers == {"h1": "x"}


@pytest.mark.asyncio
async def test_fetch_zero_limit_short_circuits():
    browser = KafkaBrowser(bootstrap_servers="mock:9092")
    # No consumer patch needed; method returns immediately.
    assert await browser.fetch("demo", limit=0) == []
