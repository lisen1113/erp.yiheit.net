"""Standard product master data and tracking guard on PostgreSQL."""

import base64
import csv
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from secrets import token_urlsafe
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from test_authorization import ORIGIN, create_company, create_user, grant
from test_authorization import auth_env as auth_env
from test_suppliers import write
from yihe_api.auth.router import session_factory
from yihe_api.main import app
from yihe_api.products import tracking
from yihe_api.products.imports import COLUMNS

ROOT = "/api/v1/products"
CAT = "/api/v1/product-categories"


@pytest.fixture
def catalog(monkeypatch, auth_env):  # noqa: F811
    monkeypatch.setenv("ERP_AUDIT_IDENTIFIER_KEY", token_urlsafe(48))
    with session_factory().begin() as db:
        company = create_company(db)
        maker, _, maker_headers = create_user(db, company, verified=True)
        reviewer, _, reviewer_headers = create_user(db, company, verified=True)
        for user, keys in (
            (
                maker,
                [
                    "product.view",
                    "product.create",
                    "product.edit",
                    "product.disable",
                    "product.import",
                    "product.legacy_mapping.manage",
                    "product.tracking_mode.change",
                    "product.tracking_mode.approve",
                    "product_category.create",
                    "product_category.edit",
                    "product_category.schema.manage",
                ],
            ),
            (
                reviewer,
                ["product.view", "product.tracking_mode.approve", "product.catalog.approve"],
            ),
        ):
            for key in keys:
                grant(db, user, key, "company")
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, maker_headers, reviewer_headers


def create(client, headers, path, payload):
    response = write(client, headers, "POST", path, payload)
    assert response.status_code == 201, response.text
    return response.json()["resource_id"]


def model(catalog, mode):
    client, maker, reviewer = catalog
    schema = {
        "type": "object",
        "properties": {"capacity": {"type": "integer", "minimum": 1}},
        "required": ["capacity"],
    }
    cat = create(
        client, maker, CAT, {"code": "HARDWARE", "name": "合成品类", "attribute_schema": schema}
    )
    unit = create(client, maker, ROOT + "/units", {"code": "EA", "name": "件"})
    brand = create(client, maker, ROOT + "/brands", {"code": "SYN", "name": "合成品牌"})
    defaults = {"version_no": 1, "unit_id": unit, "tax_rate": "0.1000", "tracking_mode": mode}
    result = write(client, maker, "PATCH", ROOT + f"/category-defaults/{cat}", defaults)
    assert result.status_code == 200, result.text
    spu = create(
        client,
        maker,
        ROOT + "/spus",
        {"spu_no": "ＭＯＤＥＬ", "name": "合成型号", "category_id": cat, "brand_id": brand},
    )
    base = {
        "sku_no": "ＳＫＵ",
        "spu_id": spu,
        "spec_name": "合成配置",
        "approved_name_cn": "合成配置",
        "attributes": {"capacity": 2},
    }
    assert write(client, maker, "POST", ROOT + "/skus", base).status_code == 409
    reviewed = write(client, reviewer, "POST", f"{ROOT}/spus/{spu}/approve", {"version_no": 1})
    assert reviewed.status_code == 200, reviewed.text
    return base, cat, defaults


@pytest.mark.parametrize("mode", ["quantity", "serial", "batch", "non_stock"])
def test_defaults_and_dynamic_attributes(catalog, mode):
    client, maker, _ = catalog
    base, cat, defaults = model(catalog, mode)
    sku = create(client, maker, ROOT + "/skus", base)
    for invalid in ({}, {"capacity": "two"}, {"capacity": 0}):
        result = write(
            client,
            maker,
            "POST",
            ROOT + "/skus",
            {**base, "sku_no": "INVALID", "attributes": invalid},
        )
        assert result.status_code == 422
    result = write(
        client,
        maker,
        "PATCH",
        ROOT + f"/category-defaults/{cat}",
        {
            **defaults,
            "version_no": 2,
            "tax_rate": "0.12",
            "tracking_mode": "quantity",
        },
    )
    assert result.status_code == 200, result.text
    data = client.get(f"{ROOT}/skus/{sku}", headers=maker).json()
    assert data["tracking_mode"] == mode and data["sku_no"] == "SKU"
    assert data["default_tax_rate"] == "0.1000" and data["unit_id"] == defaults["unit_id"]


def test_tracking_guard_approval_and_concurrent_code(catalog, monkeypatch):
    client, maker, reviewer = catalog
    base, _, _ = model(catalog, "quantity")
    barrier = Barrier(2)

    def send():
        barrier.wait(timeout=10)
        return write(client, maker, "POST", ROOT + "/skus", base)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(send), pool.submit(send)]
        results = [item.result() for item in results]
    assert sorted(result.status_code for result in results) == [201, 409]
    sku = next(result.json()["resource_id"] for result in results if result.status_code == 201)
    action = ROOT + f"/skus/{sku}/change-tracking-mode"
    request_id = create(
        client, maker, action, {"version_no": 1, "tracking_mode": "serial", "reason": "合成验证"}
    )
    approval = {"version_no": 1, "request_id": request_id}
    assert write(client, maker, "POST", action + "/approve", approval).status_code == 403
    monkeypatch.setattr(
        tracking,
        "TRANSACTION_FACT_PROVIDERS",
        (lambda *_: [tracking.TransactionFact.SALES_SHIPMENT_POSTED],),
    )
    locked = write(client, reviewer, "POST", action + "/approve", approval)
    assert locked.status_code == 409 and locked.json()["code"] == "PRODUCT_TRACKING_MODE_LOCKED"
    monkeypatch.setattr(tracking, "TRANSACTION_FACT_PROVIDERS", ())
    accepted = write(client, reviewer, "POST", action + "/approve", approval)
    assert accepted.status_code == 200, accepted.text
    assert client.get(f"{ROOT}/skus/{sku}", headers=maker).json()["tracking_mode"] == "serial"


def test_legacy_many_to_one_and_manual_confirmation(catalog):
    client, maker, _ = catalog
    base, _, _ = model(catalog, "quantity")
    sku = create(client, maker, ROOT + "/skus", base)
    path = ROOT + "/legacy-mappings"
    for legacy in ("legacy-1", "legacy-2"):
        mapping = create(
            client,
            maker,
            path,
            {
                "legacy_system": "synthetic",
                "legacy_product_id": legacy,
                "legacy_name": "测试旧商品",
                "target_sku_id": sku,
                "confidence": "0.10",
            },
        )
        rows = client.get(path, headers=maker).json()
        assert next(row for row in rows if row["id"] == mapping)["mapping_status"] == "SUGGESTED"
        result = write(
            client,
            maker,
            "POST",
            f"{path}/{mapping}/review",
            {
                "status": "CONFIRMED",
                "target_sku_id": sku,
                "reason": "已人工核对",
                "version_no": 1,
            },
        )
        assert result.status_code == 200, result.text
    assert {row["target_sku_id"] for row in client.get(path, headers=maker).json()} == {sku}
    assert len(client.get(ROOT + "/skus", headers=maker).json()) == 1


def test_import_preview_rolls_back_and_blocks_unreviewed_commit(catalog):
    client, maker, _ = catalog
    _, cat, _ = model(catalog, "serial")
    root = "/api/v1/product-import"
    for format in ("csv", "xlsx"):
        result = client.get(f"{root}/templates/{cat}", headers=maker, params={"format": format})
        assert result.status_code == 200, result.text
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerow(
        {
            "category_code": "HARDWARE",
            "brand_code": "SYN",
            "spu_no": "MODEL",
            "spu_name": "合成型号",
            "sku_no": "IMPORT",
            "approved_name_cn": "合成导入配置",
            "spec_name": "合成规格",
            "attributes": '{"capacity":3}',
        }
    )
    payload = {
        "format": "csv",
        "content_b64": base64.b64encode(output.getvalue().encode()).decode(),
    }
    result = client.post(root + "/preview", headers=maker, json=payload)
    assert result.status_code == 200 and result.json()["creates"] == 1, result.text
    assert client.get(ROOT + "/skus", headers=maker).json() == []
    commit = write(
        client,
        maker,
        "POST",
        root + "/commit",
        {**payload, "preview_token": result.json()["preview_token"]},
    )
    assert commit.status_code == 409 and commit.json()["code"] == "PRODUCT_IMPORT_REVIEW_PENDING"
    assert client.get(ROOT + "/skus", headers=maker).json() == []
