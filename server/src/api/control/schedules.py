from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from src.api.deps import DbSession, get_current_user, get_optional_space_id, require_perm
from src.models.schedule import SceneTrigger, Schedule, ScheduleExecution
from src.schemas.schedule import (
    ScheduleCreate,
    ScheduleExecutionOut,
    ScheduleOut,
    ScheduleUpdate,
    TriggerCreate,
    TriggerOut,
    TriggerUpdate,
)
from src.services.cron_scheduler import compute_next_run

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


# ── Triggers ──────────────────────────────────────────────

@router.get("/triggers", response_model=list[TriggerOut])
async def list_triggers(
    db: DbSession,
    _=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
):
    query = select(SceneTrigger)
    if space_id:
        query = query.where(SceneTrigger.space_id == space_id)
    result = await db.execute(query.order_by(SceneTrigger.created_at.desc()))
    return result.scalars().all()


@router.post("/triggers", response_model=TriggerOut)
async def create_trigger(
    body: TriggerCreate, db: DbSession, _=Depends(require_perm("triggers", "create"))
):
    trigger = SceneTrigger(**body.model_dump())
    db.add(trigger)
    await db.commit()
    await db.refresh(trigger)
    return trigger


@router.get("/triggers/{trigger_id}", response_model=TriggerOut)
async def get_trigger(trigger_id: str, db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(select(SceneTrigger).where(SceneTrigger.id == trigger_id))
    trigger = result.scalar_one_or_none()
    if trigger is None:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return trigger


@router.patch("/triggers/{trigger_id}", response_model=TriggerOut)
async def update_trigger(
    trigger_id: str, body: TriggerUpdate, db: DbSession,
    _=Depends(require_perm("triggers", "update"))
):
    result = await db.execute(select(SceneTrigger).where(SceneTrigger.id == trigger_id))
    trigger = result.scalar_one_or_none()
    if trigger is None:
        raise HTTPException(status_code=404, detail="Trigger not found")
    for key, val in body.model_dump(exclude_unset=True).items():
        setattr(trigger, key, val)
    await db.commit()
    await db.refresh(trigger)
    return trigger


@router.delete("/triggers/{trigger_id}")
async def delete_trigger(
    trigger_id: str, db: DbSession, _=Depends(require_perm("triggers", "delete"))
):
    result = await db.execute(select(SceneTrigger).where(SceneTrigger.id == trigger_id))
    trigger = result.scalar_one_or_none()
    if trigger is None:
        raise HTTPException(status_code=404, detail="Trigger not found")
    await db.delete(trigger)
    await db.commit()
    return {"detail": "deleted"}
