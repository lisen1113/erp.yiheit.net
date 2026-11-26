"""Product write validation shared by the structured API and imports."""

from decimal import Decimal
from typing import Annotated, Any, Literal
from unicodedata import normalize
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

TrackingMode = Literal["quantity", "serial", "batch", "non_stock"]
ProductType = Literal["physical", "software", "service", "subscription", "lease"]


def code(value: object) -> object:
    return normalize("NFKC", value).strip().upper() if isinstance(value, str) else value


Code32 = Annotated[str, Field(min_length=1, max_length=32), BeforeValidator(code)]
Code64 = Annotated[str, Field(min_length=1, max_length=64), BeforeValidator(code)]
Name = Annotated[str, Field(min_length=1, max_length=200)]
TaxRate = Annotated[Decimal, Field(ge=0, le=1, max_digits=7, decimal_places=4)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DirectoryCreate(Input):
    code: Code32
    name: Annotated[str, Field(min_length=1, max_length=100)]


class ProductVersion(Input):
    version_no: int = Field(ge=1)


class DirectoryUpdate(ProductVersion):
    name: Annotated[str, Field(min_length=1, max_length=100)]


class SPUCreate(Input):
    spu_no: Code32
    name: Name
    name_en: Name | None = None
    category_id: UUID
    brand_id: UUID | None = None
    product_type: ProductType = "physical"
    description: Annotated[str, Field(max_length=4000)] | None = None


class SPUUpdate(ProductVersion):
    name: Name
    name_en: Name | None = None
    description: Annotated[str, Field(max_length=4000)] | None = None


class SKUCreate(Input):
    sku_no: Code64
    spu_id: UUID
    manufacturer_part_no: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    spec_name: Annotated[str, Field(min_length=1, max_length=255)]
    approved_name_cn: Name
    approved_name_en: Name | None = None
    unit_id: UUID | None = None
    default_tax_rate: TaxRate | None = None
    tracking_mode: TrackingMode | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    barcode: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    allow_purchase: bool = True
    allow_sale: bool = True


class SKUUpdate(ProductVersion):
    spec_name: Annotated[str, Field(min_length=1, max_length=255)]
    approved_name_cn: Name
    approved_name_en: Name | None = None
    attributes: dict[str, Any]
    barcode: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    allow_purchase: bool = True
    allow_sale: bool = True


class TrackingRequest(ProductVersion):
    tracking_mode: TrackingMode
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class TrackingApproval(ProductVersion):
    request_id: UUID


class CategoryDefaults(ProductVersion):
    unit_id: UUID | None
    tax_rate: TaxRate | None
    tracking_mode: TrackingMode | None


class LegacyCreate(Input):
    legacy_system: Annotated[str, Field(min_length=1, max_length=64)]
    legacy_product_id: Annotated[str, Field(min_length=1, max_length=128)]
    legacy_name: Annotated[str, Field(min_length=1, max_length=255)]
    legacy_category: Annotated[str, Field(max_length=255)] | None = None
    target_sku_id: UUID | None = None
    confidence: TaxRate | None = None


class LegacyReview(ProductVersion):
    status: Literal["CONFIRMED", "REJECTED", "RETIRED"]
    target_sku_id: UUID | None = None
    reason: Annotated[str, Field(min_length=1, max_length=500)]
