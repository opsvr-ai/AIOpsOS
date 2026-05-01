import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from src.schemas.channel import ChannelOut


class AgentCreate(BaseModel):
    name: str
    type: str = "sub"
    system_prompt: str | None = None
    user_prompt: str | None = None
    model_name: str = "deepseek-v4-flash"
    agent_type: str | None = "deep_agent"
    config: dict = {}
    is_active: bool = True
    viewable_roles: list[str] = []
    editable_roles: list[str] = []
    tool_ids: list[str] = []
    sub_agent_ids: list[str] = []
    channel_ids: list[str] = []
    space_id: str | None = None


class AgentUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    system_prompt: str | None = None
    user_prompt: str | None = None
    model_name: str | None = None
    agent_type: str | None = None
    config: dict | None = None
    is_active: bool | None = None
    viewable_roles: list[str] | None = None
    editable_roles: list[str] | None = None
    tool_ids: list[str] | None = None
    sub_agent_ids: list[str] | None = None
    channel_ids: list[str] | None = None
    space_id: str | None = None


class AgentRefOut(BaseModel):
    """Non-recursive agent reference — used as sub_agent items to prevent infinite nesting."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: str
    system_prompt: str | None = None
    model_name: str
    agent_type: str | None = None
    config: dict
    is_active: bool
    is_builtin: bool = False
    viewable_roles: list[str] = []
    editable_roles: list[str] = []
    created_at: datetime | None = None
    updated_at: datetime | None = None
    tools: list["ToolOut"] = []
    channels: list[ChannelOut] = []
    space_id: uuid.UUID | None = None

    @field_serializer("id")
    def serialize_id(self, value: uuid.UUID) -> str:
        return str(value)

    @field_serializer("space_id")
    def serialize_space_id(self, value: uuid.UUID | None) -> str | None:
        return str(value) if value else None


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: str
    system_prompt: str | None = None
    user_prompt: str | None = None
    model_name: str
    agent_type: str | None = None
    config: dict
    is_active: bool
    is_builtin: bool = False
    viewable_roles: list[str] = []
    editable_roles: list[str] = []
    space_id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    tools: list["ToolOut"] = []
    sub_agents: list[AgentRefOut] = []
    channels: list[ChannelOut] = []

    @field_serializer("id")
    def serialize_id(self, value: uuid.UUID) -> str:
        return str(value)

    @field_serializer("space_id")
    def serialize_space_id(self, value: uuid.UUID | None) -> str | None:
        return str(value) if value else None


class AgentVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    name: str
    system_prompt: str | None = None
    user_prompt: str | None = None
    model_name: str
    agent_type: str | None = None
    config: dict
    created_at: datetime | None = None

    @field_serializer("id", "agent_id")
    def serialize_uuid(self, value: uuid.UUID) -> str:
        return str(value)


class AgentRollbackRequest(BaseModel):
    version_id: str


class MCPServerCreate(BaseModel):
    name: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = []
    url: str | None = None
    is_active: bool = True


class MCPServerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    transport: str
    command: str | None = None
    args: list[str]
    url: str | None = None
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_serializer("id")
    def serialize_id(self, value: uuid.UUID) -> str:
        return str(value)


class ToolCreate(BaseModel):
    name: str
    type: str = "skill"
    description: str | None = None
    mcp_server_id: str | None = None
    category: str | None = None
    source_path: str | None = None
    config: dict = {}
    is_approved: bool = False
    is_active: bool = True
    # Skill protocol fields (type=skill)
    version: str | None = None
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = {}
    allowed_tools: list[str] = []
    skill_prompt: str | None = None  # markdown body of SKILL.md


class ToolUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    config: dict | None = None
    is_approved: bool | None = None
    is_active: bool | None = None
    # Skill protocol fields (type=skill)
    version: str | None = None
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] | None = None
    allowed_tools: list[str] | None = None
    skill_prompt: str | None = None


class ToolListOut(BaseModel):
    """Paginated tool list response."""
    items: list["ToolOut"]
    total: int


class ToolSearchParams(BaseModel):
    """Query params for GET /tools search/filter."""
    type: str | None = None
    name: str | None = None
    description: str | None = None
    category: str | None = None
    space_id: str | None = None
    status: str | None = None  # "active" | "inactive" | "all"
    health: str | None = None  # "invalid" for orphaned skills, otherwise all
    page: int = 1
    page_size: int = 50


class BatchStatusRequest(BaseModel):
    """Batch enable/disable tools."""
    tool_ids: list[str]
    is_active: bool


class BatchDeleteRequest(BaseModel):
    """Batch delete tools by IDs."""
    tool_ids: list[str]


class SkillGenerateRequest(BaseModel):
    """AI-assisted skill generation request."""
    name: str
    description: str  # what the skill should do, in natural language
    language: str = "zh"  # "zh" | "en"


class SkillUploadResult(BaseModel):
    """Per-file result of a zip upload."""
    filename: str
    name: str | None = None
    status: str  # "created" | "updated" | "skipped" | "error"
    message: str = ""


class SkillUploadResponse(BaseModel):
    """Response for batch zip upload."""
    total: int
    created: int
    updated: int
    skipped: int
    errors: int
    results: list[SkillUploadResult]


class ToolOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: str
    description: str | None = None
    mcp_server_id: str | None = None
    category: str | None = None
    source_path: str | None = None
    config: dict
    is_approved: bool
    is_active: bool
    is_builtin: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    is_consistent: bool | None = None  # populated by consistency-aware endpoints
    is_valid: bool | None = None  # skill has valid directory + SKILL.md on disk

    @field_serializer("id")
    def serialize_id(self, value: uuid.UUID) -> str:
        return str(value)


class SkillVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool_id: uuid.UUID
    name: str
    description: str | None = None
    config: dict
    created_at: datetime | None = None

    @field_serializer("id", "tool_id")
    def serialize_uuid(self, value: uuid.UUID) -> str:
        return str(value)


class ToolConsistencyOut(BaseModel):
    tool_id: str
    tool_name: str
    is_consistent: bool | None
    db_hash: str | None = None
    fs_hash: str | None = None


class BatchConsistencyOut(BaseModel):
    tools: list[ToolConsistencyOut]
    inconsistent_count: int


class SkillRollbackRequest(BaseModel):
    version_id: str


class SkillFileNode(BaseModel):
    """A node in the skill directory tree."""
    name: str
    type: str  # "file" | "directory"
    path: str  # relative path within skill dir
    children: list["SkillFileNode"] | None = None  # set for directories
    content: str | None = None  # populated for files when requested


class SkillFileWriteRequest(BaseModel):
    """Write content to a file within a skill directory."""
    path: str
    content: str


class SkillDirectoryCreate(BaseModel):
    """Create a subdirectory within a skill directory."""
    path: str


class SkillValidationResult(BaseModel):
    """Result of skill protocol validation."""
    valid: bool
    errors: list[str] = []


class ConsistencySummary(BaseModel):
    """Lightweight inconsistency count for polling."""
    inconsistent_count: int


class SyncDiffItem(BaseModel):
    """A single inconsistency between filesystem and DB."""
    name: str
    category: str | None = None  # from DB
    type: str = "skill"
    status: str  # "only_in_db" | "only_in_fs" | "modified" | "consistent"
    db_id: str | None = None
    db_description: str | None = None
    db_version: str | None = None
    fs_description: str | None = None
    fs_version: str | None = None
    fs_category: str | None = None
    source_path: str | None = None
    source_label: str | None = None  # "standard" | "extended" | None for user skills
    is_active: bool = False


class SyncScanOut(BaseModel):
    """Result of a full filesystem vs DB scan."""
    total_fs: int
    total_db: int
    only_in_db: list[SyncDiffItem]
    only_in_fs: list[SyncDiffItem]
    modified: list[SyncDiffItem]
    consistent: int


class SyncAction(BaseModel):
    """An action to take during sync execution."""
    action: str  # "register" | "update" | "delete"
    name: str


class SyncExecuteRequest(BaseModel):
    """Request to execute sync actions."""
    actions: list[SyncAction]


class SyncExecuteOut(BaseModel):
    """Result of sync execution."""
    registered: int
    updated: int
    deleted: int
    errors: list[str]


class ScenarioCreate(BaseModel):
    name: str
    description: str | None = None
    trigger_command: str
    params_schema: dict = {}
    is_active: bool = True
    tool_ids: list[str] = []
    agent_ids: list[str] = []


class ScenarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None = None
    trigger_command: str
    params_schema: dict
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_serializer("id")
    def serialize_id(self, value: uuid.UUID) -> str:
        return str(value)


class ScenarioDetailOut(ScenarioOut):
    tools: list["ToolOut"] = []
    agents: list["AgentOut"] = []
