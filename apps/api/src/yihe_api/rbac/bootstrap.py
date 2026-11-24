"""Install role templates and bootstrap the first administrator without a default secret."""

from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from yihe_api.audit.service import Actor, Category, Source, audit_change
from yihe_api.auth.models import Employee, User
from yihe_api.auth.security import PASSWORD_HASHER, valid_new_password
from yihe_api.db.models import Company
from yihe_api.rbac.models import Permission, Role, RolePermission, UserRole
from yihe_api.rbac.policy import (
    CUSTOMER_GRANTS,
    HIGH_PERMISSIONS,
    PARTNER_GRANTS,
    PERMISSIONS,
    SYSTEM_ADMIN,
    SYSTEM_ROLES,
)


def seed_roles(db: Session, company_id: UUID, actor_id: UUID) -> dict[str, Role]:
    permissions = {p.code: p for p in db.scalars(select(Permission))}
    for code in sorted(PERMISSIONS):
        if code not in permissions:
            permission = Permission(
                code=code,
                module=code.split(".")[0],
                action=code.split(".")[-1],
                description=code,
                sensitivity_level="high" if code in HIGH_PERMISSIONS else "standard",
            )
            db.add(permission)
            permissions[code] = permission
    db.flush()
    roles = {r.code: r for r in db.scalars(select(Role).where(Role.company_id == company_id))}
    for code, (name, scope, required, grants) in SYSTEM_ROLES.items():
        if code in roles:
            if not roles[code].is_system:
                raise ValueError("An existing custom role conflicts with a reserved system code")
            continue
        role = Role(
            company_id=company_id,
            code=code,
            name=name,
            data_scope=scope.lower(),
            requires_mfa=required,
            is_system=True,
            created_by=actor_id,
            updated_by=actor_id,
        )
        db.add(role)
        db.flush()
        db.add_all(
            RolePermission(role_id=role.id, permission_id=permissions[p].id, granted_by=actor_id)
            for p in sorted(
                grants | PARTNER_GRANTS.get(code, set()) | CUSTOMER_GRANTS.get(code, set())
            )
        )
        roles[code] = role
    db.flush()
    return roles


def bootstrap_admin(
    db: Session,
    *,
    company_code: str,
    company_name: str,
    employee_no: str,
    employee_name: str,
    username: str,
    password: str,
) -> UUID:
    values = (
        (company_code, 20),
        (company_name, 200),
        (employee_no, 32),
        (employee_name, 200),
        (username, 100),
    )
    if any(not value.strip() or len(value) > size for value, size in values):
        raise ValueError("Invalid bootstrap identity fields")
    if not valid_new_password(password, username):
        raise ValueError("Password does not meet the account policy")
    db.execute(text("SELECT pg_advisory_xact_lock(61106101)"))
    existing_admin = db.scalar(
        select(UserRole.id)
        .join(
            Role,
            Role.id == UserRole.role_id,
        )
        .where(Role.is_system.is_(True), Role.code == SYSTEM_ADMIN)
        .limit(1)
    )
    if existing_admin is not None:
        raise ValueError("An administrator has already been bootstrapped")
    actor_id = uuid4()
    company = db.scalar(select(Company).where(Company.code == company_code).with_for_update())
    if company is None:
        company = Company(
            code=company_code, name=company_name, created_by=actor_id, updated_by=actor_id
        )
        db.add(company)
        db.flush()
    elif company.is_deleted or company.name != company_name:
        raise ValueError("The existing company does not match the bootstrap identity")
    employee = Employee(
        company_id=company.id,
        employee_no=employee_no,
        name=employee_name,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(employee)
    db.flush()
    user = User(
        id=actor_id,
        company_id=company.id,
        employee_id=employee.id,
        username=username,
        password_hash=PASSWORD_HASHER.hash(password),
        mfa_required=True,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(user)
    db.flush()
    roles = seed_roles(db, company.id, actor_id)
    db.add(
        UserRole(
            company_id=company.id,
            user_id=actor_id,
            role_id=roles[SYSTEM_ADMIN].id,
            assigned_by=actor_id,
        )
    )
    db.flush()
    audit_change(
        db,
        company_id=company.id,
        actor=Actor(company.id, user.id, employee.id),
        action="rbac.bootstrap",
        entity_type="app_user",
        entity_id=user.id,
        category=Category.SYSTEM_CONFIGURATION,
        source=Source.CLI,
    )
    return actor_id
