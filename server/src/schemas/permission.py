import uuid

from pydantic import BaseModel, ConfigDict


class PermissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    resource: str
    action: str
    description: str | None = None


class PermissionCreate(BaseModel):
    resource: str
    action: str
    description: str | None = None


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    description: str | None = None
    permissions: list[PermissionOut] = []


class RoleCreate(BaseModel):
    name: str
    description: str | None = None
    permission_ids: list[str] = []


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    permission_ids: list[str] | None = None


class UserRoleAssign(BaseModel):
    role_ids: list[str]


class UserAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    username: str
    email: str
    is_active: bool
    is_ldap: bool = False
    roles: list[RoleOut] = []
    display_name: str | None = None
    phone: str | None = None
    department: str | None = None
    title: str | None = None
    source: str = "local"
    status: str = "active"
    created_at: str | None = None


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


class UserInvitationRequest(BaseModel):
    email: str
    space_id: str | None = None
    platform_url: str | None = None
