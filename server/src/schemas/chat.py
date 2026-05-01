from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_serializer


class MessageOut(BaseModel):
    id: UUID | str
    session_id: UUID | str
    role: str
    content: str
    message_type: str = "text"
    extra_metadata: dict = {}
    created_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_serializer("id", "session_id")
    @classmethod
    def serialize_uuid_msg(cls, v: UUID | str) -> str:
        return str(v)


class SessionOut(BaseModel):
    id: UUID | str
    user_id: UUID | str
    agent_id: str | None = None
    title: str | None = None
    status: str
    sleep_status: str = "awake"
    memory_status: str = "unconsolidated"
    auto_consolidate: bool = True
    last_active_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_serializer("id", "user_id")
    @classmethod
    def serialize_uuid(cls, v: UUID | str) -> str:
        return str(v)


class SessionDetailOut(SessionOut):
    messages: list[MessageOut] = []


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    scenario_id: str | None = None
    space_id: str | None = None
    model_provider_id: str | None = None
    params: dict = {}


class ChatEvent(BaseModel):
    type: str  # intent, plan, exec, synthesize, final, human_interrupt, error
    data: dict
    session_id: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    events: list[ChatEvent] = []


class SessionFileOut(BaseModel):
    id: str
    session_id: str
    filename: str
    file_size: int
    mime_type: str | None = None
    content_text: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_serializer("id", "session_id")
    @classmethod
    def serialize_uuid(cls, v: UUID | str) -> str:
        return str(v)
