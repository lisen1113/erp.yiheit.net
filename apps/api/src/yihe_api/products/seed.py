"""Fixed synthetic catalog covering the four tracking modes."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from yihe_api.products.models import ProductBrand, ProductSKU, ProductSPU, ProductUnit
from yihe_api.products.schemas import SKUCreate, SPUCreate
from yihe_api.products.service import create_sku, create_spu

ITEMS: tuple[tuple[str, str, str, str, str, dict[str, Any]], ...] = (
    ("NOTEBOOK", "合成商用笔记本", "COMPUTE-1", "physical", "serial", {"memory_gb": 16}),
    ("SURFACE-STYLE", "合成平板笔记本", "COMPUTE-1", "physical", "serial", {"memory_gb": 32}),
    ("TABLET", "合成平板", "COMPUTE-4", "physical", "serial", {}),
    ("WORKSTATION", "合成工作站", "COMPUTE-3", "physical", "serial", {}),
    ("SERVER", "合成机架服务器", "DATACENTER-1", "physical", "serial", {"rack_units": 2}),
    ("KEYBOARD", "合成键盘", "PERIPHERALS-2", "physical", "quantity", {}),
    ("MOUSE", "合成鼠标", "PERIPHERALS-2", "physical", "quantity", {}),
    ("POWER", "合成电源", "PERIPHERALS-4", "physical", "quantity", {}),
    ("CABLE", "合成线材", "PERIPHERALS-4", "physical", "quantity", {}),
    ("BATCH-CABLE", "合成批次线材", "PERIPHERALS-4", "physical", "batch", {}),
    (
        "M365-TEST",
        "合成 Microsoft 365 订阅",
        "SOFTWARE-2",
        "subscription",
        "non_stock",
        {"billing_cycle": "annual"},
    ),
    (
        "COPILOT-TEST",
        "合成 Copilot 订阅",
        "SOFTWARE-2",
        "subscription",
        "non_stock",
        {"billing_cycle": "monthly"},
    ),
    ("INSTALL", "合成安装服务", "SERVICES-1", "service", "non_stock", {}),
    ("SUPPORT", "合成技术支持", "SERVICES-2", "service", "non_stock", {}),
)


def seed_products(db: Session, company: UUID, actor: UUID) -> None:
    from yihe_api.dev_seed import seed_id

    brand_id = seed_id("product_brand", "SYNTHETIC")
    unit_id = seed_id("product_unit", "EA")
    for model, id, code, name in (
        (ProductBrand, brand_id, "SYNTHETIC", "合成商品品牌"),
        (ProductUnit, unit_id, "EA", "件"),
    ):
        row = db.get(model, id)
        if row is None:
            db.add(
                model(
                    id=id,
                    company_id=company,
                    code=code,
                    name=name,
                    is_synthetic=True,
                    created_by=actor,
                    updated_by=actor,
                )
            )
        elif row.company_id != company or not row.is_synthetic or row.created_by != actor:
            raise ValueError("Fixed product directory ID is not owned by seed")
    db.flush()
    for code, name, category, product_type, mode, attributes in ITEMS:
        spu = db.scalar(
            select(ProductSPU).where(ProductSPU.company_id == company, ProductSPU.spu_no == code)
        )
        if spu is None:
            spu = create_spu(
                db,
                company,
                actor,
                SPUCreate.model_validate(
                    {
                        "spu_no": code,
                        "name": name,
                        "category_id": seed_id("product_category", category),
                        "brand_id": brand_id,
                        "product_type": product_type,
                    }
                ),
                id=seed_id("product_spu", code),
            )
            spu.is_synthetic = True
            spu.approved_by = actor
            spu.approved_at = datetime(2026, 1, 1, tzinfo=UTC)
        sku = db.scalar(
            select(ProductSKU).where(ProductSKU.company_id == company, ProductSKU.sku_no == code)
        )
        if sku is None:
            sku = create_sku(
                db,
                company,
                actor,
                SKUCreate.model_validate(
                    {
                        "sku_no": code,
                        "spu_id": spu.id,
                        "spec_name": name,
                        "approved_name_cn": name,
                        "unit_id": unit_id,
                        "tracking_mode": mode,
                        "attributes": attributes,
                        "default_tax_rate": Decimal("0.13"),
                    }
                ),
                id=seed_id("product_sku", code),
            )
            sku.is_synthetic = True
        for row in (spu, sku):
            if not row.is_synthetic or row.created_by != actor:
                raise ValueError("Product seed conflicts with non-seed master data")
    db.flush()


def reset_products(db: Session, company: UUID, actor: UUID) -> None:
    from yihe_api.products.models import LegacyProductMap, TrackingModeChange

    for protected in (LegacyProductMap, TrackingModeChange):
        if db.scalar(select(protected.id).where(protected.company_id == company).limit(1)):
            raise ValueError("Reset cannot remove product reviews")
    for table in (ProductSKU, ProductSPU, ProductUnit, ProductBrand):
        for row in db.scalars(select(table).where(table.company_id == company)):
            if not row.is_synthetic or row.created_by != actor or row.version_no != 1:
                raise ValueError("Reset cannot remove non-seed or edited product data")
            db.delete(row)
        db.flush()
