"""Unit tests for :func:`ensure_default_topics` (broker-free).

Mocks :class:`KafkaAdminService` + :class:`KafkaSchemaRegistry` so each test
can drive the decision logic without hitting a real broker or DB.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.kafka.admin import TopicInfo
from src.services.kafka.ensure import (
    DEFAULT_TOPICS,
    EnsureReport,
    default_topics_present,
    ensure_default_topics,
)


def _fake_admin(
    existing: dict[str, TopicInfo],
    *,
    list_fail: bool = False,
    create_fail_on: set[str] | None = None,
) -> MagicMock:
    admin = MagicMock()
    admin.start = AsyncMock()
    admin.close = AsyncMock()

    async def _list_topics(include_internal: bool = False):
        if list_fail:
            raise RuntimeError("broker down")
        return list(existing.values())

    async def _create_topic(name: str, *, partitions: int, replication_factor: int, configs=None):
        if create_fail_on and name in create_fail_on:
            raise RuntimeError(f"create failed for {name}")
        existing[name] = TopicInfo(
            name=name,
            partitions=partitions,
            replication_factor=replication_factor,
            configs=dict(configs or {}),
        )

    async def _alter_topic(name: str, *, partitions=None, configs=None):
        if name in existing and partitions is not None:
            prev = existing[name]
            existing[name] = TopicInfo(
                name=prev.name,
                partitions=partitions,
                replication_factor=prev.replication_factor,
                configs=prev.configs,
            )

    async def _describe_topic(name: str):
        if name not in existing:
            raise LookupError(name)
        return existing[name]

    admin.list_topics = _list_topics
    admin.create_topic = _create_topic
    admin.alter_topic = _alter_topic
    admin.describe_topic = _describe_topic

    # Required for async-context-manager usage inside ensure_default_topics
    async def _aenter(*_a, **_k):
        return admin

    async def _aexit(*_a, **_k):
        return None

    admin.__aenter__ = _aenter
    admin.__aexit__ = _aexit
    return admin


def _fake_registry() -> MagicMock:
    """Schema registry stub that records calls."""
    reg = MagicMock()
    reg.get = AsyncMock(return_value=None)
    reg.register = AsyncMock()
    return reg


@pytest.mark.asyncio
async def test_ensure_creates_all_missing_topics():
    admin = _fake_admin({})
    registry = _fake_registry()

    report = await ensure_default_topics(admin=admin, schema_registry=registry)

    assert isinstance(report, EnsureReport)
    assert report.ok
    expected = {spec.name for spec in DEFAULT_TOPICS}
    assert set(report.created) == expected
    assert report.existing == []
    assert report.upgraded == []
    # Every topic got its placeholder schema seeded
    assert registry.register.await_count == len(DEFAULT_TOPICS)


@pytest.mark.asyncio
async def test_ensure_skips_existing_topics_with_matching_partitions():
    existing = {
        spec.name: TopicInfo(
            name=spec.name,
            partitions=spec.partitions,
            replication_factor=spec.replication,
            configs=dict(spec.configs),
        )
        for spec in DEFAULT_TOPICS
    }
    admin = _fake_admin(existing)
    registry = _fake_registry()
    # Pretend schema already seeded
    registry.get = AsyncMock(return_value=object())

    report = await ensure_default_topics(admin=admin, schema_registry=registry)

    assert report.ok
    assert report.created == []
    assert set(report.existing) == {spec.name for spec in DEFAULT_TOPICS}
    # Schema register NOT called because rows already present
    registry.register.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_upgrades_partition_count_when_lower():
    target = DEFAULT_TOPICS[0]  # ops.agent.trajectory, partitions=6
    existing = {
        target.name: TopicInfo(
            name=target.name,
            partitions=2,  # below required
            replication_factor=1,
            configs={},
        )
    }
    # Other topics still missing — we'll assert they get created
    admin = _fake_admin(existing)
    registry = _fake_registry()

    report = await ensure_default_topics(admin=admin, schema_registry=registry)

    assert target.name in report.upgraded
    # Others should be in ``created``
    assert {spec.name for spec in DEFAULT_TOPICS[1:]}.issubset(set(report.created))
    assert report.ok


@pytest.mark.asyncio
async def test_ensure_never_downgrades():
    target = DEFAULT_TOPICS[0]
    existing = {
        target.name: TopicInfo(
            name=target.name,
            partitions=target.partitions + 4,  # already larger than spec
            replication_factor=1,
            configs={},
        )
    }
    admin = _fake_admin(existing)
    registry = _fake_registry()

    report = await ensure_default_topics(admin=admin, schema_registry=registry)

    # Must NOT be in upgraded — partition growth-only; larger is left alone.
    assert target.name not in report.upgraded
    assert target.name in report.existing


@pytest.mark.asyncio
async def test_ensure_captures_per_topic_errors():
    admin = _fake_admin({}, create_fail_on={"ops.agent.trajectory"})
    registry = _fake_registry()

    report = await ensure_default_topics(admin=admin, schema_registry=registry)

    # One failure, rest created
    assert any("ops.agent.trajectory" in e for e in report.errors)
    assert not report.ok
    assert "ops.agent.trajectory" not in report.created


@pytest.mark.asyncio
async def test_ensure_aborts_on_list_failure():
    admin = _fake_admin({}, list_fail=True)
    registry = _fake_registry()

    report = await ensure_default_topics(admin=admin, schema_registry=registry)

    assert not report.ok
    assert any("list_topics" in e for e in report.errors)
    # No topics created
    assert report.created == []


@pytest.mark.asyncio
async def test_default_topics_present_reports_true_when_all_present():
    existing = {
        spec.name: TopicInfo(
            name=spec.name,
            partitions=spec.partitions,
            replication_factor=spec.replication,
            configs=dict(spec.configs),
        )
        for spec in DEFAULT_TOPICS
    }
    admin = _fake_admin(existing)
    assert await default_topics_present(admin=admin) is True


@pytest.mark.asyncio
async def test_default_topics_present_reports_false_when_any_missing():
    # omit the last spec
    existing = {
        spec.name: TopicInfo(
            name=spec.name,
            partitions=spec.partitions,
            replication_factor=spec.replication,
            configs={},
        )
        for spec in DEFAULT_TOPICS[:-1]
    }
    admin = _fake_admin(existing)
    assert await default_topics_present(admin=admin) is False


@pytest.mark.asyncio
async def test_default_topics_present_returns_false_on_broker_outage():
    admin = _fake_admin({}, list_fail=True)
    assert await default_topics_present(admin=admin) is False
