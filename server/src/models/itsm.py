"""ITSM ticket model — incident/change/problem/request workflow tracking with alert correlation."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class ItsmTicket(Base, TimestampMixin):
    __tablename__ = "itsm_tickets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    external_id: Mapped[str] = mapped_column(
        String(256), unique=True, nullable=False, index=True
    )
    ticket_type: Mapped[str] = mapped_column(
        String(32), index=True
    )
    title: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(
        String(32), index=True
    )
    priority: Mapped[str] = mapped_column(String(16))
    affected_service: Mapped[str] = mapped_column(
        String(128), index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    raw_data: Mapped[dict] = mapped_column(JSONB, default=dict)
    linked_alert_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True
    )
    datasource_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("datasources.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spaces.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    __table_args__ = (
        Index("ix_itsm_tickets_service_created", "affected_service", "created_at"),
        Index("ix_itsm_tickets_type_status", "ticket_type", "status"),
    )
