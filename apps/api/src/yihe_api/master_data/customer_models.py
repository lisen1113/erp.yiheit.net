"""Customer-specific profile, keyed to the existing partner identity."""

from uuid import UUID

from sqlalchemy import Boolean, ForeignKeyConstraint, String, UniqueConstraint, false, true
from sqlalchemy.orm import Mapped, mapped_column

from yihe_api.db.models import AuditMixin, Base, CompanyScopeMixin, IdentityMixin, SoftDeleteMixin


class CustomerProfile(IdentityMixin, CompanyScopeMixin, AuditMixin, SoftDeleteMixin, Base):
    __tablename__ = "customer_profile"
    __table_args__ = (
        UniqueConstraint("company_id", "customer_no"),
        UniqueConstraint("company_id", "business_partner_id"),
        ForeignKeyConstraint(
            ["company_id", "business_partner_id"],
            ["business_partner.company_id", "business_partner.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "salesperson_id"],
            ["employee.company_id", "employee.id"],
            ondelete="RESTRICT",
        ),
    )

    business_partner_id: Mapped[UUID]
    customer_no: Mapped[str] = mapped_column(String(32))
    customer_type: Mapped[str] = mapped_column(String(32), server_default="enterprise")
    salesperson_id: Mapped[UUID] = mapped_column(index=True)
    delivery_address: Mapped[str | None] = mapped_column(String(500))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, server_default=false())
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
