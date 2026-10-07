"""Rest (cool-off) period between consecutive calls on a gateway line (SQLite + simulator only)."""
from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.auth import hash_password
from app.database import Base, get_db
from app.gateway_lines import (
    allocate_line, describe_lines, effective_cooloff_seconds, ensure_gateway_lines, free_lines,
    next_line_free_at, resting_line_numbers,
)
from app.main import app
from app.models import Call, CallStatus, Campaign, GlobalSettings, User


class LineCooloffTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        ensure_gateway_lines(self.db)
        self.settings = GlobalSettings(id=1, line_cooloff_seconds=60)
        self.db.add(self.settings)
        self.campaign = Campaign(name="Rest", script="Approved gateway script", gateway_lines_json=[1, 2, 3])
        self.db.add(self.campaign)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def ended(self, line, seconds_ago, status=CallStatus.completed, category=None):
        now = datetime.now(timezone.utc)
        self.db.add(Call(campaign_id=self.campaign.id, phone="x", status=status, gateway_line=line,
                         gateway_prefix=f"88{line:02d}", failure_category=category,
                         started_at=now - timedelta(seconds=seconds_ago + 30),
                         completed_at=now - timedelta(seconds=seconds_ago)))
        self.db.commit()

    def numbers(self, lines):
        return [line.number for line in lines]

    def test_line_rests_after_a_call_while_other_lines_keep_dialling(self):
        self.ended(1, seconds_ago=10)
        self.assertEqual(self.numbers(free_lines(self.db, self.campaign)), [2, 3])
        self.assertEqual(allocate_line(self.db, self.campaign).number, 2)
        self.db.rollback()
        rest = resting_line_numbers(self.db, 60)
        self.assertEqual(list(rest), [1])
        self.assertAlmostEqual((rest[1] - datetime.now(timezone.utc)).total_seconds(), 50, delta=3)

    def test_line_returns_once_rested_and_rotation_is_kept(self):
        self.ended(1, seconds_ago=61)
        self.assertEqual(self.numbers(free_lines(self.db, self.campaign)), [1, 2, 3])
        self.assertEqual(allocate_line(self.db, self.campaign).number, 1)

    def test_failed_and_unanswered_attempts_also_start_the_rest(self):
        self.ended(1, seconds_ago=5, status=CallStatus.failed, category="no_answer")
        self.ended(2, seconds_ago=5, status=CallStatus.failed, category="provider_failure")
        self.assertEqual(self.numbers(free_lines(self.db, self.campaign)), [3])

    def test_campaign_override_beats_settings_and_zero_turns_it_off(self):
        self.ended(1, seconds_ago=30)
        self.campaign.line_cooloff_seconds_override = 20
        self.db.commit()
        self.assertEqual(effective_cooloff_seconds(self.db, self.campaign), 20)
        self.assertEqual(self.numbers(free_lines(self.db, self.campaign)), [1, 2, 3])
        self.campaign.line_cooloff_seconds_override = 120
        self.db.commit()
        self.assertEqual(self.numbers(free_lines(self.db, self.campaign)), [2, 3])
        self.campaign.line_cooloff_seconds_override = 0
        self.db.commit()
        self.assertEqual(self.numbers(free_lines(self.db, self.campaign)), [1, 2, 3])

    def test_no_cooloff_behaves_exactly_as_before(self):
        self.settings.line_cooloff_seconds = 0
        self.db.commit()
        self.ended(1, seconds_ago=0)
        self.assertEqual(resting_line_numbers(self.db, 0), {})
        self.assertEqual(allocate_line(self.db, self.campaign).number, 1)

    def test_all_lines_resting_returns_nothing_and_reports_when_next_is_free(self):
        for line, ago in ((1, 50), (2, 10), (3, 30)):
            self.ended(line, seconds_ago=ago)
        self.assertEqual(free_lines(self.db, self.campaign), [])
        self.assertIsNone(allocate_line(self.db, self.campaign))
        free_at = next_line_free_at(self.db, self.campaign)
        self.assertAlmostEqual((free_at - datetime.now(timezone.utc)).total_seconds(), 10, delta=3)

    def test_settings_table_shows_resting_lines(self):
        self.ended(2, seconds_ago=15)
        rows = {row["number"]: row for row in describe_lines(self.db, "UTC")}
        self.assertEqual(rows[2]["status"], "resting")
        self.assertIsNotNone(rows[2]["resting_until"])
        self.assertEqual((rows[1]["status"], rows[1]["resting_until"]), ("ready", None))


class LineCooloffApiTests(unittest.TestCase):
    def setUp(self):
        self.database_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.database_file.close()
        self.engine = create_engine(f"sqlite:///{self.database_file.name}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

        def override_get_db():
            session = self.session_factory()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app, base_url="https://testserver")
        with self.session_factory() as db:
            db.add(User(email="owner@example.test", display_name="Owner", role="owner",
                        password_hash=hash_password("correct horse battery staple")))
            db.commit()
        login = self.client.post("/auth/login", json={"email": "owner@example.test", "password": "correct horse battery staple"})
        self.client.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()
        os.unlink(self.database_file.name)

    def save_settings(self, cooloff, live=False):
        return self.client.put("/settings", json={
            "default_timezone": "Asia/Dubai", "default_calling_window_json": {}, "max_concurrent_calls": 2,
            "recording_retention_days": 30, "test_call_enabled": False, "live_campaign_calling_enabled": live,
            "line_cooloff_seconds": cooloff,
        })

    def test_settings_round_trip_and_range(self):
        self.assertEqual(self.save_settings(90).json()["line_cooloff_seconds"], 90)
        self.assertEqual(self.client.get("/settings").json()["line_cooloff_seconds"], 90)
        self.assertEqual(self.save_settings(-1).status_code, 422)
        self.assertEqual(self.save_settings(86401).status_code, 422)

    def test_campaign_override_set_on_create_and_changed_only_while_paused(self):
        created = self.client.post("/campaigns", json={
            "name": "Rested", "script": "Approved gateway script", "gateway_lines": [1], "line_cooloff_seconds_override": 120,
        })
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["line_cooloff_seconds_override"], 120)
        campaign_id = created.json()["id"]
        changed = self.client.put(f"/campaigns/{campaign_id}/gateway-lines", json={"gateway_lines": [1, 2], "line_cooloff_seconds_override": None})
        self.assertEqual(changed.json()["line_cooloff_seconds_override"], None)
        kept = self.client.put(f"/campaigns/{campaign_id}/gateway-lines", json={"gateway_lines": [2]})
        self.assertEqual(kept.json()["line_cooloff_seconds_override"], None)
        self.client.put(f"/campaigns/{campaign_id}/gateway-lines", json={"gateway_lines": [2], "line_cooloff_seconds_override": 30})
        self.client.post(f"/campaigns/{campaign_id}/launch")
        blocked = self.client.put(f"/campaigns/{campaign_id}/gateway-lines", json={"gateway_lines": [2], "line_cooloff_seconds_override": 5})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(self.client.get("/campaigns").json()[0]["line_cooloff_seconds_override"], 30)

    def test_resting_campaign_waits_without_a_slot_and_others_still_dial(self):
        from app.config import get_settings
        from app.dispatcher import DispatchError, _is_dispatchable, place_next_call
        self.save_settings(60, live=True)
        with tempfile.TemporaryDirectory() as media_dir, patch.dict(os.environ, {
            "CALL_PROVIDER": "threecx", "AUDIO_STORAGE_DIR": media_dir,
        }):
            get_settings.cache_clear()
            ids = {}
            for name, lines in (("Resting", [5]), ("Fresh", [6]), ("Standard", [])):
                audio = self.client.post("/audio-assets", files={"file": (f"{name}.mp3", name.encode(), "audio/mpeg")}).json()
                playbook = self.client.post("/playbooks", json={
                    "name": f"{name} playbook", "script": "Ask a discovery question before presenting the offer.",
                    "opening_audio_id": audio["id"], "approve": True,
                }).json()
                ids[name] = self.client.post("/campaigns", json={
                    "name": name, "playbook_version_id": playbook["current_version_id"], "gateway_lines": lines,
                }).json()["id"]
                self.client.post(f"/campaigns/{ids[name]}/contacts", json=[{"phone": "+971500000009"}])
                self.client.post(f"/campaigns/{ids[name]}/launch")
            with self.session_factory() as db:
                now = datetime.now(timezone.utc)
                db.add(Call(campaign_id=ids["Resting"], phone="earlier", status=CallStatus.completed, gateway_line=5,
                            gateway_prefix="8805", started_at=now - timedelta(seconds=40), completed_at=now - timedelta(seconds=20)))
                db.commit()
                settings = get_settings()
                self.assertFalse(_is_dispatchable(db.get(Campaign, ids["Resting"]), db, settings))
                self.assertTrue(_is_dispatchable(db.get(Campaign, ids["Fresh"]), db, settings))
                self.assertTrue(_is_dispatchable(db.get(Campaign, ids["Standard"]), db, settings))
                with patch("app.dispatcher.ThreeCXClient") as client_class:
                    with self.assertRaises(DispatchError) as raised:
                        place_next_call(ids["Resting"], db)
                    client_class.assert_not_called()
                self.assertIn("resting", str(raised.exception))
                queued = db.scalars(select(Call).where(Call.campaign_id == ids["Resting"], Call.phone == "+971500000009")).one()
                self.assertEqual(queued.status, CallStatus.queued)
            status = self.client.get("/campaigns/live-status").json()
            get_settings.cache_clear()
        by_id = {item["id"]: item for item in status["active_campaigns"]}
        self.assertEqual(by_id[ids["Resting"]]["waiting_reason"], "Lines resting between calls")
        self.assertIsNotNone(by_id[ids["Resting"]]["next_line_free_at"])
        self.assertIsNone(by_id[ids["Fresh"]]["waiting_reason"])
        self.assertIsNone(by_id[ids["Standard"]]["waiting_reason"])


if __name__ == "__main__":
    unittest.main()
