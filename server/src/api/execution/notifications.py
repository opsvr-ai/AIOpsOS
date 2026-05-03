from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, not_, select, update

from src.api.deps import CurrentUser, DbSession
from src.models.notification import Notification
from src.schemas.notification import NotificationOut

router = APIRouter(prefix="/api/v1")


@router.get("/notifications", response_model=list[NotificationOut])
async def list_notifications(
    db: DbSession,
    user: CurrentUser,
    is_read: bool | None = Query(None),
    severity: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = select(Notification).where(
        (Notification.user_id.is_(None)) | (Notification.user_id == user.id)
    )
    if is_read is not None:
        q = q.where(Notification.is_read == is_read)
    if severity:
        q = q.where(Notification.severity == severity)
    q = q.order_by(Notification.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/notifications/unread-count")
async def unread_count(db: DbSession, user: CurrentUser):
    result = await db.execute(
        select(func.count()).select_from(Notification).where(
            (Notification.user_id.is_(None)) | (Notification.user_id == user.id),
            not_(Notification.is_read),
        )
    )
    count = result.scalar() or 0
    return {"unread": count}


@router.post("/notifications/{notification_id}/read")
async def mark_read(notification_id: str, db: DbSession, user: CurrentUser):
    result = await db.execute(
        select(Notification).where(Notification.id == notification_id)
    )
    notif = result.scalar_one_or_none()
    if notif is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    notif.is_read = True
    notif.read_at = datetime.now(UTC)
    await db.commit()
    return {"detail": "marked as read"}


@router.post("/notifications/read-all")
async def mark_all_read(db: DbSession, user: CurrentUser):
    await db.execute(
        update(Notification)
        .where(
            (Notification.user_id.is_(None)) | (Notification.user_id == user.id),
            not_(Notification.is_read),
        )
        .values(is_read=True, read_at=datetime.now(UTC))
    )
    await db.commit()
    return {"detail": "all marked as read"}
