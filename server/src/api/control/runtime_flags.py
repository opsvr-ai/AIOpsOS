"""Admin-facing CRUD for the ``runtime_feature_flags`` table.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 5.2 / R-7.1.

All mutating endpoints require :func:`src.api.deps.require_admin`. Each
mutation writes an ``AUDIT runtime_flag ...`` line for ops traceability.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.api.deps import DbSession, require_admin
from src.models.runtime_flag import RuntimeFeatureFlag
from src.schemas.runtime_flag import RuntimeFlagOut, RuntimeFlagUpsert

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/runtime-flags", tags=["runtime-flags"])


def _audit(user: Any, action: str, key: str, **extra: Any) -> None:
    actor = getattr(user, "username", None) or getattr(user, "id", "<unknown>")
    payload = ", ".join(f"{k}={v}" for k, v in extra.items())
    logger.info(
        "AUDIT runtime_flag action=%s key=%s actor=%s %s",
        action,
        key,
        actor,
        payload,
    )


@router.get("", response_model=list[RuntimeFlagOut])
async def list_flags(
    db: DbSession,
    _=Depends(require_admin),
) -> list[RuntimeFeatureFlag]:
    result = await db.execute(
        select(RuntimeFeatureFlag).order_by(RuntimeFeatureFlag.key)
    )
    return list(result.scalars().all())


@router.get("/{key}", response_model=RuntimeFlagOut)
async def get_flag(
    key: str,
    db: DbSession,
    _=Depends(require_admin),
) -> RuntimeFeatureFlag:
    row = await db.get(RuntimeFeatureFlag, key)
    if row is None:
        raise HTTPException(status_code=404, detail="flag not found")
    return row


@router.put("/{key}", response_model=RuntimeFlagOut)
async def upsert_flag(
    key: str,
    body: RuntimeFlagUpsert,
    db: DbSession,
    user=Depends(require_admin),
) -> RuntimeFeatureFlag:
    values: dict[str, Any] = {
        "key": key,
        "enabled": body.enabled,
        "rollout_percent": body.rollout_percent,
    }
    set_values: dict[str, Any] = {
        "enabled": body.enabled,
        "rollout_percent": body.rollout_percent,
    }
    # ``data=None`` means "leave existing JSON alone" — only set ``data``
    # when the caller explicitly provided a dict (possibly empty).
    if body.data is not None:
        values["data"] = dict(body.data)
        set_values["data"] = dict(body.data)

    stmt = (
        pg_insert(RuntimeFeatureFlag)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[RuntimeFeatureFlag.key],
            set_=set_values,
        )
    )
    await db.execute(stmt)
    await db.commit()

    row = await db.get(RuntimeFeatureFlag, key)
    if row is None:  # should never happen post-upsert
        raise HTTPException(status_code=500, detail="upsert lost row")
    _audit(
        user,
        "upsert",
        key,
        enabled=body.enabled,
        rollout_percent=body.rollout_percent,
        data_updated=(body.data is not None),
    )
    return row


@router.delete("/{key}", status_code=204, response_model=None)
async def delete_flag(
    key: str,
    db: DbSession,
    user=Depends(require_admin),
) -> None:
    row = await db.get(RuntimeFeatureFlag, key)
    if row is None:
        raise HTTPException(status_code=404, detail="flag not found")
    await db.execute(delete(RuntimeFeatureFlag).where(RuntimeFeatureFlag.key == key))
    await db.commit()
    _audit(user, "delete", key)
    return None


__all__ = ["router"]
