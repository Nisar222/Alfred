"""End-to-end API tests using an isolated SQLite file.

These exercise the operator's MVP workflow without requiring PostgreSQL,
telephony, or any customer data.
"""
import os
import tempfile
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app


class ApiIntegrationTests(unittest.TestCase):
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
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()
        os.unlink(self.database_file.name)

    def create_campaign(self):
        response = self.client.post("/campaigns", json={
            "name": "July follow-up", "script": "Ask a discovery question before presenting the offer.",
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def test_campaign_to_review_and_metrics_workflow(self):
        campaign_id = self.create_campaign()

        # Calls must not run until an operator explicitly launches the campaign.
        blocked = self.client.post(f"/campaigns/{campaign_id}/run-simulation")
        self.assertEqual(blocked.status_code, 409)

        queued = self.client.post(f"/campaigns/{campaign_id}/contacts", json=[
            {"phone": "+971500000001", "name": "Aisha", "details": "Retail"},
            {"phone": "+971500000002", "name": "Omar"},
        ])
        self.assertEqual(queued.status_code, 201, queued.text)
        self.assertEqual(len(queued.json()), 2)
        self.assertTrue(all(call["status"] == "queued" for call in queued.json()))

        launched = self.client.post(f"/campaigns/{campaign_id}/launch")
        self.assertEqual(launched.status_code, 200)
        self.assertEqual(launched.json()["status"], "active")

        completed = self.client.post(f"/campaigns/{campaign_id}/run-simulation")
        self.assertEqual(completed.status_code, 200, completed.text)
        calls = completed.json()
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call["status"] == "completed" and call["transcript"] for call in calls))

        sale = self.client.post(f"/calls/{calls[0]['id']}/outcome", json={"outcome": "sale"})
        self.assertEqual(sale.status_code, 200, sale.text)
        self.assertEqual(sale.json()["outcome"], "sale")
        self.assertIsNotNone(sale.json()["metric"])

        reject = self.client.post(f"/calls/{calls[1]['id']}/outcome", json={"outcome": "reject"})
        self.assertEqual(reject.status_code, 200, reject.text)

        listed = self.client.get(f"/calls?campaign_id={campaign_id}")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 2)
        self.assertTrue(all(call["metric"] for call in listed.json()))

        metrics = self.client.get("/metrics/daily")
        self.assertEqual(metrics.status_code, 200)
        self.assertEqual(metrics.json()["calls"], 2)
        self.assertEqual(metrics.json()["outcomes"]["sale"], 1)
        self.assertEqual(metrics.json()["outcomes"]["reject"], 1)
        self.assertEqual(metrics.json()["conversion_rate"], 50.0)

    def test_csv_validation_and_outcome_requires_completed_call(self):
        campaign_id = self.create_campaign()
        missing_phone = self.client.post(
            f"/campaigns/{campaign_id}/contacts/csv",
            files={"file": ("contacts.csv", "name\nAisha\n", "text/csv")},
        )
        self.assertEqual(missing_phone.status_code, 422)

        uploaded = self.client.post(
            f"/campaigns/{campaign_id}/contacts/csv",
            files={"file": ("contacts.csv", "phone,name\n+971500000003,Noor\n\n", "text/csv")},
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        self.assertEqual(uploaded.json(), {"queued": 1})
        call_id = self.client.get(f"/calls?campaign_id={campaign_id}").json()[0]["id"]
        early_label = self.client.post(f"/calls/{call_id}/outcome", json={"outcome": "lead"})
        self.assertEqual(early_label.status_code, 409)


if __name__ == "__main__":
    unittest.main()
