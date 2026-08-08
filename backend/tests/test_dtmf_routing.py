import unittest

from app.dtmf_routing import (
    effective_dtmf_routes,
    normalize_dtmf_routes,
    resolve_dtmf_destination,
    sync_legacy_dtmf_fields,
)


class NormalizeDtmfRoutesTests(unittest.TestCase):
    def test_keeps_only_valid_digit_keys_and_extensions(self):
        routes = normalize_dtmf_routes(
            {"1": "801", "2": "802", "10": "900", "x": "801", "3": "x", "4": ""},
        )
        self.assertEqual(routes, {"1": "801", "2": "802"})

    def test_falls_back_to_legacy_single_route(self):
        routes = normalize_dtmf_routes({}, legacy_digit="5", legacy_ext="805")
        self.assertEqual(routes, {"5": "805"})

    def test_legacy_fallback_ignored_when_routes_present(self):
        routes = normalize_dtmf_routes({"1": "801"}, legacy_digit="2", legacy_ext="802")
        self.assertEqual(routes, {"1": "801"})


class ResolveDtmfDestinationTests(unittest.TestCase):
    def test_returns_extension_for_configured_digit(self):
        routes = {"0": "800", "9": "809"}
        self.assertEqual(resolve_dtmf_destination("0", routes), "800")
        self.assertEqual(resolve_dtmf_destination("9", routes), "809")

    def test_returns_none_for_unconfigured_or_invalid_digit(self):
        routes = {"1": "801"}
        self.assertIsNone(resolve_dtmf_destination("2", routes))
        self.assertIsNone(resolve_dtmf_destination(None, routes))
        self.assertIsNone(resolve_dtmf_destination("12", routes))


class EffectiveDtmfRoutesTests(unittest.TestCase):
    def test_merges_legacy_fields_when_json_empty(self):
        class Settings:
            dtmf_routes_json = {}
            dtmf_menu_digit = "3"
            dtmf_queue_extension = "803"

        self.assertEqual(effective_dtmf_routes(Settings()), {"3": "803"})

    def test_prefers_stored_json_over_legacy(self):
        class Settings:
            dtmf_routes_json = {"1": "801", "2": "802"}
            dtmf_menu_digit = "9"
            dtmf_queue_extension = "809"

        self.assertEqual(effective_dtmf_routes(Settings()), {"1": "801", "2": "802"})


class SyncLegacyDtmfFieldsTests(unittest.TestCase):
    def test_syncs_first_route_in_digit_order(self):
        class Settings:
            dtmf_routes_json = {"2": "802", "0": "800", "9": "809"}
            dtmf_menu_digit = "1"
            dtmf_queue_extension = None

        settings = Settings()
        sync_legacy_dtmf_fields(settings)
        self.assertEqual(settings.dtmf_menu_digit, "0")
        self.assertEqual(settings.dtmf_queue_extension, "800")


if __name__ == "__main__":
    unittest.main()
