"""Category hierarchy, validation, and two-stage import against PostgreSQL."""

import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier
from uuid import uuid4

import pytest
from openpyxl import load_workbook
from test_authorization import auth_env as auth_env
from test_suppliers import users as users
from test_suppliers import write
from yihe_api.product_categories.schema import (
    compatibility,
    effective_schema,
    validate_attributes,
    validate_schema,
)

PATH = "/api/v1/product-categories"


def schema(key, rule):
    return {"type": "object", "properties": {key: rule}, "required": [key]}


@pytest.mark.parametrize(
    ("code", "key", "rule", "good", "bad"),
    [
        ("NOTEBOOK", "memory_gb", {"type": "integer", "minimum": 8}, 16, 4),
        ("RACK", "rack_units", {"type": "integer", "maximum": 8}, 2, 20),
        (
            "SUBSCRIPTION",
            "billing_cycle",
            {"type": "string", "enum": ["annual", "monthly"]},
            "annual",
            "week",
        ),
    ],
)
def test_non_surface_category_and_attributes(users, code, key, rule, good, bad):  # noqa: F811
    client, identities, _ = users
    admin = identities["admin"][1]
    spec = schema(key, rule)
    result = write(
        client, admin, "POST", PATH, {"code": code, "name": code, "attribute_schema": spec}
    )
    assert result.status_code == 201, result.text
    url = f"{PATH}/{result.json()['resource_id']}"
    assert (
        client.get(f"{url}/effective-schema", headers=admin).json()["properties"]
        == spec["properties"]
    )
    assert client.post(f"{url}/validate-attributes", headers=admin, json={key: good}).json() == {}
    assert key in client.post(f"{url}/validate-attributes", headers=admin, json={key: bad}).json()
    assert key in client.post(f"{url}/validate-attributes", headers=admin, json={}).json()


def test_tree_cycle_version_and_import(users):  # noqa: F811
    client, identities, _ = users
    admin = identities["admin"][1]
    key = str(uuid4())
    parent = {"code": " ｒｏｏｔ ", "name": "根分类"}
    result = write(client, admin, "POST", PATH, parent, key)
    assert result.status_code == 201, result.text
    root = result.json()["resource_id"]
    assert write(client, admin, "POST", PATH, parent, key).headers["Idempotency-Replayed"] == "true"
    assert (
        write(client, admin, "POST", PATH, {**parent, "code": "DIFFERENT"}, key).status_code == 409
    )
    assert (
        write(client, admin, "POST", PATH, {**parent, "name": "另一个名称"}).json()["code"]
        == "PRODUCT_CATEGORY_CODE_CONFLICT"
    )
    child = write(
        client, admin, "POST", PATH, {"code": "CHILD", "name": "子分类", "parent_id": root}
    )
    assert child.status_code == 201
    assert (
        write(
            client, admin, "POST", PATH, {"code": "OTHER", "name": "子分类", "parent_id": root}
        ).status_code
        == 409
    )
    for id in (root, child.json()["resource_id"]):
        assert (
            write(
                client, admin, "POST", f"{PATH}/{root}/move", {"parent_id": id, "version_no": 1}
            ).json()["code"]
            == "PRODUCT_CATEGORY_CYCLE"
        )
    assert (
        write(client, admin, "POST", f"{PATH}/{root}/disable", {"version_no": 1}).status_code == 409
    )
    assert (
        write(
            client, admin, "PATCH", f"{PATH}/{root}", {"version_no": 2, "name": "冲突"}
        ).status_code
        == 409
    )
    for format in ("csv", "xlsx"):
        assert client.get(f"{PATH}/imports/template/{format}", headers=admin).status_code == 200
    data = (
        b"code,name,parent_code,sort_order,schema_version,attribute_schema\nNEW,New,ROOT,0,1,{}\n"
    )
    payload = {"format": "csv", "content_b64": base64.b64encode(data).decode()}
    preview = client.post(f"{PATH}/imports/preview", headers=admin, json=payload)
    assert preview.status_code == 200, preview.text
    assert preview.json()["creates"] == 1 and not preview.json()["errors"]
    assert client.get(PATH, headers=admin).json()["total"] == 2
    token = preview.json()["preview_token"]
    commit_data = {**payload, "preview_token": token}
    key = str(uuid4())
    success = write(client, admin, "POST", f"{PATH}/imports/commit", commit_data, key)
    assert success.status_code == 200, success.text
    assert (
        write(client, admin, "POST", f"{PATH}/imports/commit", commit_data, key).headers[
            "Idempotency-Replayed"
        ]
        == "true"
    )
    assert client.get(PATH, headers=admin).json()["total"] == 3
    assert client.get(f"{PATH}/{root}", headers=identities["foreign"][1]).status_code == 404


def test_safe_subset_and_inheritance():
    parent = schema("model", {"type": "string", "minLength": 3, "pattern": "^[A-Z]{3}$"})
    local = schema("ports", {"type": "array", "items": {"type": "integer", "minimum": 1}})
    merged = effective_schema(parent, local)
    assert set(merged["required"]) == {"model", "ports"}
    assert not validate_attributes(merged, {"model": "ABC", "ports": [2]})
    assert validate_attributes(merged, {"model": "AA", "ports": [0]})
    assert compatibility(parent, {}) == "BREAKING"
    assert compatibility(parent, merged) == "REQUIRES_DATA_REVALIDATION"
    assert compatibility(parent, {**parent, "title": "Renamed"}) == "BACKWARD_COMPATIBLE"
    for value in (
        {"type": "object", "$ref": "https://example.invalid/schema"},
        schema("model", {"type": "string", "pattern": "(a+)+$"}),
        schema("model", {"type": "array", "items": {"type": "array"}}),
        {"type": "object", "properties": {f"key{i}": {"type": "string"} for i in range(65)}},
    ):
        with pytest.raises(ValueError):
            validate_schema(value)


def test_concurrent_create_move_and_schema_update(users):  # noqa: F811
    client, identities, _ = users
    headers = identities["admin"][1]

    def race(method, path, inputs):
        barrier = Barrier(2)

        def send(payload):
            barrier.wait(timeout=10)
            return write(client, headers, method, path, payload)

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(send, inputs))

    roots = race("POST", PATH, [{"code": "ＲＡＣＥ", "name": "一"}, {"code": "race", "name": "二"}])
    assert sorted(response.status_code for response in roots) == [201, 409]
    assert (
        next(response for response in roots if response.status_code == 409).json()["code"]
        == "PRODUCT_CATEGORY_CODE_CONFLICT"
    )
    root = next(response for response in roots if response.status_code == 201).json()["resource_id"]
    child = write(client, headers, "POST", PATH, {"code": "CHILD", "name": "子类"}).json()[
        "resource_id"
    ]
    moved = race(
        "POST",
        f"{PATH}/{child}/move",
        [
            {"version_no": 1, "parent_id": root},
            {"version_no": 1, "parent_id": None},
        ],
    )
    assert sorted(response.status_code for response in moved) == [200, 409]
    response = race(
        "PATCH",
        f"{PATH}/{root}",
        [
            {"version_no": 1, "attribute_schema": schema("term", {"type": "integer"})},
            {"version_no": 1, "attribute_schema": schema("range", {"type": "string"})},
        ],
    )
    assert sorted(item.status_code for item in response) == [200, 409]
    assert client.get(f"{PATH}/{root}", headers=headers).json()["schema_version"] == 2


def test_xlsx_preview_errors_and_commit(users):  # noqa: F811
    client, identities, _ = users
    headers = identities["admin"][1]
    template = client.get(f"{PATH}/imports/template/xlsx", headers=headers)
    book = load_workbook(BytesIO(template.content))
    sheet = book.active
    assert sheet is not None
    sheet.append(
        [
            "SERVICE",
            "服务",
            None,
            0,
            1,
            '{"type":"object","properties":{"region":{"type":"string"}}}',
        ]
    )

    def verify_preview():
        buffer = BytesIO()
        book.save(buffer)
        data = {"format": "xlsx", "content_b64": base64.b64encode(buffer.getvalue()).decode()}
        preview = client.post(f"{PATH}/imports/preview", headers=headers, json=data)
        assert preview.status_code == 200, preview.text
        return data, preview.json()

    sheet.append(["CHILD", "无效子类", "MISSING", 0, 1, "{}"])
    _, rejected = verify_preview()
    assert not rejected["preview_token"]
    assert rejected["errors"][0]["row"] == 3 and rejected["errors"][0]["field"] == "parent_code"
    sheet.delete_rows(3)
    data, report = verify_preview()
    book.close()
    result = write(
        client,
        headers,
        "POST",
        f"{PATH}/imports/commit",
        {
            **data,
            "preview_token": report["preview_token"],
        },
    )
    assert result.status_code == 200, result.text
    assert client.get(PATH, headers=headers).json()["items"][0]["code"] == "SERVICE"
