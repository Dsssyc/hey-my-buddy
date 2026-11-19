"""Read-only Host projection of frozen Router packets and each item's evidence.

No resolution, native/account probe, retry consumption or state transition belongs
here. Dispatches and their own bound receipts supply actors and outcomes; the
current request output cannot stand in for a previous item's result.
"""
from __future__ import annotations

import json
from copy import deepcopy

from . import router_history, router_sequence
from ...protocol import schemas
from ...protocol.owned_process import WorkingDirectoryRejected
from ...protocol.reasons import reason_from_json
from .router_boundary import build_boundary
from .candidate import candidate_bound
from ..tasks.micro_task import ROUTING_TASKS_SQL
from ..tasks.workspace_bindings import effective_manifest

_CHANGED_CODES = frozenset({
    "router-input-changed",
    "router-out-of-bounds", "workflow-routing-fenced", "router-input-too-large",
    WorkingDirectoryRejected.code,
})


def _json(value, default=None):
    return default if value is None else json.loads(value)


def _events(connection, row):
    rows = connection.execute(
        "SELECT seq,kind,task_id,attempt_id,created_at,payload_json FROM events WHERE "
        "(kind IN ('router.no_answer','router.answered','router.claimed') AND "
        "json_extract(payload_json,'$.requestId')=?) OR "
        "(kind LIKE 'decision.%' AND json_extract(payload_json,'$.decisionId')=?) ORDER BY seq",
        (row["request_id"], row["decision_id"]))
    return [{**dict(event), "payload": _json(event["payload_json"], {})} for event in rows]


def _stopped(connection, task_id):
    task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if task is None:
        return False
    attempts = connection.execute("SELECT * FROM attempts WHERE task_id=?", (task_id,)).fetchall()
    if any(item["execution_state"] != "finished" or item["shutdown_confirmed"] != 1 for item in attempts):
        return False
    ids = {item["attempt_id"] for item in attempts}
    if any(value and value not in ids for value in (task["active_attempt_id"], task["selected_attempt_id"])):
        return False
    return bool(attempts) or task["state"] not in ("running", "cancelling", "reconciliation-needed")


def _state(connection, buddy_id, facts, now):
    return router_history.router_state(connection, buddy_id=buddy_id,
                                       interval_seconds=facts["routerRetryIntervalSeconds"], now=now)


def _quota_recovery(connection, buddy, now):
    """Provider cooling ends at its recorded deadline."""
    from .quota_routing import cooling
    state = cooling(connection, buddy, now=now)
    return (True, state["coolingUntil"]) if state is not None else (False, None)


def _preflight_no_answer(connection, item, event, state, now):
    """Project a recorded qualification failure, including after a dispatch queued."""
    item.update(outcome="no_answer", phase=event["payload"].get("phase"),
                code=event["payload"].get("code"), retryAt=None)
    from ..catalog import buddy_state
    buddy = buddy_state.get(connection, item["buddyId"])
    if item["code"] == "router-quota-exhausted":
        _, item["retryAt"] = _quota_recovery(connection, buddy, now)
        if (item["retryAt"] and state.get("inSkipWindow")
                and router_history._parse(state["retryAt"]) > router_history._parse(item["retryAt"])):
            item["retryAt"] = state["retryAt"]


def router_trials(connection, row, *, now):
    """Every observed/dispatch item, with its own receipt; unknown stays null."""
    snapshot = router_sequence.request_snapshot(connection, row["decision_id"])
    request = _json(row["requested_json"], {})
    if snapshot["facts"].get("routerCalled") is False or (request.get("routingBasis") or {}).get("candidateCount") == 0:
        return []
    current_round = connection.execute("SELECT MAX(round) FROM router_requests WHERE decision_id=?",
                                       (row["decision_id"],)).fetchone()[0]
    facts = snapshot["facts"]
    events = _events(connection, row)
    if request.get("selectionRetry"):
        rerouted = next((event["seq"] for event in reversed(events)
                         if event["kind"] == "decision.selection_rerouted"), None)
        if rerouted is not None:
            events = [event for event in events if event["seq"] > rerouted]
    inspected = {item["index"]: item for item in snapshot["inspections"]}
    dispatches = tuple(item for item in router_sequence.dispatches(connection, row["decision_id"])
                       if item["round"] == current_round)
    dispatched = {item["routerIndex"]: item for item in dispatches}
    for event in events:
        payload = event["payload"]
        if event["kind"] == "router.no_answer" and payload.get("phase") == "preflight" and payload.get("taskId") is None:
            index = payload.get("routerIndex")
            inspected.setdefault(index, {"index": index, "buddyId": payload.get("buddyId"),
                                         "code": payload.get("code")})
    for index, buddy_id in enumerate(facts.get("routerBuddyIds") or []):
        inspected.setdefault(index, {"index": index, "buddyId": buddy_id, "unvisited": True})
    trials = []
    for index in sorted(set(inspected) | set(dispatched)):
        inspection = inspected.get(index, {})
        doc = dispatched.get(index)
        buddy_id = doc["buddyId"] if doc else inspection.get("buddyId")
        identities = facts.get("routerIdentities") or []
        identity = doc["buddy"] if doc else inspection.get("identity") or (
            identities[index])
        state = _state(connection, buddy_id, facts, now) if buddy_id else {}
        item = {"buddyId": buddy_id, "identity": deepcopy(identity), "index": index,
                "phase": "preflight", "outcome": "unknown", "code": inspection.get("code"),
                "taskId": doc["taskId"] if doc else None,
                "attemptId": None, "shutdownConfirmed": None,
                "consecutiveNoAnswers": state.get("consecutiveNoAnswers"),
                "retryAt": state.get("retryAt"), "usage": None, "nativeIdentity": None}
        matching = [event for event in events if event["payload"].get("routerIndex") == index
                    and event["payload"].get("buddyId") == buddy_id]
        if doc is None:
            no_answer = next((event for event in reversed(matching) if event["kind"] == "router.no_answer"), None)
            if no_answer is not None:
                item.update(outcome="no_answer", phase=no_answer["payload"].get("phase"),
                            code=no_answer["payload"].get("code"))
            elif inspection.get("code") == "router-skip-window":
                item.update(outcome="skipped", retryAt=inspection.get("retryAt"),
                            consecutiveNoAnswers=inspection.get("consecutiveNoAnswers"))
            elif inspection.get("selected"):
                # An inspected eligible item need not have been dispatched, e.g.
                # a packet exceeded the send ceiling. It did not execute/fail.
                item.update(outcome="skipped", code=None)
            elif inspection.get("unvisited"):
                item.update(phase=None, outcome="skipped")
            if item["code"] not in (None, "router-skip-window"):
                item["retryAt"] = None
                from ..catalog import buddy_state
                buddy = buddy_state.get(connection, buddy_id)
                if item["code"] == "router-quota-exhausted":
                    _, item["retryAt"] = _quota_recovery(connection, buddy, now)
                    if item["retryAt"] and state.get("inSkipWindow") and router_history._parse(state["retryAt"]) > router_history._parse(item["retryAt"]):
                        item["retryAt"] = state["retryAt"]
            trials.append(item)
            continue
        task_id = doc["taskId"]
        task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        attempts = connection.execute("SELECT * FROM attempts WHERE task_id=? ORDER BY generation", (task_id,)).fetchall()
        # Each attempt is bound to this dispatch in the claim transaction.
        if not attempts:
            item.update(phase="queued", outcome="queued", code=None)
            if (no_answer := next((event for event in reversed(matching)
                    if event["kind"] == "router.no_answer" and event["payload"].get("phase") == "preflight"
                    and event["payload"].get("taskId") in (None, task_id)
                    and event["payload"].get("attemptId") is None), None)) is not None:
                _preflight_no_answer(connection, item, no_answer, state, now)
            elif row["decision_task_id"] == task_id and row["status"] in ("cancelled", "needs-host", "failed"):
                changed = row["status"] != "cancelled" and row["reason_code"] in _CHANGED_CODES
                item.update(outcome="cancelled" if row["status"] == "cancelled" else "changed" if changed else "skipped",
                            phase="preflight", code=row["reason_code"] if changed else None)
            item["shutdownConfirmed"] = True if _stopped(connection, task_id) else None
            trials.append(item)
            continue
        for attempt in attempts:
            trial = deepcopy(item)
            current_attempt = (row["decision_attempt_id"] == attempt["attempt_id"]
                               and row["decision_generation"] == attempt["generation"])
            receipt = _json(attempt["result_json"], {})
            output = receipt.get("result") or {}
            own_events = [event for event in matching if event["payload"].get("taskId") == task_id
                          and event["payload"].get("attemptId") == attempt["attempt_id"]
                          and event["kind"] in ("router.no_answer", "router.answered")]
            outcome_event = own_events[-1] if own_events else None
            trial.update(attemptId=attempt["attempt_id"], phase="runtime", code=None,
                         usage=deepcopy(output.get("usage")),
                         nativeIdentity=deepcopy(output.get("nativeIdentity")))
            outer_stopped = attempt["execution_state"] == "finished" and attempt["shutdown_confirmed"] == 1
            stop = output.get("stopEvidence") or {}
            native = stop.get("native") or {}
            released = connection.execute(
                "SELECT 1 FROM events WHERE kind='attempt.released' AND task_id=? AND attempt_id=? LIMIT 1",
                (task_id, attempt["attempt_id"])).fetchone()
            confirmed = outer_stopped and (bool(released) or (
                receipt.get("shutdownConfirmed") == 1 and stop.get("shutdownConfirmed") is True
                and native.get("shutdownConfirmed") is True))
            trial["shutdownConfirmed"] = (True if confirmed else False if attempt["shutdown_confirmed"] == 0
                                           or stop.get("shutdownConfirmed") is False
                                           or native.get("shutdownConfirmed") is False else None)
            if outcome_event:
                trial.update(outcome=outcome_event["payload"]["outcome"], phase=outcome_event["payload"].get("phase"),
                             code=outcome_event["payload"].get("code"))
            elif receipt.get("status") == "cancelled" or attempt["cancel_requested_at"] or (task and task["state"] == "cancelled") or (row["decision_task_id"] == task_id and row["status"] == "cancelled"):
                trial.update(outcome="cancelled")
            elif current_attempt and row["decision_task_id"] == task_id and row["reason_code"] in _CHANGED_CODES:
                trial.update(outcome="changed", code=row["reason_code"])
            elif not confirmed and (receipt or attempt["execution_state"] in ("uncertain", "finished")):
                trial.update(outcome="stopUnknown", code="router-stop-unconfirmed")
            elif attempt["execution_state"] in ("starting", "executing", "finalizing"):
                trial.update(outcome="executing")
            else:
                trial.update(outcome="unknown")
            trials.append(trial)
    return trials


def _continuation_context(connection, route, run_id, state_dir):
    """Read the same stop and workspace facts a continuation needs."""
    if run_id is None:
        return False, None
    run = connection.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
    if run is None:
        return False, "micro-task-not-found"
    own_stopped = _stopped(connection, run_id)
    if route is None or route["decision_id"] != run["current_routing_id"] or route["run_id"] != run_id:
        return own_stopped, "workflow-routing-fenced"
    terminal = connection.execute(
        "WITH RECURSIVE ancestors(run_id) AS ("
        " SELECT ? UNION SELECT lead.lead_run_id FROM workflow_lead_tasks lead"
        " JOIN ancestors ON ancestors.run_id=lead.owned_run_id)"
        " SELECT 1 FROM workflow_runs run JOIN ancestors USING(run_id)"
        " WHERE run.state IN ('accepted','concluded','cancelled') LIMIT 1",
        (run_id,)).fetchone()
    if terminal:
        return own_stopped, "ANCESTOR_TERMINAL"
    if connection.execute("SELECT 1 FROM workspace_cleanup_plans WHERE run_id=? AND state IN ('applying','applied')", (run_id,)).fetchone():
        return own_stopped, "workspace-cleanup-started"
    if not own_stopped:
        return False, None
    routing_tasks = connection.execute(
        f"SELECT task_id FROM ({ROUTING_TASKS_SQL}) WHERE run_id=?", (run_id,))
    if any(not _stopped(connection, item[0]) for item in routing_tasks):
        return False, None
    if run["checkout"] == "none":
        return True, None
    manifest = effective_manifest(state_dir, connection, run)
    reservations = connection.execute("SELECT * FROM workspace_reservations WHERE holder_task_id=? ORDER BY rowid DESC", (run_id,)).fetchall()
    current = next((item for item in reservations if item["state"] == "held"), None)
    current = current or next((item for item in reservations if item["checkout_root"] == manifest["checkoutRoot"]), None)
    task = connection.execute("SELECT cwd FROM tasks WHERE task_id=?", (run_id,)).fetchone()
    reservation_access = "read" if manifest["access"] == "read" else "write"
    if (current is None or current["access"] != reservation_access
            or task["cwd"] != manifest["path"]
            or current["checkout_root"] != manifest["checkoutRoot"]):
        return True, "workspace-ownership-lost"
    conflict = connection.execute(
        "SELECT 1 FROM workspace_reservations WHERE checkout_root=?"
        " AND holder_task_id!=? AND state='held' AND (access='write' OR ?='write') LIMIT 1",
        (manifest["checkoutRoot"], run_id, reservation_access)).fetchone()
    return True, "workspace-ownership-lost" if conflict else None


def _candidate_problem(connection, candidate, request, now):
    from ..catalog import buddy_state
    published = buddy_state.get(connection, candidate["buddyId"])
    constraints = request.get("constraints", {})
    bound = candidate_bound(connection, published, {**request, "constraints": constraints}, now, frozen=candidate)
    return {"code": "router-out-of-bounds", "bound": bound} if bound is not None else None


def _retry_at(connection, facts, trials, *, now, kind):
    if any(item["outcome"] in ("stopUnknown", "unknown", "executing") for item in trials if item["taskId"]):
        return None
    if kind in ("routing-changed", "router-abstained"):
        return now
    known = []
    for index, buddy_id in enumerate(facts.get("routerBuddyIds") or []):
        identity = (facts.get("routerIdentities") or [])[index]
        from ..catalog import buddy_state
        buddy = buddy_state.get(connection, buddy_id)
        if candidate_bound(connection, buddy, {"codingOnly": False}, now,
                           frozen=identity, apply_preferences=False, check_cooling=False) is not None:
            continue
        quota_blocked, quota_at = _quota_recovery(connection, buddy, now)
        if quota_blocked and quota_at is None:
            continue
        trial = next((item for item in reversed(trials) if item["index"] == index), None)
        if trial and trial["phase"] == "preflight" and trial["code"] in (
                "router-no-tool-unsupported", "router-review-unsupported"):
            # Qualification/health/quota has no known restoration time. Its
            # no-answer event is real, but interval expiry cannot repair it.
            continue
        state = _state(connection, buddy_id, facts, now)
        if state.get("retryInProgress"):
            continue
        if quota_at is not None:
            if state.get("inSkipWindow") and router_history._parse(state["retryAt"]) > router_history._parse(quota_at):
                quota_at = state["retryAt"]
            known.append(quota_at)
            continue
        if state.get("inSkipWindow"):
            known.append(state["retryAt"])
        elif facts.get("routingMode") is not None:
            return now
    return min(known, key=router_history._parse) if known else None


def routing_boundary(connection, row, *, now, run_id=None, state_dir=None) -> dict | None:
    """Build a current readable Host boundary from frozen evidence, without writes."""
    route = connection.execute("SELECT * FROM workflow_routes WHERE decision_id=?", (row["decision_id"],)).fetchone()
    execution_changed = row["status"] == "completed" and route is not None and route["state"] in ("needs-host", "fenced")
    if row["status"] not in ("needs-host", "failed", "cancelled") and not execution_changed:
        return None
    request = _json(row["requested_json"], {})
    snapshot = router_sequence.request_snapshot(connection, row["decision_id"])
    if (request.get("routingBasis") or {}).get("candidateCount") == 0:
        stopped, problem = _continuation_context(connection, route, run_id, state_dir)
        return build_boundary(
            kind="routing-changed", code=row["reason_code"],
            reason=reason_from_json(row["reason_code"], row["reason_params_json"]),
            candidates=[], facts=request, router_trials=[], retry_at=None, decision_id=row["decision_id"],
            run_id=run_id, shutdown_confirmed=stopped, continuation_problem=problem)
    if snapshot["facts"].get("routerCalled") is False:
        return None
    trials = router_trials(connection, row, now=now)
    code = row["reason_code"] or (request.get("routerProblem") or {}).get("code")
    current = [item for item in trials if item["taskId"] == row["decision_task_id"] and item["outcome"] == "answered"]
    if execution_changed:
        kind = "routing-changed"
    elif current and row["status"] == "needs-host" and row["reason_code"] == "router-abstained":
        kind = "router-abstained"
    elif any(item["outcome"] == "changed" for item in trials) or (
            code in _CHANGED_CODES and not any(item["taskId"] == row["decision_task_id"] and item["outcome"] == "no_answer" for item in trials)):
        kind = "routing-changed"
    else:
        kind = "router-unavailable"
    reason = reason_from_json(row["reason_code"], row["reason_params_json"])
    facts = deepcopy(snapshot["baseInput"])
    facts.update(deepcopy(snapshot["facts"]))
    facts["inspections"] = deepcopy(snapshot["inspections"])
    candidates = facts.get("buddies") or []
    retry_at = _retry_at(connection, snapshot["facts"],
                         trials, now=now, kind=kind)
    stopped, problem = _continuation_context(connection, route, run_id, state_dir)
    if any(item["outcome"] in ("stopUnknown", "unknown", "executing") for item in trials if item["taskId"]):
        stopped = False
    boundary = build_boundary(
        kind=kind, code=code, reason=reason, candidates=candidates, facts=facts,
        router_trials=trials, retry_at=retry_at, decision_id=row["decision_id"],
        run_id=run_id, shutdown_confirmed=stopped, continuation_problem=problem)
    boundary["candidates"] = [
        {**candidate, "problem": _candidate_problem(connection, candidate, request, now)}
        for candidate in candidates
    ]
    return boundary
