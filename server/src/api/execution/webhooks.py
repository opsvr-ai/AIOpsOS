"""Webhook receiver — public endpoint, no JWT auth."""

from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import select

from src.api.deps import DbSession
from src.models.datasource import DataSource
from src.services.webhook_handler import process_webhook

router = APIRouter(prefix="/api/v1")


@router.post("/webhook/{endpoint_id}")
async def receive_webhook(endpoint_id: str, request: Request, db: DbSession):
    result = await db.execute(
        select(DataSource).where(
            DataSource.source_type == "webhook",
            DataSource.config["endpoint_id"].astext == endpoint_id,
            DataSource.is_enabled == True,
        )
    )
    ds = result.scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    headers = dict(request.headers)
    result = await process_webhook(ds, body, headers)

    if result.get("status") == "unauthorized":
        raise HTTPException(status_code=403, detail=result.get("detail"))
    if result.get("status") == "rate_limited":
        raise HTTPException(status_code=429, detail=result.get("detail"))

    return result
