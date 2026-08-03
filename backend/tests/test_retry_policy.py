"""Focused tests for durable, bounded campaign retries."""
from datetime import datetime, timezone
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.dispatcher import _failure_category, _next_allowed_time, _schedule_retry
from app.models import Call, CallStatus, Campaign
from app.threecx import ThreeCXError


class RetryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()
        self.campaign = Campaign(
            name="Retry test", script="Approved retry test script",
            timezone="UTC", calling_window_json={"start": "09:00", "end": "17:00"},
        )
        self.session.add(self.campaign)
        self.session.flush()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def failed_attempt(self, number=1, category="no_answer", maximum=3):
        call = Call(
            campaign_id=self.campaign.id, phone="simulator-destination",
            status=CallStatus.failed, attempt_number=number,
            completed_at=datetime(2026, 8, 3, 16, 45, tzinfo=timezone.utc),
            failure_reason="The simulator call was not answered", failure_category=category,
            configuration_snapshot_json={"global": {
                "retry_max_attempts": maximum, "retry_delay_minutes": 30,
                "retry_no_answer": True, "retry_busy": False,
                "retry_provider_failure": True,
            }},
        )
        self.session.add(call)
        self.session.flush()
        return call

    def test_retry_is_a_new_immutable_attempt_and_moves_to_next_window(self):
        first = self.failed_attempt()
        retry = _schedule_retry(first, self.campaign, self.session)
        self.session.commit()

        self.assertIsNotNone(retry)
        self.assertEqual(retry.previous_attempt_id, first.id)
        self.assertEqual(retry.attempt_number, 2)
        self.assertEqual(retry.status, CallStatus.queued)
        self.assertEqual(retry.scheduled_for, datetime(2026, 8, 4, 9, 0))
        stored_first = self.session.get(Call, first.id)
        self.assertEqual(stored_first.status, CallStatus.failed)
        self.assertEqual(stored_first.failure_reason, "The simulator call was not answered")

    def test_retry_is_bounded_and_respects_disabled_category(self):
        final_attempt = self.failed_attempt(number=3, maximum=3)
        self.assertIsNone(_schedule_retry(final_attempt, self.campaign, self.session))
        busy = self.failed_attempt(category="busy")
        self.assertIsNone(_schedule_retry(busy, self.campaign, self.session))
        self.assertEqual(len(self.session.scalars(select(Call)).all()), 2)

    def test_failure_categories_and_overnight_window(self):
        self.assertEqual(_failure_category(ThreeCXError("The line is busy")), "busy")
        self.assertEqual(_failure_category(ThreeCXError("The call was not answered")), "no_answer")
        self.assertEqual(_failure_category(ThreeCXError("3CX authentication failed")), "provider_failure")
        self.campaign.calling_window_json = {"start": "22:00", "end": "06:00"}
        requested = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(_next_allowed_time(self.campaign, requested), datetime(2026, 8, 3, 22, 0, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
