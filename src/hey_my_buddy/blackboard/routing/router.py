"""Harness-neutral Router settings, the ordered-list resolution entry and answer bounds."""
from __future__ import annotations

import dataclasses
import json

from ...protocol.reasons import reason, reason_from_json

CONFIG_COLUMNS = {
    "routerRetryIntervalSeconds": "retry_interval_seconds",
    "defaultRoutingMode": "default_mode",
    "modelResponseWaitSeconds": "model_response_wait_seconds",
    "maintenanceBuddyId": "maintenance_buddy_id",
    "maintenanceIntervalSeconds": "maintenance_interval_seconds",
    "explorationEvery": "exploration_every",
    "profileBuddyId": "profile_buddy_id",
}

def _identity(row) -> dict:
    """Copy the published complete name of one buddy row."""
    from ...protocol.schemas import BUDDY_FIELDS
    return {key: row[key] for key in BUDDY_FIELDS}


def buddy_problem(connection, buddy_id: str, routing_mode: str, *, now=None) -> tuple[dict | None, str | None, dict]:
    """Cached eligibility of one listed buddy: no probing, model calls or settings mutation.

    Enabled, availability, harness health, provider cooling and the mode mechanism are
    checked from cached records only; the caller owns the settings read, so a frozen
    snapshot can keep checking its own list without re-reading shared settings.
    """
    from ..service.harness_health import read_health
    def unavailable(code, **facts):
        return None, code, facts

    from ..catalog import buddy_state
    row = buddy_state.get(connection, buddy_id)
    if row is None:
        return unavailable("router-catalog-unavailable")
    if not row["enabled"]:
        return unavailable("router-disabled")
    if not row["catalog_available"]:
        cause = reason_from_json(row["unavailable_code"], row["unavailable_params_json"])
        return unavailable("router-catalog-unavailable", **({"cause": cause} if cause else {}))
    health = read_health(connection, row["harness"])
    if health["status"] != "ready":
        return unavailable("router-harness-unavailable", **({"cause": health["reason"]} if health.get("reason") else {}))
    from .quota_routing import cooling
    cooldown = cooling(connection, row, now=now)
    if cooldown is not None:
        return unavailable("router-quota-exhausted", retryAt=cooldown["coolingUntil"])
    from ...protocol.capabilities import PERMISSION_LEVELS, ROUTER_SNAPSHOT_QUERY_HARNESSES
    level = {"fast": "none", "review": "read", "profile": "web"}[routing_mode]
    if level == "web" and PERMISSION_LEVELS[row["harness"]].get("web") != "enforced":
        return unavailable("profile-web-unsupported")
    snapshots_required = routing_mode in ("fast", "review")
    if (level not in PERMISSION_LEVELS[row["harness"]]
            or snapshots_required and row["harness"] not in ROUTER_SNAPSHOT_QUERY_HARNESSES):
        code = "router-no-tool-unsupported" if routing_mode == "fast" else "router-review-unsupported"
        return unavailable(code)
    return dict(row), None, {}


def _write_settings(connection, settings: dict) -> None:
    """Store validated settings; omitted keys keep theirs."""
    columns = [column for name, column in CONFIG_COLUMNS.items() if name in settings]
    if columns:
        connection.execute("UPDATE router_settings SET " + ",".join(column + "=?" for column in columns) + " WHERE id=1",
                           [settings[name] for name in CONFIG_COLUMNS if name in settings])
    if "routerBuddyIds" in settings:
        connection.execute("DELETE FROM router_buddies")
        connection.executemany("INSERT INTO router_buddies(position,buddy_id) VALUES(?,?)",
                               enumerate(settings["routerBuddyIds"]))


def configuration(connection) -> dict:
    """Read the current Router settings."""
    row = connection.execute("SELECT * FROM router_settings WHERE id=1").fetchone()
    return {
        "routerBuddyIds": [row[0] for row in connection.execute("SELECT buddy_id FROM router_buddies ORDER BY position")],
        "routerRetryIntervalSeconds": row["retry_interval_seconds"],
        "defaultRoutingMode": row["default_mode"],
        "modelResponseWaitSeconds": row["model_response_wait_seconds"],
        "maintenanceBuddyId": row["maintenance_buddy_id"],
        "maintenanceIntervalSeconds": row["maintenance_interval_seconds"],
        "explorationEvery": row["exploration_every"],
        "profileBuddyId": row["profile_buddy_id"],
    }


def initialize_configuration(connection) -> dict:
    """Initialize a fresh board's Router defaults."""
    from .router_settings import DEFAULT_ROUTER_SETTINGS
    settings = {**DEFAULT_ROUTER_SETTINGS,
                "routerBuddyIds": list(DEFAULT_ROUTER_SETTINGS["routerBuddyIds"])}
    # The model response wait and other omitted settings take the schema defaults.
    connection.execute("INSERT INTO router_settings(id,default_mode,retry_interval_seconds) VALUES(1,?,?)",
                       (settings["defaultRoutingMode"], settings["routerRetryIntervalSeconds"]))
    return settings


@dataclasses.dataclass(frozen=True)
class RouterResolution:
    """One read-only ordered-list resolution.

    ``buddy`` is a copy of the current published row; ``buddyId``/``routerIndex``
    are its public projections. ``facts`` freezes the ordered IDs with their complete
    identity snapshot plus the global mode, model response wait and retry interval.
    ``inspections`` walks the candidates examined in order and ends with the
    selected one when a Router was found; each entry carries eligibility, the skip
    reason with ``retryAt``, and the live ``retryInProgress`` fact. ``problem`` is
    the reason no Router was resolved; a frozen continuation never re-selects mode
    or candidates and never follows later changes to the shared settings.
    """

    buddy: dict | None
    buddy_id: str | None
    router_index: int | None
    facts: dict
    inspections: tuple[dict, ...]
    problem: dict | None


def _snapshot_facts(connection, settings: dict) -> dict:
    """The frozen facts of one settings snapshot: ordered IDs, identities, mode, wait."""
    identities = []
    for buddy_id in settings["routerBuddyIds"]:
        row = connection.execute("SELECT harness,provider,model,effort FROM buddies WHERE buddy_id=?",
                                 (buddy_id,)).fetchone()
        identities.append(_identity(row))
    return {
        "routerBuddyIds": list(settings["routerBuddyIds"]),
        "routerIdentities": identities,
        "routingMode": settings["defaultRoutingMode"],
        "modelResponseWaitSeconds": settings["modelResponseWaitSeconds"],
        "routerRetryIntervalSeconds": settings["routerRetryIntervalSeconds"],
    }


def _frozen_facts(frozen: dict) -> dict:
    """Copy service-frozen Router facts including the request's own response wait."""
    return {
        "routerBuddyIds": list(frozen["routerBuddyIds"]),
        "routerIdentities": [dict(value) for value in frozen["routerIdentities"]],
        "routingMode": frozen["routingMode"],
        "modelResponseWaitSeconds": frozen["modelResponseWaitSeconds"],
        "routerRetryIntervalSeconds": frozen["routerRetryIntervalSeconds"],
    }


def current_router(connection, *, frozen: dict | None = None, after_index: int = -1,
                   now: str | None = None) -> RouterResolution:
    """Read-only resolution of the current buddy for the routing plane.

    Walks the ordered settings list from ``after_index + 1`` strictly forward and
    returns the first eligible buddy that is outside its skip window. Reading never
    writes: skip windows, retry-in-progress facts and per-buddy streaks are projected
    from recorded facts only, and a still-unavailable preflight is recorded by the
    caller, never here.

    ``frozen`` continues one request inside its own snapshot: the frozen list,
    identities, mode, response wait and interval are used unchanged and later
    changes to the shared settings are neither read nor adopted. ``now`` defaults
    to the board clock; eligibility reuses cached harness
    health, the local mode mechanism and provider cooling, and never ranks by
    Worker preference.
    """
    from .router_history import router_state
    if frozen is not None:
        # A frozen continuation lives only inside its own snapshot: it never
        # re-reads the shared settings, never re-selects the mode and never adopts
        # later changes.
        facts = _frozen_facts(frozen)
    else:
        settings = configuration(connection)
        facts = _snapshot_facts(connection, settings)
    interval = facts["routerRetryIntervalSeconds"]
    mode = facts["routingMode"]
    ids = facts["routerBuddyIds"]
    inspections: list[dict] = []
    for index in range(after_index + 1, len(ids)):
        buddy_id = ids[index]
        identity = facts["routerIdentities"][index]
        state = router_state(connection, buddy_id=buddy_id, interval_seconds=interval, now=now)
        if state["inSkipWindow"]:
            inspections.append({"index": index, "buddyId": buddy_id, "identity": identity,
                                "eligible": False, "code": "router-skip-window",
                                "skipUntil": state["skipUntil"], "retryAt": state["retryAt"],
                                "consecutiveNoAnswers": state["consecutiveNoAnswers"],
                                "retryInProgress": state["retryInProgress"], "selected": False})
            continue
        buddy, code, problem_facts = buddy_problem(connection, buddy_id, mode, now=now)
        if buddy is None:
            inspections.append({"index": index, "buddyId": buddy_id, "identity": identity,
                                "eligible": False, "code": code,
                                "skipUntil": state["skipUntil"], "retryAt": state["retryAt"], **problem_facts,
                                "consecutiveNoAnswers": state["consecutiveNoAnswers"],
                                "retryInProgress": state["retryInProgress"], "selected": False})
            continue
        if frozen is not None:
            buddy = {**buddy, **identity}
        inspections.append({"index": index, "buddyId": buddy_id, "identity": identity,
                            "eligible": True, "code": None,
                            "skipUntil": state["skipUntil"], "retryAt": state["retryAt"],
                            "consecutiveNoAnswers": state["consecutiveNoAnswers"],
                            "retryInProgress": state["retryInProgress"], "selected": True})
        return RouterResolution(buddy, buddy_id, index, facts, tuple(inspections), None)
    if not ids:
        problem = reason("router-not-configured")
    elif after_index + 1 >= len(ids):
        problem = reason("router-list-exhausted")
    else:
        problem = reason("router-all-unavailable")
    return RouterResolution(None, None, None, facts, tuple(inspections), problem)


def selection_source(request: dict) -> str:
    """The recorded selection source of one routing request.

    ``model-selection`` is the Router path; ``single-candidate`` marks the
    program's direct selection of the sole frozen legal candidate, recorded as
    ``routerCalled: false`` on that request; ``no-candidate`` marks the Host
    boundary a request stops at when its own frozen ``routingBasis`` records
    zero legal candidates, so no Router task or model call exists. A record
    from before program selection existed keeps the Router-path marker; this
    does not claim a model ran, and a record without the frozen basis is never
    relabeled from later state.
    """
    if (request.get("routingBasis") or {}).get("candidateCount") == 0:
        return "no-candidate"
    if request.get("routerCalled") is False:
        return "single-candidate"
    return "model-selection"
