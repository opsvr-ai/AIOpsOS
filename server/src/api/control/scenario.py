"""Scenario CRUD API endpoints.

This module provides comprehensive API endpoints for managing scenarios,
including creation, retrieval, update, and deletion with proper validation
for scenario types (command, natural_language, hybrid).

Also provides:
- Scenario execution APIs for manually triggering executions and querying execution records
- Resource association APIs for managing scenario relationships with tools, agents,
  knowledge documents, and notification channels

Requirements covered: 1.1-1.6, 4.1-4.7, 5.1, 5.2
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.api.deps import (
    DbSession,
    get_current_user,
    get_optional_space_id,
    require_perm,
)
from src.models.agent import (
    Agent,
    Scenario,
    Tool,
)
from src.models.channel import NotificationChannel
from src.models.knowledge import KnowledgeDocument
from src.models.scenario import ScenarioExecution
from src.schemas.scenario import (
    ScenarioCreate,
    ScenarioDetailResponse,
    ScenarioExecutionResponse,
    ScenarioResponse,
    ScenarioType,
    ScenarioUpdate,
    TriggerType,
)
from src.services.scenario_execution import ScenarioExecutionEngine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scenarios", tags=["scenarios"])


# =============================================================================
# Helper Functions
# =============================================================================


def _validate_scenario_type_fields(
    scenario_type: str,
    trigger_command: str | None,
    nl_prompt: str | None,
) -> None:
    """Validate that required fields are present based on scenario type.

    Requirements 1.2, 1.3, 1.4, 1.5, 1.6: Validates type-field consistency
    and returns clear validation error messages.

    Args:
        scenario_type: The scenario type (command, natural_language, hybrid)
        trigger_command: The trigger command for command/hybrid types
        nl_prompt: The natural language prompt for natural_language/hybrid types

    Raises:
        HTTPException: If validation fails with a 422 status code
    """
    if scenario_type == ScenarioType.COMMAND.value:
        if not trigger_command:
            raise HTTPException(
                status_code=422,
                detail="trigger_command is required for command type scenarios",
            )
        if not trigger_command.startswith("/"):
            raise HTTPException(
                status_code=422,
                detail="trigger_command must start with '/' for command type scenarios",
            )
    elif scenario_type == ScenarioType.NATURAL_LANGUAGE.value:
        if not nl_prompt:
            raise HTTPException(
                status_code=422,
                detail="nl_prompt is required for natural_language type scenarios",
            )
    elif scenario_type == ScenarioType.HYBRID.value:
        if not trigger_command and not nl_prompt:
            raise HTTPException(
                status_code=422,
                detail=(
                    "At least one of trigger_command or nl_prompt is required "
                    "for hybrid type scenarios"
                ),
            )
        if trigger_command and not trigger_command.startswith("/"):
            raise HTTPException(
                status_code=422,
                detail=(
                    "trigger_command must start with '/' when provided "
                    "for hybrid type scenarios"
                ),
            )
    else:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid scenario_type '{scenario_type}'. "
                "Must be one of: command, natural_language, hybrid"
            ),
        )


async def _load_scenario_with_relations(db: DbSession, scenario_id: str) -> Scenario | None:
    """Load a scenario with all its relationships.

    Args:
        db: Database session
        scenario_id: The scenario ID to load

    Returns:
        The scenario with loaded relationships, or None if not found
    """
    result = await db.execute(
        select(Scenario)
        .where(Scenario.id == scenario_id)
        .options(
            selectinload(Scenario.tools),
            selectinload(Scenario.agents),
            selectinload(Scenario.knowledge_docs),
            selectinload(Scenario.notification_channels),
        )
    )
    return result.scalar_one_or_none()


def _scenario_to_detail_response(scenario: Scenario) -> dict[str, Any]:
    """Convert a scenario model to a detailed response dict.

    Args:
        scenario: The scenario model instance

    Returns:
        Dictionary representation with related resources
    """
    return {
        "id": scenario.id,
        "name": scenario.name,
        "description": scenario.description,
        "scenario_type": scenario.scenario_type,
        "trigger_command": scenario.trigger_command,
        "nl_prompt": scenario.nl_prompt,
        "params_schema": scenario.params_schema,
        "execution_timeout": scenario.execution_timeout,
        "is_active": scenario.is_active,
        "enable_collaboration": scenario.enable_collaboration,
        "collaboration_config": scenario.collaboration_config,
        "template_id": scenario.template_id,
        "space_id": scenario.space_id,
        "created_at": scenario.created_at,
        "updated_at": scenario.updated_at,
        "tools": [
            {"id": str(t.id), "name": t.name, "type": t.type, "description": t.description}
            for t in scenario.tools
        ],
        "agents": [
            {"id": str(a.id), "name": a.name, "type": a.type, "agent_type": a.agent_type}
            for a in scenario.agents
        ],
        "knowledge_docs": [
            {"id": str(d.id), "title": d.title, "doc_type": d.doc_type}
            for d in scenario.knowledge_docs
        ],
        "notification_channels": [
            {"id": str(c.id), "name": c.name, "channel_type": c.channel_type}
            for c in scenario.notification_channels
        ],
    }


async def _associate_resources(
    db: DbSession,
    scenario: Scenario,
    tool_ids: list[str] | None,
    agent_ids: list[str] | None,
    knowledge_doc_ids: list[str] | None,
    channel_ids: list[str] | None,
) -> None:
    """Associate resources with a scenario.

    Args:
        db: Database session
        scenario: The scenario to associate resources with
        tool_ids: List of tool IDs to associate
        agent_ids: List of agent IDs to associate
        knowledge_doc_ids: List of knowledge document IDs to associate
        channel_ids: List of notification channel IDs to associate
    """
    if tool_ids is not None:
        if tool_ids:
            result = await db.execute(select(Tool).where(Tool.id.in_(tool_ids)))
            scenario.tools = list(result.scalars().all())
        else:
            scenario.tools = []

    if agent_ids is not None:
        if agent_ids:
            result = await db.execute(select(Agent).where(Agent.id.in_(agent_ids)))
            scenario.agents = list(result.scalars().all())
        else:
            scenario.agents = []

    if knowledge_doc_ids is not None:
        if knowledge_doc_ids:
            result = await db.execute(
                select(KnowledgeDocument).where(KnowledgeDocument.id.in_(knowledge_doc_ids))
            )
            scenario.knowledge_docs = list(result.scalars().all())
        else:
            scenario.knowledge_docs = []

    if channel_ids is not None:
        if channel_ids:
            result = await db.execute(
                select(NotificationChannel).where(NotificationChannel.id.in_(channel_ids))
            )
            scenario.notification_channels = list(result.scalars().all())
        else:
            scenario.notification_channels = []


# =============================================================================
# CRUD Endpoints
# =============================================================================


@router.get("", response_model=list[ScenarioResponse])
async def list_scenarios(
    db: DbSession,
    _=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
    is_active: bool | None = Query(None, description="Filter by active status"),
    scenario_type: str | None = Query(None, description="Filter by scenario type"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of records to return"),
):
    """List all scenarios with optional filtering and pagination.

    Args:
        db: Database session
        space_id: Optional space ID filter
        is_active: Optional active status filter
        scenario_type: Optional scenario type filter (command, natural_language, hybrid)
        skip: Number of records to skip for pagination
        limit: Maximum number of records to return

    Returns:
        List of scenarios matching the filters

    Requirements: 1.1 (supports three scenario types)
    """
    query = select(Scenario)

    # Apply filters
    if space_id:
        query = query.where((Scenario.space_id == space_id) | (Scenario.space_id.is_(None)))
    if is_active is not None:
        query = query.where(Scenario.is_active == is_active)
    if scenario_type:
        # Validate scenario type
        valid_types = {t.value for t in ScenarioType}
        if scenario_type not in valid_types:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid scenario_type '{scenario_type}'. "
                    f"Must be one of: {', '.join(valid_types)}"
                ),
            )
        query = query.where(Scenario.scenario_type == scenario_type)

    # Apply pagination and ordering
    query = query.order_by(Scenario.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/count")
async def count_scenarios(
    db: DbSession,
    _=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
    is_active: bool | None = Query(None, description="Filter by active status"),
    scenario_type: str | None = Query(None, description="Filter by scenario type"),
):
    """Get the total count of scenarios matching the filters.

    Args:
        db: Database session
        space_id: Optional space ID filter
        is_active: Optional active status filter
        scenario_type: Optional scenario type filter

    Returns:
        Dictionary with total count
    """
    query = select(func.count(Scenario.id))

    if space_id:
        query = query.where((Scenario.space_id == space_id) | (Scenario.space_id.is_(None)))
    if is_active is not None:
        query = query.where(Scenario.is_active == is_active)
    if scenario_type:
        valid_types = {t.value for t in ScenarioType}
        if scenario_type not in valid_types:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid scenario_type '{scenario_type}'. "
                    f"Must be one of: {', '.join(valid_types)}"
                ),
            )
        query = query.where(Scenario.scenario_type == scenario_type)

    result = await db.execute(query)
    total = result.scalar() or 0
    return {"total": total}


@router.post("", response_model=ScenarioDetailResponse, status_code=201)
async def create_scenario(
    body: ScenarioCreate,
    db: DbSession,
    _=Depends(require_perm("scenarios", "create")),
):
    """Create a new scenario with type validation.

    Args:
        body: Scenario creation data
        db: Database session

    Returns:
        The created scenario with all relationships

    Raises:
        HTTPException: 422 if validation fails, 409 if name already exists

    Requirements: 1.1-1.6 (scenario type system with validation)
    """
    # Validate scenario type and required fields
    _validate_scenario_type_fields(
        body.scenario_type.value,
        body.trigger_command,
        body.nl_prompt,
    )

    # Check for duplicate name
    existing = await db.execute(select(Scenario).where(Scenario.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Scenario with name '{body.name}' already exists",
        )

    # Prepare scenario data
    data = body.model_dump(exclude={"tool_ids", "agent_ids", "knowledge_doc_ids", "channel_ids"})

    # Convert enum to string value
    data["scenario_type"] = body.scenario_type.value

    # Handle collaboration_config
    if data.get("collaboration_config"):
        data["collaboration_config"] = data["collaboration_config"]
    else:
        data["collaboration_config"] = {}

    # Convert space_id to UUID if provided
    if data.get("space_id"):
        try:
            data["space_id"] = uuid.UUID(data["space_id"])
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid space_id format")

    # Create scenario
    scenario = Scenario(**data)
    db.add(scenario)
    await db.flush()

    # Associate resources
    await _associate_resources(
        db,
        scenario,
        body.tool_ids,
        body.agent_ids,
        body.knowledge_doc_ids,
        body.channel_ids,
    )

    await db.commit()

    # Reload with relationships
    scenario = await _load_scenario_with_relations(db, str(scenario.id))
    return _scenario_to_detail_response(scenario)


@router.get("/{scenario_id}", response_model=ScenarioDetailResponse)
async def get_scenario(
    scenario_id: str,
    db: DbSession,
    _=Depends(get_current_user),
):
    """Get a scenario by ID with all related resources.

    Args:
        scenario_id: The scenario ID
        db: Database session

    Returns:
        The scenario with all relationships

    Raises:
        HTTPException: 404 if scenario not found
    """
    # Validate UUID format
    try:
        uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")

    scenario = await _load_scenario_with_relations(db, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    return _scenario_to_detail_response(scenario)


@router.put("/{scenario_id}", response_model=ScenarioDetailResponse)
async def update_scenario(
    scenario_id: str,
    body: ScenarioUpdate,
    db: DbSession,
    _=Depends(require_perm("scenarios", "update")),
):
    """Update an existing scenario.

    Args:
        scenario_id: The scenario ID to update
        body: Scenario update data
        db: Database session

    Returns:
        The updated scenario with all relationships

    Raises:
        HTTPException: 404 if not found, 422 if validation fails, 409 if name conflict

    Requirements: 1.5, 1.6 (validates type-field consistency on update)
    """
    # Validate UUID format
    try:
        uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")

    scenario = await _load_scenario_with_relations(db, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Get update data
    update_data = body.model_dump(exclude_unset=True)

    # Determine the effective scenario type and fields for validation
    effective_type = (
        update_data.get("scenario_type").value
        if update_data.get("scenario_type")
        else scenario.scenario_type
    )
    effective_trigger_command = update_data.get("trigger_command", scenario.trigger_command)
    effective_nl_prompt = update_data.get("nl_prompt", scenario.nl_prompt)

    # Validate type-field consistency if type or relevant fields are being updated
    if any(k in update_data for k in ["scenario_type", "trigger_command", "nl_prompt"]):
        _validate_scenario_type_fields(
            effective_type,
            effective_trigger_command,
            effective_nl_prompt,
        )

    # Check for name conflict if name is being updated
    if "name" in update_data and update_data["name"] != scenario.name:
        existing = await db.execute(
            select(Scenario).where(
                Scenario.name == update_data["name"],
                Scenario.id != scenario.id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail=f"Scenario with name '{update_data['name']}' already exists",
            )

    # Extract resource IDs
    tool_ids = update_data.pop("tool_ids", None)
    agent_ids = update_data.pop("agent_ids", None)
    knowledge_doc_ids = update_data.pop("knowledge_doc_ids", None)
    channel_ids = update_data.pop("channel_ids", None)

    # Convert enum to string value if present
    if "scenario_type" in update_data and update_data["scenario_type"]:
        update_data["scenario_type"] = update_data["scenario_type"].value

    # Convert space_id to UUID if provided
    if "space_id" in update_data and update_data["space_id"]:
        try:
            update_data["space_id"] = uuid.UUID(update_data["space_id"])
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid space_id format")

    # Update scalar fields
    for key, value in update_data.items():
        if value is not None or key in ["description", "nl_prompt", "template_id"]:
            setattr(scenario, key, value)

    # Update resource associations
    await _associate_resources(
        db,
        scenario,
        tool_ids,
        agent_ids,
        knowledge_doc_ids,
        channel_ids,
    )

    await db.commit()

    # Reload with relationships
    scenario = await _load_scenario_with_relations(db, scenario_id)
    return _scenario_to_detail_response(scenario)


@router.delete("/{scenario_id}")
async def delete_scenario(
    scenario_id: str,
    db: DbSession,
    _=Depends(require_perm("scenarios", "delete")),
):
    """Delete a scenario by ID.

    Args:
        scenario_id: The scenario ID to delete
        db: Database session

    Returns:
        Confirmation message

    Raises:
        HTTPException: 404 if scenario not found
    """
    # Validate UUID format
    try:
        uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")

    result = await db.execute(select(Scenario).where(Scenario.id == scenario_id))
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    await db.delete(scenario)
    await db.commit()

    logger.info(f"Deleted scenario: {scenario_id} ({scenario.name})")
    return {"detail": "Scenario deleted successfully", "id": scenario_id}


# =============================================================================
# Batch Operations
# =============================================================================


@router.post("/batch-delete")
async def batch_delete_scenarios(
    body: dict,
    db: DbSession,
    _=Depends(require_perm("scenarios", "delete")),
):
    """Delete multiple scenarios by IDs.

    Args:
        body: Dictionary with 'scenario_ids' list
        db: Database session

    Returns:
        Summary of deleted scenarios

    Raises:
        HTTPException: 422 if no IDs provided
    """
    scenario_ids = body.get("scenario_ids", [])
    if not scenario_ids:
        raise HTTPException(status_code=422, detail="No scenario_ids provided")

    # Validate UUID formats
    valid_ids = []
    for sid in scenario_ids:
        try:
            uuid.UUID(sid)
            valid_ids.append(sid)
        except ValueError:
            logger.warning(f"Invalid scenario_id format in batch delete: {sid}")

    if not valid_ids:
        raise HTTPException(status_code=422, detail="No valid scenario_ids provided")

    # Find and delete scenarios
    result = await db.execute(select(Scenario).where(Scenario.id.in_(valid_ids)))
    scenarios = result.scalars().all()

    deleted_ids = []
    for scenario in scenarios:
        await db.delete(scenario)
        deleted_ids.append(str(scenario.id))

    await db.commit()

    logger.info(f"Batch deleted {len(deleted_ids)} scenarios")
    return {
        "detail": f"Deleted {len(deleted_ids)} scenarios",
        "deleted_ids": deleted_ids,
        "not_found_ids": [sid for sid in valid_ids if sid not in deleted_ids],
    }


@router.patch("/batch-update-status")
async def batch_update_scenario_status(
    body: dict,
    db: DbSession,
    _=Depends(require_perm("scenarios", "update")),
):
    """Update the active status of multiple scenarios.

    Args:
        body: Dictionary with 'scenario_ids' list and 'is_active' boolean
        db: Database session

    Returns:
        Summary of updated scenarios

    Raises:
        HTTPException: 422 if required fields missing
    """
    scenario_ids = body.get("scenario_ids", [])
    is_active = body.get("is_active")

    if not scenario_ids:
        raise HTTPException(status_code=422, detail="No scenario_ids provided")
    if is_active is None:
        raise HTTPException(status_code=422, detail="is_active field is required")

    # Validate UUID formats
    valid_ids = []
    for sid in scenario_ids:
        try:
            uuid.UUID(sid)
            valid_ids.append(sid)
        except ValueError:
            logger.warning(f"Invalid scenario_id format in batch update: {sid}")

    if not valid_ids:
        raise HTTPException(status_code=422, detail="No valid scenario_ids provided")

    # Find and update scenarios
    result = await db.execute(select(Scenario).where(Scenario.id.in_(valid_ids)))
    scenarios = result.scalars().all()

    updated_ids = []
    for scenario in scenarios:
        scenario.is_active = is_active
        updated_ids.append(str(scenario.id))

    await db.commit()

    logger.info(f"Batch updated status for {len(updated_ids)} scenarios to is_active={is_active}")
    return {
        "detail": f"Updated {len(updated_ids)} scenarios",
        "updated_ids": updated_ids,
        "not_found_ids": [sid for sid in valid_ids if sid not in updated_ids],
    }


# =============================================================================
# Scenario Execution Endpoints
# =============================================================================


class ExecuteTriggerRequest(BaseModel):
    """Request body for manually triggering scenario execution."""

    params: dict[str, Any] = Field(default_factory=dict, description="Input parameters for execution")
    triggered_by: str | None = Field(None, description="User or system that triggered the execution")


@router.post("/{scenario_id}/execute", response_model=ScenarioExecutionResponse, status_code=201)
async def execute_scenario(
    scenario_id: str,
    body: ExecuteTriggerRequest,
    db: DbSession,
    user=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
):
    """Manually trigger a scenario execution.

    Creates a new execution record and starts the scenario execution process.
    The execution runs asynchronously and the response returns immediately
    with the execution record in 'pending' or 'running' status.

    Args:
        scenario_id: The scenario ID to execute
        body: Execution parameters and trigger info
        db: Database session
        user: Current authenticated user
        space_id: Optional workspace scope

    Returns:
        The created ScenarioExecution record

    Raises:
        HTTPException: 404 if scenario not found, 422 if validation fails,
                      400 if scenario is not active

    Requirements: 5.1 (manual trigger of scenario execution)
    """
    # Validate UUID format
    try:
        scenario_uuid = uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")

    # Check if scenario exists and is active
    result = await db.execute(select(Scenario).where(Scenario.id == scenario_uuid))
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    if not scenario.is_active:
        raise HTTPException(
            status_code=400,
            detail="Cannot execute inactive scenario. Please activate the scenario first."
        )

    # Determine triggered_by
    triggered_by = body.triggered_by
    if not triggered_by and user:
        triggered_by = getattr(user, "username", None) or getattr(user, "email", None) or str(user.id)

    # Convert space_id to UUID if provided
    space_uuid = None
    if space_id:
        try:
            space_uuid = uuid.UUID(space_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid space_id format")

    # Create execution engine and trigger execution
    engine = ScenarioExecutionEngine(db)
    execution = await engine.trigger_manual(
        scenario_id=scenario_uuid,
        params=body.params,
        triggered_by=triggered_by,
        space_id=space_uuid,
    )

    logger.info(
        f"Manually triggered scenario execution: scenario={scenario_id}, "
        f"execution={execution.id}, triggered_by={triggered_by}"
    )

    return execution


@router.get("/{scenario_id}/executions", response_model=list[ScenarioExecutionResponse])
async def list_scenario_executions(
    scenario_id: str,
    db: DbSession,
    _=Depends(get_current_user),
    status: str | None = Query(None, description="Filter by execution status"),
    trigger_type: str | None = Query(None, description="Filter by trigger type"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Maximum number of records to return"),
):
    """List execution records for a specific scenario.

    Returns execution records ordered by creation time (newest first).
    Supports filtering by status and trigger type.

    Args:
        scenario_id: The scenario ID to list executions for
        db: Database session
        status: Optional filter by execution status (pending, running, completed, failed, timeout)
        trigger_type: Optional filter by trigger type (manual, schedule, trigger_rule)
        skip: Number of records to skip for pagination
        limit: Maximum number of records to return

    Returns:
        List of ScenarioExecution records

    Raises:
        HTTPException: 404 if scenario not found, 422 if validation fails

    Requirements: 5.1, 5.2 (query execution records)
    """
    # Validate UUID format
    try:
        scenario_uuid = uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")

    # Check if scenario exists
    result = await db.execute(select(Scenario).where(Scenario.id == scenario_uuid))
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Build query
    query = select(ScenarioExecution).where(ScenarioExecution.scenario_id == scenario_uuid)

    # Apply filters
    if status:
        valid_statuses = {"pending", "running", "completed", "failed", "timeout"}
        if status not in valid_statuses:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status '{status}'. Must be one of: {', '.join(valid_statuses)}"
            )
        query = query.where(ScenarioExecution.status == status)

    if trigger_type:
        valid_trigger_types = {t.value for t in TriggerType}
        if trigger_type not in valid_trigger_types:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid trigger_type '{trigger_type}'. Must be one of: {', '.join(valid_trigger_types)}"
            )
        query = query.where(ScenarioExecution.trigger_type == trigger_type)

    # Apply pagination and ordering
    query = query.order_by(ScenarioExecution.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/executions/{execution_id}", response_model=ScenarioExecutionResponse)
async def get_execution(
    execution_id: str,
    db: DbSession,
    _=Depends(get_current_user),
):
    """Get execution details by execution ID.

    Returns the full execution record including status, parameters,
    results, and logs.

    Args:
        execution_id: The execution ID to retrieve
        db: Database session

    Returns:
        The ScenarioExecution record with full details

    Raises:
        HTTPException: 404 if execution not found, 422 if validation fails

    Requirements: 5.1, 5.2 (query execution records)
    """
    # Validate UUID format
    try:
        execution_uuid = uuid.UUID(execution_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid execution_id format")

    result = await db.execute(
        select(ScenarioExecution).where(ScenarioExecution.id == execution_uuid)
    )
    execution = result.scalar_one_or_none()
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")

    return execution


@router.get("/{scenario_id}/executions/count")
async def count_scenario_executions(
    scenario_id: str,
    db: DbSession,
    _=Depends(get_current_user),
    status: str | None = Query(None, description="Filter by execution status"),
    trigger_type: str | None = Query(None, description="Filter by trigger type"),
):
    """Get the total count of executions for a scenario.

    Args:
        scenario_id: The scenario ID to count executions for
        db: Database session
        status: Optional filter by execution status
        trigger_type: Optional filter by trigger type

    Returns:
        Dictionary with total count

    Raises:
        HTTPException: 404 if scenario not found, 422 if validation fails
    """
    # Validate UUID format
    try:
        scenario_uuid = uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")

    # Check if scenario exists
    result = await db.execute(select(Scenario).where(Scenario.id == scenario_uuid))
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Build count query
    query = select(func.count(ScenarioExecution.id)).where(
        ScenarioExecution.scenario_id == scenario_uuid
    )

    # Apply filters
    if status:
        valid_statuses = {"pending", "running", "completed", "failed", "timeout"}
        if status not in valid_statuses:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status '{status}'. Must be one of: {', '.join(valid_statuses)}"
            )
        query = query.where(ScenarioExecution.status == status)

    if trigger_type:
        valid_trigger_types = {t.value for t in TriggerType}
        if trigger_type not in valid_trigger_types:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid trigger_type '{trigger_type}'. Must be one of: {', '.join(valid_trigger_types)}"
            )
        query = query.where(ScenarioExecution.trigger_type == trigger_type)

    result = await db.execute(query)
    total = result.scalar() or 0
    return {"total": total}


# =============================================================================
# Resource Association Endpoints
# =============================================================================


@router.get("/{scenario_id}/resources")
async def get_scenario_resources(
    scenario_id: str,
    db: DbSession,
    _=Depends(get_current_user),
):
    """Get all resources associated with a scenario.

    Returns all tools, agents, knowledge documents, and notification channels
    associated with the specified scenario.

    Args:
        scenario_id: The scenario ID
        db: Database session

    Returns:
        ScenarioResourcesResponse with all associated resources

    Raises:
        HTTPException: 404 if scenario not found, 422 if invalid ID format

    Requirements: 4.7 - Provides API to query scenario's associated resources
    """
    # Validate UUID format
    try:
        uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")

    scenario = await _load_scenario_with_relations(db, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Build response with all associated resources
    tools = [
        {
            "id": str(t.id),
            "name": t.name,
            "type": t.type,
            "description": t.description,
            "category": t.category,
        }
        for t in scenario.tools
    ]

    agents = [
        {
            "id": str(a.id),
            "name": a.name,
            "type": a.type,
            "agent_type": a.agent_type,
        }
        for a in scenario.agents
    ]

    knowledge_docs = [
        {
            "id": str(d.id),
            "title": d.title,
            "doc_type": d.doc_type,
        }
        for d in scenario.knowledge_docs
    ]

    notification_channels = [
        {
            "id": str(c.id),
            "name": c.name,
            "channel_type": c.channel_type,
        }
        for c in scenario.notification_channels
    ]

    total_resources = len(tools) + len(agents) + len(knowledge_docs) + len(notification_channels)

    logger.info(f"Retrieved {total_resources} resources for scenario: {scenario_id}")
    return {
        "scenario_id": str(scenario.id),
        "scenario_name": scenario.name,
        "tools": tools,
        "agents": agents,
        "knowledge_docs": knowledge_docs,
        "notification_channels": notification_channels,
        "total_resources": total_resources,
    }


@router.put("/{scenario_id}/tools")
async def set_scenario_tools(
    scenario_id: str,
    body: dict,
    db: DbSession,
    _=Depends(require_perm("scenarios", "update")),
):
    """Set the tools associated with a scenario.

    Replaces all existing tool associations with the provided list.
    Pass an empty list to remove all tool associations.

    Args:
        scenario_id: The scenario ID
        body: Dictionary with 'ids' list of tool IDs
        db: Database session

    Returns:
        ResourceAssociationResponse with updated associations

    Raises:
        HTTPException: 404 if scenario not found, 422 if invalid ID format

    Requirements: 4.1 - Supports scenario-tool many-to-many association
    """
    # Validate UUID format
    try:
        uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")

    tool_ids = body.get("ids", [])

    # Validate tool IDs format
    valid_tool_ids = []
    for tid in tool_ids:
        try:
            uuid.UUID(tid)
            valid_tool_ids.append(tid)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid tool_id format: {tid}")

    scenario = await _load_scenario_with_relations(db, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Update tool associations
    if valid_tool_ids:
        result = await db.execute(select(Tool).where(Tool.id.in_(valid_tool_ids)))
        scenario.tools = list(result.scalars().all())
    else:
        scenario.tools = []

    await db.commit()

    associated_ids = [str(t.id) for t in scenario.tools]
    logger.info(
        f"Updated tool associations for scenario {scenario_id}: {len(associated_ids)} tools"
    )

    return {
        "scenario_id": str(scenario.id),
        "resource_type": "tools",
        "associated_ids": associated_ids,
        "total": len(associated_ids),
        "message": f"Successfully associated {len(associated_ids)} tools with scenario",
    }


@router.put("/{scenario_id}/agents")
async def set_scenario_agents(
    scenario_id: str,
    body: dict,
    db: DbSession,
    _=Depends(require_perm("scenarios", "update")),
):
    """Set the agents associated with a scenario.

    Replaces all existing agent associations with the provided list.
    Pass an empty list to remove all agent associations.

    Args:
        scenario_id: The scenario ID
        body: Dictionary with 'ids' list of agent IDs
        db: Database session

    Returns:
        ResourceAssociationResponse with updated associations

    Raises:
        HTTPException: 404 if scenario not found, 422 if invalid ID format

    Requirements: 4.2 - Supports scenario-agent many-to-many association
    """
    # Validate UUID format
    try:
        uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")

    agent_ids = body.get("ids", [])

    # Validate agent IDs format
    valid_agent_ids = []
    for aid in agent_ids:
        try:
            uuid.UUID(aid)
            valid_agent_ids.append(aid)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid agent_id format: {aid}")

    scenario = await _load_scenario_with_relations(db, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Update agent associations
    if valid_agent_ids:
        result = await db.execute(select(Agent).where(Agent.id.in_(valid_agent_ids)))
        scenario.agents = list(result.scalars().all())
    else:
        scenario.agents = []

    await db.commit()

    associated_ids = [str(a.id) for a in scenario.agents]
    logger.info(
        f"Updated agent associations for scenario {scenario_id}: {len(associated_ids)} agents"
    )

    return {
        "scenario_id": str(scenario.id),
        "resource_type": "agents",
        "associated_ids": associated_ids,
        "total": len(associated_ids),
        "message": f"Successfully associated {len(associated_ids)} agents with scenario",
    }


@router.put("/{scenario_id}/knowledge-docs")
async def set_scenario_knowledge_docs(
    scenario_id: str,
    body: dict,
    db: DbSession,
    _=Depends(require_perm("scenarios", "update")),
):
    """Set the knowledge documents associated with a scenario.

    Replaces all existing knowledge document associations with the provided list.
    Pass an empty list to remove all knowledge document associations.

    Args:
        scenario_id: The scenario ID
        body: Dictionary with 'ids' list of knowledge document IDs
        db: Database session

    Returns:
        ResourceAssociationResponse with updated associations

    Raises:
        HTTPException: 404 if scenario not found, 422 if invalid ID format

    Requirements: 4.3 - Supports scenario-knowledge document many-to-many association
    """
    # Validate UUID format
    try:
        uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")

    doc_ids = body.get("ids", [])

    # Validate document IDs format
    valid_doc_ids = []
    for did in doc_ids:
        try:
            uuid.UUID(did)
            valid_doc_ids.append(did)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid document_id format: {did}")

    scenario = await _load_scenario_with_relations(db, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Update knowledge document associations
    if valid_doc_ids:
        result = await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id.in_(valid_doc_ids))
        )
        scenario.knowledge_docs = list(result.scalars().all())
    else:
        scenario.knowledge_docs = []

    await db.commit()

    associated_ids = [str(d.id) for d in scenario.knowledge_docs]
    logger.info(
        f"Updated knowledge doc associations for scenario {scenario_id}: {len(associated_ids)} docs"
    )

    return {
        "scenario_id": str(scenario.id),
        "resource_type": "knowledge_docs",
        "associated_ids": associated_ids,
        "total": len(associated_ids),
        "message": (
            f"Successfully associated {len(associated_ids)} "
            "knowledge documents with scenario"
        ),
    }


@router.put("/{scenario_id}/channels")
async def set_scenario_channels(
    scenario_id: str,
    body: dict,
    db: DbSession,
    _=Depends(require_perm("scenarios", "update")),
):
    """Set the notification channels associated with a scenario.

    Replaces all existing notification channel associations with the provided list.
    Pass an empty list to remove all notification channel associations.

    Args:
        scenario_id: The scenario ID
        body: Dictionary with 'ids' list of notification channel IDs
        db: Database session

    Returns:
        ResourceAssociationResponse with updated associations

    Raises:
        HTTPException: 404 if scenario not found, 422 if invalid ID format

    Requirements: 4.4 - Supports scenario-notification channel many-to-many association
    """
    # Validate UUID format
    try:
        uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")

    channel_ids = body.get("ids", [])

    # Validate channel IDs format
    valid_channel_ids = []
    for cid in channel_ids:
        try:
            uuid.UUID(cid)
            valid_channel_ids.append(cid)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid channel_id format: {cid}")

    scenario = await _load_scenario_with_relations(db, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Update notification channel associations
    if valid_channel_ids:
        result = await db.execute(
            select(NotificationChannel).where(NotificationChannel.id.in_(valid_channel_ids))
        )
        scenario.notification_channels = list(result.scalars().all())
    else:
        scenario.notification_channels = []

    await db.commit()

    associated_ids = [str(c.id) for c in scenario.notification_channels]
    logger.info(
        f"Updated channel associations for scenario {scenario_id}: {len(associated_ids)} channels"
    )

    return {
        "scenario_id": str(scenario.id),
        "resource_type": "notification_channels",
        "associated_ids": associated_ids,
        "total": len(associated_ids),
        "message": (
            f"Successfully associated {len(associated_ids)} "
            "notification channels with scenario"
        ),
    }


# =============================================================================
# Resource Association POST/DELETE Endpoints (Add/Remove Individual Resources)
# =============================================================================


@router.post("/{scenario_id}/tools/{tool_id}", status_code=201)
async def add_scenario_tool(
    scenario_id: str,
    tool_id: str,
    db: DbSession,
    _=Depends(require_perm("scenarios", "update")),
):
    """Add a single tool to a scenario's associations.

    Args:
        scenario_id: The scenario ID
        tool_id: The tool ID to add
        db: Database session

    Returns:
        Confirmation with updated tool list

    Raises:
        HTTPException: 404 if scenario or tool not found, 422 if invalid ID format,
                      409 if tool already associated

    Requirements: 4.1 - Supports scenario-tool many-to-many association
    """
    # Validate UUID formats
    try:
        uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")
    try:
        uuid.UUID(tool_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid tool_id format")

    scenario = await _load_scenario_with_relations(db, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Check if tool exists
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")

    # Check if already associated
    existing_ids = {str(t.id) for t in scenario.tools}
    if tool_id in existing_ids:
        raise HTTPException(status_code=409, detail="Tool already associated with scenario")

    # Add the tool
    scenario.tools.append(tool)
    await db.commit()

    associated_ids = [str(t.id) for t in scenario.tools]
    logger.info(f"Added tool {tool_id} to scenario {scenario_id}")

    return {
        "scenario_id": str(scenario.id),
        "resource_type": "tools",
        "added_id": tool_id,
        "associated_ids": associated_ids,
        "total": len(associated_ids),
        "message": f"Successfully added tool to scenario",
    }


@router.delete("/{scenario_id}/tools/{tool_id}")
async def remove_scenario_tool(
    scenario_id: str,
    tool_id: str,
    db: DbSession,
    _=Depends(require_perm("scenarios", "update")),
):
    """Remove a single tool from a scenario's associations.

    Args:
        scenario_id: The scenario ID
        tool_id: The tool ID to remove
        db: Database session

    Returns:
        Confirmation with updated tool list

    Raises:
        HTTPException: 404 if scenario not found or tool not associated,
                      422 if invalid ID format

    Requirements: 4.1 - Supports scenario-tool many-to-many association
    """
    # Validate UUID formats
    try:
        uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")
    try:
        uuid.UUID(tool_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid tool_id format")

    scenario = await _load_scenario_with_relations(db, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Find and remove the tool
    tool_to_remove = None
    for t in scenario.tools:
        if str(t.id) == tool_id:
            tool_to_remove = t
            break

    if tool_to_remove is None:
        raise HTTPException(status_code=404, detail="Tool not associated with scenario")

    scenario.tools.remove(tool_to_remove)
    await db.commit()

    associated_ids = [str(t.id) for t in scenario.tools]
    logger.info(f"Removed tool {tool_id} from scenario {scenario_id}")

    return {
        "scenario_id": str(scenario.id),
        "resource_type": "tools",
        "removed_id": tool_id,
        "associated_ids": associated_ids,
        "total": len(associated_ids),
        "message": f"Successfully removed tool from scenario",
    }


@router.post("/{scenario_id}/agents/{agent_id}", status_code=201)
async def add_scenario_agent(
    scenario_id: str,
    agent_id: str,
    db: DbSession,
    _=Depends(require_perm("scenarios", "update")),
):
    """Add a single agent to a scenario's associations.

    Args:
        scenario_id: The scenario ID
        agent_id: The agent ID to add
        db: Database session

    Returns:
        Confirmation with updated agent list

    Raises:
        HTTPException: 404 if scenario or agent not found, 422 if invalid ID format,
                      409 if agent already associated

    Requirements: 4.2 - Supports scenario-agent many-to-many association
    """
    # Validate UUID formats
    try:
        uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")
    try:
        uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid agent_id format")

    scenario = await _load_scenario_with_relations(db, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Check if agent exists
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Check if already associated
    existing_ids = {str(a.id) for a in scenario.agents}
    if agent_id in existing_ids:
        raise HTTPException(status_code=409, detail="Agent already associated with scenario")

    # Add the agent
    scenario.agents.append(agent)
    await db.commit()

    associated_ids = [str(a.id) for a in scenario.agents]
    logger.info(f"Added agent {agent_id} to scenario {scenario_id}")

    return {
        "scenario_id": str(scenario.id),
        "resource_type": "agents",
        "added_id": agent_id,
        "associated_ids": associated_ids,
        "total": len(associated_ids),
        "message": f"Successfully added agent to scenario",
    }


@router.delete("/{scenario_id}/agents/{agent_id}")
async def remove_scenario_agent(
    scenario_id: str,
    agent_id: str,
    db: DbSession,
    _=Depends(require_perm("scenarios", "update")),
):
    """Remove a single agent from a scenario's associations.

    Args:
        scenario_id: The scenario ID
        agent_id: The agent ID to remove
        db: Database session

    Returns:
        Confirmation with updated agent list

    Raises:
        HTTPException: 404 if scenario not found or agent not associated,
                      422 if invalid ID format

    Requirements: 4.2 - Supports scenario-agent many-to-many association
    """
    # Validate UUID formats
    try:
        uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")
    try:
        uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid agent_id format")

    scenario = await _load_scenario_with_relations(db, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Find and remove the agent
    agent_to_remove = None
    for a in scenario.agents:
        if str(a.id) == agent_id:
            agent_to_remove = a
            break

    if agent_to_remove is None:
        raise HTTPException(status_code=404, detail="Agent not associated with scenario")

    scenario.agents.remove(agent_to_remove)
    await db.commit()

    associated_ids = [str(a.id) for a in scenario.agents]
    logger.info(f"Removed agent {agent_id} from scenario {scenario_id}")

    return {
        "scenario_id": str(scenario.id),
        "resource_type": "agents",
        "removed_id": agent_id,
        "associated_ids": associated_ids,
        "total": len(associated_ids),
        "message": f"Successfully removed agent from scenario",
    }


@router.post("/{scenario_id}/knowledge-docs/{doc_id}", status_code=201)
async def add_scenario_knowledge_doc(
    scenario_id: str,
    doc_id: str,
    db: DbSession,
    _=Depends(require_perm("scenarios", "update")),
):
    """Add a single knowledge document to a scenario's associations.

    Args:
        scenario_id: The scenario ID
        doc_id: The knowledge document ID to add
        db: Database session

    Returns:
        Confirmation with updated knowledge document list

    Raises:
        HTTPException: 404 if scenario or document not found, 422 if invalid ID format,
                      409 if document already associated

    Requirements: 4.3 - Supports scenario-knowledge document many-to-many association
    """
    # Validate UUID formats
    try:
        uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")
    try:
        uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid document_id format")

    scenario = await _load_scenario_with_relations(db, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Check if document exists
    result = await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id))
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="Knowledge document not found")

    # Check if already associated
    existing_ids = {str(d.id) for d in scenario.knowledge_docs}
    if doc_id in existing_ids:
        raise HTTPException(
            status_code=409, detail="Knowledge document already associated with scenario"
        )

    # Add the document
    scenario.knowledge_docs.append(doc)
    await db.commit()

    associated_ids = [str(d.id) for d in scenario.knowledge_docs]
    logger.info(f"Added knowledge document {doc_id} to scenario {scenario_id}")

    return {
        "scenario_id": str(scenario.id),
        "resource_type": "knowledge_docs",
        "added_id": doc_id,
        "associated_ids": associated_ids,
        "total": len(associated_ids),
        "message": f"Successfully added knowledge document to scenario",
    }


@router.delete("/{scenario_id}/knowledge-docs/{doc_id}")
async def remove_scenario_knowledge_doc(
    scenario_id: str,
    doc_id: str,
    db: DbSession,
    _=Depends(require_perm("scenarios", "update")),
):
    """Remove a single knowledge document from a scenario's associations.

    Args:
        scenario_id: The scenario ID
        doc_id: The knowledge document ID to remove
        db: Database session

    Returns:
        Confirmation with updated knowledge document list

    Raises:
        HTTPException: 404 if scenario not found or document not associated,
                      422 if invalid ID format

    Requirements: 4.3 - Supports scenario-knowledge document many-to-many association
    """
    # Validate UUID formats
    try:
        uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")
    try:
        uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid document_id format")

    scenario = await _load_scenario_with_relations(db, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Find and remove the document
    doc_to_remove = None
    for d in scenario.knowledge_docs:
        if str(d.id) == doc_id:
            doc_to_remove = d
            break

    if doc_to_remove is None:
        raise HTTPException(
            status_code=404, detail="Knowledge document not associated with scenario"
        )

    scenario.knowledge_docs.remove(doc_to_remove)
    await db.commit()

    associated_ids = [str(d.id) for d in scenario.knowledge_docs]
    logger.info(f"Removed knowledge document {doc_id} from scenario {scenario_id}")

    return {
        "scenario_id": str(scenario.id),
        "resource_type": "knowledge_docs",
        "removed_id": doc_id,
        "associated_ids": associated_ids,
        "total": len(associated_ids),
        "message": f"Successfully removed knowledge document from scenario",
    }


@router.post("/{scenario_id}/channels/{channel_id}", status_code=201)
async def add_scenario_channel(
    scenario_id: str,
    channel_id: str,
    db: DbSession,
    _=Depends(require_perm("scenarios", "update")),
):
    """Add a single notification channel to a scenario's associations.

    Args:
        scenario_id: The scenario ID
        channel_id: The notification channel ID to add
        db: Database session

    Returns:
        Confirmation with updated channel list

    Raises:
        HTTPException: 404 if scenario or channel not found, 422 if invalid ID format,
                      409 if channel already associated

    Requirements: 4.4 - Supports scenario-notification channel many-to-many association
    """
    # Validate UUID formats
    try:
        uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")
    try:
        uuid.UUID(channel_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid channel_id format")

    scenario = await _load_scenario_with_relations(db, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Check if channel exists
    result = await db.execute(
        select(NotificationChannel).where(NotificationChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="Notification channel not found")

    # Check if already associated
    existing_ids = {str(c.id) for c in scenario.notification_channels}
    if channel_id in existing_ids:
        raise HTTPException(
            status_code=409, detail="Notification channel already associated with scenario"
        )

    # Add the channel
    scenario.notification_channels.append(channel)
    await db.commit()

    associated_ids = [str(c.id) for c in scenario.notification_channels]
    logger.info(f"Added notification channel {channel_id} to scenario {scenario_id}")

    return {
        "scenario_id": str(scenario.id),
        "resource_type": "notification_channels",
        "added_id": channel_id,
        "associated_ids": associated_ids,
        "total": len(associated_ids),
        "message": f"Successfully added notification channel to scenario",
    }


@router.delete("/{scenario_id}/channels/{channel_id}")
async def remove_scenario_channel(
    scenario_id: str,
    channel_id: str,
    db: DbSession,
    _=Depends(require_perm("scenarios", "update")),
):
    """Remove a single notification channel from a scenario's associations.

    Args:
        scenario_id: The scenario ID
        channel_id: The notification channel ID to remove
        db: Database session

    Returns:
        Confirmation with updated channel list

    Raises:
        HTTPException: 404 if scenario not found or channel not associated,
                      422 if invalid ID format

    Requirements: 4.4 - Supports scenario-notification channel many-to-many association
    """
    # Validate UUID formats
    try:
        uuid.UUID(scenario_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid scenario_id format")
    try:
        uuid.UUID(channel_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid channel_id format")

    scenario = await _load_scenario_with_relations(db, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Find and remove the channel
    channel_to_remove = None
    for c in scenario.notification_channels:
        if str(c.id) == channel_id:
            channel_to_remove = c
            break

    if channel_to_remove is None:
        raise HTTPException(
            status_code=404, detail="Notification channel not associated with scenario"
        )

    scenario.notification_channels.remove(channel_to_remove)
    await db.commit()

    associated_ids = [str(c.id) for c in scenario.notification_channels]
    logger.info(f"Removed notification channel {channel_id} from scenario {scenario_id}")

    return {
        "scenario_id": str(scenario.id),
        "resource_type": "notification_channels",
        "removed_id": channel_id,
        "associated_ids": associated_ids,
        "total": len(associated_ids),
        "message": f"Successfully removed notification channel from scenario",
    }
