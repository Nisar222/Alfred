#!/usr/bin/env python3
"""Production smoke test against a running Alfred API (default: http://127.0.0.1:8000)."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("ALFRED_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
EMAIL = os.environ.get("ALFRED_TEST_EMAIL", "")
PASSWORD = os.environ.get("ALFRED_TEST_PASSWORD", "")


def request(method: str, path: str, body: dict | None = None, headers: dict | None = None):
    payload = None if body is None else json.dumps(body).encode()
    req_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(f"{BASE}{path}", data=payload, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode()
            data = json.loads(raw) if raw else {}
            return resp.status, resp.headers, data
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"raw": raw}
        return exc.code, exc.headers, data


def check(name: str, ok: bool, detail: str = ""):
    status = "PASS" if ok else "FAIL"
    line = f"[{status}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    return ok


def main() -> int:
    passed = 0
    total = 0

    def record(name: str, ok: bool, detail: str = ""):
        nonlocal passed, total
        total += 1
        if check(name, ok, detail):
            passed += 1

    code, _, health = request("GET", "/health")
    record("Health endpoint", code == 200 and health.get("status") == "ok", f"HTTP {code}")

    code, _, _ = request("GET", "/calls")
    record("Calls require auth", code == 401, f"HTTP {code}")

    code, _, _ = request("GET", "/audio-assets")
    record("Audio list requires auth", code == 401, f"HTTP {code}")

    code, _, _ = request("GET", "/settings")
    record("Settings require auth", code == 401, f"HTTP {code}")

    code, _, _ = request("POST", "/auth/login", {"email": "invalid@example.test", "password": "wrong-password"})
    record("Bad login rejected", code == 401, f"HTTP {code}")

    for path in ("/", "/app.js", "/config.css", "/styles.css"):
        req = urllib.request.Request(f"{BASE}{path}", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                record(f"Static asset {path}", resp.status == 200, f"HTTP {resp.status}")
        except urllib.error.HTTPError as exc:
            record(f"Static asset {path}", False, f"HTTP {exc.code}")

    if EMAIL and PASSWORD:
        code, headers, login = request("POST", "/auth/login", {"email": EMAIL, "password": PASSWORD})
        record("Owner login", code == 200 and "csrf_token" in login, f"HTTP {code}")
        if code != 200:
            print(f"Authenticated checks skipped — login failed")
        else:
            cookie = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
            session = cookie.split(";", 1)[0]
            csrf = login["csrf_token"]
            auth_headers = {"Cookie": session, "X-CSRF-Token": csrf}

            code, _, assets = request("GET", "/audio-assets", headers=auth_headers)
            record("Audio assets list", code == 200 and isinstance(assets, list), f"HTTP {code}, count={len(assets) if isinstance(assets, list) else 'n/a'}")

            code, _, settings = request("GET", "/settings", headers=auth_headers)
            record("Settings load", code == 200 and "max_concurrent_calls" in settings, f"HTTP {code}")

            code, _, live = request("GET", "/campaigns/live-status", headers=auth_headers)
            record("Live status", code == 200 and "active_campaigns" in live, f"HTTP {code}")

            code, _, campaigns = request("GET", "/campaigns", headers=auth_headers)
            record("Campaigns list", code == 200 and isinstance(campaigns, list), f"HTTP {code}, count={len(campaigns) if isinstance(campaigns, list) else 'n/a'}")

            code, _, calls = request("GET", "/calls", headers=auth_headers)
            record("Calls list", code == 200 and isinstance(calls, list), f"HTTP {code}, count={len(calls) if isinstance(calls, list) else 'n/a'}")

            toggle = not bool(settings.get("test_call_enabled"))
            code, _, saved = request(
                "PUT",
                "/settings",
                {
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
                },
                headers=auth_headers,
            )
            record("Settings save (PUT)", code == 200 and saved.get("test_call_enabled") == toggle, f"HTTP {code}")

            # Restore original value
            request(
                "PUT",
                "/settings",
                {
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
                    "test_call_enabled": settings.get("test_call_enabled", False),
                    "live_campaign_calling_enabled": settings.get("live_campaign_calling_enabled", False),
                },
                headers=auth_headers,
            )
    else:
        print("[SKIP] Authenticated API checks — set ALFRED_TEST_EMAIL and ALFRED_TEST_PASSWORD")

    print(f"\nResult: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
