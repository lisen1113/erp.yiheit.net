"""Add company-scoped category trees and attribute definitions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0012_product_category"
down_revision = "0011_supplier_profile"
branch_labels = None
depends_on = None

ACTIONS = ("view", "create", "edit", "disable", "move", "schema.manage", "import")
GRANTS = {
    "SYSTEM_ADMIN": ACTIONS,
    **dict.fromkeys(("MANAGEMENT", "SALES", "PURCHASE", "WAREHOUSE", "AUDITOR"), ("view",)),
}


def upgrade() -> None:
    op.create_table(
        "product_category",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey("company.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("normalized_name", sa.String(300), nullable=False),
        sa.Column("parent_id", sa.Uuid()),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column(
            "attribute_schema", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "parent_id IS DISTINCT FROM id", name=op.f("ck_product_category_not_own_parent")
        ),
        sa.CheckConstraint(
            "level >= 0 AND sort_order >= 0 AND schema_version >= 1",
            name=op.f("ck_product_category_bounds"),
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), server_default="1", nullable=False),
        sa.UniqueConstraint("company_id", "id"),
        sa.UniqueConstraint("company_id", "code"),
        sa.UniqueConstraint(
            "company_id", "parent_id", "normalized_name", postgresql_nulls_not_distinct=True
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "parent_id"],
            ["product_category.company_id", "product_category.id"],
            ondelete="RESTRICT",
        ),
    )
    for field in ("company_id", "created_at", "is_deleted", "parent_id"):
        op.create_index(f"ix_product_category_{field}", "product_category", [field])
    op.create_index(
        "ix_product_category_attribute_schema",
        "product_category",
        ["attribute_schema"],
        postgresql_using="gin",
    )
    connection = op.get_bind()
    for action in ACTIONS:
        connection.execute(
            sa.text("""
            INSERT INTO permission (code, module, action, description, sensitivity_level)
            VALUES (:code, 'product_category', :action, :code, 'standard')
        """),
            {"code": f"product_category.{action}", "action": action},
        )
    for role, actions in GRANTS.items():
        for action in actions:
            connection.execute(
                sa.text("""
                INSERT INTO role_permission (role_id, permission_id, granted_by)
                SELECT role.id, permission.id, role.created_by
                FROM role CROSS JOIN permission
                WHERE role.code = :role AND role.is_system AND permission.code = :permission
            """),
                {"role": role, "permission": f"product_category.{action}"},
            )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM product_category)")):
        raise RuntimeError(
            "Cannot downgrade while product categories exist; export and reconcile first."
        )
    op.execute("""
        DELETE FROM role_permission WHERE permission_id IN (
            SELECT id FROM permission WHERE module = 'product_category'
        )
    """)
    op.execute("""
        DELETE FROM permission WHERE module = 'product_category'
    """)
    op.drop_table("product_category")
