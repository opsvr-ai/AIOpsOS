"""Task CRUD for chat sessions."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from src.api.deps import DbSession, get_current_user
from src.models.task import Task

router = APIRouter(prefix="/api/v1")


@router.get("/sessions/{session_id}/tasks")
async def list_tasks(session_id: str, db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(
        select(Task)
        .where(Task.session_id == session_id)
        .order_by(Task.created_at.desc())
    )
    return [_task_out(t) for t in result.scalars().all()]


@router.post("/sessions/{session_id}/tasks")
async def create_task(session_id: str, body: dict, db: DbSession, _=Depends(get_current_user)):
    task = Task(
        session_id=session_id,
        title=body.get("title", ""),
        description=body.get("description", ""),
        status=body.get("status", "pending"),
        priority=body.get("priority", "medium"),
        source=body.get("source", "manual"),
        confidence=body.get("confidence"),
        due_date=body.get("due_date"),
        form_definition=body.get("form_definition"),
        form_data=body.get("form_data"),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return _task_out(task)


@router.patch("/sessions/{session_id}/tasks/{task_id}")
async def update_task(
    session_id: str, task_id: str, body: dict, db: DbSession, _=Depends(get_current_user)
):
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.session_id == session_id)
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    for key in ("title", "description", "status", "priority", "due_date", "confidence", "form_data", "form_definition"):
        if key in body:
            setattr(task, key, body[key])
    await db.commit()
    await db.refresh(task)
    return _task_out(task)


@router.delete("/sessions/{session_id}/tasks/{task_id}")
async def delete_task(
    session_id: str, task_id: str, db: DbSession, _=Depends(get_current_user)
):
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.session_id == session_id)
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.delete(task)
    await db.commit()
    return {"detail": "deleted"}


def _task_out(t: Task) -> dict:
    return {
        "id": str(t.id),
        "session_id": str(t.session_id),
        "title": t.title,
        "description": t.description,
        "status": t.status,
        "priority": t.priority,
        "source": t.source,
        "confidence": t.confidence,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "form_definition": t.form_definition,
        "form_data": t.form_data,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }
