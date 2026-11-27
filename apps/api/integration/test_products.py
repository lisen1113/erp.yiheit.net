"""Standard product master data and tracking guard on PostgreSQL."""

import base64
import csv
import hashlib
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from secrets import token_urlsafe
from threading import Barrier
from time import perf_counter
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from test_authorization import ORIGIN, create_company, create_user, grant
from test_authorization import auth_env as auth_env
from test_suppliers import write
from yihe_api.audit.models import AuditLog
from yihe_api.auth.router import session_factory
from yihe_api.main import app
from yihe_api.products import service, tracking
from yihe_api.products.imports import COLUMNS
from yihe_api.products.models import ProductBrand, ProductSPU, ProductUnit

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
                    "product.import.approve",
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
                [
                    "product.view",
                    "product.tracking_mode.approve",
                    "product.catalog.approve",
                    "product.import.approve",
                ],
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


def product_file(count=1, *, existing=False):
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=COLUMNS)
    writer.writeheader()
    for index in range(count):
        code = "MODEL" if existing else f"IMPORT-SPU-{index // 4}"
        writer.writerow(
            {
                "category_code": "HARDWARE",
                "brand_code": "SYN",
                "spu_no": code,
                "spu_name": "合成型号" if existing else code,
                "sku_no": f"IMPORT-{index}",
                "approved_name_cn": "合成导入配置",
                "spec_name": "合成规格",
                "attributes": '{"capacity":3}',
            }
        )
    return output.getvalue().encode()


IMPORT = "/api/v1/product-import"


def upload(client, maker, raw):
    result = client.post(
        IMPORT + "/preview",
        headers=maker,
        json={
            "source_filename": "synthetic.csv",
            "format": "csv",
            "content_b64": base64.b64encode(raw).decode(),
        },
    )
    assert result.status_code == 200, result.text
    return result.json()


def batch_action(client, headers, id, action, version, *, key=None, **extra):
    url = f"{IMPORT}/batches/{id}"
    batch = client.get(url, headers=headers).json()
    payload = {
        "version_no": version,
        "preview_hash": batch["preview_hash"],
        "source_file_sha256": batch["source_file_sha256"],
        **extra,
    }
    return write(client, headers, "POST", f"{url}/{action}", payload, key)


@pytest.mark.parametrize("count", [1, 200])
def test_frozen_import_batch(catalog, count):
    client, maker, reviewer = catalog
    _, cat, _ = model(catalog, "serial")
    for format in ("csv", "xlsx"):
        result = client.get(f"{IMPORT}/templates/{cat}", headers=maker, params={"format": format})
        assert result.status_code == 200, result.text
    raw = product_file(count)
    started = perf_counter()
    result = upload(client, maker, raw)
    preview_seconds = perf_counter() - started
    id = result["batch_id"]
    assert not result["errors"] and result["new_spus"] == (count + 3) // 4, result
    assert client.get(ROOT + "/skus", headers=maker).json() == []
    if count == 1:
        repeat = upload(client, maker, raw)
        assert repeat["normalized_payload_hash"] == result["normalized_payload_hash"]
        assert batch_action(client, maker, id, "commit", 1).status_code == 409
        assert (
            batch_action(client, maker, id, "submit", 1, source_file_sha256="0" * 64).status_code
            == 409
        )
    assert batch_action(client, maker, id, "submit", 1).status_code == 200
    assert batch_action(client, maker, id, "approve", 2).status_code == 403
    assert client.get(f"{IMPORT}/batches/{id}/source", headers=reviewer).content == raw
    assert batch_action(client, reviewer, id, "approve", 2).status_code == 200
    key = str(uuid4())
    started = perf_counter()
    result = batch_action(client, maker, id, "commit", 3, key=key)
    commit_seconds = perf_counter() - started
    if count == 200:
        print(f"50 SPU + 200 SKU: Preview {preview_seconds:.2f}s, Commit {commit_seconds:.2f}s")
    assert result.status_code == 200, result.text
    assert (
        batch_action(client, maker, id, "commit", 3, key=key).headers["Idempotency-Replayed"]
        == "true"
    )
    assert batch_action(client, maker, id, "commit", 4, key=key).status_code == 409
    batch = client.get(f"{IMPORT}/batches/{id}", headers=reviewer).json()
    assert batch["status"] == "COMMITTED" and batch["sku_count"] == count
    assert batch["source_file_sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(client.get(ROOT + "/skus?limit=200", headers=maker).json()) == count


def test_existing_spu_and_stale_category(catalog):
    client, maker, reviewer = catalog
    _, cat, _ = model(catalog, "serial")
    result = upload(client, maker, product_file(existing=True))
    id = result["batch_id"]
    assert result["existing_spu_skus"] == 1 and result["new_spus"] == 0, result
    assert batch_action(client, maker, id, "submit", 1).status_code == 200
    assert batch_action(client, reviewer, id, "approve", 2).status_code == 200
    changed = write(
        client,
        maker,
        "PATCH",
        f"{CAT}/{cat}",
        {
            "version_no": 2,
            "attribute_schema": {
                "type": "object",
                "properties": {"capacity": {"type": "integer", "minimum": 2}},
                "required": ["capacity"],
            },
        },
    )
    assert changed.status_code == 200
    response = batch_action(client, maker, id, "commit", 3)
    assert (
        response.status_code == 409 and response.json()["code"] == "PRODUCT_IMPORT_SNAPSHOT_STALE"
    )
    assert client.get(f"{IMPORT}/batches/{id}", headers=maker).json()["status"] == "FAILED"
    assert client.get(ROOT + "/skus", headers=maker).json() == []


def test_last_sku_failure_rolls_back_all_new_products(catalog, monkeypatch):
    client, maker, reviewer = catalog
    model(catalog, "serial")
    id = upload(client, maker, product_file(5))["batch_id"]
    assert batch_action(client, maker, id, "submit", 1).status_code == 200
    assert batch_action(client, reviewer, id, "approve", 2).status_code == 200
    original = service.create_sku
    calls = 0

    def fail_last(db, company, actor, payload, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 5:
            spu = db.get_one(ProductSPU, payload.spu_id)
            assert spu.approved_by and spu.approved_by != spu.created_by
        if calls == 10:
            payload = payload.model_copy(update={"sku_no": "IMPORT-0"})
        return original(db, company, actor, payload, **kwargs)

    monkeypatch.setattr(service, "create_sku", fail_last)
    result = batch_action(client, maker, id, "commit", 3)
    assert result.status_code == 409 and calls == 10
    assert len(client.get(ROOT + "/spus", headers=maker).json()) == 1
    assert client.get(ROOT + "/skus", headers=maker).json() == []
    assert client.get(f"{IMPORT}/batches/{id}", headers=maker).json()["status"] == "FAILED"


@pytest.mark.parametrize("directory", ["category", "brand", "unit"])
def test_inactive_dependency_invalidates_snapshot(catalog, directory):
    client, maker, reviewer = catalog
    _, cat, defaults = model(catalog, "serial")
    id = upload(client, maker, product_file())["batch_id"]
    assert batch_action(client, maker, id, "submit", 1).status_code == 200
    assert batch_action(client, reviewer, id, "approve", 2).status_code == 200
    if directory == "category":
        assert (
            write(client, maker, "POST", f"{CAT}/{cat}/disable", {"version_no": 2}).status_code
            == 200
        )
    else:
        with session_factory().begin() as db:
            if directory == "brand":
                brand_id = client.get(ROOT + "/brands", headers=maker).json()[0]["id"]
                db.get_one(ProductBrand, UUID(brand_id)).is_active = False
            else:
                db.get_one(ProductUnit, UUID(defaults["unit_id"])).is_active = False
    assert (
        batch_action(client, maker, id, "commit", 3).json()["code"]
        == "PRODUCT_IMPORT_SNAPSHOT_STALE"
    )


def test_reject_reason_and_audit(catalog):
    client, maker, reviewer = catalog
    model(catalog, "serial")
    raw = product_file()
    id = upload(client, maker, raw)["batch_id"]
    assert batch_action(client, maker, id, "submit", 1).status_code == 200
    assert batch_action(client, reviewer, id, "reject", 2).status_code == 422
    assert batch_action(client, reviewer, id, "reject", 2, reason="需复核").status_code == 200
    assert batch_action(client, maker, id, "commit", 3).status_code == 409
    with session_factory()() as db:
        entries = list(db.scalars(select(AuditLog).where(AuditLog.entity_id == UUID(id))))
    product_events = {
        entry.action: entry for entry in entries if entry.action.startswith("product.import.")
    }
    assert set(product_events) == {
        "product.import.previewed",
        "product.import.submitted",
        "product.import.rejected",
    }
    for entry in product_events.values():
        assert entry.details["file_sha256"] == hashlib.sha256(raw).hexdigest()
        assert set(entry.details) <= {
            "file_sha256",
            "preview_hash",
            "spu_count",
            "sku_count",
            "maker_id",
            "approver_id",
            "import_result",
        }


def test_existing_product_conflicts_and_snapshot_immutability(catalog):
    client, maker, reviewer = catalog
    base, _, _ = model(catalog, "serial")
    raw = product_file(existing=True)
    result = upload(client, maker, raw.replace("合成型号".encode(), "改名".encode()))
    assert result["conflicts"] == 1
    assert result["errors"][0]["field"] == "spu_no"
    create(client, maker, ROOT + "/skus", {**base, "sku_no": "IMPORT-0"})
    assert upload(client, maker, raw)["errors"][0]["field"] == "sku_no"
    fresh = raw.replace(b"IMPORT-0", b"IMPORT-NEW")
    id = upload(client, maker, fresh)["batch_id"]
    with pytest.raises(DBAPIError), session_factory().begin() as db:
        db.execute(
            text("UPDATE product_import_batch SET normalized_payload = '{}' WHERE id = :id"),
            {"id": id},
        )
    assert batch_action(client, maker, id, "submit", 1).status_code == 200
    assert batch_action(client, reviewer, id, "approve", 2).status_code == 200
    assert batch_action(client, maker, id, "commit", 3).status_code == 200
    with session_factory()() as db:
        events = set(db.scalars(select(AuditLog.action).where(AuditLog.entity_id == UUID(id))))
    assert {
        "product.import.submitted",
        "product.import.approved",
        "product.import.committed",
    } <= events
