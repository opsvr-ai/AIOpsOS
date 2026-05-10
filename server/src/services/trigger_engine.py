"""Trigger rule engine — condition evaluation and trigger matching.

Supports simple conditions (field op value), composite AND/OR/NOT conditions,
time window gating, frequency limiting, and enhanced alert conditions.

Enhanced condition support includes:
- Alert count threshold conditions (gt, lt, gte, lte operators)
- Alert type conditions (eq, in operators on alert_type field)
- Alert severity conditions (eq, in operators on severity field)
- Trend detection conditions (rising, falling, volatile trends over time windows)
- Basic operators: eq, neq, in, not_in, contains, gt, lt, gte, lte, regex, trend

Frequency limiting:
- Redis-based frequency limiting for high-performance rate checking
- Falls back to database-based checking when Redis is unavailable
- Configurable time window (default 1 hour)

Trigger recording:
- Records trigger time and reason for each successful trigger match
- Updates trigger statistics (last_triggered_at, trigger_count)
"""

import logging
import re
import statistics
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.alert import Alert
from src.models.schedule import SceneTrigger

logger = logging.getLogger(__name__)

# Minimum number of data points required for trend analysis
MIN_TREND_DATA_POINTS = 3

# Default values for trend detection
DEFAULT_TREND_WINDOW_MINUTES = 30
DEFAULT_TREND_THRESHOLD = 0.2  # 20% change threshold
DEFAULT_VOLATILITY_THRESHOLD = 0.15  # 15% coefficient of variation for volatility

# Basic comparison operators for field-value evaluation
OPERATORS: dict[str, Any] = {
    "eq": lambda fv, cv: fv == cv,
    "neq": lambda fv, cv: fv != cv,
    "in": lambda fv, cv: fv in cv if isinstance(cv, list) else False,
    "not_in": lambda fv, cv: fv not in cv if isinstance(cv, list) else True,
    "contains": lambda fv, cv: str(cv).lower() in str(fv).lower(),
    "gt": lambda fv, cv: _compare_numeric(fv, cv, lambda a, b: a > b),
    "lt": lambda fv, cv: _compare_numeric(fv, cv, lambda a, b: a < b),
    "gte": lambda fv, cv: _compare_numeric(fv, cv, lambda a, b: a >= b),
    "lte": lambda fv, cv: _compare_numeric(fv, cv, lambda a, b: a <= b),
    "regex": lambda fv, cv: bool(re.search(cv, str(fv))) if fv is not None else False,
}

# Severity levels for comparison (higher number = more severe)
SEVERITY_LEVELS: dict[str, int] = {
    "info": 0,
    "low": 1,
    "warning": 2,
    "medium": 3,
    "high": 4,
    "critical": 5,
}


def _compare_numeric(
    field_value: Any, compare_value: Any, comparator: Any
) -> bool:
    """Safely compare numeric values, handling type conversion.

    Args:
        field_value: The value from the alert field
        compare_value: The value to compare against
        comparator: A function that takes two numbers and returns bool

    Returns:
        True if comparison succeeds, False otherwise
    """
    try:
        fv = float(field_value) if field_value is not None else None
        cv = float(compare_value) if compare_value is not None else None
        if fv is None or cv is None:
            return False
        return comparator(fv, cv)
    except (TypeError, ValueError):
        return False


def _get_field_value(alert: Alert, field: str) -> Any:
    """Get a field value from an alert, checking both attributes and raw_event keys.

    Supports special field names for enhanced condition evaluation:
    - alert_type: Maps to source field or raw_event.alert_type
    - severity: Maps to severity field with level normalization support
    - alert_count: Special field for aggregate conditions (handled separately)

    Args:
        alert: The Alert object to extract field value from
        field: The field name to extract

    Returns:
        The field value, or None if not found
    """
    # Handle special field mappings
    if field == "alert_type":
        # First check raw_event for alert_type, then fall back to source
        if isinstance(alert.raw_event, dict) and "alert_type" in alert.raw_event:
            return alert.raw_event.get("alert_type")
        return getattr(alert, "source", None)

    # Standard field lookup
    if hasattr(alert, field):
        return getattr(alert, field)
    if isinstance(alert.raw_event, dict):
        return alert.raw_event.get(field)
    return None


def _normalize_severity(severity: str | None) -> str | None:
    """Normalize severity string to lowercase for consistent comparison.

    Args:
        severity: The severity string to normalize

    Returns:
        Lowercase severity string, or None if input is None
    """
    if severity is None:
        return None
    return str(severity).lower().strip()


def _compare_severity(
    alert_severity: str | None, condition_value: Any, operator: str
) -> bool:
    """Compare alert severity using level-based comparison for numeric operators.

    For eq/neq/in/not_in operators, performs string comparison.
    For gt/lt/gte/lte operators, uses severity level mapping.

    Args:
        alert_severity: The alert's severity value
        condition_value: The value to compare against
        operator: The comparison operator

    Returns:
        True if comparison succeeds, False otherwise
    """
    normalized_severity = _normalize_severity(alert_severity)
    if normalized_severity is None:
        return operator in ("neq", "not_in")

    # For equality operators, use string comparison
    if operator in ("eq", "neq", "in", "not_in"):
        if operator == "eq":
            return normalized_severity == _normalize_severity(condition_value)
        elif operator == "neq":
            return normalized_severity != _normalize_severity(condition_value)
        elif operator == "in":
            if not isinstance(condition_value, list):
                return False
            normalized_list = [_normalize_severity(v) for v in condition_value]
            return normalized_severity in normalized_list
        elif operator == "not_in":
            if not isinstance(condition_value, list):
                return True
            normalized_list = [_normalize_severity(v) for v in condition_value]
            return normalized_severity not in normalized_list

    # For numeric operators, use severity level comparison
    if operator in ("gt", "lt", "gte", "lte"):
        alert_level = SEVERITY_LEVELS.get(normalized_severity)
        if alert_level is None:
            return False

        # Handle single value or list for comparison
        if isinstance(condition_value, list):
            # For list values, compare against the first valid severity
            for cv in condition_value:
                cv_normalized = _normalize_severity(cv)
                if cv_normalized and cv_normalized in SEVERITY_LEVELS:
                    condition_level = SEVERITY_LEVELS[cv_normalized]
                    break
            else:
                return False
        else:
            cv_normalized = _normalize_severity(condition_value)
            if cv_normalized is None or cv_normalized not in SEVERITY_LEVELS:
                return False
            condition_level = SEVERITY_LEVELS[cv_normalized]

        if operator == "gt":
            return alert_level > condition_level
        elif operator == "lt":
            return alert_level < condition_level
        elif operator == "gte":
            return alert_level >= condition_level
        elif operator == "lte":
            return alert_level <= condition_level

    return False


def evaluate_simple(condition: dict[str, Any], alert: Alert) -> bool:
    """Evaluate a simple condition (field, op, value) against an alert.

    Supports enhanced conditions for:
    - Alert type conditions (field: alert_type)
    - Alert severity conditions (field: severity) with level-based comparison
    - Standard field conditions with all operators

    Args:
        condition: A dict with 'field', 'op', and 'value' keys
        alert: The Alert object to evaluate against

    Returns:
        True if the condition matches, False otherwise
    """
    field = condition.get("field", "")
    op = condition.get("op", "eq")
    value = condition.get("value")

    # Special handling for severity field with level-based comparison
    if field == "severity":
        return _compare_severity(alert.severity, value, op)

    field_val = _get_field_value(alert, field)
    if field_val is None and op not in ("neq", "not_in"):
        return False

    operator = OPERATORS.get(op)
    if operator is None:
        logger.warning(f"Unknown operator '{op}' in condition")
        return False

    try:
        return operator(field_val, value)
    except (TypeError, ValueError) as e:
        logger.debug(f"Condition evaluation error for field '{field}': {e}")
        return False


def evaluate_condition(condition: dict[str, Any], alert: Alert) -> bool:
    """Evaluate a condition dict (simple or composite) against an alert.

    Supports condition types:
    - simple: Single field comparison (field, op, value)
    - and: All sub-conditions must match
    - or: At least one sub-condition must match
    - not: Negates the sub-condition

    Args:
        condition: A condition dict with 'type' and type-specific fields
        alert: The Alert object to evaluate against

    Returns:
        True if the condition matches, False otherwise
    """
    cond_type = condition.get("type", "simple")

    if cond_type == "simple":
        return evaluate_simple(condition, alert)
    elif cond_type == "and":
        sub = condition.get("conditions", [])
        if not sub:
            return True  # Empty AND is vacuously true
        return all(evaluate_condition(c, alert) for c in sub)
    elif cond_type == "or":
        sub = condition.get("conditions", [])
        if not sub:
            return False  # Empty OR is vacuously false
        return any(evaluate_condition(c, alert) for c in sub)
    elif cond_type == "not":
        sub = condition.get("condition")
        if sub is None:
            logger.warning("NOT condition missing 'condition' field")
            return False
        return not evaluate_condition(sub, alert)

    logger.warning(f"Unknown condition type '{cond_type}'")
    return False


async def evaluate_alert_count_condition(
    db: AsyncSession,
    condition: dict[str, Any],
    time_window_minutes: int = 60,
    space_id: str | None = None,
) -> bool:
    """Evaluate an alert count threshold condition.

    Counts alerts matching optional filters within a time window and compares
    against the threshold using the specified operator.

    Condition structure:
    {
        "type": "simple",
        "field": "alert_count",
        "op": "gt" | "lt" | "gte" | "lte",
        "value": <threshold_number>,
        "filters": {  # optional
            "severity": "critical",
            "alert_type": "cpu_alert",
            "source": "prometheus"
        },
        "time_window_minutes": 60  # optional, defaults to 60
    }

    Args:
        db: Database session
        condition: The alert count condition dict
        time_window_minutes: Default time window if not specified in condition
        space_id: Optional space ID to filter alerts

    Returns:
        True if the alert count matches the threshold condition
    """
    op = condition.get("op", "gt")
    threshold = condition.get("value")

    if threshold is None:
        logger.warning("Alert count condition missing 'value' (threshold)")
        return False

    try:
        threshold = int(threshold)
    except (TypeError, ValueError):
        logger.warning(f"Invalid threshold value: {threshold}")
        return False

    # Get time window from condition or use default
    window_minutes = condition.get("time_window_minutes", time_window_minutes)
    try:
        window_minutes = int(window_minutes)
    except (TypeError, ValueError):
        window_minutes = time_window_minutes

    # Build the query
    cutoff_time = datetime.now(UTC) - timedelta(minutes=window_minutes)
    query = select(func.count()).select_from(Alert).where(Alert.created_at >= cutoff_time)

    # Apply optional filters
    filters = condition.get("filters", {})
    if isinstance(filters, dict):
        if "severity" in filters:
            severity_filter = filters["severity"]
            if isinstance(severity_filter, list):
                query = query.where(Alert.severity.in_(severity_filter))
            else:
                query = query.where(Alert.severity == severity_filter)

        if "alert_type" in filters or "source" in filters:
            source_filter = filters.get("alert_type") or filters.get("source")
            if isinstance(source_filter, list):
                query = query.where(Alert.source.in_(source_filter))
            else:
                query = query.where(Alert.source == source_filter)

        if "status" in filters:
            status_filter = filters["status"]
            if isinstance(status_filter, list):
                query = query.where(Alert.status.in_(status_filter))
            else:
                query = query.where(Alert.status == status_filter)

    # Apply space filter if provided
    if space_id:
        query = query.where(Alert.space_id == space_id)

    result = await db.execute(query)
    count = result.scalar() or 0

    # Compare count against threshold
    if op == "gt":
        return count > threshold
    elif op == "lt":
        return count < threshold
    elif op == "gte":
        return count >= threshold
    elif op == "lte":
        return count <= threshold
    elif op == "eq":
        return count == threshold
    elif op == "neq":
        return count != threshold

    logger.warning(f"Unknown operator '{op}' for alert count condition")
    return False


async def evaluate_condition_with_context(
    db: AsyncSession,
    condition: dict[str, Any],
    alert: Alert | None = None,
    space_id: str | None = None,
) -> bool:
    """Evaluate a condition with database context for aggregate conditions.

    This function handles both simple alert-based conditions and aggregate
    conditions that require database queries (like alert_count and trend).

    Args:
        db: Database session
        condition: The condition dict to evaluate
        alert: Optional Alert object for simple conditions
        space_id: Optional space ID for filtering

    Returns:
        True if the condition matches, False otherwise
    """
    cond_type = condition.get("type", "simple")
    field = condition.get("field", "")
    op = condition.get("op", "")

    # Handle alert_count as a special aggregate condition
    if cond_type == "simple" and field == "alert_count":
        return await evaluate_alert_count_condition(db, condition, space_id=space_id)

    # Handle trend as a special aggregate condition
    if cond_type == "simple" and op == "trend":
        return await evaluate_trend_condition(db, condition, space_id=space_id)

    # For composite conditions, recursively evaluate
    if cond_type == "and":
        sub = condition.get("conditions", [])
        if not sub:
            return True
        results = []
        for c in sub:
            result = await evaluate_condition_with_context(db, c, alert, space_id)
            results.append(result)
        return all(results)
    elif cond_type == "or":
        sub = condition.get("conditions", [])
        if not sub:
            return False
        for c in sub:
            if await evaluate_condition_with_context(db, c, alert, space_id):
                return True
        return False
    elif cond_type == "not":
        sub = condition.get("condition")
        if sub is None:
            return False
        return not await evaluate_condition_with_context(db, sub, alert, space_id)

    # For simple conditions without alert_count or trend, use the synchronous evaluator
    if alert is not None:
        return evaluate_condition(condition, alert)

    return False


async def match_triggers(
    db: AsyncSession, alert: Alert
) -> list[SceneTrigger]:
    """Find all active triggers whose conditions match this alert.

    Evaluates each active trigger's conditions against the alert, respecting
    time windows and frequency limits.

    Args:
        db: Database session
        alert: The Alert object to match against triggers

    Returns:
        List of SceneTrigger objects that match the alert
    """
    result = await db.execute(
        select(SceneTrigger).where(SceneTrigger.is_active.is_(True))
    )
    triggers = result.scalars().all()

    matched: list[SceneTrigger] = []
    for trigger in triggers:
        # Check time window constraint
        if trigger.time_window_start and trigger.time_window_end:
            now_time = datetime.now(UTC).time()
            if not (trigger.time_window_start <= now_time <= trigger.time_window_end):
                logger.debug(
                    f"Trigger '{trigger.name}' skipped: outside time window "
                    f"({trigger.time_window_start} - {trigger.time_window_end})"
                )
                continue

        # Evaluate condition with database context for aggregate conditions
        space_id = str(alert.space_id) if alert.space_id else None
        if not await evaluate_condition_with_context(
            db, trigger.condition, alert, space_id
        ):
            continue

        # Check frequency limit
        if trigger.frequency_limit:
            if not await _check_frequency(db, trigger):
                logger.debug(
                    f"Trigger '{trigger.name}' skipped: frequency limit exceeded"
                )
                continue

        matched.append(trigger)
        logger.info(f"Trigger '{trigger.name}' matched alert {alert.id}")

    return matched


async def match_triggers_for_alert_count(
    db: AsyncSession,
    space_id: str | None = None,
) -> list[SceneTrigger]:
    """Find all active triggers with alert_count conditions that match.

    This function is used for periodic evaluation of alert count thresholds,
    independent of individual alert events.

    Args:
        db: Database session
        space_id: Optional space ID to filter alerts

    Returns:
        List of SceneTrigger objects whose alert_count conditions are satisfied
    """
    result = await db.execute(
        select(SceneTrigger).where(SceneTrigger.is_active.is_(True))
    )
    triggers = result.scalars().all()

    matched: list[SceneTrigger] = []
    for trigger in triggers:
        # Check time window constraint
        if trigger.time_window_start and trigger.time_window_end:
            now_time = datetime.now(UTC).time()
            if not (trigger.time_window_start <= now_time <= trigger.time_window_end):
                continue

        # Check if this trigger has alert_count conditions
        if not _has_alert_count_condition(trigger.condition):
            continue

        # Evaluate condition with database context
        if not await evaluate_condition_with_context(
            db, trigger.condition, alert=None, space_id=space_id
        ):
            continue

        # Check frequency limit
        if trigger.frequency_limit:
            if not await _check_frequency(db, trigger):
                continue

        matched.append(trigger)
        logger.info(f"Trigger '{trigger.name}' matched alert count threshold")

    return matched


def _has_alert_count_condition(condition: dict[str, Any]) -> bool:
    """Check if a condition tree contains any alert_count conditions.

    Args:
        condition: The condition dict to check

    Returns:
        True if any alert_count condition is found
    """
    cond_type = condition.get("type", "simple")

    if cond_type == "simple":
        return condition.get("field") == "alert_count"
    elif cond_type in ("and", "or"):
        sub = condition.get("conditions", [])
        return any(_has_alert_count_condition(c) for c in sub)
    elif cond_type == "not":
        sub = condition.get("condition")
        if sub:
            return _has_alert_count_condition(sub)

    return False


async def _check_frequency(db: AsyncSession, trigger: SceneTrigger) -> bool:
    """Return True if trigger has not exceeded its frequency_limit in the last hour.

    Uses Redis-based frequency limiting for high-performance rate checking.
    Falls back to database-based checking when Redis is unavailable.

    Args:
        db: Database session
        trigger: The SceneTrigger to check

    Returns:
        True if the trigger can fire (under frequency limit), False otherwise
    """
    # Try Redis-based frequency check first
    redis_result = await _check_frequency_redis(trigger)
    if redis_result is not None:
        return redis_result

    # Fall back to database-based check
    return await _check_frequency_db(db, trigger)


async def _check_frequency_redis(trigger: SceneTrigger) -> bool | None:
    """Check frequency limit using Redis.

    Uses a sliding window counter pattern with Redis sorted sets.
    Returns None if Redis is unavailable.

    Args:
        trigger: The SceneTrigger to check

    Returns:
        True if under limit, False if exceeded, None if Redis unavailable
    """
    try:
        from src.core.redis import get_redis
        redis = await get_redis()
    except Exception as e:
        logger.debug(f"Redis unavailable for frequency check: {e}")
        return None

    try:
        # Use a sorted set with timestamps as scores for sliding window
        key = f"trigger:frequency:{trigger.id}"
        now = datetime.now(UTC)
        one_hour_ago = now - timedelta(hours=1)

        # Remove old entries outside the window
        await redis.zremrangebyscore(key, "-inf", one_hour_ago.timestamp())

        # Count current entries in the window
        count = await redis.zcard(key)

        if count >= trigger.frequency_limit:
            logger.debug(
                f"Trigger '{trigger.name}' frequency limit exceeded: "
                f"{count} >= {trigger.frequency_limit} (Redis)"
            )
            return False

        return True
    except Exception as e:
        logger.warning(f"Redis frequency check failed for trigger {trigger.id}: {e}")
        return None


async def _check_frequency_db(db: AsyncSession, trigger: SceneTrigger) -> bool:
    """Check frequency limit using database.

    Fallback method when Redis is unavailable.

    Args:
        db: Database session
        trigger: The SceneTrigger to check

    Returns:
        True if under limit, False if exceeded
    """
    from src.models.schedule import Schedule, ScheduleExecution

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


async def record_trigger_fired(
    db: AsyncSession,
    trigger: SceneTrigger,
    reason: str | None = None,
    alert: Alert | None = None,
) -> dict[str, Any]:
    """Record that a trigger has fired.

    Updates the trigger's last_triggered_at and trigger_count fields,
    and records the event in Redis for frequency limiting.

    Args:
        db: Database session
        trigger: The SceneTrigger that fired
        reason: Optional reason/description for the trigger
        alert: Optional Alert that caused the trigger

    Returns:
        Dict with trigger record information including:
        - trigger_id: UUID of the trigger
        - triggered_at: Timestamp when triggered
        - reason: Reason for triggering
        - trigger_count: Updated trigger count
    """
    now = datetime.now(UTC)

    # Build the reason string if not provided
    if reason is None:
        if alert is not None:
            reason = f"Alert {alert.id} matched condition"
        else:
            reason = "Condition matched"

    # Update trigger statistics in database
    await db.execute(
        update(SceneTrigger)
        .where(SceneTrigger.id == trigger.id)
        .values(
            last_triggered_at=now,
            trigger_count=SceneTrigger.trigger_count + 1,
        )
    )
    await db.commit()

    # Record in Redis for frequency limiting
    await _record_trigger_in_redis(trigger, now)

    # Refresh trigger to get updated count
    await db.refresh(trigger)

    record = {
        "trigger_id": str(trigger.id),
        "trigger_name": trigger.name,
        "triggered_at": now.isoformat(),
        "reason": reason,
        "trigger_count": trigger.trigger_count,
    }

    logger.info(
        f"Trigger '{trigger.name}' fired: {reason} "
        f"(total count: {trigger.trigger_count})"
    )

    return record


async def _record_trigger_in_redis(trigger: SceneTrigger, timestamp: datetime) -> None:
    """Record a trigger event in Redis for frequency limiting.

    Args:
        trigger: The SceneTrigger that fired
        timestamp: When the trigger fired
    """
    try:
        from src.core.redis import get_redis
        redis = await get_redis()
    except Exception as e:
        logger.debug(f"Redis unavailable for trigger recording: {e}")
        return

    try:
        key = f"trigger:frequency:{trigger.id}"
        # Add entry with timestamp as score and a unique member
        member = f"{timestamp.timestamp()}:{uuid.uuid4().hex[:8]}"
        await redis.zadd(key, {member: timestamp.timestamp()})

        # Set TTL on the key (2 hours to allow for cleanup)
        await redis.expire(key, 7200)
    except Exception as e:
        logger.warning(f"Failed to record trigger in Redis: {e}")


def check_time_window_validity(trigger: SceneTrigger) -> tuple[bool, str | None]:
    """Check if the current time is within the trigger's time window.

    Args:
        trigger: The SceneTrigger to check

    Returns:
        Tuple of (is_valid, reason) where:
        - is_valid: True if within time window or no window configured
        - reason: Description of why invalid, or None if valid
    """
    if not trigger.time_window_start or not trigger.time_window_end:
        return True, None

    now_time = datetime.now(UTC).time()

    # Handle time windows that span midnight
    if trigger.time_window_start <= trigger.time_window_end:
        # Normal case: start < end (e.g., 09:00 - 17:00)
        is_valid = trigger.time_window_start <= now_time <= trigger.time_window_end
    else:
        # Spans midnight: start > end (e.g., 22:00 - 06:00)
        is_valid = now_time >= trigger.time_window_start or now_time <= trigger.time_window_end

    if not is_valid:
        reason = (
            f"Outside time window: current time {now_time.strftime('%H:%M:%S')} "
            f"not in {trigger.time_window_start.strftime('%H:%M:%S')} - "
            f"{trigger.time_window_end.strftime('%H:%M:%S')}"
        )
        return False, reason

    return True, None


async def match_triggers_with_recording(
    db: AsyncSession,
    alert: Alert,
    record_triggers: bool = True,
) -> list[tuple[SceneTrigger, dict[str, Any]]]:
    """Find all active triggers matching an alert and optionally record them.

    This is an enhanced version of match_triggers that also records
    trigger events with timestamps and reasons.

    Args:
        db: Database session
        alert: The Alert object to match against triggers
        record_triggers: Whether to record trigger events (default True)

    Returns:
        List of tuples (SceneTrigger, trigger_record) where trigger_record
        contains trigger_id, triggered_at, reason, and trigger_count
    """
    result = await db.execute(
        select(SceneTrigger).where(SceneTrigger.is_active.is_(True))
    )
    triggers = result.scalars().all()

    matched: list[tuple[SceneTrigger, dict[str, Any]]] = []
    for trigger in triggers:
        # Check time window constraint
        is_valid, skip_reason = check_time_window_validity(trigger)
        if not is_valid:
            logger.debug(f"Trigger '{trigger.name}' skipped: {skip_reason}")
            continue

        # Evaluate condition with database context for aggregate conditions
        space_id = str(alert.space_id) if alert.space_id else None
        if not await evaluate_condition_with_context(
            db, trigger.condition, alert, space_id
        ):
            continue

        # Check frequency limit
        if trigger.frequency_limit:
            if not await _check_frequency(db, trigger):
                logger.debug(
                    f"Trigger '{trigger.name}' skipped: frequency limit exceeded"
                )
                continue

        # Build reason for trigger
        reason = _build_trigger_reason(trigger, alert)

        # Record the trigger event if requested
        if record_triggers:
            record = await record_trigger_fired(db, trigger, reason, alert)
        else:
            record = {
                "trigger_id": str(trigger.id),
                "trigger_name": trigger.name,
                "triggered_at": datetime.now(UTC).isoformat(),
                "reason": reason,
                "trigger_count": trigger.trigger_count,
            }

        matched.append((trigger, record))
        logger.info(f"Trigger '{trigger.name}' matched alert {alert.id}: {reason}")

    return matched


def _build_trigger_reason(trigger: SceneTrigger, alert: Alert | None = None) -> str:
    """Build a human-readable reason string for why a trigger fired.

    Args:
        trigger: The SceneTrigger that fired
        alert: Optional Alert that caused the trigger

    Returns:
        A descriptive reason string
    """
    parts = []

    # Add alert info if available
    if alert is not None:
        parts.append(f"Alert {alert.id}")
        if alert.title:
            parts.append(f"'{alert.title}'")
        if alert.severity:
            parts.append(f"[{alert.severity}]")

    # Add condition summary
    condition_summary = _summarize_condition(trigger.condition)
    if condition_summary:
        parts.append(f"matched: {condition_summary}")

    return " ".join(parts) if parts else "Condition matched"


def _summarize_condition(condition: dict[str, Any], max_depth: int = 2) -> str:
    """Generate a brief summary of a condition for logging.

    Args:
        condition: The condition dict to summarize
        max_depth: Maximum recursion depth for composite conditions

    Returns:
        A brief summary string
    """
    if max_depth <= 0:
        return "..."

    cond_type = condition.get("type", "simple")

    if cond_type == "simple":
        field = condition.get("field", "?")
        op = condition.get("op", "?")
        value = condition.get("value", "?")

        # Handle special cases
        if field == "alert_count":
            return f"alert_count {op} {value}"
        if op == "trend":
            trend_config = condition.get("trend_config", {})
            metric = trend_config.get("metric", field)
            direction = trend_config.get("direction", "?")
            return f"{metric} trend {direction}"

        # Truncate long values
        value_str = str(value)
        if len(value_str) > 20:
            value_str = value_str[:17] + "..."

        return f"{field} {op} {value_str}"

    elif cond_type == "and":
        sub = condition.get("conditions", [])
        if not sub:
            return "AND()"
        summaries = [_summarize_condition(c, max_depth - 1) for c in sub[:3]]
        if len(sub) > 3:
            summaries.append(f"...+{len(sub) - 3}")
        return f"AND({', '.join(summaries)})"

    elif cond_type == "or":
        sub = condition.get("conditions", [])
        if not sub:
            return "OR()"
        summaries = [_summarize_condition(c, max_depth - 1) for c in sub[:3]]
        if len(sub) > 3:
            summaries.append(f"...+{len(sub) - 3}")
        return f"OR({', '.join(summaries)})"

    elif cond_type == "not":
        sub = condition.get("condition")
        if sub:
            return f"NOT({_summarize_condition(sub, max_depth - 1)})"
        return "NOT(?)"

    return f"unknown({cond_type})"


# Convenience functions for specific condition types


def evaluate_alert_type_condition(
    alert: Alert, expected_types: str | list[str], operator: str = "eq"
) -> bool:
    """Evaluate an alert type condition.

    Args:
        alert: The Alert object to evaluate
        expected_types: Single type string or list of types
        operator: Comparison operator (eq, neq, in, not_in)

    Returns:
        True if the condition matches
    """
    condition = {
        "type": "simple",
        "field": "alert_type",
        "op": operator,
        "value": expected_types,
    }
    return evaluate_simple(condition, alert)


def evaluate_severity_condition(
    alert: Alert, expected_severity: str | list[str], operator: str = "eq"
) -> bool:
    """Evaluate an alert severity condition.

    Supports both equality operators (eq, neq, in, not_in) and
    level-based comparison operators (gt, lt, gte, lte).

    Args:
        alert: The Alert object to evaluate
        expected_severity: Single severity string or list of severities
        operator: Comparison operator

    Returns:
        True if the condition matches
    """
    condition = {
        "type": "simple",
        "field": "severity",
        "op": operator,
        "value": expected_severity,
    }
    return evaluate_simple(condition, alert)


# =============================================================================
# Trend Detection Functions
# =============================================================================


def _extract_metric_value(alert: Alert, metric: str) -> float | None:
    """Extract a numeric metric value from an alert.

    Searches for the metric in:
    1. Alert's raw_event dict
    2. Alert's enriched_context dict
    3. Direct alert attributes

    Args:
        alert: The Alert object to extract metric from
        metric: The metric name to extract

    Returns:
        The numeric metric value, or None if not found or not numeric
    """
    value = None

    # Check raw_event first (most common location for metrics)
    if isinstance(alert.raw_event, dict):
        value = alert.raw_event.get(metric)
        # Also check nested 'metrics' or 'data' keys
        if value is None and "metrics" in alert.raw_event:
            metrics = alert.raw_event.get("metrics", {})
            if isinstance(metrics, dict):
                value = metrics.get(metric)
        if value is None and "data" in alert.raw_event:
            data = alert.raw_event.get("data", {})
            if isinstance(data, dict):
                value = data.get(metric)

    # Check enriched_context
    if value is None and isinstance(alert.enriched_context, dict):
        value = alert.enriched_context.get(metric)

    # Check direct attributes
    if value is None and hasattr(alert, metric):
        value = getattr(alert, metric)

    # Convert to float if possible
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return None


def _calculate_trend_direction(
    values: list[float],
    threshold: float,
) -> tuple[str, float]:
    """Calculate the trend direction from a series of values.

    Uses linear regression slope to determine trend direction.
    The slope is normalized by the mean value to get a percentage change rate.

    Args:
        values: List of numeric values in chronological order
        threshold: The threshold for considering a trend significant

    Returns:
        Tuple of (direction, change_rate) where direction is one of:
        - "rising": Values are increasing above threshold
        - "falling": Values are decreasing below negative threshold
        - "flat": Values are relatively stable
        - "insufficient_data": Not enough data points
    """
    if len(values) < MIN_TREND_DATA_POINTS:
        return ("insufficient_data", 0.0)

    n = len(values)
    mean_value = statistics.mean(values)

    # Avoid division by zero for flat zero values
    if abs(mean_value) < 1e-10:
        return ("flat", 0.0)

    # Calculate linear regression slope using least squares
    # slope = sum((x - x_mean) * (y - y_mean)) / sum((x - x_mean)^2)
    x_values = list(range(n))
    x_mean = (n - 1) / 2.0

    numerator = sum((x - x_mean) * (y - mean_value) for x, y in zip(x_values, values))
    denominator = sum((x - x_mean) ** 2 for x in x_values)

    if abs(denominator) < 1e-10:
        return ("flat", 0.0)

    slope = numerator / denominator

    # Normalize slope by mean value to get percentage change per data point
    # Then multiply by n to get total percentage change over the window
    change_rate = (slope * n) / abs(mean_value)

    if change_rate > threshold:
        return ("rising", change_rate)
    elif change_rate < -threshold:
        return ("falling", change_rate)
    else:
        return ("flat", change_rate)


def _calculate_volatility(values: list[float]) -> float:
    """Calculate the volatility (coefficient of variation) of a series of values.

    Coefficient of variation = standard deviation / mean

    Args:
        values: List of numeric values

    Returns:
        The coefficient of variation (0.0 if insufficient data or zero mean)
    """
    if len(values) < MIN_TREND_DATA_POINTS:
        return 0.0

    mean_value = statistics.mean(values)
    if abs(mean_value) < 1e-10:
        return 0.0

    try:
        std_dev = statistics.stdev(values)
        return std_dev / abs(mean_value)
    except statistics.StatisticsError:
        return 0.0


async def evaluate_trend_condition(
    db: AsyncSession,
    condition: dict[str, Any],
    space_id: str | None = None,
) -> bool:
    """Evaluate a trend detection condition.

    Analyzes historical alert data to detect trends in metric values.
    Supports rising, falling, and volatile trend detection.

    Condition structure:
    {
        "type": "simple",
        "field": "...",  # optional, can use trend_config.metric instead
        "op": "trend",
        "trend_config": {
            "metric": "cpu_usage",  # metric name to analyze
            "direction": "rising" | "falling" | "volatile",
            "threshold": 0.2,  # percentage change threshold (default 0.2 = 20%)
            "window_minutes": 30,  # time window for analysis (default 30)
            "filters": {  # optional filters for alerts
                "source": "prometheus",
                "severity": ["warning", "critical"]
            }
        }
    }

    Args:
        db: Database session
        condition: The trend condition dict
        space_id: Optional space ID to filter alerts

    Returns:
        True if the trend condition is satisfied
    """
    trend_config = condition.get("trend_config", {})
    if not isinstance(trend_config, dict):
        logger.warning("Trend condition missing or invalid 'trend_config'")
        return False

    # Get metric name from trend_config or fall back to field
    metric = trend_config.get("metric") or condition.get("field")
    if not metric:
        logger.warning("Trend condition missing 'metric' in trend_config")
        return False

    # Get trend parameters with defaults
    direction = trend_config.get("direction", "rising")
    if direction not in ("rising", "falling", "volatile"):
        logger.warning(f"Invalid trend direction '{direction}', must be rising/falling/volatile")
        return False

    threshold = trend_config.get("threshold", DEFAULT_TREND_THRESHOLD)
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        threshold = DEFAULT_TREND_THRESHOLD

    window_minutes = trend_config.get("window_minutes", DEFAULT_TREND_WINDOW_MINUTES)
    try:
        window_minutes = int(window_minutes)
    except (TypeError, ValueError):
        window_minutes = DEFAULT_TREND_WINDOW_MINUTES

    # Query alerts within the time window
    cutoff_time = datetime.now(UTC) - timedelta(minutes=window_minutes)
    query = (
        select(Alert)
        .where(Alert.created_at >= cutoff_time)
        .order_by(Alert.created_at.asc())
    )

    # Apply optional filters
    filters = trend_config.get("filters", {})
    if isinstance(filters, dict):
        if "source" in filters:
            source_filter = filters["source"]
            if isinstance(source_filter, list):
                query = query.where(Alert.source.in_(source_filter))
            else:
                query = query.where(Alert.source == source_filter)

        if "severity" in filters:
            severity_filter = filters["severity"]
            if isinstance(severity_filter, list):
                query = query.where(Alert.severity.in_(severity_filter))
            else:
                query = query.where(Alert.severity == severity_filter)

        if "status" in filters:
            status_filter = filters["status"]
            if isinstance(status_filter, list):
                query = query.where(Alert.status.in_(status_filter))
            else:
                query = query.where(Alert.status == status_filter)

    # Apply space filter if provided
    if space_id:
        query = query.where(Alert.space_id == space_id)

    result = await db.execute(query)
    alerts = result.scalars().all()

    # Extract metric values from alerts
    values: list[float] = []
    for alert in alerts:
        value = _extract_metric_value(alert, metric)
        if value is not None:
            values.append(value)

    # Check if we have enough data points
    if len(values) < MIN_TREND_DATA_POINTS:
        logger.debug(
            f"Trend condition for metric '{metric}' has insufficient data: "
            f"{len(values)} points (need at least {MIN_TREND_DATA_POINTS})"
        )
        return False

    # Evaluate based on direction
    if direction == "volatile":
        volatility = _calculate_volatility(values)
        volatility_threshold = trend_config.get("volatility_threshold", DEFAULT_VOLATILITY_THRESHOLD)
        try:
            volatility_threshold = float(volatility_threshold)
        except (TypeError, ValueError):
            volatility_threshold = DEFAULT_VOLATILITY_THRESHOLD

        is_volatile = volatility > volatility_threshold
        logger.debug(
            f"Trend volatility check for '{metric}': volatility={volatility:.4f}, "
            f"threshold={volatility_threshold}, result={is_volatile}"
        )
        return is_volatile
    else:
        detected_direction, change_rate = _calculate_trend_direction(values, threshold)
        is_match = detected_direction == direction
        logger.debug(
            f"Trend direction check for '{metric}': detected={detected_direction}, "
            f"expected={direction}, change_rate={change_rate:.4f}, threshold={threshold}, "
            f"result={is_match}"
        )
        return is_match


def _has_trend_condition(condition: dict[str, Any]) -> bool:
    """Check if a condition tree contains any trend conditions.

    Args:
        condition: The condition dict to check

    Returns:
        True if any trend condition is found
    """
    cond_type = condition.get("type", "simple")

    if cond_type == "simple":
        return condition.get("op") == "trend"
    elif cond_type in ("and", "or"):
        sub = condition.get("conditions", [])
        return any(_has_trend_condition(c) for c in sub)
    elif cond_type == "not":
        sub = condition.get("condition")
        if sub:
            return _has_trend_condition(sub)

    return False


async def match_triggers_for_trend(
    db: AsyncSession,
    space_id: str | None = None,
) -> list[SceneTrigger]:
    """Find all active triggers with trend conditions that match.

    This function is used for periodic evaluation of trend conditions,
    independent of individual alert events.

    Args:
        db: Database session
        space_id: Optional space ID to filter alerts

    Returns:
        List of SceneTrigger objects whose trend conditions are satisfied
    """
    result = await db.execute(
        select(SceneTrigger).where(SceneTrigger.is_active.is_(True))
    )
    triggers = result.scalars().all()

    matched: list[SceneTrigger] = []
    for trigger in triggers:
        # Check time window constraint
        if trigger.time_window_start and trigger.time_window_end:
            now_time = datetime.now(UTC).time()
            if not (trigger.time_window_start <= now_time <= trigger.time_window_end):
                continue

        # Check if this trigger has trend conditions
        if not _has_trend_condition(trigger.condition):
            continue

        # Evaluate condition with database context
        if not await evaluate_condition_with_context(
            db, trigger.condition, alert=None, space_id=space_id
        ):
            continue

        # Check frequency limit
        if trigger.frequency_limit:
            if not await _check_frequency(db, trigger):
                continue

        matched.append(trigger)
        logger.info(f"Trigger '{trigger.name}' matched trend condition")

    return matched


# Convenience function for trend evaluation
def evaluate_trend_condition_sync(
    values: list[float],
    direction: str,
    threshold: float = DEFAULT_TREND_THRESHOLD,
    volatility_threshold: float = DEFAULT_VOLATILITY_THRESHOLD,
) -> bool:
    """Synchronous helper to evaluate trend from pre-extracted values.

    Useful for testing or when values are already available.

    Args:
        values: List of numeric values in chronological order
        direction: Expected trend direction ("rising", "falling", "volatile")
        threshold: Threshold for rising/falling detection
        volatility_threshold: Threshold for volatility detection

    Returns:
        True if the trend matches the expected direction
    """
    if len(values) < MIN_TREND_DATA_POINTS:
        return False

    if direction == "volatile":
        volatility = _calculate_volatility(values)
        return volatility > volatility_threshold
    else:
        detected_direction, _ = _calculate_trend_direction(values, threshold)
        return detected_direction == direction
