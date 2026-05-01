"""Log event model — hourly partitioned table with 30-min TTL window."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class LogEvent(Base):
    """Partitioned log events table.

    Partition key: ingested_at (hourly range partitions managed by pg_partman or manual DDL).
    TTL: 30 minutes via pg_cron DELETE WHERE ingested_at < NOW() - INTERVAL '30 minutes'.
    """

    __tablename__ = "log_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )
    timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    service: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    host: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    level: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    datasource_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("datasources.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    __table_args__ = (
        Index("ix_log_events_service_level_ingested", "service", "level", "ingested_at"),
        Index("ix_log_events_trace_id", "trace_id"),
    )
