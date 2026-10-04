"""Gateway line rotation, dial strings, and campaign line rules (SQLite + fake 3CX only)."""
from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.auth import hash_password
from app.database import Base, get_db
from app.gateway_lines import (
    allocate_line, campaign_line_numbers, describe_lines, dial_destination, ensure_gateway_lines, free_lines,
)
from app.main import app
from app.models import Call, CallStatus, Campaign, GatewayLine, User
from app.threecx import ThreeCXTestCall


class GatewayLineRotationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        ensure_gateway_lines(self.db)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def campaign(self, lines, name="Lines"):
        campaign = Campaign(name=name, script="Approved gateway test script", gateway_lines_json=lines)
        self.db.add(campaign)
        self.db.flush()
        return campaign

    def take(self, campaign, phone="+971500000000"):
        """Allocate a line and hold it with an in-progress call, as the dispatcher does."""
        line = allocate_line(self.db, campaign)
        if line is None:
            return None
        self.db.add(Call(campaign_id=campaign.id, phone=phone, status=CallStatus.in_progress,
                         gateway_line=line.number, gateway_prefix=line.prefix))
        self.db.commit()
        return line.number

    def finish_all(self):
        for call in self.db.scalars(select(Call).where(Call.status == CallStatus.in_progress)):
            call.status = CallStatus.completed
            call.completed_at = datetime.now(timezone.utc)
        self.db.commit()

    def test_seeds_32_lines_with_8801_to_8832_prefixes(self):
        lines = self.db.scalars(select(GatewayLine).order_by(GatewayLine.number)).all()
        self.assertEqual(len(lines), 32)
        self.assertEqual((lines[0].prefix, lines[21].prefix, lines[31].prefix), ("8801", "8822", "8832"))

    def test_dial_destination_prefixes_the_number_exactly_as_uploaded(self):
        line = self.db.get(GatewayLine, 22)
        self.assertEqual(dial_destination("+971501234567", line), "8822+971501234567")
        self.assertEqual(dial_destination("+971501234567", None), "+971501234567")

    def test_campaign_without_lines_uses_the_standard_route(self):
        campaign = self.campaign([])
        self.assertEqual(campaign_line_numbers(campaign), [])
        self.assertIsNone(allocate_line(self.db, campaign))

    def test_rotates_through_all_lines_in_order_then_wraps(self):
        campaign = self.campaign(list(range(1, 33)))
        used = []
        for _ in range(34):
            used.append(self.take(campaign))
            self.finish_all()
        self.assertEqual(used, list(range(1, 33)) + [1, 2])

    def test_subset_rotates_only_among_selected_lines(self):
        campaign = self.campaign([1, 22, 23])
        used = []
        for _ in range(5):
            used.append(self.take(campaign))
            self.finish_all()
        self.assertEqual(used, [1, 22, 23, 1, 22])

    def test_busy_lines_are_skipped_and_none_when_all_busy(self):
        campaign = self.campaign([1, 22, 23])
        self.assertEqual([self.take(campaign) for _ in range(3)], [1, 22, 23])
        self.assertIsNone(allocate_line(self.db, campaign))
        self.assertEqual(free_lines(self.db, campaign), [])

    def test_line_busy_for_one_campaign_is_skipped_by_another(self):
        first, second = self.campaign([5, 6], "First"), self.campaign([5, 6], "Second")
        self.assertEqual(self.take(first), 5)
        self.assertEqual(self.take(second), 6)
        self.assertIsNone(allocate_line(self.db, first))

    def test_switched_off_line_is_never_used_and_all_off_never_falls_back(self):
        campaign = self.campaign([1, 2])
        self.db.get(GatewayLine, 1).enabled = False
        self.db.commit()
        self.assertEqual([self.take(campaign), self.take(campaign)], [2, None])
        self.finish_all()
        self.db.get(GatewayLine, 2).enabled = False
        self.db.commit()
        self.assertIsNone(allocate_line(self.db, campaign))
        # The selection is kept, so turning a line back on restores it.
        self.assertEqual(campaign_line_numbers(campaign), [1, 2])

    def test_database_rejects_two_live_calls_on_one_line(self):
        campaign = self.campaign([3])
        self.db.add_all([
            Call(campaign_id=campaign.id, phone="1", status=CallStatus.in_progress, gateway_line=3),
            Call(campaign_id=campaign.id, phone="2", status=CallStatus.in_progress, gateway_line=3),
        ])
        with self.assertRaises(IntegrityError):
            self.db.commit()

    def test_needs_attention_after_three_provider_failures(self):
        campaign = self.campaign([4])
        now = datetime.now(timezone.utc)
        for minutes in range(3):
            self.db.add(Call(campaign_id=campaign.id, phone="x", status=CallStatus.failed, gateway_line=4,
                             failure_category="provider_failure", started_at=now,
                             completed_at=now + timedelta(minutes=minutes)))
        self.db.commit()
        line = next(item for item in describe_lines(self.db, "UTC") if item["number"] == 4)
        self.assertTrue(line["needs_attention"])
        self.assertEqual((line["status"], line["calls_today"], line["failed_today"]), ("attention", 3, 3))
        # A later completed call clears the flag.
        self.db.add(Call(campaign_id=campaign.id, phone="x", status=CallStatus.completed, gateway_line=4,
                         started_at=now, completed_at=now + timedelta(minutes=5)))
        self.db.commit()
        line = next(item for item in describe_lines(self.db, "UTC") if item["number"] == 4)
        self.assertFalse(line["needs_attention"])
        self.assertEqual(line["answered_today"], 1)


class GatewayLineApiTests(unittest.TestCase):
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
            db.add(User(email="super@example.test", display_name="Supervisor", role="supervisor",
                        password_hash=hash_password("correct horse battery staple")))
            db.commit()
        self.login("owner@example.test")

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()
        os.unlink(self.database_file.name)

    def login(self, email):
        self.client.cookies.clear()
        self.client.headers.pop("X-CSRF-Token", None)
        login = self.client.post("/auth/login", json={"email": email, "password": "correct horse battery staple"})
        self.assertEqual(login.status_code, 200, login.text)
        self.client.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})

    def switch(self, number, enabled, label=None):
        response = self.client.put("/gateway-lines", json=[{"number": number, "enabled": enabled, "label": label}])
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_lines_list_requires_a_session_and_updates_are_owner_only_with_csrf(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/gateway-lines").status_code, 401)
        self.login("super@example.test")
        self.assertEqual(self.client.get("/gateway-lines").status_code, 200)
        self.assertEqual(self.client.put("/gateway-lines", json=[{"number": 1, "enabled": False}]).status_code, 403)
        self.login("owner@example.test")
        token = self.client.headers.pop("X-CSRF-Token")
        self.assertEqual(self.client.put("/gateway-lines", json=[{"number": 1, "enabled": False}]).status_code, 403)
        self.client.headers["X-CSRF-Token"] = token

    def test_switching_lines_off_and_labelling_is_reflected_in_the_list(self):
        lines = self.switch(7, False, "  Spare SIM ")
        line7 = next(item for item in lines if item["number"] == 7)
        self.assertEqual((line7["enabled"], line7["label"], line7["status"]), (False, "Spare SIM", "off"))
        listed = self.client.get("/gateway-lines").json()
        self.assertEqual(len(listed), 32)
        self.assertEqual([item["number"] for item in listed if item["enabled"]], [n for n in range(1, 33) if n != 7])

    def test_campaign_lines_must_be_switched_on_and_change_only_while_paused(self):
        self.switch(7, False)
        rejected = self.client.post("/campaigns", json={"name": "Has off line", "script": "Approved gateway script", "gateway_lines": [1, 7]})
        self.assertEqual(rejected.status_code, 422)
        self.assertIn("Line 7 is switched off", rejected.text)
        out_of_range = self.client.post("/campaigns", json={"name": "Bad line", "script": "Approved gateway script", "gateway_lines": [33]})
        self.assertEqual(out_of_range.status_code, 422)

        created = self.client.post("/campaigns", json={"name": "Campaign 001", "script": "Approved gateway script", "gateway_lines": [23, 1, 22, 1]})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["gateway_lines"], [1, 22, 23])
        campaign_id = created.json()["id"]

        self.assertEqual(self.client.put(f"/campaigns/{campaign_id}/gateway-lines", json={"gateway_lines": [7]}).status_code, 422)
        changed = self.client.put(f"/campaigns/{campaign_id}/gateway-lines", json={"gateway_lines": [2, 3]})
        self.assertEqual(changed.json()["gateway_lines"], [2, 3])
        self.client.post(f"/campaigns/{campaign_id}/launch")
        self.assertEqual(self.client.put(f"/campaigns/{campaign_id}/gateway-lines", json={"gateway_lines": [4]}).status_code, 409)

    def test_campaign_without_lines_keeps_the_standard_route(self):
        created = self.client.post("/campaigns", json={"name": "Standard", "script": "Approved gateway script"})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["gateway_lines"], [])

    def live_campaign(self, media_dir, lines, name="Gateway live call"):
        uploaded = self.client.post("/audio-assets", files={"file": (f"{name}.mp3", name.encode(), "audio/mpeg")})
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        playbook = self.client.post("/playbooks", json={
            "name": f"{name} playbook", "script": "Ask a discovery question before presenting the offer.",
            "opening_audio_id": uploaded.json()["id"], "approve": True,
        })
        self.assertEqual(playbook.status_code, 201, playbook.text)
        campaign = self.client.post("/campaigns", json={
            "name": name, "playbook_version_id": playbook.json()["current_version_id"], "gateway_lines": lines,
        })
        self.assertEqual(campaign.status_code, 201, campaign.text)
        campaign_id = campaign.json()["id"]
        self.client.post(f"/campaigns/{campaign_id}/contacts", json=[
            {"phone": "+971501234567", "name": "First"}, {"phone": "+971507654321", "name": "Second"},
        ])
        self.client.post(f"/campaigns/{campaign_id}/launch")
        return campaign_id

    def enable_live_calling(self):
        self.client.put("/settings", json={
            "default_timezone": "Asia/Dubai", "default_calling_window_json": {},
            "max_concurrent_calls": 2, "recording_retention_days": 30,
            "test_call_enabled": False, "live_campaign_calling_enabled": True,
        })

    def test_live_call_dials_through_the_line_prefix_and_records_the_line(self):
        from app.config import get_settings
        with tempfile.TemporaryDirectory() as media_dir, patch.dict(os.environ, {
            "CALL_PROVIDER": "threecx", "AUDIO_STORAGE_DIR": media_dir,
        }, clear=False):
            get_settings.cache_clear()
            campaign_id = self.live_campaign(media_dir, [22, 23])
            self.enable_live_calling()
            with patch("app.dispatcher.ThreeCXClient") as client_class:
                client = MagicMock()
                client.start_test_call.side_effect = [
                    ThreeCXTestCall(participant_id=pid, destination="x", initial_status="ok", initial_reason="ok")
                    for pid in (88, 89)
                ]
                client_class.return_value = client
                first = self.client.post(f"/campaigns/{campaign_id}/place-next-call")
                second = self.client.post(f"/campaigns/{campaign_id}/place-next-call")
            get_settings.cache_clear()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual([c.args[0] for c in client.start_test_call.call_args_list],
                         ["8822+971501234567", "8823+971507654321"])
        self.assertEqual((first.json()["gateway_line"], first.json()["gateway_prefix"]), (22, "8822"))
        self.assertEqual(second.json()["gateway_line"], 23)
        log = self.client.get(f"/calls/{first.json()['id']}").json()
        self.assertEqual(log["gateway_line"], 22)

    def test_all_lines_busy_keeps_the_call_queued_and_frees_the_slot(self):
        from app.config import get_settings
        from app.dispatcher import DispatchError, _is_dispatchable, place_next_call
        with tempfile.TemporaryDirectory() as media_dir, patch.dict(os.environ, {
            "CALL_PROVIDER": "threecx", "AUDIO_STORAGE_DIR": media_dir,
        }, clear=False):
            get_settings.cache_clear()
            busy_id = self.live_campaign(media_dir, [5], "Busy line")
            other_id = self.live_campaign(media_dir, [6], "Other line")
            self.enable_live_calling()
            with self.session_factory() as db:
                db.add(Call(campaign_id=busy_id, phone="held", status=CallStatus.in_progress,
                            gateway_line=5, gateway_prefix="8805", started_at=datetime.now(timezone.utc)))
                db.commit()
                busy, other = db.get(Campaign, busy_id), db.get(Campaign, other_id)
                settings = get_settings()
                self.assertFalse(_is_dispatchable(busy, db, settings))
                self.assertTrue(_is_dispatchable(other, db, settings))
                with patch("app.dispatcher.ThreeCXClient") as client_class:
                    with self.assertRaises(DispatchError):
                        place_next_call(busy_id, db)
                    client_class.assert_not_called()
                queued = db.scalars(select(Call).where(Call.campaign_id == busy_id, Call.status == CallStatus.queued)).all()
                self.assertEqual(len(queued), 2)
            status = self.client.get("/campaigns/live-status").json()
            get_settings.cache_clear()
        busy_status = next(item for item in status["active_campaigns"] if item["id"] == busy_id)
        self.assertEqual(busy_status["waiting_reason"], "Waiting for a free line")
        self.assertEqual(busy_status["live_calls"][0]["gateway_line"], 5)
        self.assertEqual(busy_status["gateway_lines"], [5])

    def test_live_status_reports_when_every_campaign_line_is_switched_off(self):
        created = self.client.post("/campaigns", json={"name": "Off lines", "script": "Approved gateway script", "gateway_lines": [9]})
        campaign_id = created.json()["id"]
        self.client.post(f"/campaigns/{campaign_id}/launch")
        self.switch(9, False)
        status = self.client.get("/campaigns/live-status").json()
        entry = next(item for item in status["active_campaigns"] if item["id"] == campaign_id)
        self.assertEqual(entry["waiting_reason"], "None of this campaign's lines are switched on")
        self.assertEqual(entry["lines_available"], 0)


if __name__ == "__main__":
    unittest.main()
