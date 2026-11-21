"""File metadata and append-preserving brand versions."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yihe_api.db.models import Base, CompanyScopeMixin, IdentityMixin


class FileAsset(IdentityMixin, CompanyScopeMixin, Base):
    __tablename__ = "file_asset"
    __table_args__ = (
        UniqueConstraint("company_id", "id"),
        UniqueConstraint("storage_provider", "bucket", "object_key"),
        ForeignKeyConstraint(
            ["company_id", "uploaded_by"],
            ["app_user.company_id", "app_user.id"],
            name="fk_file_asset_company_uploader",
            ondelete="RESTRICT",
        ),
        CheckConstraint("size_bytes > 0 AND size_bytes <= 524288000", name="bounded_size"),
        CheckConstraint(
            "status IN ('UPLOADING','AVAILABLE','QUARANTINED','REJECTED','EXPIRED',"
            "'INVALIDATED','DELETED_FROM_STORAGE')",
            name="file_status",
        ),
        CheckConstraint(
            "status <> 'AVAILABLE' OR (storage_version IS NOT NULL AND sha256 IS NOT NULL"
            " AND confirmed_at IS NOT NULL)",
            name="available_integrity",
        ),
        CheckConstraint("sha256 IS NULL OR sha256 ~ '^[a-f0-9]{64}$'", name="digest_format"),
        CheckConstraint("expected_sha256 ~ '^[a-f0-9]{64}$'", name="expected_digest_format"),
    )
    asset_type: Mapped[str] = mapped_column(String(24))
    original_filename: Mapped[str] = mapped_column(String(180))
    display_name: Mapped[str] = mapped_column(String(180))
    object_key: Mapped[str] = mapped_column(String(255))
    bucket: Mapped[str] = mapped_column(String(128))
    storage_provider: Mapped[str] = mapped_column(String(12))
    storage_version: Mapped[str | None] = mapped_column(String(1024))
    content_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    expected_sha256: Mapped[str] = mapped_column(String(64))
    sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    sensitivity_level: Mapped[str] = mapped_column(String(16))
    uploaded_by: Mapped[UUID] = mapped_column(Uuid)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_class: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(16))
    details: Mapped[dict[str, str]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    version_no: Mapped[int] = mapped_column(Integer, server_default=text("1"))


Index("ix_file_asset_company_sha256", FileAsset.company_id, FileAsset.sha256)
Index("ix_file_asset_status_expires", FileAsset.status, FileAsset.expires_at)


class BrandAsset(IdentityMixin, CompanyScopeMixin, Base):
    __tablename__ = "brand_asset"
    __table_args__ = (
        UniqueConstraint("company_id", "asset_code", "version"),
        ForeignKeyConstraint(
            ["company_id", "file_asset_id"],
            ["file_asset.company_id", "file_asset.id"],
            name="fk_brand_asset_company_file",
            ondelete="RESTRICT",
        ),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="valid_window"),
        CheckConstraint(
            "NOT is_active OR (valid_from IS NOT NULL AND valid_to IS NULL)",
            name="active_integrity",
        ),
        CheckConstraint(
            "asset_type IN ('LOGO_HORIZONTAL','LOGO_COMPACT','LOGO_LIGHT_BACKGROUND',"
            "'LOGO_DARK_BACKGROUND','DOCUMENT_BACKGROUND','DOCUMENT_FOOTER','EMAIL_HEADER')",
            name="brand_type",
        ),
        Index(
            "uq_brand_asset_active_purpose",
            "company_id",
            "asset_type",
            "language",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )
    file_asset_id: Mapped[UUID] = mapped_column(Uuid)
    asset_code: Mapped[str] = mapped_column(String(50))
    asset_type: Mapped[str] = mapped_column(String(24))
    language: Mapped[str] = mapped_column(String(8))
    version: Mapped[int] = mapped_column(Integer)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=false())
    approved_by: Mapped[UUID | None] = mapped_column(ForeignKey("app_user.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    version_no: Mapped[int] = mapped_column(Integer, server_default=text("1"))
