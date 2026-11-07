"""Ordered Router list settings; no state access."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from ...errors import BoardError
from ...protocol.schemas import IDENTIFIER_PATTERN
from ..store.schema_37_38 import DEFAULT_MODEL_RESPONSE_WAIT_SECONDS, MAX_MODEL_RESPONSE_WAIT_SECONDS

FIELDS = frozenset({"routerBuddyIds", "routerRetryIntervalSeconds", "defaultRoutingMode", "modelResponseWaitSeconds",
                    "maintenanceBuddyId", "maintenanceIntervalSeconds", "profileBuddyId", "explorationEvery"})
MODES = ("fast", "review")
RETRY_INTERVAL_MIN_SECONDS = 1
RETRY_INTERVAL_MAX_SECONDS = 2147483647
EXPLORATION_EVERY_MIN = 2


DEFAULT_ROUTER_SETTINGS = {"routerBuddyIds": [], "routerRetryIntervalSeconds": 600,
                           "defaultRoutingMode": "fast", "modelResponseWaitSeconds": DEFAULT_MODEL_RESPONSE_WAIT_SECONDS,
                           "maintenanceBuddyId": None, "maintenanceIntervalSeconds": 3600,
                           "profileBuddyId": None, "explorationEvery": 5}


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
    if "modelResponseWaitSeconds" in entry:
        seconds = entry["modelResponseWaitSeconds"]
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= MAX_MODEL_RESPONSE_WAIT_SECONDS:
            raise BoardError("INVALID_ARGUMENT",
                             f"modelResponseWaitSeconds must be between 1 and {MAX_MODEL_RESPONSE_WAIT_SECONDS} seconds")
        result["modelResponseWaitSeconds"] = seconds
    if "maintenanceBuddyId" in entry:
        buddy_id = entry["maintenanceBuddyId"]
        if buddy_id is not None:
            _buddy_entries([buddy_id], "maintenanceBuddyId")
        result["maintenanceBuddyId"] = buddy_id
    if "profileBuddyId" in entry:
        buddy_id = entry["profileBuddyId"]
        if buddy_id is not None:
            _buddy_entries([buddy_id], "profileBuddyId")
        result["profileBuddyId"] = buddy_id
    if "maintenanceIntervalSeconds" in entry:
        seconds = entry["maintenanceIntervalSeconds"]
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 1:
            raise BoardError("INVALID_ARGUMENT", "maintenanceIntervalSeconds must be a positive integer")
        result["maintenanceIntervalSeconds"] = seconds
    if "explorationEvery" in entry:
        every = entry["explorationEvery"]
        if every is not None and (isinstance(every, bool) or not isinstance(every, int)
                                  or every < EXPLORATION_EVERY_MIN):
            raise BoardError("INVALID_ARGUMENT",
                             f"explorationEvery must be null (exploration off) or an integer"
                             f" of at least {EXPLORATION_EVERY_MIN}")
        result["explorationEvery"] = every
    return result
