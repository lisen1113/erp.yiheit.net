"""Standard product master data, without inventory or trading facts."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yihe_api.db.models import (
    TAX_RATE,
    AuditMixin,
    Base,
    CompanyScopeMixin,
    IdentityMixin,
    SoftDeleteMixin,
)

MODE_CHECK = "tracking_mode IN ('quantity', 'serial', 'batch', 'non_stock')"


def reference(field: str, table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["company_id", field], [f"{table}.company_id", f"{table}.id"], ondelete="RESTRICT"
    )


class ProductMaster(IdentityMixin, CompanyScopeMixin, AuditMixin, SoftDeleteMixin):
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    is_synthetic: Mapped[bool] = mapped_column(Boolean, server_default=false())


class ProductBrand(ProductMaster, Base):
    __tablename__ = "product_brand"
    __table_args__ = (UniqueConstraint("company_id", "id"), UniqueConstraint("company_id", "code"))
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(100))


class ProductUnit(ProductMaster, Base):
    __tablename__ = "product_unit"
    __table_args__ = (UniqueConstraint("company_id", "id"), UniqueConstraint("company_id", "code"))
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(100))


class ProductSPU(ProductMaster, Base):
    __tablename__ = "product_spu"
    __table_args__ = (
        UniqueConstraint("company_id", "id"),
        UniqueConstraint("company_id", "spu_no"),
        UniqueConstraint("company_id", "id", "brand_id"),
        reference("category_id", "product_category"),
        reference("brand_id", "product_brand"),
        CheckConstraint(
            "product_type IN ('physical', 'software', 'service', 'subscription', 'lease')",
            name="product_type",
        ),
    )
    spu_no: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200), index=True)
    name_en: Mapped[str | None] = mapped_column(String(200))
    category_id: Mapped[UUID] = mapped_column(index=True)
    brand_id: Mapped[UUID | None]
    product_type: Mapped[str] = mapped_column(String(20), server_default="physical")
    description: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[UUID | None]
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProductSKU(ProductMaster, Base):
    __tablename__ = "product_sku"
    __table_args__ = (
        UniqueConstraint("company_id", "id"),
        UniqueConstraint("company_id", "sku_no"),
        UniqueConstraint("company_id", "barcode"),
        UniqueConstraint("company_id", "brand_id", "manufacturer_part_no"),
        ForeignKeyConstraint(
            ["company_id", "spu_id", "brand_id"],
            ["product_spu.company_id", "product_spu.id", "product_spu.brand_id"],
            ondelete="RESTRICT",
        ),
        reference("brand_id", "product_brand"),
        reference("unit_id", "product_unit"),
        CheckConstraint(MODE_CHECK, name="tracking_mode"),
        CheckConstraint("default_tax_rate BETWEEN 0 AND 1", name="default_tax_rate"),
        Index("ix_product_sku_attributes", "attributes", postgresql_using="gin"),
    )
    sku_no: Mapped[str] = mapped_column(String(64))
    spu_id: Mapped[UUID] = mapped_column(index=True)
    brand_id: Mapped[UUID]
    manufacturer_part_no: Mapped[str | None] = mapped_column(String(128))
    spec_name: Mapped[str] = mapped_column(String(255))
    approved_name_cn: Mapped[str] = mapped_column(String(200))
    approved_name_en: Mapped[str | None] = mapped_column(String(200))
    unit_id: Mapped[UUID]
    default_tax_rate: Mapped[Decimal] = mapped_column(TAX_RATE, server_default=text("0.13"))
    tracking_mode: Mapped[str] = mapped_column(String(20), server_default="quantity", index=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    barcode: Mapped[str | None] = mapped_column(String(64))
    allow_purchase: Mapped[bool] = mapped_column(Boolean, server_default=true())
    allow_sale: Mapped[bool] = mapped_column(Boolean, server_default=true())


class TrackingModeChange(IdentityMixin, CompanyScopeMixin, AuditMixin, Base):
    __tablename__ = "product_tracking_mode_change"
    __table_args__ = (
        reference("sku_id", "product_sku"),
        CheckConstraint(
            "from_mode IN ('quantity', 'serial', 'batch', 'non_stock')", name="from_mode"
        ),
        CheckConstraint("to_mode IN ('quantity', 'serial', 'batch', 'non_stock')", name="to_mode"),
        CheckConstraint("from_mode <> to_mode", name="different_modes"),
        CheckConstraint("approved_by IS DISTINCT FROM created_by", name="separate_approver"),
        CheckConstraint("(approved_by IS NULL) = (approved_at IS NULL)", name="approval_pair"),
    )
    sku_id: Mapped[UUID] = mapped_column(index=True)
    from_mode: Mapped[str] = mapped_column(String(20))
    to_mode: Mapped[str] = mapped_column(String(20))
    sku_version: Mapped[int]
    reason: Mapped[str] = mapped_column(String(500))
    approved_by: Mapped[UUID | None]
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LegacyProductMap(IdentityMixin, CompanyScopeMixin, AuditMixin, Base):
    __tablename__ = "legacy_product_map"
    __table_args__ = (
        UniqueConstraint("company_id", "legacy_system", "legacy_product_id"),
        reference("target_sku_id", "product_sku"),
        reference("target_spu_id", "product_spu"),
        CheckConstraint(
            "mapping_status IN ('UNMAPPED', 'SUGGESTED', 'CONFIRMED', 'REJECTED', 'RETIRED')",
            name="mapping_status",
        ),
        CheckConstraint(
            "mapping_status <> 'CONFIRMED' OR "
            "(target_sku_id IS NOT NULL AND approved_by IS NOT NULL)",
            name="confirmed_review",
        ),
    )
    legacy_system: Mapped[str] = mapped_column(String(64))
    legacy_product_id: Mapped[str] = mapped_column(String(128))
    legacy_name: Mapped[str] = mapped_column(String(255))
    legacy_category: Mapped[str | None] = mapped_column(String(255))
    target_sku_id: Mapped[UUID | None] = mapped_column(index=True)
    target_spu_id: Mapped[UUID | None]
    mapping_status: Mapped[str] = mapped_column(String(16), server_default="UNMAPPED")
    match_method: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[Decimal | None] = mapped_column(TAX_RATE)
    reason: Mapped[str | None] = mapped_column(String(500))
    approved_by: Mapped[UUID | None]
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
