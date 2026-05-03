import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

MODEL_TYPES = ["llm", "multimodal", "voice", "embedding", "rerank"]


class ModelProviderCreate(BaseModel):
    name: str
    provider_type: str
    api_key: str
    base_url: str | None = None
    model_name: str
    model_type: str = "llm"
    is_active: bool = True
    is_default: bool = False
    priority: int = 0
    config: dict = {}


class ModelProviderUpdate(BaseModel):
    name: str | None = None
    provider_type: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    model_type: str | None = None
    is_active: bool | None = None
    is_default: bool | None = None
    priority: int | None = None
    config: dict | None = None


class ModelProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    provider_type: str
    api_key: str
    base_url: str | None = None
    model_name: str
    model_type: str
    is_active: bool
    is_default: bool
    priority: int
    config: dict
    created_at: datetime
    updated_at: datetime


    @field_validator('api_key', mode='before')
    @classmethod
    def mask_api_key(cls, v: str) -> str:
        return '***'

class ModelProviderTestResult(BaseModel):
    ok: bool
    message: str
    latency_ms: float = 0
