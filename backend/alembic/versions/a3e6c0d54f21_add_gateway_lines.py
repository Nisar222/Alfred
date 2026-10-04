"""add Dinstar gateway lines and per-call line tracking

Revision ID: a3e6c0d54f21
Revises: d7f3a1c95b28
"""
from alembic import op
import sqlalchemy as sa


revision = "a3e6c0d54f21"
down_revision = "d7f3a1c95b28"
branch_labels = None
depends_on = None

ACTIVE_LINE_WHERE = "status = 'in_progress' AND gateway_line IS NOT NULL"


def upgrade() -> None:
    gateway_lines = op.create_table(
        "gateway_lines",
        sa.Column("number", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("prefix", sa.String(length=8), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("number BETWEEN 1 AND 32", name="ck_gateway_lines_number"),
        sa.UniqueConstraint("prefix", name="uq_gateway_lines_prefix"),
    )
    op.bulk_insert(gateway_lines, [
        {"number": number, "prefix": f"88{number:02d}", "enabled": True} for number in range(1, 33)
    ])

    op.add_column("campaigns", sa.Column("gateway_lines_json", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("calls", sa.Column("gateway_line", sa.Integer(), nullable=True))
    op.add_column("calls", sa.Column("gateway_prefix", sa.String(length=8), nullable=True))
    op.create_index("ix_calls_gateway_line", "calls", ["gateway_line"])
    op.create_index(
        "uq_calls_active_gateway_line", "calls", ["gateway_line"], unique=True,
        postgresql_where=sa.text(ACTIVE_LINE_WHERE), sqlite_where=sa.text(ACTIVE_LINE_WHERE),
    )


def downgrade() -> None:
    op.drop_index("uq_calls_active_gateway_line", table_name="calls")
    op.drop_index("ix_calls_gateway_line", table_name="calls")
    op.drop_column("calls", "gateway_prefix")
    op.drop_column("calls", "gateway_line")
    op.drop_column("campaigns", "gateway_lines_json")
    op.drop_table("gateway_lines")
