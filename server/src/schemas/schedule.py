import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


class ScheduleCreate(BaseModel):
    name: str
    cron_expression: str
    scenario_id: str
    params: dict = {}
    is_active: bool = True


class ScheduleUpdate(BaseModel):
    name: str | None = None
    cron_expression: str | None = None
    scenario_id: str | None = None
    params: dict | None = None
    is_active: bool | None = None

class ScheduleOut(BaseModel):
    id: str
    name: str
    cron_expression: str
    scenario_id: str
    params: dict
    is_active: bool
    next_run: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    @field_validator('id', 'scenario_id', mode='before')
    @classmethod
    def coerce_uuid(cls, v: object) -> str | None:
        if isinstance(v, uuid.UUID):
            return str(v)
        return v  # type: ignore[return-value]


    model_config = {"from_attributes": True}


class ScheduleExecutionOut(BaseModel):
    id: str
    schedule_id: str
    session_id: str | None = None
    status: str
    result: dict
    created_at: datetime | None = None
    @field_validator('id', 'schedule_id', mode='before')
    @classmethod
    def coerce_uuid(cls, v: object) -> str | None:
        if isinstance(v, uuid.UUID):
            return str(v)
        return v  # type: ignore[return-value]


    model_config = {"from_attributes": True}


class TriggerCreate(BaseModel):
    name: str
    condition: dict
    scenario_id: str
    frequency_limit: int | None = None
    time_window_start: str | None = None
    time_window_end: str | None = None
    is_active: bool = True


class TriggerUpdate(BaseModel):
    name: str | None = None
    condition: dict | None = None
    scenario_id: str | None = None
    frequency_limit: int | None = None
    time_window_start: str | None = None
    time_window_end: str | None = None
    is_active: bool | None = None

class TriggerOut(BaseModel):
    id: str
    name: str
    condition: dict
    scenario_id: str
    frequency_limit: int | None = None
    time_window_start: str | None = None
    time_window_end: str | None = None
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None
    @field_validator('id', 'scenario_id', mode='before')
    @classmethod
    def coerce_uuid(cls, v: object) -> str | None:
        if isinstance(v, uuid.UUID):
            return str(v)
        return v  # type: ignore[return-value]


    model_config = {"from_attributes": True}
