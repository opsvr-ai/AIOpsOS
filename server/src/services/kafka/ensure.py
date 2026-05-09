"""Ensure the platform's default Kafka topics exist on startup.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 4.7 / R-5.1.

The default topic set lives in :data:`DEFAULT_TOPICS` and mirrors
design.md § "平台默认注册的 topics". Startup semantics:

* **create** — topic absent: create with the spec'd partitions / configs.
* **existing** — topic present with ≥ required partitions: skip.
* **upgrade** — topic present but with FEWER partitions than required:
  grow via ``alter_topic`` (aiokafka only supports growing).
* **never downgrade** — aiokafka itself raises if we try, so we don't.

On any failure, the caller should log but continue; ``/readyz`` will
subsequently return 503 until the defaults exist, giving operators a
visible signal without blocking the app.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.services.kafka.admin import KafkaAdminService
from src.services.kafka.schema import KafkaSchemaRegistry

logger = logging.getLogger(__name__)


# Minimal placeholder schema that accepts any JSON object. Real schemas are
# registered by Phase C task 6.1; this just seeds a v1 row so the producer
# side can start calling ``registry.get(...)`` without crashing.
_PLACEHOLDER_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
}


@dataclass(frozen=True)
class _TopicSpec:
    name: str
    partitions: int
    replication: int
    configs: dict[str, str]


# 30d, 90d, 14d, 180d in ms
_RETENTION_30D = "2592000000"
_RETENTION_90D = "7776000000"
_RETENTION_14D = "1209600000"
_RETENTION_180D = "15552000000"


# Dev defaults use replication=1 because the dev docker-compose runs a single
# broker. Production should override by setting KAFKA_DEFAULT_REPLICATION in
# settings (future work); for now we hard-code 1 and let ops upgrade it.
DEFAULT_TOPICS: tuple[_TopicSpec, ...] = (
    _TopicSpec(
        name="ops.agent.trajectory",
        partitions=6,
        replication=1,
        configs={"retention.ms": _RETENTION_30D},
    ),
    _TopicSpec(
        name="ops.agent.trajectory.dlq",
        partitions=3,
        replication=1,
        configs={"retention.ms": _RETENTION_90D},
    ),
    _TopicSpec(
        name="ops.agent.reflection",
        partitions=3,
        replication=1,
        configs={"retention.ms": _RETENTION_14D},
    ),
    _TopicSpec(
        name="ops.agent.promotion",
        partitions=3,
        replication=1,
        configs={"cleanup.policy": "compact"},
    ),
    _TopicSpec(
        name="ops.agent.feedback",
        partitions=3,
        replication=1,
        configs={"retention.ms": _RETENTION_180D},
    ),
)


@dataclass
class EnsureReport:
    created: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    upgraded: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": list(self.created),
            "existing": list(self.existing),
            "upgraded": list(self.upgraded),
            "errors": list(self.errors),
        }

    @property
    def ok(self) -> bool:
        return not self.errors


async def ensure_default_topics(
    admin: KafkaAdminService | None = None,
    *,
    schema_registry: KafkaSchemaRegistry | None = None,
    topics: tuple[_TopicSpec, ...] = DEFAULT_TOPICS,
) -> EnsureReport:
    """Create or upgrade every default topic. Never raises on per-topic failure."""
    report = EnsureReport()
    owns_admin = admin is None
    admin = admin or KafkaAdminService()
    registry = schema_registry or KafkaSchemaRegistry()

    if owns_admin:
        await admin.start()
    try:
        # One round-trip to learn the current state
        try:
            current = {t.name: t for t in await admin.list_topics(include_internal=False)}
        except Exception as exc:
            logger.exception("list_topics failed; ensure_default_topics aborting")
            report.errors.append(f"list_topics: {exc}")
            return report

        for spec in topics:
            try:
                existing_info = current.get(spec.name)
                if existing_info is None:
                    await admin.create_topic(
                        spec.name,
                        partitions=spec.partitions,
                        replication_factor=spec.replication,
                        configs=dict(spec.configs),
                    )
                    report.created.append(spec.name)
                    logger.info("ensure: created topic %s", spec.name)
                elif existing_info.partitions < spec.partitions:
                    await admin.alter_topic(
                        spec.name, partitions=spec.partitions
                    )
                    report.upgraded.append(spec.name)
                    logger.info(
                        "ensure: upgraded topic %s partitions %d→%d",
                        spec.name,
                        existing_info.partitions,
                        spec.partitions,
                    )
                else:
                    report.existing.append(spec.name)
            except Exception as exc:
                logger.exception("ensure: topic %s failed", spec.name)
                report.errors.append(f"{spec.name}: {exc}")

            # Placeholder schema v1 — best-effort; a DB outage here should not
            # block topic creation above.
            try:
                current_schema = await registry.get(spec.name, version=1)
                if current_schema is None:
                    await registry.register(
                        topic=spec.name,
                        version=1,
                        schema=_PLACEHOLDER_SCHEMA,
                        description="placeholder; populated by Phase C task 6.1",
                    )
            except Exception as exc:
                logger.warning(
                    "ensure: schema seed for %s failed: %s", spec.name, exc
                )
    finally:
        if owns_admin:
            try:
                await admin.close()
            except Exception:  # pragma: no cover
                logger.exception("admin.close failed")
    return report


async def default_topics_present(admin: KafkaAdminService | None = None) -> bool:
    """Return True iff every default topic exists on the broker.

    Used by ``/readyz`` to signal liveness of the Kafka management surface.
    Never raises; a broker outage returns False.
    """
    owns_admin = admin is None
    admin = admin or KafkaAdminService()
    try:
        if owns_admin:
            await admin.start()
        try:
            names = {t.name for t in await admin.list_topics(include_internal=False)}
        except Exception:
            logger.exception("default_topics_present: list_topics failed")
            return False
        return all(spec.name in names for spec in DEFAULT_TOPICS)
    finally:
        if owns_admin:
            try:
                await admin.close()
            except Exception:  # pragma: no cover
                pass


__all__ = [
    "DEFAULT_TOPICS",
    "EnsureReport",
    "default_topics_present",
    "ensure_default_topics",
]
