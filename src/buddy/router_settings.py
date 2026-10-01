"""Single Router settings and the explicit-upgrade conversion; no state access."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping

from .errors import BoardError
from .schemas import IDENTIFIER_PATTERN

FIELDS = frozenset({"routerProfileId", "defaultRoutingMode", "routingBudget"})
LEGACY_FIELDS = frozenset({"fastRouterProfileId", "reviewRouterProfileId", "defaultRoutingMode", "routingBudget"})
MODES = ("fast", "review")
PRESETS = ("brief", "standard", "deep")


@dataclass(frozen=True)
class RouterSettings:
    router_profile_id: str | None = None
    default_routing_mode: str = "fast"
    routing_budget: str = "standard"

    def as_dict(self) -> dict:
        return {"routerProfileId": self.router_profile_id,
                "defaultRoutingMode": self.default_routing_mode,
                "routingBudget": self.routing_budget}


@dataclass(frozen=True)
class RouterConversion:
    settings: RouterSettings
    discarded_profile_id: str | None


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
    if "routerProfileId" in entry:
        result["routerProfileId"] = _profile(entry["routerProfileId"], "routerProfileId")
    if "defaultRoutingMode" in entry:
        result["defaultRoutingMode"] = _choice(entry["defaultRoutingMode"], MODES, "defaultRoutingMode")
    if "routingBudget" in entry:
        result["routingBudget"] = _choice(entry["routingBudget"], PRESETS, "routingBudget")
    return result


def convert_legacy_router_settings(value: object) -> RouterConversion:
    """Preserve the default mode's slot even when empty or unavailable.

    Only L15's explicit upgrade may call this for production state. Eligibility
    cannot change the user's choice, and the other slot never becomes a fallback.
    """
    entry = _object(value, LEGACY_FIELDS, "legacy Router settings")
    mode = _choice(entry.get("defaultRoutingMode", "fast"), MODES, "defaultRoutingMode")
    preset = entry.get("routingBudget", "standard")
    preset = _choice("brief" if preset == "quick" else preset, PRESETS, "routingBudget")
    fast = _profile(entry.get("fastRouterProfileId"), "fastRouterProfileId")
    review = _profile(entry.get("reviewRouterProfileId"), "reviewRouterProfileId")
    chosen, discarded = (fast, review) if mode == "fast" else (review, fast)
    return RouterConversion(RouterSettings(chosen, mode, preset), discarded)
