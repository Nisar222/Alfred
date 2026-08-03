"""add local Alfred password and revocable sessions

Revision ID: ab52d8e19f60
Revises: f18a4d9b72c3
"""
from alembic import op
import sqlalchemy as sa


revision = "ab52d8e19f60"
down_revision = "f18a4d9b72c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("password_hash", sa.String(length=512), nullable=True))
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_index("ix_auth_sessions_active", "auth_sessions", ["user_id", "expires_at", "revoked_at"])


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_active", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("password_hash")
