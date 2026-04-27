from datetime import datetime
from pydantic import BaseModel


class ChannelCreate(BaseModel):
    name: str
    channel_type: str
    config: dict = {}
    is_active: bool = True


class ChannelOut(BaseModel):
    id: str
    name: str
    channel_type: str
    config: dict
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class AgentProfileCreate(BaseModel):
    name: str
    skills: dict = {}
    collection: dict = {}
    rules: dict = {}
    model_config: dict = {}
    resources: dict = {}
    update_policy: dict = {}


class AgentProfileOut(BaseModel):
    id: str
    name: str
    profile_version: int
    skills: dict
    collection: dict
    rules: dict
    model_config: dict
    resources: dict
    update_policy: dict
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}
