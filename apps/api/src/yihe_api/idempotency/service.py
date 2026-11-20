"""Single-transaction idempotency for local PostgreSQL business writes."""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from fastapi import Request
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from yihe_api.audit.service import Actor, Category, audit_change, current_trace
from yihe_api.errors import ERRORS, ApiError, ErrorCode, error_response
from yihe_api.idempotency.fingerprint import fingerprint, normalize_key
from yihe_api.idempotency.models import IdempotencyRecord

KEY_HEADER = "Idempotency-Key"
REPLAY_HEADER = "Idempotency-Replayed"
ENTITY = re.compile(r"[a-z][a-z0-9_]{0,49}")
ACTION = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,3}")


class Status(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    EXPIRED = "EXPIRED"


class Retention(StrEnum):
    STANDARD = "STANDARD"
    EXTERNAL_CALLBACK = "EXTERNAL_CALLBACK"

    def span(self) -> timedelta:
        return timedelta(days=30 if self is Retention.EXTERNAL_CALLBACK else 7)


@dataclass(frozen=True)
class Operation:
    action: str
    entity_type: str
    target_id: UUID | None
    normalized_path: str
    retention: Retention = Retention.STANDARD

    def object_scope(self) -> str:
        if not ENTITY.fullmatch(self.entity_type) or not ACTION.fullmatch(self.action):
            raise ValueError("Declare a stable action and entity type")
        if len(self.action) > 64 or len(self.normalized_path) > 255:
            raise ValueError("Idempotency scope exceeds its storage limit")
        return f"{self.entity_type}:{self.target_id or 'create'}"


class ResourceState(StrEnum):
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    QUEUED = "queued"


@dataclass(frozen=True)
class Success:
    """Only this fixed non-secret receipt is persisted and replayed."""

    resource_type: str
    resource_id: UUID
    state: ResourceState = ResourceState.COMPLETED
    status_code: int = 200
    location: str | None = None

    def receipt(self) -> tuple[dict[str, str], dict[str, str]]:
        if not ENTITY.fullmatch(self.resource_type) or self.status_code not in {200, 201, 202}:
            raise ValueError("Unsupported idempotency receipt")
        headers = {}
        if self.location is not None:
            # Location can only expose the result's own UUID in a same-origin API URL.
            if (
                not re.fullmatch(
                    r"/api/v1/[a-z][a-z0-9_/-]{0,150}/" + str(self.resource_id), self.location
                )
                or "//" in self.location
            ):
                raise ValueError("Invalid idempotency receipt Location")
            headers["Location"] = self.location
        return {
            "resource_type": self.resource_type,
            "resource_id": str(self.resource_id),
            "status": self.state.value,
        }, headers


class FinalFailure(Exception):
    def __init__(self, code: ErrorCode) -> None:
        if ERRORS[code][0] not in {400, 409, 422}:
            raise ValueError("Only deterministic business validation failures are replayable")
        self.code = code
        super().__init__(code)


class RolledBackRetry(Exception):
    """Opt-in from DB-only handlers when their entire savepoint may safely be rolled back."""


def read_key(request: Request) -> str:
    entries = request.headers.getlist(KEY_HEADER)
    if not entries:
        raise ApiError(ErrorCode.IDEMPOTENCY_KEY_REQUIRED)
    try:
        if len(entries) != 1:
            raise ValueError
        return normalize_key(entries[0])
    except ValueError:
        raise ApiError(ErrorCode.IDEMPOTENCY_KEY_INVALID) from None


def record_event(
    db: Session,
    actor: Actor,
    request: Request,
    event: str,
    *,
    operation: Operation,
    key: str,
    request_hash: str,
    state: Status,
    resource_id: UUID | None = None,
) -> None:
    audit_change(
        db,
        company_id=actor.company_id,
        actor=actor,
        action=f"idempotency.{event}",
        category=Category.BUSINESS,
        entity_type=operation.entity_type,
        entity_id=resource_id,
        metadata={
            "idempotency_key_hash": hashlib.sha256(key.encode()).hexdigest(),
            "idempotency_request_hash": request_hash,
            "idempotency_action": operation.action,
            "idempotency_status": state.value,
        },
        request=request,
    )


def execute(
    db: Session,
    request: Request,
    *,
    actor: Actor,
    operation: Operation,
    projection: object,
    work: Callable[[Session], Success],
) -> JSONResponse:
    """Caller must authenticate/authorize first and commit only after this function returns."""
    key = read_key(request)
    scope = operation.object_scope()
    method = request.method.upper()
    try:
        request_hash = fingerprint(method, operation.normalized_path, operation.action, projection)
    except ValueError:
        raise ApiError(ErrorCode.VALIDATION_ERROR) from None

    identity = dict(
        company_id=actor.company_id,
        actor_user_id=actor.user_id,
        action=operation.action,
        object_scope=scope,
        idempotency_key=key,
    )
    # The full SQL unique constraint is authoritative; advisory contention returns early.
    tag = json.dumps([str(value) for value in identity.values()], separators=(",", ":"))
    bucket = int.from_bytes(hashlib.sha256(tag.encode()).digest()[:8], "big", signed=True)
    if not db.scalar(select(func.pg_try_advisory_xact_lock(bucket))):
        record_event(
            db,
            actor,
            request,
            "in_progress",
            operation=operation,
            key=key,
            request_hash=request_hash,
            state=Status.IN_PROGRESS,
        )
        return error_response(ErrorCode.IDEMPOTENCY_IN_PROGRESS)
    db.execute(text("SET LOCAL lock_timeout = '3s'"))
    now = datetime.now(UTC)
    now_expiry = now + operation.retention.span()
    new_id = db.scalar(
        insert(IdempotencyRecord)
        .values(
            **identity,
            request_method=method,
            request_path=operation.normalized_path,
            request_hash=request_hash,
            status=Status.IN_PROGRESS,
            locked_at=now,
            expires_at=now_expiry,
            trace_id=current_trace(),
        )
        .on_conflict_do_nothing(constraint="uq_idempotency_scope_key")
        .returning(IdempotencyRecord.id)
    )
    row = db.scalar(select(IdempotencyRecord).filter_by(**identity).with_for_update())
    if row is None:
        raise ApiError(ErrorCode.IDEMPOTENCY_REPLAY_FAILED)

    def event(name: str) -> None:
        record_event(
            db,
            actor,
            request,
            name,
            operation=operation,
            key=key,
            request_hash=request_hash,
            state=Status(row.status),
            resource_id=row.resource_id or operation.target_id,
        )

    if new_id is None:
        if (row.request_method, row.request_path, row.request_hash) != (
            method,
            operation.normalized_path,
            request_hash,
        ):
            event("conflict")
            return error_response(ErrorCode.IDEMPOTENCY_CONFLICT)
        if now >= row.expires_at or row.status == Status.EXPIRED:
            if row.status != Status.IN_PROGRESS and row.status != Status.EXPIRED:
                row.status = Status.EXPIRED
                row.response_body = None
                row.response_headers = None
                row.version_no += 1
            # A pre-existing committed IN_PROGRESS row needs reconciliation, even if expired.
            event("expired")
            return error_response(ErrorCode.IDEMPOTENCY_REPLAY_FAILED)
        if row.status == Status.IN_PROGRESS:
            event("in_progress")
            return error_response(ErrorCode.IDEMPOTENCY_IN_PROGRESS)
        if row.status == Status.COMPLETED:
            if row.response_body is None or row.response_status is None:
                raise ApiError(ErrorCode.IDEMPOTENCY_REPLAY_FAILED)
            event("replayed")
            return JSONResponse(
                row.response_body,
                status_code=row.response_status,
                headers={**(row.response_headers or {}), REPLAY_HEADER: "true"},
            )
        if row.status == Status.FAILED_FINAL:
            if row.error_code is None:
                raise ApiError(ErrorCode.IDEMPOTENCY_REPLAY_FAILED)
            event("replayed")
            return error_response(ErrorCode(row.error_code), headers={REPLAY_HEADER: "true"})
        if row.status != Status.FAILED_RETRYABLE:
            raise ApiError(ErrorCode.IDEMPOTENCY_REPLAY_FAILED)
        row.status = Status.IN_PROGRESS
        row.locked_at = now
        row.trace_id = current_trace()
        row.version_no += 1
    event("created")

    def reject_handler_commit(_session: Session) -> None:
        raise RuntimeError("Idempotency handlers must not commit the outer transaction")

    try:
        with db.begin_nested():
            sqlalchemy_event.listen(db, "before_commit", reject_handler_commit)
            try:
                result = work(db)
                response_body, response_headers = result.receipt()
                db.flush()
            finally:
                sqlalchemy_event.remove(db, "before_commit", reject_handler_commit)
    except FinalFailure as exc:
        row.status = Status.FAILED_FINAL
        row.error_code = exc.code.value
        row.response_status = ERRORS[exc.code][0]
        row.completed_at = datetime.now(UTC)
        row.version_no += 1
        event("failed")
        return error_response(exc.code)
    except RolledBackRetry:
        row.status = Status.FAILED_RETRYABLE
        row.error_code = ErrorCode.IDEMPOTENCY_RETRYABLE.value
        row.response_status = 503
        row.completed_at = datetime.now(UTC)
        row.version_no += 1
        event("failed")
        return error_response(ErrorCode.IDEMPOTENCY_RETRYABLE, headers={"Retry-After": "1"})

    row.status = Status.COMPLETED
    row.response_status = result.status_code
    row.response_body = response_body
    row.response_headers = response_headers
    row.resource_type = result.resource_type
    row.resource_id = result.resource_id
    row.error_code = None
    row.completed_at = datetime.now(UTC)
    row.version_no += 1
    db.flush()
    return JSONResponse(response_body, status_code=result.status_code, headers=response_headers)
