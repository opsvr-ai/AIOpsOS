"""Pydantic schemas for scenario operations and templates.

This module defines the request/response schemas for:
- Scenario CRUD operations
- Scenario execution tracking
- Scenario templates (fault_isolation, health_inspection, capacity_prediction, alert_analysis)
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


# =============================================================================
# Enums
# =============================================================================


class ScenarioType(str, Enum):
    """Scenario execution type."""

    COMMAND = "command"
    NATURAL_LANGUAGE = "natural_language"
    HYBRID = "hybrid"


class ExecutionStatus(str, Enum):
    """Scenario execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class TriggerType(str, Enum):
    """How a scenario execution was triggered."""

    MANUAL = "manual"
    SCHEDULE = "schedule"
    TRIGGER_RULE = "trigger_rule"


class TemplateId(str, Enum):
    """Built-in scenario template identifiers.

    Requirements 2.1: THE Scenario_Template_System SHALL provide the following
    built-in templates: fault_isolation, health_inspection, capacity_prediction,
    alert_analysis.
    """

    FAULT_ISOLATION = "fault_isolation"
    HEALTH_INSPECTION = "health_inspection"
    CAPACITY_PREDICTION = "capacity_prediction"
    ALERT_ANALYSIS = "alert_analysis"


# =============================================================================
# Collaboration Config Schema
# =============================================================================


class CollaborationConfig(BaseModel):
    """Configuration for emergency collaboration when scenario is triggered."""

    auto_create_group: bool = False
    group_name_template: str = "[应急] {scenario_name} - {timestamp}"
    group_members: list[str] = Field(default_factory=list)
    group_owner: str | None = None
    send_email: bool = False
    email_recipients: list[str] = Field(default_factory=list)
    email_template_id: str | None = None


# =============================================================================
# Scenario CRUD Schemas
# =============================================================================


class ScenarioCreate(BaseModel):
    """Schema for creating a new scenario.

    Requirements 1.1-1.6: Supports three scenario types with appropriate
    field validation based on type.
    """

    name: str = Field(..., min_length=1, max_length=256)
    description: str | None = None
    scenario_type: ScenarioType = ScenarioType.COMMAND
    trigger_command: str | None = Field(None, max_length=128)
    nl_prompt: str | None = None
    params_schema: dict[str, Any] = Field(default_factory=dict)
    execution_timeout: int = Field(default=300, ge=1, le=3600)
    is_active: bool = True
    enable_collaboration: bool = False
    collaboration_config: CollaborationConfig | None = None
    template_id: str | None = None
    tool_ids: list[str] = Field(default_factory=list)
    agent_ids: list[str] = Field(default_factory=list)
    knowledge_doc_ids: list[str] = Field(default_factory=list)
    channel_ids: list[str] = Field(default_factory=list)
    space_id: str | None = None

    @model_validator(mode="after")
    def validate_type_fields(self) -> "ScenarioCreate":
        """Validate that required fields are present based on scenario type.

        Requirements 1.2, 1.3, 1.4, 1.5, 1.6: Validates type-field consistency.
        """
        if self.scenario_type == ScenarioType.COMMAND:
            if not self.trigger_command:
                raise ValueError(
                    "trigger_command is required for command type scenarios"
                )
            if not self.trigger_command.startswith("/"):
                raise ValueError("trigger_command must start with '/'")
        elif self.scenario_type == ScenarioType.NATURAL_LANGUAGE:
            if not self.nl_prompt:
                raise ValueError(
                    "nl_prompt is required for natural_language type scenarios"
                )
        elif self.scenario_type == ScenarioType.HYBRID:
            # Hybrid supports both, at least one should be provided
            if not self.trigger_command and not self.nl_prompt:
                raise ValueError(
                    "At least one of trigger_command or nl_prompt is required for hybrid type scenarios"
                )
            if self.trigger_command and not self.trigger_command.startswith("/"):
                raise ValueError("trigger_command must start with '/'")
        return self


class ScenarioUpdate(BaseModel):
    """Schema for updating an existing scenario."""

    name: str | None = Field(None, min_length=1, max_length=256)
    description: str | None = None
    scenario_type: ScenarioType | None = None
    trigger_command: str | None = Field(None, max_length=128)
    nl_prompt: str | None = None
    params_schema: dict[str, Any] | None = None
    execution_timeout: int | None = Field(None, ge=1, le=3600)
    is_active: bool | None = None
    enable_collaboration: bool | None = None
    collaboration_config: CollaborationConfig | None = None
    tool_ids: list[str] | None = None
    agent_ids: list[str] | None = None
    knowledge_doc_ids: list[str] | None = None
    channel_ids: list[str] | None = None
    space_id: str | None = None


class ScenarioResponse(BaseModel):
    """Schema for scenario response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None = None
    scenario_type: str
    trigger_command: str | None = None
    nl_prompt: str | None = None
    params_schema: dict[str, Any]
    execution_timeout: int
    is_active: bool
    enable_collaboration: bool
    collaboration_config: dict[str, Any]
    template_id: str | None = None
    space_id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_serializer("id")
    def serialize_id(self, value: uuid.UUID) -> str:
        return str(value)

    @field_serializer("space_id")
    def serialize_space_id(self, value: uuid.UUID | None) -> str | None:
        return str(value) if value else None


class ScenarioDetailResponse(ScenarioResponse):
    """Schema for detailed scenario response with related resources."""

    tools: list[dict[str, Any]] = Field(default_factory=list)
    agents: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_docs: list[dict[str, Any]] = Field(default_factory=list)
    notification_channels: list[dict[str, Any]] = Field(default_factory=list)


# =============================================================================
# Scenario Execution Schemas
# =============================================================================


class ScenarioExecutionCreate(BaseModel):
    """Schema for creating a scenario execution record.

    Requirements 5.1, 5.2: Supports manual and automatic trigger types.
    """

    scenario_id: str
    trigger_type: TriggerType = TriggerType.MANUAL
    trigger_source: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    space_id: str | None = None

    @field_validator("scenario_id")
    @classmethod
    def validate_scenario_id(cls, v: str) -> str:
        """Validate that scenario_id is a valid UUID string."""
        try:
            uuid.UUID(v)
        except ValueError:
            raise ValueError("scenario_id must be a valid UUID")
        return v


class ScenarioExecutionResponse(BaseModel):
    """Schema for scenario execution response.

    Requirements 5.3, 5.7, 5.8, 5.9: Includes status, logs, and results.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scenario_id: uuid.UUID
    trigger_type: str
    trigger_source: str | None = None
    status: str
    params: dict[str, Any]
    result: dict[str, Any]
    logs: list[dict[str, Any]]
    started_at: datetime | None = None
    completed_at: datetime | None = None
    collaboration_session_id: uuid.UUID | None = None
    space_id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_serializer("id", "scenario_id")
    def serialize_uuid(self, value: uuid.UUID) -> str:
        return str(value)

    @field_serializer("collaboration_session_id", "space_id")
    def serialize_optional_uuid(self, value: uuid.UUID | None) -> str | None:
        return str(value) if value else None


class ExecutionLogEntry(BaseModel):
    """Schema for a single execution log entry."""

    timestamp: datetime
    level: Literal["debug", "info", "warning", "error"]
    message: str


class ExecutionResult(BaseModel):
    """Schema for structured execution result.

    Requirements 5.9: Structured execution result with output, recommendations, metrics.
    """

    output: str | None = None
    recommendations: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Scenario Template Schemas
# =============================================================================


class TemplateParamSchema(BaseModel):
    """Schema definition for a template parameter.

    Requirements 2.4: Default parameter schema definition for templates.
    """

    name: str
    type: Literal["string", "integer", "number", "boolean", "array", "object"]
    description: str | None = None
    required: bool = False
    default: Any = None
    enum: list[Any] | None = None


class RecommendedTool(BaseModel):
    """Recommended tool for a scenario template.

    Requirements 2.5: Templates associate recommended tools.
    """

    tool_id: str | None = None
    tool_name: str
    description: str | None = None
    category: str | None = None


class RecommendedAgent(BaseModel):
    """Recommended agent for a scenario template.

    Requirements 2.5: Templates associate recommended agents.
    """

    agent_id: str | None = None
    agent_name: str
    description: str | None = None
    agent_type: str | None = None


class ScenarioTemplateResponse(BaseModel):
    """Schema for scenario template response.

    Requirements 2.1, 2.4, 2.5: Provides template details including
    default parameters schema and recommended resources.

    Attributes:
        template_id: Unique template identifier (fault_isolation, health_inspection,
                    capacity_prediction, alert_analysis)
        name: Human-readable template name
        description: Detailed description of what the template does
        scenario_type: Default scenario type for this template
        default_trigger_command: Default trigger command (for command/hybrid types)
        default_nl_prompt: Default natural language prompt (for nl/hybrid types)
        default_params_schema: JSON Schema defining the template's parameters
        recommended_tools: List of tools recommended for this template
        recommended_agents: List of agents recommended for this template
        default_execution_timeout: Default timeout in seconds
        default_collaboration_config: Default collaboration settings
    """

    template_id: str = Field(
        ...,
        description="Template identifier: fault_isolation, health_inspection, capacity_prediction, alert_analysis",
    )
    name: str = Field(..., description="Human-readable template name")
    description: str = Field(..., description="Detailed template description")
    scenario_type: ScenarioType = Field(
        default=ScenarioType.HYBRID, description="Default scenario type"
    )
    default_trigger_command: str | None = Field(
        None, description="Default trigger command for command/hybrid types"
    )
    default_nl_prompt: str | None = Field(
        None, description="Default NL prompt for natural_language/hybrid types"
    )
    default_params_schema: list[TemplateParamSchema] = Field(
        default_factory=list, description="Default parameter definitions"
    )
    recommended_tools: list[RecommendedTool] = Field(
        default_factory=list, description="Recommended tools for this template"
    )
    recommended_agents: list[RecommendedAgent] = Field(
        default_factory=list, description="Recommended agents for this template"
    )
    default_execution_timeout: int = Field(
        default=300, description="Default execution timeout in seconds"
    )
    default_collaboration_config: CollaborationConfig | None = Field(
        None, description="Default collaboration configuration"
    )


class ScenarioFromTemplateCreate(BaseModel):
    """Schema for creating a scenario from a template.

    Requirements 2.2, 2.3, 2.6: Allows creating scenarios from templates
    with optional customization, recording template source.

    Attributes:
        template_id: The template to use as base
        name: Custom name for the scenario (required)
        description: Optional custom description (uses template default if not provided)
        trigger_command: Optional custom trigger command
        nl_prompt: Optional custom NL prompt
        params_schema: Optional custom params (merged with template defaults)
        execution_timeout: Optional custom timeout
        is_active: Whether the scenario is active
        enable_collaboration: Whether to enable emergency collaboration
        collaboration_config: Optional custom collaboration config
        tool_ids: Optional list of tool IDs to associate
        agent_ids: Optional list of agent IDs to associate
        knowledge_doc_ids: Optional list of knowledge document IDs
        channel_ids: Optional list of notification channel IDs
        space_id: Optional workspace scope
    """

    template_id: str = Field(
        ...,
        description="Template ID to create scenario from",
    )
    name: str = Field(..., min_length=1, max_length=256, description="Scenario name")
    description: str | None = Field(
        None, description="Custom description (uses template default if not provided)"
    )
    trigger_command: str | None = Field(
        None, max_length=128, description="Custom trigger command"
    )
    nl_prompt: str | None = Field(None, description="Custom NL prompt")
    params_schema: dict[str, Any] | None = Field(
        None, description="Custom params schema (merged with template defaults)"
    )
    execution_timeout: int | None = Field(
        None, ge=1, le=3600, description="Custom execution timeout"
    )
    is_active: bool = True
    enable_collaboration: bool | None = None
    collaboration_config: CollaborationConfig | None = None
    tool_ids: list[str] | None = Field(
        None, description="Tool IDs to associate (adds to template recommendations)"
    )
    agent_ids: list[str] | None = Field(
        None, description="Agent IDs to associate (adds to template recommendations)"
    )
    knowledge_doc_ids: list[str] = Field(default_factory=list)
    channel_ids: list[str] = Field(default_factory=list)
    space_id: str | None = None

    @field_validator("template_id")
    @classmethod
    def validate_template_id(cls, v: str) -> str:
        """Validate that template_id is one of the known templates."""
        valid_templates = {t.value for t in TemplateId}
        if v not in valid_templates:
            raise ValueError(
                f"Invalid template_id '{v}'. Must be one of: {', '.join(valid_templates)}"
            )
        return v


class ScenarioTemplateListResponse(BaseModel):
    """Schema for listing all available templates."""

    templates: list[ScenarioTemplateResponse]
    total: int


# =============================================================================
# Resource Association Schemas
# =============================================================================


class ResourceIdsRequest(BaseModel):
    """Schema for setting resource associations.

    Requirements 4.1-4.4: Supports associating scenarios with tools, agents,
    knowledge documents, and notification channels.
    """

    ids: list[str] = Field(
        default_factory=list,
        description="List of resource IDs to associate with the scenario",
    )

    @field_validator("ids")
    @classmethod
    def validate_ids(cls, v: list[str]) -> list[str]:
        """Validate that all IDs are valid UUIDs."""
        for id_str in v:
            try:
                uuid.UUID(id_str)
            except ValueError:
                raise ValueError(f"Invalid UUID format: {id_str}")
        return v


class ToolSummary(BaseModel):
    """Summary of a tool associated with a scenario."""

    id: str
    name: str
    type: str
    description: str | None = None
    category: str | None = None


class AgentSummary(BaseModel):
    """Summary of an agent associated with a scenario."""

    id: str
    name: str
    type: str
    agent_type: str | None = None


class KnowledgeDocSummary(BaseModel):
    """Summary of a knowledge document associated with a scenario."""

    id: str
    title: str
    doc_type: str


class NotificationChannelSummary(BaseModel):
    """Summary of a notification channel associated with a scenario."""

    id: str
    name: str
    channel_type: str


class ScenarioResourcesResponse(BaseModel):
    """Response schema for all resources associated with a scenario.

    Requirements 4.7: Provides API to query all associated resources.
    """

    scenario_id: str
    scenario_name: str
    tools: list[ToolSummary] = Field(default_factory=list)
    agents: list[AgentSummary] = Field(default_factory=list)
    knowledge_docs: list[KnowledgeDocSummary] = Field(default_factory=list)
    notification_channels: list[NotificationChannelSummary] = Field(default_factory=list)
    total_resources: int = 0


class ResourceAssociationResponse(BaseModel):
    """Response schema for resource association operations.

    Requirements 4.1-4.4: Confirms resource association changes.
    """

    scenario_id: str
    resource_type: str
    associated_ids: list[str]
    total: int
    message: str
