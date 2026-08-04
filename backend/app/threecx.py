"""Small, deliberately constrained 3CX V20 Call Control client."""
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Callable, Iterator
from urllib.parse import urlparse, urlunparse

import httpx
from websockets.sync.client import ClientConnection, connect

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
    initial_status: str
    initial_reason: str


@dataclass(frozen=True)
class ThreeCXDirectoryUser:
    """The small, non-sensitive subset of a 3CX user needed by Alfred."""

    user_id: str
    name: str
    extension: str | None
    email: str | None


@dataclass(frozen=True)
class ThreeCXDirectoryMember:
    """A user reference in a 3CX queue or ring group."""

    user_id: str | None
    extension: str | None


@dataclass(frozen=True)
class ThreeCXDirectoryGroup:
    """Read-only representation of a 3CX queue or ring group."""

    group_id: str
    extension: str | None
    name: str
    members: tuple[ThreeCXDirectoryMember, ...]


def _event_value(value: object, *names: str) -> object | None:
    if not isinstance(value, dict):
        return None
    lowered = {str(key).lower(): item for key, item in value.items()}
    return next((lowered[name.lower()] for name in names if name.lower() in lowered), None)


def parse_dtmf_event(message: str, source_dn: str, participant_id: int) -> str | None:
    """Return one safe DTMF digit from a 3CX Route Point event, if present."""
    try:
        payload = json.loads(message)
    except (TypeError, json.JSONDecodeError):
        return None
    event = _event_value(payload, "event") or payload
    event_type = _event_value(event, "eventtype", "event_type", "type")
    if str(event_type).lower() not in {"2", "dtmfstring", "dtmf_string"}:
        return None
    entity = str(_event_value(event, "entity", "path") or "")
    if entity and f"/callcontrol/{source_dn}/participants/{participant_id}" not in entity:
        return None
    attached = _event_value(event, "attacheddata", "attached_data", "data")
    if isinstance(attached, str):
        try:
            attached = json.loads(attached)
        except json.JSONDecodeError:
            pass
    candidates = [attached]
    if isinstance(attached, dict):
        candidates.append(_event_value(attached, "response", "result", "dtmf_input", "dtmfstring", "dtmf", "digit"))
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate = _event_value(candidate, "dtmf_input", "dtmfstring", "dtmf", "digit", "value")
        value = str(candidate or "").strip()
        if len(value) == 1 and value in "0123456789*#":
            return value
    return None


class ThreeCXDtmfMonitor:
    """Short-lived event channel scoped to one application-owned participant."""
    def __init__(self, client: "ThreeCXClient", call: ThreeCXTestCall):
        self.client = client
        self.call = call
        self.connection: ClientConnection | None = None

    def __enter__(self) -> "ThreeCXDtmfMonitor":
        parsed = urlparse(self.client.settings.threecx_base_url.rstrip("/"))
        websocket_url = urlunparse(("wss" if parsed.scheme == "https" else "ws", parsed.netloc, "/callcontrol/ws", "", "", ""))
        try:
            self.connection = connect(
                websocket_url, additional_headers=self.client._authorized_headers(),
                open_timeout=self.client.settings.threecx_timeout_seconds, close_timeout=3,
            )
        except Exception as exc:
            raise ThreeCXError("3CX could not open the Route Point event channel.") from exc
        return self

    def poll(self, timeout_seconds: float = 0.05) -> str | None:
        """Return one DTMF digit if the Route Point event channel already has one."""
        if self.connection is None:
            raise ThreeCXError("The Route Point event channel is not connected.")
        if timeout_seconds <= 0:
            timeout_seconds = 0.001
        try:
            message = self.connection.recv(timeout=timeout_seconds)
        except TimeoutError:
            return None
        except Exception as exc:
            raise ThreeCXError("The Route Point event channel closed unexpectedly.") from exc
        return parse_dtmf_event(str(message), self.client.source_dn, self.call.participant_id)

    def wait(self, timeout_seconds: int) -> str | None:
        if self.connection is None:
            raise ThreeCXError("The Route Point event channel is not connected.")
        deadline = time.monotonic() + timeout_seconds
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                message = self.connection.recv(timeout=remaining)
            except TimeoutError:
                return None
            except Exception as exc:
                raise ThreeCXError("The Route Point event channel closed unexpectedly.") from exc
            digit = parse_dtmf_event(str(message), self.client.source_dn, self.call.participant_id)
            if digit is not None:
                return digit
        return None

    def __exit__(self, *_: object) -> None:
        if self.connection is not None:
            self.connection.close()


class ThreeCXClient:
    _SILENCE_CHUNK = b"\x00" * 320

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

    @staticmethod
    def _field(payload: object, *names: str) -> object | None:
        """Read an XAPI field without depending on one casing/version."""
        return _event_value(payload, *names)

    def _xapi_collection(self, path: str) -> list[dict[str, Any]]:
        """Read all pages of an XAPI collection using one short-lived token.

        3CX XAPI uses OData-style pagination on some installations, but older
        releases can return a plain JSON array.  Accept both without exposing
        arbitrary response data to callers or logs.
        """
        headers = self._authorized_headers()
        next_path: str | None = path
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        while next_path:
            if next_path in seen:
                raise ThreeCXError("3CX returned a repeating XAPI page link.")
            seen.add(next_path)
            try:
                response = self.client.get(next_path, headers=headers)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise self._failure("3CX could not read its user directory.", exc) from exc
            payload = response.json()
            if isinstance(payload, list):
                page = payload
                next_path = None
            elif isinstance(payload, dict):
                page = self._field(payload, "value", "items") or []
                next_link = self._field(payload, "@odata.nextLink", "odata.nextLink", "nextLink", "next")
                next_path = str(next_link) if next_link else None
            else:
                raise ThreeCXError("3CX returned an invalid XAPI directory response.")
            if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
                raise ThreeCXError("3CX returned an invalid XAPI directory page.")
            rows.extend(page)
        return rows

    @classmethod
    def _directory_user(cls, payload: dict[str, Any]) -> ThreeCXDirectoryUser | None:
        user_id = cls._field(payload, "id", "user_id", "userId")
        if user_id is None:
            return None
        name = cls._field(payload, "name", "displayName", "display_name")
        if not name:
            first = str(cls._field(payload, "firstName", "first_name") or "").strip()
            last = str(cls._field(payload, "lastName", "last_name") or "").strip()
            name = " ".join(part for part in (first, last) if part)
        extension = cls._field(payload, "number", "extension", "extensionNumber", "dn")
        email = cls._field(payload, "email", "emailAddress", "email_address")
        return ThreeCXDirectoryUser(
            user_id=str(user_id), name=str(name or "Unnamed 3CX user"),
            extension=str(extension) if extension not in (None, "") else None,
            email=str(email) if email not in (None, "") else None,
        )

    @classmethod
    def _directory_group(cls, payload: dict[str, Any], member_fields: tuple[str, ...]) -> ThreeCXDirectoryGroup | None:
        group_id = cls._field(payload, "id", "group_id", "groupId")
        if group_id is None:
            return None
        extension = cls._field(payload, "number", "extension", "extensionNumber", "dn")
        name = cls._field(payload, "name", "displayName", "display_name")
        members_payload = cls._field(payload, *member_fields) or []
        if not isinstance(members_payload, list):
            members_payload = []
        members: list[ThreeCXDirectoryMember] = []
        for item in members_payload:
            if isinstance(item, dict):
                member_id = cls._field(item, "id", "Id", "user_id", "userId", "memberId", "UserId")
                member_extension = cls._field(item, "number", "Number", "extension", "extensionNumber", "dn", "Extension")
            else:
                member_id, member_extension = item, None
            if member_id is not None or member_extension is not None:
                members.append(ThreeCXDirectoryMember(
                    user_id=str(member_id) if member_id is not None else None,
                    extension=str(member_extension) if member_extension not in (None, "") else None,
                ))
        return ThreeCXDirectoryGroup(
            group_id=str(group_id), extension=str(extension) if extension not in (None, "") else None,
            name=str(name or "Unnamed 3CX group"), members=tuple(members),
        )

    def _members_from_xapi_rows(self, rows: list[dict[str, Any]]) -> tuple[ThreeCXDirectoryMember, ...]:
        members: list[ThreeCXDirectoryMember] = []
        for item in rows:
            member_id = self._field(item, "id", "Id", "user_id", "userId", "memberId", "UserId")
            member_extension = self._field(item, "number", "Number", "extension", "extensionNumber", "dn", "Extension")
            if member_id is not None or member_extension is not None:
                members.append(ThreeCXDirectoryMember(
                    user_id=str(member_id) if member_id is not None else None,
                    extension=str(member_extension) if member_extension not in (None, "") else None,
                ))
        return tuple(members)

    @staticmethod
    def _resolve_member_extensions(
        groups: list[ThreeCXDirectoryGroup], users: list[ThreeCXDirectoryUser],
    ) -> list[ThreeCXDirectoryGroup]:
        extensions = {user.user_id: user.extension for user in users}
        return [
            ThreeCXDirectoryGroup(
                group_id=group.group_id, extension=group.extension, name=group.name,
                members=tuple(ThreeCXDirectoryMember(
                    user_id=member.user_id,
                    extension=member.extension or extensions.get(member.user_id or ""),
                ) for member in group.members),
            )
            for group in groups
        ]

    def list_xapi_users(self) -> list[ThreeCXDirectoryUser]:
        """List all visible 3CX users; no 3CX state is changed."""
        return [user for row in self._xapi_collection("/xapi/v1/Users") if (user := self._directory_user(row))]

    def list_xapi_ring_groups(self) -> list[ThreeCXDirectoryGroup]:
        """List visible ring groups and their member references."""
        groups: list[ThreeCXDirectoryGroup] = []
        for row in self._xapi_collection("/xapi/v1/RingGroups"):
            group = self._directory_group(row, ("Members", "members", "users"))
            if not group:
                continue
            if not group.members:
                member_rows = self._xapi_collection(f"/xapi/v1/RingGroups({group.group_id})/Members")
                group = ThreeCXDirectoryGroup(
                    group_id=group.group_id,
                    extension=group.extension,
                    name=group.name,
                    members=self._members_from_xapi_rows(member_rows),
                )
            groups.append(group)
        return groups

    def list_xapi_queues(self) -> list[ThreeCXDirectoryGroup]:
        """List visible queues and their agent/member references."""
        return [
            group for row in self._xapi_collection("/xapi/v1/Queues")
            if (group := self._directory_group(row, ("agents", "members", "users")))
        ]

    def list_xapi_directory(self) -> tuple[
        list[ThreeCXDirectoryUser], list[ThreeCXDirectoryGroup], list[ThreeCXDirectoryGroup],
    ]:
        """Return users, ring groups, and queues with member extensions resolved."""
        users = self.list_xapi_users()
        return (
            users,
            self._resolve_member_extensions(self.list_xapi_ring_groups(), users),
            self._resolve_member_extensions(self.list_xapi_queues(), users),
        )

    def single_member_extension(self, destination: str) -> str | None:
        """Resolve a one-person queue/ring group without broadcasting context.

        Multi-member destinations deliberately return ``None`` until the call
        event stream identifies which member actually answered.
        """
        _users, ring_groups, queues = self.list_xapi_directory()
        group = next((item for item in (*ring_groups, *queues) if item.extension == destination), None)
        if not group:
            return destination if destination.isdigit() else None
        extensions = {member.extension for member in group.members if member.extension}
        return next(iter(extensions)) if len(extensions) == 1 else None

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
        payload = response.json()
        result = payload.get("result") or {}
        participant_id = result.get("id")
        if participant_id is None:
            raise ThreeCXError("3CX accepted the test call but did not return its call participant.")
        return ThreeCXTestCall(
            participant_id=int(participant_id),
            destination=destination,
            initial_status=str(payload.get("finalstatus", "not provided")),
            initial_reason=str(payload.get("reasontext") or payload.get("reason") or "not provided"),
        )

    def wait_until_connected(self, call: ThreeCXTestCall) -> None:
        deadline = time.monotonic() + self.settings.threecx_test_call_timeout_seconds
        last_status = "unknown"
        while time.monotonic() < deadline:
            try:
                response = self.client.get(
                    f"/callcontrol/{self.source_dn}",
                    headers=self._authorized_headers(),
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise self._failure("3CX could not read the Route Point call state.", exc) from exc
            participant = next(
                (
                    item
                    for item in (response.json().get("participants") or [])
                    if item.get("id") == call.participant_id
                ),
                None,
            )
            if participant is None:
                time.sleep(1)
                continue
            last_status = str(participant.get("status", "unknown"))
            if last_status.lower() == "connected":
                return
            if last_status.lower() in {"dropped", "failed", "disconnected"}:
                raise ThreeCXError(f"The test call ended before it was answered ({last_status}).")
            time.sleep(1)
        raise ThreeCXError(
            "The test call was not answered within the timeout "
            f"({last_status}; initial 3CX result: {call.initial_status} — {call.initial_reason})."
        )

    @staticmethod
    def _stop_ffmpeg(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

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

    def play_prerecorded_message_with_dtmf(
        self,
        call: ThreeCXTestCall,
        monitor: ThreeCXDtmfMonitor,
        audio_path: Path,
        timeout_seconds: int = 15,
    ) -> tuple[str | None, Callable[[], None]]:
        """Stream opening audio while listening for DTMF.

        Returns the captured digit and a ``finish`` callback. Call ``finish``
        only after ``route_to`` (or ``drop_call``) so 3CX keeps the caller
        connected instead of treating an abrupt stream end as a hangup.
        """
        if not audio_path.is_file():
            raise ThreeCXError("The prerecorded message is missing from the VPS media folder.")
        command = [
            "ffmpeg", "-nostdin", "-v", "error", "-re", "-i", str(audio_path),
            "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1",
        ]
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            raise ThreeCXError("Audio converter is unavailable in the API container.") from exc

        captured_digit: list[str | None] = [None]
        hold_stream = threading.Event()
        end_stream = threading.Event()
        stream_error: list[Exception] = []
        deadline = time.monotonic() + timeout_seconds

        def listen_for_dtmf() -> None:
            while not end_stream.is_set() and time.monotonic() < deadline:
                digit = monitor.poll(timeout_seconds=0.1)
                if digit is not None:
                    captured_digit[0] = digit
                    hold_stream.set()
                    self._stop_ffmpeg(process)
                    return

        listener = threading.Thread(target=listen_for_dtmf, name="dtmf-listener", daemon=True)
        listener.start()

        def chunk_generator() -> Iterator[bytes]:
            assert process.stdout is not None
            try:
                while not end_stream.is_set():
                    if hold_stream.is_set():
                        yield self._SILENCE_CHUNK
                        time.sleep(0.02)
                        continue
                    if time.monotonic() >= deadline:
                        break
                    chunk = process.stdout.read(320)
                    if not chunk:
                        break
                    yield chunk
            finally:
                end_stream.set()
                listener.join(timeout=1)
                self._stop_ffmpeg(process)

        def run_stream() -> None:
            try:
                with self.client.stream(
                    "POST",
                    f"/callcontrol/{self.source_dn}/participants/{call.participant_id}/stream",
                    headers={**self._authorized_headers(), "Content-Type": "application/octet-stream"},
                    content=chunk_generator(),
                ) as response:
                    response.raise_for_status()
            except Exception as exc:
                stream_error.append(exc)

        stream_thread = threading.Thread(target=run_stream, name="audio-stream", daemon=True)
        stream_thread.start()

        while stream_thread.is_alive() and time.monotonic() < deadline and captured_digit[0] is None:
            time.sleep(0.05)

        def finish() -> None:
            end_stream.set()
            stream_thread.join(timeout=10)
            listener.join(timeout=1)
            if stream_error:
                raise stream_error[0]

        return captured_digit[0], finish

    def monitor_dtmf(self, call: ThreeCXTestCall) -> ThreeCXDtmfMonitor:
        return ThreeCXDtmfMonitor(self, call)

    def route_to(self, call: ThreeCXTestCall, destination: str, alfred_call_id: int) -> None:
        """Keep the Route Point connected until an allowlisted destination answers."""
        try:
            response = self.client.post(
                f"/callcontrol/{self.source_dn}/participants/{call.participant_id}/routeto",
                headers=self._authorized_headers(),
                json={"destination": destination},
                # routeto completes only after 3CX delivers the call or its
                # queue attempt fails. Do not cancel a valid PBX handoff at
                # the client's shorter general API timeout.
                timeout=max(90.0, self.settings.threecx_timeout_seconds),
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ThreeCXError("3CX did not finish the queue routing attempt within 90 seconds.") from exc
        except httpx.HTTPError as exc:
            raise self._failure("3CX could not route the answered call to the selected queue.", exc) from exc

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

    def list_xapi_recordings(self) -> list[dict[str, Any]]:
        """List call recordings visible to the configured 3CX application."""
        return self._xapi_collection("/xapi/v1/Recordings")

    def stream_recording(self, recording_id: int):
        """Stream one recording from 3CX without persisting it on Alfred."""
        path = f"/xapi/v1/Recordings/Pbx.DownloadRecording(recId={recording_id})"
        try:
            return self.client.stream(
                "GET",
                path,
                headers=self._authorized_headers(),
                follow_redirects=True,
                timeout=max(120.0, self.settings.threecx_timeout_seconds),
            )
        except httpx.HTTPError as exc:
            raise self._failure("3CX could not stream the call recording.", exc) from exc
