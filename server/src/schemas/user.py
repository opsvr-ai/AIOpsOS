import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, field_serializer, field_validator


class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str


class UserLogin(BaseModel):
    username: str
    password: str
    login_type: str = "local"


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str

    @field_serializer("id")
    def serialize_id(self, value: uuid.UUID) -> str:
        return str(value)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    email: str
    is_active: bool
    default_space_id: str | None = None
    roles: list[RoleOut] = []
    display_name: str | None = None
    phone: str | None = None
    department: str | None = None
    title: str | None = None
    source: str = "local"
    status: str = "active"
    setup_required: bool = False

    @field_validator("default_space_id", mode="before")
    @classmethod
    def coerce_default_space_id(cls, v: object) -> str | None:
        if v is None:
            return None
        return str(v)

    @field_serializer("id", "default_space_id")
    def serialize_id(self, value: uuid.UUID | None) -> str | None:
        return str(value) if value is not None else None


class UserAdminCreate(BaseModel):
    username: str
    email: str
    password: str
    display_name: str | None = None
    phone: str | None = None
    department: str | None = None
    title: str | None = None
    role_ids: list[str] = []


class UserAdminUpdate(BaseModel):
    username: str | None = None
    email: str | None = None
    password: str | None = None
    display_name: str | None = None
    phone: str | None = None
    department: str | None = None
    title: str | None = None
    is_active: bool | None = None
    status: str | None = None
    role_ids: list[str] | None = None


class ProfileUpdate(BaseModel):
    display_name: str | None = None
    phone: str | None = None
    department: str | None = None
    title: str | None = None
    email: str | None = None


class PasswordChange(BaseModel):
    old_password: str
    new_password: str


class UserRegistrationApproval(BaseModel):
    approved: bool
    message: str | None = None


class UserInvitationCreate(BaseModel):
    email: str
    space_id: str | None = None


class UserInvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    token: str
    inviter_id: uuid.UUID
    space_id: uuid.UUID | None = None
    status: str
    expires_at: str
    created_at: str

    @field_serializer("id", "inviter_id", "space_id")
    def serialize_id(self, value: uuid.UUID) -> str:
        return str(value)
