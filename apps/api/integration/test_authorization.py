"""Test live authorization and database boundaries against PostgreSQL."""

from datetime import timedelta
from secrets import token_urlsafe
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from yihe_api.audit.models import AuditLog, SecurityEvent
from yihe_api.auth.models import AuthSession, Employee, User
from yihe_api.auth.router import now_utc, session_factory
from yihe_api.auth.security import PASSWORD_HASHER, access_token, auth_config
from yihe_api.db.models import Company
from yihe_api.errors import install_error_handlers
from yihe_api.main import app
from yihe_api.rbac.bootstrap import bootstrap_admin
from yihe_api.rbac.models import Permission, Role, RolePermission, UserRole
from yihe_api.rbac.policy import (
    BANK_MASKED,
    COST_VIEW,
    ROLE_VIEW,
    USER_VIEW,
    project_fields,
)
from yihe_api.rbac.router import Current
from yihe_api.request_middleware import RequestTraceMiddleware

ORIGIN = "https://erp.example.test"
BASE = "/api/v1"


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("ERP_AUTH_ORIGIN", ORIGIN)
    monkeypatch.setenv("ERP_AUTH_JWT_KEY", token_urlsafe(48))
    monkeypatch.setenv("ERP_AUTH_MFA_KEY", Fernet.generate_key().decode())
    auth_config.cache_clear()
    yield
    auth_config.cache_clear()


def create_user(db, company_id, *, username=None, verified=False):
    actor = uuid4()
    employee = Employee(
        company_id=company_id,
        employee_no=uuid4().hex,
        name="Synthetic",
        created_by=actor,
        updated_by=actor,
    )
    db.add(employee)
    db.flush()
    user = User(
        company_id=company_id,
        employee_id=employee.id,
        username=username or uuid4().hex,
        password_hash=PASSWORD_HASHER.hash(token_urlsafe(24)),
        created_by=actor,
        updated_by=actor,
        mfa_enabled=verified,
    )
    db.add(user)
    db.flush()
    now = now_utc()
    session = AuthSession(
        company_id=company_id,
        user_id=user.id,
        mfa_verified=verified,
        expires_at=now + timedelta(days=1),
    )
    db.add(session)
    db.flush()
    token = access_token(
        auth_config(), user=str(user.id), company=str(company_id), session=str(session.id), now=now
    )
    return user, session.id, {"Authorization": f"Bearer {token}"}


def create_company(db):
    actor = uuid4()
    row = Company(code=uuid4().hex[:20], name="Synthetic", created_by=actor, updated_by=actor)
    db.add(row)
    db.flush()
    return row.id


def grant(db, user, code, scope="self", *, requires_mfa=False, expired=False):
    permission = db.scalar(select(Permission).where(Permission.code == code))
    if permission is None:
        permission = Permission(code=code, module="testing", action="view", description=code)
        db.add(permission)
    role = Role(
        company_id=user.company_id,
        code="TEST_" + uuid4().hex.upper(),
        name="Synthetic",
        data_scope=scope,
        requires_mfa=requires_mfa,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(role)
    db.flush()
    ends = now_utc() - timedelta(minutes=1) if expired else None
    db.add(RolePermission(role_id=role.id, permission_id=permission.id, granted_by=user.id))
    db.add(
        UserRole(
            company_id=user.company_id,
            user_id=user.id,
            role_id=role.id,
            assigned_by=user.id,
            valid_from=now_utc() - timedelta(minutes=2),
            valid_until=ends,
        )
    )
    db.flush()
    return role.id


@pytest.fixture
def identity(auth_env):
    with session_factory().begin() as db:
        company_id = create_company(db)
        user, session_id, headers = create_user(db, company_id)
        user_id = user.id
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, company_id, user_id, session_id, headers


def test_forbidden_then_multiple_roles_and_immediate_revocation(identity):
    client, _, user_id, _, headers = identity
    denied = client.get(BASE + "/users", headers=headers)
    assert denied.status_code == 403
    assert denied.json()["code"] == "FORBIDDEN"
    assert denied.json()["trace_id"] == denied.headers["X-Request-ID"]
    assert client.get(BASE + "/roles", headers=headers).status_code == 403
    assert (
        client.post(
            BASE + "/roles", json={"code": "PROBE", "name": "Probe"}, headers=headers
        ).status_code
        == 403
    )
    with session_factory().begin() as db:
        user = db.get(User, user_id)
        grant(db, user, USER_VIEW)
        other_role = grant(db, user, ROLE_VIEW, "company")
    assert client.get(BASE + "/users", headers=headers).status_code == 200
    assert client.get(BASE + "/roles", headers=headers).status_code == 200
    snapshot = client.get(BASE + "/auth/me/permissions", headers=headers)
    assert set(snapshot.json()["permissions"]) == {ROLE_VIEW, USER_VIEW}
    with session_factory().begin() as db:
        entry = db.scalar(select(UserRole).where(UserRole.role_id == other_role))
        entry.revoked_at = now_utc()
    assert client.get(BASE + "/roles", headers=headers).status_code == 403
    assert client.get(BASE + "/users", headers=headers).status_code == 200


@pytest.mark.parametrize("deactivation", ["role", "user", "employee", "expired"])
def test_old_token_respects_live_status(identity, deactivation):
    client, _, user_id, _, headers = identity
    with session_factory().begin() as db:
        user = db.get(User, user_id)
        role_id = grant(db, user, USER_VIEW, expired=deactivation == "expired")
    expected = 403 if deactivation == "expired" else 200
    assert client.get(BASE + "/users", headers=headers).status_code == expected
    if deactivation == "expired":
        return
    with session_factory().begin() as db:
        user = db.get(User, user_id)
        if deactivation == "role":
            db.get(Role, role_id).is_active = False
        elif deactivation == "user":
            user.status = "disabled"
        else:
            db.get(Employee, user.employee_id).is_active = False
    assert client.get(BASE + "/users", headers=headers).status_code == (
        403 if deactivation == "role" else 401
    )


def test_company_self_and_closed_department(identity):
    client, company_id, user_id, _, headers = identity
    with session_factory().begin() as db:
        user = db.get(User, user_id)
        department = grant(db, user, USER_VIEW, "department")
        local, _, _ = create_user(db, company_id)
        foreign, _, _ = create_user(db, create_company(db))
        local_id, foreign_id = local.id, foreign.id
    response = client.get(BASE + "/users", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "DATA_SCOPE_UNAVAILABLE"
    with session_factory().begin() as db:
        db.get(Role, department).data_scope = "self"
    assert {item["id"] for item in client.get(BASE + "/users", headers=headers).json()} == {
        str(user_id)
    }
    assert client.get(f"{BASE}/users/{local_id}", headers=headers).status_code == 404
    with session_factory().begin() as db:
        db.get(Role, department).data_scope = "company"
    assert client.get(f"{BASE}/users/{local_id}", headers=headers).status_code == 200
    assert client.get(f"{BASE}/users/{foreign_id}", headers=headers).status_code == 404
    with pytest.raises(IntegrityError), session_factory().begin() as db:
        db.add(
            UserRole(
                company_id=company_id, user_id=foreign_id, role_id=department, assigned_by=user_id
            )
        )
        db.flush()


def test_sensitive_role_needs_verified_session_and_fields_are_projected(identity):
    _, _, user_id, session_id, headers = identity
    demo = FastAPI()
    demo.add_middleware(RequestTraceMiddleware)
    install_error_handlers(demo)

    @demo.get("/probe")
    def probe(principal: Current) -> dict[str, object]:
        principal.require(USER_VIEW)
        return project_fields(
            {
                "unit_cost": "1.00",
                "inventory_value": "2.00",
                "sales_cost": "1.00",
                "gross_margin": "2.00",
                "gross_margin_rate": "0.20",
                "bank_account_number": "test-1234",
            },
            principal,
            company_id=principal.user.company_id,
            owner_user_id=principal.user.id,
        )

    with session_factory().begin() as db:
        user = db.get(User, user_id)
        grant(db, user, USER_VIEW, requires_mfa=True)
        grant(db, user, COST_VIEW, requires_mfa=True)
        grant(db, user, BANK_MASKED)
    with TestClient(demo, base_url=ORIGIN) as client:
        denied = client.get("/probe", headers=headers)
        assert denied.status_code == 403 and denied.json()["code"] == "MFA_REQUIRED"
        with session_factory().begin() as db:
            db.get(User, user_id).mfa_enabled = True
        assert client.get("/probe", headers=headers).status_code == 403
        with session_factory().begin() as db:
            db.get(AuthSession, session_id).mfa_verified = True
            roles = db.scalars(select(Role).join(UserRole).where(UserRole.user_id == user_id))
            for role in roles:
                grants = set(
                    db.scalars(
                        select(Permission.code)
                        .join(RolePermission)
                        .where(
                            RolePermission.role_id == role.id,
                        )
                    )
                )
                if COST_VIEW in grants:
                    role.is_active = False
        response = client.get("/probe", headers=headers)
        assert response.status_code == 200
        assert response.json() == {"bank_account_number": "****1234"}


def test_bootstrap_mfa_repeated_bootstrap_and_no_self_escalation(auth_env):
    code = uuid4().hex[:20]
    username = "Admin" + uuid4().hex
    password = token_urlsafe(24)
    args = dict(
        company_code=code,
        company_name="Synthetic",
        employee_no="FIRST",
        employee_name="Operator",
        username=username,
        password=password,
    )
    with session_factory().begin() as db:
        user_id = bootstrap_admin(db, **args)
    with pytest.raises(ValueError, match="already"), session_factory().begin() as db:
        bootstrap_admin(db, **args)
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post(
            BASE + "/auth/login",
            json={"username": username.upper(), "password": password},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "mfa_setup_required"
        assert "access_token" not in response.json()
        # Seed an MFA-verified session for mutation-policy checks independently of OTP clocks.
        with session_factory().begin() as db:
            user = db.get(User, user_id)
            user.mfa_enabled = True
            now = now_utc()
            session = AuthSession(
                company_id=user.company_id,
                user_id=user.id,
                mfa_verified=True,
                expires_at=now + timedelta(days=1),
            )
            db.add(session)
            db.flush()
            signed = access_token(
                auth_config(),
                user=str(user_id),
                company=str(user.company_id),
                session=str(session.id),
                now=now,
            )
            headers = {"Authorization": f"Bearer {signed}"}
            role = db.scalar(
                select(Role).where(Role.company_id == user.company_id, Role.code == "SYSTEM_ADMIN")
            )
            role_id, role_version = role.id, role.version_no
            company_id = user.company_id
            permission = db.scalar(
                select(Permission).where(
                    Permission.code == "finance.payment.approve",
                )
            )
            permission_id = permission.id
            target, _, _ = create_user(db, company_id)
            target_id = target.id
        with pytest.raises(IntegrityError), session_factory().begin() as db:
            create_user(db, company_id, username=username.lower())
        roles = client.get(BASE + "/roles", headers=headers)
        assert roles.status_code == 200 and len(roles.json()) == 8
        warehouse = next(role for role in roles.json() if role["code"] == "WAREHOUSE")
        patch = {
            key: warehouse[key]
            for key in ("name", "description", "data_scope", "is_active", "version_no")
        }
        changed = client.patch(
            f"{BASE}/roles/{warehouse['id']}",
            headers=headers,
            json={**patch, "requires_mfa": True},
        )
        assert changed.status_code == 200 and changed.json()["requires_mfa"] is True
        denied = client.post(
            f"{BASE}/users/{user_id}/roles",
            headers=headers,
            json={"version_no": 1, "role_id": str(role_id)},
        )
        assert denied.status_code == 403
        denied = client.put(
            f"{BASE}/roles/{role_id}/permissions",
            headers=headers,
            json={"version_no": role_version, "permission_ids": [str(permission_id)]},
        )
        assert denied.status_code == 403
        payload = {"code": "CUSTOM_TEST", "name": "Synthetic"}
        created = client.post(BASE + "/roles", headers=headers, json=payload)
        assert created.status_code == 201
        assert client.post(BASE + "/roles", headers=headers, json=payload).status_code == 409
        custom = created.json()["id"]
        denied = client.put(
            f"{BASE}/roles/{custom}/permissions",
            headers=headers,
            json={"version_no": 1, "permission_ids": [str(permission_id)]},
        )
        assert denied.status_code == 403
        with session_factory().begin() as db:
            read_id = db.scalar(select(Permission.id).where(Permission.code == USER_VIEW))
        updated = client.put(
            f"{BASE}/roles/{custom}/permissions",
            headers=headers,
            json={"version_no": 1, "permission_ids": [str(read_id)]},
        )
        assert updated.status_code == 200
        stale = client.put(
            f"{BASE}/roles/{custom}/permissions",
            headers=headers,
            json={"version_no": 1, "permission_ids": []},
        )
        assert stale.status_code == 409
        assign = client.post(
            f"{BASE}/users/{target_id}/roles",
            headers=headers,
            json={"version_no": 1, "role_id": custom},
        )
        assert assign.status_code == 201
        revoked = client.delete(
            f"{BASE}/users/{target_id}/roles/{custom}?version_no=2", headers=headers
        )
        assert revoked.status_code == 204
        disabled = client.patch(
            f"{BASE}/users/{target_id}",
            headers=headers,
            json={"version_no": 3, "status": "disabled"},
        )
        assert disabled.status_code == 200
        with session_factory()() as db:
            persisted = list(db.scalars(select(AuditLog).where(AuditLog.actor_user_id == user_id)))
            assert {"rbac.bootstrap", "user.role.assign", "user.role.revoke", "user.status"} <= {
                row.action for row in persisted
            }
            assert any(
                row.action == "user.status"
                and row.before_data == {"status": "active"}
                and row.after_data == {"status": "disabled"}
                for row in persisted
            )
            assert (
                db.scalar(
                    select(SecurityEvent.id).where(
                        SecurityEvent.user_id == user_id,
                        SecurityEvent.event_type == "SELF_ESCALATION_DENIED",
                    )
                )
                is not None
            )
        with pytest.raises(IntegrityError), session_factory().begin() as db:
            db.add(Permission(code=USER_VIEW, module="system", action="view", description="Dup"))
            db.flush()
