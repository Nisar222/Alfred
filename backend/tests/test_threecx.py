import unittest
import json

import httpx

from app.config import Settings
from app.threecx import ThreeCXClient, parse_dtmf_event


class ThreeCXClientTests(unittest.TestCase):
    def setUp(self):
        ThreeCXClient._reset_token_cache()

    def tearDown(self):
        ThreeCXClient._reset_token_cache()

    def test_parses_only_dtmf_for_the_application_participant(self):
        message = '{"event":{"event_type":2,"entity":"/callcontrol/3cxapi/participants/72","attached_data":{"dtmf_input":"1"}}}'
        self.assertEqual(parse_dtmf_event(message, "3cxapi", 72), "1")
        self.assertIsNone(parse_dtmf_event(message, "3cxapi", 73))
        self.assertIsNone(parse_dtmf_event('{"event":{"eventType":0,"attachedData":"1"}}', "3cxapi", 72))

    def test_rejects_malformed_or_multi_digit_dtmf_events(self):
        self.assertIsNone(parse_dtmf_event("not-json", "3cxapi", 72))
        message = '{"eventType":"DTMFString","entity":"/callcontrol/3cxapi/participants/72","attachedData":{"response":{"digit":"12"}}}'
        self.assertIsNone(parse_dtmf_event(message, "3cxapi", 72))

    def settings(self):
        return Settings(
            threecx_base_url="https://pbx.example.test",
            threecx_app_id="3cxapi",
            threecx_api_key="test-secret",
            threecx_control_extension="101",
        )

    def test_lists_devices_after_client_credentials_authentication(self):
        def handler(request):
            if request.url.path == "/connect/token":
                self.assertEqual(request.method, "POST")
                return httpx.Response(200, json={"access_token": "temporary-token"})
            self.assertEqual(request.headers["Authorization"], "Bearer temporary-token")
            self.assertEqual(request.url.path, "/callcontrol/101/devices")
            return httpx.Response(200, json=[{"device_id": "device-1", "user_agent": "3CX Web Client"}])

        client = ThreeCXClient(self.settings(), transport=httpx.MockTransport(handler))
        try:
            devices = client.list_devices()
        finally:
            client.close()
        self.assertEqual(devices[0].device_id, "device-1")

    def test_starts_call_from_application_route_point(self):
        def handler(request):
            if request.url.path == "/connect/token":
                return httpx.Response(200, json={"access_token": "temporary-token"})
            self.assertEqual(request.headers["Authorization"], "Bearer temporary-token")
            self.assertEqual(request.url.path, "/callcontrol/3cxapi/makecall")
            self.assertEqual(json.loads(request.content), {"destination": "+15551234567", "timeout": 45})
            return httpx.Response(202, json={"result": {"id": 72}})

        client = ThreeCXClient(self.settings(), transport=httpx.MockTransport(handler))
        try:
            call = client.start_test_call("+15551234567")
        finally:
            client.close()
        self.assertEqual(call.participant_id, 72)
        self.assertEqual(call.initial_status, "not provided")

    def test_routes_application_participant_with_alfred_call_id(self):
        def handler(request):
            if request.url.path == "/connect/token":
                return httpx.Response(200, json={"access_token": "temporary-token"})
            self.assertEqual(request.url.path, "/callcontrol/3cxapi/participants/72/routeto")
            self.assertEqual(json.loads(request.content), {"destination": "801", "reason": "None", "timeout": 90})
            return httpx.Response(200, json={"finalstatus": "Succeeded"})

        client = ThreeCXClient(self.settings(), transport=httpx.MockTransport(handler))
        try:
            from app.threecx import ThreeCXTestCall
            client.route_to(ThreeCXTestCall(72, "+15551234567", "ok", "ok"), "801", 418)
        finally:
            client.close()

    def test_lists_paginated_xapi_users_with_safe_directory_fields(self):
        requests = []

        def handler(request):
            requests.append(request.url.path + (f"?{request.url.query.decode()}" if request.url.query else ""))
            if request.url.path == "/connect/token":
                return httpx.Response(200, json={"access_token": "temporary-token"})
            self.assertEqual(request.headers["Authorization"], "Bearer temporary-token")
            if request.url.path == "/xapi/v1/Users" and not request.url.query:
                return httpx.Response(200, json={"value": [{"Id": "7", "FirstName": "Ada", "LastName": "Lovelace", "Number": "101", "Email": "ada@example.test"}], "@odata.nextLink": "/xapi/v1/Users?$skip=1"})
            self.assertEqual(request.url.path, "/xapi/v1/Users")
            self.assertEqual(request.url.params["$skip"], "1")
            return httpx.Response(200, json={"value": [{"Id": "8", "Name": "Grace", "Extension": "102"}]})

        client = ThreeCXClient(self.settings(), transport=httpx.MockTransport(handler))
        try:
            users = client.list_xapi_users()
        finally:
            client.close()
        self.assertEqual([(user.user_id, user.name, user.extension, user.email) for user in users], [
            ("7", "Ada Lovelace", "101", "ada@example.test"), ("8", "Grace", "102", None),
        ])
        self.assertEqual(requests.count("/connect/token"), 1)

    def test_reuses_cached_token_across_repeated_polling_calls(self):
        from app.threecx import ThreeCXTestCall

        token_requests = []

        def handler(request):
            if request.url.path == "/connect/token":
                token_requests.append(1)
                return httpx.Response(200, json={"access_token": "temporary-token"})
            self.assertEqual(request.headers["Authorization"], "Bearer temporary-token")
            self.assertEqual(request.url.path, "/callcontrol/3cxapi")
            return httpx.Response(200, json={"participants": [{"id": 72, "status": "Connected"}]})

        client = ThreeCXClient(self.settings(), transport=httpx.MockTransport(handler))
        try:
            call = ThreeCXTestCall(72, "+15551234567", "ok", "ok")
            client.wait_until_connected(call)
            client.get_participant(call)
        finally:
            client.close()
        self.assertEqual(len(token_requests), 1)

    def test_retries_once_with_a_fresh_token_after_a_401(self):
        from app.threecx import ThreeCXTestCall

        token_requests = []
        callcontrol_attempts = []

        def handler(request):
            if request.url.path == "/connect/token":
                token_requests.append(1)
                return httpx.Response(200, json={"access_token": f"token-{len(token_requests)}"})
            callcontrol_attempts.append(request.headers["Authorization"])
            if len(callcontrol_attempts) == 1:
                return httpx.Response(401, json={"error": "invalid_token"})
            self.assertEqual(request.headers["Authorization"], "Bearer token-2")
            return httpx.Response(200, json={"participants": [{"id": 72, "status": "Connected"}]})

        client = ThreeCXClient(self.settings(), transport=httpx.MockTransport(handler))
        try:
            call = ThreeCXTestCall(72, "+15551234567", "ok", "ok")
            client.wait_until_connected(call)
        finally:
            client.close()
        self.assertEqual(len(token_requests), 2)
        self.assertEqual(len(callcontrol_attempts), 2)

    def test_shares_one_token_across_client_instances(self):
        """A second client (e.g. the background recording sync) must reuse the
        shared token instead of minting a new one. Minting a second token would
        make 3CX revoke the first, which is what broke in-progress calls."""
        from app.threecx import ThreeCXTestCall

        a_tokens: list[int] = []
        b_tokens: list[int] = []

        def make_handler(counter: list[int]):
            def handler(request):
                if request.url.path == "/connect/token":
                    counter.append(1)
                    return httpx.Response(200, json={"access_token": "shared-token", "expires_in": 3600})
                self.assertEqual(request.headers["Authorization"], "Bearer shared-token")
                return httpx.Response(200, json={"participants": [{"id": 72, "status": "Connected"}]})
            return handler

        call = ThreeCXTestCall(72, "+15551234567", "ok", "ok")
        client_a = ThreeCXClient(self.settings(), transport=httpx.MockTransport(make_handler(a_tokens)))
        client_b = ThreeCXClient(self.settings(), transport=httpx.MockTransport(make_handler(b_tokens)))
        try:
            client_a.get_participant(call)  # first use mints the shared token
            client_b.get_participant(call)  # a separate client reuses it, no new mint
        finally:
            client_a.close()
            client_b.close()
        self.assertEqual(len(a_tokens), 1)
        self.assertEqual(len(b_tokens), 0)

    def test_monitor_records_every_observed_dtmf_digit(self):
        from app.threecx import ThreeCXDtmfMonitor, ThreeCXTestCall

        client = ThreeCXClient(self.settings(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        monitor = ThreeCXDtmfMonitor(client, ThreeCXTestCall(72, "+15551234567", "ok", "ok"))

        class FakeConnection:
            def __init__(self, messages):
                self._messages = list(messages)

            def recv(self, timeout=None):
                if self._messages:
                    return self._messages.pop(0)
                raise TimeoutError()

            def close(self):
                pass

        monitor.connection = FakeConnection([
            '{"event":{"event_type":2,"entity":"/callcontrol/3cxapi/participants/72","attached_data":{"dtmf_input":"1"}}}',
            '{"event":{"event_type":2,"entity":"/callcontrol/3cxapi/participants/72","attached_data":{"dtmf_input":"2"}}}',
        ])
        try:
            first = monitor.poll(timeout_seconds=0.1)
            second = monitor.poll(timeout_seconds=0.1)
            empty = monitor.poll(timeout_seconds=0.1)
        finally:
            client.close()

        self.assertEqual((first, second, empty), ("1", "2", None))
        self.assertEqual([event["digit"] for event in monitor.observed_digits], ["1", "2"])
        self.assertTrue(all("at" in event for event in monitor.observed_digits))

    def test_resolves_ring_group_and_queue_members_to_extensions(self):
        def handler(request):
            if request.url.path == "/connect/token":
                return httpx.Response(200, json={"access_token": "temporary-token"})
            if request.url.path == "/xapi/v1/Users":
                return httpx.Response(200, json={"value": [{"Id": "7", "Name": "Ada", "Number": "101"}]})
            if request.url.path == "/xapi/v1/RingGroups":
                return httpx.Response(200, json={"value": [{"Id": "rg-803", "Number": "803", "Name": "Alfred", "Members": [{"UserId": "7"}]}]})
            self.assertEqual(request.url.path, "/xapi/v1/Queues")
            return httpx.Response(200, json={"value": [{"Id": "queue-800", "Number": "800", "Name": "Sales", "Agents": [{"Id": "7", "Extension": "101"}]}]})

        client = ThreeCXClient(self.settings(), transport=httpx.MockTransport(handler))
        try:
            users, ring_groups, queues = client.list_xapi_directory()
            single_member = client.single_member_extension("803")
        finally:
            client.close()
        self.assertEqual(users[0].extension, "101")
        self.assertEqual((ring_groups[0].extension, ring_groups[0].members[0].user_id, ring_groups[0].members[0].extension), ("803", "7", "101"))
        self.assertEqual((queues[0].extension, queues[0].members[0].user_id, queues[0].members[0].extension), ("800", "7", "101"))
        self.assertEqual(single_member, "101")
