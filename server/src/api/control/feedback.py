import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from src.api.deps import DbSession, get_current_user
from src.models.feedback import Feedback
from src.schemas.feedback import FeedbackCreate, FeedbackOut, FeedbackUpdate

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_STATUSES = [
    "待AI分析", "已分析", "开发中", "修复中",
    "驳回", "已修复", "开发完成", "已上线",
]


@router.post("/feedbacks", response_model=FeedbackOut)
async def create_feedback(
    body: FeedbackCreate, db: DbSession, user=Depends(get_current_user),
):
    if body.type not in ("bug", "feature"):
        raise HTTPException(status_code=422, detail="type must be 'bug' or 'feature'")
    fb = Feedback(
        user_id=str(user.id),
        username=user.username,
        type=body.type,
        title=body.title,
        description=body.description,
    )
    db.add(fb)
    await db.commit()
    await db.refresh(fb)
    return fb


@router.get("/feedbacks", response_model=list[FeedbackOut])
async def list_feedbacks(
    db: DbSession,
    _=Depends(get_current_user),
    type: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    q = select(Feedback).order_by(Feedback.created_at.desc())
    if type:
        q = q.where(Feedback.type == type)
    if status:
        q = q.where(Feedback.status == status)
    q = q.offset(offset).limit(limit)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/feedbacks/statuses")
async def list_feedback_statuses(_=Depends(get_current_user)):
    return [{"value": s, "label": s} for s in VALID_STATUSES]


@router.get("/feedbacks/{feedback_id}", response_model=FeedbackOut)
async def get_feedback(
    feedback_id: str, db: DbSession, _=Depends(get_current_user)
):
    result = await db.execute(select(Feedback).where(Feedback.id == feedback_id))
    fb = result.scalar_one_or_none()
    if fb is None:
        raise HTTPException(status_code=404, detail="Feedback not found")
    return fb


@router.patch("/feedbacks/{feedback_id}", response_model=FeedbackOut)
async def update_feedback(
    feedback_id: str,
    body: FeedbackUpdate,
    db: DbSession,
    _=Depends(get_current_user),
):
    result = await db.execute(select(Feedback).where(Feedback.id == feedback_id))
    fb = result.scalar_one_or_none()
    if fb is None:
        raise HTTPException(status_code=404, detail="Feedback not found")
    data = body.model_dump(exclude_unset=True)
    for key, val in data.items():
        setattr(fb, key, val)
    await db.commit()
    await db.refresh(fb)
    return fb
