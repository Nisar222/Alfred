"""Small, deliberately constrained 3CX V20 Call Control client."""
from dataclasses import dataclass
from pathlib import Path
import subprocess
import time
from typing import Iterator

import httpx

from .config import Settings


class ThreeCXError(RuntimeError):
    """A safe, user-facing failure from the 3CX integration."""


@dataclass(frozen=True)
class ThreeCXDevice:
    device_id: str
    user_agent: str | None = None


@dataclass(frozen=True)
class ThreeCXTestCall:
    participant_id: int
    destination: str


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

    @staticmethod
    def _failure(message: str, exc: httpx.HTTPError) -> ThreeCXError:
        """Expose only the upstream HTTP diagnostic; never credentials or tokens."""
        if isinstance(exc, httpx.HTTPStatusError):
            body = exc.response.text.replace("\n", " ").strip()[:300]
            suffix = f" (3CX HTTP {exc.response.status_code}" + (f": {body}" if body else "") + ")"
            return ThreeCXError(message + suffix)
        return ThreeCXError(message)

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

    def inspect_accessible_dns(self) -> list[dict[str, object]]:
        """Return a privacy-safe view of call-control DNs available to the app."""
        try:
            response = self.client.get("/callcontrol", headers=self._authorized_headers())
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._failure("3CX could not list call-control entities.", exc) from exc
        entities: list[dict[str, object]] = []
        for entity in response.json():
            participants = entity.get("participants") or []
            entities.append(
                {
                    "dn": entity.get("dn"),
                    "type": entity.get("type"),
                    "participants": [
                        {
                            "id": participant.get("id"),
                            "status": participant.get("status"),
                            "dn": participant.get("dn"),
                            "direct_control": participant.get("direct_control"),
                        }
                        for participant in participants
                    ],
                }
            )
        return entities

    def _authorized_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    @property
    def source_dn(self) -> str:
        # A 3CX Service Principal Client ID is also the Route Point DN. Media
        # control is intentionally performed only on this application-owned DN,
        # never on a user's extension.
        return self.settings.threecx_app_id

    def start_test_call(self, destination: str) -> ThreeCXTestCall:
        try:
            response = self.client.post(
                f"/callcontrol/{self.source_dn}/makecall",
                headers=self._authorized_headers(),
                json={"destination": destination, "timeout": self.settings.threecx_test_call_timeout_seconds},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._failure("3CX could not start the test call. Check the route point and outbound route.", exc) from exc
        result = response.json().get("result") or {}
        participant_id = result.get("id")
        if participant_id is None:
            raise ThreeCXError("3CX accepted the test call but did not return its call participant.")
        return ThreeCXTestCall(participant_id=int(participant_id), destination=destination)

    def wait_until_connected(self, call: ThreeCXTestCall) -> None:
        deadline = time.monotonic() + self.settings.threecx_test_call_timeout_seconds
        last_status = "unknown"
        while time.monotonic() < deadline:
            try:
                response = self.client.get(
                    f"/callcontrol/{self.source_dn}/participants/{call.participant_id}",
                    headers=self._authorized_headers(),
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise self._failure("3CX could not read the test-call status.", exc) from exc
            participant = response.json()
            last_status = str(participant.get("status", "unknown"))
            if last_status.lower() == "connected":
                return
            if last_status.lower() in {"dropped", "failed", "disconnected"}:
                raise ThreeCXError(f"The test call ended before it was answered ({last_status}).")
            time.sleep(1)
        raise ThreeCXError(f"The test call was not answered within the timeout ({last_status}).")

    @staticmethod
    def _pcm_chunks(audio_path: Path) -> Iterator[bytes]:
        """Convert a source MP3/WAV to the exact real-time audio 3CX expects."""
        command = [
            "ffmpeg", "-nostdin", "-v", "error", "-re", "-i", str(audio_path),
            "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1",
        ]
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            raise ThreeCXError("Audio converter is unavailable in the API container.") from exc
        assert process.stdout is not None
        try:
            while chunk := process.stdout.read(320):  # 20 ms of 8 kHz, 16-bit mono PCM
                yield chunk
        finally:
            process.stdout.close()
            process.wait(timeout=10)
            if process.returncode not in (0, None):
                raise ThreeCXError("3CX could not convert the prerecorded message to call audio.")

    def play_prerecorded_message(self, call: ThreeCXTestCall, audio_path: Path) -> None:
        if not audio_path.is_file():
            raise ThreeCXError("The prerecorded message is missing from the VPS media folder.")
        try:
            with self.client.stream(
                "POST",
                f"/callcontrol/{self.source_dn}/participants/{call.participant_id}/stream",
                headers={**self._authorized_headers(), "Content-Type": "application/octet-stream"},
                content=self._pcm_chunks(audio_path),
            ) as response:
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._failure("3CX could not play the prerecorded message.", exc) from exc

    def drop_call(self, call: ThreeCXTestCall) -> None:
        try:
            response = self.client.post(
                f"/callcontrol/{self.source_dn}/participants/{call.participant_id}/drop",
                headers=self._authorized_headers(),
                json={},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._failure("The message finished, but 3CX could not end the test call.", exc) from exc
