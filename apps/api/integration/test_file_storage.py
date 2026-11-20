"""Exercise real versioned object transport and PostgreSQL authorization."""

import hashlib
import os
from io import BytesIO
from uuid import UUID, uuid4

import boto3
import httpx
import pytest
from PIL import Image
from sqlalchemy import select
from test_authorization import BASE, create_company, create_user, grant
from test_authorization import auth_env as auth_env
from test_authorization import identity as identity_fixture  # noqa: F401
from yihe_api.audit.models import AuditLog
from yihe_api.auth.models import AuthSession, User
from yihe_api.auth.router import session_factory
from yihe_api.errors import ApiError
from yihe_api.files.lifecycle import require_cleanup_candidate
from yihe_api.files.models import BrandAsset, FileAsset
from yihe_api.files.storage import storage
from yihe_api.files.validation import Verdict

KEY = "Idempotency-Key"


def admin_client(identity, monkeypatch):
    client, company, user, session, headers = identity
    monkeypatch.setenv("ERP_PUBLIC_BRAND_COMPANY_ID", str(company))
    storage.cache_clear()
    with session_factory().begin() as db:
        account = db.get(User, user)
        account.mfa_enabled = True
        db.get(AuthSession, session).mfa_verified = True
        grant(db, account, "brand_asset.manage", "company")
    return client, company, headers


def post(client, path, headers, value, key=None):
    return client.post(BASE + path, json=value, headers={**headers, KEY: key or str(uuid4())})


def manifest(data, name="test.png"):
    return {
        "asset_type": "BRAND",
        "filename": name,
        "media_type": "image/png",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def upload(client, headers, data, name="test.png"):
    response = post(client, "/files", headers, manifest(data, name))
    assert response.status_code == 201
    id = response.json()["resource_id"]
    signed = client.post(f"{BASE}/files/{id}/upload-ticket", headers=headers)
    assert signed.status_code == 200
    ticket = signed.json()
    assert ticket["expires_in"] <= 180
    sent = httpx.put(
        ticket["url"], content=data, headers=ticket["required_headers"], trust_env=False
    )
    assert sent.status_code == 200
    return id, ticket


def png(color):
    stream = BytesIO()
    Image.new("RGB", (12, 12), color).save(stream, "PNG")
    return stream.getvalue()


def test_real_minio_brand_upload_confirm_pinned_download_and_lifecycle(
    identity_fixture,  # noqa: F811
    monkeypatch,
):  # noqa: F811
    client, company, headers = admin_client(identity_fixture, monkeypatch)
    scans = []

    def clean(data):
        scans.append(hashlib.sha256(data).hexdigest())
        return Verdict.CLEAN

    monkeypatch.setattr("yihe_api.files.router.scan", clean)
    data = png("white")
    id, upload_ticket = upload(client, headers, data)
    assert client.post(f"{BASE}/files/{id}/download-ticket", headers=headers).status_code == 409
    reference = {"file_asset_id": id, "asset_code": "TEST_LOGO", "asset_type": "LOGO_HORIZONTAL"}
    assert post(client, "/brand-assets", headers, reference).status_code == 409
    key = str(uuid4())
    confirmed = post(client, f"/files/{id}/confirm", headers, {"version_no": 1}, key)
    assert confirmed.status_code == 200
    replay = post(client, f"/files/{id}/confirm", headers, {"version_no": 1}, key)
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert len(scans) == 1
    info = client.get(f"{BASE}/files/{id}", headers=headers).json()
    assert info["status"] == "AVAILABLE" and info["sha256"] == hashlib.sha256(data).hexdigest()
    assert "object_key" not in info
    # An old PUT may create another version, but it cannot supersede the confirmed snapshot.
    changed = bytes((data[0] ^ 1,)) + data[1:]
    assert (
        httpx.put(
            upload_ticket["url"],
            content=changed,
            headers=upload_ticket["required_headers"],
            trust_env=False,
        ).status_code
        == 200
    )
    delivery = client.post(f"{BASE}/files/{id}/download-ticket", headers=headers).json()
    assert delivery["expires_in"] == 300
    downloaded = client.get(delivery["url"], headers=headers)
    assert downloaded.status_code == 200 and downloaded.content == data
    assert downloaded.headers["X-Content-Type-Options"] == "nosniff"
    for version in (1, 2):
        result = post(client, "/brand-assets", headers, reference)
        assert result.status_code == 201
        if version == 1:
            first_brand = result.json()["resource_id"]
        else:
            second_brand = result.json()["resource_id"]
    active_key = str(uuid4())
    assert (
        post(
            client, f"/brand-assets/{first_brand}/activate", headers, {"version_no": 1}, active_key
        ).status_code
        == 200
    )
    assert (
        post(
            client, f"/brand-assets/{first_brand}/activate", headers, {"version_no": 1}, active_key
        ).headers["Idempotency-Replayed"]
        == "true"
    )
    assert (
        post(
            client, f"/brand-assets/{second_brand}/activate", headers, {"version_no": 1}
        ).status_code
        == 409
    )
    assert client.get(f"{BASE}/brand-assets/public/logo").content == data
    assert post(client, f"/files/{id}/invalidate", headers, {"version_no": 2}).status_code == 409
    assert (
        post(
            client, f"/brand-assets/{first_brand}/deactivate", headers, {"version_no": 2}
        ).status_code
        == 200
    )
    assert client.get(f"{BASE}/brand-assets/public/logo").status_code == 404
    assert post(client, f"/files/{id}/invalidate", headers, {"version_no": 2}).status_code == 200
    assert client.get(delivery["url"], headers=headers).status_code == 409
    with session_factory()() as db:
        row = db.get(FileAsset, UUID(id))
        assert row.status == "INVALIDATED"
        with pytest.raises(ApiError):
            require_cleanup_candidate(db, row, has_external_reference=True)
        grants = list(db.scalars(select(AuditLog).where(AuditLog.company_id == company)))
        actions = {event.action for event in grants}
        assert {
            "file.downloaded",
            "brand_asset.activated",
            "brand_asset.deactivated",
            "file.invalidated",
            "file.upload.confirmed",
        } <= actions
        assert all("X-Amz-Signature" not in str(event.details) for event in grants)
        signed = storage().get_url(row.object_key, row.storage_version, "attachment", 30)
        assert httpx.get(signed, trust_env=False).content == data
        assert [
            brand.version
            for brand in db.scalars(
                select(BrandAsset)
                .where(BrandAsset.company_id == company)
                .order_by(BrandAsset.version)
            )
        ] == [1, 2]
    print("MinIO: confirmed PNG read by pinned version after stale upload URL wrote newer bytes")


def test_rejected_quarantine_and_tenant_permissions(identity_fixture, monkeypatch):  # noqa: F811
    client, company, headers = admin_client(identity_fixture, monkeypatch)
    monkeypatch.setattr("yihe_api.files.router.scan", lambda _: Verdict.CLEAN)
    data = png("black")
    for candidate, filename in ((b"MZ-not-a-png", "test.png"), (data, "test.jpg")):
        if filename.endswith(".jpg"):
            body = {**manifest(candidate, filename), "media_type": "image/jpeg"}
            req = post(client, "/files", headers, body)
            assert req.status_code == 201
            bad_id = req.json()["resource_id"]
            url = client.post(f"{BASE}/files/{bad_id}/upload-ticket", headers=headers).json()
            assert (
                httpx.put(
                    url["url"], content=candidate, headers=url["required_headers"], trust_env=False
                ).status_code
                == 200
            )
        else:
            bad_id, _ = upload(client, headers, candidate)
        assert (
            post(client, f"/files/{bad_id}/confirm", headers, {"version_no": 1}).status_code == 200
        )
        assert client.get(f"{BASE}/files/{bad_id}", headers=headers).json()["status"] == "REJECTED"
    monkeypatch.setattr("yihe_api.files.router.scan", lambda _: Verdict.UNAVAILABLE)
    blocked_id, _ = upload(client, headers, data)
    assert (
        post(client, f"/files/{blocked_id}/confirm", headers, {"version_no": 1}).status_code == 200
    )
    assert (
        client.get(f"{BASE}/files/{blocked_id}", headers=headers).json()["status"] == "QUARANTINED"
    )
    too_large = {**manifest(data), "size_bytes": 10 * 1024**2 + 1}
    assert post(client, "/files", headers, too_large).status_code == 422
    assert (
        post(client, "/files", headers, {**manifest(data), "object_key": "arbitrary"}).status_code
        == 422
    )
    other_id, _ = upload(client, headers, data)
    with session_factory().begin() as db:
        _, _, local = create_user(db, company)
        other, _, foreign = create_user(db, create_company(db))
        grant(db, other, "brand_asset.view", "company")
        keys = list(
            db.scalars(
                select(FileAsset.object_key).where(
                    FileAsset.id.in_([UUID(blocked_id), UUID(other_id)]),
                )
            )
        )
        assert len(set(keys)) == 2
    assert client.get(f"{BASE}/files/{blocked_id}", headers=foreign).status_code == 404
    assert (
        client.post(f"{BASE}/files/{blocked_id}/download-ticket", headers=local).status_code == 403
    )
    print("Executable and MIME mismatch rejected; unavailable scanner quarantined the upload")


def test_real_storage_versioned_delete_and_persistence_probe():
    store = storage()
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["ERP_STORAGE_ENDPOINT"],
        aws_access_key_id=os.environ["ERP_STORAGE_ACCESS_KEY"],
        aws_secret_access_key=os.environ["ERP_STORAGE_SECRET_KEY"],
        region_name="us-east-1",
    )
    assert store.read("ci/probe", store.stat("ci/probe").id, 4096) == b"minio-persistence"
    key = f"ci/{uuid4()}"
    client.put_object(
        Bucket=store.bucket, Key=key, Body=b"synthetic", ContentType="application/octet-stream"
    )
    version = store.stat(key)
    assert version.size == 9
    store.delete_version(key, version.id)
    from yihe_api.files.storage import StorageError

    with pytest.raises(StorageError):
        store.stat(key)


def test_eicar_quarantines_even_without_scanner(identity_fixture, monkeypatch):  # noqa: F811
    client, _, headers = admin_client(identity_fixture, monkeypatch)
    monkeypatch.delenv("ERP_CLAMD_HOST", raising=False)
    signature = (
        b"X5O!P%@AP[4" + bytes([92]) + b"PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    )
    from yihe_api.files.validation import scan

    assert scan(signature) == Verdict.INFECTED
    asset_id, _ = upload(client, headers, signature)
    result = post(client, f"/files/{asset_id}/confirm", headers, {"version_no": 1})
    assert result.status_code == 200
    info = client.get(f"{BASE}/files/{asset_id}", headers=headers).json()
    assert info["status"] == "QUARANTINED"
    assert (
        client.post(f"{BASE}/files/{asset_id}/download-ticket", headers=headers).status_code == 409
    )
