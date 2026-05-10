import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Time, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class Schedule(Base, TimestampMixin):
    __tablename__ = "schedules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(128), nullable=False)
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE")
    )
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True
    )

    executions: Mapped[list["ScheduleExecution"]] = relationship(back_populates="schedule")


class ScheduleExecution(Base):
    __tablename__ = "schedule_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schedules.id", ondelete="CASCADE")
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=func.now()
    )

    schedule: Mapped["Schedule"] = relationship(back_populates="executions")


class SceneTrigger(Base, TimestampMixin):
    """场景触发器模型
    
    支持增强的触发条件配置，包括趋势检测。
    
    condition JSONB 结构支持以下格式:
    {
        "type": "and" | "or" | "simple",
        "conditions": [...],  # 用于 and/or 组合条件
        "field": "severity",  # 用于 simple 简单条件
        "op": "eq" | "neq" | "in" | "not_in" | "contains" | "gt" | "lt" | "gte" | "lte" | "regex" | "trend",
        "value": "critical",
        "trend_config": {  # 用于 trend 操作符的趋势检测配置
            "metric": "cpu_usage",
            "direction": "rising" | "falling" | "volatile",
            "threshold": 0.2,
            "window_minutes": 30
        }
    }
    """
    __tablename__ = "scene_triggers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    condition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE")
    )
    frequency_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_window_start: Mapped[datetime | None] = mapped_column(Time, nullable=True)
    time_window_end: Mapped[datetime | None] = mapped_column(Time, nullable=True)
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # 新增字段：触发统计
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trigger_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
