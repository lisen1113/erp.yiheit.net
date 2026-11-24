"""Run on a migrated, dedicated PostgreSQL database."""

import json
import os
import smtplib
from secrets import token_urlsafe

import boto3
import pytest
from sqlalchemy import select
from yihe_api import cli
from yihe_api.audit.models import AuditLog
from yihe_api.audit.service import Category, Source, audit_change
from yihe_api.auth.models import Employee, User
from yihe_api.auth.router import session_factory
from yihe_api.db.models import Company
from yihe_api.dev_seed import COMPANY_ID, EPOCH, PEOPLE, seed_id
from yihe_api.master_data.customer_models import CustomerProfile
from yihe_api.master_data.models import BusinessPartner
from yihe_api.rbac.models import Permission, Role, RolePermission, UserRole
from yihe_api.rbac.policy import (
    CUSTOMER_GRANTS,
    PARTNER_GRANTS,
    PERMISSIONS,
    SOD_ACTIONS,
    SYSTEM_ROLES,
)


def snapshot():
    with session_factory()() as db:
        records = {
            model.__tablename__: tuple(db.scalars(select(model.id).order_by(model.id)))
            for model in (
                Company,
                Employee,
                User,
                Role,
                Permission,
                UserRole,
                BusinessPartner,
                CustomerProfile,
            )
        }
        records["role_permission"] = tuple(
            db.execute(
                select(RolePermission.role_id, RolePermission.permission_id).order_by(
                    RolePermission.role_id, RolePermission.permission_id
                ),
            )
        )
        return records


def test_dev_seed_repeat_reset_repeat_and_remote_isolation(monkeypatch, capsys):
    monkeypatch.setenv("ERP_ENV", "test")
    monkeypatch.setenv("ERP_SEED_TEST_DB_HOST", os.environ["POSTGRES_HOST"])
    monkeypatch.setenv("ERP_SEED_DEFAULT_PASSWORD", token_urlsafe(24))
    for key in (
        "ERP_STORAGE_ACCESS_KEY",
        "ERP_STORAGE_SECRET_KEY",
        "ERP_AUTH_JWT_KEY",
        "ERP_AUTH_MFA_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("Seed attempted SMTP or cloud object storage")

    monkeypatch.setattr(smtplib, "SMTP", forbidden)
    monkeypatch.setattr(smtplib, "SMTP_SSL", forbidden)
    monkeypatch.setattr(boto3, "client", forbidden)
    assert cli.run_seed("seed-dev") == 0
    first_result = capsys.readouterr().out
    first = snapshot()
    assert cli.run_seed("seed-dev") == 0
    assert capsys.readouterr().out == first_result
    assert snapshot() == first
    counts = {table: len(rows) for table, rows in first.items()}
    assert counts | {"permission": 0, "role_permission": 0} == {
        "company": 1,
        "business_partner": 14,
        "customer_profile": 10,
        "employee": 9,
        "app_user": 9,
        "role": 8,
        "user_role": 9,
        "permission": 0,
        "role_permission": 0,
    }
    assert counts["permission"] == len(PERMISSIONS)
    assert counts["role_permission"] == sum(
        len(role[3] | PARTNER_GRANTS.get(code, set()) | CUSTOMER_GRANTS.get(code, set()))
        for code, role in SYSTEM_ROLES.items()
    )
    with session_factory()() as db:
        profiles = list(db.scalars(select(CustomerProfile)))
        assert len(profiles) == 10 and all(row.is_synthetic for row in profiles)
        assert {p.customer_type for p in profiles} >= {"standard", "credit", "prepaid", "dual"}
        assert not next(p for p in profiles if p.customer_no == "C09").is_active
        assert next(p for p in profiles if p.customer_no == "C10").business_partner_id == (
            seed_id("business_partner", "PARTY-DUAL")
        )
        partners = list(db.scalars(select(BusinessPartner)))
        assert sum(p.partner_type in {"CUSTOMER", "BOTH"} for p in partners) == 10
        assert sum(p.partner_type in {"SUPPLIER", "BOTH"} for p in partners) == 5
        assert [p.id for p in partners if p.partner_type == "BOTH"] == [
            seed_id("business_partner", "PARTY-DUAL")
        ]
        people = list(
            db.execute(
                select(User, Employee, Role, UserRole)
                .join(Employee, Employee.id == User.employee_id)
                .join(UserRole, UserRole.user_id == User.id)
                .join(Role, Role.id == UserRole.role_id)
            )
        )
        assert len(people) == 9
        assert len({employee.id for _, employee, _, _ in people}) == 9
        for user, employee, role, assignment in people:
            code = employee.employee_no
            assert user.id == seed_id("user", code)
            assert employee.id == seed_id("employee", code)
            assert assignment.id == seed_id("user_role", code)
            assert assignment.valid_from == EPOCH and user.password_changed_at == EPOCH
            assert employee.email.endswith("@example.invalid")
            assert role.code == dict((no, role) for no, _, role in PEOPLE)[code]
            if role.code in {"SYSTEM_ADMIN", "FINANCE", "MANAGEMENT"}:
                assert user.mfa_required and role.requires_mfa
            if role.code == "SYSTEM_ADMIN":
                grants = set(
                    db.scalars(
                        select(Permission.code)
                        .join(RolePermission)
                        .where(RolePermission.role_id == role.id)
                    )
                )
                assert not grants & SOD_ACTIONS
    print("Seed run 1 and 2 identical:", json.dumps(counts, sort_keys=True))
    assert cli.run_seed("seed-reset") == 0
    empty = snapshot()
    assert not empty["company"] and not empty["employee"] and not empty["app_user"]
    assert empty["permission"] == first["permission"]
    assert cli.run_seed("seed-dev") == 0
    after_reset = snapshot()
    for key in ("company", "employee", "app_user", "user_role"):
        assert after_reset[key] == first[key]
    assert cli.run_seed("seed-reset") == 0
    assert cli.run_seed("seed-reset") == 0


def test_reset_rolls_back_when_immutable_audit_references_exist(monkeypatch):
    monkeypatch.setenv("ERP_ENV", "test")
    monkeypatch.setenv("ERP_SEED_TEST_DB_HOST", os.environ["POSTGRES_HOST"])
    monkeypatch.setenv("ERP_SEED_DEFAULT_PASSWORD", token_urlsafe(24))
    assert cli.run_seed("seed-dev") == 0
    before = snapshot()
    with session_factory().begin() as db:
        event = audit_change(
            db,
            company_id=COMPANY_ID,
            category=Category.SYSTEM_CONFIGURATION,
            action="seed.retention.test",
            entity_type="company",
            entity_id=COMPANY_ID,
            source=Source.SYSTEM,
        )
        event_id = event.id
    assert cli.run_seed("seed-reset") == 1
    assert snapshot() == before
    with session_factory()() as db:
        assert db.get(AuditLog, event_id) is not None
