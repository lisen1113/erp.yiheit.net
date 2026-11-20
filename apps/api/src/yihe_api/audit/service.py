"""Audit writes join the caller's transaction; rejected requests use their own transaction."""

import hashlib
import hmac
import os
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from ipaddress import ip_address
from uuid import UUID, uuid4

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from yihe_api.audit.models import AuditLog, SecurityEvent
from yihe_api.audit.payload import changed_fields, sanitize
from yihe_api.db.models import Company
from yihe_api.db.session import DatabaseSettings, make_engine, make_session_factory
from yihe_api.errors import ApiError, ErrorCode
from yihe_api.tracing import LOGGER, trace_context

CONTROLLED = re.compile(r"[a-zA-Z0-9_.:/-]+")


class Category(StrEnum):
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    MASTER_DATA = "MASTER_DATA"
    BUSINESS = "BUSINESS"
    APPROVAL = "APPROVAL"
    POSTING = "POSTING"
    REVERSAL = "REVERSAL"
    SECURITY = "SECURITY"
    IMPORT = "IMPORT"
    EXPORT = "EXPORT"
    PRINT = "PRINT"
    MIGRATION = "MIGRATION"
    SYSTEM_CONFIGURATION = "SYSTEM_CONFIGURATION"


class Source(StrEnum):
    API = "API"
    CLI = "CLI"
    SYSTEM = "SYSTEM"
    WORKER = "WORKER"
    MIGRATION = "MIGRATION"


@dataclass(frozen=True)
class Actor:
    company_id: UUID
    user_id: UUID
    employee_id: UUID
    session_id: UUID | None = None


def stamp_identity(request: Request, actor: Actor) -> None:
    request.state.audit_actor = actor


def request_context(request: Request | None) -> tuple[str | None, str | None]:
    if request is None:
        return None, None
    raw_ip = request.client.host if request.client else ""
    try:
        ip = str(ip_address(raw_ip))
    except ValueError:
        ip = None
    agent = request.headers.get("user-agent", "")[:512]
    return ip, hashlib.sha256(agent.encode()).hexdigest() if agent else None


def current_trace() -> str:
    context = trace_context()
    return context.trace_id if context else uuid4().hex


def audit_change(
    db: Session,
    *,
    company_id: UUID,
    action: str,
    entity_type: str,
    category: Category,
    actor: Actor | None = None,
    entity_id: UUID | None = None,
    entity_ref: str | None = None,
    reason: str | None = None,
    before: Mapping[str, object] | None = None,
    after: Mapping[str, object] | None = None,
    metadata: Mapping[str, object] | None = None,
    source: Source = Source.API,
    request: Request | None = None,
) -> AuditLog:
    if actor is not None and actor.company_id != company_id:
        raise ApiError(ErrorCode.AUDIT_EVENT_INVALID)
    if actor is None and source not in {Source.SYSTEM, Source.WORKER, Source.MIGRATION}:
        raise ApiError(ErrorCode.AUDIT_EVENT_INVALID)
    for value, limit in ((action, 50), (entity_type, 50), (entity_ref, 128), (reason, 100)):
        if value is not None and (len(value) > limit or not CONTROLLED.fullmatch(value)):
            raise ApiError(ErrorCode.AUDIT_EVENT_INVALID)
    try:
        old, new = changed_fields(before or {}, after or {})
        details = sanitize(metadata or {})
    except ValueError:
        raise ApiError(ErrorCode.AUDIT_EVENT_INVALID) from None
    ip, agent = request_context(request)
    row = AuditLog(
        company_id=company_id,
        actor_user_id=actor.user_id if actor else None,
        actor_employee_id=actor.employee_id if actor else None,
        session_id=actor.session_id if actor else None,
        trace_id=current_trace(),
        event_category=category,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_ref=entity_ref,
        result="SUCCESS",
        reason=reason,
        before_data=old or None,
        after_data=new or None,
        details=details,
        ip_address=ip,
        user_agent=agent,
        source=source,
        retention_class="STANDARD" if category == Category.AUTHENTICATION else "CRITICAL",
    )
    db.add(row)
    try:
        db.flush()
    except SQLAlchemyError:
        raise ApiError(ErrorCode.AUDIT_WRITE_FAILED) from None
    return row


@lru_cache
def security_sessions() -> sessionmaker[Session]:
    return make_session_factory(make_engine(DatabaseSettings.from_environment()))


def identifier_digest(identifier: str, key: str) -> str:
    normalized = unicodedata.normalize("NFKC", identifier.strip()).casefold()
    return hmac.new(key.encode(), normalized.encode(), "sha256").hexdigest()


def record_rejection(request: Request, code: ErrorCode, *, status: int) -> None:
    # Only known authentication failures and refusals; no recursive audit on DB faults.
    if status not in {401, 403} and not (
        status in {404, 405, 422} and request.url.path.startswith(("/api/v1/auth/", "/v1/auth/"))
    ):
        return
    actor = getattr(request.state, "audit_actor", None)
    hosts = request.headers.getlist("host")
    raw_host = hosts[0].lower() if len(hosts) == 1 else "<untrusted>"
    auth_origin = os.environ.get("ERP_AUTH_ORIGIN", "")
    raw_origin = request.headers.get("origin", "")
    host = (
        raw_host
        if raw_host in {"erp.yiheit.net", auth_origin.removeprefix("https://")}
        else ("<untrusted>" if raw_host else None)
    )
    origin = (
        raw_origin
        if raw_origin == auth_origin and raw_origin
        else ("<untrusted>" if raw_origin else None)
    )
    route = request.scope.get("route")
    path = getattr(route, "path", None) or "<unmatched>"
    method = (
        request.method
        if request.method in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
        else "<other>"
    )
    key = os.environ.get("ERP_AUDIT_IDENTIFIER_KEY", "")
    identifier = getattr(request.state, "audit_identifier", None)
    fingerprint = (
        identifier_digest(identifier, key) if len(key.encode()) >= 32 and identifier else None
    )
    ip, agent = request_context(request)
    try:
        with security_sessions().begin() as db:
            company_id = actor.company_id if actor else None
            resolution = "AUTHENTICATED_USER" if actor else "UNKNOWN"
            if actor is None and raw_host == "erp.yiheit.net":
                configured = os.environ.get("ERP_AUDIT_HOST_COMPANY_ID")
                try:
                    mapped_id = UUID(configured) if configured else None
                except ValueError:
                    mapped_id = None
                    LOGGER.error("audit.security_write_failed")
                if mapped_id is not None:
                    mapped = db.get(Company, mapped_id)
                    if mapped is not None and not mapped.is_deleted:
                        company_id, resolution = mapped.id, "HOST_MAPPING"
            row = SecurityEvent(
                company_id=company_id,
                company_resolution=resolution,
                user_id=actor.user_id if actor else None,
                session_id=actor.session_id if actor else None,
                trace_id=current_trace(),
                event_type=getattr(request.state, "audit_type", code.value),
                result="FAILURE" if status in {401, 422} else "DENIED",
                ip_address=ip,
                user_agent=agent,
                details={},
                request_path=path,
                request_method=method,
                host=host,
                origin=origin,
                identifier_hash=fingerprint,
            )
            db.add(row)
    except (SQLAlchemyError, ValueError, RuntimeError):
        LOGGER.error("audit.security_write_failed")
