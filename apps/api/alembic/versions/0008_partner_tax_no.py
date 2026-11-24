"""Normalize and enforce company-scoped invoice tax numbers."""

import sqlalchemy as sa
from alembic import op

revision = "0008_partner_tax_no"
down_revision = "0007_business_partner"
branch_labels = None
depends_on = None

# Keep the edge whitespace set aligned with Python str.strip().
CANONICAL = r"""
    NULLIF(
        translate(
            replace(
                btrim(
                    normalize(tax_no, NFKC),
                    U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0'
                    || U&'\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A'
                    || U&'\2028\2029\202F\205F\3000'
                ),
                ' ', ''
            ),
            'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        ),
        ''
    )
"""


def upgrade() -> None:
    op.alter_column("business_partner", "invoice_tax_no", new_column_name="tax_no")
    op.execute(f"UPDATE business_partner SET tax_no = {CANONICAL} WHERE tax_no IS NOT NULL")
    op.create_check_constraint(
        "tax_no_normalized",
        "business_partner",
        f"tax_no IS NOT DISTINCT FROM ({CANONICAL})",
    )
    op.create_index(
        "uq_business_partner_company_tax_no",
        "business_partner",
        ["company_id", "tax_no"],
        unique=True,
        postgresql_where=sa.text("tax_no IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_business_partner_company_tax_no", table_name="business_partner")
    op.drop_constraint(
        op.f("ck_business_partner_tax_no_normalized"), "business_partner", type_="check"
    )
    op.alter_column("business_partner", "tax_no", new_column_name="invoice_tax_no")
