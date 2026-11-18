"""Public column conventions and the minimal company registry."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Uuid,
    false,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

AMOUNT = Numeric(18, 2, asdecimal=True)
QUANTITY = Numeric(18, 4, asdecimal=True)
UNIT_COST = Numeric(18, 6, asdecimal=True)
TAX_RATE = Numeric(7, 4, asdecimal=True)


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class IdentityMixin:
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4, server_default=func.gen_random_uuid()
    )


class CompanyScopeMixin:
    company_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("company.id", ondelete="RESTRICT"), nullable=False, index=True
    )


class AuditMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    created_by: Mapped[UUID] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[UUID] = mapped_column(Uuid)
    version_no: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class SoftDeleteMixin:
    is_deleted: Mapped[bool] = mapped_column(Boolean, server_default=false(), index=True)


class LegacyMixin:
    is_legacy: Mapped[bool] = mapped_column(Boolean, server_default=false(), index=True)
    legacy_system: Mapped[str | None] = mapped_column(String(64))
    legacy_id: Mapped[str | None] = mapped_column(String(128))
    migration_batch_id: Mapped[UUID | None] = mapped_column(Uuid, index=True)
    legacy_readonly: Mapped[bool] = mapped_column(Boolean, server_default=false())


class Company(IdentityMixin, AuditMixin, SoftDeleteMixin, Base):
    __tablename__ = "company"
    __table_args__ = (CheckConstraint("version_no >= 1", name="version_no_positive"),)

    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(200))

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        return {"version_id_col": cls.version_no}
