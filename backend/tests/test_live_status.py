"""Tests for the lightweight live campaign status endpoint."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.auth import hash_password
from app.main import app
from app.models import Call, CallStatus, Campaign, CampaignStatus, GlobalSettings, User


class LiveStatusTests(unittest.TestCase):
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
            db.add(GlobalSettings(max_concurrent_calls=2, default_timezone="Asia/Dubai"))
            db.commit()
        login = self.client.post("/auth/login", json={
            "email": "owner@example.test", "password": "correct horse battery staple",
        })
        self.assertEqual(login.status_code, 200, login.text)
        self.client.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()
        os.unlink(self.database_file.name)

    def create_active_campaign(self, name="Live campaign"):
        with self.session_factory() as db:
            campaign = Campaign(name=name, script="Hello there", status=CampaignStatus.active)
            db.add(campaign)
            db.commit()
            db.refresh(campaign)
            return campaign.id

    def test_empty_when_no_active_campaigns(self):
        response = self.client.get("/campaigns/live-status")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["active_campaigns"], [])
        self.assertEqual(body["lines_in_use"], 0)

    def test_reports_live_call_counts_and_elapsed(self):
        campaign_id = self.create_active_campaign()
        started = datetime.now(timezone.utc) - timedelta(seconds=75)
        with self.session_factory() as db:
            db.add_all([
                Call(campaign_id=campaign_id, phone="+971500000001", prospect_name="Aisha",
                     status=CallStatus.in_progress, started_at=started),
                Call(campaign_id=campaign_id, phone="+971500000002", status=CallStatus.queued),
                Call(campaign_id=campaign_id, phone="+971500000003", status=CallStatus.completed,
                     completed_at=datetime.now(timezone.utc)),
            ])
            db.commit()

        response = self.client.get("/campaigns/live-status")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["lines_in_use"], 1)
        campaign = body["active_campaigns"][0]
        self.assertEqual(campaign["name"], "Live campaign")
        self.assertEqual(campaign["queued"], 1)
        self.assertEqual(campaign["completed_today"], 1)
        self.assertEqual(campaign["lines_in_use"], 1)
        self.assertEqual(campaign["lines_available"], 2)
        self.assertEqual(len(campaign["live_calls"]), 1)
        self.assertEqual(campaign["live_calls"][0]["prospect_name"], "Aisha")
        self.assertGreaterEqual(campaign["live_calls"][0]["elapsed_seconds"], 75)


if __name__ == "__main__":
    unittest.main()
