"""Company-scoped product operations and shared category inheritance."""

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from yihe_api.errors import ApiError, ErrorCode
from yihe_api.idempotency.service import FinalFailure
from yihe_api.product_categories.models import ProductCategory
from yihe_api.product_categories.router import (
    assert_category_available_for_new_product,
    lineage,
    load,
    rules,
)
from yihe_api.product_categories.schema import validate_attributes
from yihe_api.products.models import (
    LegacyProductMap,
    ProductBrand,
    ProductMaster,
    ProductSKU,
    ProductSPU,
    ProductUnit,
    TrackingModeChange,
)
from yihe_api.products.schemas import SKUCreate, SPUCreate


def get[Row: ProductMaster | TrackingModeChange | LegacyProductMap](
    db: Session,
    model: type[Row],
    company: UUID,
    id: UUID,
    *,
    lock: bool = False,
) -> Row:
    statement = select(model).where(model.company_id == company, model.id == id)
    if issubclass(model, ProductMaster):
        statement = statement.where(model.is_deleted.is_(False))
    if lock:
        statement = statement.with_for_update(key_share=True).execution_options(
            populate_existing=True
        )
    record = db.scalar(statement)
    if record is None:
        raise ApiError(ErrorCode.NOT_FOUND)
    return record


def flush(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) != "23505":
            raise
        name = getattr(getattr(exc.orig, "diag", None), "constraint_name", "")
        code = (
            ErrorCode.PRODUCT_CODE_CONFLICT
            if name in {"uq_product_spu_company_id_spu_no", "uq_product_sku_company_id_sku_no"}
            else ErrorCode.CONFLICT
        )
        raise FinalFailure(code) from None


def availability(db: Session, company: UUID, category_id: UUID) -> ProductCategory:
    category = assert_category_available_for_new_product(db, company, category_id)
    rows = load(db, company)
    if any(item.parent_id == category_id for item in rows.values()):
        raise FinalFailure(ErrorCode.PRODUCT_CATEGORY_NOT_TRADABLE)
    return category


def ensure_sku_attributes(
    db: Session, company: UUID, category: ProductCategory, payload: dict[str, Any]
) -> None:
    rows = load(db, company)
    if validate_attributes(rules(lineage(rows, category)), payload):
        raise FinalFailure(ErrorCode.PRODUCT_ATTRIBUTES_INVALID)


def create_spu(
    db: Session, company: UUID, actor: UUID, payload: SPUCreate, *, id: UUID | None = None
) -> ProductSPU:
    availability(db, company, payload.category_id)
    if payload.brand_id and not get(db, ProductBrand, company, payload.brand_id).is_active:
        raise FinalFailure(ErrorCode.CONFLICT)
    row = ProductSPU(
        id=id or uuid4(),
        company_id=company,
        created_by=actor,
        updated_by=actor,
        **payload.model_dump(),
    )
    db.add(row)
    flush(db)
    return row


def create_sku(
    db: Session, company: UUID, actor: UUID, payload: SKUCreate, *, id: UUID | None = None
) -> ProductSKU:
    spu = get(db, ProductSPU, company, payload.spu_id)
    category = availability(db, company, spu.category_id)
    if (
        not spu.is_active
        or spu.approved_at is None
        or spu.brand_id is None
        or not get(db, ProductBrand, company, spu.brand_id).is_active
    ):
        raise FinalFailure(ErrorCode.CONFLICT)
    ensure_sku_attributes(db, company, category, payload.attributes)
    unit_id = payload.unit_id or category.default_unit_id
    if unit_id is None or not get(db, ProductUnit, company, unit_id).is_active:
        raise FinalFailure(ErrorCode.PRODUCT_UNIT_REQUIRED)
    values = payload.model_dump(exclude={"unit_id", "default_tax_rate", "tracking_mode"})
    rate = payload.default_tax_rate
    if rate is None:
        rate = (
            category.default_tax_rate if category.default_tax_rate is not None else Decimal("0.13")
        )
    row = ProductSKU(
        id=id or uuid4(),
        company_id=company,
        created_by=actor,
        updated_by=actor,
        brand_id=spu.brand_id,
        unit_id=unit_id,
        default_tax_rate=rate,
        tracking_mode=payload.tracking_mode or category.default_tracking_mode or "quantity",
        **values,
    )
    db.add(row)
    flush(db)
    return row
