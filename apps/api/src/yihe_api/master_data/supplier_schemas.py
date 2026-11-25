"""Supplier API contracts and shared partner projection."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self
from unicodedata import normalize
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from yihe_api.master_data.schemas import (
    Code,
    CreditLimit,
    Name,
    PartnerEdit,
    PartnerFields,
    PartnerView,
    PaymentDays,
)


def supplier_number(value: object) -> object:
    return normalize("NFKC", value).strip().upper() if isinstance(value, str) else value


Number = Annotated[str, Field(min_length=1, max_length=32), BeforeValidator(supplier_number)]
Kind = Literal["DISTRIBUTOR", "HARDWARE", "SOFTWARE_SERVICE", "GENERAL"]
Address = Annotated[str, Field(max_length=500)]


class SupplierPartnerCreate(PartnerFields):
    code: Code
    name: Name
    credit_limit: CreditLimit = Decimal("0.00")
    payment_terms_days: PaymentDays = 0
    currency_code: Literal["CNY"] = "CNY"


class SupplierVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version_no: int = Field(ge=1)


class SupplierCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    supplier_no: Number
    supplier_type: Kind = "GENERAL"
    buyer_id: UUID
    delivery_or_pickup_notes: Address | None = None
    payment_term_days: PaymentDays = 0
    default_contact_name: Annotated[str, Field(max_length=120)] | None = None
    default_contact_phone: Annotated[str, Field(max_length=64)] | None = None
    default_contact_email: Annotated[str, Field(max_length=255)] | None = None
    business_partner_id: UUID | None = None
    partner: SupplierPartnerCreate | None = None

    @model_validator(mode="after")
    def one_identity_source(self) -> Self:
        if (self.business_partner_id is None) == (self.partner is None):
            raise ValueError("Provide exactly one partner identity source")
        return self


class SupplierPatch(SupplierVersion):
    supplier_no: Number | None = None
    supplier_type: Kind | None = None
    buyer_id: UUID | None = None
    delivery_or_pickup_notes: Address | None = None
    payment_term_days: PaymentDays | None = None
    default_contact_name: Annotated[str, Field(max_length=120)] | None = None
    default_contact_phone: Annotated[str, Field(max_length=64)] | None = None
    default_contact_email: Annotated[str, Field(max_length=255)] | None = None
    partner: PartnerEdit | None = None

    @model_validator(mode="after")
    def protect_identity(self) -> Self:
        required = {"supplier_no", "supplier_type", "buyer_id", "payment_term_days"}
        if any(getattr(self, key) is None for key in required & self.model_fields_set):
            raise ValueError("Required supplier fields cannot be cleared")
        if self.partner and "partner_type" in self.partner.model_fields_set:
            raise ValueError("Partner roles are managed by role-specific operations")
        return self


class SupplierDisable(SupplierVersion):
    reason: Literal["supplier_request", "duplicate", "inactive", "other"]


class BuyerOption(BaseModel):
    id: UUID
    name: str


class SupplierView(BaseModel):
    id: UUID
    company_id: UUID
    business_partner_id: UUID
    supplier_no: str
    supplier_type: Kind
    buyer_id: UUID
    buyer_name: str
    delivery_or_pickup_notes: str | None
    payment_term_days: int
    default_contact_name: str | None
    default_contact_phone: str | None
    default_contact_email: str | None
    customer_profile_id: UUID | None = None
    is_active: bool
    is_available: bool
    version_no: int
    created_at: datetime
    created_by: UUID
    updated_at: datetime
    updated_by: UUID
    partner: PartnerView


class SupplierPage(BaseModel):
    items: list[SupplierView]
    total: int
    page: int
    page_size: int


class SupplierReceipt(BaseModel):
    resource_id: UUID
    resource_type: Literal["supplier_profile"]
    status: Literal["completed"]
