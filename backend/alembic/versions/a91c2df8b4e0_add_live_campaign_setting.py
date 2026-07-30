"""add live campaign calling setting

Revision ID: a91c2df8b4e0
Revises: 6c39c4b7ea21
"""
from alembic import op
import sqlalchemy as sa

revision = "a91c2df8b4e0"
down_revision = "6c39c4b7ea21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("global_settings") as batch:
        batch.add_column(sa.Column("live_campaign_calling_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("global_settings") as batch:
        batch.drop_column("live_campaign_calling_enabled")
