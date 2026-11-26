"""Two-stage import of standard product definitions, without stock data."""

import csv
import hashlib
import hmac
import json
import os
from io import BytesIO, StringIO
from typing import Any
from uuid import UUID
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from fastapi import APIRouter, Request, Response
from openpyxl import Workbook  # type: ignore[import-untyped]
from openpyxl.utils.exceptions import InvalidFileException  # type: ignore[import-untyped]
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from yihe_api.audit.service import Actor
from yihe_api.auth.router import DB
from yihe_api.errors import ApiError, ErrorCode
from yihe_api.idempotency.service import FinalFailure, Success
from yihe_api.master_data.customer_spreadsheet import (
    Format,
    ImportFile,
    ImportIssue,
    values_from_csv,
    values_from_xlsx,
)
from yihe_api.master_data.router import Idempotency
from yihe_api.product_categories.imports import CommitFile, Preview, actor_for, decode
from yihe_api.product_categories.models import ProductCategory
from yihe_api.products import service
from yihe_api.products.models import ProductBrand, ProductSPU, ProductUnit
from yihe_api.products.router import audit, execute, require
from yihe_api.products.schemas import SKUCreate, SPUCreate, code
from yihe_api.rbac.router import Current

router = APIRouter(prefix="/product-import", tags=["product-import"])
PATH = "/api/v1/product-import"
COLUMNS = (
    "category_code",
    "brand_code",
    "spu_no",
    "spu_name",
    "product_type",
    "sku_no",
    "manufacturer_part_no",
    "approved_name_cn",
    "approved_name_en",
    "spec_name",
    "unit_code",
    "default_tax_rate",
    "tracking_mode",
    "barcode",
    "allow_purchase",
    "allow_sale",
    "attributes",
)


@router.get("/templates/{category_id}", operation_id="downloadProductTemplate")
def template(category_id: UUID, db: DB, principal: Current, format: Format = "xlsx") -> Response:
    require(principal, "import")
    category = service.availability(db, principal.user.company_id, category_id)
    if format == "csv":
        output = StringIO(newline="")
        csv.writer(output).writerow(COLUMNS)
        data = output.getvalue().encode("utf-8-sig")
        media = "text/csv; charset=utf-8"
    else:
        book = Workbook()
        sheet = book.active
        assert sheet is not None
        sheet.title = "Products"
        sheet.append(COLUMNS)
        for cell in sheet[1]:
            sheet.column_dimensions[cell.column_letter].number_format = "@"
            sheet.column_dimensions[cell.column_letter].width = 25
        output_bytes = BytesIO()
        book.save(output_bytes)
        book.close()
        data = output_bytes.getvalue()
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(
        data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="products-{category.id}.{format}"'},
    )


def marker(actor: Actor, payload: ImportFile, data: bytes) -> str:
    key = os.environ.get("ERP_AUDIT_IDENTIFIER_KEY", "").encode()
    if len(key) < 32:
        raise ApiError(ErrorCode.INTERNAL_ERROR)
    material = (
        f"product-import:{actor.company_id}:{actor.user_id}:{payload.format}:".encode() + data
    )
    return hmac.new(key, material, "sha256").hexdigest()


def flag(value: str) -> bool:
    if not value or value.casefold() in {"true", "1", "yes"}:
        return True
    if value.casefold() in {"false", "0", "no"}:
        return False
    raise ValueError("Expected a boolean flag")


def process(
    db: Session, request: Request, actor: Actor, format: Format, data: bytes, *, preview: bool
) -> Preview:
    report = Preview(creates=0, updates=0, conflicts=0, errors=[])
    try:
        rows = values_from_csv(data) if format == "csv" else values_from_xlsx(data)
    except (ValueError, KeyError, BadZipFile, InvalidFileException, ParseError):
        report.errors.append(
            ImportIssue(row=1, field="file", code="INVALID_FILE", message="无法读取商品导入文件")
        )
        return report
    if not rows or tuple(v for v in rows[0][1] if v is not None) != COLUMNS:
        report.errors.append(
            ImportIssue(
                row=1, field="file", code="INVALID_HEADER", message="请使用标准商品主数据模板"
            )
        )
        return report

    for number, cells, formulas in rows[1:]:
        if not any(v not in (None, "") for v in cells) and not formulas:
            continue
        field = "file"
        try:
            if formulas or len(cells) > len(COLUMNS):
                raise ValueError("Invalid shape")
            values = {
                k: str(v).strip() if v is not None else ""
                for k, v in zip(COLUMNS, cells, strict=False)
            }
            with db.begin_nested():
                field = "category_code"
                category = db.scalar(
                    select(ProductCategory).where(
                        ProductCategory.company_id == actor.company_id,
                        ProductCategory.code == code(values.get(field)),
                        ProductCategory.is_deleted.is_(False),
                    )
                )
                if category is None:
                    raise ValueError("Unknown category")
                field = "brand_code"
                brand = db.scalar(
                    select(ProductBrand).where(
                        ProductBrand.company_id == actor.company_id,
                        ProductBrand.code == code(values.get(field)),
                        ProductBrand.is_active.is_(True),
                        ProductBrand.is_deleted.is_(False),
                    )
                )
                if brand is None:
                    raise ValueError("Unknown brand")
                field = "spu_no"
                spu_data = SPUCreate(
                    spu_no=values.get("spu_no", ""),
                    name=values.get("spu_name", ""),
                    category_id=category.id,
                    brand_id=brand.id,
                    product_type=values.get("product_type") or "physical",
                )
                spu = db.scalar(
                    select(ProductSPU).where(
                        ProductSPU.company_id == actor.company_id,
                        ProductSPU.spu_no == spu_data.spu_no,
                    )
                )
                if spu is None:
                    spu = service.create_spu(db, actor.company_id, actor.user_id, spu_data)
                    if not preview:
                        audit(db, request, actor, "product_spu", spu.id, "created")
                elif (
                    spu.is_deleted
                    or spu.category_id != category.id
                    or spu.brand_id != brand.id
                    or spu.name != spu_data.name
                    or spu.product_type != spu_data.product_type
                ):
                    raise FinalFailure(ErrorCode.CONFLICT)
                field = "unit_code"
                unit = None
                if values.get(field):
                    unit = db.scalar(
                        select(ProductUnit).where(
                            ProductUnit.company_id == actor.company_id,
                            ProductUnit.code == code(values[field]),
                            ProductUnit.is_active.is_(True),
                            ProductUnit.is_deleted.is_(False),
                        )
                    )
                    if unit is None:
                        raise ValueError("Unknown unit")
                field = "attributes"
                payload: dict[str, Any] = {
                    k: values[k]
                    for k in (
                        "sku_no",
                        "manufacturer_part_no",
                        "approved_name_cn",
                        "approved_name_en",
                        "spec_name",
                        "default_tax_rate",
                        "tracking_mode",
                        "barcode",
                    )
                    if values.get(k)
                }
                payload.update(
                    spu_id=spu.id,
                    unit_id=unit.id if unit else None,
                    attributes=json.loads(values.get("attributes") or "{}"),
                )
                for field in ("allow_purchase", "allow_sale"):
                    payload[field] = flag(values.get(field, ""))
                field = "sku_no"
                sku = service.create_sku(
                    db, actor.company_id, actor.user_id, SKUCreate.model_validate(payload)
                )
                if not preview:
                    audit(db, request, actor, "product_sku", sku.id, "created")
            report.creates += 1
        except ValidationError as exc:
            for item in exc.errors(include_input=False, include_context=False):
                report.errors.append(
                    ImportIssue(
                        row=number,
                        field=str(item["loc"][0]),
                        code="VALIDATION_ERROR",
                        message="字段值无效",
                    )
                )
        except (ValueError, ApiError, FinalFailure) as exc:
            if (
                isinstance(exc, (ApiError, FinalFailure))
                and exc.code == ErrorCode.PRODUCT_ATTRIBUTES_INVALID
            ):
                field = "attributes"
            error = exc.code.value if isinstance(exc, (ApiError, FinalFailure)) else "INVALID_ROW"
            report.errors.append(
                ImportIssue(
                    row=number,
                    field=field,
                    code=error,
                    message="数据无效、引用不存在或与现有记录冲突",
                )
            )
    if not report.creates and not report.errors:
        report.errors.append(
            ImportIssue(row=2, field="file", code="EMPTY_FILE", message="没有商品数据行")
        )
    return report


@router.post("/preview", operation_id="previewProductImport")
def preview(payload: ImportFile, request: Request, db: DB, principal: Current) -> Preview:
    require(principal, "import")
    require(principal, "create")
    actor = actor_for(request)
    data = decode(payload)
    savepoint = db.begin_nested()
    try:
        result = process(db, request, actor, payload.format, data, preview=True)
    finally:
        savepoint.rollback()
    if not result.errors:
        result.preview_token = marker(actor, payload, data)
    audit(
        db,
        request,
        actor,
        "product",
        actor.company_id,
        "import_previewed",
        metadata={"added_count": result.creates, "error_count": len(result.errors)},
    )
    return result


@router.post("/commit", operation_id="commitProductImport")
def commit(
    payload: CommitFile, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    require(principal, "import")
    require(principal, "create")
    data = decode(payload)

    def work(tx: Session, actor: Actor) -> Success:
        if not hmac.compare_digest(marker(actor, payload, data), payload.preview_token):
            raise FinalFailure(ErrorCode.VERSION_CONFLICT)
        # T016-01: persist no catalog rows until the frozen review sequence is decided.
        raise FinalFailure(ErrorCode.PRODUCT_IMPORT_REVIEW_PENDING)

    return execute(
        db,
        request,
        "import_commit",
        "product",
        f"{PATH}/commit",
        {"file_sha256": hashlib.sha256(data).hexdigest(), "format": payload.format},
        work,
    )
