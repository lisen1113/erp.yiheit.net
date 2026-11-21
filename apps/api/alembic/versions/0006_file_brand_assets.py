"""Private file metadata and versioned company branding."""

from alembic import op

revision = "0006_file_brand_assets"
down_revision = "0005_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE file_asset (
            asset_type VARCHAR(24) NOT NULL,
            original_filename VARCHAR(180) NOT NULL,
            display_name VARCHAR(180) NOT NULL,
            object_key VARCHAR(255) NOT NULL,
            bucket VARCHAR(128) NOT NULL,
            storage_provider VARCHAR(12) NOT NULL,
            storage_version VARCHAR(1024),
            content_type VARCHAR(128) NOT NULL,
            size_bytes BIGINT NOT NULL,
            expected_sha256 VARCHAR(64) NOT NULL,
            sha256 VARCHAR(64),
            status VARCHAR(24) NOT NULL,
            sensitivity_level VARCHAR(16) NOT NULL,
            uploaded_by UUID NOT NULL,
            uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            confirmed_at TIMESTAMP WITH TIME ZONE,
            expires_at TIMESTAMP WITH TIME ZONE,
            retention_class VARCHAR(16) NOT NULL,
            source VARCHAR(16) NOT NULL,
            metadata JSONB NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            version_no INTEGER DEFAULT 1 NOT NULL,
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            company_id UUID NOT NULL,
            CONSTRAINT pk_file_asset PRIMARY KEY (id),
            CONSTRAINT uq_file_asset_company_id_id UNIQUE (company_id, id),
            CONSTRAINT uq_file_asset_storage_provider_bucket_object_key UNIQUE (storage_provider,
                bucket, object_key),
            CONSTRAINT fk_file_asset_company_uploader FOREIGN KEY(company_id, uploaded_by)
                REFERENCES app_user (company_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_file_asset_bounded_size CHECK (size_bytes > 0 AND size_bytes <=
                524288000),
            CONSTRAINT ck_file_asset_file_status CHECK (status IN
                ('UPLOADING','AVAILABLE','QUARANTINED','REJECTED','EXPIRED','INVALIDATED','DELETED_FROM_STORAGE')),
            CONSTRAINT ck_file_asset_available_integrity CHECK (status <> 'AVAILABLE' OR
                (storage_version IS NOT NULL AND sha256 IS NOT NULL AND confirmed_at IS NOT
                NULL)),
            CONSTRAINT ck_file_asset_digest_format CHECK (sha256 IS NULL OR sha256 ~
                '^[a-f0-9]{64}$'),
            CONSTRAINT ck_file_asset_expected_digest_format CHECK (expected_sha256 ~
                '^[a-f0-9]{64}$'),
            CONSTRAINT fk_file_asset_company_id_company FOREIGN KEY(company_id) REFERENCES company
                (id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_file_asset_company_id ON file_asset (company_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_file_asset_company_sha256 ON file_asset (company_id, sha256)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_file_asset_status_expires ON file_asset (status, expires_at)
        """
    )
    op.execute(
        """
        CREATE TABLE brand_asset (
            file_asset_id UUID NOT NULL,
            asset_code VARCHAR(50) NOT NULL,
            asset_type VARCHAR(24) NOT NULL,
            language VARCHAR(8) NOT NULL,
            version INTEGER NOT NULL,
            valid_from TIMESTAMP WITH TIME ZONE,
            valid_to TIMESTAMP WITH TIME ZONE,
            is_active BOOLEAN DEFAULT false NOT NULL,
            approved_by UUID,
            approved_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            version_no INTEGER DEFAULT 1 NOT NULL,
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            company_id UUID NOT NULL,
            CONSTRAINT pk_brand_asset PRIMARY KEY (id),
            CONSTRAINT uq_brand_asset_company_id_asset_code_version UNIQUE (company_id,
                asset_code, version),
            CONSTRAINT fk_brand_asset_company_file FOREIGN KEY(company_id, file_asset_id)
                REFERENCES file_asset (company_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_brand_asset_valid_window CHECK (valid_to IS NULL OR valid_to >=
                valid_from),
            CONSTRAINT ck_brand_asset_active_integrity CHECK (NOT is_active OR (valid_from IS NOT
                NULL AND valid_to IS NULL)),
            CONSTRAINT ck_brand_asset_brand_type CHECK (asset_type IN
                ('LOGO_HORIZONTAL','LOGO_COMPACT','LOGO_LIGHT_BACKGROUND','LOGO_DARK_BACKGROUND','DOCUMENT_BACKGROUND','DOCUMENT_FOOTER','EMAIL_HEADER')),
            CONSTRAINT fk_brand_asset_approved_by_app_user FOREIGN KEY(approved_by) REFERENCES
                app_user (id),
            CONSTRAINT fk_brand_asset_company_id_company FOREIGN KEY(company_id) REFERENCES
                company (id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_brand_asset_company_id ON brand_asset (company_id)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_brand_asset_active_purpose ON brand_asset (company_id, asset_type,
            language) WHERE is_active
        """
    )
    op.execute(
        """
        INSERT INTO permission (code,module,action,description,sensitivity_level) VALUES
        ('file_asset.upload','file_asset','upload','file_asset.upload','standard'),
        ('file_asset.view','file_asset','view','file_asset.view','standard'),
        ('file_asset.download','file_asset','download','file_asset.download','standard'),
        ('file_asset.invalidate','file_asset','invalidate','file_asset.invalidate','standard'),
        ('file_asset.view_sensitive','file_asset','view_sensitive','file_asset.view_sensitive','high'),
        ('brand_asset.view','brand_asset','view','brand_asset.view','standard'),
        ('brand_asset.manage','brand_asset','manage','brand_asset.manage','high'),
        ('migration_file.upload','migration_file','upload','migration_file.upload','high'),
        ('migration_file.download','migration_file','download','migration_file.download','high')
        ON CONFLICT (code) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO role_permission (role_id, permission_id, granted_by)
        SELECT r.id,p.id,r.updated_by FROM role r JOIN app_user u ON u.id=r.updated_by JOIN
            permission p ON (r.code='SYSTEM_ADMIN' AND p.code IN
            ('brand_asset.manage','brand_asset.view','file_asset.download','file_asset.invalidate','file_asset.upload','file_asset.view'))
            OR (r.code='MANAGEMENT' AND p.code='brand_asset.view') WHERE r.is_system ON CONFLICT
            DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("brand_asset")
    op.drop_table("file_asset")
    op.execute(
        """
        DELETE FROM role_permission rp USING permission p WHERE rp.permission_id=p.id AND p.code
            IN ('file_asset.upload', 'file_asset.view', 'file_asset.download',
            'file_asset.invalidate', 'file_asset.view_sensitive', 'brand_asset.view',
            'brand_asset.manage', 'migration_file.upload', 'migration_file.download')
        """
    )
    op.execute(
        """
        DELETE FROM permission WHERE code IN ('file_asset.upload', 'file_asset.view',
            'file_asset.download', 'file_asset.invalidate', 'file_asset.view_sensitive',
            'brand_asset.view', 'brand_asset.manage', 'migration_file.upload',
            'migration_file.download')
        """
    )
