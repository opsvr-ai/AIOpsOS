import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PersonalAssistantConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    enabled_sub_agents: list[str] = []
    favorite_tools: list[str] = []
    preferred_scenarios: list[str] = []
    custom_prompt: str | None = None
    notification_prefs: dict = {}
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PersonalAssistantConfigUpdate(BaseModel):
    enabled_sub_agents: list[str] | None = None
    favorite_tools: list[str] | None = None
    preferred_scenarios: list[str] | None = None
    custom_prompt: str | None = None
    notification_prefs: dict | None = None
