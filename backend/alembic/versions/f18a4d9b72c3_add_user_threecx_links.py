"""add Alfred user links to 3CX directory identities

Revision ID: f18a4d9b72c3
Revises: c3a7e9d2f104
"""
from alembic import op
import sqlalchemy as sa


revision = "f18a4d9b72c3"
down_revision = "c3a7e9d2f104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `operator` is retained for existing historical users.  New UI work will
    # present the clearer owner/supervisor/agent roles before enforcing login.
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("threecx_user_id", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("threecx_extension", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("threecx_last_synced_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_unique_constraint("uq_users_threecx_user_id", ["threecx_user_id"])
        batch.create_unique_constraint("uq_users_threecx_extension", ["threecx_extension"])
        batch.create_check_constraint(
            "ck_users_role", "role IN ('owner', 'supervisor', 'agent', 'operator')"
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_role", type_="check")
        batch.drop_constraint("uq_users_threecx_extension", type_="unique")
        batch.drop_constraint("uq_users_threecx_user_id", type_="unique")
        batch.drop_column("threecx_last_synced_at")
        batch.drop_column("threecx_extension")
        batch.drop_column("threecx_user_id")
