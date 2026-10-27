"""Routing facts from cumulative statistics and current admission load."""
from __future__ import annotations

from collections import Counter, defaultdict
import heapq

from ...protocol.billing import for_provider
from .statistics import (
    COUNTS, IDENTITY, KIND, METRICS, approximate_median, key as _key, time as _time,
)


def _empty_statistics():
    return {**dict.fromkeys(COUNTS, 0), "buckets": {metric: Counter() for metric in METRICS}}


def _statistics_fact(stats):
    samples = stats["samples"]
    bins = stats["buckets"]
    return {"samples": samples, "successes": stats["successes"], "failures": stats["failures"],
            "firstPasses": stats["first_passes"], "reworkRounds": stats["rework_rounds"],
            "firstPassRate": stats["first_passes"] / samples if samples else None,
            "failureRate": stats["failures"] / samples if samples else None,
            "medianDeliverySeconds": approximate_median(bins["delivery_seconds"]),
            "usage": {"samples": sum(bins["input_tokens"].values()),
                      "medianInputTokens": approximate_median(bins["input_tokens"]),
                      "medianCachedInputTokens": approximate_median(bins["cached_input_tokens"]),
                      "medianOutputTokens": approximate_median(bins["output_tokens"])}}


def _stored_statistics(connection, table, kinds, *, cells=False):
    statistics = defaultdict(_empty_statistics)
    if not kinds:
        return statistics
    fields = (*KIND, "domain_node_id", "task_type") if cells else KIND
    where = " WHERE (harness,model,effort) IN (" + ",".join("(?,?,?)" for _ in kinds) + ")"
    values = tuple(value for kind in kinds for value in kind)
    for row in connection.execute("SELECT * FROM " + table + where, values):
        stats = statistics[_key(row, fields)]
        for field in COUNTS:
            stats[field] = row[field]
    for row in connection.execute("SELECT * FROM " + table + "_buckets" + where, values):
        statistics[_key(row, fields)]["buckets"][row["metric"]][(row["sign"], row["bucket"])] = row["count"]
    return statistics


def _infrastructure(connection, buddies):
    failures = {}
    for buddy in buddies:
        identity = _key(buddy, IDENTITY)
        if identity in failures:
            continue
        row = connection.execute(
            "SELECT count,last_at FROM buddy_infrastructure_failures"
            " WHERE harness=? AND provider=? AND model=? AND effort=?", identity).fetchone()
        failures[identity] = {"count": row["count"], "lastAt": row["last_at"]} if row else {"count": 0, "lastAt": None}
    return failures


def _domain_lineage(node_id, nodes):
    node = nodes[node_id]
    current = node["merged_into_id"] or node_id
    lineage = []
    while current is not None:
        lineage.append(current)
        current = nodes[current]["parent_id"]
    return lineage


def _add_statistics(target, source):
    for field in COUNTS:
        target[field] += source[field]
    for metric in METRICS:
        target["buckets"][metric].update(source["buckets"][metric])


def kind_domain_statistics(connection, buddy_kinds) -> dict:
    """Three levels per real kind; infrastructure stays split by provider.

    The enclosing kind plus each provider identifies a full buddy. Maintenance
    has no selected provider and must not invent one to read these facts.
    """
    kinds = {_key(buddy, KIND) for buddy in buddy_kinds}
    overall = _stored_statistics(connection, "kind_statistics", kinds)
    cells = _stored_statistics(connection, "kind_cell_statistics", kinds, cells=True)
    infrastructure = defaultdict(list)
    for row in connection.execute("SELECT * FROM buddy_infrastructure_failures ORDER BY harness,model,effort,provider"):
        kind = _key(row, KIND)
        if kind in kinds:
            infrastructure[kind].append({"provider": row["provider"], "count": row["count"], "lastAt": row["last_at"]})
    nodes = {row["node_id"]: row for row in connection.execute(
        "SELECT node_id,parent_id,merged_into_id FROM domain_nodes")}
    by_domain = defaultdict(lambda: defaultdict(_empty_statistics))
    by_type = defaultdict(lambda: defaultdict(lambda: defaultdict(_empty_statistics)))
    for (*kind, node_id, task_type), stats in cells.items():
        for ancestor in _domain_lineage(node_id, nodes):
            _add_statistics(by_domain[tuple(kind)][ancestor], stats)
            _add_statistics(by_type[tuple(kind)][ancestor][task_type], stats)
    return {
        kind: {
            "overall": _statistics_fact(overall[kind]),
            "byDomain": {node_id: _statistics_fact(stats) for node_id, stats in by_domain[kind].items()},
            "byDomainTaskType": {
                node_id: {task_type: _statistics_fact(stats) for task_type, stats in types.items()}
                for node_id, types in by_type[kind].items()},
            "infrastructureFailures": infrastructure[kind],
        } for kind in kinds
    }


def domain_statistics(connection, buddies) -> dict:
    """Project each kind's facts onto the requested full buddy identities."""
    kinds = kind_domain_statistics(connection, buddies)
    infrastructure = _infrastructure(connection, buddies)
    return {buddy["buddy_id"]: {**kinds[_key(buddy, KIND)],
                              "infrastructureFailures": infrastructure[_key(buddy, IDENTITY)]}
            for buddy in buddies}


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
    statistics = domain_statistics(connection, buddies)
    summaries = {_key(row, KIND): row["text"] for row in connection.execute(
        "SELECT harness,model,effort,text FROM kind_summaries")}
    loads = _loads(store, connection, {_key(buddy, ("harness", "provider", "model")) for buddy in buddies}, now=now)
    return {
        buddy["buddy_id"]: {
            "billing": for_provider(connection, buddy["harness"], buddy["provider"]),
            "load": loads[_key(buddy, ("harness", "provider", "model"))],
            "statistics": {key: value for key, value in statistics[buddy["buddy_id"]].items()
                           if key != "infrastructureFailures"},
            "infrastructureFailures": statistics[buddy["buddy_id"]]["infrastructureFailures"],
            "summary": summaries.get(_key(buddy, KIND)),
        } for buddy in buddies
    }
