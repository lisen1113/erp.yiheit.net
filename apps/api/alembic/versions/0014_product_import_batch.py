"""Freeze normalized standard product import batches before independent review."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014_product_import_batch"
down_revision = "0013_product_master"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_import_batch",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey("company.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("source_file_sha256", sa.String(64), nullable=False),
        sa.Column("source_filename", sa.String(180), nullable=False),
        sa.Column("source_format", sa.String(8), nullable=False),
        sa.Column("source_file", sa.LargeBinary(), nullable=False),
        sa.Column("preview_hash", sa.String(64), nullable=False),
        sa.Column("normalized_payload_hash", sa.String(64), nullable=False),
        sa.Column("normalized_payload", JSONB(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("spu_count", sa.Integer(), nullable=False),
        sa.Column("sku_count", sa.Integer(), nullable=False),
        sa.Column("existing_spu_sku_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), server_default="PREVIEWED", nullable=False),
        sa.Column("submitted_by", sa.Uuid()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by", sa.Uuid()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        sa.Column("rejection_reason", sa.String(500)),
        sa.Column("failure_code", sa.String(64)),
        sa.CheckConstraint(
            "status IN ('PREVIEWED', 'SUBMITTED', 'APPROVED', 'REJECTED', 'COMMITTED', 'FAILED')",
            name=op.f("ck_product_import_batch_status"),
        ),
        sa.CheckConstraint(
            "approved_by IS NULL OR approved_by IS DISTINCT FROM submitted_by",
            name=op.f("ck_product_import_batch_independent"),
        ),
    )
    for key in ("company_id", "created_at"):
        op.create_index(f"ix_product_import_batch_{key}", "product_import_batch", [key])
    op.execute("""
        CREATE FUNCTION preserve_product_import_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Product import snapshot cannot be deleted';
            END IF;
            IF ROW(NEW.id, NEW.company_id, NEW.created_by, NEW.source_file_sha256,
                   NEW.source_filename,
                   NEW.source_format, NEW.source_file, NEW.preview_hash,
                   NEW.normalized_payload_hash,
                   NEW.normalized_payload, NEW.row_count, NEW.spu_count, NEW.sku_count,
                   NEW.existing_spu_sku_count)
                IS DISTINCT FROM
                ROW(OLD.id, OLD.company_id, OLD.created_by, OLD.source_file_sha256,
                   OLD.source_filename,
                   OLD.source_format, OLD.source_file, OLD.preview_hash,
                   OLD.normalized_payload_hash,
                   OLD.normalized_payload, OLD.row_count, OLD.spu_count, OLD.sku_count,
                   OLD.existing_spu_sku_count) THEN
                RAISE EXCEPTION 'Product import snapshot is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER product_import_snapshot_immutable
        BEFORE UPDATE OR DELETE ON product_import_batch
        FOR EACH ROW EXECUTE FUNCTION preserve_product_import_snapshot()
    """)
    op.execute("""
        INSERT INTO permission (code, module, action, description, sensitivity_level)
        VALUES ('product.import.approve', 'product', 'approve',
                'product.import.approve', 'standard')
    """)
    op.execute("""
        INSERT INTO role_permission (role_id, permission_id, granted_by)
        SELECT role.id, permission.id, role.created_by FROM role CROSS JOIN permission
        WHERE role.code = 'MANAGEMENT' AND role.is_system
          AND permission.code = 'product.import.approve'
    """)


def downgrade() -> None:
    op.execute("LOCK TABLE product_import_batch IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM product_import_batch)")):
        raise RuntimeError("Cannot downgrade while product import snapshots exist")
    op.drop_table("product_import_batch")
    op.execute("DROP FUNCTION preserve_product_import_snapshot()")
    op.execute("""
        DELETE FROM role_permission WHERE permission_id IN
            (SELECT id FROM permission WHERE code = 'product.import.approve')
    """)
    op.execute("DELETE FROM permission WHERE code = 'product.import.approve'")
