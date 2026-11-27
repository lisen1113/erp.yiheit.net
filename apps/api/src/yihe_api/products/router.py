"""Standard catalog writes and dedicated, dual-control tracking-mode actions."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from yihe_api.audit.service import Actor, Category, audit_change
from yihe_api.auth.router import DB
from yihe_api.errors import ApiError, ErrorCode
from yihe_api.idempotency.service import FinalFailure, Operation, Success
from yihe_api.master_data.router import Idempotency, dispatch
from yihe_api.product_categories.router import load
from yihe_api.products import service
from yihe_api.products.models import (
    LegacyProductMap,
    ProductBrand,
    ProductMaster,
    ProductSKU,
    ProductSPU,
    ProductUnit,
    TrackingModeChange,
)
from yihe_api.products.schemas import (
    CategoryDefaults,
    DirectoryCreate,
    DirectoryUpdate,
    LegacyCreate,
    LegacyReview,
    ProductVersion,
    SKUCreate,
    SKUUpdate,
    SPUCreate,
    SPUUpdate,
    TrackingApproval,
    TrackingRequest,
)
from yihe_api.products.tracking import has_persisted_product_transactions, lock_product_sku
from yihe_api.rbac.policy import Principal, assert_separation_of_duties
from yihe_api.rbac.router import Current

router = APIRouter(prefix="/products", tags=["products"])
PATH = "/api/v1/products"
DIRECTORIES: dict[str, type[ProductBrand] | type[ProductUnit]] = {
    "brands": ProductBrand,
    "units": ProductUnit,
}
ITEMS: dict[str, type[ProductSPU] | type[ProductSKU]] = {"spus": ProductSPU, "skus": ProductSKU}


def require(principal: Principal, action: str) -> None:
    principal.require(f"product.{action}", company=action != "view")


def fields(row: ProductMaster | TrackingModeChange | LegacyProductMap) -> dict[str, Any]:
    output = {column.key: getattr(row, column.key) for column in inspect(type(row)).columns}
    return {
        key: format(value, "f") if isinstance(value, Decimal) else value
        for key, value in output.items()
    }


def audit(
    db: Session,
    request: Request,
    actor: Actor,
    kind: str,
    id: UUID,
    action: str,
    *,
    metadata: dict[str, object] | None = None,
    reason: str | None = None,
) -> None:
    audit_change(
        db,
        request=request,
        actor=actor,
        company_id=actor.company_id,
        category=Category.MASTER_DATA,
        action=f"{kind}.{action}",
        entity_type=kind,
        entity_id=id,
        metadata=metadata,
        reason=reason,
    )


def execute(
    db: Session,
    request: Request,
    action: str,
    kind: str,
    path: str,
    payload: dict[str, Any],
    work: Any,
    id: UUID | None = None,
) -> Response:
    if "attributes" in payload:
        attributes = payload.pop("attributes")
        try:
            encoded = json.dumps(attributes, sort_keys=True, allow_nan=False).encode()
        except (ValueError, RecursionError):
            raise ApiError(ErrorCode.PRODUCT_ATTRIBUTES_INVALID) from None
        if len(encoded) > 16384:
            raise ApiError(ErrorCode.PRODUCT_ATTRIBUTES_INVALID)
        payload["attribute_sha256"] = hashlib.sha256(encoded).hexdigest()
    return dispatch(db, request, Operation(f"{kind}.{action}", kind, id, path), payload, work)


def check_version(
    row: ProductMaster | TrackingModeChange | LegacyProductMap, payload: ProductVersion
) -> None:
    if row.version_no != payload.version_no:
        raise FinalFailure(ErrorCode.VERSION_CONFLICT)


def stamp(row: ProductMaster | TrackingModeChange | LegacyProductMap, actor: Actor) -> None:
    row.version_no += 1
    row.updated_by = actor.user_id


@router.get("/brands", operation_id="listProductBrands")
@router.get("/units", operation_id="listProductUnits")
@router.get("/spus", operation_id="listProductSpus")
@router.get("/skus", operation_id="listProductSkus")
def list_catalog(
    request: Request, db: DB, principal: Current, offset: int = 0, limit: int = 50
) -> list[dict[str, Any]]:
    require(principal, "view")
    table = request.url.path.rsplit("/", 1)[-1]
    model = DIRECTORIES[table] if table in DIRECTORIES else ITEMS[table]
    return [
        fields(row)
        for row in db.scalars(
            select(model)
            .where(
                model.company_id == principal.user.company_id,
                model.is_deleted.is_(False),
            )
            .order_by(model.id)
            .offset(max(offset, 0))
            .limit(max(1, min(limit, 200)))
        )
    ]


@router.post("/brands", status_code=201, operation_id="createProductBrand")
@router.post("/units", status_code=201, operation_id="createProductUnit")
def create_directory(
    payload: DirectoryCreate, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    require(principal, "create")
    table = request.url.path.rsplit("/", 1)[-1]
    model = DIRECTORIES[table]

    def work(tx: Session, actor: Actor) -> Success:
        row = model(
            company_id=actor.company_id,
            created_by=actor.user_id,
            updated_by=actor.user_id,
            **payload.model_dump(),
        )
        tx.add(row)
        service.flush(tx)
        audit(tx, request, actor, model.__tablename__, row.id, "created")
        return Success(model.__tablename__, row.id, status_code=201)

    return execute(
        db,
        request,
        "create",
        model.__tablename__,
        f"{PATH}/{table}",
        payload.model_dump(mode="json"),
        work,
    )


@router.patch("/brands/{id}", operation_id="updateProductBrand")
@router.patch("/units/{id}", operation_id="updateProductUnit")
def update_directory(
    id: UUID,
    payload: DirectoryUpdate,
    request: Request,
    db: DB,
    principal: Current,
    key: Idempotency,
) -> Response:
    require(principal, "edit")
    table = request.url.path.split("/")[-2]
    model = DIRECTORIES[table]

    def work(tx: Session, actor: Actor) -> Success:
        row = service.get(tx, model, actor.company_id, id, lock=True)
        check_version(row, payload)
        row.name = payload.name
        stamp(row, actor)
        audit(tx, request, actor, model.__tablename__, id, "updated")
        return Success(model.__tablename__, id)

    return execute(
        db,
        request,
        "edit",
        model.__tablename__,
        f"{PATH}/{table}/{{id}}",
        payload.model_dump(mode="json"),
        work,
        id,
    )


@router.post("/spus", status_code=201, operation_id="createProductSpu")
def create_spu(
    payload: SPUCreate, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    require(principal, "create")

    def work(tx: Session, actor: Actor) -> Success:
        row = service.create_spu(tx, actor.company_id, actor.user_id, payload)
        audit(tx, request, actor, "product_spu", row.id, "created")
        return Success("product_spu", row.id, status_code=201)

    return execute(
        db, request, "create", "product_spu", f"{PATH}/spus", payload.model_dump(mode="json"), work
    )


@router.post("/skus", status_code=201, operation_id="createProductSku")
def create_sku(
    payload: SKUCreate, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    require(principal, "create")

    def work(tx: Session, actor: Actor) -> Success:
        row = service.create_sku(tx, actor.company_id, actor.user_id, payload)
        audit(tx, request, actor, "product_sku", row.id, "created")
        return Success("product_sku", row.id, status_code=201)

    return execute(
        db, request, "create", "product_sku", f"{PATH}/skus", payload.model_dump(mode="json"), work
    )


@router.get("/spus/{id}", operation_id="getProductSpu")
@router.get("/skus/{id}", operation_id="getProductSku")
def detail(id: UUID, request: Request, db: DB, principal: Current) -> dict[str, Any]:
    require(principal, "view")
    model = ITEMS[request.url.path.split("/")[-2]]
    return fields(service.get(db, model, principal.user.company_id, id))


@router.patch("/spus/{id}", operation_id="updateProductSpu")
def update_spu(
    id: UUID, payload: SPUUpdate, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    require(principal, "edit")

    def work(tx: Session, actor: Actor) -> Success:
        row = service.get(tx, ProductSPU, actor.company_id, id, lock=True)
        check_version(row, payload)
        for field, value in payload.model_dump(exclude={"version_no"}, exclude_unset=True).items():
            setattr(row, field, value)
        stamp(row, actor)
        audit(tx, request, actor, "product_spu", id, "updated")
        return Success("product_spu", id)

    return execute(
        db,
        request,
        "edit",
        "product_spu",
        f"{PATH}/spus/{{id}}",
        payload.model_dump(mode="json"),
        work,
        id,
    )


@router.patch("/skus/{id}", operation_id="updateProductSku")
def update_sku(
    id: UUID, payload: SKUUpdate, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    require(principal, "edit")

    def work(tx: Session, actor: Actor) -> Success:
        row = lock_product_sku(tx, actor.company_id, id)
        check_version(row, payload)
        spu = service.get(tx, ProductSPU, actor.company_id, row.spu_id)
        category = service.availability(tx, actor.company_id, spu.category_id)
        service.ensure_sku_attributes(tx, actor.company_id, category, payload.attributes)
        for field, value in payload.model_dump(exclude={"version_no"}, exclude_unset=True).items():
            setattr(row, field, value)
        stamp(row, actor)
        service.flush(tx)
        audit(tx, request, actor, "product_sku", id, "updated")
        return Success("product_sku", id)

    return execute(
        db,
        request,
        "edit",
        "product_sku",
        f"{PATH}/skus/{{id}}",
        payload.model_dump(mode="json"),
        work,
        id,
    )


@router.post("/spus/{id}/enable", operation_id="enableProductSpu")
@router.post("/spus/{id}/disable", operation_id="disableProductSpu")
@router.post("/skus/{id}/enable", operation_id="enableProductSku")
@router.post("/skus/{id}/disable", operation_id="disableProductSku")
def set_active(
    id: UUID,
    payload: ProductVersion,
    request: Request,
    db: DB,
    principal: Current,
    key: Idempotency,
) -> Response:
    require(principal, "disable")
    table, _, action = request.url.path.split("/")[-3:]
    model = ITEMS[table]

    def work(tx: Session, actor: Actor) -> Success:
        row = service.get(tx, model, actor.company_id, id, lock=True)
        check_version(row, payload)
        row.is_active = action == "enable"
        stamp(row, actor)
        audit(
            tx, request, actor, model.__tablename__, id, "enabled" if row.is_active else "disabled"
        )
        return Success(model.__tablename__, id)

    return execute(
        db,
        request,
        action,
        model.__tablename__,
        f"{PATH}/{table}/{{id}}/{action}",
        payload.model_dump(mode="json"),
        work,
        id,
    )


@router.patch("/category-defaults/{id}", operation_id="updateProductCategoryDefaults")
def defaults(
    id: UUID,
    payload: CategoryDefaults,
    request: Request,
    db: DB,
    principal: Current,
    key: Idempotency,
) -> Response:
    require(principal, "edit")
    principal.require("product_category.edit", company=True)

    def work(tx: Session, actor: Actor) -> Success:
        rows = load(tx, actor.company_id, lock=True)
        row = rows.get(id)
        if row is None:
            raise ApiError(ErrorCode.NOT_FOUND)
        if row.version_no != payload.version_no:
            raise FinalFailure(ErrorCode.VERSION_CONFLICT)
        if (
            payload.unit_id
            and not service.get(tx, ProductUnit, actor.company_id, payload.unit_id).is_active
        ):
            raise FinalFailure(ErrorCode.PRODUCT_UNIT_REQUIRED)
        row.default_unit_id, row.default_tax_rate, row.default_tracking_mode = (
            payload.unit_id,
            payload.tax_rate,
            payload.tracking_mode,
        )
        row.updated_by = actor.user_id
        row.version_no += 1
        audit(tx, request, actor, "product_category", id, "defaults_updated")
        return Success("product_category", id)

    return execute(
        db,
        request,
        "defaults",
        "product_category",
        f"{PATH}/category-defaults/{{id}}",
        payload.model_dump(mode="json"),
        work,
        id,
    )


@router.post(
    "/skus/{id}/change-tracking-mode", status_code=201, operation_id="requestSkuTrackingMode"
)
def request_tracking(
    id: UUID,
    payload: TrackingRequest,
    request: Request,
    db: DB,
    principal: Current,
    key: Idempotency,
) -> Response:
    require(principal, "tracking_mode.change")

    def work(tx: Session, actor: Actor) -> Success:
        row = lock_product_sku(tx, actor.company_id, id)
        check_version(row, payload)
        if has_persisted_product_transactions(tx, row):
            raise FinalFailure(ErrorCode.PRODUCT_TRACKING_MODE_LOCKED)
        if row.tracking_mode == payload.tracking_mode:
            raise FinalFailure(ErrorCode.CONFLICT)
        change = TrackingModeChange(
            company_id=actor.company_id,
            sku_id=id,
            from_mode=row.tracking_mode,
            to_mode=payload.tracking_mode,
            sku_version=row.version_no,
            reason=payload.reason,
            created_by=actor.user_id,
            updated_by=actor.user_id,
        )
        tx.add(change)
        service.flush(tx)
        audit(
            tx,
            request,
            actor,
            "product_sku",
            id,
            "tracking_mode_change_requested",
            metadata={
                "related_id": change.id,
                "tracking_mode_before": change.from_mode,
                "tracking_mode_after": change.to_mode,
                "maker_id": actor.user_id,
            },
        )
        return Success("product_tracking_mode_change", change.id, status_code=201)

    return execute(
        db,
        request,
        "request_mode",
        "product_sku",
        f"{PATH}/skus/{{id}}/change-tracking-mode",
        payload.model_dump(mode="json"),
        work,
        id,
    )


@router.post("/skus/{id}/change-tracking-mode/approve", operation_id="approveSkuTrackingMode")
def approve_tracking(
    id: UUID,
    payload: TrackingApproval,
    request: Request,
    db: DB,
    principal: Current,
    key: Idempotency,
) -> Response:
    require(principal, "tracking_mode.approve")

    def work(tx: Session, actor: Actor) -> Success:
        row = lock_product_sku(tx, actor.company_id, id)
        check_version(row, payload)
        change = service.get(
            tx, TrackingModeChange, actor.company_id, payload.request_id, lock=True
        )
        if change.sku_id != id or change.approved_at or change.sku_version != row.version_no:
            raise FinalFailure(ErrorCode.CONFLICT)
        assert_separation_of_duties(
            change.created_by, actor.user_id, "product.tracking_mode.approve"
        )
        if has_persisted_product_transactions(tx, row):
            raise FinalFailure(ErrorCode.PRODUCT_TRACKING_MODE_LOCKED)
        row.tracking_mode = change.to_mode
        change.approved_by, change.approved_at = actor.user_id, datetime.now(UTC)
        stamp(row, actor)
        stamp(change, actor)
        audit(
            tx,
            request,
            actor,
            "product_sku",
            id,
            "tracking_mode_changed",
            metadata={
                "related_id": change.id,
                "tracking_mode_before": change.from_mode,
                "tracking_mode_after": change.to_mode,
                "maker_id": change.created_by,
                "approver_id": actor.user_id,
            },
        )
        return Success("product_sku", id)

    return execute(
        db,
        request,
        "approve_mode",
        "product_sku",
        f"{PATH}/skus/{{id}}/change-tracking-mode/approve",
        payload.model_dump(mode="json"),
        work,
        id,
    )


@router.get("/skus/{id}/change-tracking-mode", operation_id="listSkuTrackingRequests")
def list_tracking(id: UUID, db: DB, principal: Current) -> list[dict[str, Any]]:
    require(principal, "view")
    require(
        principal, "tracking_mode.change"
    ) if "product.tracking_mode.change" in principal.grants else require(
        principal, "tracking_mode.approve"
    )
    service.get(db, ProductSKU, principal.user.company_id, id)
    return [
        fields(row)
        for row in db.scalars(
            select(TrackingModeChange)
            .where(
                TrackingModeChange.company_id == principal.user.company_id,
                TrackingModeChange.sku_id == id,
            )
            .order_by(TrackingModeChange.created_at.desc())
            .limit(100)
        )
    ]


@router.get("/legacy-mappings", operation_id="listLegacyProductMappings")
def list_legacy(db: DB, principal: Current) -> list[dict[str, Any]]:
    require(principal, "legacy_mapping.manage")
    return [
        fields(row)
        for row in db.scalars(
            select(LegacyProductMap)
            .where(LegacyProductMap.company_id == principal.user.company_id)
            .order_by(LegacyProductMap.created_at.desc(), LegacyProductMap.id)
            .limit(200)
        )
    ]


@router.post("/legacy-mappings", operation_id="createLegacyProductMapping", status_code=201)
def create_legacy(
    payload: LegacyCreate, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    require(principal, "legacy_mapping.manage")

    def work(tx: Session, actor: Actor) -> Success:
        sku = (
            service.get(tx, ProductSKU, actor.company_id, payload.target_sku_id)
            if payload.target_sku_id
            else None
        )
        row = LegacyProductMap(
            company_id=actor.company_id,
            target_spu_id=sku.spu_id if sku else None,
            mapping_status="SUGGESTED" if sku else "UNMAPPED",
            created_by=actor.user_id,
            updated_by=actor.user_id,
            **payload.model_dump(),
        )
        tx.add(row)
        service.flush(tx)
        audit(tx, request, actor, "product", row.id, "legacy_mapping_created")
        return Success("legacy_product_map", row.id, status_code=201)

    return execute(
        db,
        request,
        "legacy_create",
        "product",
        f"{PATH}/legacy-mappings",
        payload.model_dump(mode="json"),
        work,
    )


@router.post("/legacy-mappings/{id}/review", operation_id="reviewLegacyProductMapping")
def review_legacy(
    id: UUID, payload: LegacyReview, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    require(principal, "legacy_mapping.manage")

    def work(tx: Session, actor: Actor) -> Success:
        row = service.get(tx, LegacyProductMap, actor.company_id, id, lock=True)
        check_version(row, payload)
        if row.mapping_status in {"CONFIRMED", "RETIRED"}:
            raise FinalFailure(ErrorCode.CONFLICT)
        if payload.status == "CONFIRMED":
            if payload.target_sku_id is None:
                raise FinalFailure(ErrorCode.VALIDATION_ERROR)
            sku = service.get(tx, ProductSKU, actor.company_id, payload.target_sku_id)
            if not sku.is_active:
                raise FinalFailure(ErrorCode.CONFLICT)
            row.target_sku_id, row.target_spu_id = sku.id, sku.spu_id
            row.match_method = "MANUAL"
        row.mapping_status = payload.status
        row.reason = payload.reason
        row.approved_by, row.approved_at = actor.user_id, datetime.now(UTC)
        stamp(row, actor)
        audit(
            tx,
            request,
            actor,
            "product",
            id,
            f"legacy_mapping_{payload.status.lower()}",
            metadata={"related_id": row.target_sku_id, "approver_id": actor.user_id},
        )
        return Success("legacy_product_map", id)

    return execute(
        db,
        request,
        "legacy_review",
        "product",
        f"{PATH}/legacy-mappings/{{id}}/review",
        payload.model_dump(mode="json"),
        work,
        id,
    )


@router.post("/spus/{id}/approve", operation_id="approveProductSpu")
def approve_spu(
    id: UUID,
    payload: ProductVersion,
    request: Request,
    db: DB,
    principal: Current,
    key: Idempotency,
) -> Response:
    require(principal, "catalog.approve")

    def work(tx: Session, actor: Actor) -> Success:
        row = service.get(tx, ProductSPU, actor.company_id, id, lock=True)
        check_version(row, payload)
        if row.approved_at is not None:
            raise FinalFailure(ErrorCode.CONFLICT)
        if row.created_by == actor.user_id:
            raise FinalFailure(ErrorCode.SEPARATION_OF_DUTIES)
        row.approved_by, row.approved_at = actor.user_id, datetime.now(UTC)
        stamp(row, actor)
        audit(
            tx,
            request,
            actor,
            "product_spu",
            id,
            "approved",
            metadata={"maker_id": row.created_by, "approver_id": actor.user_id},
        )
        return Success("product_spu", id)

    return execute(
        db,
        request,
        "approve",
        "product_spu",
        f"{PATH}/spus/{{id}}/approve",
        payload.model_dump(mode="json"),
        work,
        id,
    )


class LegacyMappingImport(BaseModel):
    rows: list[LegacyCreate] = Field(min_length=1, max_length=100)


@router.post("/legacy-mappings/import", operation_id="importLegacyProductMappings")
def import_legacy(
    payload: LegacyMappingImport, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    require(principal, "legacy_mapping.manage")

    def work(tx: Session, actor: Actor) -> Success:
        for item in payload.rows:
            sku = (
                service.get(tx, ProductSKU, actor.company_id, item.target_sku_id)
                if item.target_sku_id
                else None
            )
            row = LegacyProductMap(
                company_id=actor.company_id,
                target_spu_id=sku.spu_id if sku else None,
                mapping_status="SUGGESTED" if sku else "UNMAPPED",
                created_by=actor.user_id,
                updated_by=actor.user_id,
                **item.model_dump(),
            )
            tx.add(row)
            service.flush(tx)
            audit(tx, request, actor, "product", row.id, "legacy_mapping_created")
        return Success("legacy_product_map", actor.company_id)

    return execute(
        db,
        request,
        "legacy_import",
        "product",
        f"{PATH}/legacy-mappings/import",
        payload.model_dump(mode="json"),
        work,
    )
