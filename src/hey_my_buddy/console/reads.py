"""The console's periodic read projection.

The old periodic snapshot re-derived the latest hundred fully decorated task rows
and the full evaluation/routing/harness projection every poll. This
module builds the projection the console actually polls:

* the evaluation core (``EvaluationStore.console_core``) — configuration,
  routed buddies with provider cooling, policy and model-service capacity;
* harness health and routing health, as before;
* ``tasks`` reduced to ``pendingCount`` — the full-board count of micro task
  roots awaiting their Host, with the exact dedup semantics the top bar used;
* maintenance runs, pending work, the domain tree and cumulative kind facts.

The projection also reports the earliest time at which one of its fields can
flip (provider cooling and router skip windows), so a read cache can serve it
unchanged without freezing state that must expire.
"""
from __future__ import annotations

import json
import time as _time

from ..blackboard.routing.evidence import kind_domain_statistics
from ..blackboard.routing.maintenance_data import pending_count
from ..protocol.reasons import reason_from_json


def _maintenance_view(connection) -> dict:
    runs = list(connection.execute(
        "SELECT m.maintenance_run_id,m.trigger_type,m.buddy_id,m.task_id,m.state,m.reason_code,"
        " m.reason_params_json,m.started_at,m.finished_at,b.harness,b.provider,b.model,b.effort"
        " FROM maintenance_runs m JOIN buddies b ON b.buddy_id=m.buddy_id"
        " ORDER BY m.rowid DESC"))
    attempts = {run["task_id"]: [] for run in runs}
    for row in connection.execute(
        "SELECT a.attempt_id,a.task_id,a.result_json FROM attempts a"
        " JOIN maintenance_runs m ON m.task_id=a.task_id ORDER BY a.task_id,a.generation"):
        recorded = json.loads(row["result_json"]) if row["result_json"] else {}
        result = recorded.get("result") or {}
        usage = result.get("usage") or {}
        attempts[row["task_id"]].append({
            "attemptId": row["attempt_id"], "elapsedMs": usage.get("elapsedMs"),
            "toolCalls": usage.get("toolCalls"), "tokenUsage": result.get("tokenUsage"),
        })
    return {
        "pendingCount": pending_count(connection),
        "runs": [{
            "maintenanceRunId": row["maintenance_run_id"], "triggerType": row["trigger_type"],
            "buddyId": row["buddy_id"], "buddy": {field: row[field] for field in ("harness", "provider", "model", "effort")},
            "taskId": row["task_id"], "state": row["state"],
            "reason": reason_from_json(row["reason_code"], row["reason_params_json"]),
            "startedAt": row["started_at"], "finishedAt": row["finished_at"],
            "usage": {"attempts": attempts[row["task_id"]]},
        } for row in runs],
    }


def _kind_evaluations(connection) -> list[dict]:
    kinds = [dict(row) for row in connection.execute(
        "SELECT DISTINCT harness,model,effort FROM buddies ORDER BY harness,model,effort")]
    statistics = kind_domain_statistics(connection, kinds)
    summaries = {(row["harness"], row["model"], row["effort"]):
                 {"text": row["text"], "citations": []}
                 for row in connection.execute("SELECT harness,model,effort,text FROM kind_summaries")}
    for row in connection.execute(
        "SELECT e.harness,e.model,e.effort,e.run_id,r.title,r.macro_task_id"
        " FROM kind_summary_evidence e JOIN workflow_runs r ON r.run_id=e.run_id"
        " WHERE e.cited=1 ORDER BY e.harness,e.model,e.effort,e.run_id"):
        summaries[row["harness"], row["model"], row["effort"]]["citations"].append({
            "runId": row["run_id"], "title": row["title"], "macroTaskId": row["macro_task_id"],
        })
    return [{**kind, "summary": summaries.get((kind["harness"], kind["model"], kind["effort"])),
             "statistics": statistics[kind["harness"], kind["model"], kind["effort"]]}
            for kind in kinds]


def console_snapshot_projection(service, *, clock=_time.time) -> tuple[dict, float]:
    """Assemble the console snapshot and its earliest change boundary.

    Returns ``(projection, deadline)`` where ``deadline`` is epoch seconds: the
    earliest future moment at which some field of the projection can flip on
    time alone. Database changes are not tracked here — the caller's marker
    covers them — and neither is the console's own session/access state.

    While discovery is automatic, the projection also borrows the rate-limited
    harness scan: the deadline is capped at the scan interval so an idle
    console that keeps answering from the cache still re-enters this function
    (and kicks the scan) instead of starving the 180-second cadence — including
    the cold case where every health record is still unknown and carries no
    ``scanAfter`` of its own.
    """
    # A periodic read still borrows the rate-limited harness scan exactly as
    # the previous implementation did; the scan itself is bounded by its own
    # 180-second markers and writes land as ordinary database changes.
    automatic = bool(getattr(service, "automatic_discovery", False))
    if automatic:
        service.harnesses.kick()
    now = clock()
    from ..blackboard.service.harness_health import SCAN_SECONDS
    from ..blackboard.tasks import macro_tasks
    from .read_cache import collect_deadline

    projection = service.evaluation.console_core()
    projection["harnesses"] = service.harnesses.all()
    projection["routingHealth"] = service.decisions.health_summary()
    projection["capabilities"] = service.evaluation.capabilities()
    projection["reminderSettings"] = service.reminder_settings_view()
    projection["tasks"] = {"pendingCount": macro_tasks.pending_root_count(service.store)}
    with service.store.db.read() as connection:
        projection["maintenance"] = _maintenance_view(connection)
        projection["domainNodes"] = [
            {"nodeId": row["node_id"], "name": row["name"],
             "definition": row["definition"], "parentId": row["parent_id"],
             "mergedIntoId": row["merged_into_id"]}
            for row in connection.execute(
                "SELECT node_id,name,definition,parent_id,merged_into_id FROM domain_nodes"
                " ORDER BY node_id")]
        projection["kindEvaluations"] = _kind_evaluations(connection)
    deadline = collect_deadline(projection, after=now)
    if automatic:
        deadline = min(deadline, now + SCAN_SECONDS)
    return projection, deadline
