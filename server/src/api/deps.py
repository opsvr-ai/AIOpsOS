import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.redis import cache_get, cache_set
from src.core.security import decode_token
from src.models.base import async_session_factory
from src.models.user import Role, User

security_scheme = HTTPBearer(auto_error=False)

AUTH_CACHE_TTL = 300


# ── Lightweight cached types to avoid ORM dependency ───────────

@dataclass
class CachedPerm:
    resource: str
    action: str


@dataclass
class CachedRole:
    name: str
    permissions: list[CachedPerm]


@dataclass
class CachedUser:
    id: uuid.UUID
    is_active: bool
    username: str
    display_name: str | None
    email: str
    default_space_id: uuid.UUID | None
    source: str
    status: str
    roles: list[CachedRole]


def _serialize_user(user: User) -> dict:
    return {
        "id": str(user.id),
        "is_active": user.is_active,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "default_space_id": str(user.default_space_id) if user.default_space_id else None,
        "source": user.source,
        "status": user.status,
        "roles": [
            {
                "name": r.name,
                "permissions": [
                    {"resource": p.resource, "action": p.action}
                    for p in r.permissions
                ],
            }
            for r in user.roles
        ],
    }


def _deserialize_user(data: dict) -> CachedUser:
    return CachedUser(
        id=uuid.UUID(data["id"]),
        is_active=data["is_active"],
        username=data["username"],
        display_name=data.get("display_name"),
        email=data["email"],
        default_space_id=uuid.UUID(data["default_space_id"]) if data.get("default_space_id") else None,
        source=data.get("source", "local"),
        status=data.get("status", "active"),
        roles=[
            CachedRole(
                name=r["name"],
                permissions=[
                    CachedPerm(resource=p["resource"], action=p["action"])
                    for p in r["permissions"]
                ],
            )
            for r in data["roles"]
        ],
    )


def _check_perm(user: User | CachedUser, resource: str, action: str) -> bool:
    for role in user.roles:
        for perm in role.permissions:
            if perm.resource == "admin":
                return True
            if perm.resource == resource and perm.action == action:
                return True
    return False


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | CachedUser:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token") from None

    cache_key = f"auth:user:{user_id}"
    try:
        cached = await cache_get(cache_key)
    except Exception:
        cached = None
    if cached is not None:
        user = _deserialize_user(cached)
        if not user.is_active:
            raise HTTPException(status_code=401, detail="User not found or inactive")
        return user

    result = await db.execute(
        select(User).where(User.id == user_id).options(
            selectinload(User.roles).selectinload(Role.permissions)
        )
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    try:
        await cache_set(cache_key, _serialize_user(user), ttl=AUTH_CACHE_TTL)
    except Exception:
        pass

    return user


async def require_admin(user: User | CachedUser = Depends(get_current_user)) -> User | CachedUser:
    if not user.roles:
        raise HTTPException(status_code=403, detail="Admin permission required")
    if not _check_perm(user, "admin", "any"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    return user


def require_perm(resource: str, action: str):
    async def checker(user: User | CachedUser = Depends(get_current_user)) -> User | CachedUser:
        if not user.roles:
            raise HTTPException(status_code=403, detail="Permission denied")
        if not _check_perm(user, resource, action):
            raise HTTPException(status_code=403, detail="Permission denied")
        return user
    return checker


async def get_current_user_optional(
    request: Request,
    db: AsyncSession,
) -> User | None:
    """Return current user if authenticated, else None. Never raises 401."""
    token = (
        request.cookies.get("access_token")
        or request.headers.get("Authorization", "").removeprefix("Bearer ")
    )
    if not token:
        return None
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        if not user_id:
            return None
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()
    except Exception:
        return None


async def get_optional_space_id(
    space_id: str | None = Query(None, description="Filter by space"),
    x_space_id: str | None = Header(None, alias="X-Space-Id"),
) -> str | None:
    """Read space_id from query parameter or X-Space-Id header."""
    return space_id or x_space_id


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
