from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.api.deps import DbSession, require_perm
from src.models.user import Permission, Role, User
from src.schemas.permission import (
    PermissionCreate,
    PermissionOut,
    RoleCreate,
    RoleOut,
    RoleUpdate,
    UserAdminOut,
    UserAdminUpdate,
    UserInvitationRequest,
)
from src.schemas.user import UserAdminCreate, UserRegistrationApproval

router = APIRouter()


# ── Permissions ──────────────────────────────────────────────────────

@router.get("/permissions", response_model=list[PermissionOut])
async def list_permissions(db: DbSession, _=Depends(require_perm("permissions", "view"))):
    result = await db.execute(select(Permission).order_by(Permission.resource, Permission.action))
    return result.scalars().all()


@router.post("/permissions", response_model=PermissionOut)
async def create_permission(
    body: PermissionCreate, db: DbSession, _=Depends(require_perm("permissions", "create"))
):
    perm = Permission(**body.model_dump())
    db.add(perm)
    await db.commit()
    await db.refresh(perm)
    return perm


@router.delete("/permissions/{permission_id}")
async def delete_permission(
    permission_id: str, db: DbSession, _=Depends(require_perm("permissions", "delete"))
):
    result = await db.execute(select(Permission).where(Permission.id == permission_id))
    perm = result.scalar_one_or_none()
    if perm is None:
        raise HTTPException(status_code=404, detail="Permission not found")
    await db.delete(perm)
    await db.commit()
    return {"detail": "deleted"}


# ── Roles ────────────────────────────────────────────────────────────

@router.get("/roles", response_model=list[RoleOut])
async def list_roles(db: DbSession, _=Depends(require_perm("roles", "view"))):
    result = await db.execute(
        select(Role).options(selectinload(Role.permissions)).order_by(Role.name)
    )
    return result.scalars().all()


@router.post("/roles", response_model=RoleOut)
async def create_role(
    body: RoleCreate, db: DbSession, _=Depends(require_perm("roles", "create"))
):
    role = Role(name=body.name, description=body.description)
    if body.permission_ids:
        perm_result = await db.execute(
            select(Permission).where(Permission.id.in_(body.permission_ids))
        )
        role.permissions = perm_result.scalars().all()
    db.add(role)
    await db.commit()
    await db.refresh(role)
    result = await db.execute(
        select(Role).where(Role.id == role.id).options(selectinload(Role.permissions))
    )
    return result.scalar_one()


@router.patch("/roles/{role_id}", response_model=RoleOut)
async def update_role(
    role_id: str, body: RoleUpdate, db: DbSession, _=Depends(require_perm("roles", "update"))
):
    result = await db.execute(
        select(Role).where(Role.id == role_id).options(selectinload(Role.permissions))
    )
    role = result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")

    if body.name is not None:
        role.name = body.name
    if body.description is not None:
        role.description = body.description
    if body.permission_ids is not None:
        perm_result = await db.execute(
            select(Permission).where(Permission.id.in_(body.permission_ids))
        )
        role.permissions = perm_result.scalars().all()

    await db.commit()
    await db.refresh(role)
    result = await db.execute(
        select(Role).where(Role.id == role.id).options(selectinload(Role.permissions))
    )
    return result.scalar_one()


@router.delete("/roles/{role_id}")
async def delete_role(
    role_id: str, db: DbSession, _=Depends(require_perm("roles", "delete"))
):
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    await db.delete(role)
    await db.commit()
    return {"detail": "deleted"}


# ── User management ──────────────────────────────────────────────────

def _user_admin_out(user: User) -> dict:
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "is_active": user.is_active,
        "is_ldap": getattr(user, "is_ldap", False),
        "roles": [
            {"id": str(r.id), "name": r.name, "description": r.description,
             "permissions": [{"id": str(p.id), "resource": p.resource, "action": p.action,
                              "description": p.description} for p in (r.permissions or [])]}
            for r in (user.roles or [])
        ],
        "display_name": user.display_name,
        "phone": user.phone,
        "department": user.department,
        "title": user.title,
        "source": user.source or "local",
        "status": user.status or "active",
        "created_at": str(user.created_at) if user.created_at else None,
    }


@router.get("/users", response_model=list[UserAdminOut])
async def list_users(
    db: DbSession, q: str | None = None, source: str | None = None,
    status: str | None = None, page: int = 1, page_size: int = 20,
    _=Depends(require_perm("users", "view")),
):
    query = select(User).options(selectinload(User.roles).selectinload(Role.permissions))
    if q:
        query = query.where(User.username.ilike(f"%{q}%") | User.email.ilike(f"%{q}%"))
    if source:
        query = query.where(User.source == source)
    if status:
        query = query.where(User.status == status)
    query = query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    return [_user_admin_out(u) for u in result.scalars().all()]


@router.get("/users/{user_id}", response_model=UserAdminOut)
async def get_user(user_id: str, db: DbSession, _=Depends(require_perm("users", "view"))):
    result = await db.execute(
        select(User).where(User.id == user_id).options(
            selectinload(User.roles).selectinload(Role.permissions)
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_admin_out(user)


@router.post("/users", response_model=UserAdminOut)
async def create_user(
    body: UserAdminCreate, db: DbSession, _=Depends(require_perm("users", "create"))
):
    from src.core.security import hash_password

    existing = await db.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already exists")
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already exists")

    user = User(
        username=body.username, email=body.email,
        hashed_password=hash_password(body.password),
        display_name=body.display_name, phone=body.phone,
        department=body.department, title=body.title,
        source="local", status="active",
    )
    if body.role_ids:
        role_result = await db.execute(select(Role).where(Role.id.in_(body.role_ids)))
        user.roles = role_result.scalars().all()
    db.add(user)
    await db.commit()
    await db.refresh(user)

    from src.services.space_service import create_default_space_for_user
    await create_default_space_for_user(str(user.id))

    result = await db.execute(
        select(User).where(User.id == user.id).options(
            selectinload(User.roles).selectinload(Role.permissions)
        )
    )
    return _user_admin_out(result.scalar_one())


@router.put("/users/{user_id}", response_model=UserAdminOut)
async def update_user(
    user_id: str, body: "UserAdminUpdate", db: DbSession,
    _=Depends(require_perm("users", "update"))
):
    from src.core.security import hash_password

    result = await db.execute(
        select(User).where(User.id == user_id).options(
            selectinload(User.roles).selectinload(Role.permissions)
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    for field in ("username", "email", "display_name", "phone", "department", "title",
                  "is_active", "status"):
        val = getattr(body, field, None)
        if val is not None:
            setattr(user, field, val)
    if body.password is not None:
        user.hashed_password = hash_password(body.password)
    if body.role_ids is not None:
        role_result = await db.execute(select(Role).where(Role.id.in_(body.role_ids)))
        user.roles = role_result.scalars().all()

    await db.commit()
    await db.refresh(user)
    return _user_admin_out(user)


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, db: DbSession, _=Depends(require_perm("users", "delete"))):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.status = "disabled"
    user.is_active = False
    await db.commit()
    return {"detail": "disabled"}


@router.post("/users/{user_id}/approve")
async def approve_user(
    user_id: str, body: UserRegistrationApproval, db: DbSession,
    _=Depends(require_perm("users", "update")),
):
    result = await db.execute(
        select(User).where(User.id == user_id, User.status == "pending")
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Pending user not found")

    if body.approved:
        user.status = "active"
        user.is_active = True
    else:
        user.status = "disabled"
        user.is_active = False
    await db.commit()

    try:
        from src.models.notification import Notification
        if body.approved:
            db.add(Notification(
                user_id=user.id, title="账号审批通过",
                message="您的 AIOpsOS 账号已通过审批，请登录平台。",
                severity="info", category="system",
            ))
        else:
            db.add(Notification(
                user_id=user.id, title="注册申请未通过",
                message=f"原因: {body.message or '未说明'}",
                severity="warning", category="system",
            ))
        await db.commit()
    except Exception:
        pass

    return {"detail": "approved" if body.approved else "rejected"}


# ── User invitations ──────────────────────────────────────────────────

@router.post("/users/invite")
async def invite_user(
    body: "UserInvitationRequest", db: DbSession,
    current_user=Depends(require_perm("users", "create")),
):
    import secrets
    from datetime import UTC, datetime, timedelta

    from src.models.user import UserInvitation

    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该邮箱已被注册")

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(days=30)
    invitation = UserInvitation(
        email=body.email,
        token=token,
        inviter_id=current_user.id,
        space_id=body.space_id if body.space_id else None,
        expires_at=expires_at,
    )
    db.add(invitation)
    await db.commit()

    # Send invitation email
    try:
        from src.models.channel import NotificationChannel
        from src.services.channel_manager import channel_manager

        email_result = await db.execute(
            select(NotificationChannel).where(
                NotificationChannel.channel_type == "email",
                NotificationChannel.is_active,
            )
        )
        ch = email_result.scalars().first()
        if ch:
            invite_link = f"{body.platform_url or 'http://localhost:5173'}/invite/{token}"
            await channel_manager.send(
                channel_type="email",
                config=ch.config,
                title="AIOpsOS 邀请",
                message=f"你被邀请加入 AIOpsOS 平台。<br/><br/>"
                        f"请点击以下链接完成注册：<a href=\"{invite_link}\">{invite_link}</a><br/><br/>"
                        f"此链接 30 天内有效。",
                severity="info",
                recipients=[body.email],
            )
    except Exception:
        pass

    return {"detail": "invited", "token": token}
