"""Ordered Router list settings; no state access."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from ...errors import BoardError
from ...protocol.schemas import IDENTIFIER_PATTERN

FIELDS = frozenset({"routerBuddyIds", "routerRetryIntervalSeconds", "defaultRoutingMode", "routingBudget"})
MODES = ("fast", "review")
PRESETS = ("brief", "standard", "deep")
RETRY_INTERVAL_MIN_SECONDS = 1
RETRY_INTERVAL_MAX_SECONDS = 2147483647


DEFAULT_ROUTER_SETTINGS = {"routerBuddyIds": [], "routerRetryIntervalSeconds": 600,
                           "defaultRoutingMode": "fast", "routingBudget": "standard"}


def _object(value: object, fields: frozenset[str], label: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise BoardError("INVALID_ARGUMENT", f"{label} must be an object")
    unknown = set(value) - fields
    if unknown:
        raise BoardError("INVALID_ARGUMENT", f"Unknown {label} field: {sorted(unknown)[0]}")
    return value




def _buddy_entries(ids: Sequence[object], name: str) -> None:
    seen: set[str] = set()
    for buddy_id in ids:
        if not isinstance(buddy_id, str) or not IDENTIFIER_PATTERN.fullmatch(buddy_id):
            raise BoardError("INVALID_ARGUMENT", f"{name} entries must be buddyIds")
        if buddy_id in seen:
            raise BoardError("INVALID_ARGUMENT", f"{name} entries must be unique")
        seen.add(buddy_id)


def _buddy_array(value: object, name: str) -> list[str]:
    if not isinstance(value, list):
        raise BoardError("INVALID_ARGUMENT", f"{name} must be an array of buddyIds")
    _buddy_entries(value, name)
    return list(value)


def _retry_interval(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BoardError("INVALID_ARGUMENT", f"{name} must be an integer number of seconds")
    if not RETRY_INTERVAL_MIN_SECONDS <= value <= RETRY_INTERVAL_MAX_SECONDS:
        raise BoardError("INVALID_ARGUMENT",
                         f"{name} must be between {RETRY_INTERVAL_MIN_SECONDS} and "
                         f"{RETRY_INTERVAL_MAX_SECONDS} seconds")
    return value


def _choice(value: object, allowed: tuple[str, ...], name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise BoardError("INVALID_ARGUMENT", f"{name} must be one of {', '.join(allowed)}")
    return value


def validate_router_settings_patch(value: object) -> dict:
    """Validate a current field patch without supplying omitted values."""
    entry = _object(value, FIELDS, "configuration")
    if not entry:
        raise BoardError("INVALID_ARGUMENT", "configuration requires a Router setting")
    result = {}
    if "routerBuddyIds" in entry:
        result["routerBuddyIds"] = _buddy_array(entry["routerBuddyIds"], "routerBuddyIds")
    if "routerRetryIntervalSeconds" in entry:
        result["routerRetryIntervalSeconds"] = _retry_interval(entry["routerRetryIntervalSeconds"],
                                                               "routerRetryIntervalSeconds")
    if "defaultRoutingMode" in entry:
        result["defaultRoutingMode"] = _choice(entry["defaultRoutingMode"], MODES, "defaultRoutingMode")
    if "routingBudget" in entry:
        result["routingBudget"] = _choice(entry["routingBudget"], PRESETS, "routingBudget")
    return result
