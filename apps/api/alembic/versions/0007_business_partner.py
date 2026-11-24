"""Unified business partner records and initial permissions."""

import sqlalchemy as sa
from alembic import op

revision = "0007_business_partner"
down_revision = "0006_file_brand_assets"
branch_labels = None
depends_on = None

VIEW = "business_partner.view"
FULL = "business_partner.bank_account.view_full"
GRANTS = {
    "SYSTEM_ADMIN": (
        VIEW,
        "business_partner.create",
        "business_partner.edit",
        "business_partner.disable",
    ),
    "MANAGEMENT": (VIEW,),
    "FINANCE": (VIEW, FULL),
    "SALES": ("business_partner.view_customer",),
    "PURCHASE": ("business_partner.view_supplier",),
    "WAREHOUSE": ("business_partner.view_basic",),
    "AUDITOR": (VIEW,),
}


def upgrade() -> None:
    op.create_table(
        "business_partner",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey("company.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("partner_type", sa.String(8), nullable=False),
        sa.Column("currency_code", sa.String(3), server_default="CNY", nullable=False),
        sa.Column("credit_limit", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("payment_terms_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_legacy", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("legacy_readonly", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("legacy_system", sa.String(64)),
        sa.Column("legacy_id", sa.String(128)),
        sa.Column("migration_batch_id", sa.Uuid()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), server_default="1", nullable=False),
        sa.Column("short_name", sa.String(120)),
        sa.Column("unified_social_credit_code", sa.String(32)),
        sa.Column("taxpayer_type", sa.String(40)),
        sa.Column("tax_registration_no", sa.String(64)),
        sa.Column("contact_name", sa.String(120)),
        sa.Column("phone", sa.String(64)),
        sa.Column("email", sa.String(254)),
        sa.Column("registered_address", sa.String(500)),
        sa.Column("business_address", sa.String(500)),
        sa.Column("invoice_title", sa.String(200)),
        sa.Column("invoice_tax_no", sa.String(64)),
        sa.Column("invoice_address_phone", sa.String(500)),
        sa.Column("invoice_bank_name", sa.String(200)),
        sa.Column("invoice_bank_account", sa.String(128)),
        sa.Column("notes", sa.String(2000)),
        sa.UniqueConstraint("company_id", "code"),
        sa.UniqueConstraint("company_id", "unified_social_credit_code"),
        sa.CheckConstraint("partner_type IN ('CUSTOMER', 'SUPPLIER', 'BOTH')", name="partner_type"),
        sa.CheckConstraint("currency_code = 'CNY'", name="currency_cny"),
        sa.CheckConstraint("credit_limit >= 0", name="credit_limit_nonnegative"),
        sa.CheckConstraint("payment_terms_days >= 0", name="payment_terms_nonnegative"),
        sa.CheckConstraint("version_no >= 1", name="version_positive"),
    )
    for field in ("company_id", "created_at", "is_deleted", "is_legacy", "migration_batch_id"):
        op.create_index(f"ix_business_partner_{field}", "business_partner", [field])
    connection = op.get_bind()
    for code in sorted({code for grants in GRANTS.values() for code in grants}):
        connection.execute(
            sa.text("""
                INSERT INTO permission (code, module, action, description, sensitivity_level)
                VALUES (:code, 'business_partner', :action, :code, :sensitivity)
            """),
            {
                "code": code,
                "action": code.split(".")[-1],
                "sensitivity": "high" if code == FULL else "standard",
            },
        )
    for role, grants in GRANTS.items():
        for code in grants:
            connection.execute(
                sa.text("""
                INSERT INTO role_permission (role_id, permission_id, granted_by)
                SELECT role.id, permission.id, role.created_by
                FROM role CROSS JOIN permission
                WHERE role.code = :role AND role.is_system AND permission.code = :code
            """),
                {"role": role, "code": code},
            )


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_permission WHERE permission_id IN "
        "(SELECT id FROM permission WHERE module = 'business_partner')"
    )
    op.execute("DELETE FROM permission WHERE module = 'business_partner'")
    op.drop_table("business_partner")
