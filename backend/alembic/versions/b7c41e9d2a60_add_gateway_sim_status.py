"""add live gateway SIM status

Revision ID: b7c41e9d2a60
Revises: a3e6c0d54f21
"""
from alembic import op
import sqlalchemy as sa


revision = "b7c41e9d2a60"
down_revision = "a3e6c0d54f21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gateway_lines", sa.Column("sim_registration", sa.String(length=40), nullable=True))
    op.add_column("gateway_lines", sa.Column("sim_signal", sa.Integer(), nullable=True))
    op.add_column("gateway_lines", sa.Column("sim_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("global_settings", sa.Column("sim_check_last_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("global_settings", sa.Column("sim_check_last_success_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("global_settings", sa.Column("sim_check_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("global_settings", "sim_check_error")
    op.drop_column("global_settings", "sim_check_last_success_at")
    op.drop_column("global_settings", "sim_check_last_attempt_at")
    op.drop_column("gateway_lines", "sim_checked_at")
    op.drop_column("gateway_lines", "sim_signal")
    op.drop_column("gateway_lines", "sim_registration")
