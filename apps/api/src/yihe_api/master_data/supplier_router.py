"""Supplier profile access with the existing partner, audit and idempotency services."""

from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from yihe_api.audit.service import Actor, Category, audit_change
from yihe_api.auth.models import Employee
from yihe_api.auth.router import DB
from yihe_api.errors import ApiError, ErrorCode
from yihe_api.idempotency.service import FinalFailure, Operation, Success
from yihe_api.master_data import router as partner_api
from yihe_api.master_data.customer_models import CustomerProfile
from yihe_api.master_data.models import BusinessPartner
from yihe_api.master_data.schemas import PartnerView
from yihe_api.master_data.supplier_models import SupplierProfile
from yihe_api.master_data.supplier_schemas import (
    BuyerOption,
    Kind,
    SupplierCreate,
    SupplierDisable,
    SupplierPage,
    SupplierPatch,
    SupplierReceipt,
    SupplierVersion,
    SupplierView,
)
from yihe_api.rbac.policy import Principal, Scope, scope_filters
from yihe_api.rbac.router import Current

PATH = "/api/v1/suppliers"
router = APIRouter(prefix="/suppliers", tags=["suppliers"])
P, B, E = SupplierProfile, BusinessPartner, Employee


def scoped(principal: Principal, permission: str) -> tuple[ColumnElement[bool], ...]:
    return scope_filters(
        principal, f"supplier.{permission}", P.company_id, owner_employee=P.buyer_id
    )


def joined(principal: Principal, permission: str) -> Select[SupplierProfile, BusinessPartner, str]:
    return (
        select(P, B, E.name)
        .join(B, P.business_partner_id == B.id)
        .join(E, P.buyer_id == E.id)
        .where(P.is_deleted.is_(False), *scoped(principal, permission))
    )


def enabled(profile: SupplierProfile, partner: BusinessPartner) -> bool:
    return (
        profile.is_active
        and partner.is_active
        and not partner.is_deleted
        and partner.partner_type in {"SUPPLIER", "BOTH"}
    )


def project(
    profile: SupplierProfile,
    partner: BusinessPartner,
    buyer: str,
    principal: Principal,
    reveal: bool = False,
) -> SupplierView:
    data = {
        key: getattr(partner, key) for key in PartnerView.model_fields if key != partner_api.BANK
    }
    account = partner.invoice_bank_account
    data[partner_api.BANK] = (
        (
            account
            if reveal and partner_api.allowed(principal, partner_api.FULL, partner.created_by)
            else "****" + (account[-4:] if len(account) > 4 else "")
        )
        if account
        else None
    )
    fields = {
        key: getattr(profile, key)
        for key in SupplierView.model_fields
        if key not in {"partner", "buyer_name", "is_available", "customer_profile_id"}
    }
    return SupplierView(
        **fields,
        buyer_name=buyer,
        is_available=enabled(profile, partner),
        partner=PartnerView.model_validate(data),
    )


@router.get("", operation_id="listSuppliers")
def list_suppliers(
    db: DB,
    principal: Current,
    response: Response,
    supplier_no: Annotated[str | None, Query(max_length=32)] = None,
    name: Annotated[str | None, Query(max_length=200)] = None,
    tax_no: Annotated[str | None, Query(max_length=64)] = None,
    buyer_id: UUID | None = None,
    supplier_type: Kind | None = None,
    active: bool = True,
    sort: Literal["supplier_no", "name", "updated_at"] = "supplier_no",
    descending: bool = False,
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 25,
) -> SupplierPage:
    response.headers["Cache-Control"] = "private, no-store"
    stmt = joined(principal, "view").where(P.is_active == active)
    if active:
        stmt = stmt.where(
            B.is_active.is_(True), B.is_deleted.is_(False), B.partner_type.in_(("SUPPLIER", "BOTH"))
        )
    for value, col in ((supplier_no, P.supplier_no), (name, B.name), (tax_no, B.tax_no)):
        if value:
            stmt = stmt.where(col.icontains(value, autoescape=True))
    if supplier_type:
        stmt = stmt.where(P.supplier_type == supplier_type)
    if buyer_id:
        stmt = stmt.where(P.buyer_id == buyer_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    column = {"supplier_no": P.supplier_no, "name": B.name, "updated_at": P.updated_at}[sort]
    rows = db.execute(
        stmt.order_by(column.desc() if descending else column.asc(), P.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return SupplierPage(
        items=[project(p, b, n, principal) for p, b, n in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/buyers", operation_id="listSupplierBuyers")
def buyers(
    db: DB,
    principal: Current,
    q: Annotated[str, Query(max_length=100)] = "",
) -> list[BuyerOption]:
    scope = principal.require("supplier.view")
    stmt = select(E).where(E.company_id == principal.user.company_id, E.is_active.is_(True))
    if scope == Scope.SELF:
        stmt = stmt.where(E.id == principal.user.employee_id)
    if q:
        stmt = stmt.where(E.name.icontains(q, autoescape=True))
    return [
        BuyerOption(id=e.id, name=e.name)
        for e in db.scalars(stmt.order_by(E.name, E.id).limit(200))
    ]


@router.get("/{id}", operation_id="getSupplier")
def get_supplier(
    id: UUID, db: DB, principal: Current, response: Response, reveal_bank_account: bool = False
) -> SupplierView:
    response.headers["Cache-Control"] = "private, no-store"
    row = db.execute(joined(principal, "view").where(P.id == id)).one_or_none()
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND)
    profile, partner, name = row
    result = project(profile, partner, name, principal, reveal_bank_account)
    if partner.partner_type == "BOTH":
        try:
            customer_scope = scope_filters(
                principal,
                "customer.view",
                CustomerProfile.company_id,
                owner_employee=CustomerProfile.salesperson_id,
            )
        except ApiError as exc:
            if exc.code not in {
                ErrorCode.FORBIDDEN,
                ErrorCode.MFA_REQUIRED,
                ErrorCode.DATA_SCOPE_UNAVAILABLE,
            }:
                raise
        else:
            result.customer_profile_id = db.scalar(
                select(CustomerProfile.id).where(
                    CustomerProfile.business_partner_id == partner.id,
                    CustomerProfile.is_deleted.is_(False),
                    *customer_scope,
                )
            )
    return result


def validate_buyer(db: Session, principal: Principal, permission: str, id: UUID) -> None:
    scope = principal.require(f"supplier.{permission}")
    if scope == Scope.SELF and id != principal.user.employee_id:
        raise ApiError(ErrorCode.FORBIDDEN)
    if (
        db.scalar(
            select(E.id)
            .where(E.id == id, E.company_id == principal.user.company_id, E.is_active.is_(True))
            .with_for_update(read=True)
        )
        is None
    ):
        raise FinalFailure(ErrorCode.VALIDATION_ERROR)


def log(
    db: Session,
    request: Request,
    actor: Actor,
    row: SupplierProfile,
    action: str,
    changes: dict[str, object] | None = None,
    *,
    reason: str | None = None,
) -> None:
    audit_change(
        db,
        request=request,
        company_id=actor.company_id,
        actor=actor,
        action=f"supplier.{action}",
        category=Category.MASTER_DATA,
        entity_type="supplier_profile",
        entity_id=row.id,
        metadata=changes,
        reason=reason,
    )


def flush_profile(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as error:
        constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
        if getattr(error.orig, "sqlstate", None) == "23505":
            code = (
                ErrorCode.SUPPLIER_NO_CONFLICT
                if constraint == "uq_supplier_profile_company_id_supplier_no"
                else ErrorCode.CONFLICT
            )
            raise FinalFailure(code) from None
        raise


def partner_scope(principal: Principal) -> tuple[ColumnElement[bool], ...]:
    return scope_filters(principal, "business_partner.edit", B.company_id, owner_user=B.created_by)


@router.post("", status_code=201, response_model=SupplierReceipt, operation_id="createSupplier")
def create_supplier(
    payload: SupplierCreate,
    request: Request,
    db: DB,
    principal: Current,
    idempotency_key: partner_api.Idempotency,
) -> Response:
    return create_supplier_record(payload, request, db, principal)


def create_supplier_record(
    payload: SupplierCreate,
    request: Request,
    db: Session,
    principal: Principal,
    *,
    match_existing: bool = False,
) -> Response:
    scope = principal.require("supplier.create")
    if scope == Scope.SELF and payload.buyer_id != principal.user.employee_id:
        raise ApiError(ErrorCode.FORBIDDEN)
    partner_data = partner_api.normalize(payload.partner.model_dump()) if payload.partner else {}
    if not match_existing and payload.business_partner_id:
        partner_scope(principal)

    def work(tx: Session, actor: Actor) -> Success:
        partner: BusinessPartner | None
        added_role = False
        validate_buyer(tx, principal, "create", payload.buyer_id)
        partner_id = payload.business_partner_id
        if match_existing and payload.partner:
            partner_id = match_partner(
                tx,
                actor.company_id,
                payload.partner.code,
                payload.partner.tax_no,
                payload.partner.name,
                cast(str | None, partner_data.get("unified_social_credit_code")),
            )
        if partner_id is None:
            if any(
                partner_data.get(key)
                for key in (partner_api.BANK, "credit_limit", "payment_terms_days")
            ):
                principal.require("business_partner.create")
            partner = B(
                company_id=actor.company_id,
                created_by=actor.user_id,
                updated_by=actor.user_id,
                partner_type="SUPPLIER",
                **partner_data,
            )
            tx.add(partner)
            partner_api.flush_partner(tx)
            partner_api.log(tx, request, actor, partner, "created")
            partner_api.audit_roles(tx, request, actor, partner, set())
            if partner.invoice_bank_account:
                partner_api.log(tx, request, actor, partner, "bank_account_changed")
        else:
            partner = tx.scalar(
                select(B)
                .where(B.id == partner_id, B.is_deleted.is_(False), *partner_scope(principal))
                .with_for_update()
            )
            if partner is None:
                raise ApiError(ErrorCode.NOT_FOUND)
            if partner.legacy_readonly or not partner.is_active:
                raise FinalFailure(ErrorCode.SUPPLIER_UNAVAILABLE)
            if partner.partner_type == "CUSTOMER":
                added_role = True
                partner.partner_type = "BOTH"
                partner.updated_by = actor.user_id
                partner.version_no += 1
                partner_api.flush_partner(tx)
                partner_api.audit_roles(tx, request, actor, partner, {"CUSTOMER"})
        profile = P(
            company_id=actor.company_id,
            business_partner_id=partner.id,
            supplier_no=payload.supplier_no,
            supplier_type=payload.supplier_type,
            buyer_id=payload.buyer_id,
            delivery_or_pickup_notes=payload.delivery_or_pickup_notes,
            payment_term_days=payload.payment_term_days,
            default_contact_name=payload.default_contact_name,
            default_contact_phone=payload.default_contact_phone,
            default_contact_email=payload.default_contact_email,
            created_by=actor.user_id,
            updated_by=actor.user_id,
        )
        tx.add(profile)
        flush_profile(tx)
        log(tx, request, actor, profile, "created")
        if added_role:
            log(tx, request, actor, profile, "role_added")
        if match_existing:
            log(tx, request, actor, profile, "imported")
        return Success(
            "supplier_profile", profile.id, status_code=201, location=f"{PATH}/{profile.id}"
        )

    projection = payload.model_dump(exclude={"partner"}, mode="json") | partner_data
    action, path = (
        ("supplier.import_row", f"{PATH}/import") if match_existing else ("supplier.create", PATH)
    )
    return partner_api.dispatch(
        db, request, Operation(action, "supplier_profile", None, path), projection, work
    )


def match_partner(
    db: Session, company_id: UUID, code: str, tax_no: str | None, name: str, credit_code: str | None
) -> UUID | None:
    identity = B.code == code
    if tax_no:
        identity |= B.tax_no == tax_no
    if credit_code:
        identity |= B.unified_social_credit_code == credit_code
    rows = db.scalars(select(B).where(B.company_id == company_id, identity)).all()
    if not rows:
        possible_duplicate = db.scalar(
            select(B.id)
            .where(B.company_id == company_id, func.lower(B.name) == name.lower())
            .limit(1)
        )
        if possible_duplicate:
            raise FinalFailure(ErrorCode.SUPPLIER_IMPORT_PARTNER_MISMATCH)
        return None
    if len(rows) > 1:
        raise FinalFailure(ErrorCode.SUPPLIER_IMPORT_PARTNER_MISMATCH)
    row = rows[0]
    if (tax_no and row.tax_no != tax_no) or (
        credit_code and row.unified_social_credit_code != credit_code
    ):
        raise FinalFailure(ErrorCode.SUPPLIER_IMPORT_PARTNER_MISMATCH)
    return row.id


def edit_partner(
    tx: Session,
    request: Request,
    actor: Actor,
    profile: SupplierProfile,
    partner: BusinessPartner,
    patch: dict[str, object],
) -> bool:
    # The caller holds the partner lock and has checked write authorization.
    if partner.legacy_readonly:
        raise FinalFailure(ErrorCode.CONFLICT)
    if patch["version_no"] != partner.version_no:
        raise FinalFailure(ErrorCode.VERSION_CONFLICT)
    changes = {
        key: value
        for key, value in patch.items()
        if key != "version_no" and getattr(partner, key) != value
    }
    if not changes:
        return False
    if any(changes[key] is None for key in changes.keys() & partner_api.NONNULL):
        raise FinalFailure(ErrorCode.VALIDATION_ERROR)
    previous_credit = format(partner.credit_limit, ".2f")
    for key, value in changes.items():
        setattr(partner, key, value)
    partner.version_no += 1
    partner.updated_by = actor.user_id
    partner_api.flush_partner(tx)
    partner_api.log(tx, request, actor, partner, "updated", {"fields": sorted(changes)})
    if partner_api.BANK in changes:
        partner_api.log(tx, request, actor, partner, "bank_account_changed")
    if "credit_limit" in changes:
        log(
            tx,
            request,
            actor,
            profile,
            "credit_limit_changed",
            {
                "previous_credit": previous_credit,
                "current_credit": format(partner.credit_limit, ".2f"),
            },
        )
    if "payment_terms_days" in changes:
        log(tx, request, actor, profile, "payment_terms_changed")
    if any(key.startswith("invoice_") or key == "tax_no" for key in changes):
        log(
            tx,
            request,
            actor,
            profile,
            "invoice_information_changed",
            {"bank_account_changed": partner_api.BANK in changes},
        )
    return True


def mutate(
    id: UUID,
    payload: SupplierVersion,
    request: Request,
    db: Session,
    principal: Current,
    action: str,
) -> Response:
    permission = "edit" if action == "edit" else "disable"
    query = joined(principal, permission).where(P.id == id)
    current = db.execute(query).one_or_none()
    if current is None:
        raise ApiError(ErrorCode.NOT_FOUND)
    patch = (
        partner_api.normalize(payload.partner.model_dump(exclude_unset=True))
        if (isinstance(payload, SupplierPatch) and payload.partner)
        else {}
    )
    if patch and not partner_api.allowed(principal, "business_partner.edit", current[1].created_by):
        principal.require("business_partner.edit")
        raise ApiError(ErrorCode.FORBIDDEN)
    values = payload.model_dump(exclude_unset=True, exclude={"partner"})
    changes = {
        key: values[key]
        for key in values.keys()
        & {
            "supplier_no",
            "supplier_type",
            "buyer_id",
            "delivery_or_pickup_notes",
            "payment_term_days",
            "default_contact_name",
            "default_contact_phone",
            "default_contact_email",
        }
    }

    def work(tx: Session, actor: Actor) -> Success:
        # Lock the identity first, matching partner role changes and profile creation.
        partner_id = current[0].business_partner_id
        tx.execute(select(B.id).where(B.id == partner_id).with_for_update())
        row = tx.execute(
            query.with_for_update(of=P).execution_options(populate_existing=True)
        ).one_or_none()
        if row is None:
            raise ApiError(ErrorCode.NOT_FOUND)
        profile, partner, _ = row
        if profile.version_no != payload.version_no:
            raise FinalFailure(ErrorCode.VERSION_CONFLICT)
        edited = False
        if action == "edit":
            if (
                isinstance(payload, SupplierPatch)
                and payload.buyer_id
                and payload.buyer_id != profile.buyer_id
            ):
                validate_buyer(tx, principal, "edit", payload.buyer_id)
                log(
                    tx,
                    request,
                    actor,
                    profile,
                    "buyer_changed",
                    {"employee_id": payload.buyer_id},
                )
            for key, value in changes.items():
                if getattr(profile, key) != value:
                    if key == "payment_term_days":
                        log(
                            tx,
                            request,
                            actor,
                            profile,
                            "payment_terms_changed",
                            {"before": profile.payment_term_days, "after": value},
                        )
                    setattr(profile, key, value)
                    edited = True
            if patch:
                edited = edit_partner(tx, request, actor, profile, partner, patch) or edited
        elif profile.is_active != (action == "enable"):
            profile.is_active = action == "enable"
            edited = True
        if edited:
            profile.version_no += 1
            profile.updated_by = actor.user_id
            flush_profile(tx)
            label = {"edit": "updated", "enable": "enabled", "disable": "disabled"}[action]
            reason = payload.reason if isinstance(payload, SupplierDisable) else None
            log(tx, request, actor, profile, label, reason=reason)
        return Success("supplier_profile", profile.id)

    path = f"{PATH}/{{id}}" + (f"/{action}" if action != "edit" else "")
    projection = payload.model_dump(exclude_unset=True, exclude={"partner"}, mode="json") | patch
    # Keep profile and partner version values distinct in the fingerprint.
    if patch:
        projection["partner_version_no"] = projection.pop("version_no")
        projection["version_no"] = payload.version_no
    return partner_api.dispatch(
        db, request, Operation(f"supplier.{action}", "supplier_profile", id, path), projection, work
    )


@router.patch("/{id}", response_model=SupplierReceipt, operation_id="updateSupplier")
def update_supplier(
    id: UUID,
    payload: SupplierPatch,
    request: Request,
    db: DB,
    principal: Current,
    idempotency_key: partner_api.Idempotency,
) -> Response:
    return mutate(id, payload, request, db, principal, "edit")


@router.post("/{id}/disable", response_model=SupplierReceipt, operation_id="disableSupplier")
def disable_supplier(
    id: UUID,
    payload: SupplierDisable,
    request: Request,
    db: DB,
    principal: Current,
    idempotency_key: partner_api.Idempotency,
) -> Response:
    return mutate(id, payload, request, db, principal, "disable")


@router.post("/{id}/enable", response_model=SupplierReceipt, operation_id="enableSupplier")
def enable_supplier(
    id: UUID,
    payload: SupplierVersion,
    request: Request,
    db: DB,
    principal: Current,
    idempotency_key: partner_api.Idempotency,
) -> Response:
    return mutate(id, payload, request, db, principal, "enable")
