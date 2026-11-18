"""Minimal employee identity and authentication records. Extend employee incrementally."""

from alembic import op

revision = "0002_auth_identity"
down_revision = "0001_company_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE employee (
            employee_no VARCHAR(32) NOT NULL,
            name VARCHAR(200) NOT NULL,
            email VARCHAR(254),
            mobile VARCHAR(32),
            is_active BOOLEAN DEFAULT true NOT NULL,
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            company_id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            created_by UUID NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_by UUID NOT NULL,
            version_no INTEGER DEFAULT 1 NOT NULL,
            CONSTRAINT pk_employee PRIMARY KEY (id),
            CONSTRAINT uq_employee_company_id_employee_no UNIQUE (company_id, employee_no),
            CONSTRAINT uq_employee_company_id_id UNIQUE (company_id, id),
            CONSTRAINT fk_employee_company_id_company FOREIGN KEY(company_id)
                REFERENCES company (id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_employee_company_id ON employee (company_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_employee_created_at ON employee (created_at)
        """
    )
    op.execute(
        """
        CREATE TABLE app_user (
            employee_id UUID NOT NULL,
            username VARCHAR(100) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            status VARCHAR(16) DEFAULT 'active' NOT NULL,
            failed_login_count INTEGER DEFAULT 0 NOT NULL,
            failure_window_at TIMESTAMP WITH TIME ZONE,
            locked_until TIMESTAMP WITH TIME ZONE,
            mfa_required BOOLEAN DEFAULT false NOT NULL,
            mfa_enabled BOOLEAN DEFAULT false NOT NULL,
            mfa_secret_encrypted VARCHAR(255),
            mfa_last_step INTEGER,
            mfa_setup_expires_at TIMESTAMP WITH TIME ZONE,
            password_changed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            last_login_at TIMESTAMP WITH TIME ZONE,
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            company_id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            created_by UUID NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_by UUID NOT NULL,
            version_no INTEGER DEFAULT 1 NOT NULL,
            CONSTRAINT pk_app_user PRIMARY KEY (id),
            CONSTRAINT uq_app_user_company_id_id UNIQUE (company_id, id),
            CONSTRAINT fk_app_user_company_employee FOREIGN KEY(company_id, employee_id)
                REFERENCES employee (company_id, id) ON DELETE RESTRICT,
            CONSTRAINT uq_app_user_employee_id UNIQUE (employee_id),
            CONSTRAINT uq_app_user_username UNIQUE (username),
            CONSTRAINT fk_app_user_company_id_company FOREIGN KEY(company_id)
                REFERENCES company (id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_app_user_company_id ON app_user (company_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_app_user_created_at ON app_user (created_at)
        """
    )
    op.execute(
        """
        CREATE TABLE auth_session (
            user_id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            revoked_at TIMESTAMP WITH TIME ZONE,
            last_used_at TIMESTAMP WITH TIME ZONE,
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            company_id UUID NOT NULL,
            CONSTRAINT pk_auth_session PRIMARY KEY (id),
            CONSTRAINT fk_auth_session_company_user FOREIGN KEY(company_id, user_id)
                REFERENCES app_user (company_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_auth_session_company_id_company FOREIGN KEY(company_id)
                REFERENCES company (id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_auth_session_company_id ON auth_session (company_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_auth_session_user_id ON auth_session (user_id)
        """
    )
    op.execute(
        """
        CREATE TABLE auth_refresh_grant (
            token_hash VARCHAR(64) NOT NULL,
            session_id UUID NOT NULL,
            consumed_at TIMESTAMP WITH TIME ZONE,
            CONSTRAINT pk_auth_refresh_grant PRIMARY KEY (token_hash),
            CONSTRAINT fk_auth_refresh_grant_session_id_auth_session FOREIGN KEY(session_id)
                REFERENCES auth_session (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_auth_refresh_grant_session_id ON auth_refresh_grant (session_id)
        """
    )
    op.execute(
        """
        CREATE TABLE auth_login_challenge (
            token_hash VARCHAR(64) NOT NULL,
            user_id UUID NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            consumed BOOLEAN DEFAULT false NOT NULL,
            CONSTRAINT pk_auth_login_challenge PRIMARY KEY (token_hash),
            CONSTRAINT fk_auth_login_challenge_user_id_app_user FOREIGN KEY(user_id)
                REFERENCES app_user (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_auth_login_challenge_user_id ON auth_login_challenge (user_id)
        """
    )


def downgrade() -> None:
    op.drop_table("auth_login_challenge")
    op.drop_table("auth_refresh_grant")
    op.drop_table("auth_session")
    op.drop_table("app_user")
    op.drop_table("employee")
