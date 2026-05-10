"""Pydantic schemas for collaboration sessions and related entities.

This module defines the request/response schemas for emergency collaboration
workflows, including sessions, messages, and AI-generated recommendations.

Requirements:
- 6.2: Auto-create collaboration session when scenario with collaboration enabled is triggered
- 6.3: Generate unique identifier for collaboration session
- 6.4: Record creation time, trigger scenario, trigger reason
- 9.4: Record source channel and original message ID for each synced message
- 11.1: Generate next-step recommendations based on progress analysis
- 11.4: Provide priority and estimated impact for each recommendation
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


# ============================================================================
# Collaboration Session Schemas
# ============================================================================


class CollaborationSessionCreate(BaseModel):
    """Schema for creating a new collaboration session.

    Requirements:
    - 6.2: Auto-create collaboration session when scenario triggered
    - 6.3: Generate unique identifier (handled by backend)
    - 6.4: Record trigger scenario and trigger reason
    """

    scenario_id: str = Field(..., description="ID of the scenario that triggered this session")
    trigger_reason: str | None = Field(
        None, description="Description of why the session was triggered"
    )
    space_id: str | None = Field(None, description="Optional workspace scope")


class CollaborationSessionUpdate(BaseModel):
    """Schema for updating a collaboration session."""

    status: Literal["created", "active", "resolved", "closed"] | None = Field(
        None, description="Session status"
    )
    group_chat_id: str | None = Field(None, description="External group chat ID")
    group_chat_name: str | None = Field(None, description="Name of the associated group chat")
    progress_summary: dict | None = Field(None, description="Structured progress information")
    summary_report: dict | None = Field(None, description="Final summary report")


class CollaborationSessionBase(BaseModel):
    """Base schema for collaboration session responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scenario_id: uuid.UUID
    status: str
    trigger_reason: str | None = None
    group_chat_id: str | None = None
    group_chat_name: str | None = None
    progress_summary: dict = Field(default_factory=dict)
    config_snapshot: dict = Field(default_factory=dict)
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    summary_report: dict | None = None
    space_id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_serializer("id", "scenario_id")
    def serialize_uuid(self, value: uuid.UUID) -> str:
        return str(value)

    @field_serializer("space_id")
    def serialize_space_id(self, value: uuid.UUID | None) -> str | None:
        return str(value) if value else None


class CollaborationSessionResponse(CollaborationSessionBase):
    """Full response schema for collaboration session with messages and recommendations.

    Requirements:
    - 6.3: Include unique identifier
    - 6.4: Include creation time, trigger scenario, trigger reason
    - 6.5: Include status (created, active, resolved, closed)
    """

    messages: list["CollaborationMessageResponse"] = Field(default_factory=list)
    recommendations: list["CollaborationRecommendationResponse"] = Field(default_factory=list)


class CollaborationSessionListResponse(CollaborationSessionBase):
    """Lightweight response schema for session list queries (without nested messages/recommendations)."""

    pass


class CollaborationSessionListOut(BaseModel):
    """Paginated list response for collaboration sessions."""

    items: list[CollaborationSessionListResponse]
    total: int
    page: int = 1
    page_size: int = 50


# ============================================================================
# Collaboration Message Schemas
# ============================================================================


class CollaborationMessageCreate(BaseModel):
    """Schema for creating a new collaboration message.

    Requirements:
    - 9.4: Record source channel and original message ID for each synced message
    """

    session_id: str = Field(..., description="ID of the parent collaboration session")
    source_channel: Literal["wecom", "email", "system", "api"] = Field(
        ..., description="Origin channel of the message"
    )
    source_message_id: str | None = Field(
        None, description="Original message ID from the source channel"
    )
    sender_id: str | None = Field(None, description="Identifier of the message sender")
    sender_name: str | None = Field(None, description="Display name of the sender")
    content: str = Field(..., description="Message content text")
    message_type: Literal["text", "markdown", "event"] = Field(
        "text", description="Type of message"
    )
    metadata: dict = Field(default_factory=dict, description="Additional metadata")


class CollaborationMessageResponse(BaseModel):
    """Response schema for collaboration message.

    Requirements:
    - 9.4: Include source channel and original message ID
    - 9.5: Support message format conversion (message_type field)
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    source_channel: str
    source_message_id: str | None = None
    sender_id: str | None = None
    sender_name: str | None = None
    content: str
    message_type: str
    msg_metadata: dict = Field(default_factory=dict, alias="metadata")
    synced_to: list[str] = Field(default_factory=list)
    created_at: datetime | None = None

    @field_serializer("id", "session_id")
    def serialize_uuid(self, value: uuid.UUID) -> str:
        return str(value)

    @field_validator("msg_metadata", mode="before")
    @classmethod
    def handle_metadata_alias(cls, v: dict | None) -> dict:
        """Handle both 'metadata' and 'msg_metadata' field names."""
        return v or {}


class CollaborationMessageListOut(BaseModel):
    """Paginated list response for collaboration messages."""

    items: list[CollaborationMessageResponse]
    total: int
    page: int = 1
    page_size: int = 50


# ============================================================================
# Collaboration Recommendation Schemas
# ============================================================================


class CollaborationRecommendationCreate(BaseModel):
    """Schema for creating a new recommendation (internal use)."""

    session_id: str = Field(..., description="ID of the parent collaboration session")
    content: str = Field(..., description="The recommendation text content")
    priority: int = Field(
        0, ge=0, le=2, description="Priority level: 0=low, 1=medium, 2=high"
    )
    estimated_impact: str | None = Field(
        None, description="Description of the expected impact if adopted"
    )
    reference_docs: list[dict] = Field(
        default_factory=list, description="Referenced knowledge documents"
    )


class CollaborationRecommendationUpdate(BaseModel):
    """Schema for updating a recommendation (e.g., providing feedback).

    Requirements:
    - 11.5: Support user feedback (adopt, ignore, modify)
    """

    status: Literal["pending", "adopted", "ignored", "modified"] | None = Field(
        None, description="Current status of the recommendation"
    )
    feedback: str | None = Field(None, description="User feedback text")


class CollaborationRecommendationResponse(BaseModel):
    """Response schema for collaboration recommendation.

    Requirements:
    - 11.1: Generate next-step recommendations based on progress analysis
    - 11.4: Provide priority and estimated impact for each recommendation
    - 11.5: Support user feedback (adopt, ignore, modify)
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    content: str
    priority: int = Field(description="Priority level: 0=low, 1=medium, 2=high")
    estimated_impact: str | None = None
    reference_docs: list[dict] = Field(default_factory=list)
    status: str = Field(description="Status: pending, adopted, ignored, modified")
    feedback: str | None = None
    adopted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_serializer("id", "session_id")
    def serialize_uuid(self, value: uuid.UUID) -> str:
        return str(value)


class CollaborationRecommendationListOut(BaseModel):
    """Paginated list response for collaboration recommendations."""

    items: list[CollaborationRecommendationResponse]
    total: int
    page: int = 1
    page_size: int = 50


# ============================================================================
# Progress Analysis Schemas
# ============================================================================


class ProgressAnalysisRequest(BaseModel):
    """Request schema for triggering progress analysis."""

    session_id: str = Field(..., description="ID of the collaboration session to analyze")


class ProgressAnalysisResponse(BaseModel):
    """Response schema for progress analysis results.

    Requirements:
    - 10.2: Identify key events (problem confirmation, solution discussion, operation execution, result verification)
    - 10.3: Generate progress summary (completed steps, current phase, pending items)
    - 10.4: Calculate processing duration and phase timing
    """

    session_id: str
    current_phase: str | None = Field(None, description="Current phase of the collaboration")
    completed_steps: list[str] = Field(
        default_factory=list, description="List of completed steps"
    )
    pending_items: list[str] = Field(
        default_factory=list, description="List of pending items"
    )
    duration_minutes: int = Field(0, description="Total duration in minutes")
    key_events: list[dict] = Field(
        default_factory=list,
        description="Identified key events with timestamps and descriptions",
    )
    last_analysis_at: datetime | None = None


# ============================================================================
# Session Report Schemas
# ============================================================================


class CollaborationReportRequest(BaseModel):
    """Request schema for generating collaboration session report."""

    session_id: str = Field(..., description="ID of the collaboration session")
    format: Literal["json", "markdown", "pdf"] = Field(
        "json", description="Output format for the report"
    )


class CollaborationReportResponse(BaseModel):
    """Response schema for collaboration session report.

    Requirements:
    - 6.8: Generate collaboration summary report when session is closed
    - 12.4: Support exporting collaboration session report
    """

    session_id: str
    scenario_name: str
    status: str
    trigger_reason: str | None = None
    created_at: datetime | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    duration_minutes: int = 0
    message_count: int = 0
    recommendation_count: int = 0
    adopted_recommendations: int = 0
    progress_summary: dict = Field(default_factory=dict)
    key_events: list[dict] = Field(default_factory=list)
    participants: list[str] = Field(default_factory=list)


# ============================================================================
# Search Schemas
# ============================================================================


class CollaborationSearchParams(BaseModel):
    """Query parameters for searching collaboration sessions.

    Requirements:
    - 12.1: Support pagination
    - 12.2: Support filtering by status, time range, scenario
    - 12.7: Support keyword search in message content
    """

    status: Literal["created", "active", "resolved", "closed"] | None = Field(
        None, description="Filter by session status"
    )
    scenario_id: str | None = Field(None, description="Filter by scenario ID")
    space_id: str | None = Field(None, description="Filter by workspace ID")
    start_time: datetime | None = Field(None, description="Filter by creation time (start)")
    end_time: datetime | None = Field(None, description="Filter by creation time (end)")
    keyword: str | None = Field(None, description="Search keyword in message content")
    page: int = Field(1, ge=1, description="Page number")
    page_size: int = Field(50, ge=1, le=100, description="Items per page")


# ============================================================================
# Message Sync Failure Schemas
# ============================================================================


class MessageSyncFailureResponse(BaseModel):
    """Response schema for message sync failure record.

    Requirements:
    - 9.6: Record sync failure reason and support manual retry
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    message_id: uuid.UUID
    session_id: uuid.UUID
    target_channel: str = Field(description="Target channel (wecom, email)")
    error_reason: str = Field(description="Description of why the sync failed")
    error_code: str | None = Field(None, description="Error code from target channel")
    error_details: dict = Field(default_factory=dict, description="Additional error details")
    retry_count: int = Field(description="Number of retry attempts made")
    max_retries: int = Field(description="Maximum number of retries allowed")
    status: str = Field(description="Status: pending, retrying, resolved, abandoned")
    last_retry_at: datetime | None = Field(None, description="Timestamp of last retry attempt")
    resolved_at: datetime | None = Field(None, description="Timestamp when sync was resolved")
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_serializer("id", "message_id", "session_id")
    def serialize_uuid(self, value: uuid.UUID) -> str:
        return str(value)


class MessageSyncFailureListOut(BaseModel):
    """Paginated list response for message sync failures."""

    items: list[MessageSyncFailureResponse]
    total: int
    page: int = 1
    page_size: int = 50


class MessageSyncRetryRequest(BaseModel):
    """Request schema for retrying a failed message sync.

    Requirements:
    - 9.6: Support manual retry for failed syncs
    """

    failure_id: str = Field(..., description="ID of the failure record to retry")


class MessageSyncRetryAllRequest(BaseModel):
    """Request schema for retrying all failed syncs for a session.

    Requirements:
    - 9.6: Support manual retry for failed syncs
    """

    session_id: str = Field(..., description="ID of the collaboration session")


class MessageSyncStatusResponse(BaseModel):
    """Response schema for message sync status.

    Requirements:
    - 9.6: Record sync failure reason
    - 9.7: Avoid duplicate sync through message ID deduplication
    """

    session_id: str
    total_messages: int = Field(description="Total number of messages in session")
    from_wecom: int = Field(description="Messages received from WeChat Work")
    from_other_channels: int = Field(description="Messages from other channels")
    synced_to_wecom: int = Field(description="Messages synced to WeChat Work")
    pending_sync_to_wecom: int = Field(description="Messages pending sync to WeChat Work")
    pending_failures: int = Field(description="Number of pending sync failures")


# Update forward references
CollaborationSessionResponse.model_rebuild()
