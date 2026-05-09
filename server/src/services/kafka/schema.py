"""Local JSON-schema registry for Kafka topics.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 4.6 / R-5.6.

Thin wrapper around the :class:`src.models.kafka_schema.KafkaTopicSchema`
table. Validation uses :mod:`jsonschema` Draft 2020-12; the schemas themselves
are validated on ``register`` via ``check_schema`` to catch malformed meta.

Validation failures are surfaced to callers as ``(False, [error_messages])``
and reported to Prometheus via ``kafka_schema_reject_total``.
"""
from __future__ import annotations

import logging
from typing import Any

from jsonschema import Draft202012Validator, SchemaError
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.metrics import kafka_schema_reject_total
from src.models.base import async_session_factory
from src.models.kafka_schema import KafkaTopicSchema

logger = logging.getLogger(__name__)


class KafkaSchemaRegistry:
    """Async CRUD + validate facade over the ``kafka_topic_schemas`` table."""

    def __init__(self, session_factory: Any | None = None) -> None:
        # Allow tests to inject an alternate session factory (e.g. a test DB)
        self._session_factory = session_factory or async_session_factory

    def _session(self) -> AsyncSession:
        return self._session_factory()

    # -- write ---------------------------------------------------------

    async def register(
        self,
        topic: str,
        version: int,
        schema: dict,
        description: str | None = None,
    ) -> None:
        """Register a schema for ``(topic, version)``. Upserts on conflict."""
        if not topic:
            raise ValueError("topic must be non-empty")
        if version < 1:
            raise ValueError("version must be >= 1")
        if not isinstance(schema, dict):
            raise TypeError("schema must be a dict")
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ValueError(f"invalid JSON schema: {exc.message}") from exc

        stmt = (
            pg_insert(KafkaTopicSchema)
            .values(
                topic=topic,
                version=version,
                schema=schema,
                description=description,
            )
            .on_conflict_do_update(
                constraint="uq_kafka_topic_schemas_topic_version",
                set_={
                    "schema": schema,
                    "description": description,
                },
            )
        )
        async with self._session() as session:
            await session.execute(stmt)
            await session.commit()

    async def delete(self, topic: str, version: int) -> None:
        async with self._session() as session:
            await session.execute(
                delete(KafkaTopicSchema).where(
                    KafkaTopicSchema.topic == topic,
                    KafkaTopicSchema.version == version,
                )
            )
            await session.commit()

    # -- read ----------------------------------------------------------

    async def get(
        self, topic: str, version: int | None = None
    ) -> KafkaTopicSchema | None:
        """Fetch a specific version, or the latest if ``version is None``."""
        async with self._session() as session:
            stmt = select(KafkaTopicSchema).where(KafkaTopicSchema.topic == topic)
            if version is not None:
                stmt = stmt.where(KafkaTopicSchema.version == version)
            else:
                stmt = stmt.order_by(KafkaTopicSchema.version.desc()).limit(1)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list(self, topic: str | None = None) -> list[KafkaTopicSchema]:
        async with self._session() as session:
            stmt = select(KafkaTopicSchema)
            if topic is not None:
                stmt = stmt.where(KafkaTopicSchema.topic == topic)
            stmt = stmt.order_by(KafkaTopicSchema.topic, KafkaTopicSchema.version)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # -- validate ------------------------------------------------------

    async def validate(
        self,
        topic: str,
        payload: dict,
        version: int | None = None,
    ) -> tuple[bool, list[str]]:
        """Validate a payload against the schema registered for ``topic``.

        Returns ``(True, [])`` if OK, else ``(False, [error_messages])``.
        If no schema exists for the topic, validation fails with
        ``["no schema registered for topic <name>"]`` and the metric is not
        incremented (this is a configuration problem, not a data problem).
        """
        row = await self.get(topic, version)
        if row is None:
            return False, [f"no schema registered for topic {topic}"]

        validator = Draft202012Validator(row.schema)
        errors = [
            f"{'.'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
            for err in sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
        ]
        if errors:
            try:
                kafka_schema_reject_total.labels(topic=topic).inc()
            except Exception:  # pragma: no cover - metric backend failures
                logger.exception("metric increment failed")
            return False, errors
        return True, []


__all__ = ["KafkaSchemaRegistry"]
