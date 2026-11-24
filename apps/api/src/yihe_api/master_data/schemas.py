"""Business partner request and response contracts."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from unicodedata import normalize
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


class PartnerType(StrEnum):
    CUSTOMER = "CUSTOMER"
    SUPPLIER = "SUPPLIER"
    BOTH = "BOTH"


def validate_decimal(value: object) -> object:
    if not isinstance(value, (str, Decimal)):
        raise ValueError("Use a decimal string for credit limit")
    return value


CreditLimit = Annotated[
    Decimal,
    Field(ge=0, max_digits=18, decimal_places=2),
    BeforeValidator(validate_decimal, json_schema_input_type=str),
]
ASCII_UPPER = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def normalize_tax_no(value: object) -> object:
    if not isinstance(value, str):
        return value
    return normalize("NFKC", value).strip().replace(" ", "").translate(ASCII_UPPER) or None


TaxNo = Annotated[str | None, Field(max_length=64), BeforeValidator(normalize_tax_no)]
Code = Annotated[str, Field(min_length=1, max_length=32)]
Name = Annotated[str, Field(min_length=1, max_length=200)]
PaymentDays = Annotated[int, Field(ge=0, le=2147483647)]


class PartnerFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    short_name: str | None = Field(default=None, max_length=120)
    unified_social_credit_code: str | None = Field(default=None, max_length=32)
    taxpayer_type: str | None = Field(default=None, max_length=40)
    tax_registration_no: str | None = Field(default=None, max_length=64)
    contact_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=254)
    registered_address: str | None = Field(default=None, max_length=500)
    business_address: str | None = Field(default=None, max_length=500)
    invoice_title: str | None = Field(default=None, max_length=200)
    tax_no: TaxNo = None
    invoice_address_phone: str | None = Field(default=None, max_length=500)
    invoice_bank_name: str | None = Field(default=None, max_length=200)
    invoice_bank_account: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=2000)


class PartnerCreate(PartnerFields):
    code: Code
    name: Name
    partner_type: PartnerType
    credit_limit: CreditLimit = Decimal("0.00")
    payment_terms_days: PaymentDays = 0
    currency_code: Literal["CNY"] = "CNY"


class PartnerVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_no: int = Field(ge=1)


class PartnerEdit(PartnerFields, PartnerVersion):
    code: Code | None = None
    name: Name | None = None
    partner_type: PartnerType | None = None
    credit_limit: CreditLimit | None = None
    payment_terms_days: PaymentDays | None = None
    currency_code: Literal["CNY"] | None = None


class PartnerView(BaseModel):
    id: UUID
    company_id: UUID
    code: str
    name: str
    partner_type: PartnerType
    is_active: bool
    version_no: int
    short_name: str | None = None
    currency_code: Literal["CNY"] | None = None
    credit_limit: Decimal | None = None
    payment_terms_days: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    unified_social_credit_code: str | None = None
    taxpayer_type: str | None = None
    tax_registration_no: str | None = None
    contact_name: str | None = None
    phone: str | None = None
    email: str | None = None
    registered_address: str | None = None
    business_address: str | None = None
    invoice_title: str | None = None
    tax_no: str | None = None
    invoice_address_phone: str | None = None
    invoice_bank_name: str | None = None
    invoice_bank_account: str | None = None
    notes: str | None = None


class PartnerPage(BaseModel):
    items: list[PartnerView]
    total: int
    page: int
    page_size: int


class PartnerReceipt(BaseModel):
    resource_id: UUID
    resource_type: Literal["business_partner"]
    status: Literal["completed"]
