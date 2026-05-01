"""Trigger rule engine — condition evaluation and trigger matching.

Supports simple conditions (field op value), composite AND/OR conditions,
time window gating, and frequency limiting.
"""

import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.alert import Alert
from src.models.schedule import SceneTrigger

logger = logging.getLogger(__name__)

OPERATORS = {
    "eq": lambda fv, cv: fv == cv,
    "neq": lambda fv, cv: fv != cv,
    "in": lambda fv, cv: fv in cv if isinstance(cv, list) else False,
    "not_in": lambda fv, cv: fv not in cv if isinstance(cv, list) else True,
    "contains": lambda fv, cv: str(cv).lower() in str(fv).lower(),
    "gt": lambda fv, cv: float(fv) > float(cv),
    "lt": lambda fv, cv: float(fv) < float(cv),
    "gte": lambda fv, cv: float(fv) >= float(cv),
    "lte": lambda fv, cv: float(fv) <= float(cv),
    "regex": lambda fv, cv: bool(re.search(cv, str(fv))),
}


def _get_field_value(alert: Alert, field: str):
    """Get a field value from an alert, checking both attributes and raw_event keys."""
    if hasattr(alert, field):
        return getattr(alert, field)
    if isinstance(alert.raw_event, dict):
        return alert.raw_event.get(field)
    return None


def evaluate_simple(condition: dict, alert: Alert) -> bool:
    """Evaluate a simple condition (field, op, value) against an alert."""
    field = condition.get("field", "")
    op = condition.get("op", "eq")
    value = condition.get("value")

    field_val = _get_field_value(alert, field)
    if field_val is None and op not in ("neq", "not_in"):
        return False

    operator = OPERATORS.get(op)
    if operator is None:
        return False

    try:
        return operator(field_val, value)
    except (TypeError, ValueError):
        return False


def evaluate_condition(condition: dict, alert: Alert) -> bool:
    """Evaluate a condition dict (simple or composite) against an alert."""
    cond_type = condition.get("type", "simple")
    if cond_type == "simple":
        return evaluate_simple(condition, alert)
    elif cond_type == "and":
        sub = condition.get("conditions", [])
        return all(evaluate_condition(c, alert) for c in sub)
    elif cond_type == "or":
        sub = condition.get("conditions", [])
        return any(evaluate_condition(c, alert) for c in sub)
    return False


async def match_triggers(
    db: AsyncSession, alert: Alert
) -> list[SceneTrigger]:
    """Find all active triggers whose conditions match this alert."""
    result = await db.execute(
        select(SceneTrigger).where(SceneTrigger.is_active.is_(True))
    )
    triggers = result.scalars().all()

    matched: list[SceneTrigger] = []
    for trigger in triggers:
        if trigger.time_window_start and trigger.time_window_end:
            now_time = datetime.now(UTC).time()
            if not (trigger.time_window_start <= now_time <= trigger.time_window_end):
                continue

        if not evaluate_condition(trigger.condition, alert):
            continue

        if trigger.frequency_limit:
            if not await _check_frequency(db, trigger):
                continue

        matched.append(trigger)

    return matched


async def _check_frequency(db: AsyncSession, trigger: SceneTrigger) -> bool:
    """Return True if trigger has not exceeded its frequency_limit in the last hour."""
    from src.models.schedule import ScheduleExecution, Schedule

    one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    result = await db.execute(
        select(func.count())
        .select_from(ScheduleExecution)
        .join(Schedule, ScheduleExecution.schedule_id == Schedule.id)
        .where(
            Schedule.scenario_id == trigger.scenario_id,
            ScheduleExecution.created_at >= one_hour_ago,
        )
    )
    count = result.scalar() or 0
    return count < trigger.frequency_limit
