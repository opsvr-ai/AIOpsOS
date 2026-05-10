import hashlib
import logging
import os

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select

from src.api.deps import DbSession, get_current_user
from src.config import settings
from src.models.feedback import Feedback
from src.schemas.feedback import (
    FeedbackCreate,
    FeedbackImageUploadResponse,
    FeedbackOut,
    FeedbackUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Image upload constants
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB
ALLOWED_IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

VALID_STATUSES = [
    "待AI分析", "已分析", "开发中", "修复中",
    "驳回", "已修复", "开发完成", "已上线",
]


@router.post("/feedbacks/images", response_model=FeedbackImageUploadResponse)
async def upload_feedback_image(
    file: UploadFile = File(...),
    _=Depends(get_current_user),
):
    """Upload an image for use in feedback submissions.

    Accepts multipart/form-data with a file field.
    Validates file type (PNG, JPG, JPEG, GIF, WebP) and size (max 5MB).
    Uses content hash for filename to enable deduplication.

    Requirements: 5.1, 5.2, 5.3
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    # Validate file size
    if file.size and file.size > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=413, detail="Image too large (max 5MB)")

    # Validate file type by extension
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image type: {ext}. Allowed types: PNG, JPG, JPEG, GIF, WebP"
        )

    # Read file content
    content = await file.read()

    # Double-check size after reading (in case file.size was not set)
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=413, detail="Image too large (max 5MB)")

    # Generate content hash for filename (SHA256 for better collision resistance)
    content_hash = hashlib.sha256(content).hexdigest()[:16]
    safe_filename = f"{content_hash}{ext}"

    # Ensure upload directory exists
    upload_path = os.path.join(settings.upload_dir, "feedbacks")
    os.makedirs(upload_path, exist_ok=True)

    # Save file
    dest_path = os.path.join(upload_path, safe_filename)
    with open(dest_path, "wb") as f:
        f.write(content)

    logger.info("Uploaded feedback image: %s -> %s", file.filename, safe_filename)

    return FeedbackImageUploadResponse(
        url=f"/uploads/feedbacks/{safe_filename}",
        filename=file.filename,
    )


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
        images=body.images,
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
