"""Database-backed authorization, canonical usernames and MFA-bound sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0003_rbac"
down_revision = "0002_auth_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM app_user GROUP BY lower(username) HAVING count(*) > 1)
            THEN RAISE EXCEPTION 'Case-insensitive username collision; resolve before migration';
            END IF;
        END $$
    """)
    op.add_column(
        "app_user",
        sa.Column(
            "username_normalized",
            sa.String(100),
            sa.Computed("lower(username)", persisted=True),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_app_user_username_normalized", "app_user", ["username_normalized"]
    )
    op.add_column(
        "auth_session",
        sa.Column(
            "mfa_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        """
        CREATE TABLE role (
            code VARCHAR(50) NOT NULL,
            name VARCHAR(100) NOT NULL,
            description VARCHAR(255),
            data_scope VARCHAR(20) DEFAULT 'self' NOT NULL,
            requires_mfa BOOLEAN DEFAULT false NOT NULL,
            is_system BOOLEAN DEFAULT false NOT NULL,
            is_active BOOLEAN DEFAULT true NOT NULL,
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            company_id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            created_by UUID NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_by UUID NOT NULL,
            version_no INTEGER DEFAULT 1 NOT NULL,
            is_deleted BOOLEAN DEFAULT false NOT NULL,
            CONSTRAINT pk_role PRIMARY KEY (id),
            CONSTRAINT uq_role_company_id_code UNIQUE (company_id, code),
            CONSTRAINT uq_role_company_id_id UNIQUE (company_id, id),
            CONSTRAINT ck_role_normalized_code CHECK (code ~ '^[A-Z][A-Z0-9_]*$'),
            CONSTRAINT ck_role_data_scope CHECK (data_scope IN ('self','department','company')),
            CONSTRAINT fk_role_company_id_company FOREIGN KEY(company_id)
                REFERENCES company (id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_role_company_id ON role (company_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_role_created_at ON role (created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_role_is_deleted ON role (is_deleted)
        """
    )
    op.execute(
        """
        CREATE TABLE permission (
            code VARCHAR(100) NOT NULL,
            module VARCHAR(50) NOT NULL,
            action VARCHAR(50) NOT NULL,
            description VARCHAR(255) NOT NULL,
            sensitivity_level VARCHAR(16) DEFAULT 'standard' NOT NULL,
            is_active BOOLEAN DEFAULT true NOT NULL,
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            CONSTRAINT pk_permission PRIMARY KEY (id),
            CONSTRAINT ck_permission_normalized_code CHECK (code = lower(code)),
            CONSTRAINT ck_permission_sensitivity CHECK (sensitivity_level IN ('standard','high')),
            CONSTRAINT uq_permission_code UNIQUE (code)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_permission_module ON permission (module)
        """
    )
    op.execute(
        """
        CREATE TABLE user_role (
            user_id UUID NOT NULL,
            role_id UUID NOT NULL,
            valid_from TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            valid_until TIMESTAMP WITH TIME ZONE,
            assigned_by UUID NOT NULL,
            assigned_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            revoked_at TIMESTAMP WITH TIME ZONE,
            revoked_by UUID,
            version_no INTEGER DEFAULT 1 NOT NULL,
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            company_id UUID NOT NULL,
            CONSTRAINT pk_user_role PRIMARY KEY (id),
            CONSTRAINT fk_user_role_company_user FOREIGN KEY(company_id, user_id)
                REFERENCES app_user (company_id, id),
            CONSTRAINT fk_user_role_company_role FOREIGN KEY(company_id, role_id)
                REFERENCES role (company_id, id),
            CONSTRAINT ck_user_role_valid_window
                CHECK (valid_until IS NULL OR valid_until > valid_from),
            CONSTRAINT fk_user_role_assigned_by_app_user FOREIGN KEY(assigned_by)
                REFERENCES app_user (id),
            CONSTRAINT fk_user_role_revoked_by_app_user FOREIGN KEY(revoked_by)
                REFERENCES app_user (id),
            CONSTRAINT fk_user_role_company_id_company FOREIGN KEY(company_id)
                REFERENCES company (id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_user_role_company_id ON user_role (company_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_user_role_role_id ON user_role (role_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_user_role_user_id ON user_role (user_id)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_user_role_open ON user_role (company_id, user_id, role_id)
            WHERE revoked_at IS NULL
        """
    )
    op.execute(
        """
        CREATE TABLE role_permission (
            role_id UUID NOT NULL,
            permission_id UUID NOT NULL,
            granted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            granted_by UUID NOT NULL,
            CONSTRAINT pk_role_permission PRIMARY KEY (role_id, permission_id),
            CONSTRAINT fk_role_permission_role_id_role FOREIGN KEY(role_id)
                REFERENCES role (id),
            CONSTRAINT fk_role_permission_permission_id_permission FOREIGN KEY(permission_id)
                REFERENCES permission (id),
            CONSTRAINT fk_role_permission_granted_by_app_user FOREIGN KEY(granted_by)
                REFERENCES app_user (id)
        )
        """
    )


def downgrade() -> None:
    op.drop_table("role_permission")
    op.drop_table("user_role")
    op.drop_table("permission")
    op.drop_table("role")
    op.drop_column("auth_session", "mfa_verified")
    op.drop_constraint("uq_app_user_username_normalized", "app_user", type_="unique")
    op.drop_column("app_user", "username_normalized")
