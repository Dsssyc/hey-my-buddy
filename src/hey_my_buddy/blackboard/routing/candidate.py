"""Current hard bounds shared by candidate scans and routing projections."""
from __future__ import annotations

import json

from ...protocol import schemas
from ...protocol.capabilities import buddy_capabilities
from .quota_routing import cooling


def candidate_bound(connection, candidate, request, now, *, frozen=None,
                    require_health=True, apply_preferences=True, check_cooling=True):
    if frozen is not None and any(candidate[key] != frozen[key] for key in schemas.BUDDY_FIELDS):
        return "identity-changed"
    if not candidate["enabled"]:
        return "disabled"
    if not candidate["available"]:
        return "unavailable"
    if request.get("codingOnly", True) and candidate["harness"] not in schemas.CODING_HARNESSES:
        return "constraints"
    if any(candidate[key] != value for key, value in request.get("constraints", {}).items()):
        return "constraints"
    capabilities = buddy_capabilities(candidate["harness"], json.loads(candidate["capabilities_json"]))
    if not set(request.get("requiredCapabilities", [])) <= set(capabilities):
        return "capability-missing"
    if apply_preferences:
        preference = connection.execute("SELECT mode FROM evaluation_preferences WHERE buddy_id=?",
                                        (candidate["buddy_id"],)).fetchone()
        if preference and preference[0] == "exclude":
            return "excluded"
        pins = connection.execute("SELECT 1 FROM evaluation_preferences WHERE mode='pin' LIMIT 1").fetchone()
        if pins and (not preference or preference[0] != "pin"):
            return "not-pinned"
    if require_health:
        health = connection.execute("SELECT status FROM harness_health WHERE harness=?", (candidate["harness"],)).fetchone()
        if health is None or health[0] != "ready":
            return "harness-unavailable"
    if check_cooling and cooling(connection, candidate, now=now) is not None:
        return "quota-exhausted"
    return None
