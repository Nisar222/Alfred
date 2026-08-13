#!/usr/bin/env python3
"""Authenticated API checks — run inside the Alfred API container."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from app.auth import SESSION_COOKIE, create_session
from app.database import SessionLocal
from app.models import User

BASE = "http://127.0.0.1:8000"


def request(method: str, path: str, body: dict | None = None, headers: dict | None = None):
    payload = None if body is None else json.dumps(body).encode()
    req_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(f"{BASE}{path}", data=payload, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            data = json.loads(raw) if raw else {}
            return resp.status, data
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"raw": raw}
        return exc.code, data


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    return ok


def main() -> int:
    db = SessionLocal()
    user = db.query(User).filter(User.role == "owner").first()
    if not user:
        print("[FAIL] No owner user in database")
        return 1

    token, csrf, auth_session = create_session(db, user)
    db.commit()
    cookie = f"{SESSION_COOKIE}={token}"
    headers = {"Cookie": cookie, "X-CSRF-Token": csrf}

    passed = 0
    total = 0

    def record(name: str, ok: bool, detail: str = ""):
        nonlocal passed, total
        total += 1
        if check(name, ok, detail):
            passed += 1

    code, health = request("GET", "/health")
    record("Health", code == 200 and health.get("status") == "ok", f"HTTP {code}")

    code, assets = request("GET", "/audio-assets", headers=headers)
    record("Audio assets", code == 200 and isinstance(assets, list), f"count={len(assets) if isinstance(assets, list) else 'n/a'}")
    if isinstance(assets, list) and assets:
        first = assets[0]
        record("Audio filename present", bool(first.get("filename") or first.get("original_filename")), first.get("filename") or first.get("original_filename"))

    code, settings = request("GET", "/settings", headers=headers)
    record("Settings GET", code == 200 and "max_concurrent_calls" in settings, f"max_concurrent={settings.get('max_concurrent_calls')}")

    code, live = request("GET", "/campaigns/live-status", headers=headers)
    record("Live status", code == 200 and "active_campaigns" in live, f"active={len(live.get('active_campaigns', []))}")

    code, calls = request("GET", "/calls", headers=headers)
    record("Calls list", code == 200 and isinstance(calls, list), f"count={len(calls) if isinstance(calls, list) else 'n/a'}")
    if isinstance(calls, list) and calls:
        record("Calls list items are objects", all(isinstance(c, dict) for c in calls[:5]))

    code, campaigns = request("GET", "/campaigns", headers=headers)
    record("Campaigns", code == 200 and isinstance(campaigns, list), f"count={len(campaigns) if isinstance(campaigns, list) else 'n/a'}")

    code, playbooks = request("GET", "/playbooks", headers=headers)
    record("Playbooks", code == 200 and isinstance(playbooks, list), f"count={len(playbooks) if isinstance(playbooks, list) else 'n/a'}")

    toggle = not bool(settings.get("test_call_enabled"))
    payload = {
        "default_timezone": settings.get("default_timezone", "Asia/Dubai"),
        "default_calling_window_json": settings.get("default_calling_window_json") or {"start": "09:00", "end": "17:00"},
        "max_concurrent_calls": settings.get("max_concurrent_calls", 1),
        "recording_retention_days": settings.get("recording_retention_days", 90),
        "retry_max_attempts": settings.get("retry_max_attempts", 1),
        "retry_delay_minutes": settings.get("retry_delay_minutes", 60),
        "retry_no_answer": settings.get("retry_no_answer", True),
        "retry_busy": settings.get("retry_busy", True),
        "retry_provider_failure": settings.get("retry_provider_failure", True),
        "dtmf_routing_enabled": settings.get("dtmf_routing_enabled", False),
        "dtmf_routes_json": settings.get("dtmf_routes_json") or {},
        "test_call_enabled": toggle,
        "live_campaign_calling_enabled": settings.get("live_campaign_calling_enabled", False),
    }
    code, saved = request("PUT", "/settings", payload, headers)
    record("Settings PUT round-trip", code == 200 and saved.get("test_call_enabled") == toggle, f"HTTP {code}")

    payload["test_call_enabled"] = settings.get("test_call_enabled", False)
    request("PUT", "/settings", payload, headers)

    print(f"\nResult: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
