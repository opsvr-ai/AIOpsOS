import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class FeedbackCreate(BaseModel):
    type: str = Field(..., description="bug | feature")
    title: str
    description: str
    images: list[str] = Field(default_factory=list, max_length=5)


class FeedbackUpdate(BaseModel):
    status: str | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    ai_analysis: str | None = None
    resolved_version: str | None = None


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: str
    username: str
    type: str
    title: str
    description: str
    status: str
    rating: int | None = None
    ai_analysis: str | None = None
    resolved_version: str | None = None
    images: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_serializer("id")
    def serialize_id(self, value: uuid.UUID) -> str:
        return str(value)


class FeedbackImageUploadResponse(BaseModel):
    url: str
    filename: str
