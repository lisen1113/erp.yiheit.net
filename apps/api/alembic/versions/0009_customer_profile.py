"""Add company-scoped profiles without duplicating legal identities."""

import sqlalchemy as sa
from alembic import op

revision = "0009_customer_profile"
down_revision = "0008_partner_tax_no"
branch_labels = None
depends_on = None

GRANTS = {
    "SYSTEM_ADMIN": ("view", "create", "edit", "disable"),
    "MANAGEMENT": ("view",),
    "FINANCE": ("view",),
    "SALES_MANAGER": ("view",),
    "SALES": ("view",),
    "AUDITOR": ("view",),
}


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_business_partner_company_id_id",
        "business_partner",
        ["company_id", "id"],
    )
    op.create_table(
        "customer_profile",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey("company.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("business_partner_id", sa.Uuid(), nullable=False),
        sa.Column("customer_no", sa.String(32), nullable=False),
        sa.Column("customer_type", sa.String(32), server_default="enterprise", nullable=False),
        sa.Column("salesperson_id", sa.Uuid(), nullable=False),
        sa.Column("delivery_address", sa.String(500)),
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
        sa.UniqueConstraint("company_id", "customer_no"),
        sa.UniqueConstraint("company_id", "business_partner_id"),
        sa.ForeignKeyConstraint(
            ["company_id", "business_partner_id"],
            ["business_partner.company_id", "business_partner.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "salesperson_id"],
            ["employee.company_id", "employee.id"],
            ondelete="RESTRICT",
        ),
    )
    for field in ("company_id", "created_at", "is_deleted", "salesperson_id"):
        op.create_index(f"ix_customer_profile_{field}", "customer_profile", [field])
    connection = op.get_bind()
    for action in ("view", "create", "edit", "disable"):
        connection.execute(
            sa.text("""
            INSERT INTO permission (code, module, action, description, sensitivity_level)
            VALUES (:code, 'customer', :action, :code, 'standard')
        """),
            {"code": f"customer.{action}", "action": action},
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
                {"role": role, "permission": f"customer.{action}"},
            )


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permission WHERE permission_id IN (
            SELECT id FROM permission WHERE module = 'customer'
        )
    """)
    op.execute("""
        DELETE FROM permission WHERE module = 'customer'
    """)
    op.drop_table("customer_profile")
    op.drop_constraint("uq_business_partner_company_id_id", "business_partner", type_="unique")
