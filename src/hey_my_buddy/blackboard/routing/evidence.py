"""Read-time routing facts from the blackboard's existing records.

Outcomes belong to a buddy kind; infrastructure failures belong to the full
buddy identity. Each counted sample adds the complete harness-reported usage of
its ability turns. Load uses the same unresolved attempts and effective queued
model identities as admission, including Router executions.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import heapq
import json
from statistics import median

from ...protocol.billing import for_provider
from ...protocol.turn_input import turn_buddy

NEITHER = frozenset({"service-stop", "console-stop", "transport-error",
                     "workspace-error", "unknown-stop", "interrupted"})
PROVIDER_REJECTIONS = frozenset({"overloaded", "quota-exceeded", "rate-limited"})
IDENTITY = ("harness", "provider", "model", "effort")
KIND = ("harness", "model", "effort")
USAGE_FIELDS = ("inputTokens", "cachedInputTokens", "outputTokens")


def round_classification(receipt: dict | None) -> str:
    """The single classification of an ended turn: ability, infrastructure, neither.

    Product/user-side termination takes precedence over native failure facts.
    A never-spawned release has no receipt and is also product-side. Other
    endings are ability evidence except structured provider rejections and an
    unavailable harness. No inference from diagnostics or modelStarted occurs.
    """
    if receipt is None or receipt["terminationReason"] in NEITHER:
        return "neither"
    result = receipt["result"] or {}
    rejection = result.get("providerRejection") or {}
    if result.get("code") == "harness-unavailable" or rejection.get("code") in PROVIDER_REJECTIONS:
        return "infrastructure"
    return "ability"


def _key(buddy, fields):
    return tuple(buddy[field] for field in fields)


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _usage_totals(turns):
    """One sample's summed usage, or None unless every ability turn is complete."""
    if any((turn["usage"] or {}).get("completeness") != "complete" for turn in turns):
        return None
    return tuple(sum(turn["usage"][field] for turn in turns) for field in USAGE_FIELDS)


def _usage_fact(usages):
    return {"samples": len(usages),
            "medianInputTokens": median([item[0] for item in usages]) if usages else None,
            "medianCachedInputTokens": median([item[1] for item in usages]) if usages else None,
            "medianOutputTokens": median([item[2] for item in usages]) if usages else None}


def _statistics(connection):
    groups = defaultdict(list)
    infrastructure = defaultdict(lambda: {"count": 0, "lastAt": None})
    # A returned/failed/rejected/interrupted turn has ended; a merely received
    # receipt on a still-running turn is not yet an outcome fact.
    for row in connection.execute(
        "SELECT w.run_id,w.turn_index,w.state,w.input_json,a.result_json,"
        " a.attempt_id,a.started_at,a.finished_at FROM workflow_turns w"
        " JOIN attempts a ON a.attempt_id=w.attempt_id"
        " WHERE w.state IN ('returned','failed','rejected','interrupted')"
        " ORDER BY w.run_id,w.turn_index"
    ):
        buddy = turn_buddy(row["input_json"])
        receipt = json.loads(row["result_json"]) if row["result_json"] is not None else None
        category = round_classification(receipt)
        result = (receipt["result"] or {}) if receipt is not None else {}
        turn = result.get("turn") or {}
        delivered = row["state"] == "returned" and turn["outcome"]["disposition"] == "completed"
        fact = {**dict(row), "category": category, "delivered": delivered,
                "usage": result.get("tokenUsage")}
        groups[(row["run_id"], _key(buddy, KIND))].append(fact)
        if category == "infrastructure":
            failures = infrastructure[_key(buddy, IDENTITY)]
            failures["count"] += 1
            ended = row["finished_at"]
            if failures["lastAt"] is None or _time(ended) > _time(failures["lastAt"]):
                failures["lastAt"] = ended

    terminal = {row["run_id"]: row for row in connection.execute(
        "SELECT r.run_id,r.state,r.final_attempt_id,c.execution_status FROM workflow_runs r"
        " LEFT JOIN workflow_host_conclusions c ON c.run_id=r.run_id"
        " WHERE r.state IN ('accepted','concluded')"
    )}
    continuations = defaultdict(list)
    for row in connection.execute(
        "SELECT c.run_id,c.created_at,w.turn_index FROM workflow_continuations c"
        " LEFT JOIN workflow_turns w ON w.turn_id=c.turn_id WHERE c.kind='manual'"
    ):
        continuations[row["run_id"]].append(row)
    integrations = {row["run_id"]: json.loads(row["verification_json"]) for row in connection.execute(
        "SELECT i.run_id,i.verification_json FROM workflow_integrations i"
        " JOIN workflow_runs r ON r.run_id=i.run_id AND r.final_artifact_id=i.artifact_id"
        " WHERE r.state='accepted'"
    )}
    statistics = defaultdict(lambda: {"samples": 0, "successes": 0, "failures": 0,
                                      "firstPasses": 0, "reworkRounds": 0, "durations": [],
                                      "usages": []})
    for (run_id, kind), turns in groups.items():
        ability = [turn for turn in turns if turn["category"] == "ability"]
        run = terminal.get(run_id)
        if run is None or not ability:
            continue
        success = run["state"] == "accepted" and any(
            turn["attempt_id"] == run["final_attempt_id"]
            for turn in turns)
        deliveries = [turn for turn in turns if turn["delivered"]]
        if run["execution_status"] == "cancelled" and not deliveries:
            continue
        if not success and turns[-1]["category"] != "ability":
            continue
        # Acceptance can explicitly complete a stopped assistance/attention turn
        # or adopt a native delivery after a workspace conflict. Its artifact is
        # still that attempt's delivery, and the frozen turn identity owns it.
        if success:
            accepted = next(turn for turn in turns if turn["attempt_id"] == run["final_attempt_id"])
            if not accepted["delivered"]:
                deliveries.append(accepted)
                deliveries.sort(key=lambda turn: turn["turn_index"])
        rework = 0
        if deliveries:
            first = deliveries[0]
            rework = sum(
                (item["turn_index"] > first["turn_index"] if item["turn_index"] is not None
                 else _time(item["created_at"]) >= _time(first["finished_at"]))
                for item in continuations[run_id])
        stats = statistics[kind]
        stats["samples"] += 1
        stats["successes"] += int(success)
        stats["failures"] += int(not success)
        stats["reworkRounds"] += rework
        if success:
            verification = integrations[run_id]
            unchanged = not any(verification.get(field) for field in ("differingPaths", "missingPaths", "hostPaths"))
            stats["firstPasses"] += int(deliveries[0] is accepted and rework == 0 and unchanged)
        stats["durations"].extend(
            (_time(turn["finished_at"]) - _time(turn["started_at"])).total_seconds()
            for turn in deliveries)
        usage = _usage_totals(ability)
        if usage is not None:
            stats["usages"].append(usage)
    return statistics, infrastructure


def _loads(store, connection, services, *, now):
    remaining = defaultdict(list)
    for row in connection.execute(
        "SELECT a.model_harness,a.model_provider,a.model_model,a.started_at,a.created_at,"
        " COALESCE(w.expected_seconds,t.expected_seconds,t.router_budget_seconds) AS duration"
        " FROM (SELECT * FROM attempts WHERE " + store.UNRESOLVED_SQL + ") a"
        " JOIN tasks t ON t.task_id=a.task_id"
        " LEFT JOIN workflow_turns w ON w.attempt_id=a.attempt_id WHERE a.model_model IS NOT NULL"
    ):
        service = (row["model_harness"], row["model_provider"], row["model_model"])
        elapsed = (_time(now) - _time(row["started_at"] or row["created_at"])).total_seconds()
        remaining[service].append(row["duration"] - elapsed)
    queued = defaultdict(list)
    for row in connection.execute(
        "SELECT t.adapter AS harness," + store._QUEUED_MODEL_SERVICE_PROVIDER + " AS provider,"
        + store._QUEUED_MODEL_SERVICE_MODEL + " AS model,d.harness AS router_harness,"
        " d.provider AS router_provider,d.model AS router_model,"
        " COALESCE(t.expected_seconds,t.router_budget_seconds) AS duration"
        " FROM tasks t LEFT JOIN workflow_runs r ON r.run_id=t.task_id"
        " LEFT JOIN router_dispatches d ON d.task_id=t.task_id WHERE t.state='queued'"
        " ORDER BY t.created_at,t.task_id"
    ):
        service = ((row["router_harness"], row["router_provider"], row["router_model"])
                   if row["router_harness"] else (row["harness"], row["provider"], row["model"]))
        queued[service].append(row["duration"])
    loads = {}
    for capacity in store.model_capacity_rows(connection, services):
        service = _key(capacity, ("harness", "provider", "model"))
        running = remaining[service]
        overdue = sum(seconds <= 0 for seconds in running)
        slots = [max(0, seconds) for seconds in running]
        slots.extend([0] * max(0, capacity["limit"] - len(slots)))
        heapq.heapify(slots)
        # A limit lowered below occupancy requires those excess attempts to
        # finish before any slot can admit another task.
        for _ in range(max(0, len(slots) - capacity["limit"])):
            heapq.heappop(slots)
        for duration in queued[service]:
            heapq.heapreplace(slots, slots[0] + duration)
        loads[service] = {"active": capacity["active"], "limit": capacity["limit"],
                          "queued": len(queued[service]),
                          "wait": {"seconds": slots[0], "atLeast": overdue > 0, "overdue": overdue}}
    return loads


def candidate_facts(store, connection, buddies, *, now: str) -> dict:
    """The aggregate packet frozen for a Router, with explicit no-data rates."""
    statistics, infrastructure = _statistics(connection)
    loads = _loads(store, connection, {_key(buddy, ("harness", "provider", "model")) for buddy in buddies}, now=now)
    facts = {}
    for buddy in buddies:
        stats = statistics[_key(buddy, KIND)]
        samples = stats["samples"]
        usages = stats["usages"]
        facts[buddy["buddy_id"]] = {
            "billing": for_provider(connection, buddy["harness"], buddy["provider"]),
            "load": loads[_key(buddy, ("harness", "provider", "model"))],
            "statistics": {**{key: value for key, value in stats.items() if key not in ("durations", "usages")},
                           "firstPassRate": stats["firstPasses"] / samples if samples else None,
                           "failureRate": stats["failures"] / samples if samples else None,
                           "medianDeliverySeconds": median(stats["durations"]) if stats["durations"] else None,
                           "usage": _usage_fact(usages)},
            "infrastructureFailures": infrastructure[_key(buddy, IDENTITY)],
        }
    return facts
