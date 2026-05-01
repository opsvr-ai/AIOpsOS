from fastapi import APIRouter, Depends
from sqlalchemy import select

from src.api.deps import CurrentUser, DbSession, require_perm
from src.models.channel import SystemConfig
from src.services.ldap_service import test_ldap_connection, sync_ldap_users

router = APIRouter()

DEFAULT_LDAP_CONFIG = {
    "server_url": "",
    "bind_dn": "",
    "bind_password": "",
    "base_dn": "",
    "user_filter": "(objectClass=person)",
    "attr_username": "sAMAccountName",
    "attr_email": "mail",
    "attr_display_name": "displayName",
    "group_base_dn": "",
    "group_filter": "(objectClass=group)",
    "group_role_map": {},
    "sync_enabled": False,
    "sync_interval_hours": 24,
}


async def _get_ldap_config(db: DbSession) -> dict:
    result = await db.execute(
        select(SystemConfig).where(SystemConfig.key == "ldap_config")
    )
    row = result.scalar_one_or_none()
    return row.value if row else DEFAULT_LDAP_CONFIG


async def _save_ldap_config(db: DbSession, config: dict):
    result = await db.execute(
        select(SystemConfig).where(SystemConfig.key == "ldap_config")
    )
    row = result.scalar_one_or_none()
    if row:
        row.value = config
    else:
        db.add(SystemConfig(key="ldap_config", value=config))
    await db.commit()


@router.get("/system/ldap")
async def get_ldap_config(db: DbSession, _=Depends(require_perm("system", "view"))):
    return await _get_ldap_config(db)


@router.put("/system/ldap")
async def update_ldap_config(
    body: dict, db: DbSession, _=Depends(require_perm("system", "update"))
):
    config = await _get_ldap_config(db)
    config.update(body)
    await _save_ldap_config(db, config)
    return {"detail": "updated"}


@router.post("/system/ldap/test")
async def test_ldap(db: DbSession, _=Depends(require_perm("system", "update"))):
    config = await _get_ldap_config(db)
    ok, msg = await test_ldap_connection(config)
    return {"ok": ok, "message": msg}


@router.post("/system/ldap/sync")
async def sync_ldap(db: DbSession, _=Depends(require_perm("system", "update"))):
    config = await _get_ldap_config(db)
    stats = await sync_ldap_users(config)
    return stats
