import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from src.api.deps import DbSession, get_current_user, require_admin, require_perm
from src.core.model_factory import _build_model_from_provider, invalidate_model_cache
from src.models.model_provider import ModelProvider
from src.models.user import User
from src.schemas.model_provider import (
    ModelProviderCreate,
    ModelProviderOut,
    ModelProviderTestResult,
    ModelProviderUpdate,
)

router = APIRouter()


def _is_admin(user: User) -> bool:
    for r in (user.roles or []):
        for p in (r.permissions or []):
            if p.resource == "admin":
                return True
    return False


@router.get("/model-providers", response_model=list[ModelProviderOut])
async def list_providers(db: DbSession, user=Depends(get_current_user)):
    result = await db.execute(select(ModelProvider).order_by(ModelProvider.priority.asc()))
    providers = result.scalars().all()
    if not _is_admin(user):
        for p in providers:
            p.api_key = "***"
    return providers


@router.get("/model-providers/{provider_id}", response_model=ModelProviderOut)
async def get_provider(provider_id: uuid.UUID, db: DbSession, _=Depends(require_admin)):
    result = await db.execute(select(ModelProvider).where(ModelProvider.id == provider_id))
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="not found")
    return provider


@router.post("/model-providers", response_model=ModelProviderOut)
async def create_provider(
    body: ModelProviderCreate,
    db: DbSession,
    _=Depends(require_perm("model_providers", "create")),
):
    if body.is_default:
        await _unset_defaults(db, body.model_type)
    provider = ModelProvider(**body.model_dump())
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    await invalidate_model_cache(provider.model_type)
    return provider


@router.patch("/model-providers/{provider_id}", response_model=ModelProviderOut)
async def update_provider(
    provider_id: uuid.UUID,
    body: ModelProviderUpdate,
    db: DbSession,
    _=Depends(require_perm("model_providers", "update")),
):
    result = await db.execute(select(ModelProvider).where(ModelProvider.id == provider_id))
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="not found")
    update_data = body.model_dump(exclude_unset=True)
    if update_data.get("is_default"):
        model_type = update_data.get("model_type", provider.model_type)
        await _unset_defaults(db, model_type)
    # Skip api_key update if it's masked (starts with ***) to prevent overwriting
    # the real key with the masked placeholder
    if "api_key" in update_data:
        api_key_value = update_data["api_key"]
        if api_key_value and (api_key_value == "***" or api_key_value.startswith("***")):
            del update_data["api_key"]
    for k, v in update_data.items():
        setattr(provider, k, v)
    await db.commit()
    await db.refresh(provider)
    await invalidate_model_cache(provider.model_type)
    return provider


@router.delete("/model-providers/{provider_id}")
async def delete_provider(
    provider_id: uuid.UUID,
    db: DbSession,
    _=Depends(require_perm("model_providers", "delete")),
):
    result = await db.execute(select(ModelProvider).where(ModelProvider.id == provider_id))
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="not found")
    await db.delete(provider)
    await db.commit()
    await invalidate_model_cache(provider.model_type)
    return {"detail": "deleted"}


@router.post("/model-providers/{provider_id}/test", response_model=ModelProviderTestResult)
async def test_provider(
    provider_id: uuid.UUID,
    db: DbSession,
    _=Depends(require_perm("model_providers", "update")),
):
    result = await db.execute(select(ModelProvider).where(ModelProvider.id == provider_id))
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="not found")
    try:
        model = _build_model_from_provider(provider)
        start = time.time()
        response = await model.ainvoke("ping")
        latency_ms = (time.time() - start) * 1000
        return ModelProviderTestResult(
            ok=True, message=f"OK: {response.content[:100]}", latency_ms=round(latency_ms, 1)
        )
    except Exception as exc:
        return ModelProviderTestResult(ok=False, message=str(exc)[:500])


@router.post("/model-providers/{provider_id}/set-default")
async def set_default(
    provider_id: uuid.UUID,
    db: DbSession,
    _=Depends(require_perm("model_providers", "update")),
):
    result = await db.execute(select(ModelProvider).where(ModelProvider.id == provider_id))
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="not found")
    await _unset_defaults(db, provider.model_type)
    provider.is_default = True
    await db.commit()
    await invalidate_model_cache(provider.model_type)
    return {"detail": "set as default"}


async def _unset_defaults(db: DbSession, model_type: str):
    result = await db.execute(
        select(ModelProvider).where(
            ModelProvider.is_default, ModelProvider.model_type == model_type
        )
    )
    for p in result.scalars().all():
        p.is_default = False
