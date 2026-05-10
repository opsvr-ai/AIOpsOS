"""Schedule and Trigger Rule API endpoints.

This module provides comprehensive API endpoints for managing schedules and trigger rules,
including enhanced trigger condition support for:
- Alert count threshold conditions
- Alert type and severity conditions
- Trend detection conditions (rising, falling, volatile)
- Combination conditions (AND, OR, NOT)
- Frequency limiting configuration
- Time window constraints

Requirements covered: 3.1-3.8 (Enhanced Trigger Conditions)
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update

from src.api.deps import DbSession, get_current_user, get_optional_space_id, require_perm
from src.models.agent import Scenario
from src.models.schedule import SceneTrigger, Schedule, ScheduleExecution
from src.schemas.schedule import (
    ScheduleCreate,
    ScheduleExecutionOut,
    ScheduleOut,
    ScheduleUpdate,
    TriggerBulkActionRequest,
    TriggerBulkActionResponse,
    TriggerConditionValidateRequest,
    TriggerConditionValidateResponse,
    TriggerCreate,
    TriggerOut,
    TriggerStatisticsOut,
    TriggerTestRequest,
    TriggerTestResponse,
    TriggerUpdate,
    validate_trigger_condition,
)
from src.services.cron_scheduler import compute_next_run

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Schedules ────────────────────────────────────────────

@router.get("/schedules", response_model=list[ScheduleOut])
async def list_schedules(
    db: DbSession,
    _=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
):
    query = select(Schedule)
    if space_id:
        query = query.where(Schedule.space_id == space_id)
    result = await db.execute(query.order_by(Schedule.created_at.desc()))
    return result.scalars().all()


@router.post("/schedules", response_model=ScheduleOut)
async def create_schedule(
    body: ScheduleCreate, db: DbSession, _=Depends(require_perm("schedules", "create"))
):
    sched = Schedule(**body.model_dump())
    sched.next_run = compute_next_run(sched.cron_expression)
    db.add(sched)
    await db.commit()
    await db.refresh(sched)
    return sched


@router.get("/schedules/{schedule_id}", response_model=ScheduleOut)
async def get_schedule(schedule_id: str, db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    sched = result.scalar_one_or_none()
    if sched is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return sched


@router.patch("/schedules/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    schedule_id: str, body: ScheduleUpdate, db: DbSession,
    _=Depends(require_perm("schedules", "update"))
):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    sched = result.scalar_one_or_none()
    if sched is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    for key, val in body.model_dump(exclude_unset=True).items():
        setattr(sched, key, val)
    # Recompute next_run if cron expression or active state changed
    if body.cron_expression is not None or body.is_active is not None:
        sched.next_run = compute_next_run(sched.cron_expression)
    await db.commit()
    await db.refresh(sched)
    return sched


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(
    schedule_id: str, db: DbSession, _=Depends(require_perm("schedules", "delete"))
):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    sched = result.scalar_one_or_none()
    if sched is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await db.delete(sched)
    await db.commit()
    return {"detail": "deleted"}


@router.get("/schedules/{schedule_id}/executions", response_model=list[ScheduleExecutionOut])
async def list_executions(schedule_id: str, db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(
        select(ScheduleExecution)
        .where(ScheduleExecution.schedule_id == schedule_id)
        .order_by(ScheduleExecution.created_at.desc())
        .limit(50)
    )
    return result.scalars().all()


# =============================================================================
# Trigger Rule Helper Functions
# =============================================================================


def _analyze_condition(condition: dict[str, Any]) -> dict[str, Any]:
    """Analyze a trigger condition and extract metadata.

    Args:
        condition: The condition dict to analyze

    Returns:
        Dictionary with analysis results including:
        - condition_type: The type of the root condition
        - has_alert_count_condition: Whether alert_count conditions exist
        - has_trend_condition: Whether trend conditions exist
        - has_composite_condition: Whether composite (and/or/not) conditions exist
        - operators_used: List of operators used in the condition tree
        - fields_referenced: List of fields referenced in the condition tree
    """
    operators: set[str] = set()
    fields: set[str] = set()
    has_alert_count = False
    has_trend = False
    has_composite = False

    def _traverse(cond: dict[str, Any]) -> None:
        nonlocal has_alert_count, has_trend, has_composite

        cond_type = cond.get("type", "simple")

        if cond_type in ("and", "or", "not"):
            has_composite = True

        if cond_type == "simple":
            field = cond.get("field", "")
            op = cond.get("op", "")

            if field:
                fields.add(field)
            if op:
                operators.add(op)

            if field == "alert_count":
                has_alert_count = True
            if op == "trend":
                has_trend = True

        elif cond_type in ("and", "or"):
            for sub in cond.get("conditions", []):
                _traverse(sub)
        elif cond_type == "not":
            sub = cond.get("condition")
            if sub:
                _traverse(sub)

    _traverse(condition)

    return {
        "condition_type": condition.get("type", "simple"),
        "has_alert_count_condition": has_alert_count,
        "has_trend_condition": has_trend,
        "has_composite_condition": has_composite,
        "operators_used": sorted(operators),
        "fields_referenced": sorted(fields),
    }


def _generate_condition_summary(condition: dict[str, Any]) -> str:
    """Generate a human-readable summary of a trigger condition.

    Args:
        condition: The condition dict to summarize

    Returns:
        A human-readable summary string
    """
    cond_type = condition.get("type", "simple")

    if cond_type == "simple":
        field = condition.get("field", "unknown")
        op = condition.get("op", "eq")
        value = condition.get("value", "")

        if op == "trend":
            trend_config = condition.get("trend_config", {})
            metric = trend_config.get("metric", "unknown")
            direction = trend_config.get("direction", "unknown")
            threshold = trend_config.get("threshold", 0)
            window = trend_config.get("window_minutes", 30)
            return f"Trend: {metric} {direction} by {threshold*100:.0f}% over {window} minutes"

        op_names = {
            "eq": "equals",
            "neq": "not equals",
            "in": "in",
            "not_in": "not in",
            "contains": "contains",
            "gt": ">",
            "lt": "<",
            "gte": ">=",
            "lte": "<=",
            "regex": "matches regex",
        }
        op_name = op_names.get(op, op)
        return f"{field} {op_name} {value}"

    elif cond_type == "and":
        sub_conditions = condition.get("conditions", [])
        sub_summaries = [_generate_condition_summary(c) for c in sub_conditions]
        return f"({' AND '.join(sub_summaries)})"

    elif cond_type == "or":
        sub_conditions = condition.get("conditions", [])
        sub_summaries = [_generate_condition_summary(c) for c in sub_conditions]
        return f"({' OR '.join(sub_summaries)})"

    elif cond_type == "not":
        sub = condition.get("condition", {})
        return f"NOT ({_generate_condition_summary(sub)})"

    return "Unknown condition"


async def _validate_scenario_exists(db: DbSession, scenario_id: str) -> None:
    """Validate that a scenario exists.

    Args:
        db: Database session
        scenario_id: The scenario ID to validate

    Raises:
        HTTPException: 404 if scenario not found, 422 if invalid UUID format
    """
    try:
        scenario_uuid = uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")

    result = await db.execute(select(Scenario).where(Scenario.id == scenario_uuid))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found")


# =============================================================================
# Trigger Rule CRUD Endpoints
# =============================================================================


@router.get("/triggers", response_model=list[TriggerOut])
async def list_triggers(
    db: DbSession,
    _=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
    scenario_id: str | None = Query(None, description="Filter by scenario ID"),
    is_active: bool | None = Query(None, description="Filter by active status"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of records to return"),
):
    """List all trigger rules with optional filtering and pagination.

    Supports filtering by space, scenario, and active status.

    Args:
        db: Database session
        space_id: Optional space ID filter
        scenario_id: Optional scenario ID filter
        is_active: Optional active status filter
        skip: Number of records to skip for pagination
        limit: Maximum number of records to return

    Returns:
        List of trigger rules matching the filters

    Requirements: 3.1-3.8 (supports enhanced trigger conditions)
    """
    query = select(SceneTrigger)

    # Apply filters
    if space_id:
        query = query.where(
            (SceneTrigger.space_id == space_id) | (SceneTrigger.space_id.is_(None))
        )
    if scenario_id:
        try:
            scenario_uuid = uuid.UUID(scenario_id)
            query = query.where(SceneTrigger.scenario_id == scenario_uuid)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid scenario_id format")
    if is_active is not None:
        query = query.where(SceneTrigger.is_active == is_active)

    # Apply pagination and ordering
    query = query.order_by(SceneTrigger.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/triggers/count")
async def count_triggers(
    db: DbSession,
    _=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
    scenario_id: str | None = Query(None, description="Filter by scenario ID"),
    is_active: bool | None = Query(None, description="Filter by active status"),
):
    """Get the total count of trigger rules matching the filters.

    Args:
        db: Database session
        space_id: Optional space ID filter
        scenario_id: Optional scenario ID filter
        is_active: Optional active status filter

    Returns:
        Dictionary with total count
    """
    query = select(func.count(SceneTrigger.id))

    if space_id:
        query = query.where(
            (SceneTrigger.space_id == space_id) | (SceneTrigger.space_id.is_(None))
        )
    if scenario_id:
        try:
            scenario_uuid = uuid.UUID(scenario_id)
            query = query.where(SceneTrigger.scenario_id == scenario_uuid)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid scenario_id format")
    if is_active is not None:
        query = query.where(SceneTrigger.is_active == is_active)

    result = await db.execute(query)
    total = result.scalar() or 0
    return {"total": total}


@router.post("/triggers", response_model=TriggerOut, status_code=201)
async def create_trigger(
    body: TriggerCreate,
    db: DbSession,
    _=Depends(require_perm("triggers", "create")),
    space_id: str | None = Depends(get_optional_space_id),
):
    """Create a new trigger rule with enhanced condition support.

    Supports creating triggers with:
    - Simple conditions (field, operator, value)
    - Trend detection conditions (rising, falling, volatile)
    - Combination conditions (AND, OR, NOT)
    - Frequency limiting configuration
    - Time window constraints

    Args:
        body: Trigger creation data with validated condition
        db: Database session
        space_id: Optional workspace scope

    Returns:
        The created trigger rule

    Raises:
        HTTPException: 422 if validation fails, 404 if scenario not found

    Requirements: 3.1-3.8 (enhanced trigger conditions)
    """
    # Validate scenario exists
    await _validate_scenario_exists(db, body.scenario_id)

    # Prepare trigger data
    trigger_data = body.model_dump()

    # Convert scenario_id to UUID
    trigger_data["scenario_id"] = uuid.UUID(body.scenario_id)

    # Set space_id if provided
    if space_id:
        try:
            trigger_data["space_id"] = uuid.UUID(space_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid space_id format")

    # Create trigger
    trigger = SceneTrigger(**trigger_data)
    db.add(trigger)
    await db.commit()
    await db.refresh(trigger)

    logger.info(
        f"Created trigger rule: id={trigger.id}, name={trigger.name}, "
        f"scenario_id={trigger.scenario_id}"
    )

    return trigger


@router.get("/triggers/{trigger_id}", response_model=TriggerOut)
async def get_trigger(
    trigger_id: str,
    db: DbSession,
    _=Depends(get_current_user),
):
    """Get a trigger rule by ID.

    Args:
        trigger_id: The trigger ID to retrieve
        db: Database session

    Returns:
        The trigger rule with full details

    Raises:
        HTTPException: 404 if trigger not found, 422 if invalid UUID format
    """
    try:
        trigger_uuid = uuid.UUID(trigger_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid trigger_id format")

    result = await db.execute(select(SceneTrigger).where(SceneTrigger.id == trigger_uuid))
    trigger = result.scalar_one_or_none()
    if trigger is None:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return trigger


@router.patch("/triggers/{trigger_id}", response_model=TriggerOut)
async def update_trigger(
    trigger_id: str,
    body: TriggerUpdate,
    db: DbSession,
    _=Depends(require_perm("triggers", "update")),
):
    """Update an existing trigger rule.

    Supports updating:
    - Basic fields (name, description, is_active)
    - Condition with enhanced validation
    - Frequency limiting configuration
    - Time window constraints

    Args:
        trigger_id: The trigger ID to update
        body: Trigger update data
        db: Database session

    Returns:
        The updated trigger rule

    Raises:
        HTTPException: 404 if not found, 422 if validation fails

    Requirements: 3.1-3.8 (enhanced trigger conditions)
    """
    try:
        trigger_uuid = uuid.UUID(trigger_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid trigger_id format")

    result = await db.execute(select(SceneTrigger).where(SceneTrigger.id == trigger_uuid))
    trigger = result.scalar_one_or_none()
    if trigger is None:
        raise HTTPException(status_code=404, detail="Trigger not found")

    # Get update data
    update_data = body.model_dump(exclude_unset=True)

    # Validate scenario exists if being updated
    if "scenario_id" in update_data and update_data["scenario_id"]:
        await _validate_scenario_exists(db, update_data["scenario_id"])
        update_data["scenario_id"] = uuid.UUID(update_data["scenario_id"])

    # Update fields
    for key, val in update_data.items():
        setattr(trigger, key, val)

    await db.commit()
    await db.refresh(trigger)

    logger.info(f"Updated trigger rule: id={trigger_id}, fields={list(update_data.keys())}")

    return trigger


@router.delete("/triggers/{trigger_id}")
async def delete_trigger(
    trigger_id: str,
    db: DbSession,
    _=Depends(require_perm("triggers", "delete")),
):
    """Delete a trigger rule by ID.

    Args:
        trigger_id: The trigger ID to delete
        db: Database session

    Returns:
        Confirmation message

    Raises:
        HTTPException: 404 if trigger not found, 422 if invalid UUID format
    """
    try:
        trigger_uuid = uuid.UUID(trigger_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid trigger_id format")

    result = await db.execute(select(SceneTrigger).where(SceneTrigger.id == trigger_uuid))
    trigger = result.scalar_one_or_none()
    if trigger is None:
        raise HTTPException(status_code=404, detail="Trigger not found")

    trigger_name = trigger.name
    await db.delete(trigger)
    await db.commit()

    logger.info(f"Deleted trigger rule: id={trigger_id}, name={trigger_name}")

    return {"detail": "Trigger deleted successfully", "id": trigger_id}


# =============================================================================
# Enhanced Trigger Rule Endpoints
# =============================================================================


@router.post("/triggers/validate-condition", response_model=TriggerConditionValidateResponse)
async def validate_trigger_condition_endpoint(
    body: TriggerConditionValidateRequest,
    _=Depends(get_current_user),
):
    """Validate a trigger condition structure without saving.

    Use this endpoint to validate condition syntax and structure before
    creating or updating a trigger rule. Returns detailed analysis of
    the condition including:
    - Whether the condition is valid
    - Condition type and summary
    - Whether it contains alert_count, trend, or composite conditions
    - List of operators and fields used

    Args:
        body: Request containing the condition to validate

    Returns:
        Validation result with condition analysis

    Requirements: 3.1-3.8 (validates enhanced condition types)
    """
    try:
        # Validate the condition structure
        validate_trigger_condition(body.condition)

        # Analyze the condition
        analysis = _analyze_condition(body.condition)
        summary = _generate_condition_summary(body.condition)

        return TriggerConditionValidateResponse(
            valid=True,
            condition_summary=summary,
            condition_type=analysis["condition_type"],
            has_alert_count_condition=analysis["has_alert_count_condition"],
            has_trend_condition=analysis["has_trend_condition"],
            has_composite_condition=analysis["has_composite_condition"],
            operators_used=analysis["operators_used"],
            fields_referenced=analysis["fields_referenced"],
            error=None,
        )
    except ValueError as e:
        return TriggerConditionValidateResponse(
            valid=False,
            condition_summary="Invalid condition",
            condition_type="unknown",
            has_alert_count_condition=False,
            has_trend_condition=False,
            has_composite_condition=False,
            operators_used=[],
            fields_referenced=[],
            error=str(e),
        )


@router.post("/triggers/test-condition", response_model=TriggerTestResponse)
async def test_trigger_condition(
    body: TriggerTestRequest,
    db: DbSession,
    _=Depends(get_current_user),
):
    """Test a trigger condition against sample alert data.

    Use this endpoint to test whether a condition would match a given
    alert without actually creating a trigger. Useful for debugging
    and validating trigger configurations.

    Note: This endpoint only tests simple field-based conditions.
    Alert count and trend conditions require actual database data
    and cannot be fully tested with sample data.

    Args:
        body: Request containing condition and test alert data
        db: Database session

    Returns:
        Test result indicating whether the condition matched

    Requirements: 3.1-3.5 (tests condition evaluation)
    """
    try:
        # Import the trigger engine for condition evaluation
        from src.services.trigger_engine import evaluate_condition

        # Create a mock alert object for testing
        class MockAlert:
            def __init__(self, data: dict[str, Any]):
                self.raw_event = data
                # Set common alert fields
                for key, value in data.items():
                    setattr(self, key, value)

        mock_alert = MockAlert(body.test_alert)

        # Analyze the condition
        analysis = _analyze_condition(body.condition)

        # Evaluate the condition
        matched = evaluate_condition(body.condition, mock_alert)

        # Build evaluation details
        evaluation_details: dict[str, Any] = {
            "condition_type": analysis["condition_type"],
            "fields_checked": analysis["fields_referenced"],
            "operators_used": analysis["operators_used"],
        }

        # Add warnings for conditions that can't be fully tested
        warnings = []
        if analysis["has_alert_count_condition"]:
            warnings.append(
                "Alert count conditions require actual database data and "
                "cannot be fully tested with sample data"
            )
        if analysis["has_trend_condition"]:
            warnings.append(
                "Trend conditions require historical metric data and "
                "cannot be fully tested with sample data"
            )
        if warnings:
            evaluation_details["warnings"] = warnings

        return TriggerTestResponse(
            matched=matched,
            condition_type=analysis["condition_type"],
            evaluation_details=evaluation_details,
            error=None,
        )
    except Exception as e:
        logger.warning(f"Trigger condition test failed: {e}")
        return TriggerTestResponse(
            matched=False,
            condition_type="unknown",
            evaluation_details={},
            error=str(e),
        )


@router.get("/triggers/{trigger_id}/statistics", response_model=TriggerStatisticsOut)
async def get_trigger_statistics(
    trigger_id: str,
    db: DbSession,
    _=Depends(get_current_user),
):
    """Get statistics for a specific trigger rule.

    Returns trigger statistics including:
    - Total trigger count
    - Last triggered timestamp
    - Active status
    - Frequency limit configuration
    - Time window status

    Args:
        trigger_id: The trigger ID to get statistics for
        db: Database session

    Returns:
        Trigger statistics

    Raises:
        HTTPException: 404 if trigger not found, 422 if invalid UUID format

    Requirements: 3.8 (records trigger time and reason)
    """
    try:
        trigger_uuid = uuid.UUID(trigger_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid trigger_id format")

    result = await db.execute(select(SceneTrigger).where(SceneTrigger.id == trigger_uuid))
    trigger = result.scalar_one_or_none()
    if trigger is None:
        raise HTTPException(status_code=404, detail="Trigger not found")

    # Determine if time window is currently active
    time_window_active = False
    if trigger.time_window_start and trigger.time_window_end:
        now_time = datetime.now(UTC).time()
        if trigger.time_window_start <= trigger.time_window_end:
            time_window_active = trigger.time_window_start <= now_time <= trigger.time_window_end
        else:
            # Spans midnight
            time_window_active = now_time >= trigger.time_window_start or now_time <= trigger.time_window_end

    return TriggerStatisticsOut(
        trigger_id=str(trigger.id),
        trigger_name=trigger.name,
        trigger_count=trigger.trigger_count,
        last_triggered_at=trigger.last_triggered_at,
        is_active=trigger.is_active,
        frequency_limit=trigger.frequency_limit,
        time_window_active=time_window_active,
        created_at=trigger.created_at,
    )


@router.post("/triggers/{trigger_id}/reset-statistics")
async def reset_trigger_statistics(
    trigger_id: str,
    db: DbSession,
    _=Depends(require_perm("triggers", "update")),
):
    """Reset statistics for a specific trigger rule.

    Resets the trigger count and last triggered timestamp.

    Args:
        trigger_id: The trigger ID to reset statistics for
        db: Database session

    Returns:
        Confirmation message

    Raises:
        HTTPException: 404 if trigger not found, 422 if invalid UUID format
    """
    try:
        trigger_uuid = uuid.UUID(trigger_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid trigger_id format")

    result = await db.execute(select(SceneTrigger).where(SceneTrigger.id == trigger_uuid))
    trigger = result.scalar_one_or_none()
    if trigger is None:
        raise HTTPException(status_code=404, detail="Trigger not found")

    # Reset statistics
    trigger.trigger_count = 0
    trigger.last_triggered_at = None

    await db.commit()

    logger.info(f"Reset statistics for trigger: id={trigger_id}")

    return {
        "detail": "Trigger statistics reset successfully",
        "id": trigger_id,
        "trigger_count": 0,
        "last_triggered_at": None,
    }


@router.post("/triggers/bulk-action", response_model=TriggerBulkActionResponse)
async def bulk_action_triggers(
    body: TriggerBulkActionRequest,
    db: DbSession,
    _=Depends(require_perm("triggers", "update")),
):
    """Perform bulk enable/disable action on multiple trigger rules.

    Args:
        body: Request containing trigger IDs and action (enable/disable)
        db: Database session

    Returns:
        Summary of the bulk action results

    Requirements: 3.6 (supports frequency limiting and activation control)
    """
    # Validate and convert trigger IDs
    valid_ids: list[uuid.UUID] = []
    invalid_ids: list[str] = []

    for tid in body.trigger_ids:
        try:
            valid_ids.append(uuid.UUID(tid))
        except ValueError:
            invalid_ids.append(tid)

    if not valid_ids:
        return TriggerBulkActionResponse(
            success_count=0,
            failed_count=len(body.trigger_ids),
            failed_ids=body.trigger_ids,
            message="No valid trigger IDs provided",
        )

    # Determine the new active state
    new_active_state = body.action == "enable"

    # Update triggers
    result = await db.execute(
        update(SceneTrigger)
        .where(SceneTrigger.id.in_(valid_ids))
        .values(is_active=new_active_state)
        .returning(SceneTrigger.id)
    )
    updated_ids = [str(row[0]) for row in result.fetchall()]
    await db.commit()

    # Calculate results
    success_count = len(updated_ids)
    failed_ids = invalid_ids + [
        str(tid) for tid in valid_ids if str(tid) not in updated_ids
    ]
    failed_count = len(failed_ids)

    action_verb = "enabled" if new_active_state else "disabled"
    logger.info(f"Bulk {action_verb} {success_count} triggers")

    return TriggerBulkActionResponse(
        success_count=success_count,
        failed_count=failed_count,
        failed_ids=failed_ids,
        message=f"Successfully {action_verb} {success_count} trigger(s)",
    )


@router.get("/triggers/by-scenario/{scenario_id}", response_model=list[TriggerOut])
async def list_triggers_by_scenario(
    scenario_id: str,
    db: DbSession,
    _=Depends(get_current_user),
    is_active: bool | None = Query(None, description="Filter by active status"),
):
    """List all trigger rules for a specific scenario.

    Args:
        scenario_id: The scenario ID to list triggers for
        db: Database session
        is_active: Optional active status filter

    Returns:
        List of trigger rules for the scenario

    Raises:
        HTTPException: 404 if scenario not found, 422 if invalid UUID format
    """
    # Validate scenario exists
    await _validate_scenario_exists(db, scenario_id)

    scenario_uuid = uuid.UUID(scenario_id)
    query = select(SceneTrigger).where(SceneTrigger.scenario_id == scenario_uuid)

    if is_active is not None:
        query = query.where(SceneTrigger.is_active == is_active)

    query = query.order_by(SceneTrigger.created_at.desc())

    result = await db.execute(query)
    return result.scalars().all()
