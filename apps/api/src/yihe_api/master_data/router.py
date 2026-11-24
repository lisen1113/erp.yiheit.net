"""Company-scoped partner access and transactional mutations."""

import hmac
import os
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from yihe_api.audit.service import Actor, Category, audit_change
from yihe_api.auth.router import DB
from yihe_api.errors import ApiError, ErrorCode
from yihe_api.idempotency.service import FinalFailure, Operation, Success, execute
from yihe_api.master_data.models import BusinessPartner
from yihe_api.master_data.schemas import (
    PartnerCreate,
    PartnerEdit,
    PartnerPage,
    PartnerReceipt,
    PartnerType,
    PartnerVersion,
    PartnerView,
)
from yihe_api.rbac.policy import Principal, Scope, scope_filters
from yihe_api.rbac.router import Current

PATH = "/api/v1/business-partners"
VIEW = "business_partner.view"
FULL = "business_partner.bank_account.view_full"
BANK = "invoice_bank_account"
router = APIRouter(prefix="/business-partners", tags=["business partners"])
Idempotency = Annotated[str, Header(alias="Idempotency-Key")]
READ_VARIANTS = {
    VIEW: tuple(PartnerType),
    "business_partner.view_customer": (PartnerType.CUSTOMER, PartnerType.BOTH),
    "business_partner.view_supplier": (PartnerType.SUPPLIER, PartnerType.BOTH),
    "business_partner.view_basic": tuple(PartnerType),
}
BASIC = {
    "id",
    "company_id",
    "code",
    "name",
    "short_name",
    "partner_type",
    "is_active",
    "version_no",
}
NONNULL = {"code", "name", "partner_type", "credit_limit", "payment_terms_days", "currency_code"}


def allowed(principal: Principal, code: str, owner: UUID) -> bool:
    scopes = principal.grants.get(code, set())
    return Scope.COMPANY in scopes or (Scope.SELF in scopes and principal.user.id == owner)


def visibility(principal: Principal, *, detailed_only: bool = False) -> ColumnElement[bool]:
    filters = [
        and_(
            BusinessPartner.partner_type.in_(roles),
            *scope_filters(
                principal, code, BusinessPartner.company_id, owner_user=BusinessPartner.created_by
            ),
        )
        for code, roles in READ_VARIANTS.items()
        if (not detailed_only or not code.endswith("_basic"))
        and principal.grants.get(code, set()) & {Scope.COMPANY, Scope.SELF}
    ]
    if not filters:
        for code in READ_VARIANTS:
            if code in principal.blocked or Scope.DEPARTMENT in principal.grants.get(code, set()):
                principal.require(code)
        raise ApiError(ErrorCode.FORBIDDEN)
    return and_(BusinessPartner.is_deleted.is_(False), or_(*filters))


def render(
    row: BusinessPartner, principal: Principal, *, reveal_bank_account: bool = False
) -> PartnerView:
    detailed = any(
        allowed(principal, code, row.created_by)
        for code, kinds in READ_VARIANTS.items()
        if not code.endswith("_basic") and row.partner_type in kinds
    )
    fields = PartnerView.model_fields if detailed else BASIC
    values = {key: getattr(row, key) for key in fields if key != BANK}
    account = row.invoice_bank_account
    if detailed and account:
        values[BANK] = (
            account
            if reveal_bank_account and allowed(principal, FULL, row.created_by)
            else "****" + (account[-4:] if len(account) > 4 else "")
        )
    return PartnerView.model_validate(values)


@router.get("", response_model_exclude_unset=True, operation_id="listBusinessPartners")
def list_partners(
    db: DB,
    principal: Current,
    response: Response,
    code: Annotated[str | None, Query(max_length=32)] = None,
    name: Annotated[str | None, Query(max_length=200)] = None,
    unified_social_credit_code: Annotated[str | None, Query(max_length=32)] = None,
    role: PartnerType | None = None,
    active: bool = True,
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 25,
) -> PartnerPage:
    response.headers["Cache-Control"] = "private, no-store"
    filters = [visibility(principal), BusinessPartner.is_active == active]
    for value, column in (
        (code, BusinessPartner.code),
        (name, BusinessPartner.name),
        (unified_social_credit_code, BusinessPartner.unified_social_credit_code),
    ):
        if value:
            filters.append(column.icontains(value, autoescape=True))
    if unified_social_credit_code:
        filters.append(visibility(principal, detailed_only=True))
    if role is not None:
        filters.append(BusinessPartner.partner_type.in_((role, PartnerType.BOTH)))
    total = db.scalar(select(func.count()).select_from(BusinessPartner).where(*filters)) or 0
    rows = db.scalars(
        select(BusinessPartner)
        .where(*filters)
        .order_by(BusinessPartner.code, BusinessPartner.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return PartnerPage(
        items=[render(row, principal) for row in rows], total=total, page=page, page_size=page_size
    )


@router.get("/{id}", response_model_exclude_unset=True, operation_id="getBusinessPartner")
def get_partner(
    id: UUID,
    db: DB,
    principal: Current,
    response: Response,
    reveal_bank_account: bool = False,
) -> PartnerView:
    response.headers["Cache-Control"] = "private, no-store"
    row = db.scalar(select(BusinessPartner).where(BusinessPartner.id == id, visibility(principal)))
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND)
    return render(row, principal, reveal_bank_account=reveal_bank_account)


def log(
    db: Session,
    request: Request,
    actor: Actor,
    row: BusinessPartner,
    action: str,
    metadata: dict[str, object] | None = None,
) -> None:
    audit_change(
        db,
        request=request,
        company_id=actor.company_id,
        actor=actor,
        action=f"business_partner.{action}",
        category=Category.MASTER_DATA,
        entity_type="business_partner",
        entity_id=row.id,
        metadata=metadata,
    )


def roles(kind: str) -> set[str]:
    return {"CUSTOMER", "SUPPLIER"} if kind == "BOTH" else {kind}


def audit_roles(
    db: Session, request: Request, actor: Actor, row: BusinessPartner, previous: set[str]
) -> None:
    current = roles(row.partner_type)
    for action, changes in (
        ("role_added", current - previous),
        ("role_removed", previous - current),
    ):
        for role in sorted(changes):
            log(db, request, actor, row, action, {"role": role})


def flush_partner(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as error:
        state = getattr(error.orig, "sqlstate", None)
        if state == "23505":
            constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
            if constraint == "uq_business_partner_company_tax_no":
                raise FinalFailure(ErrorCode.BUSINESS_PARTNER_TAX_NO_CONFLICT) from None
            raise FinalFailure(ErrorCode.CONFLICT) from None
        raise


def normalize(data: dict[str, object]) -> dict[str, object]:
    for key in ("unified_social_credit_code", BANK):
        if key in data and data[key] == "":
            data[key] = None
    credit_code = data.get("unified_social_credit_code")
    if isinstance(credit_code, str):
        data["unified_social_credit_code"] = credit_code.upper()
    account = data.get(BANK)
    if isinstance(account, str) and len(account) <= 4:
        raise ApiError(ErrorCode.VALIDATION_ERROR)
    return data


def dispatch(
    db: Session,
    request: Request,
    operation: Operation,
    data: dict[str, object],
    work: Callable[[Session, Actor], Success],
) -> Response:
    actor = getattr(request.state, "audit_actor", None)
    if not isinstance(actor, Actor):
        raise ApiError(ErrorCode.UNAUTHENTICATED)
    projection = dict(data)
    account = projection.pop(BANK, None)
    if BANK in data:
        key = os.environ.get("ERP_AUDIT_IDENTIFIER_KEY", "").encode()
        if len(key) < 32:
            raise ApiError(ErrorCode.INTERNAL_ERROR)
        projection["partner_account_hmac"] = (
            hmac.digest(key, b"business_partner:" + account.encode(), "sha256").hex()
            if isinstance(account, str)
            else None
        )
    return execute(
        db,
        request,
        actor=actor,
        operation=operation,
        projection=projection,
        work=lambda session: work(session, actor),
    )


@router.post(
    "", status_code=201, response_model=PartnerReceipt, operation_id="createBusinessPartner"
)
def create_partner(
    payload: PartnerCreate,
    request: Request,
    db: DB,
    principal: Current,
    idempotency_key: Idempotency,
) -> Response:
    principal.require("business_partner.create")
    data = normalize(payload.model_dump())

    def work(session: Session, actor: Actor) -> Success:
        row = BusinessPartner(
            company_id=actor.company_id, created_by=actor.user_id, updated_by=actor.user_id, **data
        )
        session.add(row)
        flush_partner(session)
        log(session, request, actor, row, "created")
        audit_roles(session, request, actor, row, set())
        if row.invoice_bank_account:
            log(session, request, actor, row, "bank_account_changed")
        return Success("business_partner", row.id, status_code=201, location=f"{PATH}/{row.id}")

    return dispatch(
        db,
        request,
        Operation("business_partner.create", "business_partner", None, PATH),
        data,
        work,
    )


def mutate(
    id: UUID,
    payload: PartnerVersion,
    request: Request,
    db: Session,
    principal: Principal,
    action: str,
) -> Response:
    permission = "business_partner.edit" if action == "edit" else "business_partner.disable"
    scope = scope_filters(
        principal, permission, BusinessPartner.company_id, owner_user=BusinessPartner.created_by
    )
    query = select(BusinessPartner).where(
        BusinessPartner.id == id, BusinessPartner.is_deleted.is_(False), *scope
    )
    if db.scalar(query) is None:
        raise ApiError(ErrorCode.NOT_FOUND)
    data = normalize(payload.model_dump(exclude_unset=True))
    updates = {key: value for key, value in data.items() if key != "version_no"}
    if any(updates[key] is None for key in NONNULL & updates.keys()):
        raise ApiError(ErrorCode.VALIDATION_ERROR)

    def work(session: Session, actor: Actor) -> Success:
        row = session.scalar(query.with_for_update().execution_options(populate_existing=True))
        if row is None:
            raise ApiError(ErrorCode.NOT_FOUND)
        if row.legacy_readonly:
            raise FinalFailure(ErrorCode.CONFLICT)
        if row.version_no != payload.version_no:
            raise FinalFailure(ErrorCode.VERSION_CONFLICT)
        previous_roles = roles(row.partner_type)
        if action != "edit":
            updates["is_active"] = action == "enable"
        changed = {key for key, value in updates.items() if getattr(row, key) != value}
        for key in changed:
            setattr(row, key, updates[key])
        if changed:
            row.updated_by = actor.user_id
            row.version_no += 1
            flush_partner(session)
            label = {"edit": "updated", "enable": "enabled", "disable": "disabled"}[action]
            log(session, request, actor, row, label, {"fields": sorted(changed)})
            audit_roles(session, request, actor, row, previous_roles)
            if BANK in changed:
                log(session, request, actor, row, "bank_account_changed")
        return Success("business_partner", row.id)

    path = f"{PATH}/{{id}}" + (f"/{action}" if action != "edit" else "")
    return dispatch(
        db,
        request,
        Operation(f"business_partner.{action}", "business_partner", id, path),
        data,
        work,
    )


@router.patch("/{id}", response_model=PartnerReceipt, operation_id="updateBusinessPartner")
def update_partner(
    id: UUID,
    payload: PartnerEdit,
    request: Request,
    db: DB,
    principal: Current,
    idempotency_key: Idempotency,
) -> Response:
    return mutate(id, payload, request, db, principal, "edit")


@router.post("/{id}/disable", response_model=PartnerReceipt, operation_id="disableBusinessPartner")
def disable_partner(
    id: UUID,
    payload: PartnerVersion,
    request: Request,
    db: DB,
    principal: Current,
    idempotency_key: Idempotency,
) -> Response:
    return mutate(id, payload, request, db, principal, "disable")


@router.post("/{id}/enable", response_model=PartnerReceipt, operation_id="enableBusinessPartner")
def enable_partner(
    id: UUID,
    payload: PartnerVersion,
    request: Request,
    db: DB,
    principal: Current,
    idempotency_key: Idempotency,
) -> Response:
    return mutate(id, payload, request, db, principal, "enable")
