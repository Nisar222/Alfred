"""add multi-digit DTMF routes JSON column

Revision ID: c9d2e8f1a704
Revises: b8e4f1a2c903
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision = "c9d2e8f1a704"
down_revision = "b8e4f1a2c903"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "global_settings",
        sa.Column("dtmf_routes_json", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.execute(
        text(
            """
            UPDATE global_settings
            SET dtmf_routes_json = json_build_object(dtmf_menu_digit, dtmf_queue_extension)
            WHERE dtmf_queue_extension IS NOT NULL AND dtmf_queue_extension <> ''
            """
        )
    )


def downgrade() -> None:
    op.drop_column("global_settings", "dtmf_routes_json")
