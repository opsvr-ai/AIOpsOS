"""One-shot seeds for :class:`KafkaSchemaRegistry`.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 6.1 / R-5.6.

``ensure.py`` already seeds a placeholder v1 for every default topic so
producers don't crash on ``registry.get(...)``. This module upgrades
those placeholders to the real JSON schemas as each Phase lands its
real event type.

Current seeds:

* ``ops.agent.trajectory`` → TrajectoryEvent.v1

Future phases will extend ``SCHEMA_SEEDS`` with reflection / promotion /
feedback schemas.
"""
from __future__ import annotations

import logging
from typing import Any

from src.schemas.trajectory import TrajectoryEvent
from src.services.kafka.schema import KafkaSchemaRegistry

logger = logging.getLogger(__name__)


# ``(topic, version, schema, description)`` tuples.
SCHEMA_SEEDS: list[tuple[str, int, dict[str, Any], str]] = [
    (
        "ops.agent.trajectory",
        1,
        TrajectoryEvent.json_schema(),
        "Agent runtime trajectory event v1 (Phase C).",
    ),
]


async def register_trajectory_schema(
    registry: KafkaSchemaRegistry | None = None,
) -> None:
    """Register / upsert every seed in :data:`SCHEMA_SEEDS`."""
    registry = registry or KafkaSchemaRegistry()
    for topic, version, schema, description in SCHEMA_SEEDS:
        try:
            await registry.register(
                topic=topic,
                version=version,
                schema=schema,
                description=description,
            )
            logger.info(
                "schema registry: seeded %s v%d (%s)", topic, version, description
            )
        except Exception:
            logger.exception(
                "schema registry: failed to seed %s v%d", topic, version
            )


__all__ = ["SCHEMA_SEEDS", "register_trajectory_schema"]
