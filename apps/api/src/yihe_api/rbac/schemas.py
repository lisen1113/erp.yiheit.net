from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, SecretStr, StringConstraints

from yihe_api.rbac.policy import Scope

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
RoleCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,49}$")]
Version = Annotated[int, Field(ge=1)]


class UserCreate(BaseModel):
    employee_id: UUID
    username: Name
    password: Annotated[SecretStr, Field(min_length=12, max_length=128)]
    mfa_required: bool = False


class UserPatch(BaseModel):
    version_no: Version
    status: Literal["active", "disabled"]


class UserView(BaseModel):
    id: UUID
    employee_id: UUID
    company_id: UUID
    username: str
    status: str
    mfa_required: bool
    mfa_enabled: bool
    version_no: int


class RoleCreate(BaseModel):
    code: RoleCode
    name: Name
    description: Annotated[str, Field(max_length=255)] | None = None
    data_scope: Scope = Scope.SELF
    requires_mfa: bool = False


class RolePatch(BaseModel):
    name: Name
    description: Annotated[str, Field(max_length=255)] | None = None
    data_scope: Scope
    requires_mfa: bool
    is_active: bool
    version_no: Version


class RoleView(RoleCreate):
    id: UUID
    is_active: bool
    is_system: bool
    version_no: int


class RoleAssignment(BaseModel):
    role_id: UUID
    version_no: Version
    valid_until: AwareDatetime | None = None


class AssignmentView(BaseModel):
    id: UUID
    role_id: UUID
    valid_from: datetime
    valid_until: datetime | None
    revoked_at: datetime | None


class PermissionView(BaseModel):
    id: UUID
    code: str
    module: str
    action: str
    description: str
    sensitivity_level: str
    is_active: bool


class PermissionChange(BaseModel):
    version_no: Version
    permission_ids: Annotated[set[UUID], Field(max_length=200)]


class EffectivePermissions(BaseModel):
    permissions: dict[str, list[Scope]]
    mfa_required_for: list[str]
