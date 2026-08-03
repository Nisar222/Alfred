"""add configurable DTMF queue routing

Revision ID: c3a7e9d2f104
Revises: 8f6b2c1d4e7a
"""
from alembic import op
import sqlalchemy as sa


revision = "c3a7e9d2f104"
down_revision = "8f6b2c1d4e7a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("global_settings") as batch:
        batch.add_column(sa.Column("dtmf_routing_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("dtmf_menu_digit", sa.String(length=1), nullable=False, server_default="1"))
        batch.add_column(sa.Column("dtmf_queue_extension", sa.String(length=20), nullable=True))
    with op.batch_alter_table("campaigns") as batch:
        batch.add_column(sa.Column("dtmf_queue_extension_override", sa.String(length=20), nullable=True))
    with op.batch_alter_table("calls") as batch:
        batch.add_column(sa.Column("dtmf_digit", sa.String(length=1), nullable=True))
        batch.add_column(sa.Column("routed_destination", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("routing_status", sa.String(length=40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("calls") as batch:
        batch.drop_column("routing_status")
        batch.drop_column("routed_destination")
        batch.drop_column("dtmf_digit")
    with op.batch_alter_table("campaigns") as batch:
        batch.drop_column("dtmf_queue_extension_override")
    with op.batch_alter_table("global_settings") as batch:
        batch.drop_column("dtmf_queue_extension")
        batch.drop_column("dtmf_menu_digit")
        batch.drop_column("dtmf_routing_enabled")
