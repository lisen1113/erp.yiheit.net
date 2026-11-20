"""PostgreSQL idempotency state. A scope and its key are never recycled."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yihe_api.db.models import Base, CompanyScopeMixin, IdentityMixin


class IdempotencyRecord(IdentityMixin, CompanyScopeMixin, Base):
    __tablename__ = "idempotency_record"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "actor_user_id",
            "action",
            "object_scope",
            "idempotency_key",
            name="uq_idempotency_scope_key",
        ),
        ForeignKeyConstraint(
            ["company_id", "actor_user_id"],
            ["app_user.company_id", "app_user.id"],
            name="fk_idempotency_company_actor",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('IN_PROGRESS','COMPLETED','FAILED_RETRYABLE','FAILED_FINAL','EXPIRED')",
            name="status",
        ),
        CheckConstraint("expires_at > locked_at", name="expiry"),
        Index("ix_idempotency_status_expiry", "status", "expires_at"),
    )
    actor_user_id: Mapped[UUID] = mapped_column(Uuid)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_method: Mapped[str] = mapped_column(String(12))
    request_path: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(64))
    object_scope: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, str] | None] = mapped_column(JSONB(none_as_null=True))
    response_headers: Mapped[dict[str, str] | None] = mapped_column(JSONB(none_as_null=True))
    resource_type: Mapped[str | None] = mapped_column(String(50))
    resource_id: Mapped[UUID | None] = mapped_column(Uuid)
    trace_id: Mapped[str] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    version_no: Mapped[int] = mapped_column(Integer, server_default=text("1"))
