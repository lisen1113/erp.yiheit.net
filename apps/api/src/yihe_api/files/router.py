"""Version-pinned uploads and authorized file delivery."""

import hashlib
import hmac
import os
from datetime import timedelta
from typing import Annotated
from urllib.parse import quote
from uuid import UUID, uuid4

import jwt
from fastapi import APIRouter, Header, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from yihe_api.audit.service import Actor, Category, audit_change
from yihe_api.auth.router import DB, now_utc
from yihe_api.auth.security import auth_config
from yihe_api.db.models import Company
from yihe_api.errors import ApiError, ErrorCode
from yihe_api.files.models import BrandAsset, FileAsset
from yihe_api.files.schemas import (
    BrandCreate,
    BrandView,
    FileView,
    Purpose,
    Receipt,
    State,
    Ticket,
    Upload,
    VersionInput,
)
from yihe_api.files.storage import BadObject, Storage, StorageError, storage
from yihe_api.files.validation import Kind, Verdict, filename_extension, limit, scan, verify_format
from yihe_api.idempotency.service import Operation, Success, execute
from yihe_api.rbac.policy import Principal, Scope
from yihe_api.rbac.router import Current

router = APIRouter(tags=["files"])
Idempotency = Annotated[str | None, Header(alias="Idempotency-Key")]
SAFE = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
ISSUER = "yihe-erp-file"


def provider(row: FileAsset | None = None) -> Storage:
    try:
        store = storage()
        if row and (row.bucket, row.storage_provider) != (store.bucket, store.provider):
            raise StorageError("Provider mismatch")
        return store
    except StorageError:
        raise ApiError(ErrorCode.FILE_STORAGE_UNAVAILABLE) from None


def identity(request: Request) -> Actor:
    value = getattr(request.state, "audit_actor", None)
    if not isinstance(value, Actor):
        raise ApiError(ErrorCode.UNAUTHENTICATED)
    return value


def brand_permission(principal: Principal, *, write: bool) -> None:
    if not write and "brand_asset.view" in principal.grants:
        principal.require("brand_asset.view", company=True)
    elif "brand_asset.manage" in principal.grants or "brand_asset.manage" in principal.blocked:
        principal.require("brand_asset.manage", company=True)
    else:
        principal.require("template.manage", company=True)


def load_file(
    db: Session, principal: Principal, id: UUID, action: str, *, lock: bool = False
) -> FileAsset:
    stmt = select(FileAsset).where(
        FileAsset.id == id,
        FileAsset.company_id == principal.user.company_id,
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(stmt)
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND)
    if row.asset_type == Kind.BRAND:
        brand_permission(principal, write=action in {"upload", "invalidate"})
    elif principal.require(f"file_asset.{action}") == Scope.SELF and (
        row.uploaded_by != principal.user.id
    ):
        raise ApiError(ErrorCode.NOT_FOUND)
    if row.sensitivity_level == "SENSITIVE":
        principal.require("file_asset.view_sensitive", company=True)
    return row


def log_file(db: Session, request: Request, row: FileAsset, action: str) -> None:
    actor = identity(request)
    audit_change(
        db,
        company_id=actor.company_id,
        actor=actor,
        action=action,
        category=Category.MASTER_DATA,
        entity_type="file_asset",
        entity_id=row.id,
        request=request,
        metadata={
            "file_asset_type": row.asset_type,
            "file_content_type": row.content_type,
            "file_size_bytes": row.size_bytes,
            "file_sha256": row.sha256,
            "file_status": row.status,
        },
    )


def pending(row: FileAsset) -> int:
    if row.status != State.UPLOADING or row.expires_at is None:
        raise ApiError(ErrorCode.FILE_NOT_AVAILABLE)
    seconds = int((row.expires_at - now_utc()).total_seconds())
    if seconds <= 0:
        raise ApiError(ErrorCode.FILE_NOT_AVAILABLE)
    return seconds


def available(row: FileAsset) -> str:
    if row.status != State.AVAILABLE or not row.storage_version:
        raise ApiError(ErrorCode.FILE_NOT_AVAILABLE)
    if row.expires_at is not None and row.expires_at <= now_utc():
        raise ApiError(ErrorCode.FILE_NOT_AVAILABLE)
    return row.storage_version


def version_matches(row: FileAsset | BrandAsset, body: VersionInput) -> None:
    if row.version_no != body.version_no:
        raise ApiError(ErrorCode.VERSION_CONFLICT)


@router.post("/files", status_code=201, response_model=Receipt)
def request_upload(
    body: Upload,
    request: Request,
    db: DB,
    principal: Current,
    idempotency_key: Idempotency = None,
) -> JSONResponse:
    if body.asset_type == Kind.BRAND:
        brand_permission(principal, write=True)
        if body.sensitivity != "STANDARD":
            raise ApiError(ErrorCode.FILE_INVALID)
    else:
        principal.require("file_asset.upload")
    if body.sensitivity == "SENSITIVE":
        principal.require("file_asset.view_sensitive", company=True)
    try:
        filename_extension(body.filename, body.asset_type, body.media_type)
    except ValueError:
        raise ApiError(ErrorCode.FILE_INVALID) from None
    if body.size_bytes > limit(body.asset_type):
        raise ApiError(ErrorCode.FILE_INVALID)
    actor = identity(request)

    def create(tx: Session) -> Success:
        store = provider()
        now = now_utc()
        key = f"company/{actor.company_id}/{now:%Y/%m}/{body.asset_type.lower()}/{uuid4()}"
        row = FileAsset(
            company_id=actor.company_id,
            asset_type=body.asset_type,
            original_filename=body.filename,
            display_name=body.filename,
            object_key=key,
            bucket=store.bucket,
            storage_provider=store.provider,
            content_type=body.media_type,
            size_bytes=body.size_bytes,
            expected_sha256=body.sha256,
            status=State.UPLOADING,
            sensitivity_level=body.sensitivity,
            uploaded_by=actor.user_id,
            expires_at=now + timedelta(hours=1),
            retention_class="STANDARD",
            source="API",
            details={},
        )
        tx.add(row)
        tx.flush()
        log_file(tx, request, row, "file.upload.requested")
        return Success("file_asset", row.id, status_code=201, location=f"/api/v1/files/{row.id}")

    return execute(
        db,
        request,
        actor=actor,
        operation=Operation(
            "file.upload.request",
            "file_asset",
            None,
            "/api/v1/files",
        ),
        projection=body.model_dump(mode="json"),
        work=create,
    )


@router.get("/files/{id}")
def file_view(id: UUID, db: DB, principal: Current) -> FileView:
    return FileView.model_validate(load_file(db, principal, id, "view"), from_attributes=True)


@router.post("/files/{id}/upload-ticket")
def upload_ticket(id: UUID, response: Response, db: DB, principal: Current) -> Ticket:
    row = load_file(db, principal, id, "upload")
    if row.uploaded_by != principal.user.id:
        raise ApiError(ErrorCode.FORBIDDEN)
    seconds = min(180, pending(row))
    try:
        url = provider(row).put_url(row.object_key, row.content_type, row.size_bytes, seconds)
    except StorageError:
        raise ApiError(ErrorCode.FILE_STORAGE_UNAVAILABLE) from None
    response.headers.update(SAFE)
    return Ticket(url=url, expires_in=seconds, required_headers={"Content-Type": row.content_type})


def inspect(row: FileAsset) -> State:
    store = provider(row)
    try:
        version = store.stat(row.object_key)
        maximum = limit(Kind(row.asset_type))
        if (
            version.size != row.size_bytes
            or version.size > maximum
            or (version.mime != row.content_type)
        ):
            return State.REJECTED
        content = store.read(row.object_key, version.id, maximum)
    except BadObject:
        return State.REJECTED
    except StorageError:
        raise ApiError(ErrorCode.FILE_STORAGE_UNAVAILABLE) from None
    digest = hashlib.sha256(content).hexdigest()
    if len(content) != row.size_bytes or digest != row.expected_sha256:
        return State.REJECTED
    if scan(content) != Verdict.CLEAN:
        return State.QUARANTINED
    try:
        verify_format(
            content,
            filename_extension(
                row.original_filename,
                Kind(row.asset_type),
                row.content_type,
            ),
        )
    except ValueError:
        return State.REJECTED
    row.storage_version = version.id
    row.sha256 = digest
    row.confirmed_at = now_utc()
    row.expires_at = None
    return State.AVAILABLE


@router.post("/files/{id}/confirm", response_model=Receipt)
def confirm(
    id: UUID,
    body: VersionInput,
    request: Request,
    db: DB,
    principal: Current,
    idempotency_key: Idempotency = None,
) -> JSONResponse:
    original = load_file(db, principal, id, "upload")
    if original.uploaded_by != principal.user.id:
        raise ApiError(ErrorCode.FORBIDDEN)

    def finish(tx: Session) -> Success:
        row = load_file(tx, principal, id, "upload", lock=True)
        version_matches(row, body)
        pending(row)
        row.status = inspect(row)
        row.version_no += 1
        action = {
            State.AVAILABLE: "file.upload.confirmed",
            State.REJECTED: "file.upload.rejected",
            State.QUARANTINED: "file.quarantined",
        }[State(row.status)]
        log_file(tx, request, row, action)
        return Success("file_asset", id)

    return execute(
        db,
        request,
        actor=identity(request),
        operation=Operation(
            "file.upload.confirm",
            "file_asset",
            id,
            f"/api/v1/files/{id}/confirm",
        ),
        projection=body.model_dump(),
        work=finish,
    )


@router.post("/files/{id}/invalidate", response_model=Receipt)
def invalidate(
    id: UUID,
    body: VersionInput,
    request: Request,
    db: DB,
    principal: Current,
    idempotency_key: Idempotency = None,
) -> JSONResponse:
    load_file(db, principal, id, "invalidate")

    def update(tx: Session) -> Success:
        row = load_file(tx, principal, id, "invalidate", lock=True)
        version_matches(row, body)
        available(row)
        active = tx.scalar(
            select(BrandAsset.id)
            .where(
                BrandAsset.company_id == row.company_id,
                BrandAsset.file_asset_id == id,
                BrandAsset.is_active.is_(True),
            )
            .limit(1)
        )
        if active is not None:
            raise ApiError(ErrorCode.CONFLICT)
        row.status = State.INVALIDATED
        row.version_no += 1
        log_file(tx, request, row, "file.invalidated")
        return Success("file_asset", id)

    return execute(
        db,
        request,
        actor=identity(request),
        operation=Operation(
            "file.invalidate",
            "file_asset",
            id,
            f"/api/v1/files/{id}/invalidate",
        ),
        projection=body.model_dump(),
        work=update,
    )


def delivery_key() -> bytes:
    return hmac.digest(auth_config().signing_key.encode(), b"yihe-erp:file-delivery:v1", "sha256")


@router.post("/files/{id}/download-ticket")
def download_ticket(id: UUID, response: Response, db: DB, principal: Current) -> Ticket:
    row = load_file(db, principal, id, "download")
    seconds = 60 if row.sensitivity_level == "SENSITIVE" else 300
    token = jwt.encode(
        {
            "iss": ISSUER,
            "sub": str(principal.user.id),
            "file": str(id),
            "version": available(row),
            "exp": now_utc() + timedelta(seconds=seconds),
        },
        delivery_key(),
        algorithm="HS256",
    )
    response.headers.update(SAFE)
    return Ticket(url=f"/api/v1/files/{id}/content?ticket={token}", expires_in=seconds)


@router.get("/files/{id}/content", responses={200: {"content": {"application/octet-stream": {}}}})
def download(id: UUID, ticket: str, request: Request, db: DB, principal: Current) -> Response:
    row = load_file(db, principal, id, "download")
    version = available(row)
    try:
        if len(ticket) > 4096:
            raise ValueError
        claims = jwt.decode(
            ticket,
            delivery_key(),
            algorithms=["HS256"],
            issuer=ISSUER,
            options={"require": ["sub", "exp", "file", "version"]},
        )
        if (claims["sub"], claims["file"], claims["version"]) != (
            str(principal.user.id),
            str(id),
            version,
        ):
            raise ValueError
    except (jwt.InvalidTokenError, ValueError):
        raise ApiError(ErrorCode.FORBIDDEN) from None
    data = read_available(row, version)
    log_file(db, request, row, "file.downloaded")
    headers = {
        **SAFE,
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(row.display_name, safe=""),
    }
    return Response(data, media_type="application/octet-stream", headers=headers)


def read_available(row: FileAsset, version: str) -> bytes:
    try:
        data = provider(row).read(row.object_key, version, row.size_bytes)
    except StorageError:
        raise ApiError(ErrorCode.FILE_STORAGE_UNAVAILABLE) from None
    if len(data) != row.size_bytes or hashlib.sha256(data).hexdigest() != row.sha256:
        raise ApiError(ErrorCode.FILE_NOT_AVAILABLE)
    return data


@router.get("/brand-assets")
def list_brand(db: DB, principal: Current) -> list[BrandView]:
    brand_permission(principal, write=False)
    rows = db.scalars(
        select(BrandAsset)
        .where(
            BrandAsset.company_id == principal.user.company_id,
        )
        .order_by(BrandAsset.created_at.desc())
        .limit(200)
    )
    return [BrandView.model_validate(row, from_attributes=True) for row in rows]


def log_brand(tx: Session, request: Request, row: BrandAsset, action: str) -> None:
    actor = identity(request)
    audit_change(
        tx,
        company_id=actor.company_id,
        actor=actor,
        action=action,
        category=Category.MASTER_DATA,
        entity_type="brand_asset",
        entity_id=row.id,
        request=request,
        metadata={"related_id": row.file_asset_id, "is_active": row.is_active},
    )


@router.post("/brand-assets", status_code=201, response_model=Receipt)
def create_brand(
    body: BrandCreate,
    request: Request,
    db: DB,
    principal: Current,
    idempotency_key: Idempotency = None,
) -> JSONResponse:
    brand_permission(principal, write=True)
    actor = identity(request)

    def create(tx: Session) -> Success:
        tx.get(Company, actor.company_id, with_for_update=True)
        file = load_file(tx, principal, body.file_asset_id, "upload", lock=True)
        available(file)
        if file.asset_type != Kind.BRAND or file.sensitivity_level != "STANDARD":
            raise ApiError(ErrorCode.FILE_INVALID)
        last = (
            tx.scalar(
                select(func.max(BrandAsset.version)).where(
                    BrandAsset.company_id == actor.company_id,
                    BrandAsset.asset_code == body.asset_code,
                )
            )
            or 0
        )
        row = BrandAsset(
            company_id=actor.company_id,
            file_asset_id=file.id,
            asset_code=body.asset_code,
            asset_type=body.asset_type,
            language=body.language,
            version=last + 1,
        )
        tx.add(row)
        tx.flush()
        log_brand(tx, request, row, "brand_asset.created")
        return Success("brand_asset", row.id, status_code=201)

    return execute(
        db,
        request,
        actor=actor,
        operation=Operation(
            "brand_asset.create",
            "brand_asset",
            None,
            "/api/v1/brand-assets",
        ),
        projection=body.model_dump(mode="json"),
        work=create,
    )


def change_brand(
    id: UUID,
    body: VersionInput,
    request: Request,
    db: Session,
    principal: Principal,
    active: bool,
) -> JSONResponse:
    brand_permission(principal, write=True)
    actor = identity(request)
    action = "activate" if active else "deactivate"

    def change(tx: Session) -> Success:
        tx.get(Company, actor.company_id, with_for_update=True)
        row = tx.scalar(
            select(BrandAsset)
            .where(
                BrandAsset.id == id,
                BrandAsset.company_id == actor.company_id,
            )
            .with_for_update()
        )
        if row is None:
            raise ApiError(ErrorCode.NOT_FOUND)
        version_matches(row, body)
        if row.is_active == active or (active and row.valid_from is not None):
            raise ApiError(ErrorCode.CONFLICT)
        if active:
            available(load_file(tx, principal, row.file_asset_id, "upload", lock=True))
            duplicate = tx.scalar(
                select(BrandAsset.id).where(
                    BrandAsset.company_id == actor.company_id,
                    BrandAsset.asset_type == row.asset_type,
                    BrandAsset.language == row.language,
                    BrandAsset.is_active.is_(True),
                )
            )
            if duplicate is not None:
                raise ApiError(ErrorCode.CONFLICT)
            row.valid_from = now_utc()
            row.approved_by = actor.user_id
            row.approved_at = row.valid_from
        else:
            row.valid_to = now_utc()
        row.is_active = active
        row.version_no += 1
        log_brand(tx, request, row, f"brand_asset.{action}d")
        return Success("brand_asset", id)

    return execute(
        db,
        request,
        actor=actor,
        operation=Operation(
            f"brand_asset.{action}",
            "brand_asset",
            id,
            f"/api/v1/brand-assets/{id}/{action}",
        ),
        projection=body.model_dump(),
        work=change,
    )


@router.post("/brand-assets/{id}/activate", response_model=Receipt)
def activate(
    id: UUID,
    body: VersionInput,
    request: Request,
    db: DB,
    principal: Current,
    idempotency_key: Idempotency = None,
) -> JSONResponse:
    return change_brand(id, body, request, db, principal, True)


@router.post("/brand-assets/{id}/deactivate", response_model=Receipt)
def deactivate(
    id: UUID,
    body: VersionInput,
    request: Request,
    db: DB,
    principal: Current,
    idempotency_key: Idempotency = None,
) -> JSONResponse:
    return change_brand(id, body, request, db, principal, False)


@router.get("/brand-assets/public/logo", responses={200: {"content": {"image/png": {}}}})
def public_logo(db: DB) -> Response:
    try:
        company = UUID(os.environ.get("ERP_PUBLIC_BRAND_COMPANY_ID", ""))
    except ValueError:
        raise ApiError(ErrorCode.NOT_FOUND) from None
    row = db.scalar(
        select(FileAsset)
        .join(
            BrandAsset,
            (
                (BrandAsset.file_asset_id == FileAsset.id)
                & (BrandAsset.company_id == FileAsset.company_id)
            ),
        )
        .where(
            BrandAsset.company_id == company,
            BrandAsset.asset_type == Purpose.LOGO_HORIZONTAL,
            BrandAsset.language == "zh-CN",
            BrandAsset.is_active.is_(True),
            FileAsset.asset_type == Kind.BRAND,
            FileAsset.sensitivity_level == "STANDARD",
            FileAsset.status == State.AVAILABLE,
        )
    )
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND)
    return Response(read_available(row, available(row)), media_type=row.content_type, headers=SAFE)
