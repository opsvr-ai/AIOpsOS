"""Collaboration models for emergency response workflows.

This module contains models for managing emergency collaboration sessions,
including message tracking and AI-generated recommendations.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class CollaborationSession(Base, TimestampMixin):
    """应急协同会话

    Represents an emergency collaboration session that is triggered when
    a scenario with collaboration enabled is executed. Tracks the full
    lifecycle from creation through resolution and closure.

    Attributes:
        id: Unique identifier for the session
        scenario_id: Reference to the triggering scenario
        status: Current session status (created, active, resolved, closed)
        trigger_reason: Description of why the session was triggered
        group_chat_id: External group chat ID (e.g., WeCom group)
        group_chat_name: Name of the associated group chat
        progress_summary: Structured progress information including current phase,
            completed steps, pending items, and timing metrics
        config_snapshot: Snapshot of collaboration config at session creation
        resolved_at: When the issue was marked as resolved
        closed_at: When the session was formally closed
        summary_report: Final summary report generated at closure
        space_id: Optional workspace scope

    Relationships:
        scenario: The scenario that triggered this session
        execution: The scenario execution associated with this session
        messages: All messages in this collaboration session
        recommendations: AI-generated recommendations for this session
    """

    __tablename__ = "collaboration_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="created"
    )  # created | active | resolved | closed
    trigger_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Group chat information
    group_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    group_chat_name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Progress information
    progress_summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    # progress_summary structure:
    # {
    #   "current_phase": "investigation",
    #   "completed_steps": ["问题确认", "初步排查"],
    #   "pending_items": ["根因分析", "修复验证"],
    #   "duration_minutes": 45,
    #   "last_analysis_at": "2024-01-01T12:00:00Z"
    # }

    # Configuration snapshot at creation time
    config_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Lifecycle timestamps
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    summary_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    scenario: Mapped["Scenario"] = relationship()
    execution: Mapped["ScenarioExecution | None"] = relationship(
        back_populates="collaboration_session"
    )
    messages: Mapped[list["CollaborationMessage"]] = relationship(
        back_populates="session", order_by="CollaborationMessage.created_at"
    )
    recommendations: Mapped[list["CollaborationRecommendation"]] = relationship(
        back_populates="session", order_by="CollaborationRecommendation.created_at.desc()"
    )
    analysis_records: Mapped[list["ProgressAnalysisRecord"]] = relationship(
        back_populates="session", order_by="ProgressAnalysisRecord.created_at.desc()"
    )


class CollaborationMessage(Base):
    """协同会话消息记录

    Records all messages exchanged during a collaboration session,
    including messages from different channels (WeCom, email, system, API).
    Supports message synchronization tracking across channels.

    Attributes:
        id: Unique identifier for the message
        session_id: Reference to the parent collaboration session
        source_channel: Origin channel of the message (wecom, email, system, api)
        source_message_id: Original message ID from the source channel
        sender_id: Identifier of the message sender
        sender_name: Display name of the sender
        content: Message content text
        message_type: Type of message (text, markdown, event)
        msg_metadata: Additional metadata about the message
        synced_to: List of channels this message has been synced to
        created_at: When the message was created/received

    Relationships:
        session: The collaboration session this message belongs to

    Requirements:
        - 9.4: Record source channel and original message ID for each synced message
        - 9.5: Support message format conversion for different channel requirements
    """

    __tablename__ = "collaboration_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("collaboration_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_channel: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # wecom | email | system | api
    source_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sender_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sender_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="text"
    )  # text | markdown | event
    msg_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    synced_to: Mapped[list] = mapped_column(JSONB, default=list)  # ["wecom", "email"]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    # Relationships
    session: Mapped["CollaborationSession"] = relationship(back_populates="messages")


class CollaborationRecommendation(Base, TimestampMixin):
    """协同会话建议记录

    Stores AI-generated recommendations for collaboration sessions.
    Each recommendation includes priority, estimated impact, and
    supports user feedback tracking.

    Attributes:
        id: Unique identifier for the recommendation
        session_id: Reference to the parent collaboration session
        content: The recommendation text content
        priority: Priority level (0=low, 1=medium, 2=high)
        estimated_impact: Description of the expected impact if adopted
        reference_docs: List of referenced knowledge documents
        status: Current status (pending, adopted, ignored, modified)
        feedback: User feedback text if provided
        adopted_at: When the recommendation was adopted

    Relationships:
        session: The collaboration session this recommendation belongs to

    Requirements:
        - 11.1: Generate next-step recommendations based on progress analysis
        - 11.4: Provide priority and estimated impact for each recommendation
        - 11.5: Support user feedback (adopt, ignore, modify)
    """

    __tablename__ = "collaboration_recommendations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("collaboration_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )  # 0=low, 1=medium, 2=high
    estimated_impact: Mapped[str | None] = mapped_column(String(256), nullable=True)
    reference_docs: Mapped[list] = mapped_column(
        JSONB, default=list
    )  # [{"doc_id": "...", "title": "..."}]
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )  # pending | adopted | ignored | modified
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    adopted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    session: Mapped["CollaborationSession"] = relationship(
        back_populates="recommendations"
    )


class ProgressAnalysisRecord(Base, TimestampMixin):
    """进度分析记录

    Stores the history of progress analysis results for collaboration sessions.
    Each record represents a single analysis execution, enabling historical
    tracking and trend analysis of session progress over time.

    Attributes:
        id: Unique identifier for the analysis record
        session_id: Reference to the collaboration session
        current_phase: The phase identified at analysis time
        completed_steps: List of completed steps at analysis time
        pending_items: List of pending items at analysis time
        key_events: List of key events identified during analysis
        phase_metrics: Timing metrics for each phase
        total_duration_minutes: Total session duration at analysis time
        message_count: Number of messages analyzed
        analysis_type: Type of analysis (manual, automatic, force_refresh)
        trigger_source: What triggered the analysis (api, scheduler, system)
        raw_llm_output: Raw output from LLM analysis (if applicable)
        error: Error message if analysis failed
        analysis_config: Configuration used for this analysis

    Relationships:
        session: The collaboration session this analysis belongs to

    Requirements:
        - 10.5: Support manual trigger of progress analysis
        - 10.6: Support configurable automatic analysis interval
        - 10.7: Update collaboration session progress status when analysis completes
        - 10.8: Store analysis results in collaboration session records
    """

    __tablename__ = "progress_analysis_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("collaboration_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    current_phase: Mapped[str] = mapped_column(String(32), nullable=False)
    completed_steps: Mapped[list] = mapped_column(JSONB, default=list)
    pending_items: Mapped[list] = mapped_column(JSONB, default=list)
    key_events: Mapped[list] = mapped_column(JSONB, default=list)
    # key_events structure:
    # [
    #   {
    #     "event_type": "problem_confirmed",
    #     "description": "确认了数据库连接超时问题",
    #     "timestamp": "2024-01-01T12:00:00Z",
    #     "message_id": "uuid",
    #     "confidence": 0.9
    #   }
    # ]
    phase_metrics: Mapped[list] = mapped_column(JSONB, default=list)
    # phase_metrics structure:
    # [
    #   {
    #     "phase": "investigation",
    #     "started_at": "2024-01-01T12:00:00Z",
    #     "ended_at": "2024-01-01T12:30:00Z",
    #     "duration_minutes": 30,
    #     "message_count": 15
    #   }
    # ]
    total_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    analysis_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="manual"
    )  # manual | automatic | force_refresh
    trigger_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="api"
    )  # api | scheduler | system
    raw_llm_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    # analysis_config structure:
    # {
    #   "include_history": true,
    #   "force_refresh": false,
    #   "analysis_interval": 300
    # }

    # Relationships
    session: Mapped["CollaborationSession"] = relationship(
        back_populates="analysis_records"
    )


class MessageSyncFailure(Base, TimestampMixin):
    """消息同步失败记录

    Tracks failed message synchronization attempts for manual retry support.
    Records the failure reason, retry count, and allows manual retry operations.

    Attributes:
        id: Unique identifier for the failure record
        message_id: Reference to the collaboration message that failed to sync
        session_id: Reference to the collaboration session
        target_channel: The channel the message failed to sync to (wecom, email)
        error_reason: Description of why the sync failed
        error_code: Optional error code from the target channel
        retry_count: Number of retry attempts made
        max_retries: Maximum number of retries allowed
        status: Current status (pending, retrying, resolved, abandoned)
        last_retry_at: Timestamp of the last retry attempt
        resolved_at: Timestamp when the sync was successfully resolved

    Relationships:
        message: The collaboration message that failed to sync
        session: The collaboration session

    Requirements:
        - 9.6: Record sync failure reason and support manual retry
        - 9.7: Avoid duplicate sync through message ID deduplication
    """

    __tablename__ = "message_sync_failures"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("collaboration_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("collaboration_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_channel: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # wecom | email
    error_reason: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_details: Mapped[dict] = mapped_column(JSONB, default=dict)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )  # pending | retrying | resolved | abandoned
    last_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    message: Mapped["CollaborationMessage"] = relationship()
    session: Mapped["CollaborationSession"] = relationship()


# Type hints for forward references
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.models.agent import Scenario
    from src.models.scenario import ScenarioExecution
