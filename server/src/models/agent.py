import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Table, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from src.models.channel import NotificationChannel
    from src.models.knowledge import KnowledgeDocument
    from src.models.scenario import ScenarioExecution

scenario_tools = Table(
    "scenario_tools",
    Base.metadata,
    Column("scenario_id", UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"), primary_key=True),
    Column("tool_id", UUID(as_uuid=True), ForeignKey("tools.id", ondelete="CASCADE"), primary_key=True),
)

scenario_agents = Table(
    "scenario_agents",
    Base.metadata,
    Column("scenario_id", UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"), primary_key=True),
    Column("agent_id", UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True),
)

agent_tools = Table(
    "agent_tools",
    Base.metadata,
    Column("agent_id", UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True),
    Column("tool_id", UUID(as_uuid=True), ForeignKey("tools.id", ondelete="CASCADE"), primary_key=True),
)

agent_sub_agents = Table(
    "agent_sub_agents",
    Base.metadata,
    Column("main_agent_id", UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True),
    Column("sub_agent_id", UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True),
)

agent_channels = Table(
    "agent_channels",
    Base.metadata,
    Column("agent_id", UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True),
    Column("channel_id", UUID(as_uuid=True), ForeignKey("notification_channels.id", ondelete="CASCADE"), primary_key=True),
)

# New association tables for Scenario resource relationships
scenario_knowledge_docs = Table(
    "scenario_knowledge_docs",
    Base.metadata,
    Column("scenario_id", UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"), primary_key=True),
    Column("document_id", UUID(as_uuid=True), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), primary_key=True),
)

scenario_channels = Table(
    "scenario_channels",
    Base.metadata,
    Column("scenario_id", UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"), primary_key=True),
    Column("channel_id", UUID(as_uuid=True), ForeignKey("notification_channels.id", ondelete="CASCADE"), primary_key=True),
)


class Agent(Base, TimestampMixin):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    viewable_roles: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    editable_roles: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    model_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_providers.id", ondelete="SET NULL"), nullable=True
    )
    model_provider: Mapped["ModelProvider | None"] = relationship()
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True
    )

    scenarios: Mapped[list["Scenario"]] = relationship(
        secondary="scenario_agents", back_populates="agents"
    )
    tools: Mapped[list["Tool"]] = relationship(
        secondary="agent_tools",
    )
    sub_agents: Mapped[list["Agent"]] = relationship(
        secondary="agent_sub_agents",
        primaryjoin="Agent.id == agent_sub_agents.c.main_agent_id",
        secondaryjoin="Agent.id == agent_sub_agents.c.sub_agent_id",
    )
    channels: Mapped[list["NotificationChannel"]] = relationship(
        secondary="agent_channels",
    )
    versions: Mapped[list["AgentVersion"]] = relationship(
        back_populates="agent", order_by="AgentVersion.created_at.desc()",
        cascade="all, delete-orphan",
    )


class MCPServer(Base, TimestampMixin):
    __tablename__ = "mcp_servers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    transport: Mapped[str] = mapped_column(String(32), nullable=False)
    command: Mapped[str | None] = mapped_column(String(512), nullable=True)
    args: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True
    )


class Tool(Base, TimestampMixin):
    __tablename__ = "tools"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    mcp_server_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mcp_servers.id", ondelete="SET NULL"), nullable=True
    )
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Safety classification consumed by ToolDispatcher (design.md § ToolDispatcher).
    # Values: 'parallel-safe' | 'sequential' | 'destructive'. A CHECK constraint
    # enforces the domain at the DB layer (migration 202605041830). The DDL-level
    # server_default was dropped after backfill, so a Python-level ``default`` is
    # supplied here to keep SQLAlchemy-managed inserts consistent even when the
    # caller omits the field. ``server_default`` is still declared for the sake
    # of tooling that introspects the model (Alembic autogenerate, tests, etc.).
    safety: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="sequential",
        server_default="sequential",
    )
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True
    )

    mcp_server: Mapped[MCPServer | None] = relationship()
    scenarios: Mapped[list["Scenario"]] = relationship(
        secondary="scenario_tools", back_populates="tools"
    )


class AgentVersion(Base, TimestampMixin):
    __tablename__ = "agent_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)

    agent: Mapped["Agent"] = relationship(back_populates="versions")


class Scenario(Base, TimestampMixin):
    __tablename__ = "scenarios"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_command: Mapped[str] = mapped_column(String(128), nullable=False)
    params_schema: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True
    )

    # New fields for scenario type system (Requirements 1.1-1.4)
    scenario_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="command", server_default="command"
    )  # command | natural_language | hybrid
    nl_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_timeout: Mapped[int] = mapped_column(
        Integer, nullable=False, default=300, server_default="300"
    )  # seconds

    # Emergency collaboration configuration (Requirements 6.1)
    enable_collaboration: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    collaboration_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    # collaboration_config structure:
    # {
    #   "auto_create_group": true,
    #   "group_name_template": "[应急] {scenario_name} - {timestamp}",
    #   "group_members": ["user1", "user2"],
    #   "group_owner": "admin",
    #   "send_email": true,
    #   "email_recipients": ["ops@example.com"],
    #   "email_template_id": "emergency_alert"
    # }

    # Existing relationships
    tools: Mapped[list["Tool"]] = relationship(
        secondary="scenario_tools", back_populates="scenarios"
    )
    agents: Mapped[list["Agent"]] = relationship(
        secondary="scenario_agents", back_populates="scenarios"
    )

    # New relationships for resource associations (Requirements 4.3, 4.4)
    knowledge_docs: Mapped[list["KnowledgeDocument"]] = relationship(
        secondary="scenario_knowledge_docs"
    )
    notification_channels: Mapped[list["NotificationChannel"]] = relationship(
        secondary="scenario_channels"
    )
    executions: Mapped[list["ScenarioExecution"]] = relationship(
        back_populates="scenario", order_by="ScenarioExecution.created_at.desc()"
    )
