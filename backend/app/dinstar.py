"""Read-only client for the Dinstar gateway's HTTP API (live SIM status).

The gateway numbers its ports from 0, so Alfred line N is gateway port N-1.
Credentials come only from server settings and never appear in errors or logs.
"""
from dataclasses import dataclass

import httpx

from .config import Settings

LINE_COUNT = 32
REGISTERED = "REGISTER_OK"


class DinstarError(RuntimeError):
    """A human-readable reason the gateway could not be checked."""


@dataclass(frozen=True)
class SimStatus:
    line: int
    registration: str
    signal: int | None


class DinstarClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        if not settings.dinstar_configured:
            raise DinstarError("Gateway SIM checks are not set up on the server")
        self.client = httpx.Client(
            base_url=settings.dinstar_base_url.rstrip("/"),
            auth=httpx.DigestAuth(settings.dinstar_api_username, settings.dinstar_api_password),
            verify=settings.dinstar_verify_tls,
            timeout=settings.dinstar_timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "DinstarClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def sim_statuses(self, lines: range = range(1, LINE_COUNT + 1)) -> dict[int, SimStatus]:
        """Registration and signal for each line, in one request."""
        ports = ",".join(str(line - 1) for line in lines)
        try:
            response = self.client.get("/api/get_port_info", params={"port": ports, "info_type": "reg,signal"})
        except httpx.TimeoutException as exc:
            raise DinstarError("The gateway did not answer in time") from exc
        except httpx.HTTPError as exc:
            raise DinstarError(f"Alfred could not connect to the gateway ({type(exc).__name__})") from exc
        if response.status_code == 401:
            raise DinstarError("The gateway rejected Alfred's API login")
        if response.status_code != 200:
            raise DinstarError(f"The gateway returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise DinstarError("The gateway sent a reply Alfred could not read") from exc
        if not isinstance(payload, dict) or payload.get("error_code") != 200:
            code = payload.get("error_code") if isinstance(payload, dict) else None
            raise DinstarError(f"The gateway reported an error (code {code})")
        statuses: dict[int, SimStatus] = {}
        for item in payload.get("info") or []:
            try:
                line = int(item["port"]) + 1
            except (KeyError, TypeError, ValueError):
                continue
            if line not in lines:
                continue
            signal = item.get("signal")
            statuses[line] = SimStatus(
                line=line,
                registration=str(item.get("reg") or "UNKNOWN").upper(),
                signal=int(signal) if isinstance(signal, (int, float)) or str(signal).isdigit() else None,
            )
        if not statuses:
            raise DinstarError("The gateway did not report any SIM ports")
        return statuses
