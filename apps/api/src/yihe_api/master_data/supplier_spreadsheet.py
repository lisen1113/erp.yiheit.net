"""Bounded standard supplier spreadsheet input. No legacy mapping or transactional data."""

import csv
from io import BytesIO, StringIO
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from openpyxl import Workbook  # type: ignore[import-untyped]
from openpyxl.utils.exceptions import InvalidFileException  # type: ignore[import-untyped]
from pydantic import ValidationError

from yihe_api.master_data.customer_spreadsheet import (
    MAX_FILE,
    Format,
    ImportIssue,
    InputRow,
    SpreadsheetError,
    values_from_csv,
    values_from_xlsx,
)
from yihe_api.master_data.supplier_schemas import SupplierCreate, SupplierPartnerCreate

PROFILE_FIELDS = tuple(key for key in SupplierCreate.model_fields if key != "partner")
PARTNER_FIELDS = tuple(SupplierPartnerCreate.model_fields)
COLUMNS = (*PROFILE_FIELDS, *(f"partner.{key}" for key in PARTNER_FIELDS))
REQUIRED = {"supplier_no", "buyer_id"}


def template(file_format: Format) -> bytes:
    if file_format == "csv":
        output = StringIO(newline="")
        csv.writer(output).writerow(COLUMNS)
        return output.getvalue().encode("utf-8-sig")
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Suppliers"
    sheet.append(COLUMNS)
    for column in sheet[1]:
        sheet.column_dimensions[column.column_letter].number_format = "@"
        sheet.column_dimensions[column.column_letter].width = 26
    sheet.freeze_panes = "C2"
    content = BytesIO()
    workbook.save(content)
    workbook.close()
    return content.getvalue()


def parse_document(data: bytes, file_format: Format) -> list[InputRow]:
    if not data or len(data) > MAX_FILE:
        raise SpreadsheetError(1, "file", "文件为空或超过 256 KiB。")
    try:
        rows = values_from_csv(data) if file_format == "csv" else values_from_xlsx(data)
    except SpreadsheetError:
        raise
    except (
        UnicodeError,
        ValueError,
        BadZipFile,
        InvalidFileException,
        KeyError,
        OSError,
        ParseError,
        TypeError,
        OverflowError,
    ):
        raise SpreadsheetError(1, "file", "文件格式无效。") from None
    if not rows or rows[0][0] != 1:
        raise SpreadsheetError(1, "file", "第一行必须是模板列名。")
    header = [value.strip() if isinstance(value, str) else "" for value in rows[0][1]]
    if rows[0][2] or len(set(header)) != len(header) or set(header) - set(COLUMNS):
        raise SpreadsheetError(1, "file", "列名为空、重复或不在模板内。")
    if not REQUIRED <= set(header):
        raise SpreadsheetError(1, "file", "缺少 supplier_no 或 buyer_id 列。")
    if len(rows) == 1:
        raise SpreadsheetError(2, "file", "文件没有供应商数据行。")
    result = []
    for number, cells, formulas in rows[1:]:
        if not any(value not in (None, "") for value in cells) and not formulas:
            continue
        issues = []
        values: dict[str, object] = {}
        if len(cells) > len(header):
            issues.append(
                ImportIssue(
                    row=number,
                    field="file",
                    code="EXTRA_COLUMNS",
                    message="数据列多于表头。",
                )
            )
        for index, (column, value) in enumerate(zip(header, cells, strict=False)):
            if index in formulas or (
                value not in (None, "")
                and not isinstance(value, str)
                and not (
                    column in {"partner.payment_terms_days", "payment_term_days"}
                    and type(value) is int
                )
            ):
                issues.append(
                    ImportIssue(
                        row=number,
                        field=column,
                        code="CELL_TYPE",
                        message="请输入文本，账期可使用整数。单元格不得使用公式。",
                    )
                )
                continue
            if isinstance(value, str):
                value = value.strip()
            if value not in (None, ""):
                values[column] = value
        result.append(InputRow(number, values, issues))
    if not result:
        raise SpreadsheetError(2, "file", "文件没有供应商数据行。")
    return result


def row_payload(row: InputRow) -> SupplierCreate:
    profile = {field: value for field, value in row.values.items() if field in PROFILE_FIELDS}
    partner = {
        field.removeprefix("partner."): value
        for field, value in row.values.items()
        if field.startswith("partner.")
    }
    if partner or not profile.get("business_partner_id"):
        profile["partner"] = partner
    return SupplierCreate.model_validate(profile)


VALIDATION_MESSAGES = {
    "missing": "此字段必填。",
    "uuid_parsing": "请输入有效的 UUID。",
    "int_parsing": "请输入整数。",
    "greater_than_equal": "不能小于零。",
    "decimal_max_digits": "金额超过允许的精度。",
    "decimal_max_places": "金额最多保留两位小数。",
    "string_too_long": "文本超出最大长度。",
    "string_too_short": "文本不能为空。",
    "literal_error": "只接受 CNY。",
    "value_error": "请确认只选择一种主体来源，金额使用十进制文本。",
}


def validation_issues(number: int, exc: ValidationError) -> list[ImportIssue]:
    # Pydantic includes raw inputs in its error objects. Return only the field and error class.
    return [
        ImportIssue(
            row=number,
            field=".".join(map(str, error["loc"])) or "business_partner_id",
            code=str(error["type"]),
            message=VALIDATION_MESSAGES.get(str(error["type"]), "字段值不符合供应商创建规则。"),
        )
        for error in exc.errors(include_input=False, include_context=False, include_url=False)
    ]
