import unittest

from app.call_analysis import analyze_sentiment, generate_call_summary
from app.models import Call, CallStatus, Sentiment, Transcript


class CallAnalysisTests(unittest.TestCase):
    def test_generate_call_summary_detects_interest(self):
        text = "Agent: Thanks for your time.\nCustomer: This sounds good. Can we schedule next week?"
        summary = generate_call_summary(text, prospect_name="Shivangi")
        self.assertIn("follow-up", summary.lower())

    def test_analyze_sentiment_positive(self):
        call = Call(
            phone="+15551234567",
            status=CallStatus.completed,
            configuration_snapshot_json={},
        )
        call._transcript = Transcript(content="Customer: Yes, that sounds good. Thank you.", summary="")
        call.transcript = call._transcript.content
        analyze_sentiment(call)
        self.assertEqual(call.sentiment, Sentiment.positive)
        self.assertEqual(call.sentiment_source, "transcript-v1")

    def test_analyze_sentiment_unknown_without_transcript(self):
        call = Call(phone="+15551234567", status=CallStatus.completed, configuration_snapshot_json={})
        analyze_sentiment(call)
        self.assertEqual(call.sentiment, Sentiment.unknown)


if __name__ == "__main__":
    unittest.main()
