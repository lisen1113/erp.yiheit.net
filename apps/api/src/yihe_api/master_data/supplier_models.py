"""Supplier-specific profile, keyed to the existing partner identity."""

from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from yihe_api.db.models import AuditMixin, Base, CompanyScopeMixin, IdentityMixin, SoftDeleteMixin


class SupplierProfile(IdentityMixin, CompanyScopeMixin, AuditMixin, SoftDeleteMixin, Base):
    __tablename__ = "supplier_profile"
    __table_args__ = (
        CheckConstraint(
            "supplier_type IN ('DISTRIBUTOR', 'HARDWARE', 'SOFTWARE_SERVICE', 'GENERAL')",
            name="supplier_type",
        ),
        CheckConstraint("payment_term_days >= 0", name="payment_term_days"),
        UniqueConstraint("company_id", "supplier_no"),
        UniqueConstraint("company_id", "business_partner_id"),
        ForeignKeyConstraint(
            ["company_id", "business_partner_id"],
            ["business_partner.company_id", "business_partner.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "buyer_id"],
            ["employee.company_id", "employee.id"],
            ondelete="RESTRICT",
        ),
    )

    business_partner_id: Mapped[UUID]
    supplier_no: Mapped[str] = mapped_column(String(32))
    supplier_type: Mapped[str] = mapped_column(String(32), server_default="GENERAL")
    buyer_id: Mapped[UUID] = mapped_column(index=True)
    payment_term_days: Mapped[int] = mapped_column(Integer, server_default="0")
    default_contact_name: Mapped[str | None] = mapped_column(String(120))
    default_contact_phone: Mapped[str | None] = mapped_column(String(64))
    default_contact_email: Mapped[str | None] = mapped_column(String(255))
    delivery_or_pickup_notes: Mapped[str | None] = mapped_column(String(500))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, server_default=false())
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
