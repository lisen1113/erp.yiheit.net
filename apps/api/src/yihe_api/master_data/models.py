"""Shared customer and supplier legal identity."""

from decimal import Decimal

from sqlalchemy import CheckConstraint, Index, String, UniqueConstraint, text, true
from sqlalchemy.orm import Mapped, mapped_column

from yihe_api.db.models import (
    AMOUNT,
    AuditMixin,
    Base,
    CompanyScopeMixin,
    IdentityMixin,
    LegacyMixin,
    SoftDeleteMixin,
)

CANONICAL_TAX_NO = r"""
    NULLIF(
        translate(
            replace(
                btrim(
                    normalize(tax_no, NFKC),
                    U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0'
                    || U&'\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A'
                    || U&'\2028\2029\202F\205F\3000'
                ),
                ' ', ''
            ),
            'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        ),
        ''
    )
"""


class BusinessPartner(
    IdentityMixin, CompanyScopeMixin, AuditMixin, SoftDeleteMixin, LegacyMixin, Base
):
    __tablename__ = "business_partner"
    __table_args__ = (
        UniqueConstraint("company_id", "code"),
        UniqueConstraint("company_id", "id"),
        Index(
            "uq_business_partner_company_tax_no",
            "company_id",
            "tax_no",
            unique=True,
            postgresql_where=text("tax_no IS NOT NULL"),
        ),
        UniqueConstraint("company_id", "unified_social_credit_code"),
        CheckConstraint("partner_type IN ('CUSTOMER', 'SUPPLIER', 'BOTH')", name="partner_type"),
        CheckConstraint(
            f"tax_no IS NOT DISTINCT FROM ({CANONICAL_TAX_NO})",
            name="tax_no_normalized",
        ),
        CheckConstraint("currency_code = 'CNY'", name="currency_cny"),
        CheckConstraint("credit_limit >= 0", name="credit_limit_nonnegative"),
        CheckConstraint("payment_terms_days >= 0", name="payment_terms_nonnegative"),
        CheckConstraint("version_no >= 1", name="version_positive"),
    )

    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    partner_type: Mapped[str] = mapped_column(String(8))
    currency_code: Mapped[str] = mapped_column(String(3), server_default="CNY")
    credit_limit: Mapped[Decimal] = mapped_column(AMOUNT, server_default="0")
    payment_terms_days: Mapped[int] = mapped_column(server_default="0")
    is_active: Mapped[bool] = mapped_column(server_default=true())
    short_name: Mapped[str | None] = mapped_column(String(120))
    unified_social_credit_code: Mapped[str | None] = mapped_column(String(32))
    taxpayer_type: Mapped[str | None] = mapped_column(String(40))
    tax_registration_no: Mapped[str | None] = mapped_column(String(64))
    contact_name: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(254))
    registered_address: Mapped[str | None] = mapped_column(String(500))
    business_address: Mapped[str | None] = mapped_column(String(500))
    invoice_title: Mapped[str | None] = mapped_column(String(200))
    tax_no: Mapped[str | None] = mapped_column(String(64))
    invoice_address_phone: Mapped[str | None] = mapped_column(String(500))
    invoice_bank_name: Mapped[str | None] = mapped_column(String(200))
    invoice_bank_account: Mapped[str | None] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(String(2000))
