"""add retry policy settings

Revision ID: 8f6b2c1d4e7a
Revises: d45a6f0b9c12
"""
from alembic import op
import sqlalchemy as sa


revision = "8f6b2c1d4e7a"
down_revision = "d45a6f0b9c12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("global_settings") as batch:
        batch.add_column(sa.Column("retry_max_attempts", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("retry_delay_minutes", sa.Integer(), nullable=False, server_default="60"))
        batch.add_column(sa.Column("retry_no_answer", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("retry_busy", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("retry_provider_failure", sa.Boolean(), nullable=False, server_default=sa.true()))
    with op.batch_alter_table("calls") as batch:
        batch.add_column(sa.Column("previous_attempt_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("failure_category", sa.String(length=40), nullable=True))
        batch.create_foreign_key("fk_calls_previous_attempt", "calls", ["previous_attempt_id"], ["id"], ondelete="RESTRICT")
        batch.create_unique_constraint("uq_calls_previous_attempt", ["previous_attempt_id"])
        batch.create_index("ix_calls_scheduled_for", ["scheduled_for"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("calls") as batch:
        batch.drop_index("ix_calls_scheduled_for")
        batch.drop_constraint("uq_calls_previous_attempt", type_="unique")
        batch.drop_constraint("fk_calls_previous_attempt", type_="foreignkey")
        batch.drop_column("failure_category")
        batch.drop_column("scheduled_for")
        batch.drop_column("attempt_number")
        batch.drop_column("previous_attempt_id")
    with op.batch_alter_table("global_settings") as batch:
        batch.drop_column("retry_provider_failure")
        batch.drop_column("retry_busy")
        batch.drop_column("retry_no_answer")
        batch.drop_column("retry_delay_minutes")
        batch.drop_column("retry_max_attempts")
