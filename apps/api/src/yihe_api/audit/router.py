"""Company audit queries and separately authorized global security queries."""

from datetime import datetime, timedelta
from ipaddress import IPv4Address, IPv6Address
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import AwareDatetime, BaseModel
from sqlalchemy import select

from yihe_api.audit.models import AuditLog, SecurityEvent
from yihe_api.audit.payload import AuditValue
from yihe_api.audit.service import Category, Source
from yihe_api.auth.router import DB, now_utc
from yihe_api.errors import ApiError, ErrorCode
from yihe_api.rbac.router import Current

router = APIRouter(tags=["audit"])
Page = Annotated[int, Query(ge=1)]
Size = Annotated[int, Query(ge=1, le=200)]
Code = Annotated[str | None, Query(max_length=64)]


class Filters(BaseModel):
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    actor_user_id: UUID | None = None
    actor_employee_id: UUID | None = None
    event_category: Category | None = None
    action: Annotated[str | None, Query(max_length=50)] = None
    entity_type: Annotated[str | None, Query(max_length=50)] = None
    entity_id: UUID | None = None
    entity_ref: Annotated[str | None, Query(max_length=128)] = None
    result: Annotated[str | None, Query(max_length=12)] = None
    trace_id: Code = None
    ip_address: IPv4Address | IPv6Address | None = None
    source: Source | None = None
    page: Page = 1
    page_size: Size = 50


class EventView(BaseModel):
    id: UUID
    company_id: UUID | None
    occurred_at: datetime
    trace_id: str
    result: str
    ip_address: str | None = None
    user_agent: str | None = None


class AuditView(EventView):
    actor_user_id: UUID | None
    actor_employee_id: UUID | None
    session_id: UUID | None
    event_category: str
    action: str
    entity_type: str
    entity_id: UUID | None
    entity_ref: str | None
    reason: str | None
    before_data: dict[str, AuditValue] | None
    after_data: dict[str, AuditValue] | None
    metadata: dict[str, AuditValue] | None = None
    source: str
    retention_class: str


class SecurityView(EventView):
    company_resolution: str
    user_id: UUID | None
    session_id: UUID | None
    event_type: str
    request_method: str
    request_path: str
    host: str | None
    origin: str | None
    identifier_hash: str | None


def check_permission(principal: Current, code: str) -> None:
    try:
        principal.require(code, company=True)
    except ApiError as exc:
        if exc.code != ErrorCode.FORBIDDEN:
            raise
        raise ApiError(ErrorCode.AUDIT_ACCESS_DENIED) from None


def audit_view(row: AuditLog, principal: Current) -> AuditView:
    sensitive = "COMPANY" in principal.grants.get("system.audit.view_sensitive_metadata", set())
    view = AuditView(
        id=row.id,
        company_id=row.company_id,
        occurred_at=row.occurred_at,
        trace_id=row.trace_id,
        result=row.result,
        actor_user_id=row.actor_user_id,
        actor_employee_id=row.actor_employee_id,
        session_id=row.session_id,
        event_category=row.event_category,
        action=row.action,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        entity_ref=row.entity_ref,
        reason=row.reason,
        before_data=row.before_data,
        after_data=row.after_data,
        source=row.source,
        retention_class=row.retention_class,
    )
    if sensitive:
        view.metadata = row.details
        view.ip_address = str(row.ip_address) if row.ip_address else None
        view.user_agent = row.user_agent
    return view


@router.get("/audit-logs", response_model_exclude_none=True)
def audit_list(db: DB, principal: Current, filters: Annotated[Filters, Query()]) -> list[AuditView]:
    check_permission(principal, "system.audit.view")
    until = filters.until or now_utc()
    since = filters.since or until - timedelta(days=30)
    if since >= until:
        raise ApiError(ErrorCode.VALIDATION_ERROR)
    stmt = select(AuditLog).where(
        AuditLog.company_id == principal.user.company_id,
        AuditLog.occurred_at >= since,
        AuditLog.occurred_at < until,
    )
    criteria = filters.model_dump(
        exclude={"since", "until", "page", "page_size"}, exclude_none=True
    )
    if filters.ip_address is not None:
        check_permission(principal, "system.audit.view_sensitive_metadata")
        criteria["ip_address"] = str(filters.ip_address)
    for key, value in criteria.items():
        stmt = stmt.where(getattr(AuditLog, key) == value)
    rows = db.scalars(
        stmt.order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
        .offset((filters.page - 1) * filters.page_size)
        .limit(filters.page_size)
    )
    return [audit_view(row, principal) for row in rows]


@router.get("/audit-logs/{id}", response_model_exclude_none=True)
def audit_detail(id: UUID, db: DB, principal: Current) -> AuditView:
    check_permission(principal, "system.audit.view")
    row = db.scalar(
        select(AuditLog).where(AuditLog.id == id, AuditLog.company_id == principal.user.company_id)
    )
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND)
    return audit_view(row, principal)


@router.get("/security-events", response_model_exclude_none=True)
def security_list(
    db: DB,
    principal: Current,
    page: Page = 1,
    page_size: Size = 50,
    trace_id: Code = None,
) -> list[SecurityView]:
    check_permission(principal, "system.security_event.view")
    stmt = select(SecurityEvent).where(SecurityEvent.occurred_at >= now_utc() - timedelta(days=30))
    if trace_id is not None:
        stmt = stmt.where(SecurityEvent.trace_id == trace_id)
    rows = db.scalars(
        stmt.order_by(SecurityEvent.occurred_at.desc(), SecurityEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return [
        SecurityView(
            id=row.id,
            company_id=row.company_id,
            occurred_at=row.occurred_at,
            trace_id=row.trace_id,
            result=row.result,
            ip_address=str(row.ip_address) if row.ip_address else None,
            user_agent=row.user_agent,
            company_resolution=row.company_resolution,
            user_id=row.user_id,
            session_id=row.session_id,
            event_type=row.event_type,
            request_method=row.request_method,
            request_path=row.request_path,
            host=row.host,
            origin=row.origin,
            identifier_hash=row.identifier_hash,
        )
        for row in rows
    ]
