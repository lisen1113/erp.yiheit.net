"""Widen the shared bank account column to the frozen customer dictionary limit."""

import sqlalchemy as sa
from alembic import op

revision = "0010_partner_bank_account_width"
down_revision = "0009_customer_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "business_partner",
        "invoice_bank_account",
        existing_type=sa.String(128),
        type_=sa.String(255),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM business_partner WHERE char_length(invoice_bank_account) > 128
            ) THEN
                RAISE EXCEPTION 'Bank account width downgrade would discard existing data';
            END IF;
        END
        $$;
    """)
    op.alter_column(
        "business_partner",
        "invoice_bank_account",
        existing_type=sa.String(255),
        type_=sa.String(128),
        existing_nullable=True,
    )
