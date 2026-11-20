"""Database-backed idempotency scoped to actor, action and object."""

from alembic import op

revision = "0005_idempotency"
down_revision = "0004_audit_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE idempotency_record (
            actor_user_id UUID NOT NULL,
            idempotency_key VARCHAR(128) NOT NULL,
            request_method VARCHAR(12) NOT NULL,
            request_path VARCHAR(255) NOT NULL,
            action VARCHAR(64) NOT NULL,
            object_scope VARCHAR(100) NOT NULL,
            request_hash VARCHAR(64) NOT NULL,
            status VARCHAR(24) NOT NULL,
            locked_at TIMESTAMP WITH TIME ZONE NOT NULL,
            completed_at TIMESTAMP WITH TIME ZONE,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            response_status INTEGER,
            response_body JSONB,
            response_headers JSONB,
            resource_type VARCHAR(50),
            resource_id UUID,
            trace_id VARCHAR(64) NOT NULL,
            error_code VARCHAR(64),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            version_no INTEGER DEFAULT 1 NOT NULL,
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            company_id UUID NOT NULL,
            CONSTRAINT pk_idempotency_record PRIMARY KEY (id),
            CONSTRAINT uq_idempotency_scope_key UNIQUE (company_id, actor_user_id, action,
                object_scope, idempotency_key),
            CONSTRAINT fk_idempotency_company_actor FOREIGN KEY(company_id, actor_user_id)
                REFERENCES app_user (company_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_idempotency_record_status CHECK (status IN
                ('IN_PROGRESS','COMPLETED','FAILED_RETRYABLE','FAILED_FINAL','EXPIRED')),
            CONSTRAINT ck_idempotency_record_expiry CHECK (expires_at > locked_at),
            CONSTRAINT fk_idempotency_record_company_id_company FOREIGN KEY(company_id) REFERENCES
                company (id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_idempotency_record_company_id ON idempotency_record (company_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_idempotency_status_expiry ON idempotency_record (status, expires_at)
        """
    )


def downgrade() -> None:
    op.drop_table("idempotency_record")
