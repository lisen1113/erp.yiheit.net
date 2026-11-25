"""Deterministic identity seed for a disposable development database."""

import ipaddress
import os
import re
from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from yihe_api.auth.models import Employee, User
from yihe_api.auth.security import PASSWORD_HASHER, valid_new_password
from yihe_api.db.models import Company
from yihe_api.db.session import DatabaseSettings
from yihe_api.master_data.customer_models import CustomerProfile
from yihe_api.master_data.models import BusinessPartner
from yihe_api.master_data.seed_fixtures import CUSTOMERS, PARTNERS, SUPPLIERS
from yihe_api.master_data.supplier_models import SupplierProfile
from yihe_api.product_categories.models import ProductCategory
from yihe_api.product_categories.seed import FIXTURE_SCHEMAS, GROUPS
from yihe_api.rbac.bootstrap import seed_roles
from yihe_api.rbac.models import Role, RolePermission, UserRole

SEED_NAMESPACE = UUID("7f399f4b-4cc5-4d89-acc0-29d130b9e8d8")
EPOCH = datetime(2026, 1, 15, tzinfo=UTC)
COMPANY_CODE = "YIHE-DEMO"
COMPANY_NAME = "上海以和测试公司"
PEOPLE = (
    ("E001", "示例系统管理员", "SYSTEM_ADMIN"),
    ("E002", "示例管理层", "MANAGEMENT"),
    ("E003", "示例财务", "FINANCE"),
    ("E004", "示例销售经理", "SALES_MANAGER"),
    ("E005", "示例销售A", "SALES"),
    ("E006", "示例销售B", "SALES"),
    ("E007", "示例采购", "PURCHASE"),
    ("E008", "示例仓库", "WAREHOUSE"),
    ("E009", "示例审计员", "AUDITOR"),
)


def seed_id(entity: str, code: str) -> UUID:
    return uuid5(SEED_NAMESPACE, f"{entity}:{code}")


COMPANY_ID = seed_id("company", COMPANY_CODE)
ACTOR_ID = seed_id("user", "E001")


def check_environment() -> None:
    if os.environ.get("ERP_ENV") not in {"development", "test"}:
        raise ValueError("Seed commands require ERP_ENV=development or test")


def check_database(settings: DatabaseSettings) -> None:
    if not re.fullmatch(r"yihe_erp_seed_[a-z0-9_]{1,35}", settings.database):
        raise ValueError("Seed requires a dedicated yihe_erp_seed_* PostgreSQL database")
    if settings.host in {"postgres", "localhost"}:
        return
    try:
        address = ipaddress.ip_address(settings.host)
    except ValueError:
        raise ValueError("Seed requires a local or test container database endpoint") from None
    if not address.is_loopback and not (
        os.environ.get("ERP_ENV") == "test"
        and str(address) == os.environ.get("ERP_SEED_TEST_DB_HOST")
        and address.is_private
        and not address.is_unspecified
        and not address.is_link_local
    ):
        raise ValueError("Seed requires a local or test container database endpoint")


def inspect_company(db: Session) -> Company | None:
    check_environment()
    settings = DatabaseSettings.from_environment()
    check_database(settings)
    if db.scalar(text("SELECT current_database()")) != settings.database:
        raise ValueError("Seed session is not connected to the configured seed database")
    # Shared with the existing bootstrap to serialize changes to system-role templates.
    db.execute(text("SELECT pg_advisory_xact_lock(61106101)"))
    companies = list(db.scalars(select(Company).limit(2).with_for_update()))
    if not companies:
        return None
    company = companies[0]
    if len(companies) != 1 or (company.id, company.code, company.name, company.is_deleted) != (
        COMPANY_ID,
        COMPANY_CODE,
        COMPANY_NAME,
        False,
    ):
        raise ValueError("Seed database contains non-seed company records")
    return company


def seed_dev(db: Session, password: str) -> dict[str, int]:
    if not all(valid_new_password(password, f"demo-{no.lower()}") for no, _, _ in PEOPLE):
        raise ValueError("Set ERP_SEED_DEFAULT_PASSWORD to a valid 12–128 character password")
    company = inspect_company(db)
    if company is None:
        company = Company(
            id=COMPANY_ID,
            code=COMPANY_CODE,
            name=COMPANY_NAME,
            created_by=ACTOR_ID,
            updated_by=ACTOR_ID,
        )
        db.add(company)
        db.flush()

    for no, name, _ in PEOPLE:
        employee_id = seed_id("employee", no)
        user_id = seed_id("user", no)
        username = f"demo-{no.lower()}"
        email = f"{username}@example.invalid"
        employee = db.get(Employee, employee_id)
        if employee is None:
            db.add(
                Employee(
                    id=employee_id,
                    company_id=company.id,
                    employee_no=no,
                    name=name,
                    email=email,
                    created_by=ACTOR_ID,
                    updated_by=ACTOR_ID,
                )
            )
            db.flush()
        elif (employee.company_id, employee.employee_no, employee.email) != (company.id, no, email):
            raise ValueError("Fixed employee ID is not owned by seed")
        user = db.get(User, user_id)
        if user is None:
            db.add(
                User(
                    id=user_id,
                    company_id=company.id,
                    employee_id=employee_id,
                    username=username,
                    password_hash=PASSWORD_HASHER.hash(password),
                    password_changed_at=EPOCH,
                    created_by=ACTOR_ID,
                    updated_by=ACTOR_ID,
                )
            )
        elif (user.company_id, user.employee_id, user.username) != (
            company.id,
            employee_id,
            username,
        ):
            raise ValueError("Fixed user ID is not owned by seed")
    db.flush()

    roles = seed_roles(db, company.id, ACTOR_ID)
    for no, _, role_code in PEOPLE:
        user = db.get_one(User, seed_id("user", no))
        role = roles[role_code]
        if role_code in {"SYSTEM_ADMIN", "MANAGEMENT", "FINANCE"}:
            if not role.requires_mfa:
                raise ValueError("Required system MFA configuration differs from the seed")
            if not user.mfa_required:
                user.mfa_required = True
        id = seed_id("user_role", no)
        entry = db.get(UserRole, id)
        if entry is None:
            db.add(
                UserRole(
                    id=id,
                    company_id=company.id,
                    user_id=user.id,
                    role_id=role.id,
                    assigned_by=ACTOR_ID,
                    assigned_at=EPOCH,
                    valid_from=EPOCH,
                )
            )
        elif (entry.company_id, entry.user_id, entry.role_id, entry.revoked_at) != (
            company.id,
            user.id,
            role.id,
            None,
        ):
            raise ValueError("Seed role assignment was modified; use a clean database")
    for code, (name, kind, active, email) in PARTNERS.items():
        partner_id = seed_id("business_partner", code)
        row = db.get(BusinessPartner, partner_id)
        if row is None:
            db.add(
                BusinessPartner(
                    id=partner_id,
                    company_id=company.id,
                    code=code,
                    name=name,
                    partner_type=kind,
                    is_active=active,
                    email=email,
                    created_by=ACTOR_ID,
                    updated_by=ACTOR_ID,
                )
            )
        elif (row.company_id, row.code, row.created_by) != (company.id, code, ACTOR_ID):
            raise ValueError("Fixed partner ID is not owned by seed")
    db.flush()
    for party_code, (customer_no, kind, active) in CUSTOMERS.items():
        profile_id = seed_id("customer_profile", party_code)
        partner_id = seed_id("business_partner", party_code)
        profile = db.get(CustomerProfile, profile_id)
        if profile is None:
            db.add(
                CustomerProfile(
                    id=profile_id,
                    company_id=company.id,
                    business_partner_id=partner_id,
                    customer_no=customer_no,
                    customer_type=kind,
                    salesperson_id=seed_id("employee", "E005"),
                    is_active=active,
                    is_synthetic=True,
                    created_by=ACTOR_ID,
                    updated_by=ACTOR_ID,
                )
            )
        elif (
            profile.company_id != company.id
            or profile.business_partner_id != partner_id
            or not profile.is_synthetic
            or profile.created_by != ACTOR_ID
        ):
            raise ValueError("Fixed customer profile ID is not owned by seed")
    db.flush()
    for party_code, (supplier_no, kind) in SUPPLIERS.items():
        profile_id = seed_id("supplier_profile", party_code)
        partner_id = seed_id("business_partner", party_code)
        supplier = db.get(SupplierProfile, profile_id)
        if supplier is None:
            db.add(
                SupplierProfile(
                    id=profile_id,
                    company_id=company.id,
                    business_partner_id=partner_id,
                    supplier_no=supplier_no,
                    supplier_type=kind,
                    buyer_id=seed_id("employee", "E007"),
                    is_synthetic=True,
                    created_by=ACTOR_ID,
                    updated_by=ACTOR_ID,
                )
            )
        elif (
            supplier.company_id != company.id
            or supplier.business_partner_id != partner_id
            or not supplier.is_synthetic
            or supplier.created_by != ACTOR_ID
        ):
            raise ValueError("Fixed supplier profile ID is not owned by seed")
    db.flush()
    category_count = seed_categories(db, company.id)
    return {
        "supplier_profile": len(SUPPLIERS),
        "product_category": category_count,
        "customer_profile": len(CUSTOMERS),
        "business_partner": len(PARTNERS),
        "company": 1,
        "employee": len(PEOPLE),
        "app_user": len(PEOPLE),
        "role": len(roles),
        "user_role": len(PEOPLE),
    }


def seed_categories(db: Session, company_id: UUID) -> int:
    count = 0
    for group, (name, child_names) in GROUPS.items():
        root_id = seed_id("product_category", group)
        for index, category_name in enumerate([name, *child_names]):
            code = group if index == 0 else f"{group}-{index}"
            id = seed_id("product_category", code)
            category = db.get(ProductCategory, id)
            if category is None:
                db.add(
                    ProductCategory(
                        id=id,
                        company_id=company_id,
                        code=code,
                        name=category_name,
                        normalized_name=category_name.casefold(),
                        parent_id=root_id if index else None,
                        level=int(index > 0),
                        path=f"/{root_id}/{id}" if index else f"/{id}",
                        sort_order=index,
                        attribute_schema=FIXTURE_SCHEMAS.get((group, index), {}),
                        is_synthetic=True,
                        created_by=ACTOR_ID,
                        updated_by=ACTOR_ID,
                    )
                )
                db.flush()
            elif (
                category.company_id != company_id
                or not category.is_synthetic
                or category.created_by != ACTOR_ID
            ):
                raise ValueError("Fixed category ID is not owned by seed")
            count += 1
    return count


def seed_reset(db: Session) -> bool:
    company = inspect_company(db)
    if company is None:
        return False
    people_ids = {seed_id("user", no) for no, _, _ in PEOPLE}
    employee_ids = {seed_id("employee", no) for no, _, _ in PEOPLE}
    assignments = {seed_id("user_role", no) for no, _, _ in PEOPLE}
    if (
        set(db.scalars(select(User.id).where(User.company_id == company.id))) != people_ids
        or set(db.scalars(select(Employee.id).where(Employee.company_id == company.id)))
        != employee_ids
        or set(db.scalars(select(UserRole.id).where(UserRole.company_id == company.id)))
        != assignments
    ):
        raise ValueError("Reset requires the unmodified seed identities and assignments")
    roles = list(db.scalars(select(Role).where(Role.company_id == company.id)))
    if any(not row.is_system or row.created_by != ACTOR_ID for row in roles):
        raise ValueError("Reset cannot remove non-seed roles")
    categories = list(
        db.scalars(
            select(ProductCategory)
            .where(ProductCategory.company_id == company.id)
            .order_by(ProductCategory.level.desc())
        )
    )
    category_codes = {
        group if i == 0 else f"{group}-{i}"
        for group, (_, children) in GROUPS.items()
        for i in range(len(children) + 1)
    }
    for category in categories:
        if (
            category.code not in category_codes
            or category.id != seed_id("product_category", category.code)
            or not category.is_synthetic
            or category.created_by != ACTOR_ID
            or category.version_no != 1
        ):
            raise ValueError("Reset cannot remove non-seed or modified categories")
        db.delete(category)
        db.flush()
    profiles = db.scalars(select(CustomerProfile).where(CustomerProfile.company_id == company.id))
    seed_profile_ids = {seed_id("customer_profile", code) for code in CUSTOMERS}
    for profile in profiles:
        if (
            profile.id not in seed_profile_ids
            or not profile.is_synthetic
            or profile.created_by != ACTOR_ID
            or profile.version_no != 1
        ):
            raise ValueError("Reset cannot remove non-seed or modified customer profiles")
        db.delete(profile)
    db.flush()
    supplier_ids = {seed_id("supplier_profile", code) for code in SUPPLIERS}
    for supplier in db.scalars(
        select(SupplierProfile).where(SupplierProfile.company_id == company.id)
    ):
        if (
            supplier.id not in supplier_ids
            or not supplier.is_synthetic
            or supplier.created_by != ACTOR_ID
            or supplier.version_no != 1
        ):
            raise ValueError("Reset cannot remove non-seed or modified supplier profiles")
        db.delete(supplier)
    db.flush()
    db.execute(delete(UserRole).where(UserRole.company_id == company.id))
    db.execute(delete(RolePermission).where(RolePermission.role_id.in_([r.id for r in roles])))
    db.execute(delete(Role).where(Role.company_id == company.id))
    db.execute(delete(User).where(User.id.in_(people_ids), User.company_id == company.id))
    db.execute(
        delete(Employee).where(Employee.id.in_(employee_ids), Employee.company_id == company.id)
    )
    records = db.scalars(select(BusinessPartner).where(BusinessPartner.company_id == company.id))
    for record in records:
        if (
            record.code not in PARTNERS
            or record.id != seed_id("business_partner", record.code)
            or record.created_by != ACTOR_ID
            or record.version_no != 1
        ):
            raise ValueError("Reset cannot remove non-seed or modified business partners")
        db.delete(record)
    db.flush()
    db.delete(company)
    db.flush()
    return True
