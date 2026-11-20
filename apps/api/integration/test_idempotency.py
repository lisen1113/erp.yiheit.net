"""HTTP-to-PostgreSQL integration, with test-only routes and transaction markers."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock
from time import sleep
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from test_authorization import ORIGIN, create_company, create_user
from test_authorization import auth_env as auth_env
from yihe_api.audit.models import AuditLog
from yihe_api.audit.service import Actor, Category, audit_change
from yihe_api.auth.router import DB, authenticated, session_factory
from yihe_api.errors import ErrorCode, install_error_handlers
from yihe_api.idempotency.fingerprint import fingerprint
from yihe_api.idempotency.models import IdempotencyRecord
from yihe_api.idempotency.service import (
    FinalFailure,
    Operation,
    RolledBackRetry,
    Success,
    execute,
)
from yihe_api.request_middleware import RequestTraceMiddleware

CREATE_PATH = "/api/v1/proof/create"
IDEMPOTENCY_KEY = "Idempotency-Key"


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quantity: int


@pytest.fixture
def scenario(auth_env):  # noqa: F811
    app = FastAPI()
    app.add_middleware(RequestTraceMiddleware)
    install_error_handlers(app)
    attempts: list[int] = []
    mutex = Lock()

    def operation(
        request: Request,
        db: DB,
        body: Body,
        action: str,
        target_id: UUID | None,
    ):
        user, session = authenticated(request, db, lock=False)
        actor = Actor(user.company_id, user.id, user.employee_id, session.id)
        scope = Operation(action, "proof", target_id, request.url.path)

        def business(tx):
            with mutex:
                attempts.append(body.quantity)
                attempt = attempts.count(body.quantity)
            resource_id = uuid4()
            audit_change(
                tx,
                company_id=user.company_id,
                actor=actor,
                action="proof.committed",
                entity_type="proof",
                entity_id=resource_id,
                category=Category.BUSINESS,
            )
            if body.quantity == -1:
                raise FinalFailure(ErrorCode.CONFLICT)
            if body.quantity == -2 and attempt == 1:
                raise RolledBackRetry
            if body.quantity == -3:
                raise RuntimeError("synthetic handler crash")
            if body.quantity == -4:
                tx.commit()
            if body.quantity == 10:
                sleep(0.6)
            return Success(
                "proof", resource_id, status_code=201, location=f"/api/v1/proof/{resource_id}"
            )

        return execute(
            db, request, actor=actor, operation=scope, projection=body.model_dump(), work=business
        )

    @app.post(CREATE_PATH)
    @app.put(CREATE_PATH)
    def create(request: Request, db: DB, body: Body):
        return operation(request, db, body, "proof.create", None)

    @app.post("/api/v1/proof/alternate")
    def alternate(request: Request, db: DB, body: Body):
        return operation(request, db, body, "proof.create", None)

    @app.post("/api/v1/proof/{target_id}/{action}")
    def existing(request: Request, db: DB, body: Body, target_id: UUID, action: str):
        return operation(request, db, body, f"proof.{action}", target_id)

    with session_factory().begin() as db:
        company = create_company(db)
        first, _, headers = create_user(db, company)
        _, _, second_headers = create_user(db, company)
        _, _, foreign_headers = create_user(db, create_company(db))
        user_id = first.id

    with TestClient(app, base_url=ORIGIN) as client:
        yield app, client, company, user_id, headers, second_headers, foreign_headers, attempts


def send(client, headers, key, quantity=1, path=CREATE_PATH):
    return client.post(path, json={"quantity": quantity}, headers={**headers, IDEMPOTENCY_KEY: key})


def test_required_invalid_and_unauthorized(scenario):
    _, client, _, _, headers, *_ = scenario
    missing = client.post(CREATE_PATH, json={"quantity": 1}, headers=headers)
    assert missing.status_code == 422
    assert missing.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    invalid = send(client, headers, "not-a-uuid")
    assert invalid.status_code == 422 and invalid.json()["code"] == "IDEMPOTENCY_KEY_INVALID"
    assert send(client, {}, str(uuid4())).status_code == 401
    assert client.get("/api/v1/idempotency-records", headers=headers).status_code == 404


def test_exact_replay_conflicts_audit_and_create_scope(scenario):
    _, client, company, _, headers, _, _, attempts = scenario
    key = str(uuid4())
    first = send(client, headers, key)
    retry = send(client, headers, key)
    assert first.status_code == retry.status_code == 201
    assert first.json() == retry.json()
    assert retry.headers["Idempotency-Replayed"] == "true"
    assert first.headers["Location"] == retry.headers["Location"]
    assert "Set-Cookie" not in retry.headers
    assert attempts == [1]
    for conflict in (
        send(client, headers, key, quantity=2),
        send(client, headers, key, path="/api/v1/proof/alternate"),
        client.put(CREATE_PATH, headers={**headers, IDEMPOTENCY_KEY: key}, json={"quantity": 1}),
    ):
        assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    with session_factory()() as db:
        row = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == key))
        assert row.object_scope == "proof:create"
        assert row.resource_type == "proof" and str(row.resource_id) == first.json()["resource_id"]
        assert set(row.response_body) == {"resource_id", "resource_type", "status"}
        assert set(row.response_headers) == {"Location"}
        events = list(db.scalars(select(AuditLog).where(AuditLog.company_id == company)))
        assert {"idempotency.created", "idempotency.replayed", "idempotency.conflict"} <= {
            event.action for event in events
        }
        assert all(key not in str(event.details) for event in events)
    print("First: HTTP 201; replay: HTTP 201 with same receipt and Idempotency-Replayed: true")
    print("Different request: HTTP 409 IDEMPOTENCY_CONFLICT")
    print("Initial example", json.dumps(first.json(), ensure_ascii=False))
    print(
        "Replay example",
        retry.headers["Idempotency-Replayed"],
        json.dumps(retry.json(), ensure_ascii=False),
    )
    print("Conflict example", json.dumps(conflict.json(), ensure_ascii=False))


def test_scope_isolation_for_action_object_actor_company(scenario):
    _, client, _, _, headers, second, foreign, attempts = scenario
    key = str(uuid4())
    target, other = uuid4(), uuid4()
    combinations = [
        (headers, target, "approve"),
        (headers, target, "post"),
        (headers, other, "approve"),
        (second, target, "approve"),
        (foreign, target, "approve"),
    ]
    results = [
        send(client, auth, key, path=f"/api/v1/proof/{object_id}/{action}")
        for auth, object_id, action in combinations
    ]
    assert all(response.status_code == 201 for response in results)
    assert len({response.json()["resource_id"] for response in results}) == 5
    assert len(attempts) == 5


def test_ten_concurrent_requests_run_only_one_postgresql_transaction(scenario):
    app, client, company, _, headers, _, _, attempts = scenario
    key = str(uuid4())
    gate = Barrier(10)

    def submit(_index):
        with TestClient(app, base_url=ORIGIN) as worker:
            gate.wait(timeout=15)
            return send(worker, headers, key, quantity=10)

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(submit, range(10)))
    assert len(attempts) == 1
    assert (
        sum(r.status_code == 201 and "Idempotency-Replayed" not in r.headers for r in results) == 1
    )
    assert all(
        r.status_code == 201
        or (r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_IN_PROGRESS")
        for r in results
    )
    assert send(client, headers, key, quantity=10).headers["Idempotency-Replayed"] == "true"
    with session_factory()() as db:
        count = db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.company_id == company,
                AuditLog.action == "proof.committed",
            )
        )
        assert count == 1
    print(
        "10 concurrent requests, 1 callback, 1 committed effect, status counts:",
        {code: sum(r.status_code == code for r in results) for code in {201, 409}},
    )


def test_final_failure_replays_and_retryable_rolls_back_before_retry(scenario):
    _, client, company, _, headers, _, _, attempts = scenario
    final_key, retry_key = str(uuid4()), str(uuid4())
    assert send(client, headers, final_key, quantity=-1).status_code == 409
    replay = send(client, headers, final_key, quantity=-1)
    assert replay.status_code == 409 and replay.headers["Idempotency-Replayed"] == "true"
    assert attempts == [-1]
    assert send(client, headers, retry_key, quantity=-2).status_code == 503
    with session_factory()() as db:
        effects = db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.company_id == company,
                AuditLog.action == "proof.committed",
            )
        )
        row = db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == retry_key,
            )
        )
        assert effects == 0 and row.status == "FAILED_RETRYABLE"
    assert send(client, headers, retry_key, quantity=-2).status_code == 201
    assert attempts == [-1, -2, -2]


def test_crash_and_ambiguous_expired_claim_do_not_reexecute(scenario):
    _, client, company, user, headers, _, _, attempts = scenario
    crash, stalled = str(uuid4()), str(uuid4())
    assert send(client, headers, crash, quantity=-3).status_code == 500
    with session_factory().begin() as db:
        assert (
            db.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == crash,
                )
            )
            is None
        )
        now = datetime.now(UTC)
        db.add(
            IdempotencyRecord(
                company_id=company,
                actor_user_id=user,
                action="proof.create",
                object_scope="proof:create",
                idempotency_key=stalled,
                request_method="POST",
                request_path=CREATE_PATH,
                request_hash=fingerprint("POST", CREATE_PATH, "proof.create", {"quantity": 1}),
                status="IN_PROGRESS",
                locked_at=now - timedelta(days=8),
                expires_at=now - timedelta(days=1),
                trace_id="proof-stalled",
            )
        )
    denied = send(client, headers, stalled)
    assert denied.status_code == 409 and denied.json()["code"] == "IDEMPOTENCY_REPLAY_FAILED"
    assert attempts == [-3]
    with session_factory()() as db:
        row = db.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == stalled)
        )
        assert row.status == "IN_PROGRESS"


def test_handler_cannot_commit_early_and_expiry_is_a_tombstone(scenario):
    _, client, company, _, headers, _, _, attempts = scenario
    breach, expired = str(uuid4()), str(uuid4())
    assert send(client, headers, breach, quantity=-4).status_code == 500
    ok = send(client, headers, expired)
    assert ok.status_code == 201
    with session_factory().begin() as db:
        row = db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == expired,
            )
        )
        row.locked_at = datetime.now(UTC) - timedelta(days=9)
        row.expires_at = datetime.now(UTC) - timedelta(days=2)
    assert send(client, headers, expired).status_code == 409
    assert send(client, headers, expired).status_code == 409
    assert attempts == [-4, 1]
    with session_factory()() as db:
        records = list(
            db.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.company_id == company,
                )
            )
        )
        assert len(records) == 1
        assert records[0].status == "EXPIRED"
        assert records[0].response_body is None
        effects = db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.company_id == company,
                AuditLog.action == "proof.committed",
            )
        )
        assert effects == 1
