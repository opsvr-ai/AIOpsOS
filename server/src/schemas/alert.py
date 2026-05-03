import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


class AlertOut(BaseModel):
    id: str
    event_id: str | None = None
    title: str
    source: str
    severity: str
    status: str
    raw_event: dict
    enriched_context: dict
    analysis_result: dict
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    knowledge_entry_id: str | None = None
    assigned_to: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    @field_validator('id', mode='before')
    @classmethod
    def coerce_uuid(cls, v: object) -> str | None:
        if isinstance(v, uuid.UUID):
            return str(v)
        return v  # type: ignore[return-value]


    model_config = {"from_attributes": True}


class AlertListParams(BaseModel):
    page: int = 1
    page_size: int = 20
    status: str | None = None
    severity: str | None = None
    source: str | None = None
    search: str | None = None
    space_id: str | None = None
    sort_by: str = "created_at"
    sort_order: str = "desc"


class AlertActionRequest(BaseModel):
    action: str  # analyze, confirm, dismiss, close
    comment: str | None = None
    knowledge_title: str | None = None
    knowledge_tags: list[str] | None = None


class AlertCreate(BaseModel):
    title: str
    source: str
    severity: str = "warning"  # critical, warning, info
    raw_event: dict = {}
    event_id: str | None = None


class AlertIngest(AlertCreate):
    """Used by Kafka consumer; same shape as AlertCreate."""
    pass


class BatchActionRequest(BaseModel):
    alert_ids: list[str]
    action: str  # confirm, dismiss
