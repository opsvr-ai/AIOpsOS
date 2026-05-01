import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy import select

from src.api.deps import DbSession, get_current_user, require_perm
from src.config import settings
from src.models.channel import SystemConfig

router = APIRouter()

ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico'}
MAX_UPLOAD_SIZE = 2 * 1024 * 1024  # 2MB

DEFAULT_BRANDING = {
    "logo_url": "",
    "favicon_url": "",
    "company_name": "AIOpsOS",
    "primary_color": "#1677ff",
}


@router.post("/system/upload")
async def upload_file(file: UploadFile = File(...), _=Depends(get_current_user)):
    ext = Path(file.filename or ".png").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return {"url": "", "error": f"不支持的文件格式: {ext}"}
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE:
        return {"url": "", "error": "文件不能超过 2MB"}
    filename = f"{uuid.uuid4().hex}{ext}"
    os.makedirs(settings.upload_dir, exist_ok=True)
    filepath = os.path.join(settings.upload_dir, filename)
    with open(filepath, "wb") as f:
        f.write(contents)
    url = f"/uploads/{filename}"
    return {"url": url, "error": None}


@router.get("/system/branding")
async def get_branding(db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(
        select(SystemConfig).where(SystemConfig.key == "branding")
    )
    row = result.scalar_one_or_none()
    return row.value if row else DEFAULT_BRANDING


@router.put("/system/branding")
async def update_branding(
    body: dict, db: DbSession, _=Depends(require_perm("system", "update"))
):
    result = await db.execute(
        select(SystemConfig).where(SystemConfig.key == "branding")
    )
    row = result.scalar_one_or_none()
    if row:
        row.value = {**row.value, **body}
    else:
        row = SystemConfig(key="branding", value={**DEFAULT_BRANDING, **body})
        db.add(row)
    await db.commit()
    return {"detail": "updated"}
