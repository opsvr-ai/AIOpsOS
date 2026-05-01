from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CronJobCreate(BaseModel):
    name: str
    prompt: str
    schedule: str = Field(description="cron expr, '30m', '2h', '1d', or 'once'")
    timezone_str: str = "Asia/Shanghai"
    skills: list[str] = []
    enabled_toolsets: list[str] = []
    delivery: dict | None = None
    timeout_seconds: str | None = None
    max_retries: str | None = None
    enabled: bool = True


class CronJobUpdate(BaseModel):
    name: str | None = None
    prompt: str | None = None
    schedule: str | None = None
    timezone_str: str | None = None
    skills: list[str] | None = None
    enabled_toolsets: list[str] | None = None
    delivery: dict | None = None
    timeout_seconds: str | None = None
    max_retries: str | None = None
    enabled: bool | None = None


class CronJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    prompt: str
    schedule: str
    timezone_str: str
    skills: list
    enabled_toolsets: list
    delivery: dict | None = None
    timeout_seconds: str | None = None
    max_retries: str | None = None
    enabled: bool
    last_run: datetime | None = None
    next_run: datetime | None = None
    last_output: str | None = None
    created_at: datetime
    updated_at: datetime
