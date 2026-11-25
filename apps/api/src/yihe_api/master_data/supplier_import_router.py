"""Authorized standard supplier file import with isolated per-row writes."""

import base64
import binascii
import hashlib
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response
from pydantic import ValidationError

from yihe_api.auth.router import DB
from yihe_api.errors import ERRORS, ApiError, ErrorCode, FieldCode, FieldError, error_response
from yihe_api.idempotency.service import Operation, Success
from yihe_api.master_data import router as partner_api
from yihe_api.master_data.customer_spreadsheet import (
    Format,
    ImportFile,
    ImportIssue,
    ImportResult,
    SpreadsheetError,
)
from yihe_api.master_data.supplier_router import PATH, create_supplier_record
from yihe_api.master_data.supplier_spreadsheet import (
    parse_document,
    row_payload,
    template,
    validation_issues,
)
from yihe_api.rbac.policy import Scope
from yihe_api.rbac.router import Current

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


def row_request(request: Request, batch: UUID, row: int) -> Request:
    digest = hashlib.sha256(batch.bytes + row.to_bytes(4, "big")).digest()
    key = UUID(bytes=digest[:16], version=4)
    headers = [(k, v) for k, v in request.scope["headers"] if k.lower() != b"idempotency-key"]
    return Request(
        {**request.scope, "headers": [*headers, (b"idempotency-key", str(key).encode())]}
    )


def issue(row: int, code: ErrorCode, field: str | None = None) -> ImportIssue:
    fields = {
        ErrorCode.SUPPLIER_NO_CONFLICT: "supplier_no",
        ErrorCode.BUSINESS_PARTNER_TAX_NO_CONFLICT: "partner.tax_no",
        ErrorCode.SUPPLIER_IMPORT_PARTNER_MISMATCH: "partner.tax_no",
        ErrorCode.VALIDATION_ERROR: "buyer_id",
        ErrorCode.CONFLICT: "business_partner_id",
        ErrorCode.NOT_FOUND: "business_partner_id",
        ErrorCode.SUPPLIER_UNAVAILABLE: "business_partner_id",
    }
    return ImportIssue(
        row=row, field=field or fields.get(code, "row"), code=code.value, message=ERRORS[code][1]
    )


@router.get(
    "/import-template/{file_format}",
    operation_id="getSupplierImportTemplate",
    response_class=Response,
)
def get_template(file_format: Format, principal: Current) -> Response:
    principal.require("supplier.import")
    media_type = (
        "text/csv; charset=utf-8"
        if file_format == "csv"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return Response(
        template(file_format),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="suppliers.{file_format}"'},
    )


@router.post("/import", operation_id="importSuppliers", response_model=ImportResult)
def import_suppliers(
    payload: ImportFile,
    request: Request,
    db: DB,
    principal: Current,
    idempotency_key: partner_api.Idempotency,
) -> ImportResult | Response:
    scope = principal.require("supplier.import")
    try:
        data = base64.b64decode(payload.content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(ErrorCode.VALIDATION_ERROR) from None
    try:
        rows = parse_document(data, payload.format)
    except SpreadsheetError as exc:
        problem = exc.issue
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            fields=[
                FieldError(
                    field=f"rows.{problem.row}.{problem.field}",
                    code=FieldCode.INVALID_FORMAT,
                    message=problem.message,
                ),
            ],
        )

    # Reserve one batch fingerprint, then use deterministic row keys. All successful rows,
    # audit entries and receipts share the outer database transaction.
    batch = partner_api.dispatch(
        db,
        request,
        Operation("supplier.import", "supplier_import", None, f"{PATH}/import"),
        {"format": payload.format, "file_sha256": hashlib.sha256(data).hexdigest()},
        lambda _db, _actor: Success("supplier_import", uuid4()),
    )
    if batch.status_code != 200:
        return batch
    batch_id = UUID(json.loads(bytes(batch.body))["resource_id"])
    errors = []
    succeeded = 0
    for row in rows:
        if row.issues:
            errors.extend(row.issues)
            continue
        try:
            supplier = row_payload(row)
        except ValidationError as exc:
            errors.extend(validation_issues(row.number, exc))
            continue
        if scope == Scope.SELF and supplier.buyer_id != principal.user.employee_id:
            errors.append(issue(row.number, ErrorCode.FORBIDDEN, "buyer_id"))
            continue
        try:
            with db.begin_nested():
                result = create_supplier_record(
                    supplier,
                    row_request(request, batch_id, row.number),
                    db,
                    principal,
                    match_existing=True,
                )
        except ApiError as exc:
            if ERRORS[exc.code][0] >= 500:
                raise
            field = (
                "partner.invoice_bank_account" if exc.code == ErrorCode.VALIDATION_ERROR else None
            )
            errors.append(issue(row.number, exc.code, field))
            continue
        if result.status_code == 201:
            succeeded += 1
        else:
            code = ErrorCode(json.loads(bytes(result.body))["code"])
            if result.status_code >= 500:
                raise ApiError(code)
            errors.append(issue(row.number, code))
    return ImportResult(succeeded=succeeded, failed=len(rows) - succeeded, errors=errors)
