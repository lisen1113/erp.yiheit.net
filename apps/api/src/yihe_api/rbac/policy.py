"""Common authorization, field projection, and separation of duties."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.expression import SQLColumnExpression

from yihe_api.auth.models import AuthSession, User
from yihe_api.errors import ApiError, ErrorCode
from yihe_api.rbac.models import Permission, Role, RolePermission, UserRole


class Scope(StrEnum):
    SELF = "SELF"
    DEPARTMENT = "DEPARTMENT"
    COMPANY = "COMPANY"


USER_VIEW = "system.user.view"
USER_MANAGE = "system.user.manage"
ROLE_VIEW = "system.role.view"
ROLE_MANAGE = "system.role.manage"
PERMISSION_VIEW = "system.permission.view"
COST_VIEW = "inventory.cost.view"
MARGIN_VIEW = "sales.margin.view"
BANK_FULL = "bank_account.view_full"
BANK_MASKED = "bank_account.view_masked"

SOD_ACTIONS = frozenset(
    {
        "finance.payment.approve",
        "finance.refund.approve",
        "rebate.confirm.approve",
        "rebate.adjustment.approve",
        "inventory.cost_adjustment.approve",
        "inventory.negative_stock.approve",
        "period.reopen.approve",
        "company.profile_change.approve",
        "bank_account.change.approve",
        "company.profile.approve",
        "customer.credit_limit.approve",
    }
)
HIGH_PERMISSIONS = SOD_ACTIONS | {
    USER_MANAGE,
    ROLE_MANAGE,
    COST_VIEW,
    MARGIN_VIEW,
    BANK_FULL,
    "file_asset.view_sensitive",
    "migration_file.upload",
    "migration_file.download",
    "brand_asset.manage",
    "company.profile.edit",
    "bank_account.edit",
    "quotation.view_cost",
    "quotation.approve",
    "report.export",
    "serial_number.export",
    "system.audit.view",
    "system.audit.export",
    "system.audit.view_sensitive_metadata",
    "system.security_event.view",
}
PERMISSIONS = (
    frozenset(
        {
            USER_VIEW,
            ROLE_VIEW,
            PERMISSION_VIEW,
            BANK_MASKED,
            "company.profile.view",
            "template.manage",
            "file_asset.upload",
            "file_asset.view",
            "file_asset.download",
            "file_asset.invalidate",
            "brand_asset.view",
            "system.security_event.view",
            "brand_asset.manage",
            "quotation.create",
            "quotation.render",
            "quotation.include_bank",
        }
    )
    | HIGH_PERMISSIONS
)
SYSTEM_ADMIN = "SYSTEM_ADMIN"
SYSTEM_ROLES = {
    SYSTEM_ADMIN: (
        "系统管理员",
        Scope.COMPANY,
        True,
        {
            USER_VIEW,
            USER_MANAGE,
            ROLE_VIEW,
            ROLE_MANAGE,
            PERMISSION_VIEW,
            "company.profile.view",
            "company.profile.edit",
            "template.manage",
            "file_asset.upload",
            "file_asset.view",
            "file_asset.download",
            "file_asset.invalidate",
            "brand_asset.view",
            "system.security_event.view",
            "brand_asset.manage",
        },
    ),
    "MANAGEMENT": ("管理层", Scope.COMPANY, True, {"company.profile.view", "brand_asset.view"}),
    "FINANCE": ("财务", Scope.COMPANY, True, {"company.profile.view", BANK_MASKED, BANK_FULL}),
    "SALES_MANAGER": ("销售经理", Scope.SELF, False, set()),
    "SALES": ("销售", Scope.SELF, False, {BANK_MASKED, "quotation.create", "quotation.render"}),
    "PURCHASE": ("采购", Scope.SELF, False, {BANK_MASKED}),
    "WAREHOUSE": ("仓库", Scope.SELF, False, set()),
    "AUDITOR": ("审计员", Scope.COMPANY, False, {"system.audit.view"}),
}


@dataclass
class Principal:
    user: User
    grants: dict[str, set[Scope]]
    blocked: set[str]
    role_ids: set[UUID]

    def require(self, code: str, *, company: bool = False) -> Scope:
        scopes = self.grants.get(code, set())
        if Scope.COMPANY in scopes:
            return Scope.COMPANY
        if not company and Scope.SELF in scopes:
            return Scope.SELF
        if code in self.blocked:
            raise ApiError(ErrorCode.MFA_REQUIRED)
        if Scope.DEPARTMENT in scopes:
            raise ApiError(ErrorCode.DATA_SCOPE_UNAVAILABLE)
        raise ApiError(ErrorCode.FORBIDDEN)


def principal_for(db: Session, user: User, session: AuthSession, now: datetime) -> Principal:
    rows = db.execute(
        select(
            Role.id,
            Role.data_scope,
            Role.requires_mfa,
            Permission.code,
            Permission.sensitivity_level,
        )
        .join(UserRole, UserRole.role_id == Role.id)
        .outerjoin(RolePermission, RolePermission.role_id == Role.id)
        .outerjoin(
            Permission,
            (Permission.id == RolePermission.permission_id) & Permission.is_active.is_(True),
        )
        .where(
            UserRole.company_id == user.company_id,
            Role.company_id == user.company_id,
            UserRole.user_id == user.id,
            Role.is_active.is_(True),
            Role.is_deleted.is_(False),
            UserRole.revoked_at.is_(None),
            UserRole.valid_from <= now,
            or_(UserRole.valid_until.is_(None), UserRole.valid_until > now),
        )
    )
    result = Principal(user, {}, set(), set())
    for role_id, scope, requires_mfa, code, sensitivity in rows:
        result.role_ids.add(role_id)
        if code is None:
            continue
        needs_mfa = requires_mfa or sensitivity == "high" or code in HIGH_PERMISSIONS
        if needs_mfa and not (user.mfa_enabled and session.mfa_verified):
            result.blocked.add(code)
            continue
        result.grants.setdefault(code, set()).add(Scope(scope.upper()))
    return result


def scope_filters(
    principal: Principal,
    permission: str,
    company: SQLColumnExpression[UUID],
    *,
    owner_user: SQLColumnExpression[UUID] | None = None,
    owner_employee: SQLColumnExpression[UUID] | None = None,
) -> tuple[ColumnElement[bool], ...]:
    scope = principal.require(permission)
    company_filter = company == principal.user.company_id
    if scope == Scope.COMPANY:
        return (company_filter,)
    conditions = []
    if owner_user is not None:
        conditions.append(owner_user == principal.user.id)
    if owner_employee is not None:
        conditions.append(owner_employee == principal.user.employee_id)
    if not conditions:
        raise ApiError(ErrorCode.FORBIDDEN)
    return (company_filter, or_(*conditions))


def project_fields(
    values: Mapping[str, object],
    principal: Principal,
    *,
    company_id: UUID,
    owner_user_id: UUID | None = None,
    owner_employee_id: UUID | None = None,
) -> dict[str, object]:
    if company_id != principal.user.company_id:
        raise ApiError(ErrorCode.NOT_FOUND)
    owns_record = (
        owner_user_id == principal.user.id or owner_employee_id == principal.user.employee_id
    )
    allowed = {
        code
        for code, scopes in principal.grants.items()
        if Scope.COMPANY in scopes or (owns_record and Scope.SELF in scopes)
    }
    result = dict(values)
    for code, fields in (
        (COST_VIEW, ("unit_cost", "inventory_value")),
        (MARGIN_VIEW, ("sales_cost", "gross_margin", "gross_margin_rate")),
    ):
        if code not in allowed:
            for field in fields:
                result.pop(field, None)
    if "bank_account_number" in result and BANK_FULL not in allowed:
        value = result.pop("bank_account_number")
        if BANK_MASKED in allowed and isinstance(value, str):
            result["bank_account_number"] = "****" + (value[-4:] if len(value) > 4 else "")
    return result


def assert_separation_of_duties(
    maker_user_id: UUID,
    approver_user_id: UUID,
    action_code: str,
    *,
    final_approver_user_id: UUID | None = None,
) -> None:
    if action_code not in SOD_ACTIONS:
        raise ValueError("Register the separation-of-duties action before use")
    if maker_user_id in {approver_user_id, final_approver_user_id}:
        raise ApiError(ErrorCode.SEPARATION_OF_DUTIES)
