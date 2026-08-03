"""Focused authorization and durability tests for agent popup records."""
import os
import tempfile
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import hash_password
from app.database import Base, get_db
from app.main import app
from app.models import AgentNotification, Call, Campaign, User
from app.notifications import ensure_routing_notification


class AgentNotificationTests(unittest.TestCase):
    def setUp(self):
        database_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        database_file.close()
        self.database_path = database_file.name
        self.engine = create_engine(f"sqlite:///{self.database_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

        def override_get_db():
            session = self.sessions()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app, base_url="https://testserver")
        with self.sessions() as db:
            db.add_all([
                User(email="owner@example.test", display_name="Owner", role="owner",
                     password_hash=hash_password("owner password")),
                User(email="agent@example.test", display_name="Agent 101", role="agent", threecx_extension="101",
                     password_hash=hash_password("agent password")),
                User(email="other@example.test", display_name="Agent 102", role="agent", threecx_extension="102",
                     password_hash=hash_password("other password")),
            ])
            campaign = Campaign(name="Welcome campaign", script="Approved campaign script")
            db.add(campaign)
            db.flush()
            call = Call(campaign_id=campaign.id, phone="redacted", prospect_name="Customer One",
                        dtmf_digit="1", routed_destination="101", routing_status="routed")
            db.add(call)
            db.flush()
            first = ensure_routing_notification(call, db, recipient_extension="101")
            second = ensure_routing_notification(call, db, recipient_extension="101")
            self.assertIs(first, second)
            self.notification_id = first.id
            db.commit()

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()
        os.unlink(self.database_path)

    def _login(self, email: str, password: str) -> str:
        response = self.client.post("/auth/login", json={"email": email, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["csrf_token"]

    def test_linked_agent_receives_and_acknowledges_snapshot(self):
        csrf = self._login("agent@example.test", "agent password")
        response = self.client.get("/agent/notifications")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["customer_name"], "Customer One")
        self.assertEqual(response.json()[0]["campaign_name"], "Welcome campaign")
        self.assertEqual(response.json()[0]["menu_option"], "1")
        self.assertIsNotNone(response.json()[0]["delivered_at"])

        denied = self.client.post(f"/agent/notifications/{self.notification_id}/ack")
        self.assertEqual(denied.status_code, 403)
        acknowledged = self.client.post(
            f"/agent/notifications/{self.notification_id}/ack", headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(acknowledged.status_code, 200, acknowledged.text)
        self.assertIsNotNone(acknowledged.json()["read_at"])
        self.assertEqual(self.client.get("/agent/notifications").json(), [])

    def test_other_agent_cannot_see_or_acknowledge_notification(self):
        csrf = self._login("other@example.test", "other password")
        self.assertEqual(self.client.get("/agent/notifications").json(), [])
        response = self.client.post(
            f"/agent/notifications/{self.notification_id}/ack", headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(response.status_code, 404)

    def test_owner_can_inspect_without_marking_delivered(self):
        self._login("owner@example.test", "owner password")
        response = self.client.get("/agent/notifications")
        self.assertEqual(len(response.json()), 1)
        self.assertIsNone(response.json()[0]["delivered_at"])

        with self.sessions() as db:
            self.assertEqual(db.query(AgentNotification).count(), 1)


if __name__ == "__main__":
    unittest.main()
