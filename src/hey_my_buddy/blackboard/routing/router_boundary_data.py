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
from ...protocol.reasons import reason_from_json
from .router_boundary import build_boundary
from .candidate import candidate_bound

_CHANGED_CODES = frozenset({
    "router-input-changed",
    "router-out-of-bounds", "workflow-routing-fenced", "router-input-too-large",
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
    buddy = connection.execute("SELECT * FROM buddies WHERE buddy_id=?",
                                 (item["buddyId"],)).fetchone()
    if item["code"] == "router-quota-exhausted":
        _, item["retryAt"] = _quota_recovery(connection, buddy, now)
        if (item["retryAt"] and state.get("inSkipWindow")
                and router_history._parse(state["retryAt"]) > router_history._parse(item["retryAt"])):
            item["retryAt"] = state["retryAt"]


def router_trials(connection, row, *, now):
    """Every observed/dispatch item, with its own receipt; unknown stays null."""
    snapshot = router_sequence.request_snapshot(connection, row["decision_id"])
    if snapshot is None:
        # Schema 15->16 did not backfill earlier decisions' frozen snapshots;
        # retain this until the explicit upgrade converts or marks that stock.
        return []
    request = _json(row["requested_json"], {})
    facts = request if request.get("selectionRetry") else snapshot["facts"]
    events = _events(connection, row)
    if request.get("selectionRetry"):
        rerouted = next((event["seq"] for event in reversed(events)
                         if event["kind"] == "decision.selection_rerouted"), None)
        if rerouted is not None:
            events = [event for event in events if event["seq"] > rerouted]
    inspected = ({item["index"]: item for item in request.get("selectionRetryInspections", [])}
                 if request.get("selectionRetry") else
                 {item["index"]: item for item in snapshot["inspections"]})
    dispatches = router_sequence.dispatches(connection, row["decision_id"])
    if request.get("selectionRetry"):
        retry_prefix = f"decision:{row['request_id']}:1:"
        dispatches = tuple(item for item in dispatches if connection.execute(
            "SELECT request_id FROM tasks WHERE task_id=?", (item["taskId"],)
        ).fetchone()["request_id"].startswith(retry_prefix))
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
                buddy = connection.execute("SELECT * FROM buddies WHERE buddy_id=?", (buddy_id,)).fetchone()
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
                         usage=deepcopy(output.get("usage") or _json(attempt["token_usage_json"])),
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


def _continuation_context(connection, row, run_id, owner_run_id):
    """Read existing permission/lineage/stop/workspace gates; grant no authority."""
    if run_id is None:
        return False, None, None, None
    run = connection.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
    if run is None:
        return False, "micro-task-not-found", None, None
    own_stopped = _stopped(connection, run_id)
    owner_id = owner_run_id or run_id
    owned = connection.execute(
        "WITH RECURSIVE owned(id) AS (SELECT ? UNION SELECT c.child_task_id FROM workflow_children c JOIN owned o ON c.parent_run_id=o.id) SELECT id FROM owned",
        (owner_id,)).fetchall()
    if run_id not in {item[0] for item in owned}:
        return own_stopped, "micro-task-not-owned", None, run
    route = connection.execute("SELECT * FROM workflow_routes WHERE decision_id=?", (row["decision_id"],)).fetchone()
    if (run["current_routing_id"] != row["decision_id"] or route is None or route["run_id"] != run_id
            or route["owner_generation"] != run["owner_generation"]):
        return own_stopped, "workflow-routing-fenced", None, run
    ancestors = connection.execute(
        "WITH RECURSIVE ancestors(id) AS (SELECT ? UNION SELECT c.parent_run_id FROM workflow_children c JOIN ancestors a ON c.child_task_id=a.id) SELECT w.* FROM workflow_runs w JOIN ancestors a ON w.run_id=a.id",
        (run_id,)).fetchall()
    if any(item["state"] in ("accepted", "concluded", "cancelled") for item in ancestors):
        return own_stopped, "ANCESTOR_TERMINAL", None, run
    if connection.execute("SELECT 1 FROM workspace_cleanup_plans WHERE run_id=? AND state IN ('applying','applied')", (run_id,)).fetchone():
        return own_stopped, "workspace-cleanup-started", None, run
    if not own_stopped:
        return False, None, None, run
    descendants = connection.execute(
        "WITH RECURSIVE owned(id) AS (SELECT ? UNION SELECT c.child_task_id FROM workflow_children c JOIN owned o ON c.parent_run_id=o.id) SELECT c.* FROM workflow_children c JOIN owned o ON c.child_task_id=o.id",
        (run_id,)).fetchall()
    helper_policy = "keep" if any(item["state"] in ("active", "attention") or not _stopped(
        connection, item["child_task_id"]) for item in descendants) else None
    for child in descendants:
        attempts = connection.execute("SELECT * FROM attempts WHERE task_id=?", (child["child_task_id"],)).fetchall()
        if any(item["execution_state"] == "uncertain" or (item["execution_state"] == "finished" and item["shutdown_confirmed"] != 1) for item in attempts):
            return False, None, helper_policy, run
    ids = [run_id, *(item["child_task_id"] for item in descendants)]
    markers = ','.join('?' for _ in ids)
    routing_tasks = connection.execute(
        f"SELECT r.task_id FROM decision_requests r JOIN workflow_routes w ON w.decision_id=r.decision_id WHERE w.run_id IN ({markers}) AND r.task_id IS NOT NULL", ids)
    if any(not _stopped(connection, item[0]) for item in routing_tasks):
        return False, None, helper_policy, run
    manifest = _json(run["workspace_manifest_json"], {})
    if manifest.get("checkoutId"):
        reservations = connection.execute("SELECT * FROM workspace_reservations WHERE holder_task_id=? ORDER BY rowid DESC", (run_id,)).fetchall()
        current = next((item for item in reservations if item["state"] in ("held", "transferred")), None)
        current = current or next((item for item in reservations if item["checkout_id"] == manifest["checkoutId"]), None)
        task = connection.execute("SELECT cwd FROM tasks WHERE task_id=?", (run_id,)).fetchone()
        reservation_access = "read" if manifest["access"] == "read" else "write"
        if current is None or task is None or current["access"] != reservation_access or task["cwd"] != manifest.get("path") or any(
                current[key] != manifest.get(field)
                for key, field in (
                ("checkout_id", "checkoutId"), ("repository_id", "repositoryId"), ("path", "path"))):
            return True, "workspace-ownership-lost", helper_policy, run
        conflicts = connection.execute(
            "SELECT holder_task_id FROM workspace_reservations WHERE checkout_id=?"
            " AND holder_task_id!=? AND state='held' AND (access='write' OR ?='write')",
            (manifest["checkoutId"], run_id, reservation_access)).fetchall()
        if conflicts and (current["state"] != "transferred" or any(
                item["holder_task_id"] not in ids for item in conflicts)):
            return True, "workspace-ownership-lost", helper_policy, run
    return True, None, helper_policy, run


def _candidate_problem(connection, candidate, request, run, now):
    published = connection.execute("SELECT * FROM buddies WHERE buddy_id=?", (candidate["buddyId"],)).fetchone()
    constraints = request.get("constraints", {})
    if run is not None and run["buddy_locked"]:
        spec = connection.execute("SELECT spec_json FROM tasks WHERE task_id=?", (run["run_id"],)).fetchone()
        constraints = {**constraints, **schemas.buddy_constraints(_json(spec["spec_json"], {}))}
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
        buddy = connection.execute("SELECT * FROM buddies WHERE buddy_id=?", (buddy_id,)).fetchone()
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


def routing_boundary(connection, row, *, now, run_id=None, revision=None,
                     owner_run_id=None, owner_revision=None) -> dict | None:
    """Build a current readable Host boundary from frozen evidence, without writes."""
    route = connection.execute("SELECT * FROM workflow_routes WHERE decision_id=?", (row["decision_id"],)).fetchone()
    execution_changed = row["status"] == "completed" and route is not None and route["state"] in ("needs-host", "fenced")
    if row["status"] not in ("needs-host", "failed", "cancelled") and not execution_changed:
        return None
    request = _json(row["requested_json"], {})
    if request.get("routerCalled") is False:
        return None
    if (request.get("routingBasis") or {}).get("candidateCount") == 0:
        stopped, problem, helper_policy, _run = _continuation_context(connection, row, run_id, owner_run_id)
        return build_boundary(
            kind="routing-changed", code=row["reason_code"],
            reason=reason_from_json(row["reason_code"], row["reason_params_json"]),
            candidates=[], facts=request, router_trials=[], retry_at=None, decision_id=row["decision_id"],
            run_id=owner_run_id or run_id, revision=owner_revision if owner_run_id else revision,
            target_run_id=run_id if owner_run_id and owner_run_id != run_id else None,
            helper_policy=helper_policy, shutdown_confirmed=stopped, continuation_problem=problem,
            explicit_buddy=True)
    snapshot = router_sequence.request_snapshot(connection, row["decision_id"])
    if snapshot is None:
        # Schema 15->16 did not backfill earlier decisions' frozen snapshots;
        # retain this until the explicit upgrade converts or marks that stock.
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
    if request.get("selectionRetry"):
        facts = deepcopy(_json(row["input_json"], {}))
        facts.update(deepcopy(request))
        facts["inspections"] = deepcopy(request.get("selectionRetryInspections", []))
    else:
        facts = deepcopy(snapshot["baseInput"])
        facts.update(deepcopy(snapshot["facts"]))
        facts["inspections"] = deepcopy(snapshot["inspections"])
    candidates = facts.get("buddies") or []
    retry_at = _retry_at(connection, request if request.get("selectionRetry") else snapshot["facts"],
                         trials, now=now, kind=kind)
    stopped, problem, helper_policy, run = _continuation_context(connection, row, run_id, owner_run_id)
    dispatch_docs = router_sequence.dispatches(connection, row["decision_id"])
    if any(not _stopped(connection, item["taskId"]) for item in dispatch_docs) or any(
            item["outcome"] in ("stopUnknown", "unknown", "executing") for item in trials if item["taskId"]):
        stopped = False
    boundary = build_boundary(
        kind=kind, code=code, reason=reason, candidates=candidates, facts=facts,
        router_trials=trials, retry_at=retry_at, decision_id=row["decision_id"],
        run_id=owner_run_id or run_id, revision=owner_revision if owner_run_id else revision,
        target_run_id=run_id if owner_run_id and owner_run_id != run_id else None,
        helper_policy=helper_policy, shutdown_confirmed=stopped, continuation_problem=problem)
    choices = boundary["commands"]["continue"]["choices"]
    for choice, candidate in zip(choices, candidates):
        candidate_problem = _candidate_problem(connection, candidate, request, run, now)
        choice.update(blocked=candidate_problem is not None, code=None)
        if candidate_problem is not None:
            choice.update(candidate_problem)
    if choices and all(choice["blocked"] for choice in choices):
        boundary["commands"]["continue"]["blocked"] = True
    return boundary
