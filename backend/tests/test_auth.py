"""Focused tests for local, revocable Alfred browser sessions."""
import os
import tempfile
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import hash_password
from app.database import Base, get_db
from app.main import app
from app.models import AuthSession, User


class AuthTests(unittest.TestCase):
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
        db = self.session_factory()
        db.add(User(email="owner@example.test", display_name="Owner", role="owner",
                    password_hash=hash_password("correct horse battery staple")))
        db.commit()
        db.close()

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()
        os.unlink(self.database_file.name)

    def test_login_session_csrf_and_logout(self):
        denied = self.client.post("/auth/login", json={"email": "unknown@example.test", "password": "correct horse battery staple"})
        self.assertEqual(denied.status_code, 401)

        login = self.client.post("/auth/login", json={"email": "OWNER@example.test", "password": "correct horse battery staple"})
        self.assertEqual(login.status_code, 200, login.text)
        self.assertIn("alfred_session", login.headers.get("set-cookie", ""))
        csrf = login.json()["csrf_token"]
        self.assertEqual(self.client.get("/auth/me").json()["role"], "owner")

        missing_csrf = self.client.post("/auth/logout")
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(self.client.post("/auth/logout", headers={"X-CSRF-Token": csrf}).status_code, 204)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

        db = self.session_factory()
        self.assertIsNotNone(db.query(AuthSession).filter(AuthSession.revoked_at.is_not(None)).first())
        db.close()

    def test_inactive_user_cannot_log_in(self):
        db = self.session_factory()
        user = db.query(User).filter_by(email="owner@example.test").one()
        user.is_active = False
        db.commit()
        db.close()
        response = self.client.post("/auth/login", json={"email": "owner@example.test", "password": "correct horse battery staple"})
        self.assertEqual(response.status_code, 401)

    def test_only_an_owner_can_provision_an_inactive_agent(self):
        self.assertEqual(self.client.get("/admin/users").status_code, 401)
        login = self.client.post("/auth/login", json={"email": "owner@example.test", "password": "correct horse battery staple"})
        csrf = login.json()["csrf_token"]
        created = self.client.post("/admin/users", headers={"X-CSRF-Token": csrf}, json={
            "email": "agent@example.test", "display_name": "Queue Agent", "role": "agent",
            "threecx_user_id": "42", "threecx_extension": "101",
        })
        self.assertEqual(created.status_code, 201, created.text)
        self.assertFalse(created.json()["is_active"])
        access = self.client.put(f"/admin/users/{created.json()['id']}/access", headers={"X-CSRF-Token": csrf}, json={
            "password": "a safe agent password", "is_active": True,
        })
        self.assertEqual(access.status_code, 200, access.text)
        self.assertTrue(access.json()["is_active"])
        duplicate = self.client.post("/admin/users", headers={"X-CSRF-Token": csrf}, json={
            "email": "another@example.test", "display_name": "Another", "threecx_extension": "101",
        })
        self.assertEqual(duplicate.status_code, 409)
        agent_login = self.client.post("/auth/login", json={
            "email": "101", "password": "a safe agent password",
        })
        self.assertEqual(agent_login.status_code, 200, agent_login.text)
        self.assertEqual(agent_login.json()["user"]["threecx_extension"], "101")

    def test_operational_api_authorization_matrix(self):
        # Health and the dashboard shell remain reachable so login can render;
        # operational data is never returned anonymously.
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/campaigns").status_code, 401)
        self.assertEqual(self.client.get("/settings").status_code, 401)

        with self.session_factory() as db:
            db.add_all([
                User(email="supervisor@example.test", display_name="Supervisor", role="supervisor",
                     password_hash=hash_password("supervisor password")),
                User(email="agent@example.test", display_name="Agent", role="agent",
                     password_hash=hash_password("agent password")),
            ])
            db.commit()

        self.client.cookies.clear()
        supervisor_login = self.client.post("/auth/login", json={
            "email": "supervisor@example.test", "password": "supervisor password",
        })
        supervisor_csrf = supervisor_login.json()["csrf_token"]
        self.assertEqual(self.client.get("/campaigns").status_code, 200)
        self.assertEqual(self.client.get("/settings").status_code, 403)
        self.assertEqual(self.client.post("/campaigns", headers={"X-CSRF-Token": supervisor_csrf}, json={
            "name": "Supervisor campaign", "script": "Approved script",
        }).status_code, 201)
        self.assertEqual(self.client.post("/campaigns", json={
            "name": "Missing CSRF", "script": "Approved script",
        }).status_code, 403)

        self.client.cookies.clear()
        agent_login = self.client.post("/auth/login", json={
            "email": "agent@example.test", "password": "agent password",
        })
        agent_csrf = agent_login.json()["csrf_token"]
        self.assertEqual(self.client.get("/campaigns").status_code, 200)
        self.assertEqual(self.client.get("/calls").status_code, 200)
        self.assertEqual(self.client.post("/campaigns", headers={"X-CSRF-Token": agent_csrf}, json={
            "name": "Agent campaign", "script": "Not allowed",
        }).status_code, 403)
        self.assertEqual(self.client.get("/admin/users").status_code, 403)
        self.assertEqual(self.client.get("/integrations/3cx/directory").status_code, 403)
