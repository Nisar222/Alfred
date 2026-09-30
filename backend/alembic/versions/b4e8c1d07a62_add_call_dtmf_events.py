"""add per-call DTMF keypress history

Revision ID: b4e8c1d07a62
Revises: c9d2e8f1a704
"""
from alembic import op
import sqlalchemy as sa


revision = "b4e8c1d07a62"
down_revision = "c9d2e8f1a704"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calls",
        sa.Column("dtmf_events_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("calls", "dtmf_events_json")
