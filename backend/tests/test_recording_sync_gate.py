import unittest
from unittest import mock

from app import recording_sync
from app.config import Settings


class RecordingSyncGateTests(unittest.TestCase):
    """The background recording linker must not run (and therefore must not mint
    a competing 3CX token) when recording sync is disabled."""

    def _run_one_iteration(self, settings: Settings):
        worker = recording_sync.RecordingSync(poll_seconds=0)
        waits = {"count": 0}

        def fake_wait(_timeout):
            waits["count"] += 1
            return waits["count"] > 1  # execute the loop body once, then stop

        with mock.patch.object(worker.stop_event, "wait", side_effect=fake_wait), \
                mock.patch.object(recording_sync, "get_settings", return_value=settings), \
                mock.patch.object(recording_sync, "sync_threecx_recordings_safe") as sync:
            worker._run()
        return sync

    def test_skips_sync_when_recording_disabled(self):
        sync = self._run_one_iteration(
            Settings(call_provider="threecx", recording_sync_enabled=False)
        )
        sync.assert_not_called()

    def test_runs_sync_when_recording_enabled(self):
        sync = self._run_one_iteration(
            Settings(call_provider="threecx", recording_sync_enabled=True)
        )
        sync.assert_called_once()


if __name__ == "__main__":
    unittest.main()
