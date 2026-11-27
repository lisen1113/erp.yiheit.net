"""Two-stage import of standard product definitions, without stock data."""

import csv
import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO, StringIO
from typing import Any, cast
from uuid import UUID, uuid5
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from fastapi import APIRouter, Request, Response
from openpyxl import Workbook  # type: ignore[import-untyped]
from openpyxl.utils.exceptions import InvalidFileException  # type: ignore[import-untyped]
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer

from yihe_api.audit.service import Actor
from yihe_api.auth.router import DB
from yihe_api.errors import ApiError, ErrorCode, error_response
from yihe_api.idempotency.service import FinalFailure, Success
from yihe_api.master_data.customer_spreadsheet import (
    Format,
    ImportFile,
    ImportIssue,
    values_from_csv,
    values_from_xlsx,
)
from yihe_api.master_data.router import Idempotency
from yihe_api.product_categories.imports import Preview, actor_for, decode
from yihe_api.product_categories.models import ProductCategory
from yihe_api.product_categories.router import lineage, load, rules
from yihe_api.products import service
from yihe_api.products.models import (
    ProductBrand,
    ProductImportBatch,
    ProductSKU,
    ProductSPU,
    ProductUnit,
)
from yihe_api.products.router import audit, execute, require
from yihe_api.products.schemas import SKUCreate, SPUCreate, code
from yihe_api.rbac.policy import Principal, assert_separation_of_duties
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


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def flag(value: str) -> bool:
    if not value or value.casefold() in {"true", "1", "yes"}:
        return True
    if value.casefold() in {"false", "0", "no"}:
        return False
    raise ValueError("Expected a boolean flag")


class ProductPreview(Preview):
    new_spus: int = 0
    existing_spu_skus: int = 0
    batch_id: UUID | None = None
    normalized_payload_hash: str | None = None


def process(
    db: Session, actor: Actor, format: Format, data: bytes
) -> tuple[ProductPreview, dict[str, Any]]:
    report = ProductPreview(creates=0, updates=0, conflicts=0, errors=[])
    plan: dict[str, Any] = {"spus": {}, "skus": []}

    try:
        rows = (
            values_from_csv(data, max_rows=500)
            if format == "csv"
            else values_from_xlsx(data, max_rows=500)
        )
    except (ValueError, KeyError, BadZipFile, InvalidFileException, ParseError):
        report.errors.append(
            ImportIssue(row=1, field="file", code="INVALID_FILE", message="无法读取商品导入文件")
        )
        return report, plan
    if not rows or tuple(v for v in rows[0][1] if v is not None) != COLUMNS:
        report.errors.append(
            ImportIssue(
                row=1, field="file", code="INVALID_HEADER", message="请使用标准商品主数据模板"
            )
        )
        return report, plan

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
                new = spu is None
                if spu is None:
                    spu = service.create_spu(
                        db,
                        actor.company_id,
                        actor.user_id,
                        spu_data,
                        id=uuid5(actor.company_id, f"import-spu:{spu_data.spu_no}"),
                    )
                    # This record is visible only inside the Preview savepoint.
                    spu.approved_at = datetime.now(UTC)
                    spu.approved_by = actor.user_id
                elif (
                    spu.is_deleted
                    or not spu.approved_at
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
                sku_input = SKUCreate.model_validate(payload)
                field = "attributes"
                encoded_attributes = json.dumps(sku_input.attributes, allow_nan=False).encode()
                if len(encoded_attributes) > 16384:
                    field = "attributes"
                    raise ValueError("Attribute values exceed maximum size")
                for column, value, conditions in (
                    (ProductSKU.sku_no, sku_input.sku_no, ()),
                    (
                        ProductSKU.manufacturer_part_no,
                        sku_input.manufacturer_part_no,
                        (ProductSKU.brand_id == brand.id,),
                    ),
                    (ProductSKU.barcode, sku_input.barcode, ()),
                ):
                    if value is not None and db.scalar(
                        select(ProductSKU.id).where(
                            ProductSKU.company_id == actor.company_id,
                            column == value,
                            *conditions,
                        )
                    ):
                        field = column.key
                        raise FinalFailure(ErrorCode.PRODUCT_CODE_CONFLICT)
                field = "sku_no"
                sku = service.create_sku(db, actor.company_id, actor.user_id, sku_input)
                catalog = load(db, actor.company_id)
                chain = lineage(catalog, catalog[category.id])
                actual_unit = service.get(db, ProductUnit, actor.company_id, sku.unit_id)
                normalized_sku = sku_input.model_copy(
                    update={
                        "unit_id": sku.unit_id,
                        "default_tax_rate": sku.default_tax_rate,
                        "tracking_mode": sku.tracking_mode,
                    }
                ).model_dump(mode="json")
                record = {
                    "input": normalized_sku,
                    "spu_id": str(spu.id),
                    "spu_version": spu.version_no,
                    "category_id": str(category.id),
                    "schema": digest(rules(chain)),
                    "chain": [
                        [str(item.id), item.version_no, item.schema_version] for item in chain
                    ],
                    "brand": [str(brand.id), brand.version_no],
                    "unit": [str(actual_unit.id), actual_unit.version_no],
                }
            if new:
                plan["spus"][str(spu.id)] = spu_data.model_dump(mode="json")
                report.new_spus += 1
            if str(spu.id) not in plan["spus"]:
                report.existing_spu_skus += 1
            plan["skus"].append(record)
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
            if isinstance(exc, FinalFailure) and exc.code in {
                ErrorCode.CONFLICT,
                ErrorCode.PRODUCT_CODE_CONFLICT,
            }:
                report.conflicts += 1
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
    return report, plan


class ProductImportUpload(ImportFile):
    source_filename: str = Field(min_length=1, max_length=180)


class BatchAction(BaseModel):
    version_no: int = Field(ge=1)
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Reject(BatchAction):
    reason: str = Field(min_length=1, max_length=500)


def inspect_upload(
    db: Session, actor: Actor, format: Format, data: bytes
) -> tuple[ProductPreview, dict[str, Any]]:
    savepoint = db.begin_nested()
    try:
        return process(db, actor, format, data)
    finally:
        savepoint.rollback()


def batch_for(db: Session, company: UUID, id: UUID, *, lock: bool = False) -> ProductImportBatch:
    stmt = (
        select(ProductImportBatch)
        .where(
            ProductImportBatch.id == id,
            ProductImportBatch.company_id == company,
        )
        .options(defer(ProductImportBatch.source_file))
    )
    if lock:
        stmt = stmt.with_for_update(key_share=True).execution_options(populate_existing=True)
    batch = db.scalar(stmt)
    if batch is None:
        raise ApiError(ErrorCode.NOT_FOUND)
    return batch


def event(
    db: Session, request: Request, actor: Actor, batch: ProductImportBatch, action: str
) -> None:
    audit(
        db,
        request,
        actor,
        "product.import",
        batch.id,
        action,
        metadata={
            "file_sha256": batch.source_file_sha256,
            "preview_hash": batch.preview_hash,
            "spu_count": batch.spu_count,
            "sku_count": batch.sku_count,
            "maker_id": batch.submitted_by,
            "approver_id": batch.approved_by,
            "import_result": batch.status,
        },
    )


@router.post("/preview", operation_id="previewProductImport")
def preview(
    payload: ProductImportUpload, request: Request, db: DB, principal: Current
) -> ProductPreview:
    require(principal, "import")
    require(principal, "create")
    actor = actor_for(request)
    data = decode(payload)
    result, plan = inspect_upload(db, actor, payload.format, data)
    if result.errors:
        return result
    source_hash = hashlib.sha256(data).hexdigest()
    normalized_hash = digest(plan)
    preview_hash = digest([source_hash, payload.format, normalized_hash])
    batch = ProductImportBatch(
        company_id=actor.company_id,
        source_filename=payload.source_filename,
        source_format=payload.format,
        source_file=data,
        source_file_sha256=source_hash,
        preview_hash=preview_hash,
        normalized_payload_hash=normalized_hash,
        normalized_payload=plan,
        created_by=actor.user_id,
        updated_by=actor.user_id,
        row_count=result.creates,
        spu_count=result.new_spus,
        sku_count=result.creates,
        existing_spu_sku_count=result.existing_spu_skus,
    )
    db.add(batch)
    db.flush()
    event(db, request, actor, batch, "previewed")
    result.batch_id, result.preview_token = batch.id, preview_hash
    result.normalized_payload_hash = normalized_hash
    return result


def read_access(principal: Principal) -> None:
    if "product.import.approve" in principal.grants:
        require(principal, "import.approve")
    else:
        require(principal, "import")


def summary(batch: ProductImportBatch) -> dict[str, Any]:
    return {
        key: getattr(batch, key)
        for key in (
            "id",
            "status",
            "source_filename",
            "source_file_sha256",
            "preview_hash",
            "normalized_payload_hash",
            "row_count",
            "spu_count",
            "sku_count",
            "existing_spu_sku_count",
            "submitted_by",
            "submitted_at",
            "approved_by",
            "approved_at",
            "committed_at",
            "version_no",
            "rejection_reason",
            "failure_code",
        )
    }


@router.get("/batches", operation_id="listProductImportBatches")
def batches(db: DB, principal: Current) -> list[dict[str, Any]]:
    read_access(principal)
    stmt = (
        select(ProductImportBatch)
        .options(
            defer(ProductImportBatch.source_file),
            defer(ProductImportBatch.normalized_payload),
        )
        .where(ProductImportBatch.company_id == principal.user.company_id)
    )
    return [
        summary(batch)
        for batch in db.scalars(stmt.order_by(ProductImportBatch.created_at.desc()).limit(100))
    ]


@router.get("/batches/{id}", operation_id="getProductImportBatch")
def detail(id: UUID, db: DB, principal: Current) -> dict[str, Any]:
    read_access(principal)
    batch = batch_for(db, principal.user.company_id, id)
    return {**summary(batch), "normalized_payload": batch.normalized_payload}


@router.get("/batches/{id}/source", operation_id="downloadProductImportSource")
def source(id: UUID, db: DB, principal: Current) -> Response:
    read_access(principal)
    batch = batch_for(db, principal.user.company_id, id)
    download_name = f"product-import-{batch.id}.{batch.source_format}"
    return Response(
        batch.source_file,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{download_name}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def verify(batch: ProductImportBatch, payload: BatchAction) -> None:
    if batch.version_no != payload.version_no:
        raise FinalFailure(ErrorCode.VERSION_CONFLICT)
    if (
        batch.preview_hash != payload.preview_hash
        or batch.source_file_sha256 != payload.source_file_sha256
        or hashlib.sha256(batch.source_file).hexdigest() != batch.source_file_sha256
        or digest(batch.normalized_payload) != batch.normalized_payload_hash
        or digest([batch.source_file_sha256, batch.source_format, batch.normalized_payload_hash])
        != batch.preview_hash
    ):
        raise FinalFailure(ErrorCode.PRODUCT_IMPORT_SNAPSHOT_STALE)


def transition(
    id: UUID, payload: BatchAction, request: Request, db: DB, principal: Current, action: str
) -> Response:
    require(principal, "import" if action == "submit" else "import.approve")

    def work(tx: Session, actor: Actor) -> Success:
        batch = batch_for(tx, actor.company_id, id, lock=True)
        verify(batch, payload)
        now = datetime.now(UTC)
        if action == "submit":
            if batch.status != "PREVIEWED" or batch.created_by != actor.user_id:
                raise FinalFailure(ErrorCode.CONFLICT)
            batch.status, batch.submitted_by, batch.submitted_at = "SUBMITTED", actor.user_id, now
            label = "submitted"
        else:
            if batch.status != "SUBMITTED" or batch.submitted_by is None:
                raise FinalFailure(ErrorCode.CONFLICT)
            assert_separation_of_duties(batch.submitted_by, actor.user_id, "product.import.approve")
            if isinstance(payload, Reject):
                batch.status = "REJECTED"
                batch.rejection_reason = payload.reason.strip()
                if not batch.rejection_reason:
                    raise FinalFailure(ErrorCode.VALIDATION_ERROR)
                label = "rejected"
            else:
                batch.status, batch.approved_by, batch.approved_at = "APPROVED", actor.user_id, now
                label = "approved"
        batch.version_no += 1
        batch.updated_by = actor.user_id
        event(tx, request, actor, batch, label)
        return Success("product_import_batch", id)

    return execute(
        db,
        request,
        action,
        "product_import_batch",
        f"{PATH}/batches/{{id}}/{action}",
        payload.model_dump(mode="json"),
        work,
        id,
    )


@router.post("/batches/{id}/submit", operation_id="submitProductImport")
def submit(
    id: UUID, payload: BatchAction, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    return transition(id, payload, request, db, principal, "submit")


@router.post("/batches/{id}/approve", operation_id="approveProductImport")
def approve(
    id: UUID, payload: BatchAction, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    return transition(id, payload, request, db, principal, "approve")


@router.post("/batches/{id}/reject", operation_id="rejectProductImport")
def reject(
    id: UUID, payload: Reject, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    return transition(id, payload, request, db, principal, "reject")


def publish(tx: Session, request: Request, actor: Actor, batch: ProductImportBatch) -> None:
    assert batch.approved_at is not None and batch.approved_by is not None
    assert batch.submitted_by is not None
    # Freeze the catalog hierarchy and lock referenced directory/SPU records before revalidation.
    load(tx, actor.company_id, lock=True)
    plan = batch.normalized_payload
    for model, ids in (
        (ProductBrand, {UUID(row["brand"][0]) for row in plan["skus"]}),
        (ProductUnit, {UUID(row["unit"][0]) for row in plan["skus"]}),
        (
            ProductSPU,
            {UUID(row["spu_id"]) for row in plan["skus"] if row["spu_id"] not in plan["spus"]},
        ),
    ):
        for id in sorted(ids):
            service.get(tx, model, actor.company_id, id, lock=True)

    report, current = inspect_upload(
        tx, actor, cast(Format, batch.source_format), batch.source_file
    )
    if report.errors or digest(current) != batch.normalized_payload_hash:
        raise FinalFailure(ErrorCode.PRODUCT_IMPORT_SNAPSHOT_STALE)

    for raw_id, document in plan["spus"].items():
        spu = service.create_spu(
            tx,
            actor.company_id,
            batch.submitted_by,
            SPUCreate.model_validate(document),
            id=UUID(raw_id),
        )
        spu.approved_at, spu.approved_by = batch.approved_at, batch.approved_by
        audit(
            tx, request, actor, "product_spu", spu.id, "created", metadata={"related_id": batch.id}
        )
    tx.flush()
    for id in sorted({UUID(row["spu_id"]) for row in plan["skus"]}):
        spu = service.get(tx, ProductSPU, actor.company_id, id)
        if not spu.approved_at or not spu.approved_by or not spu.is_active:
            raise FinalFailure(ErrorCode.PRODUCT_IMPORT_SNAPSHOT_STALE)
    for row in plan["skus"]:
        sku = service.create_sku(
            tx, actor.company_id, batch.submitted_by, SKUCreate.model_validate(row["input"])
        )
        audit(
            tx, request, actor, "product_sku", sku.id, "created", metadata={"related_id": batch.id}
        )


@router.post("/batches/{id}/commit", operation_id="commitProductImport")
def commit(
    id: UUID, payload: BatchAction, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    require(principal, "import")
    require(principal, "create")

    def work(tx: Session, actor: Actor) -> Success:
        batch = batch_for(tx, actor.company_id, id, lock=True)
        if batch.status != "APPROVED":
            raise FinalFailure(ErrorCode.CONFLICT)
        verify(batch, payload)
        try:
            with tx.begin_nested():
                publish(tx, request, actor, batch)
        except (ApiError, FinalFailure, IntegrityError):
            batch.status = "FAILED"
            batch.failure_code = ErrorCode.PRODUCT_IMPORT_SNAPSHOT_STALE.value
            event(tx, request, actor, batch, "failed")
        else:
            batch.status = "COMMITTED"
            batch.committed_at = datetime.now(UTC)
            event(tx, request, actor, batch, "committed")
        batch.version_no += 1
        batch.updated_by = actor.user_id
        return Success("product_import_batch", id)

    result = execute(
        db,
        request,
        "commit",
        "product_import_batch",
        f"{PATH}/batches/{{id}}/commit",
        payload.model_dump(mode="json"),
        work,
        id,
    )
    if result.status_code == 200:
        batch = batch_for(db, principal.user.company_id, id)
        if batch.status == "FAILED":
            return error_response(
                ErrorCode.PRODUCT_IMPORT_SNAPSHOT_STALE,
                headers={k: v for k, v in result.headers.items() if k == "idempotency-replayed"},
            )
    return result
