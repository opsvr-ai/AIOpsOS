"""Unit tests for :class:`KafkaAdminService`.

All tests mock ``aiokafka.admin.AIOKafkaAdminClient`` so they run without a
real broker. The goal is to verify translation between aiokafka types and
the service's public DTOs — not to exercise aiokafka itself.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.kafka.admin import (
    ConsumerGroupInfo,
    KafkaAdminService,
    TopicInfo,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_describe_topics_response(name: str, partitions: int, replication: int):
    parts = [
        {
            "error_code": 0,
            "partition": i,
            "leader": 1,
            "replicas": list(range(replication)),
            "isr": list(range(replication)),
            "offline_replicas": [],
        }
        for i in range(partitions)
    ]
    return [
        {
            "error_code": 0,
            "topic": name,
            "is_internal": False,
            "partitions": parts,
        }
    ]


def _make_configs_response(name: str, configs: dict[str, str]):
    # Shape mirrors DescribeConfigsResponse_v2: resp.resources = [(code, msg, type, resource_name, [entries])]
    resp = MagicMock()
    entries = [
        (k, v, False, 5, False, [])
        for k, v in configs.items()
    ]
    resp.resources = [(0, "", 2, name, entries)]
    return [resp]


# ---------------------------------------------------------------------------
# topics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_topics_maps_to_topic_info():
    svc = KafkaAdminService(bootstrap_servers="mock:9092")
    fake = AsyncMock()
    fake.list_topics.return_value = ["demo", "_internal"]
    fake.describe_topics.return_value = _make_describe_topics_response("demo", partitions=3, replication=2)
    fake.describe_configs.return_value = _make_configs_response("demo", {"retention.ms": "604800000"})
    svc._client = fake

    topics = await svc.list_topics(include_internal=False)

    # internal topic filtered out
    assert [t.name for t in topics] == ["demo"]
    info = topics[0]
    assert isinstance(info, TopicInfo)
    assert info.partitions == 3
    assert info.replication_factor == 2
    assert info.configs == {"retention.ms": "604800000"}


@pytest.mark.asyncio
async def test_list_topics_include_internal():
    svc = KafkaAdminService(bootstrap_servers="mock:9092")
    fake = AsyncMock()
    fake.list_topics.return_value = ["demo", "_internal"]
    fake.describe_topics.return_value = [
        *_make_describe_topics_response("demo", 1, 1),
        *_make_describe_topics_response("_internal", 1, 1),
    ]
    fake.describe_configs.return_value = [
        *_make_configs_response("demo", {}),
        *_make_configs_response("_internal", {}),
    ]
    svc._client = fake
    topics = await svc.list_topics(include_internal=True)
    assert {t.name for t in topics} == {"demo", "_internal"}


@pytest.mark.asyncio
async def test_describe_topic_found():
    svc = KafkaAdminService(bootstrap_servers="mock:9092")
    fake = AsyncMock()
    fake.describe_topics.return_value = _make_describe_topics_response("demo", 6, 1)
    fake.describe_configs.return_value = _make_configs_response("demo", {"cleanup.policy": "compact"})
    svc._client = fake

    info = await svc.describe_topic("demo")
    assert info.name == "demo"
    assert info.partitions == 6
    assert info.configs["cleanup.policy"] == "compact"


@pytest.mark.asyncio
async def test_describe_topic_missing_raises_lookup_error():
    svc = KafkaAdminService(bootstrap_servers="mock:9092")
    fake = AsyncMock()
    fake.describe_topics.return_value = [
        {"error_code": 3, "topic": "ghost", "is_internal": False, "partitions": []}
    ]
    fake.describe_configs.return_value = []
    svc._client = fake

    with pytest.raises(LookupError):
        await svc.describe_topic("ghost")


@pytest.mark.asyncio
async def test_create_topic_passes_configs_and_idempotent():
    from aiokafka.errors import TopicAlreadyExistsError

    svc = KafkaAdminService(bootstrap_servers="mock:9092")
    fake = AsyncMock()
    fake.create_topics = AsyncMock()
    svc._client = fake

    await svc.create_topic(
        "demo", partitions=3, replication_factor=2, configs={"retention.ms": "1000"}
    )
    fake.create_topics.assert_awaited_once()
    args, _ = fake.create_topics.call_args
    (topics_list,) = args
    assert len(topics_list) == 1
    new_topic = topics_list[0]
    assert new_topic.name == "demo"
    assert new_topic.num_partitions == 3
    assert new_topic.replication_factor == 2
    # Config is stored under ``topic_configs`` on aiokafka NewTopic
    assert new_topic.topic_configs == {"retention.ms": "1000"}

    # Second call: idempotent on TopicAlreadyExistsError
    fake.create_topics.side_effect = TopicAlreadyExistsError("demo")
    await svc.create_topic("demo")  # should not raise


@pytest.mark.asyncio
async def test_alter_topic_grows_partitions_and_updates_configs():
    svc = KafkaAdminService(bootstrap_servers="mock:9092")
    fake = AsyncMock()
    fake.create_partitions = AsyncMock()
    fake.alter_configs = AsyncMock()
    svc._client = fake

    await svc.alter_topic("demo", partitions=10, configs={"retention.ms": "2000"})
    fake.create_partitions.assert_awaited_once()
    (arg,), _ = fake.create_partitions.call_args
    assert "demo" in arg
    assert arg["demo"].total_count == 10
    fake.alter_configs.assert_awaited_once()


@pytest.mark.asyncio
async def test_alter_topic_noop_without_params():
    svc = KafkaAdminService(bootstrap_servers="mock:9092")
    fake = AsyncMock()
    fake.create_partitions = AsyncMock()
    fake.alter_configs = AsyncMock()
    svc._client = fake

    await svc.alter_topic("demo")
    fake.create_partitions.assert_not_called()
    fake.alter_configs.assert_not_called()


@pytest.mark.asyncio
async def test_delete_topic_requires_confirm():
    svc = KafkaAdminService(bootstrap_servers="mock:9092")
    fake = AsyncMock()
    fake.delete_topics = AsyncMock()
    svc._client = fake

    with pytest.raises(ValueError, match="confirm=True required"):
        await svc.delete_topic("demo")
    fake.delete_topics.assert_not_called()

    await svc.delete_topic("demo", confirm=True)
    fake.delete_topics.assert_awaited_once_with(["demo"])


# ---------------------------------------------------------------------------
# consumer groups
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_consumer_groups_tuple_shape():
    svc = KafkaAdminService(bootstrap_servers="mock:9092")
    fake = AsyncMock()
    fake.list_consumer_groups.return_value = [
        ("group-a", "consumer"),
        ("group-b", "consumer", "Stable"),
    ]
    svc._client = fake

    groups = await svc.list_consumer_groups()
    assert groups == [
        ConsumerGroupInfo(group_id="group-a", protocol_type="consumer", state=None),
        ConsumerGroupInfo(group_id="group-b", protocol_type="consumer", state="Stable"),
    ]


@pytest.mark.asyncio
async def test_list_consumer_groups_dict_shape():
    svc = KafkaAdminService(bootstrap_servers="mock:9092")
    fake = AsyncMock()
    fake.list_consumer_groups.return_value = [
        {"group_id": "g1", "protocol_type": "consumer", "state": "Empty"}
    ]
    svc._client = fake
    groups = await svc.list_consumer_groups()
    assert len(groups) == 1 and groups[0].group_id == "g1" and groups[0].state == "Empty"


@pytest.mark.asyncio
async def test_describe_group_computes_lag_via_patched_consumer():
    svc = KafkaAdminService(bootstrap_servers="mock:9092")
    fake = AsyncMock()

    class _Resp:
        # Matches aiokafka DescribeGroupsResponse.groups = [(code, gid, state, proto_type, proto, members)]
        groups = [
            (0, "g1", "Stable", "consumer", "range", []),
        ]

    fake.describe_consumer_groups.return_value = [_Resp()]

    # Pretend committed offsets for g1
    from aiokafka.structs import OffsetAndMetadata, TopicPartition

    committed = {
        TopicPartition("demo", 0): OffsetAndMetadata(42, ""),
        TopicPartition("demo", 1): OffsetAndMetadata(50, ""),
    }
    fake.list_consumer_group_offsets.return_value = committed
    svc._client = fake

    # Patch AIOKafkaConsumer at the import site inside the service.
    fake_consumer = AsyncMock()
    fake_consumer.start = AsyncMock()
    fake_consumer.stop = AsyncMock()
    fake_consumer.end_offsets = AsyncMock(
        return_value={
            TopicPartition("demo", 0): 100,
            TopicPartition("demo", 1): 50,
        }
    )

    with patch("aiokafka.AIOKafkaConsumer", return_value=fake_consumer):
        detail = await svc.describe_group("g1")

    assert detail.group_id == "g1"
    assert detail.state == "Stable"
    lags_by_partition = {(l.topic, l.partition): l.lag for l in detail.lags}
    assert lags_by_partition[("demo", 0)] == 58  # 100 - 42
    assert lags_by_partition[("demo", 1)] == 0  # 50 - 50
    assert detail.total_lag == 58


# ---------------------------------------------------------------------------
# reset_offset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_offset_earliest():
    svc = KafkaAdminService(bootstrap_servers="mock:9092")

    from aiokafka.structs import TopicPartition

    fake_consumer = AsyncMock()
    fake_consumer.start = AsyncMock()
    fake_consumer.stop = AsyncMock()
    fake_consumer.assign = MagicMock()
    fake_consumer.seek = MagicMock()
    fake_consumer.commit = AsyncMock()
    fake_consumer.beginning_offsets = AsyncMock(
        return_value={TopicPartition("demo", 0): 17}
    )

    with patch("aiokafka.AIOKafkaConsumer", return_value=fake_consumer):
        new_off = await svc.reset_offset("g1", "demo", 0, "earliest")

    assert new_off == 17
    fake_consumer.seek.assert_called_once_with(TopicPartition("demo", 0), 17)
    fake_consumer.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reset_offset_specific_integer():
    svc = KafkaAdminService(bootstrap_servers="mock:9092")
    from aiokafka.structs import TopicPartition

    fake_consumer = AsyncMock()
    fake_consumer.start = AsyncMock()
    fake_consumer.stop = AsyncMock()
    fake_consumer.assign = MagicMock()
    fake_consumer.seek = MagicMock()
    fake_consumer.commit = AsyncMock()
    with patch("aiokafka.AIOKafkaConsumer", return_value=fake_consumer):
        new_off = await svc.reset_offset("g1", "demo", 0, "1234")
    assert new_off == 1234
    fake_consumer.seek.assert_called_once_with(TopicPartition("demo", 0), 1234)


@pytest.mark.asyncio
async def test_context_manager_starts_and_stops_client():
    svc = KafkaAdminService(bootstrap_servers="mock:9092")

    fake_admin = AsyncMock()
    fake_admin.start = AsyncMock()
    fake_admin.close = AsyncMock()

    with patch(
        "aiokafka.admin.AIOKafkaAdminClient", return_value=fake_admin
    ):
        async with svc as entered:
            assert entered is svc
            assert svc._client is not None
        fake_admin.start.assert_awaited_once()
        fake_admin.close.assert_awaited_once()
        assert svc._client is None


@pytest.mark.asyncio
async def test_ensure_raises_without_start():
    svc = KafkaAdminService(bootstrap_servers="mock:9092")
    with pytest.raises(RuntimeError, match="not started"):
        await svc.list_topics()
