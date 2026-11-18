"""Create the minimal company registry."""

import sqlalchemy as sa
from alembic import op

revision = "0001_company_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.CheckConstraint("version_no >= 1", name=op.f("ck_company_version_no_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company")),
        sa.UniqueConstraint("code", name=op.f("uq_company_code")),
    )
    op.create_index(op.f("ix_company_created_at"), "company", ["created_at"])
    op.create_index(op.f("ix_company_is_deleted"), "company", ["is_deleted"])


def downgrade() -> None:
    op.drop_table("company")
