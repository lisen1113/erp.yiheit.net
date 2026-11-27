"""Company-scoped category hierarchy and its locally owned attribute definitions."""

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
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


class ProductCategory(IdentityMixin, CompanyScopeMixin, AuditMixin, SoftDeleteMixin, Base):
    __tablename__ = "product_category"
    __table_args__ = (
        UniqueConstraint("company_id", "id"),
        UniqueConstraint("company_id", "code"),
        UniqueConstraint(
            "company_id", "parent_id", "normalized_name", postgresql_nulls_not_distinct=True
        ),
        ForeignKeyConstraint(
            ["company_id", "parent_id"],
            ["product_category.company_id", "product_category.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "default_unit_id"],
            ["product_unit.company_id", "product_unit.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("default_tax_rate BETWEEN 0 AND 1", name="default_tax_rate"),
        CheckConstraint(
            "default_tracking_mode IN ('quantity', 'serial', 'batch', 'non_stock')",
            name="default_tracking_mode",
        ),
        CheckConstraint("parent_id IS DISTINCT FROM id", name="not_own_parent"),
        CheckConstraint("level >= 0 AND sort_order >= 0 AND schema_version >= 1", name="bounds"),
        Index("ix_product_category_attribute_schema", "attribute_schema", postgresql_using="gin"),
    )

    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(100))
    normalized_name: Mapped[str] = mapped_column(String(300))
    parent_id: Mapped[UUID | None] = mapped_column(index=True)
    level: Mapped[int] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, server_default="0")
    description: Mapped[str | None] = mapped_column(String(500))
    attribute_schema: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    schema_version: Mapped[int] = mapped_column(Integer, server_default="1")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    is_synthetic: Mapped[bool] = mapped_column(Boolean, server_default=false())
    default_unit_id: Mapped[UUID | None]
    default_tax_rate: Mapped[Decimal | None] = mapped_column(TAX_RATE)
    default_tracking_mode: Mapped[str | None] = mapped_column(String(20))
