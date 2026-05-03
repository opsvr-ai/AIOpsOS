import uuid

from sqlalchemy import Boolean, Column, ForeignKey, String, Table, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin

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
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True
    )

    mcp_server: Mapped[MCPServer | None] = relationship()
    scenarios: Mapped[list["Scenario"]] = relationship(
        secondary="scenario_tools", back_populates="tools"
    )
    versions: Mapped[list["SkillVersion"]] = relationship(
        back_populates="tool", order_by="SkillVersion.created_at.desc()",
        cascade="all, delete-orphan",
    )


class SkillVersion(Base, TimestampMixin):
    __tablename__ = "skill_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)

    tool: Mapped["Tool"] = relationship(back_populates="versions")


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

    tools: Mapped[list["Tool"]] = relationship(
        secondary="scenario_tools", back_populates="scenarios"
    )
    agents: Mapped[list["Agent"]] = relationship(
        secondary="scenario_agents", back_populates="scenarios"
    )
