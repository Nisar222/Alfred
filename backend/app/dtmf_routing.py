"""Helpers for multi-digit DTMF keypad routing."""

import re

EXTENSION_PATTERN = re.compile(r"^\d{2,10}$")
VALID_DIGITS = tuple(str(d) for d in range(10))


def normalize_dtmf_routes(
    routes: dict[str, str] | None,
    legacy_digit: str | None = None,
    legacy_ext: str | None = None,
) -> dict[str, str]:
    """Return digit -> extension map with only valid 0-9 keys and 2-10 digit extensions."""
    normalized: dict[str, str] = {}
    for key, value in (routes or {}).items():
        digit = str(key).strip()
        ext = str(value or "").strip()
        if digit in VALID_DIGITS and ext and EXTENSION_PATTERN.match(ext):
            normalized[digit] = ext
    if not normalized and legacy_digit and legacy_ext:
        digit = str(legacy_digit).strip()
        ext = str(legacy_ext).strip()
        if digit in VALID_DIGITS and ext and EXTENSION_PATTERN.match(ext):
            normalized[digit] = ext
    return normalized


def resolve_dtmf_destination(digit: str | None, routes: dict[str, str]) -> str | None:
    """Return the extension for a captured DTMF digit, if configured."""
    if digit is None:
        return None
    key = str(digit).strip()
    if key not in VALID_DIGITS:
        return None
    return routes.get(key)


def effective_dtmf_routes(settings) -> dict[str, str]:
    """Merge stored routes with legacy single-digit fields when routes are empty."""
    stored = getattr(settings, "dtmf_routes_json", None) or {}
    return normalize_dtmf_routes(
        stored,
        legacy_digit=getattr(settings, "dtmf_menu_digit", None),
        legacy_ext=getattr(settings, "dtmf_queue_extension", None),
    )


def sync_legacy_dtmf_fields(settings) -> None:
    """Keep legacy single-route columns aligned with the first configured route."""
    routes = normalize_dtmf_routes(getattr(settings, "dtmf_routes_json", None) or {})
    if routes:
        for digit in VALID_DIGITS:
            if digit in routes:
                settings.dtmf_menu_digit = digit
                settings.dtmf_queue_extension = routes[digit]
                return
    if not getattr(settings, "dtmf_menu_digit", None):
        settings.dtmf_menu_digit = "1"


def first_dtmf_route_destination(settings) -> str | None:
    """Return the first configured route destination in digit order 0-9."""
    routes = effective_dtmf_routes(settings)
    for digit in VALID_DIGITS:
        if digit in routes:
            return routes[digit]
    return getattr(settings, "dtmf_queue_extension", None)
