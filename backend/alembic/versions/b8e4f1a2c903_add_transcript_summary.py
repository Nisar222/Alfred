"""add transcript summary column

Revision ID: b8e4f1a2c903
Revises: e71c4a8d930f
"""
from alembic import op
import sqlalchemy as sa


revision = "b8e4f1a2c903"
down_revision = "e71c4a8d930f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcripts", sa.Column("summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("transcripts", "summary")
