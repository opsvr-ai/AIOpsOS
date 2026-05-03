import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ── Space ──────────────────────────────────────────────────────

class SpaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    visibility: str = Field(default="private", pattern=r"^(private|public)$")


class SpaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    visibility: str | None = Field(default=None, pattern=r"^(private|public)$")


class SpaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None = None
    visibility: str
    created_by: uuid.UUID
    member_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SpaceDetailOut(SpaceOut):
    my_role: str | None = None


# ── Space Member ───────────────────────────────────────────────

class SpaceMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    username: str = ""
    email: str = ""
    role: str
    joined_at: datetime | None = None


class SpaceMemberRoleUpdate(BaseModel):
    role: str = Field(pattern=r"^(admin|member)$")


# ── Space Invitation ───────────────────────────────────────────

class SpaceInvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    space_id: uuid.UUID
    inviter_id: uuid.UUID
    invitee_id: uuid.UUID
    invitee_name: str = ""
    inviter_name: str = ""
    space_name: str = ""
    status: str
    created_at: datetime | None = None


class SpaceInviteRequest(BaseModel):
    user_id: uuid.UUID


class SpaceInviteRespond(BaseModel):
    accept: bool


# ── Space Join Request ─────────────────────────────────────────

class SpaceJoinRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    space_id: uuid.UUID
    user_id: uuid.UUID
    username: str = ""
    message: str | None = None
    status: str
    created_at: datetime | None = None


class SpaceJoinRequestCreate(BaseModel):
    message: str | None = Field(default=None, max_length=200)


class SpaceJoinRequestReview(BaseModel):
    status: str = Field(pattern=r"^(approved|rejected)$")
