"""Minimal, test-first client for the 3CX V20 Call Control API.

This module deliberately supports discovery only.  It proves that the VPS can
authenticate and see the approved extension before any outbound call or audio
stream is enabled.
"""
from dataclasses import dataclass
import httpx

from .config import Settings


class ThreeCXError(RuntimeError):
    """A safe, user-facing failure from the 3CX integration."""


@dataclass(frozen=True)
class ThreeCXDevice:
    device_id: str
    user_agent: str | None = None


class ThreeCXClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        if not settings.threecx_base_url or not settings.threecx_app_id or not settings.threecx_api_key:
            raise ThreeCXError("3CX is not configured. Add the base URL, app ID, and API key on the VPS.")
        if not settings.threecx_control_extension:
            raise ThreeCXError("Choose the approved 3CX extension before testing the connection.")
        self.settings = settings
        self.client = httpx.Client(
            base_url=settings.threecx_base_url.rstrip("/"),
            timeout=settings.threecx_timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def _access_token(self) -> str:
        try:
            response = self.client.post(
                "/connect/token",
                data={
                    "client_id": self.settings.threecx_app_id,
                    "client_secret": self.settings.threecx_api_key,
                    "grant_type": "client_credentials",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ThreeCXError("3CX authentication failed. Check the app ID, API key, and API permissions.") from exc
        token = response.json().get("access_token")
        if not token:
            raise ThreeCXError("3CX did not return an access token.")
        return token

    def list_devices(self) -> list[ThreeCXDevice]:
        token = self._access_token()
        extension = self.settings.threecx_control_extension
        try:
            response = self.client.get(
                f"/callcontrol/{extension}/devices",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ThreeCXError(
                f"3CX cannot access extension {extension}. Confirm Call Control access and extension permissions."
            ) from exc
        return [
            ThreeCXDevice(device_id=str(device["device_id"]), user_agent=device.get("user_agent"))
            for device in response.json()
            if device.get("device_id")
        ]
