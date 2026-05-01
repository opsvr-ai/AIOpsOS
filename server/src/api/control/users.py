import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.api.deps import CurrentUser, DbSession
from src.core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)
from src.models.channel import SystemConfig
from src.models.space import Space, SpaceMember
from src.models.user import Role, User, UserInvitation
from src.schemas.user import (
    PasswordChange,
    ProfileUpdate,
    RefreshRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


async def _get_ldap_config(db):
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == "ldap_config"))
    row = result.scalar_one_or_none()
    return row.value if row else {}


async def _send_email(db, recipient: str, title: str, body: str):
    try:
        from src.models.channel import NotificationChannel

        result = await db.execute(
            select(NotificationChannel).where(
                NotificationChannel.channel_type == "email",
                NotificationChannel.is_active == True,
            )
        )
        ch = result.scalars().first()
        if ch is None:
            return
        from src.services.channel_manager import channel_manager

        await channel_manager.send(
            channel_type="email",
            config=ch.config,
            title=title,
            message=body,
            severity="info",
            recipients=[recipient],
        )
    except Exception:
        pass


def _user_out(user: User) -> dict:
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "is_active": user.is_active,
        "default_space_id": str(user.default_space_id) if user.default_space_id else None,
        "roles": [{"id": str(r.id), "name": r.name} for r in (user.roles or [])],
        "display_name": user.display_name,
        "phone": user.phone,
        "department": user.department,
        "title": user.title,
        "source": user.source or "local",
        "status": user.status or "active",
    }


# ── Registration ──────────────────────────────────────────────────────

@router.post("/register", response_model=UserOut)
async def register(body: UserCreate, db: DbSession):
    existing = await db.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already exists")

    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already exists")

    # LDAP overlap check
    ldap_config = await _get_ldap_config(db)
    if ldap_config.get("server_url"):
        ldap_users = await db.execute(select(User).where(User.is_ldap == True))
        for ldap_user in ldap_users.scalars().all():
            if ldap_user.username.lower() == body.username.lower():
                raise HTTPException(
                    status_code=409,
                    detail="该用户名已关联企业域账号，请使用域账号登录",
                )
            if ldap_user.email.lower() == body.email.lower():
                raise HTTPException(
                    status_code=409,
                    detail="该邮箱已关联企业域账号，请使用域账号登录",
                )

    # First user is auto-approved; others are pending
    count_result = await db.execute(select(User).limit(2))
    user_count = len(count_result.scalars().all())

    if user_count == 0:
        status = "active"
    else:
        status = "pending"

    user = User(
        username=body.username,
        email=body.email,
        hashed_password=hash_password(body.password),
        source="local",
        status=status,
        is_active=(status == "active"),
    )
    db.add(user)
    await db.flush()

    # Assign role via association table insert to avoid lazy-load trigger
    from src.models.user import user_roles
    if user_count == 0:
        admin_role = (await db.execute(select(Role).where(Role.name == "admin"))).scalar_one_or_none()
        if admin_role:
            await db.execute(user_roles.insert().values(user_id=user.id, role_id=admin_role.id))
    else:
        viewer_role = (await db.execute(select(Role).where(Role.name == "viewer"))).scalar_one_or_none()
        if viewer_role:
            await db.execute(user_roles.insert().values(user_id=user.id, role_id=viewer_role.id))

    await db.commit()

    if user.status == "active":
        from src.services.space_service import create_default_space_for_user
        await create_default_space_for_user(str(user.id))
    else:
        # Notify all admin users about the pending registration
        admin_result = await db.execute(
            select(User)
            .join(User.roles)
            .where(Role.name == "admin")
            .options(selectinload(User.roles))
        )
        admins = admin_result.scalars().all()
        for admin in admins:
            await _send_email(
                db,
                admin.email,
                "新用户注册待审批",
                f"用户 {body.username}（{body.email}）已注册，等待管理员审批。请前往控制中心 > 用户管理处理。",
            )

    # Reload with eager-loaded roles
    result = await db.execute(
        select(User).where(User.id == user.id).options(selectinload(User.roles))
    )
    user = result.scalar_one()

    return _user_out(user)


# ── Login ─────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(body: UserLogin, db: DbSession):
    # LDAP authentication
    if body.login_type == "ldap":
        ldap_config = await _get_ldap_config(db)
        if not ldap_config.get("server_url"):
            raise HTTPException(status_code=400, detail="LDAP 未配置")

        from src.services.ldap_service import authenticate_ldap_user
        ldap_attrs = await authenticate_ldap_user(ldap_config, body.username, body.password)
        if ldap_attrs is None:
            raise HTTPException(status_code=401, detail="LDAP 认证失败")

        result = await db.execute(
            select(User).where(User.username == body.username).options(selectinload(User.roles))
        )
        user = result.scalar_one_or_none()

        if user is None:
            user = User(
                username=body.username,
                email=ldap_attrs["email"] or f"{body.username}@ldap.local",
                hashed_password=hash_password(secrets.token_urlsafe(32)),
                is_ldap=True,
                source="ldap",
                status="active",
                is_active=True,
                display_name=ldap_attrs.get("display_name"),
            )
            db.add(user)
            await db.flush()

            from src.models.user import user_roles
            viewer_role = (await db.execute(select(Role).where(Role.name == "viewer"))).scalar_one_or_none()
            if viewer_role:
                await db.execute(user_roles.insert().values(user_id=user.id, role_id=viewer_role.id))
            await db.commit()

            from src.services.space_service import create_default_space_for_user
            await create_default_space_for_user(str(user.id))

            # Reload with eager-loaded roles
            result = await db.execute(
                select(User).where(User.id == user.id).options(selectinload(User.roles))
            )
            user = result.scalar_one()

        elif not user.is_ldap:
            raise HTTPException(status_code=409, detail="该账号为本地账号，请使用本地登录")

        if not user.is_active:
            raise HTTPException(status_code=401, detail="账号已被禁用")

        role_names = [r.name for r in user.roles]
        token = create_access_token(data={"sub": str(user.id), "username": user.username, "roles": role_names})
        refresh = create_refresh_token(data={"sub": str(user.id)})
        return TokenResponse(access_token=token, refresh_token=refresh)

    # Local authentication
    result = await db.execute(
        select(User).where(User.username == body.username).options(selectinload(User.roles))
    )
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if user.status == "pending":
        raise HTTPException(status_code=401, detail="账号正在审批中，请等待管理员审批")
    if user.status == "disabled" or not user.is_active:
        raise HTTPException(status_code=401, detail="账号已被禁用，请联系管理员")

    role_names = [r.name for r in user.roles]
    token = create_access_token(data={"sub": str(user.id), "username": user.username, "roles": role_names})
    refresh = create_refresh_token(data={"sub": str(user.id)})
    return TokenResponse(access_token=token, refresh_token=refresh)


# ── Token refresh ─────────────────────────────────────────────────────

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest, db: DbSession):
    try:
        payload = decode_refresh_token(body.refresh_token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    user_id = payload.get("sub")
    result = await db.execute(
        select(User).where(User.id == user_id).options(selectinload(User.roles))
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    role_names = [r.name for r in user.roles]
    access = create_access_token(data={"sub": str(user.id), "username": user.username, "roles": role_names})
    refresh = create_refresh_token(data={"sub": str(user.id)})
    return TokenResponse(access_token=access, refresh_token=refresh)


# ── Current user ──────────────────────────────────────────────────────

@router.get("/me", response_model=UserOut)
async def get_me(user: CurrentUser, db: DbSession):
    setup_required = False
    role_names = {r.name for r in user.roles}
    if "admin" in role_names:
        from sqlalchemy import func
        from src.models.model_provider import ModelProvider
        count = await db.scalar(
            select(func.count(ModelProvider.id)).where(ModelProvider.is_active == True)
        )
        setup_required = (count or 0) == 0
    result = UserOut.model_validate(user)
    result.setup_required = setup_required
    return result


# ── Profile ───────────────────────────────────────────────────────────

@router.put("/profile", response_model=UserOut)
async def update_profile(body: ProfileUpdate, user: CurrentUser, db: DbSession):
    if body.email is not None:
        existing = await db.execute(
            select(User).where(User.email == body.email, User.id != user.id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Email already in use")
        user.email = body.email
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.phone is not None:
        user.phone = body.phone
    if body.department is not None:
        user.department = body.department
    if body.title is not None:
        user.title = body.title
    await db.commit()
    await db.refresh(user)
    return _user_out(user)


# ── Password change ───────────────────────────────────────────────────

@router.put("/password")
async def change_password(body: PasswordChange, user: CurrentUser, db: DbSession):
    if not verify_password(body.old_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="当前密码错误")
    user.hashed_password = hash_password(body.new_password)
    await db.commit()
    return {"detail": "ok"}


# ── Invitation ────────────────────────────────────────────────────────

@router.get("/invitation/{token}")
async def get_invitation(token: str, db: DbSession):
    result = await db.execute(
        select(UserInvitation).where(UserInvitation.token == token)
    )
    invitation = result.scalar_one_or_none()
    if invitation is None:
        raise HTTPException(status_code=404, detail="邀请链接无效")
    if invitation.status != "pending":
        raise HTTPException(status_code=400, detail="邀请链接已使用或已过期")
    if invitation.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
        raise HTTPException(status_code=410, detail="邀请链接已过期")
    space_name = None
    if invitation.space_id:
        space_db = await db.execute(select(Space).where(Space.id == invitation.space_id))
        space = space_db.scalar_one_or_none()
        if space:
            space_name = space.name
    return {
        "email": invitation.email,
        "space_id": str(invitation.space_id) if invitation.space_id else None,
        "space_name": space_name,
        "expires_at": str(invitation.expires_at),
    }


@router.post("/accept-invitation/{token}", response_model=TokenResponse)
async def accept_invitation(token: str, body: UserCreate, db: DbSession):
    result = await db.execute(
        select(UserInvitation).where(UserInvitation.token == token)
    )
    invitation = result.scalar_one_or_none()
    if invitation is None:
        raise HTTPException(status_code=404, detail="邀请链接无效")
    if invitation.status != "pending":
        raise HTTPException(status_code=400, detail="邀请链接已使用")
    if invitation.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
        raise HTTPException(status_code=410, detail="邀请链接已过期")

    existing = await db.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already exists")
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already exists")

    user = User(
        username=body.username,
        email=body.email,
        hashed_password=hash_password(body.password),
        source="invited",
        status="active",
        is_active=True,
    )
    db.add(user)
    await db.flush()

    from src.models.user import user_roles
    viewer_role = (await db.execute(select(Role).where(Role.name == "viewer"))).scalar_one_or_none()
    if viewer_role:
        await db.execute(user_roles.insert().values(user_id=user.id, role_id=viewer_role.id))

    invitation.status = "accepted"

    if invitation.space_id:
        member_check = await db.execute(
            select(SpaceMember).where(
                SpaceMember.space_id == invitation.space_id,
                SpaceMember.user_id == user.id,
            )
        )
        if member_check.scalar_one_or_none() is None:
            db.add(SpaceMember(space_id=invitation.space_id, user_id=user.id, role="member"))

    await db.commit()

    from src.services.space_service import create_default_space_for_user
    await create_default_space_for_user(str(user.id))

    # Reload with eager-loaded roles
    result = await db.execute(
        select(User).where(User.id == user.id).options(selectinload(User.roles))
    )
    user = result.scalar_one()

    role_names = [r.name for r in user.roles]
    token_jwt = create_access_token(data={"sub": str(user.id), "username": user.username, "roles": role_names})
    refresh = create_refresh_token(data={"sub": str(user.id)})
    return TokenResponse(access_token=token_jwt, refresh_token=refresh)
