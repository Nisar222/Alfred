"""Background worker that links 3CX recordings to Alfred call rows."""
import threading

from .config import get_settings
from .database import SessionLocal
from .recordings import sync_threecx_recordings_safe


class RecordingSync:
    def __init__(self, poll_seconds: int = 30):
        self.poll_seconds = poll_seconds
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="recording-sync", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _run(self) -> None:
        while not self.stop_event.wait(self.poll_seconds):
            settings = get_settings()
            if settings.call_provider != "threecx":
                continue
            with SessionLocal() as db:
                sync_threecx_recordings_safe(db, settings)
