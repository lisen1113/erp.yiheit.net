"""Shared lifecycle rules. No autonomous deletion of confirmed assets."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from yihe_api.errors import ApiError, ErrorCode
from yihe_api.files.models import BrandAsset, FileAsset
from yihe_api.files.schemas import State


def expire_pending(db: Session, company_id: UUID, id: UUID, now: datetime) -> bool:
    row = db.scalar(
        select(FileAsset)
        .where(
            FileAsset.company_id == company_id,
            FileAsset.id == id,
        )
        .with_for_update()
    )
    if (
        row is None
        or row.status != State.UPLOADING
        or row.expires_at is None
        or row.expires_at > now
    ):
        return False
    row.status = State.EXPIRED
    row.version_no += 1
    return True


def require_cleanup_candidate(
    db: Session,
    row: FileAsset,
    *,
    has_external_reference: bool,
) -> None:
    referenced = db.scalar(
        select(BrandAsset.id)
        .where(
            BrandAsset.company_id == row.company_id,
            BrandAsset.file_asset_id == row.id,
        )
        .limit(1)
    )
    if (
        row.status != State.EXPIRED
        or row.confirmed_at is not None
        or row.sha256 is not None
        or row.retention_class != "STANDARD"
        or has_external_reference
        or referenced is not None
    ):
        raise ApiError(ErrorCode.FORBIDDEN)
