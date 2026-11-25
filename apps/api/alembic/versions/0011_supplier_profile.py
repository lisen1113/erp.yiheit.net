"""Add company-scoped profiles without duplicating legal identities."""

import sqlalchemy as sa
from alembic import op

revision = "0011_supplier_profile"
down_revision = "0010_partner_bank_account_width"
branch_labels = None
depends_on = None

GRANTS = {
    "SYSTEM_ADMIN": ("view", "create", "edit", "disable", "import"),
    "MANAGEMENT": ("view",),
    "FINANCE": ("view",),
    "PURCHASE": ("view", "create", "edit", "import"),
    "AUDITOR": ("view",),
}


def upgrade() -> None:
    op.create_table(
        "supplier_profile",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey("company.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("business_partner_id", sa.Uuid(), nullable=False),
        sa.Column("supplier_no", sa.String(32), nullable=False),
        sa.Column("supplier_type", sa.String(32), server_default="GENERAL", nullable=False),
        sa.Column("buyer_id", sa.Uuid(), nullable=False),
        sa.Column("payment_term_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("default_contact_name", sa.String(120)),
        sa.Column("default_contact_phone", sa.String(64)),
        sa.Column("default_contact_email", sa.String(255)),
        sa.Column("delivery_or_pickup_notes", sa.String(500)),
        sa.CheckConstraint(
            "supplier_type IN ('DISTRIBUTOR', 'HARDWARE', 'SOFTWARE_SERVICE', 'GENERAL')",
            name=op.f("ck_supplier_profile_supplier_type"),
        ),
        sa.CheckConstraint(
            "payment_term_days >= 0", name=op.f("ck_supplier_profile_payment_term_days")
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
        sa.UniqueConstraint("company_id", "supplier_no"),
        sa.UniqueConstraint("company_id", "business_partner_id"),
        sa.ForeignKeyConstraint(
            ["company_id", "business_partner_id"],
            ["business_partner.company_id", "business_partner.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "buyer_id"],
            ["employee.company_id", "employee.id"],
            ondelete="RESTRICT",
        ),
    )
    for field in ("company_id", "created_at", "is_deleted", "buyer_id"):
        op.create_index(f"ix_supplier_profile_{field}", "supplier_profile", [field])
    connection = op.get_bind()
    for action in ("view", "create", "edit", "disable", "import"):
        connection.execute(
            sa.text("""
            INSERT INTO permission (code, module, action, description, sensitivity_level)
            VALUES (:code, 'supplier', :action, :code, 'standard')
        """),
            {"code": f"supplier.{action}", "action": action},
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
                {"role": role, "permission": f"supplier.{action}"},
            )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM supplier_profile)")):
        raise RuntimeError(
            "Cannot downgrade while supplier profiles exist; export and reconcile first."
        )
    op.execute("""
        DELETE FROM role_permission WHERE permission_id IN (
            SELECT id FROM permission WHERE module = 'supplier'
        )
    """)
    op.execute("""
        DELETE FROM permission WHERE module = 'supplier'
    """)
    op.drop_table("supplier_profile")
