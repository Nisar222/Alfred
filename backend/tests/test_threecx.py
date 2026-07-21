import unittest
import json

import httpx

from app.config import Settings
from app.threecx import ThreeCXClient


class ThreeCXClientTests(unittest.TestCase):
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
