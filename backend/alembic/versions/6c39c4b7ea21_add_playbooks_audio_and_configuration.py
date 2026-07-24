"""add playbooks, local audio assets and call configuration snapshots

Revision ID: 6c39c4b7ea21
Revises: 211f0634fa8e
"""
from alembic import op
import sqlalchemy as sa

revision = "6c39c4b7ea21"
down_revision = "211f0634fa8e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    audio_status = sa.Enum("ready", "deleted", name="audio_asset_status")
    playbook_status = sa.Enum("draft", "approved", "retired", name="playbook_status")
    version_status = sa.Enum("draft", "approved", "retired", name="playbook_version_status")
    bind = op.get_bind()
    audio_status.create(bind, checkfirst=True)
    playbook_status.create(bind, checkfirst=True)
    version_status.create(bind, checkfirst=True)
    op.create_table(
        "global_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("default_timezone", sa.String(64), nullable=False),
        sa.Column("default_calling_window_json", sa.JSON(), nullable=False),
        sa.Column("max_concurrent_calls", sa.Integer(), nullable=False),
        sa.Column("recording_retention_days", sa.Integer(), nullable=False),
        sa.Column("test_call_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_table(
        "audio_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False, unique=True),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(128), nullable=False, unique=True),
        sa.Column("status", audio_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_table(
        "playbooks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(150), nullable=False, unique=True),
        sa.Column("status", playbook_status, nullable=False),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_table(
        "playbook_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("playbook_id", sa.Integer(), sa.ForeignKey("playbooks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("script", sa.Text(), nullable=False),
        sa.Column("opening_audio_id", sa.Integer(), sa.ForeignKey("audio_assets.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("recording_enabled", sa.Boolean(), nullable=False),
        sa.Column("status", version_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("playbook_id", "version", name="uq_playbook_version"),
    )
    op.create_index("ix_playbook_versions_playbook_id", "playbook_versions", ["playbook_id"])
    op.create_index("ix_playbook_versions_opening_audio_id", "playbook_versions", ["opening_audio_id"])
    # SQLite cannot add a foreign key with ALTER TABLE; batch mode performs
    # the safe copy-and-swap there and emits a normal ALTER on PostgreSQL.
    with op.batch_alter_table("playbooks") as batch:
        batch.create_foreign_key("fk_playbooks_current_version", "playbook_versions", ["current_version_id"], ["id"])
    with op.batch_alter_table("campaigns") as batch:
        batch.add_column(sa.Column("playbook_version_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("caller_id_override", sa.String(80), nullable=True))
        batch.add_column(sa.Column("max_concurrent_calls_override", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_campaigns_playbook_version", "playbook_versions", ["playbook_version_id"], ["id"], ondelete="RESTRICT")
        batch.create_index("ix_campaigns_playbook_version_id", ["playbook_version_id"])
    with op.batch_alter_table("calls") as batch:
        batch.add_column(sa.Column("configuration_snapshot_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    # Existing deployments retain legacy campaigns and start with a usable default row.
    op.execute("INSERT INTO global_settings (id, default_timezone, default_calling_window_json, max_concurrent_calls, recording_retention_days, test_call_enabled) VALUES (1, 'Asia/Dubai', '{}', 1, 30, false)")


def downgrade() -> None:
    with op.batch_alter_table("calls") as batch:
        batch.drop_column("configuration_snapshot_json")
    with op.batch_alter_table("campaigns") as batch:
        batch.drop_index("ix_campaigns_playbook_version_id")
        batch.drop_constraint("fk_campaigns_playbook_version", type_="foreignkey")
        batch.drop_column("max_concurrent_calls_override")
        batch.drop_column("caller_id_override")
        batch.drop_column("playbook_version_id")
    with op.batch_alter_table("playbooks") as batch:
        batch.drop_constraint("fk_playbooks_current_version", type_="foreignkey")
    op.drop_index("ix_playbook_versions_opening_audio_id", table_name="playbook_versions")
    op.drop_index("ix_playbook_versions_playbook_id", table_name="playbook_versions")
    op.drop_table("playbook_versions")
    op.drop_table("playbooks")
    op.drop_table("audio_assets")
    op.drop_table("global_settings")
    bind = op.get_bind()
    sa.Enum(name="playbook_version_status").drop(bind, checkfirst=True)
    sa.Enum(name="playbook_status").drop(bind, checkfirst=True)
    sa.Enum(name="audio_asset_status").drop(bind, checkfirst=True)
