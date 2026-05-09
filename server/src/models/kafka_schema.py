"""SQLAlchemy model for the ``kafka_topic_schemas`` table.

Matches migration ``202605041800_add_trajectory_and_evolution_tables.py``.
Backs the local JSON-schema registry used by KafkaSchemaRegistry (see
design.md § Kafka Management Surface).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class KafkaTopicSchema(Base):
    """One JSON-schema version for a Kafka topic.

    ``(topic, version)`` is unique so producers can pin a payload to a
    specific schema revision. The migration creates this uniqueness via
    a named constraint; we mirror it in ``__table_args__`` so ORM
    inserts that violate it fail in the same way as raw DDL.
    """

    __tablename__ = "kafka_topic_schemas"
    __table_args__ = (
        UniqueConstraint(
            "topic",
            "version",
            name="uq_kafka_topic_schemas_topic_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    topic: Mapped[str] = mapped_column(String(256), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema: Mapped[dict] = mapped_column("schema", JSONB, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
