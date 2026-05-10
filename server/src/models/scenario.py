"""Scenario execution models for tracking scenario runs and their results."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class ScenarioExecution(Base, TimestampMixin):
    """场景执行记录

    Tracks individual executions of scenarios, including their status,
    parameters, results, and logs. Each execution can optionally be
    associated with a collaboration session for emergency response workflows.

    Attributes:
        id: Unique identifier for the execution
        scenario_id: Reference to the scenario being executed
        trigger_type: How the execution was triggered (manual, schedule, trigger_rule)
        trigger_source: Description of the trigger source
        status: Current execution status (pending, running, completed, failed, timeout)
        params: Input parameters for this execution
        result: Structured execution result including output, recommendations, metrics
        logs: List of log entries with timestamp, level, and message
        started_at: When execution actually started
        completed_at: When execution finished
        collaboration_session_id: Optional link to emergency collaboration session
        space_id: Optional workspace scope
    """

    __tablename__ = "scenario_executions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False
    )
    trigger_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # manual | schedule | trigger_rule
    trigger_source: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )  # Description of trigger source
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )  # pending | running | completed | failed | timeout
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    # result structure:
    # {
    #   "output": "执行输出内容",
    #   "recommendations": ["建议1", "建议2"],
    #   "metrics": {"duration_ms": 1234, "steps_completed": 5}
    # }
    logs: Mapped[list] = mapped_column(JSONB, default=list)
    # logs structure: [{"timestamp": "...", "level": "info", "message": "..."}]
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    collaboration_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("collaboration_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    scenario: Mapped["Scenario"] = relationship(back_populates="executions")
    collaboration_session: Mapped["CollaborationSession | None"] = relationship(
        back_populates="execution"
    )


# Type hints for forward references
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.models.agent import Scenario
    from src.models.collaboration import CollaborationSession
