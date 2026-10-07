"""Dinstar SIM status: client parsing, fail-closed gating, and API (fake gateway only)."""
from datetime import datetime, timedelta, timezone
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.auth import hash_password
from app.config import Settings
from app.database import Base, get_db
from app.dinstar import DinstarClient, DinstarError, SimStatus
from app.gateway_lines import (
    GATEWAY_UNREACHABLE, allocate_line, check_sims, describe_lines, ensure_gateway_lines, free_lines,
    record_sim_check, sim_check_summary, sim_gate,
)
from app.main import app
from app.models import Call, CallStatus, Campaign, GatewayLine, GlobalSettings, User

SECRET = "not-a-real-gateway-password"
CONFIGURED = Settings(dinstar_base_url="https://gateway.test", dinstar_api_username="alfred",
                      dinstar_api_password=SECRET, dinstar_verify_tls=False)
NOT_CONFIGURED = Settings()


def gateway(info=None, status=200, payload=None, digest=True):
    """A fake Dinstar API that issues a digest challenge first, like the real gateway."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if digest and "authorization" not in request.headers:
            return httpx.Response(401, headers={"WWW-Authenticate": 'Digest realm="gw", nonce="abc", qop="auth"'})
        body = payload if payload is not None else {"error_code": 200, "sn": "test-sn", "info": info or []}
        return httpx.Response(status, content=json.dumps(body).encode(), headers={"Content-Type": "application/json"})

    return httpx.MockTransport(handler), seen


def all_ports(unregistered=(), missing=()):
    return [{"port": n - 1, "reg": "UNREGISTER" if n in unregistered else "REGISTER_OK", "signal": 0 if n in unregistered else 18}
            for n in range(1, 33) if n not in missing]


class DinstarClientTests(unittest.TestCase):
    def test_reads_every_port_in_one_request_and_maps_port_0_to_line_1(self):
        transport, seen = gateway(all_ports(unregistered={1}))
        with DinstarClient(CONFIGURED, transport=transport) as client:
            statuses = client.sim_statuses()
        self.assertEqual(statuses[1], SimStatus(line=1, registration="UNREGISTER", signal=0))
        self.assertEqual(statuses[2], SimStatus(line=2, registration="REGISTER_OK", signal=18))
        self.assertEqual(len(statuses), 32)
        final = seen[-1]
        self.assertEqual(final.url.path, "/api/get_port_info")
        self.assertEqual(final.url.params["port"], ",".join(str(n) for n in range(32)))
        self.assertEqual(final.url.params["info_type"], "reg,signal")
        self.assertTrue(final.headers["authorization"].startswith("Digest "))
        self.assertNotIn(SECRET, final.headers["authorization"])

    def test_errors_are_readable_and_never_include_the_password(self):
        cases = [
            (gateway(status=401, digest=False)[0], "rejected Alfred's API login"),
            (gateway(status=500)[0], "HTTP 500"),
            (gateway(payload={"error_code": 400})[0], "code 400"),
            (gateway(payload={"error_code": 200, "info": []})[0], "did not report any SIM ports"),
            (httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ConnectTimeout("timed out"))), "did not answer in time"),
            (httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused"))), "could not connect"),
        ]
        for transport, expected in cases:
            with self.subTest(expected=expected), DinstarClient(CONFIGURED, transport=transport) as client:
                with self.assertRaises(DinstarError) as raised:
                    client.sim_statuses()
                self.assertIn(expected, str(raised.exception))
                self.assertNotIn(SECRET, str(raised.exception))

    def test_refuses_to_run_without_settings(self):
        with self.assertRaises(DinstarError):
            DinstarClient(NOT_CONFIGURED)


class SimGatingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        ensure_gateway_lines(self.db)
        self.db.add(GlobalSettings(id=1))
        self.campaign = Campaign(name="SIMs", script="Approved gateway script", gateway_lines_json=[1, 2, 3])
        self.db.add(self.campaign)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def check(self, info=None, **kwargs):
        transport, _ = gateway(info if info is not None else all_ports(), **kwargs)
        return check_sims(self.db, CONFIGURED, transport=transport)

    def test_checks_off_when_not_configured_keeps_todays_behaviour(self):
        self.assertEqual(sim_gate(self.db, NOT_CONFIGURED), "off")
        self.assertEqual(allocate_line(self.db, self.campaign, NOT_CONFIGURED).number, 1)
        self.assertEqual(sim_check_summary(self.db, NOT_CONFIGURED)["enabled"], False)

    def test_unregistered_sim_is_skipped_and_returns_when_registered(self):
        summary = self.check(all_ports(unregistered={1}))
        self.assertEqual((summary["state"], summary["error"], summary["ready_lines"]), ("ok", None, 31))
        self.assertEqual([line.number for line in free_lines(self.db, self.campaign, CONFIGURED)], [2, 3])
        self.assertEqual(allocate_line(self.db, self.campaign, CONFIGURED).number, 2)
        self.db.rollback()
        self.check(all_ports())
        self.assertEqual([line.number for line in free_lines(self.db, self.campaign, CONFIGURED)], [1, 2, 3])

    def test_port_missing_from_the_reply_is_not_ready(self):
        self.check(all_ports(missing={2}))
        self.assertEqual([line.number for line in free_lines(self.db, self.campaign, CONFIGURED)], [1, 3])
        line2 = next(item for item in describe_lines(self.db, "UTC", CONFIGURED) if item["number"] == 2)
        self.assertEqual((line2["sim_status"], line2["status"]), ("not_ready", "sim_not_ready"))

    def test_unreachable_gateway_pauses_every_gateway_line_and_reports_why(self):
        self.check(all_ports())
        summary = self.check(status=503)
        self.assertEqual(summary["state"], "error")
        self.assertIn("HTTP 503", summary["error"])
        self.assertEqual(sim_gate(self.db, CONFIGURED), "error")
        self.assertIsNone(allocate_line(self.db, self.campaign, CONFIGURED))
        self.assertEqual(free_lines(self.db, self.campaign, CONFIGURED), [])
        line1 = next(item for item in describe_lines(self.db, "UTC", CONFIGURED) if item["number"] == 1)
        # Last-known data stays visible, but the line shows as unknown.
        self.assertEqual((line1["sim_status"], line1["status"], line1["sim_registration"]),
                         ("unknown", "sim_unknown", "REGISTER_OK"))
        self.check(all_ports())
        self.assertEqual(sim_gate(self.db, CONFIGURED), "ok")

    def test_never_checked_fails_closed_but_an_old_successful_check_still_counts(self):
        self.assertEqual(sim_gate(self.db, CONFIGURED), "error")
        self.assertIn("not been checked", sim_check_summary(self.db, CONFIGURED)["error"])
        self.assertIsNone(allocate_line(self.db, self.campaign, CONFIGURED))
        self.check(all_ports())
        # Checks are on demand, so a result from hours ago stays in force until the next check.
        settings_row = self.db.get(GlobalSettings, 1)
        settings_row.sim_check_last_success_at = datetime.now(timezone.utc) - timedelta(hours=6)
        self.db.commit()
        self.assertEqual(sim_gate(self.db, CONFIGURED), "ok")
        self.assertEqual(allocate_line(self.db, self.campaign, CONFIGURED).number, 1)

    def test_switched_off_line_with_ready_sim_is_still_not_used(self):
        self.check(all_ports())
        self.db.get(GatewayLine, 1).enabled = False
        self.db.commit()
        self.assertEqual([line.number for line in free_lines(self.db, self.campaign, CONFIGURED)], [2, 3])


class SimStatusApiTests(unittest.TestCase):
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
            for email, role in (("owner@example.test", "owner"), ("super@example.test", "supervisor")):
                db.add(User(email=email, display_name=role, role=role,
                            password_hash=hash_password("correct horse battery staple")))
            db.commit()
        self.login("owner@example.test")
        from app.config import get_settings
        self.env = patch.dict(os.environ, {
            "DINSTAR_BASE_URL": "https://gateway.test", "DINSTAR_API_USERNAME": "alfred",
            "DINSTAR_API_PASSWORD": SECRET, "DINSTAR_VERIFY_TLS": "false",
        })
        self.env.start()
        get_settings.cache_clear()

    def tearDown(self):
        from app.config import get_settings
        self.env.stop()
        get_settings.cache_clear()
        app.dependency_overrides.clear()
        self.engine.dispose()
        os.unlink(self.database_file.name)

    def login(self, email):
        self.client.cookies.clear()
        self.client.headers.pop("X-CSRF-Token", None)
        login = self.client.post("/auth/login", json={"email": email, "password": "correct horse battery staple"})
        self.assertEqual(login.status_code, 200, login.text)
        self.client.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})

    def fake_gateway(self, **kwargs):
        transport, _ = gateway(all_ports(unregistered={1}), **kwargs)
        original = DinstarClient.__init__

        def init(client_self, settings, transport=None, _fake=transport):
            original(client_self, settings, transport=_fake)
        return patch.object(DinstarClient, "__init__", init)

    def test_check_now_needs_owner_or_supervisor_with_csrf_and_reports_lines(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/gateway-lines/sim-check").status_code, 401)
        self.assertEqual(self.client.post("/gateway-lines/check-sims").status_code, 401)
        self.login("super@example.test")
        self.assertEqual(self.client.get("/gateway-lines/sim-check").status_code, 200)
        with self.fake_gateway():
            self.assertEqual(self.client.post("/gateway-lines/check-sims").status_code, 200)
        self.login("owner@example.test")
        token = self.client.headers.pop("X-CSRF-Token")
        self.assertEqual(self.client.post("/gateway-lines/check-sims").status_code, 403)
        self.client.headers["X-CSRF-Token"] = token
        with self.fake_gateway():
            checked = self.client.post("/gateway-lines/check-sims")
        self.assertEqual(checked.status_code, 200, checked.text)
        self.assertEqual((checked.json()["state"], checked.json()["ready_lines"]), ("ok", 31))
        self.assertNotIn(SECRET, checked.text)
        lines = {item["number"]: item for item in self.client.get("/gateway-lines").json()}
        self.assertEqual((lines[1]["sim_status"], lines[1]["sim_registration"], lines[1]["sim_signal"]),
                         ("not_ready", "UNREGISTER", 0))
        self.assertEqual((lines[2]["sim_status"], lines[2]["sim_signal"]), ("ready", 18))

    def test_starting_a_gateway_campaign_checks_sims_once_and_standard_route_does_not(self):
        calls = []
        original = DinstarClient.sim_statuses

        def counting(client_self, *args, **kwargs):
            calls.append(1)
            return original(client_self, *args, **kwargs)

        gateway_id = self.client.post("/campaigns", json={"name": "Gw", "script": "Approved gateway script", "gateway_lines": [2]}).json()["id"]
        standard_id = self.client.post("/campaigns", json={"name": "Std", "script": "Approved gateway script"}).json()["id"]
        with self.fake_gateway(), patch.object(DinstarClient, "sim_statuses", counting):
            self.assertEqual(self.client.post(f"/campaigns/{standard_id}/launch").status_code, 200)
            self.assertEqual(len(calls), 0)
            self.assertEqual(self.client.post(f"/campaigns/{gateway_id}/launch").status_code, 200)
            self.assertEqual(len(calls), 1)
        self.assertEqual(self.client.get("/gateway-lines/sim-check").json()["state"], "ok")

    def test_nothing_polls_the_gateway_in_the_background(self):
        import app.main as main_module
        self.assertFalse(hasattr(main_module, "SimStatusSync"))
        with self.assertRaises(ImportError):
            __import__("app.sim_sync")

    def test_live_status_explains_paused_lines_and_standard_route_is_unaffected(self):
        gateway_campaign = self.client.post("/campaigns", json={"name": "Gateway", "script": "Approved gateway script", "gateway_lines": [1]}).json()["id"]
        standard = self.client.post("/campaigns", json={"name": "Standard", "script": "Approved gateway script"}).json()["id"]
        for campaign_id in (gateway_campaign, standard):
            self.client.post(f"/campaigns/{campaign_id}/contacts", json=[{"phone": "+971500000001"}])
            self.client.post(f"/campaigns/{campaign_id}/launch")

        def reasons():
            status = self.client.get("/campaigns/live-status").json()
            return {item["id"]: item["waiting_reason"] for item in status["active_campaigns"]}

        with self.fake_gateway(status=503):
            failed = self.client.post("/gateway-lines/check-sims").json()
        self.assertIn("HTTP 503", failed["error"])
        self.assertEqual(reasons(), {gateway_campaign: GATEWAY_UNREACHABLE, standard: None})
        with self.fake_gateway():
            self.client.post("/gateway-lines/check-sims")
        self.assertEqual(reasons()[gateway_campaign], "None of this campaign's SIMs are ready")

        from app.dispatcher import _is_dispatchable
        from app.config import get_settings
        with self.session_factory() as db:
            self.assertFalse(_is_dispatchable(db.get(Campaign, gateway_campaign), db, get_settings()))

    def test_dispatcher_refuses_gateway_calls_while_the_gateway_is_unreachable(self):
        from app.config import get_settings
        from app.dispatcher import DispatchError, place_next_call
        with tempfile.TemporaryDirectory() as media_dir, patch.dict(os.environ, {
            "CALL_PROVIDER": "threecx", "AUDIO_STORAGE_DIR": media_dir,
        }):
            get_settings.cache_clear()
            audio = self.client.post("/audio-assets", files={"file": ("open.mp3", b"sim-audio", "audio/mpeg")}).json()
            playbook = self.client.post("/playbooks", json={
                "name": "SIM playbook", "script": "Ask a discovery question before presenting the offer.",
                "opening_audio_id": audio["id"], "approve": True,
            }).json()
            campaign_id = self.client.post("/campaigns", json={
                "name": "Gateway live", "playbook_version_id": playbook["current_version_id"], "gateway_lines": [2],
            }).json()["id"]
            self.client.post(f"/campaigns/{campaign_id}/contacts", json=[{"phone": "+971500000002"}])
            self.client.post(f"/campaigns/{campaign_id}/launch")
            self.client.put("/settings", json={
                "default_timezone": "Asia/Dubai", "default_calling_window_json": {}, "max_concurrent_calls": 1,
                "recording_retention_days": 30, "test_call_enabled": False, "live_campaign_calling_enabled": True,
            })
            with self.fake_gateway(status=503):
                self.client.post("/gateway-lines/check-sims")
            with self.session_factory() as db, patch("app.dispatcher.ThreeCXClient") as client_class:
                with self.assertRaises(DispatchError) as raised:
                    place_next_call(campaign_id, db)
                client_class.assert_not_called()
                self.assertIn(GATEWAY_UNREACHABLE, str(raised.exception))
                queued = db.scalars(select(Call).where(Call.campaign_id == campaign_id)).all()
                self.assertEqual([call.status for call in queued], [CallStatus.queued])
            get_settings.cache_clear()


if __name__ == "__main__":
    unittest.main()
