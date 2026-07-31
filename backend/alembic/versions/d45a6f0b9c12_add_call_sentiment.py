"""add call sentiment review fields

Revision ID: d45a6f0b9c12
Revises: a91c2df8b4e0
"""
from alembic import op
import sqlalchemy as sa


revision = "d45a6f0b9c12"
down_revision = "a91c2df8b4e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sentiment = sa.Enum("positive", "neutral", "negative", "unknown", name="call_sentiment")
    sentiment.create(op.get_bind(), checkfirst=True)
    with op.batch_alter_table("calls") as batch:
        batch.add_column(sa.Column("sentiment", sentiment, nullable=False, server_default="unknown"))
        batch.add_column(sa.Column("sentiment_confidence", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("sentiment_source", sa.String(40), nullable=False, server_default="not_available"))
        batch.create_index("ix_calls_sentiment", ["sentiment"])


def downgrade() -> None:
    with op.batch_alter_table("calls") as batch:
        batch.drop_index("ix_calls_sentiment")
        batch.drop_column("sentiment_source")
        batch.drop_column("sentiment_confidence")
        batch.drop_column("sentiment")
    sa.Enum(name="call_sentiment").drop(op.get_bind(), checkfirst=True)
