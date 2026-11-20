"""RBAC API and reusable FastAPI authorization dependency."""

from collections.abc import Callable, Mapping
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from yihe_api.audit.payload import permission_diff
from yihe_api.audit.service import Actor, Category, audit_change
from yihe_api.auth.models import Employee, User
from yihe_api.auth.router import DB, authenticated, now_utc, revoke_all
from yihe_api.auth.security import PASSWORD_HASHER, valid_new_password
from yihe_api.db.models import Company
from yihe_api.errors import ApiError, ErrorCode
from yihe_api.rbac.models import Permission, Role, RolePermission, UserRole
from yihe_api.rbac.policy import (
    PERMISSION_VIEW,
    ROLE_MANAGE,
    ROLE_VIEW,
    SYSTEM_ROLES,
    USER_MANAGE,
    USER_VIEW,
    Principal,
    Scope,
    principal_for,
    scope_filters,
)
from yihe_api.rbac.schemas import (
    AssignmentView,
    EffectivePermissions,
    PermissionChange,
    PermissionView,
    RoleAssignment,
    RoleCreate,
    RolePatch,
    RoleView,
    UserCreate,
    UserPatch,
    UserView,
)
from yihe_api.tracing import LOGGER

router = APIRouter(tags=["authorization"])
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=200)]


def current_principal(request: Request, db: DB) -> Principal:
    user, session = authenticated(request, db, lock=False)
    return principal_for(db, user, session, now_utc())


Current = Annotated[Principal, Depends(current_principal)]


def require_permission(code: str, *, company: bool = False) -> Callable[..., Principal]:
    def enforce(principal: Current) -> Principal:
        principal.require(code, company=company)
        return principal

    return enforce


def locked_principal(request: Request, db: Session, code: str) -> Principal:
    initial, _ = authenticated(request, db, lock=False)
    # Mutations serialize per company before evaluating live grants and versions.
    db.get(Company, initial.company_id, with_for_update=True)
    user, session = authenticated(request, db)
    principal = principal_for(db, user, session, now_utc())
    principal.require(code, company=True)
    return principal


def target_user(db: Session, principal: Principal, target_id: UUID) -> User:
    user = db.scalar(
        select(User)
        .where(
            User.id == target_id,
            User.company_id == principal.user.company_id,
        )
        .with_for_update()
    )
    if user is None:
        raise ApiError(ErrorCode.NOT_FOUND)
    if user.id == principal.user.id:
        raise ApiError(ErrorCode.SELF_ESCALATION_DENIED)
    return user


def target_role(db: Session, principal: Principal, target_id: UUID) -> Role:
    role = db.scalar(
        select(Role)
        .where(
            Role.id == target_id,
            Role.company_id == principal.user.company_id,
            Role.is_deleted.is_(False),
        )
        .with_for_update()
    )
    if role is None:
        raise ApiError(ErrorCode.NOT_FOUND)
    return role


def version_matches(current: int, requested: int) -> None:
    if current != requested:
        raise ApiError(ErrorCode.VERSION_CONFLICT)


def reject_role_self_edit(
    db: Session,
    principal: Principal,
    role: Role,
    *,
    mfa_update: bool = False,
) -> None:
    membership = db.scalar(
        select(UserRole.id)
        .where(
            UserRole.role_id == role.id,
            UserRole.user_id == principal.user.id,
            UserRole.revoked_at.is_(None),
        )
        .limit(1)
    )
    if membership is not None or (role.is_system and not mfa_update):
        raise ApiError(ErrorCode.FORBIDDEN)


def role_permissions(db: Session, role: Role) -> list[Permission]:
    return list(
        db.scalars(
            select(Permission)
            .join(
                RolePermission,
                RolePermission.permission_id == Permission.id,
            )
            .where(RolePermission.role_id == role.id)
        )
    )


def check_delegation(principal: Principal, permissions: list[Permission]) -> None:
    for permission in permissions:
        if not permission.is_active:
            raise ApiError(ErrorCode.FORBIDDEN)
        principal.require(permission.code, company=True)


def flush_change(
    db: Session,
    request: Request,
    actor: UUID,
    target: UUID,
    action: str,
    *,
    before: Mapping[str, object] | None = None,
    after: Mapping[str, object] | None = None,
    metadata: Mapping[str, object] | None = None,
) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) not in {"23505", "23P01"}:
            raise
        raise ApiError(ErrorCode.CONFLICT) from None
    identity = getattr(request.state, "audit_actor", None)
    if not isinstance(identity, Actor) or identity.user_id != actor:
        raise ApiError(ErrorCode.AUDIT_EVENT_INVALID)
    audit_change(
        db,
        company_id=identity.company_id,
        actor=identity,
        action=action,
        entity_type="role" if action.startswith("role.") else "app_user",
        entity_id=target,
        category=Category.AUTHORIZATION,
        before=before,
        after=after,
        metadata=metadata,
        request=request,
    )
    LOGGER.info(
        "rbac.change",
        extra={"actor_id": str(actor), "target_id": str(target), "rbac_action": action},
    )


def user_view(user: User) -> UserView:
    return UserView.model_validate(user, from_attributes=True)


def role_view(role: Role) -> RoleView:
    return RoleView(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        data_scope=Scope(role.data_scope.upper()),
        requires_mfa=role.requires_mfa,
        is_active=role.is_active,
        is_system=role.is_system,
        version_no=role.version_no,
    )


@router.get("/users")
def list_users(
    db: DB, principal: Current, page: Page = 1, page_size: PageSize = 50
) -> list[UserView]:
    filters = scope_filters(principal, USER_VIEW, User.company_id, owner_user=User.id)
    rows = db.scalars(
        select(User)
        .where(*filters)
        .order_by(User.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return [user_view(user) for user in rows]


@router.get("/users/{id}")
def get_user(id: UUID, db: DB, principal: Current) -> UserView:
    filters = scope_filters(principal, USER_VIEW, User.company_id, owner_user=User.id)
    target = db.scalar(select(User).where(User.id == id, *filters))
    if target is None:
        raise ApiError(ErrorCode.NOT_FOUND)
    return user_view(target)


@router.post("/users", status_code=201)
def create_user(payload: UserCreate, request: Request, db: DB) -> UserView:
    principal = locked_principal(request, db, USER_MANAGE)
    actor = principal.user
    employee = db.scalar(
        select(Employee).where(
            Employee.id == payload.employee_id,
            Employee.company_id == actor.company_id,
            Employee.is_active.is_(True),
        )
    )
    if employee is None:
        raise ApiError(ErrorCode.NOT_FOUND)
    password = payload.password.get_secret_value()
    if not valid_new_password(password, payload.username):
        raise ApiError(ErrorCode.AUTH_PASSWORD_POLICY)
    user = User(
        company_id=actor.company_id,
        employee_id=employee.id,
        username=payload.username,
        password_hash=PASSWORD_HASHER.hash(password),
        created_by=actor.id,
        updated_by=actor.id,
        mfa_required=payload.mfa_required,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) != "23505":
            raise
        raise ApiError(ErrorCode.CONFLICT) from None
    flush_change(
        db,
        request,
        actor.id,
        user.id,
        "user.create",
        after={"status": user.status, "employee_id": user.employee_id},
    )
    return user_view(user)


@router.patch("/users/{id}")
def patch_user(id: UUID, payload: UserPatch, request: Request, db: DB) -> UserView:
    principal = locked_principal(request, db, USER_MANAGE)
    user = target_user(db, principal, id)
    version_matches(user.version_no, payload.version_no)
    if payload.status == "active" and user.status != "active":
        active_grants = list(
            db.scalars(
                select(Permission)
                .join(RolePermission)
                .join(Role)
                .join(UserRole)
                .where(UserRole.user_id == user.id, UserRole.revoked_at.is_(None))
            )
        )
        check_delegation(principal, active_grants)
    before = {"status": user.status}
    user.status = payload.status
    user.updated_by = principal.user.id
    user.version_no += 1
    if user.status == "disabled":
        revoke_all(db, user, now_utc())
    flush_change(
        db,
        request,
        principal.user.id,
        user.id,
        "user.status",
        before=before,
        after={"status": user.status},
    )
    return user_view(user)


@router.post("/users/{id}/roles", status_code=201)
def assign_role(id: UUID, payload: RoleAssignment, request: Request, db: DB) -> AssignmentView:
    principal = locked_principal(request, db, ROLE_MANAGE)
    user = target_user(db, principal, id)
    version_matches(user.version_no, payload.version_no)
    role = target_role(db, principal, payload.role_id)
    if not role.is_active:
        raise ApiError(ErrorCode.FORBIDDEN)
    check_delegation(principal, role_permissions(db, role))
    now = now_utc()
    if payload.valid_until is not None and payload.valid_until <= now:
        raise ApiError(ErrorCode.VALIDATION_ERROR)
    prior = db.scalar(
        select(UserRole).where(
            UserRole.company_id == user.company_id,
            UserRole.user_id == user.id,
            UserRole.role_id == role.id,
            UserRole.revoked_at.is_(None),
        )
    )
    if prior is not None:
        if prior.valid_until is None or prior.valid_until > now:
            raise ApiError(ErrorCode.CONFLICT)
        prior.revoked_at = now
        prior.revoked_by = principal.user.id
        prior.version_no += 1
        db.flush()
    entry = UserRole(
        company_id=user.company_id,
        user_id=user.id,
        role_id=role.id,
        assigned_by=principal.user.id,
        valid_from=now,
        valid_until=payload.valid_until,
    )
    db.add(entry)
    user.updated_by = principal.user.id
    user.version_no += 1
    flush_change(
        db, request, principal.user.id, user.id, "user.role.assign", metadata={"role_id": role.id}
    )
    return AssignmentView.model_validate(entry, from_attributes=True)


@router.delete("/users/{id}/roles/{role_id}", status_code=204)
def revoke_role(
    id: UUID, role_id: UUID, request: Request, db: DB, version_no: Annotated[int, Query(ge=1)]
) -> None:
    principal = locked_principal(request, db, ROLE_MANAGE)
    user = target_user(db, principal, id)
    version_matches(user.version_no, version_no)
    target_role(db, principal, role_id)
    entry = db.scalar(
        select(UserRole).where(
            UserRole.company_id == user.company_id,
            UserRole.user_id == user.id,
            UserRole.role_id == role_id,
            UserRole.revoked_at.is_(None),
        )
    )
    if entry is None:
        raise ApiError(ErrorCode.NOT_FOUND)
    entry.revoked_at = now_utc()
    entry.revoked_by = principal.user.id
    entry.version_no += 1
    user.version_no += 1
    user.updated_by = principal.user.id
    flush_change(
        db, request, principal.user.id, user.id, "user.role.revoke", metadata={"role_id": role_id}
    )


@router.get("/roles")
def list_roles(
    db: DB, principal: Current, page: Page = 1, page_size: PageSize = 50
) -> list[RoleView]:
    principal.require(ROLE_VIEW, company=True)
    roles = db.scalars(
        select(Role)
        .where(Role.company_id == principal.user.company_id, Role.is_deleted.is_(False))
        .order_by(Role.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return [role_view(role) for role in roles]


@router.post("/roles", status_code=201)
def create_role(payload: RoleCreate, request: Request, db: DB) -> RoleView:
    principal = locked_principal(request, db, ROLE_MANAGE)
    actor = principal.user
    if payload.code in SYSTEM_ROLES:
        raise ApiError(ErrorCode.CONFLICT)
    role = Role(
        company_id=actor.company_id,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        data_scope=payload.data_scope.lower(),
        requires_mfa=payload.requires_mfa,
        created_by=actor.id,
        updated_by=actor.id,
    )
    db.add(role)
    try:
        db.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) != "23505":
            raise
        raise ApiError(ErrorCode.CONFLICT) from None
    flush_change(db, request, actor.id, role.id, "role.create", after=role_audit_fields(role))
    return role_view(role)


@router.patch("/roles/{id}")
def patch_role(id: UUID, payload: RolePatch, request: Request, db: DB) -> RoleView:
    principal = locked_principal(request, db, ROLE_MANAGE)
    role = target_role(db, principal, id)
    reject_role_self_edit(db, principal, role, mfa_update=True)
    version_matches(role.version_no, payload.version_no)
    if role.is_system:
        unchanged_template = (
            payload.name == role.name
            and payload.description == role.description
            and payload.data_scope.lower() == role.data_scope
            and payload.is_active == role.is_active
        )
        mandatory_mfa = SYSTEM_ROLES[role.code][2]
        if not unchanged_template or (mandatory_mfa and not payload.requires_mfa):
            raise ApiError(ErrorCode.FORBIDDEN)
        if not payload.requires_mfa:
            check_delegation(principal, role_permissions(db, role))
    else:
        check_delegation(principal, role_permissions(db, role))
    before = role_audit_fields(role)
    role.name = payload.name
    role.description = payload.description
    role.data_scope = payload.data_scope.lower()
    role.requires_mfa = payload.requires_mfa
    role.is_active = payload.is_active
    role.updated_by = principal.user.id
    role.version_no += 1
    flush_change(
        db,
        request,
        principal.user.id,
        role.id,
        "role.update",
        before=before,
        after=role_audit_fields(role),
    )
    return role_view(role)


@router.get("/permissions")
def list_permissions(
    db: DB,
    principal: Annotated[Principal, Depends(require_permission(PERMISSION_VIEW, company=True))],
    page: Page = 1,
    page_size: PageSize = 50,
) -> list[PermissionView]:
    rows = db.scalars(
        select(Permission).order_by(Permission.code).offset((page - 1) * page_size).limit(page_size)
    )
    return [PermissionView.model_validate(permission, from_attributes=True) for permission in rows]


@router.put("/roles/{id}/permissions")
def replace_permissions(
    id: UUID,
    payload: PermissionChange,
    request: Request,
    db: DB,
) -> RoleView:
    principal = locked_principal(request, db, ROLE_MANAGE)
    role = target_role(db, principal, id)
    reject_role_self_edit(db, principal, role)
    version_matches(role.version_no, payload.version_no)
    permissions = list(
        db.scalars(select(Permission).where(Permission.id.in_(payload.permission_ids)))
    )
    if len(permissions) != len(payload.permission_ids):
        raise ApiError(ErrorCode.NOT_FOUND)
    check_delegation(principal, permissions)
    existing = role_permissions(db, role)
    check_delegation(principal, existing)
    old_ids = {p.id for p in existing}
    removed = old_ids - payload.permission_ids
    if removed:
        db.execute(
            delete(RolePermission).where(
                RolePermission.role_id == id, RolePermission.permission_id.in_(removed)
            )
        )
    for key in payload.permission_ids - old_ids:
        db.add(RolePermission(role_id=id, permission_id=key, granted_by=principal.user.id))
    role.updated_by = principal.user.id
    role.version_no += 1
    flush_change(
        db,
        request,
        principal.user.id,
        role.id,
        "role.permissions.replace",
        metadata=permission_diff((p.code for p in existing), (p.code for p in permissions)),
    )
    return role_view(role)


@router.get("/auth/me/permissions")
def my_permissions(principal: Current) -> EffectivePermissions:
    return EffectivePermissions(
        permissions={k: sorted(v) for k, v in principal.grants.items()},
        mfa_required_for=sorted(principal.blocked - principal.grants.keys()),
    )


def role_audit_fields(role: Role) -> dict[str, object]:
    return {
        "is_active": role.is_active,
        "data_scope": role.data_scope.upper(),
        "requires_mfa": role.requires_mfa,
    }
