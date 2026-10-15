"""add gateway SIM phone numbers

Revision ID: d4f7b2c81e95
Revises: c8e2a5f19b37
"""
from alembic import op
import sqlalchemy as sa


revision = "d4f7b2c81e95"
down_revision = "c8e2a5f19b37"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gateway_lines", sa.Column("sim_number", sa.String(length=32), nullable=True))
    op.add_column("calls", sa.Column("gateway_sim_number", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("calls", "gateway_sim_number")
    op.drop_column("gateway_lines", "sim_number")
