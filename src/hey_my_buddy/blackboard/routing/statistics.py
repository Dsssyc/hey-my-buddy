"""Ended-turn classification, per-micro-task samples and cumulative writes.

Settlement and maintenance use the same sample extractor as the one pending
upgrade. Only those write paths read the outcome history.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import json
import math

from ...protocol.turn_input import turn_buddy

NEITHER = frozenset({"service-stop", "console-stop", "transport-error",
                     "workspace-error", "unknown-stop", "interrupted"})
PROVIDER_REJECTIONS = frozenset({"overloaded", "quota-exceeded", "rate-limited"})
IDENTITY = ("harness", "provider", "model", "effort")
KIND = ("harness", "model", "effort")
USAGE_FIELDS = ("inputTokens", "cachedInputTokens", "outputTokens")
METRICS = ("delivery_seconds", "input_tokens", "cached_input_tokens", "output_tokens")
COUNTS = ("samples", "successes", "first_passes", "rework_rounds", "failures")
ENDED_STATES = "('returned','failed','rejected','interrupted')"
BUCKET_RATIO = 1.2


def key(buddy, fields):
    return tuple(buddy[field] for field in fields)


def time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def round_classification(receipt: dict | None) -> str:
    """Product/user termination wins over provider rejection; no receipt is neither."""
    if receipt is None or receipt["terminationReason"] in NEITHER:
        return "neither"
    result = receipt["result"] or {}
    rejection = result.get("providerRejection") or {}
    if result.get("code") == "harness-unavailable" or rejection.get("code") in PROVIDER_REJECTIONS:
        return "infrastructure"
    return "ability"


def ended_turns(connection, run_id=None):
    where, parameters = ("", ()) if run_id is None else (" AND w.run_id=?", (run_id,))
    for row in connection.execute(
        "SELECT w.run_id,w.turn_index,w.state,w.input_json,a.result_json,"
        " a.attempt_id,a.started_at,a.finished_at FROM workflow_turns w"
        " JOIN attempts a ON a.attempt_id=w.attempt_id"
        " WHERE w.state IN " + ENDED_STATES + where + " ORDER BY w.run_id,w.turn_index", parameters
    ):
        receipt = json.loads(row["result_json"]) if row["result_json"] is not None else None
        result = (receipt["result"] or {}) if receipt is not None else {}
        turn = result.get("turn") or {}
        yield {**dict(row), "buddy": turn_buddy(row["input_json"]),
               "category": round_classification(receipt), "usage": result.get("tokenUsage"),
               "delivered": row["state"] == "returned" and turn["outcome"]["disposition"] == "completed"}


def samples_for_run(connection, run_id):
    """The original read-time sample rule, restricted to the ending micro task."""
    run = connection.execute(
        "SELECT r.state,r.final_attempt_id,c.execution_status FROM workflow_runs r"
        " LEFT JOIN workflow_host_conclusions c ON c.run_id=r.run_id"
        " WHERE r.run_id=? AND r.state IN ('accepted','concluded')", (run_id,)
    ).fetchone()
    if run is None:
        return {}
    groups = defaultdict(list)
    for turn in ended_turns(connection, run_id):
        groups[key(turn["buddy"], KIND)].append(turn)
    continuations = list(connection.execute(
        "SELECT c.created_at,w.turn_index FROM workflow_continuations c"
        " LEFT JOIN workflow_turns w ON w.turn_id=c.turn_id WHERE c.kind='manual' AND c.run_id=?", (run_id,)))
    integrations = [json.loads(row[0]) for row in connection.execute(
        "SELECT i.verification_json FROM workflow_integrations i JOIN workflow_runs r"
        " ON r.run_id=i.run_id AND r.final_artifact_id=i.artifact_id WHERE r.run_id=? AND r.state='accepted'", (run_id,))]
    samples = {}
    for kind, turns in groups.items():
        ability = [turn for turn in turns if turn["category"] == "ability"]
        if not ability:
            continue
        success = run["state"] == "accepted" and any(
            turn["attempt_id"] == run["final_attempt_id"] for turn in turns)
        deliveries = [turn for turn in turns if turn["delivered"]]
        if run["execution_status"] == "cancelled" and not deliveries:
            continue
        if not success and turns[-1]["category"] != "ability":
            continue
        # Acceptance owns the frozen turn's delivery, including an explicitly
        # completed assistance/attention or an adopted workspace-conflict seal.
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
                 else time(item["created_at"]) >= time(first["finished_at"]))
                for item in continuations)
        first_pass = False
        if success:
            unchanged = not any(integrations[-1].get(field) for field in ("differingPaths", "missingPaths", "hostPaths"))
            first_pass = deliveries[0] is accepted and rework == 0 and unchanged
        usage = None
        if all((turn["usage"] or {}).get("completeness") == "complete" for turn in ability):
            usage = tuple(sum(turn["usage"][field] for turn in ability) for field in USAGE_FIELDS)
        samples[kind] = {
            "success": success, "first_pass": first_pass, "rework": rework,
            "durations": [(time(turn["finished_at"]) - time(turn["started_at"])).total_seconds()
                          for turn in deliveries], "usage": usage,
        }
    return samples


def bucket(value):
    """Sign and log-magnitude; (0,0) is zero, |value| lies in [1.2**k,1.2**(k+1)).

    Wall-clock timestamps can move backwards, so delivery durations retain their
    sign just as the original reader did. Usage is normalized nonnegative input.
    """
    if value == 0:
        return (0, 0)
    return (1 if value > 0 else -1, math.floor(math.log(abs(value)) / math.log(BUCKET_RATIO)))


def approximate_median(buckets):
    total = sum(buckets.values())
    if not total:
        return None
    positions = ((total - 1) // 2, total // 2)
    offset, values = 0, []
    ordered = sorted((sign * BUCKET_RATIO ** (exponent + 0.5) if sign else 0, count)
                     for (sign, exponent), count in buckets.items())
    for midpoint, count in ordered:
        for position in positions:
            if offset <= position < offset + count:
                values.append(midpoint)
        offset += count
    return sum(values) / 2


def _add_sample(connection, kind, sample, cell):
    table = "kind_statistics" if cell is None else "kind_cell_statistics"
    fields = KIND if cell is None else (*KIND, "domain_node_id", "task_type")
    values = kind if cell is None else (*kind, *cell)
    counts = (1, int(sample["success"]), int(sample["first_pass"]), sample["rework"], int(not sample["success"]))
    connection.execute(
        f"INSERT INTO {table}({','.join((*fields, *COUNTS))}) VALUES({','.join('?' for _ in (*values, *counts))})"
        f" ON CONFLICT({','.join(fields)}) DO UPDATE SET " +
        ",".join(f"{field}={field}+excluded.{field}" for field in COUNTS), (*values, *counts))
    bins = Counter((METRICS[0], bucket(value)) for value in sample["durations"])
    if sample["usage"] is not None:
        bins.update((metric, bucket(value)) for metric, value in zip(METRICS[1:], sample["usage"]))
    connection.executemany(
        f"INSERT INTO {table}_buckets({','.join((*fields, 'metric', 'sign', 'bucket', 'count'))})"
        f" VALUES({','.join('?' for _ in range(len(values) + 4))})"
        f" ON CONFLICT({','.join((*fields, 'metric', 'sign', 'bucket'))}) DO UPDATE SET count=count+excluded.count",
        [(*values, metric, sign, exponent, count) for (metric, (sign, exponent)), count in bins.items()])


def accumulate_overall(connection, run_id):
    for kind, sample in samples_for_run(connection, run_id).items():
        _add_sample(connection, kind, sample, None)


def accumulate_cell(connection, run_id, node_id, task_type):
    for kind, sample in samples_for_run(connection, run_id).items():
        _add_sample(connection, kind, sample, (node_id, task_type))


def accumulate_infrastructure(connection, buddy, receipt, finished_at, *, new_round=True):
    if round_classification(receipt) != "infrastructure":
        return
    previous = connection.execute(
        "SELECT last_at FROM buddy_infrastructure_failures WHERE harness=? AND provider=? AND model=? AND effort=?",
        key(buddy, IDENTITY)).fetchone()
    latest = (finished_at if previous is None or time(finished_at) > time(previous["last_at"])
              else previous["last_at"])
    if not new_round:
        # A later stop proof settles this same turn again and advances its
        # finished_at. It refreshes the latest time, not the number of rounds.
        connection.execute(
            "UPDATE buddy_infrastructure_failures SET last_at=? WHERE harness=? AND provider=? AND model=? AND effort=?",
            (latest, *key(buddy, IDENTITY)))
        return
    connection.execute(
        "INSERT INTO buddy_infrastructure_failures(harness,provider,model,effort,count,last_at) VALUES(?,?,?,?,1,?)"
        " ON CONFLICT(harness,provider,model,effort) DO UPDATE SET count=count+1,last_at=excluded.last_at",
        (*key(buddy, IDENTITY), latest))


def backfill(connection):
    for row in connection.execute("SELECT run_id FROM workflow_runs WHERE state IN ('accepted','concluded')").fetchall():
        accumulate_overall(connection, row["run_id"])
    for turn in ended_turns(connection):
        receipt = json.loads(turn["result_json"]) if turn["result_json"] is not None else None
        accumulate_infrastructure(connection, turn["buddy"], receipt, turn["finished_at"])
