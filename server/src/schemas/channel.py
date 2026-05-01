import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChannelCreate(BaseModel):
    name: str
    channel_type: str
    config: dict = {}
    is_active: bool = True


class ChannelUpdate(BaseModel):
    name: str | None = None
    channel_type: str | None = None
    config: dict | None = None
    is_active: bool | None = None


class ChannelOut(BaseModel):
    id: uuid.UUID
    name: str
    channel_type: str
    config: dict
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class AgentProfileCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    skills: dict = {}
    collection: dict = {}
    rules: dict = {}
    model_settings: dict = Field(default={}, alias="model_config")
    resources: dict = {}
    update_policy: dict = {}


class AgentProfileUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    skills: dict | None = None
    collection: dict | None = None
    rules: dict | None = None
    model_settings: dict | None = Field(default=None, alias="model_config")
    resources: dict | None = None
    update_policy: dict | None = None


class AgentProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    name: str
    profile_version: int
    skills: dict
    collection: dict
    rules: dict
    model_settings: dict = Field(alias="model_config")
    resources: dict
    update_policy: dict
    online: bool = False
    last_heartbeat: datetime | None = None
    agent_version: str | None = None
    connected_agent_id: str | None = None
    hostname: str | None = None
    ip_address: str | None = None
    os_info: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TaskDispatchRequest(BaseModel):
    type: str = Field(..., description="nl | shell | script")
    content: str = Field(..., description="Task content")


class TaskResult(BaseModel):
    task_id: str
    status: str
    output: str | None = None
    created_at: datetime | None = None


class AgentMetrics(BaseModel):
    cpu_percent: float | None = None
    memory_percent: float | None = None
    disk_percent: float | None = None
    network_rx_bytes: int | None = None
    network_tx_bytes: int | None = None
    recorded_at: datetime | None = None
