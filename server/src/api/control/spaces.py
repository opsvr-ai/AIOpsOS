import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select

from src.api.deps import CurrentUser, DbSession, get_current_user, get_db
from src.models.space import Space, SpaceInvitation, SpaceJoinRequest, SpaceMember
from src.models.user import User
from src.schemas.space import (
    SpaceCreate,
    SpaceDetailOut,
    SpaceInvitationOut,
    SpaceInviteRequest,
    SpaceInviteRespond,
    SpaceJoinRequestCreate,
    SpaceJoinRequestOut,
    SpaceJoinRequestReview,
    SpaceMemberOut,
    SpaceMemberRoleUpdate,
    SpaceOut,
    SpaceUpdate,
)

router = APIRouter(prefix="/spaces", tags=["spaces"])


# ── helpers ────────────────────────────────────────────────────

async def _invalidate_space_cache(user_id: str) -> None:
    try:
        from src.core.redis import cache_delete
        await cache_delete(f"space:my:{user_id}")
    except Exception:
        pass


def _space_out(space, member_count: int = 0) -> dict:
    return {
        "id": space.id,
        "name": space.name,
        "description": space.description,
        "visibility": space.visibility,
        "created_by": space.created_by,
        "member_count": member_count,
        "created_at": space.created_at,
        "updated_at": space.updated_at,
    }


async def _get_member_role(db, space_id: str, user_id: str) -> str | None:
    result = await db.execute(
        select(SpaceMember.role).where(
            SpaceMember.space_id == space_id, SpaceMember.user_id == user_id
        )
    )
    row = result.scalar_one_or_none()
    return row if row else None


async def _require_admin(db, space_id: str, user_id: str) -> str:
    role = await _get_member_role(db, space_id, user_id)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Space admin required")
    return role


async def _require_member(db, space_id: str, user_id: str) -> str:
    role = await _get_member_role(db, space_id, user_id)
    if role is None:
        raise HTTPException(status_code=403, detail="Not a space member")
    return role


# ── space CRUD ─────────────────────────────────────────────────

@router.post("", response_model=SpaceOut)
async def create_space(body: SpaceCreate, user: CurrentUser, db: DbSession):
    space = Space(
        name=body.name,
        description=body.description,
        visibility=body.visibility,
        created_by=user.id,
    )
    db.add(space)
    await db.flush()

    member = SpaceMember(space_id=space.id, user_id=user.id, role="admin")
    db.add(member)
    await db.flush()

    from src.services.space_service import clone_templates_to_space
    await clone_templates_to_space(db, str(space.id))

    await db.commit()
    await db.refresh(space)
    await _invalidate_space_cache(str(user.id))

    return _space_out(space, member_count=1)


@router.get("", response_model=list[SpaceDetailOut])
async def list_my_spaces(user: CurrentUser, db: DbSession):
    from src.core.redis import cache_get, cache_set

    cache_key = f"space:my:{user.id}"
    try:
        cached = await cache_get(cache_key)
    except Exception:
        cached = None
    if cached is not None:
        return cached

    sub = (
        select(SpaceMember.space_id)
        .where(SpaceMember.user_id == user.id)
        .subquery()
    )
    result = await db.execute(
        select(Space, func.count(SpaceMember.id).label("cnt"))
        .join(SpaceMember, SpaceMember.space_id == Space.id, isouter=True)
        .where(Space.id.in_(select(sub.c.space_id)))
        .group_by(Space.id)
        .order_by(Space.created_at.desc())
    )
    rows = result.all()
    space_ids = [row.Space.id for row in rows]
    roles = {}
    if space_ids:
        role_result = await db.execute(
            select(SpaceMember.space_id, SpaceMember.role).where(
                SpaceMember.space_id.in_(space_ids), SpaceMember.user_id == user.id
            )
        )
        roles = {str(row.space_id): row.role for row in role_result}
    result = [
        {**_space_out(row.Space, member_count=row.cnt), "my_role": roles.get(str(row.Space.id))}
        for row in rows
    ]
    try:
        await cache_set(cache_key, result, ttl=120)
    except Exception:
        pass
    return result


@router.get("/discover", response_model=list[SpaceOut])
async def discover_spaces(
    keyword: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    query = (
        select(Space, func.count(SpaceMember.id).label("cnt"))
        .join(SpaceMember, SpaceMember.space_id == Space.id, isouter=True)
        .where(Space.visibility == "public")
        .group_by(Space.id)
    )
    if keyword:
        query = query.where(Space.name.ilike(f"%{keyword}%"))
    result = await db.execute(
        query.order_by(Space.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return [_space_out(row.Space, member_count=row.cnt) for row in result]


@router.get("/{space_id}", response_model=SpaceDetailOut)
async def get_space(space_id: uuid.UUID, user: CurrentUser, db: DbSession):
    result = await db.execute(
        select(Space, func.count(SpaceMember.id).label("cnt"))
        .join(SpaceMember, SpaceMember.space_id == Space.id, isouter=True)
        .where(Space.id == space_id)
        .group_by(Space.id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Space not found")
    space = row.Space
    if space.visibility == "private":
        await _require_member(db, str(space_id), str(user.id))
    my_role = await _get_member_role(db, str(space_id), str(user.id))
    return {**_space_out(space, member_count=row.cnt), "my_role": my_role}


@router.put("/{space_id}", response_model=SpaceOut)
async def update_space(space_id: uuid.UUID, body: SpaceUpdate, user: CurrentUser, db: DbSession):
    await _require_admin(db, str(space_id), str(user.id))
    result = await db.execute(select(Space).where(Space.id == space_id))
    space = result.scalar_one_or_none()
    if space is None:
        raise HTTPException(status_code=404, detail="Space not found")
    if body.name is not None:
        space.name = body.name
    if body.description is not None:
        space.description = body.description
    if body.visibility is not None:
        space.visibility = body.visibility
    cnt_result = await db.execute(select(func.count()).where(SpaceMember.space_id == space_id))
    await db.commit()
    await db.refresh(space)
    return _space_out(space, member_count=cnt_result.scalar() or 0)


@router.delete("/{space_id}")
async def delete_space(space_id: uuid.UUID, user: CurrentUser, db: DbSession):
    await _require_admin(db, str(space_id), str(user.id))
    result = await db.execute(select(Space).where(Space.id == space_id))
    space = result.scalar_one_or_none()
    if space is None:
        raise HTTPException(status_code=404, detail="Space not found")
    await db.delete(space)
    await db.commit()
    await _invalidate_space_cache(str(user.id))
    return {"ok": True}


# ── member management ──────────────────────────────────────────

@router.get("/{space_id}/members", response_model=list[SpaceMemberOut])
async def list_members(space_id: uuid.UUID, user: CurrentUser, db: DbSession):
    await _require_member(db, str(space_id), str(user.id))
    result = await db.execute(
        select(SpaceMember, User.username, User.email)
        .join(User, User.id == SpaceMember.user_id)
        .where(SpaceMember.space_id == space_id)
        .order_by(SpaceMember.joined_at.asc())
    )
    return [
        {
            "id": row.SpaceMember.id,
            "user_id": row.SpaceMember.user_id,
            "username": row.username,
            "email": row.email,
            "role": row.SpaceMember.role,
            "joined_at": row.SpaceMember.joined_at,
        }
        for row in result
    ]


@router.post("/{space_id}/invite", response_model=SpaceInvitationOut)
async def invite_member(
    space_id: uuid.UUID, body: SpaceInviteRequest, user: CurrentUser, db: DbSession
):
    await _require_admin(db, str(space_id), str(user.id))
    existing = await db.execute(
        select(SpaceMember).where(
            SpaceMember.space_id == space_id, SpaceMember.user_id == body.user_id
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="User is already a member")
    pending = await db.execute(
        select(SpaceInvitation).where(
            SpaceInvitation.space_id == space_id,
            SpaceInvitation.invitee_id == body.user_id,
            SpaceInvitation.status == "pending",
        )
    )
    if pending.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Pending invitation already exists")
    invitation = SpaceInvitation(
        space_id=space_id, inviter_id=user.id, invitee_id=body.user_id
    )
    db.add(invitation)
    await db.commit()
    await db.refresh(invitation)
    await _invalidate_space_cache(str(body.user_id))
    from src.services.space_service import send_invitation_notification
    await send_invitation_notification(db, invitation)
    return invitation


@router.get("/invitations/pending", response_model=list[SpaceInvitationOut])
async def my_pending_invitations(user: CurrentUser, db: DbSession):
    result = await db.execute(
        select(SpaceInvitation, User.username, Space.name)
        .join(User, User.id == SpaceInvitation.inviter_id)
        .join(Space, Space.id == SpaceInvitation.space_id)
        .where(
            SpaceInvitation.invitee_id == user.id,
            SpaceInvitation.status == "pending",
        )
        .order_by(SpaceInvitation.created_at.desc())
    )
    return [
        {
            "id": inv.id,
            "space_id": inv.space_id,
            "inviter_id": inv.inviter_id,
            "invitee_id": inv.invitee_id,
            "inviter_name": username,
            "space_name": space_name,
            "status": inv.status,
            "created_at": inv.created_at,
        }
        for inv, username, space_name in result
    ]

@router.get("/{space_id}/invitations", response_model=list[SpaceInvitationOut])
async def list_invitations(space_id: uuid.UUID, user: CurrentUser, db: DbSession):
    await _require_admin(db, str(space_id), str(user.id))
    result = await db.execute(
        select(SpaceInvitation, User.username)
        .join(User, User.id == SpaceInvitation.invitee_id)
        .where(SpaceInvitation.space_id == space_id)
        .order_by(SpaceInvitation.created_at.desc())
    )
    return [
        {
            "id": inv.id,
            "space_id": inv.space_id,
            "inviter_id": inv.inviter_id,
            "invitee_id": inv.invitee_id,
            "invitee_name": username,
            "status": inv.status,
            "created_at": inv.created_at,
        }
        for inv, username in result
    ]



@router.post("/invitations/{invitation_id}/respond")
async def respond_invitation(
    invitation_id: uuid.UUID, body: SpaceInviteRespond, user: CurrentUser, db: DbSession
):
    result = await db.execute(
        select(SpaceInvitation).where(
            SpaceInvitation.id == invitation_id,
            SpaceInvitation.invitee_id == user.id,
            SpaceInvitation.status == "pending",
        )
    )
    invitation = result.scalar_one_or_none()
    if invitation is None:
        raise HTTPException(status_code=404, detail="Invitation not found")
    if body.accept:
        invitation.status = "accepted"
        member = SpaceMember(space_id=invitation.space_id, user_id=user.id, role="member")
        db.add(member)
    else:
        invitation.status = "rejected"
    await db.commit()
    await _invalidate_space_cache(str(user.id))
    return {"ok": True, "status": invitation.status}


@router.put("/{space_id}/members/{member_user_id}/role")
async def update_member_role(
    space_id: uuid.UUID,
    member_user_id: uuid.UUID,
    body: SpaceMemberRoleUpdate,
    user: CurrentUser,
    db: DbSession,
):
    await _require_admin(db, str(space_id), str(user.id))
    space_result = await db.execute(select(Space.created_by).where(Space.id == space_id))
    space = space_result.scalar_one_or_none()
    if space is None:
        raise HTTPException(status_code=404, detail="Space not found")
    if member_user_id == space.created_by and body.role != "admin":
        raise HTTPException(status_code=400, detail="Cannot demote the space creator")
    result = await db.execute(
        select(SpaceMember).where(
            SpaceMember.space_id == space_id, SpaceMember.user_id == member_user_id
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    member.role = body.role
    await db.commit()
    await _invalidate_space_cache(str(member_user_id))
    return {"ok": True}


@router.delete("/{space_id}/members/{member_user_id}")
async def remove_member(
    space_id: uuid.UUID, member_user_id: uuid.UUID, user: CurrentUser, db: DbSession
):
    await _require_admin(db, str(space_id), str(user.id))
    space_result = await db.execute(select(Space.created_by).where(Space.id == space_id))
    space = space_result.scalar_one_or_none()
    if space is None:
        raise HTTPException(status_code=404, detail="Space not found")
    if member_user_id == space.created_by:
        raise HTTPException(status_code=400, detail="Cannot remove the space creator")
    result = await db.execute(
        select(SpaceMember).where(
            SpaceMember.space_id == space_id, SpaceMember.user_id == member_user_id
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    await db.delete(member)
    await db.commit()
    await _invalidate_space_cache(str(member_user_id))
    return {"ok": True}


@router.post("/{space_id}/leave")
async def leave_space(space_id: uuid.UUID, user: CurrentUser, db: DbSession):
    await _require_member(db, str(space_id), str(user.id))
    space_result = await db.execute(select(Space).where(Space.id == space_id))
    space = space_result.scalar_one_or_none()
    if space is None:
        raise HTTPException(status_code=404, detail="Space not found")
    if str(user.id) == str(space.created_by):
        admin_count_result = await db.execute(
            select(func.count()).where(
                SpaceMember.space_id == space_id, SpaceMember.role == "admin"
            )
        )
        if admin_count_result.scalar() <= 1:
            raise HTTPException(
                status_code=400,
                detail="As the only admin and space creator, transfer the space or appoint another admin before leaving",
            )
    result = await db.execute(
        select(SpaceMember).where(
            SpaceMember.space_id == space_id, SpaceMember.user_id == user.id
        )
    )
    member = result.scalar_one_or_none()
    if member:
        await db.delete(member)
        await db.commit()
        await _invalidate_space_cache(str(user.id))
    return {"ok": True}


# ── join requests ──────────────────────────────────────────────

@router.post("/{space_id}/join-request")
async def request_join(
    space_id: uuid.UUID, body: SpaceJoinRequestCreate, user: CurrentUser, db: DbSession
):
    space_result = await db.execute(select(Space).where(Space.id == space_id))
    space = space_result.scalar_one_or_none()
    if space is None:
        raise HTTPException(status_code=404, detail="Space not found")
    if space.visibility != "public":
        raise HTTPException(status_code=400, detail="Space is not public")
    existing_member = await db.execute(
        select(SpaceMember).where(
            SpaceMember.space_id == space_id, SpaceMember.user_id == user.id
        )
    )
    if existing_member.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Already a member")
    existing_req = await db.execute(
        select(SpaceJoinRequest).where(
            SpaceJoinRequest.space_id == space_id,
            SpaceJoinRequest.user_id == user.id,
            SpaceJoinRequest.status == "pending",
        )
    )
    if existing_req.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Pending join request already exists")
    req = SpaceJoinRequest(space_id=space_id, user_id=user.id, message=body.message)
    db.add(req)
    await db.commit()
    await db.refresh(req)
    from src.services.space_service import notify_admins_join_request
    await notify_admins_join_request(db, space_id, req)
    return {"ok": True, "id": str(req.id)}


@router.get("/{space_id}/join-requests", response_model=list[SpaceJoinRequestOut])
async def list_join_requests(space_id: uuid.UUID, user: CurrentUser, db: DbSession):
    await _require_admin(db, str(space_id), str(user.id))
    result = await db.execute(
        select(SpaceJoinRequest, User.username)
        .join(User, User.id == SpaceJoinRequest.user_id)
        .where(
            SpaceJoinRequest.space_id == space_id,
            SpaceJoinRequest.status == "pending",
        )
        .order_by(SpaceJoinRequest.created_at.asc())
    )
    return [
        {
            "id": row.SpaceJoinRequest.id,
            "space_id": row.SpaceJoinRequest.space_id,
            "user_id": row.SpaceJoinRequest.user_id,
            "username": row.username,
            "message": row.SpaceJoinRequest.message,
            "status": row.SpaceJoinRequest.status,
            "created_at": row.SpaceJoinRequest.created_at,
        }
        for row in result
    ]


@router.put("/{space_id}/join-requests/{req_id}")
async def review_join_request(
    space_id: uuid.UUID,
    req_id: uuid.UUID,
    body: SpaceJoinRequestReview,
    user: CurrentUser,
    db: DbSession,
):
    await _require_admin(db, str(space_id), str(user.id))
    result = await db.execute(
        select(SpaceJoinRequest).where(
            SpaceJoinRequest.id == req_id,
            SpaceJoinRequest.space_id == space_id,
            SpaceJoinRequest.status == "pending",
        )
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Join request not found")
    req.status = body.status
    req.reviewed_by = user.id
    if body.status == "approved":
        existing_member = await db.execute(
            select(SpaceMember).where(
                SpaceMember.space_id == space_id, SpaceMember.user_id == req.user_id
            )
        )
        if not existing_member.scalar_one_or_none():
            db.add(SpaceMember(space_id=space_id, user_id=req.user_id, role="member"))
    await db.commit()
    await _invalidate_space_cache(str(req.user_id))
    from src.services.space_service import notify_join_request_result
    await notify_join_request_result(db, req)
    return {"ok": True, "status": req.status}


# ── search users (for invitation) ──────────────────────────────

@router.get("/search-users")
async def search_users(
    q: str = Query(min_length=1),
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    result = await db.execute(
        select(User.id, User.username, User.email)
        .where(
            or_(User.username.ilike(f"%{q}%"), User.email.ilike(f"%{q}%")),
            User.is_active,
        )
        .limit(20)
    )
    return [
        {"id": str(row.id), "username": row.username, "email": row.email}
        for row in result
    ]


# ── migration ───────────────────────────────────────────────

@router.post("/migrate-default-spaces")
async def migrate_default_spaces(db: DbSession, _=Depends(get_current_user)):
    """Create a default space for any user who doesn't have one yet."""
    from src.services.space_service import clone_templates_to_space

    result = await db.execute(
        select(User.id, User.username)
        .where(User.is_active)
    )
    users = list(result.all())

    created = 0
    skipped = 0
    for row in users:
        uid = str(row.id)
        existing = await db.execute(
            select(Space.id).where(Space.created_by == uid)
        )
        if existing.scalar_one_or_none():
            skipped += 1
            continue
        space = Space(
            name=f"{row.username}的空间",
            description="默认空间",
            visibility="private",
            created_by=uid,
        )
        db.add(space)
        await db.flush()
        member = SpaceMember(space_id=space.id, user_id=uid, role="admin")
        db.add(member)
        await db.flush()
        try:
            await clone_templates_to_space(db, str(space.id))
        except Exception:
            pass
        created += 1

    await db.commit()
    return {"ok": True, "created": created, "skipped": skipped}
