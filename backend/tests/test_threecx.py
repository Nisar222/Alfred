import unittest
import json

import httpx

from app.config import Settings
from app.threecx import ThreeCXClient, parse_dtmf_event


class ThreeCXClientTests(unittest.TestCase):
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
            self.assertEqual(json.loads(request.content), {"destination": "801"})
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
