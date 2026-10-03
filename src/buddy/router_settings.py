"""Ordered Router list settings and the explicit-upgrade conversion; no state access."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .errors import BoardError
from .schemas import IDENTIFIER_PATTERN

FIELDS = frozenset({"routerProfileIds", "routerRetryIntervalSeconds", "defaultRoutingMode", "routingBudget"})
LEGACY_FIELDS = frozenset({"fastRouterProfileId", "reviewRouterProfileId", "defaultRoutingMode", "routingBudget"})
MODES = ("fast", "review")
PRESETS = ("brief", "standard", "deep")
RETRY_INTERVAL_MIN_SECONDS = 1
RETRY_INTERVAL_MAX_SECONDS = 2147483647


@dataclass(frozen=True)
class RouterSettings:
    router_profile_ids: tuple[str, ...] = ()
    router_retry_interval_seconds: int = 600
    default_routing_mode: str = "fast"
    routing_budget: str = "standard"

    def __post_init__(self) -> None:
        if not isinstance(self.router_profile_ids, tuple):
            raise BoardError("INVALID_ARGUMENT", "router_profile_ids must be a tuple of profileIds")
        _profile_entries(self.router_profile_ids, "router_profile_ids")
        _retry_interval(self.router_retry_interval_seconds, "router_retry_interval_seconds")
        _choice(self.default_routing_mode, MODES, "default_routing_mode")
        _choice(self.routing_budget, PRESETS, "routing_budget")

    def as_dict(self) -> dict:
        return {"routerProfileIds": list(self.router_profile_ids),
                "routerRetryIntervalSeconds": self.router_retry_interval_seconds,
                "defaultRoutingMode": self.default_routing_mode,
                "routingBudget": self.routing_budget}


@dataclass(frozen=True)
class RouterConversion:
    settings: RouterSettings
    source_slots: tuple[str | None, str | None]


def _object(value: object, fields: frozenset[str], label: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise BoardError("INVALID_ARGUMENT", f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise BoardError("INVALID_ARGUMENT", f"{label} field names must be strings")
    unknown = set(value) - fields
    if unknown:
        raise BoardError("INVALID_ARGUMENT", f"Unknown {label} field: {sorted(unknown)[0]}")
    return value


def _profile(value: object, name: str) -> str | None:
    if value is not None and (not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value)):
        raise BoardError("INVALID_ARGUMENT", f"{name} must be a profileId or null")
    return value


def _profile_entries(ids: Sequence[object], name: str) -> None:
    seen: set[str] = set()
    for profile_id in ids:
        if not isinstance(profile_id, str) or not IDENTIFIER_PATTERN.fullmatch(profile_id):
            raise BoardError("INVALID_ARGUMENT", f"{name} entries must be profileIds")
        if profile_id in seen:
            raise BoardError("INVALID_ARGUMENT", f"{name} entries must be unique")
        seen.add(profile_id)


def _profile_array(value: object, name: str) -> list[str]:
    if not isinstance(value, list):
        raise BoardError("INVALID_ARGUMENT", f"{name} must be an array of profileIds")
    _profile_entries(value, name)
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
    if "routerProfileIds" in entry:
        result["routerProfileIds"] = _profile_array(entry["routerProfileIds"], "routerProfileIds")
    if "routerRetryIntervalSeconds" in entry:
        result["routerRetryIntervalSeconds"] = _retry_interval(entry["routerRetryIntervalSeconds"],
                                                               "routerRetryIntervalSeconds")
    if "defaultRoutingMode" in entry:
        result["defaultRoutingMode"] = _choice(entry["defaultRoutingMode"], MODES, "defaultRoutingMode")
    if "routingBudget" in entry:
        result["routingBudget"] = _choice(entry["routingBudget"], PRESETS, "routingBudget")
    return result


def convert_legacy_router_settings(value: object) -> RouterConversion:
    """Convert dual-slot legacy settings into the ordered list; no state access.

    Only L15's explicit upgrade may call this for production state. The default
    mode's slot leads and the other slot follows it even when unavailable, so no
    user-entered ID is dropped; source_slots keeps the two raw slots in fixed
    fast/review order for audit.
    """
    entry = _object(value, LEGACY_FIELDS, "legacy Router settings")
    mode = _choice(entry.get("defaultRoutingMode", "fast"), MODES, "defaultRoutingMode")
    preset = entry.get("routingBudget", "standard")
    preset = _choice("brief" if preset == "quick" else preset, PRESETS, "routingBudget")
    fast = _profile(entry.get("fastRouterProfileId"), "fastRouterProfileId")
    review = _profile(entry.get("reviewRouterProfileId"), "reviewRouterProfileId")
    ordered = (fast, review) if mode == "fast" else (review, fast)
    ids: list[str] = []
    for slot in ordered:
        if slot is not None and slot not in ids:
            ids.append(slot)
    return RouterConversion(RouterSettings(tuple(ids), default_routing_mode=mode, routing_budget=preset),
                            (fast, review))
