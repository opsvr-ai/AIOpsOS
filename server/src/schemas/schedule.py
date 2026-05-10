import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, field_validator, model_validator


# =============================================================================
# Trend Condition Configuration Schema
# =============================================================================


class TrendConditionConfig(BaseModel):
    """趋势条件配置 Schema

    用于配置基于性能数据趋势的触发条件，支持上升趋势、下降趋势、异常波动检测。

    Attributes:
        metric: 要监控的指标名称（如 cpu_usage, memory_usage）
        direction: 趋势方向 - rising（上升）、falling（下降）、volatile（异常波动）
        threshold: 触发阈值（如变化率 0.2 表示 20%）
        window_minutes: 检测时间窗口（分钟）
    """

    metric: str
    direction: Literal["rising", "falling", "volatile"]
    threshold: float
    window_minutes: int

    @field_validator("metric")
    @classmethod
    def validate_metric(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("metric cannot be empty")
        return v.strip()

    @field_validator("threshold")
    @classmethod
    def validate_threshold(cls, v: float) -> float:
        if v < 0:
            raise ValueError("threshold must be non-negative")
        return v

    @field_validator("window_minutes")
    @classmethod
    def validate_window_minutes(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("window_minutes must be positive")
        return v


# =============================================================================
# Trigger Condition Schema (Recursive)
# =============================================================================

# Valid operators for simple conditions
SIMPLE_OPERATORS = frozenset(
    {"eq", "neq", "in", "not_in", "contains", "gt", "lt", "gte", "lte", "regex", "trend"}
)

# Valid condition types
CONDITION_TYPES = frozenset({"and", "or", "not", "simple"})


def validate_trigger_condition(condition: dict[str, Any]) -> dict[str, Any]:
    """递归验证触发条件结构

    支持的条件类型：
    - simple: 简单条件，包含 field, op, value
    - and: 与条件，包含 conditions 列表
    - or: 或条件，包含 conditions 列表
    - not: 非条件，包含单个 condition

    Args:
        condition: 条件字典

    Returns:
        验证后的条件字典

    Raises:
        ValueError: 条件结构无效时抛出
    """
    if not isinstance(condition, dict):
        raise ValueError("condition must be a dictionary")

    condition_type = condition.get("type")
    if not condition_type:
        raise ValueError("condition must have a 'type' field")

    if condition_type not in CONDITION_TYPES:
        raise ValueError(
            f"invalid condition type '{condition_type}', "
            f"must be one of: {', '.join(sorted(CONDITION_TYPES))}"
        )

    if condition_type == "simple":
        return _validate_simple_condition(condition)
    elif condition_type in ("and", "or"):
        return _validate_composite_condition(condition, condition_type)
    elif condition_type == "not":
        return _validate_not_condition(condition)

    return condition


def _validate_simple_condition(condition: dict[str, Any]) -> dict[str, Any]:
    """验证简单条件

    简单条件必须包含：
    - field: 字段名
    - op: 操作符
    - value: 比较值（trend 操作符除外）

    对于 trend 操作符，还需要 trend_config 配置。
    """
    field = condition.get("field")
    op = condition.get("op")

    if not field:
        raise ValueError("simple condition must have a 'field'")
    if not isinstance(field, str) or not field.strip():
        raise ValueError("'field' must be a non-empty string")

    if not op:
        raise ValueError("simple condition must have an 'op' (operator)")
    if op not in SIMPLE_OPERATORS:
        raise ValueError(
            f"invalid operator '{op}', must be one of: {', '.join(sorted(SIMPLE_OPERATORS))}"
        )

    # trend 操作符需要 trend_config
    if op == "trend":
        trend_config = condition.get("trend_config")
        if not trend_config:
            raise ValueError("trend operator requires 'trend_config'")
        # 验证 trend_config 结构
        try:
            TrendConditionConfig(**trend_config)
        except Exception as e:
            raise ValueError(f"invalid trend_config: {e}") from e
    else:
        # 非 trend 操作符需要 value
        if "value" not in condition:
            raise ValueError(f"simple condition with operator '{op}' must have a 'value'")

        # 验证特定操作符的 value 类型
        value = condition["value"]
        if op in ("in", "not_in"):
            if not isinstance(value, list):
                raise ValueError(f"operator '{op}' requires 'value' to be a list")
        elif op in ("gt", "lt", "gte", "lte"):
            if not isinstance(value, (int, float)):
                raise ValueError(f"operator '{op}' requires 'value' to be a number")
        elif op == "regex":
            if not isinstance(value, str):
                raise ValueError("operator 'regex' requires 'value' to be a string pattern")

    return condition


def _validate_composite_condition(
    condition: dict[str, Any], condition_type: str
) -> dict[str, Any]:
    """验证组合条件（and/or）

    组合条件必须包含 conditions 列表，且列表不能为空。
    """
    conditions = condition.get("conditions")
    if conditions is None:
        raise ValueError(f"'{condition_type}' condition must have a 'conditions' list")
    if not isinstance(conditions, list):
        raise ValueError(f"'conditions' must be a list for '{condition_type}' type")
    if len(conditions) == 0:
        raise ValueError(f"'{condition_type}' condition must have at least one sub-condition")

    # 递归验证每个子条件
    for i, sub_condition in enumerate(conditions):
        try:
            validate_trigger_condition(sub_condition)
        except ValueError as e:
            raise ValueError(f"invalid sub-condition at index {i}: {e}") from e

    return condition


def _validate_not_condition(condition: dict[str, Any]) -> dict[str, Any]:
    """验证 NOT 条件

    NOT 条件必须包含单个 condition 字段。
    """
    sub_condition = condition.get("condition")
    if sub_condition is None:
        raise ValueError("'not' condition must have a 'condition' field")
    if not isinstance(sub_condition, dict):
        raise ValueError("'condition' in 'not' type must be a dictionary")

    # 递归验证子条件
    try:
        validate_trigger_condition(sub_condition)
    except ValueError as e:
        raise ValueError(f"invalid sub-condition in 'not': {e}") from e

    return condition


# =============================================================================
# Schedule Schemas
# =============================================================================


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
    """触发规则创建 Schema

    支持增强的触发条件配置，包括：
    - 基于告警数量阈值的触发条件
    - 基于特定告警类型的触发条件
    - 基于告警严重级别的触发条件
    - 基于性能数据趋势的触发条件
    - 组合条件（AND、OR、NOT 逻辑运算符）
    """

    name: str
    description: str | None = None
    condition: dict[str, Any]
    scenario_id: str
    frequency_limit: int | None = None
    time_window_start: str | None = None
    time_window_end: str | None = None
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name cannot be empty")
        return v.strip()

    @field_validator("condition")
    @classmethod
    def validate_condition(cls, v: dict[str, Any]) -> dict[str, Any]:
        """验证触发条件结构"""
        return validate_trigger_condition(v)

    @field_validator("frequency_limit")
    @classmethod
    def validate_frequency_limit(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("frequency_limit must be positive")
        return v

    @model_validator(mode="after")
    def validate_time_window(self) -> "TriggerCreate":
        """验证时间窗口配置"""
        start = self.time_window_start
        end = self.time_window_end

        # 如果只配置了一个，需要两个都配置
        if (start is None) != (end is None):
            raise ValueError(
                "time_window_start and time_window_end must both be set or both be None"
            )

        # 验证时间格式 (HH:MM 或 HH:MM:SS)
        if start is not None and end is not None:
            import re

            time_pattern = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)(:[0-5]\d)?$")
            if not time_pattern.match(start):
                raise ValueError(
                    f"invalid time_window_start format '{start}', expected HH:MM or HH:MM:SS"
                )
            if not time_pattern.match(end):
                raise ValueError(
                    f"invalid time_window_end format '{end}', expected HH:MM or HH:MM:SS"
                )

        return self


class TriggerUpdate(BaseModel):
    """触发规则更新 Schema

    所有字段都是可选的，只更新提供的字段。
    """

    name: str | None = None
    description: str | None = None
    condition: dict[str, Any] | None = None
    scenario_id: str | None = None
    frequency_limit: int | None = None
    time_window_start: str | None = None
    time_window_end: str | None = None
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is not None:
            if not v.strip():
                raise ValueError("name cannot be empty")
            return v.strip()
        return v

    @field_validator("condition")
    @classmethod
    def validate_condition(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        """验证触发条件结构（如果提供）"""
        if v is not None:
            return validate_trigger_condition(v)
        return v

    @field_validator("frequency_limit")
    @classmethod
    def validate_frequency_limit(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("frequency_limit must be positive")
        return v

    @model_validator(mode="after")
    def validate_time_window(self) -> "TriggerUpdate":
        """验证时间窗口配置

        注意：对于更新操作，只有当两个字段都提供时才验证一致性。
        单独更新一个字段是允许的（假设另一个字段已在数据库中存在）。
        """
        start = self.time_window_start
        end = self.time_window_end

        # 验证时间格式（如果提供）
        import re

        time_pattern = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)(:[0-5]\d)?$")

        if start is not None and not time_pattern.match(start):
            raise ValueError(
                f"invalid time_window_start format '{start}', expected HH:MM or HH:MM:SS"
            )
        if end is not None and not time_pattern.match(end):
            raise ValueError(
                f"invalid time_window_end format '{end}', expected HH:MM or HH:MM:SS"
            )

        return self

class TriggerOut(BaseModel):
    """触发规则输出 Schema

    包含触发规则的完整信息，用于 API 响应。
    """

    id: str
    name: str
    description: str | None = None
    condition: dict[str, Any]
    scenario_id: str
    frequency_limit: int | None = None
    time_window_start: str | None = None
    time_window_end: str | None = None
    space_id: str | None = None
    is_active: bool
    last_triggered_at: datetime | None = None
    trigger_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("id", "scenario_id", "space_id", mode="before")
    @classmethod
    def coerce_uuid(cls, v: object) -> str | None:
        if isinstance(v, uuid.UUID):
            return str(v)
        if v is None:
            return None
        return v  # type: ignore[return-value]

    model_config = {"from_attributes": True}


# =============================================================================
# Enhanced Trigger Schemas for API Extensions
# =============================================================================


class TriggerConditionValidateRequest(BaseModel):
    """触发条件验证请求 Schema

    用于在保存触发规则之前验证条件结构的有效性。
    """

    condition: dict[str, Any]

    @field_validator("condition")
    @classmethod
    def validate_condition(cls, v: dict[str, Any]) -> dict[str, Any]:
        """验证触发条件结构"""
        return validate_trigger_condition(v)


class TriggerConditionValidateResponse(BaseModel):
    """触发条件验证响应 Schema

    返回条件验证结果，包括是否有效、条件摘要等信息。
    """

    valid: bool
    condition_summary: str
    condition_type: str
    has_alert_count_condition: bool = False
    has_trend_condition: bool = False
    has_composite_condition: bool = False
    operators_used: list[str] = []
    fields_referenced: list[str] = []
    error: str | None = None


class TriggerStatisticsOut(BaseModel):
    """触发规则统计信息 Schema

    返回触发规则的统计信息，包括触发次数、最近触发时间等。
    """

    trigger_id: str
    trigger_name: str
    trigger_count: int
    last_triggered_at: datetime | None = None
    is_active: bool
    frequency_limit: int | None = None
    time_window_active: bool = False
    created_at: datetime | None = None

    @field_validator("trigger_id", mode="before")
    @classmethod
    def coerce_uuid(cls, v: object) -> str | None:
        if isinstance(v, uuid.UUID):
            return str(v)
        return v  # type: ignore[return-value]

    model_config = {"from_attributes": True}


class TriggerTestRequest(BaseModel):
    """触发规则测试请求 Schema

    用于测试触发规则是否会匹配给定的告警数据。
    """

    condition: dict[str, Any]
    test_alert: dict[str, Any]

    @field_validator("condition")
    @classmethod
    def validate_condition(cls, v: dict[str, Any]) -> dict[str, Any]:
        """验证触发条件结构"""
        return validate_trigger_condition(v)

    @field_validator("test_alert")
    @classmethod
    def validate_test_alert(cls, v: dict[str, Any]) -> dict[str, Any]:
        """验证测试告警数据"""
        if not v:
            raise ValueError("test_alert cannot be empty")
        return v


class TriggerTestResponse(BaseModel):
    """触发规则测试响应 Schema

    返回触发规则测试结果。
    """

    matched: bool
    condition_type: str
    evaluation_details: dict[str, Any] = {}
    error: str | None = None


class TriggerBulkActionRequest(BaseModel):
    """触发规则批量操作请求 Schema

    用于批量启用/禁用触发规则。
    """

    trigger_ids: list[str]
    action: Literal["enable", "disable"]

    @field_validator("trigger_ids")
    @classmethod
    def validate_trigger_ids(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("trigger_ids cannot be empty")
        if len(v) > 100:
            raise ValueError("cannot process more than 100 triggers at once")
        return v


class TriggerBulkActionResponse(BaseModel):
    """触发规则批量操作响应 Schema

    返回批量操作结果。
    """

    success_count: int
    failed_count: int
    failed_ids: list[str] = []
    message: str
