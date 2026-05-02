from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.security import decode_token
from src.models.base import async_session_factory
from src.models.user import Role, User

security_scheme = HTTPBearer(auto_error=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    result = await db.execute(
        select(User).where(User.id == user_id).options(
            selectinload(User.roles).selectinload(Role.permissions)
        )
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Require admin permission (resource == 'admin')."""
    if not user.roles:
        raise HTTPException(status_code=403, detail="Admin permission required")
    for role in user.roles:
        for perm in role.permissions:
            if perm.resource == "admin":
                return user
    raise HTTPException(status_code=403, detail="Admin permission required")


def require_perm(resource: str, action: str):
    """Factory: returns a dependency that checks RBAC for the given resource/action."""
    async def checker(user: User = Depends(get_current_user)) -> User:
        if not user.roles:
            raise HTTPException(status_code=403, detail="Permission denied")
        for role in user.roles:
            for perm in role.permissions:
                if perm.resource == resource and perm.action == action:
                    return user
                if perm.resource == "admin":
                    return user
        raise HTTPException(status_code=403, detail="Permission denied")
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
