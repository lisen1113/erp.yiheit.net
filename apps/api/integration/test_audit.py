"""Verify persistence, tenant isolation, and independent rejection records."""

from secrets import token_urlsafe
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_authorization import BASE, ORIGIN, create_company, grant
from test_authorization import auth_env as auth_env
from test_authorization import identity as identity_fixture  # noqa: F401
from yihe_api.audit.models import AuditLog, SecurityEvent
from yihe_api.audit.service import Category, Source, audit_change
from yihe_api.auth.models import AuthSession, Employee, User
from yihe_api.auth.router import session_factory
from yihe_api.db.models import Company
from yihe_api.rbac.models import Permission, Role, RolePermission
from yihe_api.rbac.policy import ROLE_MANAGE, USER_VIEW


def test_system_reference_and_company_constraint(identity_fixture):  # noqa: F811
    _, company, _, _, _ = identity_fixture
    with session_factory().begin() as db:
        row = audit_change(
            db,
            company_id=company,
            action="sample.created",
            category=Category.SYSTEM_CONFIGURATION,
            source=Source.SYSTEM,
            entity_type="document",
            entity_ref="TEST-001",
        )
        assert row.actor_user_id is None and row.entity_id is None
        assert row.entity_ref == "TEST-001"
    with pytest.raises(IntegrityError), session_factory().begin() as db:
        db.execute(text("INSERT INTO audit_log (result) VALUES ('SUCCESS')"))


@pytest.mark.parametrize("table", ["audit_log", "security_event"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE", "TRUNCATE"])
def test_non_owner_cannot_mutate_even_if_dml_was_misgranted(identity_fixture, table, operation):  # noqa: F811
    statements = {
        "UPDATE": f"UPDATE {table} SET result = result WHERE false",
        "DELETE": f"DELETE FROM {table} WHERE false",
        "TRUNCATE": f"TRUNCATE {table}",
    }
    with session_factory().begin() as db:
        db.execute(text("CREATE ROLE t007_audit_probe NOLOGIN"))
        db.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON {table} TO t007_audit_probe")
        )
        with pytest.raises(DBAPIError), db.begin_nested():
            db.execute(text("SET LOCAL ROLE t007_audit_probe"))
            db.execute(text(statements[operation]))
        db.execute(text(f"REVOKE ALL ON {table} FROM t007_audit_probe"))
        db.execute(text("DROP ROLE t007_audit_probe"))


def test_unknown_login_csrf_mapping_and_no_fake_identities(identity_fixture, monkeypatch):  # noqa: F811
    client, company, _, _, _ = identity_fixture
    monkeypatch.setenv("ERP_AUDIT_HOST_COMPANY_ID", str(company))
    monkeypatch.setenv("ERP_AUDIT_IDENTIFIER_KEY", token_urlsafe(40))
    unknown = uuid4().hex
    with session_factory()() as db:
        counts = tuple(
            db.scalar(select(func.count()).select_from(table))
            for table in (Company, Employee, User)
        )
    headers = {"Origin": ORIGIN, "X-Request-ID": "audit-unknown"}
    result = client.post(
        BASE + "/auth/login",
        headers=headers,
        json={"username": unknown, "password": token_urlsafe(24)},
    )
    assert result.status_code == 401
    headers["X-Request-ID"] = "audit-unknown-repeat"
    result = client.post(
        BASE + "/auth/login",
        headers=headers,
        json={"username": unknown.upper(), "password": token_urlsafe(24)},
    )
    assert result.status_code == 401
    for request_id, host in (
        ("audit-host-hint", "erp.yiheit.net"),
        ("audit-invalid-host", "bad.example.test"),
    ):
        response = client.post(
            BASE + "/auth/refresh",
            headers={
                "Host": host,
                "Origin": "https://bad.example.test",
                "X-Request-ID": request_id,
            },
        )
        assert response.status_code == 403
    with session_factory()() as db:
        rows = {
            r.trace_id: r
            for r in db.scalars(
                select(SecurityEvent).where(
                    SecurityEvent.trace_id.in_(
                        {
                            "audit-unknown",
                            "audit-unknown-repeat",
                            "audit-host-hint",
                            "audit-invalid-host",
                        }
                    ),
                )
            )
        }
        a, b = rows["audit-unknown"], rows["audit-unknown-repeat"]
        assert a.company_resolution == "UNKNOWN" and a.company_id is None and a.user_id is None
        assert a.identifier_hash == b.identifier_hash and len(a.identifier_hash) == 64
        assert unknown not in str(a.details)
        hint = rows["audit-host-hint"]
        assert hint.company_resolution == "HOST_MAPPING" and hint.company_id == company
        assert hint.user_id is None
        bad = rows["audit-invalid-host"]
        assert bad.company_resolution == "UNKNOWN" and bad.host == "<untrusted>"
        assert counts == tuple(
            db.scalar(select(func.count()).select_from(table))
            for table in (Company, Employee, User)
        )


def test_scope_metadata_filters_and_authenticated_rejection(identity_fixture):  # noqa: F811
    client, company, user, session, headers = identity_fixture
    denied = client.get(
        BASE + "/audit-logs",
        headers={
            **headers,
            "X-Request-ID": "audit-denied-authenticated",
        },
    )
    assert denied.status_code == 403
    assert client.get(BASE + "/security-events", headers=headers).status_code == 403
    with session_factory().begin() as db:
        own = db.get(User, user)
        own.mfa_enabled = True
        db.get(AuthSession, session).mfa_verified = True
        grant(db, own, "system.audit.view", "company")
        other = create_company(db)
        for key in (company, other):
            audit_change(
                db,
                company_id=key,
                action="sample.created",
                category=Category.SYSTEM_CONFIGURATION,
                source=Source.SYSTEM,
                entity_type="document",
                entity_ref="TEST-SEARCH",
                metadata={"bank_last_four": "1234"},
            )
        event = db.scalar(
            select(SecurityEvent).where(
                SecurityEvent.trace_id == "audit-denied-authenticated",
            )
        )
        assert event.company_resolution == "AUTHENTICATED_USER"
        assert event.company_id == company and event.user_id == user
    response = client.get(
        BASE + "/audit-logs",
        headers=headers,
        params={
            "action": "sample.created",
            "entity_type": "document",
            "entity_ref": "TEST-SEARCH",
        },
    )
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1 and items[0]["company_id"] == str(company)
    assert "metadata" not in items[0]
    assert (
        client.get(
            BASE + "/audit-logs", headers=headers, params={"trace_id": items[0]["trace_id"]}
        ).json()
        == items
    )
    with session_factory().begin() as db:
        own = db.get(User, user)
        grant(db, own, "system.security_event.view", "company")
    response = client.get(
        BASE + "/security-events",
        headers=headers,
        params={"trace_id": "audit-denied-authenticated"},
    )
    assert response.status_code == 200 and len(response.json()) == 1


def test_role_grant_rollback_when_audit_insert_fails(identity_fixture):  # noqa: F811
    client, company, user, session, headers = identity_fixture
    with session_factory().begin() as db:
        owner = db.get(User, user)
        owner.mfa_enabled = True
        db.get(AuthSession, session).mfa_verified = True
        grant(db, owner, ROLE_MANAGE, "company")
        grant(db, owner, USER_VIEW, "company")
        role = Role(
            company_id=company,
            code="AUDIT_TEST",
            name="Audit test",
            created_by=user,
            updated_by=user,
        )
        db.add(role)
        db.flush()
        role_id = role.id
        permission_id = db.scalar(select(Permission.id).where(Permission.code == USER_VIEW))
        db.execute(
            text("""
            CREATE FUNCTION t007_fail_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.trace_id = 'audit-force-error' THEN RAISE EXCEPTION 'test failure'; END IF;
                RETURN NEW;
            END $$;
        """)
        )
        db.execute(
            text("""
            CREATE TRIGGER t007_force_audit_error BEFORE INSERT ON audit_log
            FOR EACH ROW EXECUTE FUNCTION t007_fail_audit()
        """)
        )
    try:
        params = {"version_no": 1, "permission_ids": [str(permission_id)]}
        path = f"{BASE}/roles/{role_id}/permissions"
        response = client.put(
            path, headers={**headers, "X-Request-ID": "audit-force-error"}, json=params
        )
        assert response.status_code == 500
        assert response.json()["code"] == "AUDIT_WRITE_FAILED"
        with session_factory()() as db:
            assert db.get(Role, role_id).version_no == 1
            assert db.get(RolePermission, (role_id, permission_id)) is None
        response = client.put(
            path, headers={**headers, "X-Request-ID": "audit-grant-ok"}, json=params
        )
        assert response.status_code == 200
        with session_factory()() as db:
            assert db.get(RolePermission, (role_id, permission_id)) is not None
            row = db.scalar(select(AuditLog).where(AuditLog.trace_id == "audit-grant-ok"))
            assert row.details == {"added": [USER_VIEW], "removed": []}
    finally:
        with session_factory().begin() as db:
            db.execute(text("DROP TRIGGER t007_force_audit_error ON audit_log"))
            db.execute(text("DROP FUNCTION t007_fail_audit()"))


def test_login_reuse_and_sod_rejection_events(identity_fixture):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from yihe_api.auth.router import COOKIE
    from yihe_api.auth.security import PASSWORD_HASHER
    from yihe_api.errors import install_error_handlers
    from yihe_api.rbac.policy import assert_separation_of_duties
    from yihe_api.rbac.router import Current
    from yihe_api.request_middleware import RequestTraceMiddleware

    client, company, user, _, headers = identity_fixture
    password = token_urlsafe(24)
    with session_factory().begin() as db:
        account = db.get(User, user)
        account.password_hash = PASSWORD_HASHER.hash(password)
        username = account.username
    logged = client.post(
        BASE + "/auth/login",
        json={"username": username, "password": password},
        headers={"Origin": ORIGIN, "X-Request-ID": "audit-login-ok"},
    )
    assert logged.status_code == 200
    credential = logged.cookies.get(COOKIE)
    browser = {"Origin": ORIGIN, "Cookie": f"{COOKIE}={credential}"}
    assert client.post(BASE + "/auth/refresh", headers=browser).status_code == 200
    reused = client.post(
        BASE + "/auth/refresh",
        headers={**browser, "X-Request-ID": "audit-reuse"},
    )
    assert reused.status_code == 401

    probe = FastAPI()
    probe.add_middleware(RequestTraceMiddleware)
    install_error_handlers(probe)

    @probe.post("/approval-probe")
    def approve(principal: Current) -> None:
        maker = principal.user.id
        assert_separation_of_duties(maker, maker, "finance.payment.approve")

    with TestClient(probe, base_url=ORIGIN) as demo:
        refused = demo.post("/approval-probe", headers={**headers, "X-Request-ID": "audit-sod"})
        assert refused.status_code == 403

    with session_factory()() as db:
        row = db.scalar(select(AuditLog).where(AuditLog.trace_id == "audit-login-ok"))
        assert (
            row.action == "auth.login" and row.actor_user_id == user and row.company_id == company
        )
        audit = dict(
            db.execute(
                select(SecurityEvent.trace_id, SecurityEvent.event_type).where(
                    SecurityEvent.trace_id.in_(["audit-reuse", "audit-sod"]),
                )
            ).all()
        )
        assert audit == {"audit-reuse": "AUTH_REFRESH_REUSED", "audit-sod": "SEPARATION_OF_DUTIES"}
        if credential in str(row.details) or password in str(row.details):
            pytest.fail("Authentication secret appeared in audit", pytrace=False)

    with session_factory().begin() as db:
        account = db.get(User, user)
        account.mfa_enabled = True
        for auth_session in db.scalars(
            select(AuthSession).where(
                AuthSession.user_id == user,
                AuthSession.revoked_at.is_(None),
            )
        ):
            auth_session.mfa_verified = True
        grant(db, account, "system.audit.view", "company")
    history = client.get(
        BASE + "/audit-logs",
        headers=headers,
        params={
            "actor_user_id": str(user),
            "action": "auth.login",
            "entity_type": "app_user",
            "entity_id": str(user),
            "since": "2000-01-01T00:00:00Z",
        },
    )
    assert history.status_code == 200 and len(history.json()) == 1
    assert history.json()[0]["trace_id"] == "audit-login-ok"
