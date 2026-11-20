"""Append-only company audit and global security events."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yihe_api.audit.payload import AuditValue
from yihe_api.db.models import Base, IdentityMixin


class EventMixin(IdentityMixin):
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    result: Mapped[str] = mapped_column(String(12))
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, AuditValue]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(EventMixin, Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint("result IN ('SUCCESS','FAILURE','DENIED')", name="result"),
        CheckConstraint("source IN ('API','CLI','WORKER','SYSTEM','MIGRATION')", name="source"),
        CheckConstraint(
            "actor_user_id IS NOT NULL OR source IN ('SYSTEM','WORKER','MIGRATION')", name="actor"
        ),
        CheckConstraint("retention_class IN ('STANDARD','CRITICAL')", name="retention"),
    )
    company_id: Mapped[UUID] = mapped_column(ForeignKey("company.id"))
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("app_user.id"))
    actor_employee_id: Mapped[UUID | None] = mapped_column(ForeignKey("employee.id"))
    session_id: Mapped[UUID | None] = mapped_column(ForeignKey("auth_session.id"))
    event_category: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(50))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[UUID | None] = mapped_column(Uuid)
    entity_ref: Mapped[str | None] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(String(500))
    before_data: Mapped[dict[str, AuditValue] | None] = mapped_column(JSONB)
    after_data: Mapped[dict[str, AuditValue] | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(12))
    retention_class: Mapped[str] = mapped_column(String(12))


Index("ix_audit_log_company_time", AuditLog.company_id, AuditLog.occurred_at.desc(), AuditLog.id)
Index(
    "ix_audit_log_company_actor_time",
    AuditLog.company_id,
    AuditLog.actor_user_id,
    AuditLog.occurred_at.desc(),
)
Index(
    "ix_audit_log_company_entity_time",
    AuditLog.company_id,
    AuditLog.entity_type,
    AuditLog.entity_id,
    AuditLog.occurred_at.desc(),
)
Index(
    "ix_audit_log_company_action_time",
    AuditLog.company_id,
    AuditLog.action,
    AuditLog.occurred_at.desc(),
)


class SecurityEvent(EventMixin, Base):
    __tablename__ = "security_event"
    __table_args__ = (
        CheckConstraint("result IN ('FAILURE','DENIED')", name="result"),
        CheckConstraint(
            "(company_resolution = 'UNKNOWN' AND company_id IS NULL AND user_id IS NULL) OR "
            "(company_resolution = 'HOST_MAPPING' AND company_id IS NOT NULL "
            "AND user_id IS NULL) OR "
            "(company_resolution = 'AUTHENTICATED_USER' AND company_id IS NOT NULL "
            "AND user_id IS NOT NULL)",
            name="resolution",
        ),
    )
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("company.id"))
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("app_user.id"))
    session_id: Mapped[UUID | None] = mapped_column(ForeignKey("auth_session.id"))
    company_resolution: Mapped[str] = mapped_column(String(24))
    event_type: Mapped[str] = mapped_column(String(50))
    request_method: Mapped[str] = mapped_column(String(12))
    request_path: Mapped[str] = mapped_column(String(128))
    host: Mapped[str | None] = mapped_column(String(253))
    origin: Mapped[str | None] = mapped_column(String(260))
    identifier_hash: Mapped[str | None] = mapped_column(String(64))


Index("ix_security_event_time", SecurityEvent.occurred_at.desc(), SecurityEvent.id)
