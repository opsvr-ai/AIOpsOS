"""Workflow context from external systems (ITSM/OA/BPM) — reserved for future use."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import Base


class WorkflowContext(Base):
    __tablename__ = "workflow_contexts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=True
    )
    space_id = Column(
        UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True
    )
    source_system = Column(String(32), nullable=False)  # itsm, oa, bpm
    workflow_id = Column(String(256), nullable=False)
    status = Column(String(32), default="pending")
    title = Column(String(512), nullable=True)
    payload = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
