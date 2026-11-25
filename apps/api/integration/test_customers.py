"""Exercise customer operations against PostgreSQL and the production API router."""

from concurrent.futures import ThreadPoolExecutor
from secrets import token_urlsafe
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_authorization import ORIGIN, create_company, create_user, grant
from test_authorization import auth_env as auth_env
from yihe_api.audit.models import AuditLog
from yihe_api.auth.models import Employee
from yihe_api.auth.router import session_factory
from yihe_api.errors import ApiError, ErrorCode
from yihe_api.main import app
from yihe_api.master_data.customer_service import assert_customer_available_for_new_transaction
from yihe_api.master_data.models import BusinessPartner

PATH = "/api/v1/customers"
PARTNERS = "/api/v1/business-partners"


@pytest.fixture
def users(auth_env, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ERP_AUDIT_IDENTIFIER_KEY", token_urlsafe(48))
    with session_factory().begin() as db:
        companies = [create_company(db), create_company(db)]
        identities = {}
        for label, company, verified, permissions in (
            (
                "admin",
                companies[0],
                False,
                [
                    "customer.create",
                    "customer.view",
                    "customer.edit",
                    "customer.disable",
                    "business_partner.create",
                    "business_partner.edit",
                    "business_partner.view",
                ],
            ),
            (
                "finance",
                companies[0],
                True,
                ["customer.view", "business_partner.bank_account.view_full"],
            ),
            (
                "unverified",
                companies[0],
                False,
                ["customer.view", "business_partner.bank_account.view_full"],
            ),
            ("foreign", companies[1], False, ["customer.view"]),
            ("inactive", companies[0], False, []),
        ):
            user, _, headers = create_user(db, company, verified=verified)
            if label == "inactive":
                db.get_one(Employee, user.employee_id).is_active = False
            for permission in permissions:
                grant(db, user, permission, "company")
            identities[label] = (str(user.employee_id), headers)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, identities, companies[0]


def write(client, headers, method, path, payload, key=None):
    return client.request(
        method, path, headers={**headers, "Idempotency-Key": key or str(uuid4())}, json=payload
    )


def payload(employee, code="C-001", **kwargs):
    return {
        "customer_no": code,
        "salesperson_id": employee,
        "partner": {"code": f"P-{code}", "name": "Synthetic partner", **kwargs},
    }


def create(client, headers, data):
    result = write(client, headers, "POST", PATH, data)
    assert result.status_code == 201, result.text
    return result.json()["resource_id"]


def test_compound_create_replay_scope_and_bank(users):
    client, identities, company = users
    salesperson, admin = identities["admin"]
    data = payload(
        salesperson,
        credit_limit="9999999999999999.99",
        payment_terms_days=45,
        invoice_bank_account="SYNTHETIC8765",
    )
    key = str(uuid4())
    first = write(client, admin, "POST", PATH, data, key)
    assert first.status_code == 201, first.text
    replay = write(client, admin, "POST", PATH, data, key)
    assert replay.status_code == 201 and replay.json() == first.json()
    assert replay.headers["Idempotency-Replayed"] == "true"
    changed = write(client, admin, "POST", PATH, {**data, "customer_no": "CHANGED"}, key)
    assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_CONFLICT"
    id = first.json()["resource_id"]
    url = f"{PATH}/{id}"
    row = client.get(url, headers=admin).json()
    assert row["partner"]["partner_type"] == "CUSTOMER"
    assert row["partner"]["id"] == row["business_partner_id"]
    assert row["partner"]["credit_limit"] == "9999999999999999.99"
    assert row["partner"]["currency_code"] == "CNY"
    assert row["partner"]["payment_terms_days"] == 45
    assert row["partner"]["invoice_bank_account"] == "****8765"
    for label, expected in (("finance", "SYNTHETIC8765"), ("unverified", "****8765")):
        res = client.get(url, params={"reveal_bank_account": True}, headers=identities[label][1])
        assert res.json()["partner"]["invoice_bank_account"] == expected
    assert client.get(PATH, headers=identities["foreign"][1]).json()["total"] == 0
    assert client.get(url, headers=identities["foreign"][1]).status_code == 404
    with session_factory().begin() as db:
        assert assert_customer_available_for_new_transaction(db, company, UUID(id)).id == UUID(id)


def test_existing_supplier_and_customer_lifecycle(users):
    client, identities, company = users
    salesperson, admin = identities["admin"]
    supplier = write(
        client,
        admin,
        "POST",
        PARTNERS,
        {
            "code": "SUP-1",
            "name": "Synthetic supplier",
            "partner_type": "SUPPLIER",
        },
    )
    assert supplier.status_code == 201
    partner_id = supplier.json()["resource_id"]
    id = create(
        client,
        admin,
        {
            "customer_no": "CUS-1",
            "salesperson_id": salesperson,
            "business_partner_id": partner_id,
        },
    )
    url = f"{PATH}/{id}"
    row = client.get(url, headers=admin).json()
    assert row["business_partner_id"] == partner_id and row["partner"]["partner_type"] == "BOTH"
    updated = write(
        client,
        admin,
        "PATCH",
        url,
        {
            "version_no": 1,
            "salesperson_id": identities["finance"][0],
            "partner": {
                "version_no": row["partner"]["version_no"],
                "credit_limit": "1200.25",
                "payment_terms_days": 30,
                "invoice_bank_account": "SYNTHETIC7890",
            },
        },
    )
    assert updated.status_code == 200, updated.text
    stale = write(client, admin, "PATCH", url, {"version_no": 1, "customer_no": "STALE"})
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"
    assert write(client, admin, "POST", f"{url}/disable", {"version_no": 2}).status_code == 422
    reason = "customer_request"
    key = str(uuid4())
    for _ in range(2):
        stopped = write(
            client, admin, "POST", f"{url}/disable", {"version_no": 2, "reason": reason}, key
        )
        assert stopped.status_code == 200
    assert stopped.headers["Idempotency-Replayed"] == "true"
    assert client.get(PATH, headers=admin).json()["total"] == 0
    assert client.get(PATH, params={"active": False}, headers=admin).json()["total"] == 1
    with session_factory().begin() as db:
        with pytest.raises(ApiError) as error:
            assert_customer_available_for_new_transaction(db, company, UUID(id))
        assert error.value.code == ErrorCode.CUSTOMER_UNAVAILABLE
        partner = db.get_one(BusinessPartner, UUID(partner_id))
        assert partner.partner_type == "BOTH" and partner.is_active
        logs = db.scalars(select(AuditLog).where(AuditLog.entity_id == UUID(id))).all()
        assert next(log for log in logs if log.action == "customer.disabled").reason == reason
        assert "SYNTHETIC7890" not in repr([log.details for log in logs])
        credit = next(log for log in logs if log.action == "customer.credit_limit_changed")
        assert credit.details == {"previous_credit": "0.00", "current_credit": "1200.25"}
        invoice = next(log for log in logs if log.action == "customer.invoice_information_changed")
        assert invoice.details == {"bank_account_changed": True}
        assert {
            "customer.salesperson_changed",
            "customer.credit_limit_changed",
            "customer.payment_terms_changed",
            "customer.invoice_information_changed",
            "customer.disabled",
        } <= {log.action for log in logs}
    enable = write(client, admin, "POST", f"{url}/enable", {"version_no": 3})
    assert enable.status_code == 200, enable.text
    assert client.get(PATH, headers=admin).json()["total"] == 1


def test_employee_and_amount_validation(users):
    client, identities, _ = users
    salesperson, admin = identities["admin"]
    for label in ("foreign", "inactive"):
        result = write(client, admin, "POST", PATH, payload(identities[label][0]))
        assert result.status_code == 422
    options = client.get(f"{PATH}/salespeople", headers=admin).json()
    assert salesperson in {o["id"] for o in options}
    assert {identities["foreign"][0], identities["inactive"][0]}.isdisjoint(
        {o["id"] for o in options}
    )
    for values in (
        {"credit_limit": "-0.01"},
        {"credit_limit": 1.22},
        {"payment_terms_days": -1},
        {"currency_code": "USD"},
    ):
        assert write(client, admin, "POST", PATH, payload(salesperson, **values)).status_code == 422
    salesperson = identities["finance"][0]
    id = create(client, admin, payload(salesperson))
    with session_factory().begin() as db:
        db.get_one(Employee, UUID(salesperson)).is_active = False
    detail = client.get(f"{PATH}/{id}", headers=admin).json()
    assert detail["salesperson_id"] == salesperson


def test_customer_number_concurrent_and_rollback(users):
    client, identities, _ = users
    salesperson, admin = identities["admin"]
    requests = [payload(salesperson, "RACE-A"), payload(salesperson, "RACE-B")]
    requests[0]["customer_no"] = " Race "
    requests[1]["customer_no"] = "ＲＡＣＥ"
    barrier = Barrier(2)

    def submit(data):
        barrier.wait(timeout=5)
        return write(client, admin, "POST", PATH, data)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, requests))
    assert sorted(r.status_code for r in results) == [201, 409]
    assert next(r for r in results if r.status_code == 409).json()["code"] == "CUSTOMER_NO_CONFLICT"
    assert client.get(PATH, headers=admin).json()["items"][0]["customer_no"] == "RACE"
    partners = client.get(PARTNERS, headers=admin).json()
    assert partners["total"] == 1, "Losing compound create must roll back its business partner"
