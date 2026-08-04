import unittest
from datetime import datetime, timezone

from app.models import Call, CallStatus
from app.recordings import (
    best_matching_call,
    parse_threecx_recording_id,
    phone_key,
    recording_enabled_for_call,
    threecx_storage_key,
)


class RecordingMatchTests(unittest.TestCase):
    def test_phone_key_ignores_formatting(self):
        self.assertEqual(phone_key("+1 (628) 218-7213"), phone_key("16282187213"))

    def test_storage_key_round_trip(self):
        self.assertEqual(threecx_storage_key(22), "threecx:22")
        self.assertEqual(parse_threecx_recording_id("threecx:22"), 22)
        self.assertIsNone(parse_threecx_recording_id("uploads/foo.wav"))

    def test_recording_enabled_defaults_true(self):
        call = Call(phone="+15551234567", status=CallStatus.completed, configuration_snapshot_json={})
        self.assertTrue(recording_enabled_for_call(call))

    def test_recording_enabled_respects_playbook(self):
        call = Call(
            phone="+15551234567",
            status=CallStatus.completed,
            configuration_snapshot_json={"playbook": {"recording_enabled": False}},
        )
        self.assertFalse(recording_enabled_for_call(call))

    def test_best_matching_call_uses_phone_and_time(self):
        anchor = datetime(2026, 8, 4, 22, 49, 0, tzinfo=timezone.utc)
        call = Call(
            id=7,
            phone="+16282187213",
            status=CallStatus.completed,
            started_at=anchor,
            completed_at=anchor,
            configuration_snapshot_json={"playbook": {"recording_enabled": True}},
        )
        recording = {
            "Id": 22,
            "FromCallerNumber": "+16282187213",
            "StartTime": "2026-08-04T22:49:33.865288Z",
        }
        matched = best_matching_call(recording, [call])
        self.assertIs(matched, call)

    def test_best_matching_call_links_diagnostic_calls_by_time(self):
        anchor = datetime(2026, 8, 4, 22, 49, 29, tzinfo=timezone.utc)
        call = Call(
            id=12,
            phone="diagnostic",
            status=CallStatus.completed,
            completed_at=anchor,
            configuration_snapshot_json={"source": "test-dtmf"},
        )
        recording = {
            "Id": 22,
            "FromCallerNumber": "+16282187213",
            "StartTime": "2026-08-04T22:49:33.865288Z",
        }
        self.assertIs(best_matching_call(recording, [call]), call)

    def test_best_matching_call_rejects_distant_time(self):
        anchor = datetime(2026, 8, 4, 10, 0, 0, tzinfo=timezone.utc)
        call = Call(
            id=7,
            phone="+16282187213",
            status=CallStatus.completed,
            completed_at=anchor,
            configuration_snapshot_json={"playbook": {"recording_enabled": True}},
        )
        recording = {
            "Id": 22,
            "FromCallerNumber": "+16282187213",
            "StartTime": "2026-08-04T22:49:33.865288Z",
        }
        self.assertIsNone(best_matching_call(recording, [call]))


if __name__ == "__main__":
    unittest.main()
