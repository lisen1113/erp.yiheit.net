"""Standard customer import tests against the same PostgreSQL and authorization path."""

import base64
import csv
from io import BytesIO, StringIO
from uuid import uuid4

import pytest
from openpyxl import Workbook, load_workbook
from test_customers import PARTNERS, PATH, write
from test_customers import auth_env as auth_env
from test_customers import users as users

COLUMNS = [
    "customer_no",
    "salesperson_id",
    "partner.code",
    "partner.name",
    "partner.tax_no",
    "partner.credit_limit",
]


def spreadsheet(file_format, rows):
    if file_format == "csv":
        output = StringIO()
        csv.writer(output).writerows([COLUMNS, *rows])
        return output.getvalue().encode("utf-8-sig")
    book = Workbook()
    book.active.append(COLUMNS)
    for row in rows:
        book.active.append(row)
    data = BytesIO()
    book.save(data)
    book.close()
    return data.getvalue()


def upload(client, headers, file_format, rows, key=None):
    return write(
        client,
        headers,
        "POST",
        f"{PATH}/import",
        {
            "format": file_format,
            "content_b64": base64.b64encode(spreadsheet(file_format, rows)).decode(),
        },
        key,
    )


@pytest.mark.parametrize("file_format", ["csv", "xlsx"])
def test_templates_and_import_reuse_supplier_and_isolate_bad_rows(users, file_format):  # noqa: F811
    client, identities, _ = users
    employee, admin = identities["admin"]
    response = client.get(f"{PATH}/import-template/{file_format}", headers=admin)
    assert response.status_code == 200
    assert (
        response.headers["Content-Disposition"] == f'attachment; filename="customers.{file_format}"'
    )
    if file_format == "csv":
        assert set(COLUMNS) <= set(next(csv.reader(StringIO(response.content.decode("utf-8-sig")))))
    else:
        book = load_workbook(BytesIO(response.content), read_only=True)
        try:
            assert set(COLUMNS) <= set(next(book.active.values))
        finally:
            book.close()

    supplier = write(
        client,
        admin,
        "POST",
        PARTNERS,
        {
            "code": "SUPPLIER-EXISTING",
            "name": "Synthetic supplier",
            "partner_type": "SUPPLIER",
            "tax_no": "TAX-0123",
        },
    )
    assert supplier.status_code == 201, supplier.text
    supplier_id = supplier.json()["resource_id"]
    rows = [
        [" Ｃ－００１ ", employee, "SUPPLIER-EXISTING", "Synthetic supplier", "tax -0123", "0.00"],
        ["C-002", employee, "NEW", "Synthetic new", "012345", "120.25"],
        ["c-001", employee, "ORPHAN", "Synthetic orphan", "", "0.00"],
        ["C-004", employee, "INVALID", "Synthetic invalid", "", "-1.00"],
        ["C-005", "not-a-uuid", "INVALID2", "Synthetic invalid 2", "", "0.00"],
    ]
    # The retry is byte-for-byte identical, including the XLSX container metadata.
    payload = {
        "format": file_format,
        "content_b64": base64.b64encode(spreadsheet(file_format, rows)).decode(),
    }
    key = str(uuid4())
    response = write(client, admin, "POST", f"{PATH}/import", payload, key)
    assert response.status_code == 200, response.text
    report = response.json()
    assert (report["succeeded"], report["failed"]) == (2, 3), report
    assert [(row["row"], row["field"], row["code"]) for row in report["errors"]] == [
        (4, "customer_no", "CUSTOMER_NO_CONFLICT"),
        (5, "partner.credit_limit", "greater_than_equal"),
        (6, "salesperson_id", "uuid_parsing"),
    ]
    if file_format == "csv":
        retry = write(client, admin, "POST", f"{PATH}/import", payload, key)
        assert retry.status_code == 200 and retry.json() == report
        mismatch = upload(client, admin, "csv", rows[:1], key)
        assert mismatch.status_code == 409 and mismatch.json()["code"] == "IDEMPOTENCY_CONFLICT"
    roster = client.get(PATH, headers=admin).json()
    assert roster["total"] == 2
    assert roster["items"][0]["business_partner_id"] == supplier_id
    assert roster["items"][0]["partner"]["partner_type"] == "BOTH"
    assert roster["items"][1]["partner"]["tax_no"] == "012345"
    assert roster["items"][1]["partner"]["credit_limit"] == "120.25"
    assert client.get(PARTNERS, headers=admin).json()["total"] == 2


def test_import_authorization_validation_and_formula_rejection(users):  # noqa: F811
    client, identities, _ = users
    employee, admin = identities["admin"]
    rows = [["C-001", employee, "P-001", "=1+1", "", "0.00"]]
    denied = upload(client, identities["finance"][1], "xlsx", rows)
    assert denied.status_code == 403
    response = upload(client, admin, "xlsx", rows)
    assert response.status_code == 200, response.text
    assert response.json()["succeeded"] == 0
    assert response.json()["errors"][0]["field"] == "partner.name"
    assert response.json()["errors"][0]["code"] == "CELL_TYPE"
    malformed = write(
        client,
        admin,
        "POST",
        f"{PATH}/import",
        {
            "format": "csv",
            "content_b64": base64.b64encode(b"customer_no,unknown\nC-001,123").decode(),
        },
    )
    assert malformed.status_code == 422
    assert malformed.json()["field_errors"][0]["field"] == "rows.1.file"
    assert client.get(PATH, headers=admin).json()["total"] == 0


def test_import_cannot_link_to_conflicting_partner_keys(users):  # noqa: F811
    client, identities, _ = users
    employee, admin = identities["admin"]
    for code, tax in [("MATCH-A", "TAXA"), ("MATCH-B", "TAXB")]:
        supplier = write(
            client,
            admin,
            "POST",
            PARTNERS,
            {
                "code": code,
                "name": "Synthetic supplier",
                "partner_type": "SUPPLIER",
                "tax_no": tax,
            },
        )
        assert supplier.status_code == 201
    response = upload(
        client, admin, "csv", [["C-001", employee, "MATCH-A", "Synthetic", "TAXB", "0.00"]]
    )
    assert response.status_code == 200
    assert response.json()["failed"] == 1
    assert response.json()["errors"][0]["code"] == "CUSTOMER_IMPORT_PARTNER_MISMATCH"
    assert client.get(PATH, headers=admin).json()["total"] == 0
    assert {
        partner["partner_type"] for partner in client.get(PARTNERS, headers=admin).json()["items"]
    } == {"SUPPLIER"}


def test_shared_bank_account_width_preserves_masks_and_mfa(users):  # noqa: F811
    client, identities, _ = users
    employee, admin = identities["admin"]
    bank = "S" * 251 + "0130"
    payload = {
        "customer_no": "BANK-1",
        "salesperson_id": employee,
        "partner": {"code": "BANK-1", "name": "Synthetic", "invoice_bank_account": bank},
    }
    response = write(client, admin, "POST", PATH, payload)
    assert response.status_code == 201, response.text
    id = response.json()["resource_id"]
    detail = f"{PATH}/{id}"
    assert client.get(detail, headers=admin).json()["partner"]["invoice_bank_account"] == "****0130"
    for user, expected in [("unverified", "****0130"), ("finance", bank)]:
        response = client.get(
            detail, params={"reveal_bank_account": True}, headers=identities[user][1]
        )
        assert response.json()["partner"]["invoice_bank_account"] == expected
    payload["partner"]["invoice_bank_account"] += "X"
    assert write(client, admin, "POST", PATH, payload).status_code == 422
