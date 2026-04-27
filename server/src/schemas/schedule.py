import uuid
from datetime import datetime, time
from pydantic import BaseModel


class ScheduleCreate(BaseModel):
    name: str
    cron_expression: str
    scenario_id: str
    params: dict = {}
    is_active: bool = True


class ScheduleOut(BaseModel):
    id: str
    name: str
    cron_expression: str
    scenario_id: str
    params: dict
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class ScheduleExecutionOut(BaseModel):
    id: str
    schedule_id: str
    session_id: str | None = None
    status: str
    result: dict
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class TriggerCreate(BaseModel):
    name: str
    condition: dict
    scenario_id: str
    frequency_limit: int | None = None
    time_window_start: str | None = None
    time_window_end: str | None = None
    is_active: bool = True


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

    model_config = {"from_attributes": True}
