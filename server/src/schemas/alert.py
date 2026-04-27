from datetime import datetime
from pydantic import BaseModel


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

    model_config = {"from_attributes": True}


class AlertListParams(BaseModel):
    page: int = 1
    page_size: int = 20
    status: str | None = None
    severity: str | None = None
    source: str | None = None
    search: str | None = None
    sort_by: str = "created_at"
    sort_order: str = "desc"


class AlertActionRequest(BaseModel):
    action: str  # confirm, dismiss
    comment: str | None = None
    knowledge_title: str | None = None
    knowledge_tags: list[str] | None = None
