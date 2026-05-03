"""Cron job model — scheduled agent tasks with delivery routing."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import Base


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class CronJob(Base):
    __tablename__ = "cron_jobs"

    id = Column(String, primary_key=True, default=_new_id)
    name = Column(String, nullable=False)
    prompt = Column(Text, nullable=False)
    schedule = Column(String, nullable=False)  # cron expr, "30m", "2h", "1d", or "once"
    timezone_str = Column(String, default="Asia/Shanghai")
    skills = Column(JSONB, default=list)  # list of skill names to preload
    enabled_toolsets = Column(JSONB, default=list)
    delivery = Column(JSONB, default=None)  # delivery targets
    timeout_seconds = Column(String, nullable=True)  # execution timeout
    max_retries = Column(String, nullable=True)  # retry count on failure
    enabled = Column(Boolean, default=True)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True)
    last_run = Column(DateTime(timezone=True), nullable=True)
    next_run = Column(DateTime(timezone=True), nullable=True)
    last_output = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC),
                        onupdate=lambda: datetime.now(UTC))
