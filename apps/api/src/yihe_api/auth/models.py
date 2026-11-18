"""Authentication records and the minimal employee identity binding."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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

from yihe_api.db.models import AuditMixin, Base, CompanyScopeMixin, IdentityMixin


class Employee(IdentityMixin, CompanyScopeMixin, AuditMixin, Base):
    """Minimal employee identity; extend incrementally with Alembic."""

    __tablename__ = "employee"
    __table_args__ = (
        UniqueConstraint("company_id", "employee_no"),
        UniqueConstraint("company_id", "id"),
    )

    employee_no: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(254))
    mobile: Mapped[str | None] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())


class User(IdentityMixin, CompanyScopeMixin, AuditMixin, Base):
    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("company_id", "id"),
        ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["employee.company_id", "employee.id"],
            name="fk_app_user_company_employee",
            ondelete="RESTRICT",
        ),
    )

    employee_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'active'"))
    failed_login_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    failure_window_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_required: Mapped[bool] = mapped_column(Boolean, server_default=false())
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, server_default=false())
    mfa_secret_encrypted: Mapped[str | None] = mapped_column(String(255))
    mfa_last_step: Mapped[int | None] = mapped_column(Integer)
    mfa_setup_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(IdentityMixin, CompanyScopeMixin, Base):
    __tablename__ = "auth_session"
    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "user_id"],
            ["app_user.company_id", "app_user.id"],
            name="fk_auth_session_company_user",
            ondelete="RESTRICT",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshGrant(Base):
    __tablename__ = "auth_refresh_grant"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("auth_session.id", ondelete="CASCADE"), index=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginChallenge(Base):
    __tablename__ = "auth_login_challenge"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("app_user.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed: Mapped[bool] = mapped_column(Boolean, server_default=false())
