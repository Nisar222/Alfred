"""add rest (cool-off) period between calls on a gateway line

Revision ID: c8e2a5f19b37
Revises: b7c41e9d2a60
"""
from alembic import op
import sqlalchemy as sa


revision = "c8e2a5f19b37"
down_revision = "b7c41e9d2a60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("global_settings", sa.Column("line_cooloff_seconds", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("campaigns", sa.Column("line_cooloff_seconds_override", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "line_cooloff_seconds_override")
    op.drop_column("global_settings", "line_cooloff_seconds")
