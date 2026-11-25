"""Authorized standard customer file import with isolated per-row writes."""

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
from yihe_api.master_data.customer_router import PATH, create_customer_record
from yihe_api.master_data.customer_spreadsheet import (
    Format,
    ImportFile,
    ImportIssue,
    ImportResult,
    SpreadsheetError,
    parse_document,
    row_payload,
    template,
    validation_issues,
)
from yihe_api.rbac.router import Current

router = APIRouter(prefix="/customers", tags=["customers"])


def row_request(request: Request, batch: UUID, row: int) -> Request:
    digest = hashlib.sha256(batch.bytes + row.to_bytes(4, "big")).digest()
    key = UUID(bytes=digest[:16], version=4)
    headers = [(k, v) for k, v in request.scope["headers"] if k.lower() != b"idempotency-key"]
    return Request(
        {**request.scope, "headers": [*headers, (b"idempotency-key", str(key).encode())]}
    )


def issue(row: int, code: ErrorCode, field: str | None = None) -> ImportIssue:
    fields = {
        ErrorCode.CUSTOMER_NO_CONFLICT: "customer_no",
        ErrorCode.BUSINESS_PARTNER_TAX_NO_CONFLICT: "partner.tax_no",
        ErrorCode.CUSTOMER_IMPORT_PARTNER_MISMATCH: "partner.tax_no",
        ErrorCode.VALIDATION_ERROR: "salesperson_id",
        ErrorCode.CONFLICT: "business_partner_id",
        ErrorCode.NOT_FOUND: "business_partner_id",
        ErrorCode.CUSTOMER_UNAVAILABLE: "business_partner_id",
    }
    return ImportIssue(
        row=row, field=field or fields.get(code, "row"), code=code.value, message=ERRORS[code][1]
    )


@router.get(
    "/import-template/{file_format}",
    operation_id="getCustomerImportTemplate",
    response_class=Response,
)
def get_template(file_format: Format, principal: Current) -> Response:
    principal.require("customer.create")
    media_type = (
        "text/csv; charset=utf-8"
        if file_format == "csv"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return Response(
        template(file_format),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="customers.{file_format}"'},
    )


@router.post("/import", operation_id="importCustomers", response_model=ImportResult)
def import_customers(
    payload: ImportFile,
    request: Request,
    db: DB,
    principal: Current,
    idempotency_key: partner_api.Idempotency,
) -> ImportResult | Response:
    principal.require("customer.create")
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
        Operation("customer.import", "customer_import", None, f"{PATH}/import"),
        {"format": payload.format, "file_sha256": hashlib.sha256(data).hexdigest()},
        lambda _db, _actor: Success("customer_import", uuid4()),
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
            customer = row_payload(row)
        except ValidationError as exc:
            errors.extend(validation_issues(row.number, exc))
            continue
        try:
            with db.begin_nested():
                result = create_customer_record(
                    customer,
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
