from datetime import datetime
import uuid
from pydantic import BaseModel, field_validator


class IngestionLogOut(BaseModel):
    id: str
    datasource_id: str
    status: str
    events_received: int = 0
    alerts_created: int = 0
    alerts_deduped: int = 0
    errors_count: int = 0
    errors_detail: list = []
    duration_ms: int | None = None
    request_url: str | None = None
    response_status: int | None = None
    created_at: datetime | None = None
    @field_validator('id', 'datasource_id', mode='before')
    @classmethod
    def coerce_uuid(cls, v: object) -> str:
        if isinstance(v, uuid.UUID):
            return str(v)
        return v  # type: ignore[return-value]


    model_config = {"from_attributes": True}
