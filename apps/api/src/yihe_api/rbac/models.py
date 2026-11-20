from datetime import datetime
from uuid import UUID

from sqlalchemy import (
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
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from yihe_api.db.models import AuditMixin, Base, CompanyScopeMixin, IdentityMixin, SoftDeleteMixin


class Role(IdentityMixin, CompanyScopeMixin, AuditMixin, SoftDeleteMixin, Base):
    __tablename__ = "role"
    __table_args__ = (
        UniqueConstraint("company_id", "code"),
        UniqueConstraint("company_id", "id"),
        CheckConstraint("code ~ '^[A-Z][A-Z0-9_]*$'", name="normalized_code"),
        CheckConstraint("data_scope IN ('self','department','company')", name="data_scope"),
    )
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(255))
    data_scope: Mapped[str] = mapped_column(String(20), server_default=text("'self'"))
    requires_mfa: Mapped[bool] = mapped_column(Boolean, server_default=false())
    is_system: Mapped[bool] = mapped_column(Boolean, server_default=false())
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())


class Permission(IdentityMixin, Base):
    __tablename__ = "permission"
    __table_args__ = (
        CheckConstraint("code = lower(code)", name="normalized_code"),
        CheckConstraint("sensitivity_level IN ('standard','high')", name="sensitivity"),
    )
    code: Mapped[str] = mapped_column(String(100), unique=True)
    module: Mapped[str] = mapped_column(String(50), index=True)
    action: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(String(255))
    sensitivity_level: Mapped[str] = mapped_column(String(16), server_default=text("'standard'"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())


class UserRole(IdentityMixin, CompanyScopeMixin, Base):
    __tablename__ = "user_role"
    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "user_id"],
            ["app_user.company_id", "app_user.id"],
            name="fk_user_role_company_user",
        ),
        ForeignKeyConstraint(
            ["company_id", "role_id"],
            ["role.company_id", "role.id"],
            name="fk_user_role_company_role",
        ),
        CheckConstraint("valid_until IS NULL OR valid_until > valid_from", name="valid_window"),
        Index(
            "uq_user_role_open",
            "company_id",
            "user_id",
            "role_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )
    user_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    role_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("app_user.id"))
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("app_user.id"))
    version_no: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class RolePermission(Base):
    __tablename__ = "role_permission"
    role_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("role.id"), primary_key=True)
    permission_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("permission.id"), primary_key=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    granted_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("app_user.id"))
