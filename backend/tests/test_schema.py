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
        self.assertEqual(out.model_dump()["dtmf_events"][0]["digit"], "1")
