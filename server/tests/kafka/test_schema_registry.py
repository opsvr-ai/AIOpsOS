"""Unit-ish tests for :class:`KafkaSchemaRegistry`.

Uses the dev Postgres instance via a **per-test** async engine (bound to the
current event loop to avoid the "Event loop is closed" errors you get when a
module-level engine is reused across pytest-asyncio tests). The fixture builds
its own ``async_sessionmaker`` and injects it into the registry.

Each test uses a uuid-suffixed topic and tears itself down — no shared state.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.services.kafka.schema import KafkaSchemaRegistry


# ---------------------------------------------------------------------------
# Skip marker
# ---------------------------------------------------------------------------


def _db_available() -> bool:
    from src.config import settings

    try:
        eng = create_engine(settings.sync_database_url)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not _db_available(), reason="PostgreSQL not available for schema registry tests"
    ),
]


# ---------------------------------------------------------------------------
# Bootstrap: make sure the table exists (the dev DB is bootstrapped via
# ``Base.metadata.create_all``, not Alembic).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _ensure_table():
    from src.config import settings
    from src.models.kafka_schema import KafkaTopicSchema

    eng = create_engine(settings.sync_database_url)
    try:
        KafkaTopicSchema.__table__.create(bind=eng, checkfirst=True)
    finally:
        eng.dispose()
    yield


@pytest_asyncio.fixture
async def registry() -> AsyncGenerator[KafkaSchemaRegistry, None]:
    """Per-test async engine + factory so each test's loop is independent."""
    from src.config import settings

    engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=False,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield KafkaSchemaRegistry(session_factory=session_factory)
    finally:
        await engine.dispose()


async def _cleanup(topic: str, registry: KafkaSchemaRegistry) -> None:
    from src.models.kafka_schema import KafkaTopicSchema

    async with registry._session_factory() as session:
        await session.execute(
            delete(KafkaTopicSchema).where(KafkaTopicSchema.topic == topic)
        )
        await session.commit()


def _unique_topic() -> str:
    return f"test.schema.{uuid.uuid4().hex[:10]}"


_MINIMAL_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["id"],
    "properties": {
        "id": {"type": "string"},
        "count": {"type": "integer", "minimum": 0},
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_and_get_roundtrip(registry: KafkaSchemaRegistry):
    topic = _unique_topic()
    try:
        await registry.register(topic, 1, _MINIMAL_SCHEMA, description="v1")
        row = await registry.get(topic, 1)
        assert row is not None
        assert row.topic == topic
        assert row.version == 1
        assert row.description == "v1"
        assert row.schema["type"] == "object"
    finally:
        await _cleanup(topic, registry)


@pytest.mark.asyncio
async def test_register_upsert_on_conflict(registry: KafkaSchemaRegistry):
    topic = _unique_topic()
    try:
        await registry.register(topic, 1, _MINIMAL_SCHEMA, description="v1")
        await registry.register(topic, 1, _MINIMAL_SCHEMA, description="v1-updated")
        row = await registry.get(topic, 1)
        assert row is not None
        assert row.description == "v1-updated"
    finally:
        await _cleanup(topic, registry)


@pytest.mark.asyncio
async def test_get_latest_when_version_none(registry: KafkaSchemaRegistry):
    topic = _unique_topic()
    try:
        await registry.register(topic, 1, _MINIMAL_SCHEMA)
        await registry.register(topic, 2, _MINIMAL_SCHEMA)
        await registry.register(topic, 3, _MINIMAL_SCHEMA)
        row = await registry.get(topic)
        assert row is not None
        assert row.version == 3
    finally:
        await _cleanup(topic, registry)


@pytest.mark.asyncio
async def test_list_filters_by_topic(registry: KafkaSchemaRegistry):
    t1 = _unique_topic()
    t2 = _unique_topic()
    try:
        await registry.register(t1, 1, _MINIMAL_SCHEMA)
        await registry.register(t1, 2, _MINIMAL_SCHEMA)
        await registry.register(t2, 1, _MINIMAL_SCHEMA)
        t1_rows = await registry.list(topic=t1)
        assert {r.version for r in t1_rows} == {1, 2}
        assert all(r.topic == t1 for r in t1_rows)
    finally:
        await _cleanup(t1, registry)
        await _cleanup(t2, registry)


@pytest.mark.asyncio
async def test_delete(registry: KafkaSchemaRegistry):
    topic = _unique_topic()
    try:
        await registry.register(topic, 1, _MINIMAL_SCHEMA)
        await registry.delete(topic, 1)
        assert await registry.get(topic, 1) is None
    finally:
        await _cleanup(topic, registry)


@pytest.mark.asyncio
async def test_validate_ok_for_conforming_payload(registry: KafkaSchemaRegistry):
    topic = _unique_topic()
    try:
        await registry.register(topic, 1, _MINIMAL_SCHEMA)
        ok, errors = await registry.validate(topic, {"id": "abc", "count": 1})
        assert ok is True
        assert errors == []
    finally:
        await _cleanup(topic, registry)


@pytest.mark.asyncio
async def test_validate_fail_increments_metric_and_returns_errors(
    registry: KafkaSchemaRegistry,
):
    topic = _unique_topic()
    from src.core.metrics import kafka_schema_reject_total

    sample = kafka_schema_reject_total.labels(topic=topic)
    before = sample._value.get()

    try:
        await registry.register(topic, 1, _MINIMAL_SCHEMA)
        ok, errors = await registry.validate(topic, {"count": -1})
        assert ok is False
        assert errors
        after = sample._value.get()
        assert after == before + 1
    finally:
        await _cleanup(topic, registry)


@pytest.mark.asyncio
async def test_validate_missing_schema_returns_error_without_metric(
    registry: KafkaSchemaRegistry,
):
    topic = _unique_topic()
    from src.core.metrics import kafka_schema_reject_total

    sample = kafka_schema_reject_total.labels(topic=topic)
    before = sample._value.get()

    ok, errors = await registry.validate(topic, {"id": "x"})
    assert ok is False
    assert any("no schema" in e for e in errors)
    assert sample._value.get() == before


@pytest.mark.asyncio
async def test_register_rejects_invalid_json_schema(registry: KafkaSchemaRegistry):
    with pytest.raises(ValueError, match="invalid JSON schema"):
        await registry.register("whatever", 1, {"type": 12345})


@pytest.mark.asyncio
async def test_register_rejects_bad_inputs(registry: KafkaSchemaRegistry):
    with pytest.raises(ValueError):
        await registry.register("", 1, _MINIMAL_SCHEMA)
    with pytest.raises(ValueError):
        await registry.register("t", 0, _MINIMAL_SCHEMA)
    with pytest.raises(TypeError):
        await registry.register("t", 1, "not-a-dict")  # type: ignore[arg-type]
