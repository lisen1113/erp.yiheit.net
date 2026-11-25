"""Two-stage category definition import. Preview does not persist category changes."""

import base64
import binascii
import csv
import hashlib
import hmac
import json
import os
from io import BytesIO, StringIO
from uuid import UUID, uuid4

from fastapi import Request, Response
from openpyxl import Workbook  # type: ignore[import-untyped]
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from yihe_api.audit.service import Actor
from yihe_api.auth.router import DB
from yihe_api.errors import ApiError, ErrorCode
from yihe_api.idempotency.service import FinalFailure, Operation, Success
from yihe_api.master_data.customer_spreadsheet import (
    MAX_FILE,
    Format,
    ImportFile,
    ImportIssue,
    values_from_csv,
    values_from_xlsx,
)
from yihe_api.master_data.router import Idempotency, dispatch
from yihe_api.product_categories.models import ProductCategory
from yihe_api.product_categories.router import (
    MODULE,
    PATH,
    Create,
    audit,
    flush,
    load,
    normalize_identity,
    rebuild,
    require,
    update_schema,
)
from yihe_api.product_categories.router import (
    router as router,
)
from yihe_api.product_categories.schema import validate_schema
from yihe_api.rbac.router import Current

COLUMNS = ("code", "name", "parent_code", "sort_order", "schema_version", "attribute_schema")


class CommitFile(ImportFile):
    preview_token: str


class Preview(BaseModel):
    creates: int
    updates: int
    conflicts: int
    errors: list[ImportIssue]
    preview_token: str | None = None


@router.get("/imports/template/{format}", operation_id="downloadCategoryTemplate")
def template(format: Format, principal: Current) -> Response:
    require(principal, "import")
    if format == "csv":
        buf = StringIO(newline="")
        csv.writer(buf).writerow(COLUMNS)
        data = buf.getvalue().encode("utf-8-sig")
        mime = "text/csv; charset=utf-8"
    else:
        workbook = Workbook()
        sheet = workbook.active
        assert sheet is not None
        sheet.title = "Product categories"
        sheet.append(COLUMNS)
        for cell in sheet[1]:
            sheet.column_dimensions[cell.column_letter].number_format = "@"
            sheet.column_dimensions[cell.column_letter].width = 24
        buffer = BytesIO()
        workbook.save(buffer)
        workbook.close()
        data = buffer.getvalue()
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(
        data,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="product-categories.{format}"'},
    )


def issue(row: int, field: str, message: str, code: str = "INVALID_ROW") -> ImportIssue:
    return ImportIssue(row=row, field=field, code=code, message=message)


def decode(payload: ImportFile) -> bytes:
    try:
        data = base64.b64decode(payload.content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(ErrorCode.VALIDATION_ERROR) from None
    if not data or len(data) > MAX_FILE:
        raise ApiError(ErrorCode.VALIDATION_ERROR)
    return data


def inspect_file(
    data: bytes, format: Format, db: Session, company: UUID
) -> tuple[Preview, list[tuple[Create, str | None, int]]]:
    report = Preview(creates=0, updates=0, conflicts=0, errors=[])
    try:
        rows = values_from_csv(data) if format == "csv" else values_from_xlsx(data)
    except Exception as exc:
        # Format readers include parser-specific errors; no untrusted exception text is returned.
        from xml.etree.ElementTree import ParseError
        from zipfile import BadZipFile

        from openpyxl.utils.exceptions import InvalidFileException  # type: ignore[import-untyped]

        if not isinstance(
            exc, (ValueError, BadZipFile, InvalidFileException, ParseError, KeyError)
        ):
            raise
        report.errors.append(issue(1, "file", "无法读取文件或超过模板限制"))
        return report, []
    if not rows or tuple(v for v in rows[0][1] if v is not None) != COLUMNS:
        report.errors.append(issue(1, "file", "请使用商品分类导入模板"))
        return report, []
    existing = load(db, company)
    current = {row.code: row for row in existing.values()}
    by_code = {
        key: (
            row.name,
            row.attribute_schema,
            row.schema_version,
            existing[row.parent_id].code if row.parent_id else None,
            row.is_active,
        )
        for key, row in current.items()
    }
    seen: set[str] = set()
    entries = []
    for n, cells, formulas in rows[1:]:
        if not any(c not in (None, "") for c in cells) and not formulas:
            continue
        if formulas or len(cells) > len(COLUMNS):
            report.errors.append(issue(n, "file", "不允许公式或多余列"))
            continue
        fields = dict(zip(COLUMNS, cells, strict=False))
        field = "code"
        try:
            code = normalize_identity(str(fields.get("code") or ""), 32)
            if code in seen:
                raise ValueError("文件中出现重复分类编码")
            field = "name"
            name = str(fields.get("name") or "").strip()
            field = "parent_code"
            raw_parent = str(fields.get("parent_code") or "").strip()
            parent = normalize_identity(raw_parent, 32) if raw_parent else None
            field = "sort_order"
            sort = int(str(fields.get("sort_order") or "0"))
            field = "schema_version"
            version = int(str(fields.get("schema_version") or "1"))
            field = "attribute_schema"
            schema = json.loads(str(fields.get("attribute_schema") or "{}"))
            if not isinstance(schema, dict):
                raise ValueError("Schema 必须是 JSON 对象")
            validate_schema(schema)
            payload = Create(code=code, name=name, sort_order=sort, attribute_schema=schema)
        except ValidationError as exc:
            report.errors.extend(
                issue(n, str(error["loc"][0]), "字段超出允许范围或格式不正确")
                for error in exc.errors(include_input=False)
            )
            continue
        except (ValueError, FinalFailure) as exc:
            report.errors.append(
                issue(n, field, str(exc) if isinstance(exc, ValueError) else "字段无效")
            )
            continue
        seen.add(code)
        prior = by_code.get(code)
        expected = prior[2] if prior else 1
        if version != expected:
            report.conflicts += 1
            report.errors.append(
                issue(n, "schema_version", "Schema 版本已变化", "VERSION_CONFLICT")
            )
            continue
        if prior and prior[3] != parent:
            report.conflicts += 1
            report.errors.append(
                issue(n, "parent_code", "已有分类请使用移动功能", "PARENT_CONFLICT")
            )
            continue
        if parent and (parent not in by_code or not by_code[parent][4]):
            report.errors.append(issue(n, "parent_code", "父分类须已存在、已启用或已在上方定义"))
            continue
        normalized_name = normalize_identity(name, 300, fold=True)
        if any(
            key != code
            and value[3] == parent
            and normalize_identity(value[0], 300, fold=True) == normalized_name
            for key, value in by_code.items()
        ):
            report.conflicts += 1
            report.errors.append(issue(n, "name", "同级名称冲突", "NAME_CONFLICT"))
            continue
        by_code[code] = (name, schema, version, parent, prior[4] if prior else True)
        # Validate every descendant after each update, exactly as regular writes do.
        try:
            for key in by_code:
                path = []
                cursor: str | None = key
                while cursor is not None:
                    node = by_code[cursor]
                    path.append(node[1])
                    cursor = node[3]
                effective = validate_schema(path[-1])
                from yihe_api.product_categories.schema import effective_schema

                for ruleset in reversed(path[:-1]):
                    effective = effective_schema(effective, ruleset)
        except ValueError:
            if prior:
                by_code[code] = prior
            else:
                del by_code[code]
            report.errors.append(issue(n, "attribute_schema", "规则与父级或子级不兼容"))
            continue
        if prior:
            report.updates += 1
        else:
            report.creates += 1
        entries.append((payload, parent, n))
    if not entries and not report.errors:
        report.errors.append(issue(2, "file", "没有商品分类数据"))
    return report, entries


def fingerprint(db: Session, actor: Actor, payload: ImportFile, data: bytes) -> str:
    key = os.environ.get("ERP_AUDIT_IDENTIFIER_KEY", "").encode()
    if len(key) < 32:
        raise ApiError(ErrorCode.INTERNAL_ERROR)
    rows = load(db, actor.company_id)
    revisions = sorted((str(row.id), row.version_no) for row in rows.values())
    material = json.dumps(
        [
            str(actor.company_id),
            str(actor.user_id),
            payload.format,
            hashlib.sha256(data).hexdigest(),
            revisions,
        ],
        separators=(",", ":"),
    ).encode()
    return hmac.new(key, b"category-import-preview:" + material, "sha256").hexdigest()


def actor_for(request: Request) -> Actor:
    actor = getattr(request.state, "audit_actor", None)
    if not isinstance(actor, Actor):
        raise ApiError(ErrorCode.FORBIDDEN)
    return actor


@router.post("/imports/preview", operation_id="previewCategoryImport")
def preview(payload: ImportFile, request: Request, db: DB, principal: Current) -> Preview:
    for action in ("import", "view", "create", "edit", "schema.manage"):
        require(principal, action)
    actor = actor_for(request)
    data = decode(payload)
    load(db, actor.company_id, lock=True)
    report, _ = inspect_file(data, payload.format, db, actor.company_id)
    if not report.errors:
        report.preview_token = fingerprint(db, actor, payload, data)
    audit(
        db,
        request,
        actor,
        actor.company_id,
        "import_previewed",
        {"creates": report.creates, "updates": report.updates, "errors": len(report.errors)},
    )
    return report


@router.post("/imports/commit", operation_id="commitCategoryImport")
def commit(
    payload: CommitFile, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    for action in ("import", "create", "edit", "schema.manage"):
        require(principal, action)
    data = decode(payload)

    def work(tx: Session, actor: Actor) -> Success:
        rows = load(tx, actor.company_id, lock=True)
        if not hmac.compare_digest(fingerprint(tx, actor, payload, data), payload.preview_token):
            raise FinalFailure(ErrorCode.VERSION_CONFLICT)
        report, entries = inspect_file(data, payload.format, tx, actor.company_id)
        if report.errors:
            raise FinalFailure(ErrorCode.VALIDATION_ERROR)
        code_to_id = {row.code: row.id for row in rows.values()}
        for entry, parent, _ in entries:
            id = code_to_id.get(entry.code)
            if id is None:
                id = uuid4()
                row = ProductCategory(
                    id=id,
                    company_id=actor.company_id,
                    code=entry.code,
                    parent_id=code_to_id.get(parent) if parent else None,
                    path="",
                    level=0,
                    name=entry.name,
                    normalized_name=normalize_identity(entry.name, 300, fold=True),
                    sort_order=entry.sort_order,
                    attribute_schema=entry.attribute_schema,
                    schema_version=1,
                    created_by=actor.user_id,
                    updated_by=actor.user_id,
                )
                rows[id] = row
                code_to_id[entry.code] = id
                tx.add(row)
                audit(tx, request, actor, id, "created")
            else:
                row = rows[id]
                update_schema(tx, request, actor, row, entry.attribute_schema)
                row.name, row.sort_order = entry.name, entry.sort_order
                row.normalized_name = normalize_identity(entry.name, 300, fold=True)
                row.version_no += 1
                row.updated_by = actor.user_id
                audit(tx, request, actor, id, "updated")
            rebuild(rows, actor, id)
            flush(tx)
        batch = uuid4()
        audit(
            tx,
            request,
            actor,
            batch,
            "imported",
            {"creates": report.creates, "updates": report.updates},
        )
        return Success(MODULE, batch)

    return dispatch(
        db,
        request,
        Operation(f"{MODULE}.import", MODULE, None, f"{PATH}/imports/commit"),
        {
            "sha256": hashlib.sha256(data).hexdigest(),
            "format": payload.format,
            "preview_token": payload.preview_token,
        },
        work,
    )
