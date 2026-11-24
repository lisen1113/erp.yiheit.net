"""Exercise business partner HTTP routes against a migrated PostgreSQL database."""

from concurrent.futures import ThreadPoolExecutor
from secrets import token_urlsafe
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_authorization import ORIGIN, create_company, create_user, grant
from test_authorization import auth_env as auth_env
from yihe_api.audit.models import AuditLog
from yihe_api.auth.router import session_factory
from yihe_api.main import app
from yihe_api.master_data import router as partner_router

PATH = "/api/v1/business-partners"
BANK = "TESTACCOUNT8799"
CHANGED_BANK = "TESTACCOUNT1843"


@pytest.fixture
def partner_clients(auth_env, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ERP_AUDIT_IDENTIFIER_KEY", token_urlsafe(48))
    with session_factory().begin() as db:
        company = create_company(db)
        foreign = create_company(db)
        headers = {}
        for label, company_id, verified, grants in (
            ("owner", company, False, ("view", "create", "edit", "disable")),
            ("finance", company, True, ("view", "bank_account.view_full")),
            ("unverified", company, False, ("view", "bank_account.view_full")),
            ("other", foreign, False, ("view", "create")),
            ("self", company, False, ()),
            ("department", company, False, ()),
            ("warehouse", company, False, ("view_basic",)),
        ):
            user, _, headers[label] = create_user(db, company_id, verified=verified)
            for permission in grants:
                grant(db, user, f"business_partner.{permission}", "company")
            if label == "self":
                grant(db, user, "business_partner.create", "self")
            if label in {"self", "department"}:
                grant(db, user, "business_partner.view", label)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, headers


def write(client, headers, method, path, payload, key=None):
    return client.request(
        method, path, headers={**headers, "Idempotency-Key": key or str(uuid4())}, json=payload
    )


def test_shared_identity_scope_and_bank_projection(partner_clients):
    client, users = partner_clients
    owner = users["owner"]
    for kind in ("CUSTOMER", "SUPPLIER", "BOTH"):
        payload = {
            "name": "Synthetic partner",
            "code": kind,
            "partner_type": kind,
            "credit_limit": "9999999999999999.99",
            "invoice_bank_account": BANK,
            "unified_social_credit_code": f"T012-{kind}",
        }
        response = write(client, owner, "POST", PATH, payload)
        assert response.status_code == 201, response.text
        if kind == "BOTH":
            both_id = response.json()["resource_id"]

    customers = client.get(PATH, params={"role": "CUSTOMER"}, headers=owner).json()["items"]
    suppliers = client.get(PATH, params={"role": "SUPPLIER"}, headers=owner).json()["items"]
    assert {row["code"] for row in customers} == {"CUSTOMER", "BOTH"}
    assert {row["code"] for row in suppliers} == {"SUPPLIER", "BOTH"}
    assert set(row["id"] for row in customers) & set(row["id"] for row in suppliers) == {both_id}

    detail = f"{PATH}/{both_id}"
    for user in ("owner", "unverified"):
        data = client.get(detail, headers=users[user]).json()
        assert data["invoice_bank_account"] == "****8799"
        assert data["credit_limit"] == "9999999999999999.99" and data["currency_code"] == "CNY"
    assert client.get(detail, headers=users["finance"]).json()["invoice_bank_account"] == "****8799"
    for user, expected in (("owner", "****8799"), ("unverified", "****8799"), ("finance", BANK)):
        response = client.get(detail, headers=users[user], params={"reveal_bank_account": True})
        assert response.json()["invoice_bank_account"] == expected
    warehouse = client.get(detail, headers=users["warehouse"]).json()
    assert "invoice_bank_account" not in warehouse and "credit_limit" not in warehouse
    assert client.get(detail, headers=users["other"]).status_code == 404
    assert client.get(detail, headers=users["self"]).status_code == 404
    own = write(
        client,
        users["self"],
        "POST",
        PATH,
        {
            "name": "Synthetic owned",
            "code": "OWNED",
            "partner_type": "CUSTOMER",
        },
    )
    assert own.status_code == 201
    assert [item["code"] for item in client.get(PATH, headers=users["self"]).json()["items"]] == [
        "OWNED"
    ]
    assert (
        client.get(detail, headers=users["department"]).json()["code"] == "DATA_SCOPE_UNAVAILABLE"
    )
    assert write(client, users["other"], "POST", PATH, payload).status_code == 201


def test_constraints_audit_replay_and_state_changes(partner_clients):
    client, users = partner_clients
    owner = users["owner"]
    payload = {
        "name": "Synthetic",
        "code": "EDGE",
        "partner_type": "CUSTOMER",
        "unified_social_credit_code": "T012-SYNTHETIC",
        "invoice_bank_account": BANK,
    }
    key = str(uuid4())
    first = write(client, owner, "POST", PATH, payload, key)
    assert first.status_code == 201, first.text
    replay = write(client, owner, "POST", PATH, payload, key)
    assert replay.status_code == 201 and replay.json() == first.json()
    assert replay.headers["Idempotency-Replayed"] == "true"
    conflict = write(
        client, owner, "POST", PATH, {**payload, "invoice_bank_account": CHANGED_BANK}, key
    )
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert write(client, owner, "POST", PATH, payload).status_code == 409
    assert write(client, owner, "POST", PATH, {**payload, "code": "SECOND"}).status_code == 409
    for invalid in ({"credit_limit": 1.25}, {"credit_limit": "0.001"}, {"currency_code": "USD"}):
        assert write(client, owner, "POST", PATH, {**payload, **invalid}).status_code == 422

    id = first.json()["resource_id"]
    detail = f"{PATH}/{id}"
    edited = write(
        client,
        owner,
        "PATCH",
        detail,
        {
            "version_no": 1,
            "invoice_bank_account": CHANGED_BANK,
            "partner_type": "BOTH",
        },
    )
    assert edited.status_code == 200, edited.text
    assert (
        write(client, owner, "PATCH", detail, {"version_no": 1, "name": "Stale"}).json()["code"]
        == "VERSION_CONFLICT"
    )
    assert (
        write(
            client, owner, "PATCH", detail, {"version_no": 2, "partner_type": "SUPPLIER"}
        ).status_code
        == 200
    )
    assert write(client, owner, "POST", f"{detail}/disable", {"version_no": 3}).status_code == 200
    assert client.get(PATH, headers=owner).json()["total"] == 0
    assert client.get(PATH, params={"active": False}, headers=owner).json()["items"][0]["id"] == id
    assert write(client, owner, "POST", f"{detail}/enable", {"version_no": 4}).status_code == 200
    assert client.get(PATH, headers=owner).json()["total"] == 1

    with session_factory()() as db:
        logs = list(db.scalars(select(AuditLog).where(AuditLog.entity_id == UUID(id))))
        actions = {row.action for row in logs}
        assert {
            f"business_partner.{action}"
            for action in (
                "created",
                "updated",
                "enabled",
                "disabled",
                "role_added",
                "role_removed",
                "bank_account_changed",
            )
        } <= actions
        for row in logs:
            assert BANK not in repr(
                (row.details, row.before_data, row.after_data)
            ) and CHANGED_BANK not in repr((row.details, row.before_data, row.after_data))


def test_concurrent_edits_do_not_overwrite(partner_clients):
    client, users = partner_clients
    owner = users["owner"]
    response = write(
        client, owner, "POST", PATH, {"name": "Synthetic", "code": "LOCK", "partner_type": "BOTH"}
    )
    assert response.status_code == 201, response.text
    path = f"{PATH}/{response.json()['resource_id']}"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda name: write(
                    client,
                    owner,
                    "PATCH",
                    path,
                    {
                        "version_no": 1,
                        "name": name,
                    },
                ),
                ["First", "Second"],
            )
        )
    assert sorted(result.status_code for result in results) == [200, 409]
    assert client.get(path, headers=owner).json()["version_no"] == 2


def test_audit_failure_rolls_back_the_partner(partner_clients, monkeypatch):
    client, users = partner_clients
    owner = users["owner"]

    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic audit failure")

    monkeypatch.setattr(partner_router, "audit_change", fail)
    response = write(
        client,
        owner,
        "POST",
        PATH,
        {
            "name": "Synthetic rollback",
            "code": "ROLLBACK",
            "partner_type": "BOTH",
        },
    )
    assert response.status_code == 500
    assert client.get(PATH, headers=owner).json()["total"] == 0
