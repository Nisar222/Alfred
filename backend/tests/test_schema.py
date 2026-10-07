import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, inspect

from app.database import Base
import app.models  # noqa: F401
from app.models import CallStatus
from app.schemas import CallOut


class SchemaTests(unittest.TestCase):
    def test_core_schema_can_be_created(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        tables = set(inspect(engine).get_table_names())
        self.assertTrue({"campaigns", "prospects", "calls", "transcripts", "recordings", "call_metrics", "prompts", "users", "audit_events", "global_settings", "audio_assets", "playbooks", "playbook_versions"} <= tables)

    def test_calls_table_has_dtmf_events_column(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        columns = {column["name"] for column in inspect(engine).get_columns("calls")}
        self.assertIn("dtmf_events_json", columns)

    def test_gateway_line_schema(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        inspector = inspect(engine)
        self.assertIn("gateway_lines", inspector.get_table_names())
        call_columns = {column["name"] for column in inspector.get_columns("calls")}
        self.assertTrue({"gateway_line", "gateway_prefix"} <= call_columns)
        self.assertIn("gateway_lines_json", {column["name"] for column in inspector.get_columns("campaigns")})
        indexes = {index["name"]: index for index in inspector.get_indexes("calls")}
        self.assertTrue(indexes["uq_calls_active_gateway_line"]["unique"])

    def test_gateway_line_migration_follows_dtmf_events(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / "alembic" / "versions" / "a3e6c0d54f21_add_gateway_lines.py").read_text()
        self.assertIn('down_revision = "d7f3a1c95b28"', source)
        self.assertIn('f"88{number:02d}"', source)
        self.assertIn("range(1, 33)", source)

    def test_sim_status_schema_and_migration(self):
        from pathlib import Path
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        inspector = inspect(engine)
        line_columns = {column["name"] for column in inspector.get_columns("gateway_lines")}
        self.assertTrue({"sim_registration", "sim_signal", "sim_checked_at"} <= line_columns)
        settings_columns = {column["name"] for column in inspector.get_columns("global_settings")}
        self.assertTrue({"sim_check_last_attempt_at", "sim_check_last_success_at", "sim_check_error"} <= settings_columns)
        source = (Path(__file__).resolve().parents[1] / "alembic" / "versions" / "b7c41e9d2a60_add_gateway_sim_status.py").read_text()
        self.assertIn('down_revision = "a3e6c0d54f21"', source)

    def test_callout_exposes_dtmf_events_history(self):
        call = SimpleNamespace(
            id=1, campaign_id=1, phone="+15551234567", prospect_name=None,
            status=CallStatus.queued, outcome=None, duration_seconds=None,
            created_at=datetime.now(timezone.utc),
            dtmf_digit="1",
            dtmf_events_json=[
                {"digit": "1", "at": "2026-01-01T00:00:00+00:00"},
                {"digit": "2", "at": "2026-01-01T00:00:01+00:00"},
            ],
        )
        out = CallOut.model_validate(call)
        self.assertEqual([event["digit"] for event in out.dtmf_events], ["1", "2"])
