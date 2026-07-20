import unittest

from sqlalchemy import create_engine, inspect

from app.database import Base
import app.models  # noqa: F401


class SchemaTests(unittest.TestCase):
    def test_core_schema_can_be_created(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        tables = set(inspect(engine).get_table_names())
        self.assertTrue({"campaigns", "prospects", "calls", "transcripts", "recordings", "call_metrics", "prompts", "users", "audit_events"} <= tables)
