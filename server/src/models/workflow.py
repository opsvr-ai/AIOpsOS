"""Workflow context from external systems (ITSM/OA/BPM) — tracks ITSM ticket linkage and script execution."""

import uuid
from datetime import UTC, datetime

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
    workflow_id = Column(String(256), nullable=False)  # external ticket ID
    action_type = Column(String(32), default="create")  # create / update / close / escalate / execute
    status = Column(String(32), default="pending")
    title = Column(String(512), nullable=True)
    payload = Column(JSONB, default=dict)
    execute_script = Column(Text, nullable=True)
    execution_log = Column(Text, nullable=True)
    linked_ticket_id = Column(
        UUID(as_uuid=True), ForeignKey("itsm_tickets.id", ondelete="SET NULL"), nullable=True
    )
    linked_session_id = Column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
