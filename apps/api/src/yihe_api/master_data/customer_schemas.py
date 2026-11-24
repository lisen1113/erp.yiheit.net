"""Customer API contracts and shared partner projection."""

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


def customer_number(value: object) -> object:
    return normalize("NFKC", value).strip().upper() if isinstance(value, str) else value


Number = Annotated[str, Field(min_length=1, max_length=32), BeforeValidator(customer_number)]
Kind = Annotated[str, Field(min_length=1, max_length=32)]
Address = Annotated[str, Field(max_length=500)]


class CustomerPartnerCreate(PartnerFields):
    code: Code
    name: Name
    credit_limit: CreditLimit = Decimal("0.00")
    payment_terms_days: PaymentDays = 0
    currency_code: Literal["CNY"] = "CNY"


class CustomerVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version_no: int = Field(ge=1)


class CustomerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    customer_no: Number
    customer_type: Kind = "enterprise"
    salesperson_id: UUID
    delivery_address: Address | None = None
    business_partner_id: UUID | None = None
    partner: CustomerPartnerCreate | None = None

    @model_validator(mode="after")
    def one_identity_source(self) -> Self:
        if (self.business_partner_id is None) == (self.partner is None):
            raise ValueError("Provide exactly one partner identity source")
        return self


class CustomerPatch(CustomerVersion):
    customer_no: Number | None = None
    customer_type: Kind | None = None
    salesperson_id: UUID | None = None
    delivery_address: Address | None = None
    partner: PartnerEdit | None = None

    @model_validator(mode="after")
    def protect_identity(self) -> Self:
        required = {"customer_no", "customer_type", "salesperson_id"}
        if any(getattr(self, key) is None for key in required & self.model_fields_set):
            raise ValueError("Required customer fields cannot be cleared")
        if self.partner and "partner_type" in self.partner.model_fields_set:
            raise ValueError("Partner roles are managed by role-specific operations")
        return self


class CustomerDisable(CustomerVersion):
    reason: Literal["customer_request", "duplicate", "inactive", "other"]


class SalespersonOption(BaseModel):
    id: UUID
    name: str


class CustomerView(BaseModel):
    id: UUID
    company_id: UUID
    business_partner_id: UUID
    customer_no: str
    customer_type: str
    salesperson_id: UUID
    salesperson_name: str
    delivery_address: str | None
    is_active: bool
    is_available: bool
    version_no: int
    created_at: datetime
    created_by: UUID
    updated_at: datetime
    updated_by: UUID
    partner: PartnerView


class CustomerPage(BaseModel):
    items: list[CustomerView]
    total: int
    page: int
    page_size: int


class CustomerReceipt(BaseModel):
    resource_id: UUID
    resource_type: Literal["customer_profile"]
    status: Literal["completed"]
