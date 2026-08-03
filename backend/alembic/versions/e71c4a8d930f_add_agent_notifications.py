"""add durable agent popup notifications

Revision ID: e71c4a8d930f
Revises: ab52d8e19f60
"""
from alembic import op
import sqlalchemy as sa


revision = "e71c4a8d930f"
down_revision = "ab52d8e19f60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("call_id", sa.Integer(), nullable=False),
        sa.Column("recipient_user_id", sa.Integer(), nullable=True),
        sa.Column("recipient_extension", sa.String(length=20), nullable=True),
        sa.Column("customer_name", sa.String(length=120), nullable=True),
        sa.Column("campaign_name", sa.String(length=150), nullable=False),
        sa.Column("menu_option", sa.String(length=1), nullable=True),
        sa.Column("routed_destination", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("call_id", name="uq_agent_notifications_call_id"),
    )
    op.create_index("ix_agent_notifications_recipient_unread", "agent_notifications", ["recipient_user_id", "read_at", "created_at"])
    op.create_index("ix_agent_notifications_extension_unread", "agent_notifications", ["recipient_extension", "read_at", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_notifications_extension_unread", table_name="agent_notifications")
    op.drop_index("ix_agent_notifications_recipient_unread", table_name="agent_notifications")
    op.drop_table("agent_notifications")
