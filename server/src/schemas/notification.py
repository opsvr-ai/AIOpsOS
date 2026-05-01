from datetime import datetime
import uuid
from pydantic import BaseModel, field_validator


class NotificationOut(BaseModel):
    id: str
    user_id: str | None = None
    alert_id: str | None = None
    title: str
    message: str | None = None
    severity: str = "info"
    is_read: bool = False
    read_at: datetime | None = None
    created_at: datetime | None = None
    @field_validator('id', 'user_id', 'alert_id', mode='before')
    @classmethod
    def coerce_uuid(cls, v: object) -> str | None:
        if isinstance(v, uuid.UUID):
            return str(v)
        return v  # type: ignore[return-value]


    model_config = {"from_attributes": True}


class NotificationsListParams(BaseModel):
    page: int = 1
    page_size: int = 20
    is_read: bool | None = None
    severity: str | None = None
