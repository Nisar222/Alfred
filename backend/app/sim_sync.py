"""Background worker that keeps each gateway line's SIM status fresh."""
import logging
import threading

from .config import get_settings
from .database import SessionLocal
from .gateway_lines import check_sims

logger = logging.getLogger(__name__)


class SimStatusSync:
    def __init__(self, poll_seconds: int = 30):
        self.poll_seconds = poll_seconds
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="sim-status-sync", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _check_once(self) -> None:
        settings = get_settings()
        if not settings.dinstar_configured:
            return
        try:
            with SessionLocal() as db:
                summary = check_sims(db, settings)
            if summary["error"]:
                logger.warning("Gateway SIM check failed: %s", summary["error"])
        except Exception:
            logger.exception("Gateway SIM check crashed")

    def _run(self) -> None:
        # Check straight away: gateway lines stay paused until the first fresh result.
        self._check_once()
        while not self.stop_event.wait(self.poll_seconds):
            self._check_once()
