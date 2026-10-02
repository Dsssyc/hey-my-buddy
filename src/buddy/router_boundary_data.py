"""Read-only Host projection of frozen Router packets and each item's evidence.

No resolution, native/account probe, retry consumption or state transition belongs
here. Dispatches and their own bound receipts supply actors and outcomes; the
current request output cannot stand in for a previous item's result.
"""
from __future__ import annotations

import json
from copy import deepcopy

from . import router_history, router_sequence, schemas
from .router_boundary import build_boundary
from .errors import BoardError

_CHANGED_CODES = frozenset({
    "router-input-changed", "router-configuration-changed", "router-profile-changed",
    "router-table-changed", "router-reader-open", "router-reader-fenced",
    "router-out-of-bounds", "router-dispatch-unverified", "workflow-routing-fenced",
    "ACCOUNT_BINDING_CHANGED", "CONFIGURATION_CONFLICT", "router-input-too-large",
})


def _json(value, default=None):
    if value is None:
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def _events(connection, row):
    rows = connection.execute(
        "SELECT seq,kind,task_id,attempt_id,created_at,payload_json FROM events WHERE "
        "(kind IN ('router.no_answer','router.answered','router.claimed') AND "
        "json_extract(CASE WHEN json_valid(payload_json) THEN payload_json END,'$.requestId')=?) OR "
        "(kind LIKE 'decision.%' AND json_extract(CASE WHEN json_valid(payload_json) THEN payload_json END,'$.decisionId')=?) ORDER BY seq",
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


def _state(connection, profile_id, facts, now):
    interval = facts.get("routerRetryIntervalSeconds")
    if type(interval) is not int or interval < 1:
        return {}
    return router_history.router_state(connection, profile_id=profile_id, interval_seconds=interval, now=now)


def _cached_available(connection, adapter):
    """Secret-free cached health/account matching, without native eligibility probes."""
    from .accounts import identity, selection
    health = connection.execute("SELECT status,record_json FROM harness_health WHERE adapter=?", (adapter,)).fetchone()
    if health is None or health["status"] != "ready":
        return False
    record = _json(health["record_json"], {})
    observed = record.get("account") or {"source": "native", "credentialRevision": 0}
    try:
        return identity(observed) == identity(selection(connection, adapter))
    except BoardError:
        return False


def _quota_recovery(connection, profile, now):
    """Only recorded reset times are recovery facts; never invent a quota fuse."""
    from .quota_routing import blocking_records
    records = blocking_records(connection, profile, now=now)
    if not records:
        return False, None
    resets = [record["item"].get("resetsAt") for record in records]
    parsed = [router_history._parse(value) for value in resets]
    if not all(parsed):
        return True, None
    return True, resets[max(range(len(resets)), key=lambda index: parsed[index])]


def _preflight_no_answer(connection, item, event, state, now):
    """Project a recorded qualification failure, including after a dispatch queued."""
    item.update(outcome="no_answer", phase=event["payload"].get("phase"),
                code=event["payload"].get("code"), retryAt=None)
    profile = connection.execute("SELECT * FROM evaluation_profiles WHERE profile_id=?",
                                 (item["profileId"],)).fetchone()
    if profile is not None and item["code"] == "router-quota-exhausted":
        _, item["retryAt"] = _quota_recovery(connection, profile, now)
        if (item["retryAt"] and state.get("inSkipWindow")
                and router_history._parse(state["retryAt"]) > router_history._parse(item["retryAt"])):
            item["retryAt"] = state["retryAt"]
    if item["retryAt"] is None:
        item["reason"] = (item["reason"] or "Cached Router qualification, health or quota is unavailable") + "; no recovery time is recorded"


def router_trials(connection, row, *, now):
    """Every observed/dispatch item, with its own receipt; unknown stays null."""
    snapshot = router_sequence.request_snapshot(connection, row["decision_id"])
    if snapshot is None:
        return []
    facts = snapshot["facts"]
    events = _events(connection, row)
    terminal = next((event for event in reversed(events) if event["kind"] in (
        "decision.completed", "decision.needs_host", "decision.cancelled", "decision.stale", "decision.failed")), None)
    inspected = {item["index"]: item for item in snapshot["inspections"]}
    dispatched = {item["routerIndex"]: item for item in router_sequence.dispatches(connection, row["decision_id"])}
    for event in events:
        payload = event["payload"]
        if event["kind"] == "router.no_answer" and payload.get("phase") == "preflight" and payload.get("taskId") is None:
            index = payload.get("routerIndex")
            inspected.setdefault(index, {"index": index, "profileId": payload.get("profileId"),
                                         "code": payload.get("code"), "reason": None})
    for index, profile_id in enumerate(facts.get("routerProfileIds") or []):
        inspected.setdefault(index, {"index": index, "profileId": profile_id, "unvisited": True})
    trials = []
    for index in sorted(set(inspected) | set(dispatched)):
        inspection = inspected.get(index, {})
        doc = dispatched.get(index)
        profile_id = doc["profileId"] if doc else inspection.get("profileId")
        identities = facts.get("routerIdentities") or []
        identity = doc["profile"] if doc else inspection.get("identity") or (
            identities[index] if type(index) is int and index < len(identities) else None)
        state = _state(connection, profile_id, facts, now) if profile_id else {}
        item = {"profileId": profile_id, "identity": deepcopy(identity), "index": index,
                "phase": "preflight", "outcome": "unknown", "code": inspection.get("code"),
                "reason": inspection.get("reason"), "taskId": doc["taskId"] if doc else None,
                "attemptId": None, "shutdownConfirmed": None,
                "consecutiveNoAnswers": state.get("consecutiveNoAnswers"),
                "retryAt": state.get("retryAt"), "usage": None, "nativeIdentity": None}
        matching = [event for event in events if event["payload"].get("routerIndex") == index
                    and event["payload"].get("profileId") == profile_id]
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
                item.update(outcome="skipped", code=None, reason="Eligible item was not dispatched")
            elif inspection.get("unvisited"):
                item.update(phase=None, outcome="skipped", reason="This item was not reached by a recorded dispatch or inspection")
            if item["code"] not in (None, "router-skip-window"):
                item["retryAt"] = None
                profile = connection.execute("SELECT * FROM evaluation_profiles WHERE profile_id=?", (profile_id,)).fetchone()
                if profile is not None and item["code"] == "router-quota-exhausted":
                    _, item["retryAt"] = _quota_recovery(connection, profile, now)
                    if item["retryAt"] and state.get("inSkipWindow") and router_history._parse(state["retryAt"]) > router_history._parse(item["retryAt"]):
                        item["retryAt"] = state["retryAt"]
                if item["retryAt"] is None:
                    item["reason"] = (item["reason"] or "Cached Router qualification, health or quota is unavailable") + "; no recovery time is recorded"
            trials.append(item)
            continue
        task_id = doc["taskId"]
        task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        attempts = connection.execute("SELECT * FROM attempts WHERE task_id=? ORDER BY generation", (task_id,)).fetchall()
        # All claim generations remain visible rather than borrowing the latest
        # request binding. Usually a Router dispatch has exactly one attempt.
        bound = []
        for attempt in attempts:
            binding_row = connection.execute("SELECT value FROM meta WHERE key=?",
                                             (router_sequence.ATTEMPT_KEY_PREFIX + attempt["attempt_id"],)).fetchone()
            binding = _json(binding_row[0], {}) if binding_row else {}
            if (binding.get("decisionId") == row["decision_id"] and binding.get("taskId") == task_id
                    and binding.get("attemptId") == attempt["attempt_id"]
                    and binding.get("generation") == attempt["generation"]
                    and binding.get("profileId") == profile_id and binding.get("routerIndex") == index):
                bound.append(attempt)
        if not bound:
            item.update(phase="queued", outcome="queued", code=None, reason=None)
            if attempts:
                item.update(outcome="unknown", code="router-dispatch-unverified",
                            reason="The attempt has no matching immutable dispatch binding")
            elif (no_answer := next((event for event in reversed(matching)
                    if event["kind"] == "router.no_answer" and event["payload"].get("phase") == "preflight"
                    and event["payload"].get("taskId") in (None, task_id)
                    and event["payload"].get("attemptId") is None), None)) is not None:
                _preflight_no_answer(connection, item, no_answer, state, now)
            elif row["decision_task_id"] == task_id and row["status"] in ("cancelled", "stale", "needs-host", "failed"):
                item.update(outcome="cancelled" if row["status"] == "cancelled" else "changed" if row["status"] == "stale" or row["error"] in _CHANGED_CODES else "skipped",
                            phase="preflight", code=row["error"], reason=row["reason"])
            item["shutdownConfirmed"] = True if _stopped(connection, task_id) else None
            trials.append(item)
            continue
        for attempt in bound:
            trial = deepcopy(item)
            receipt = _json(attempt["result_json"], {})
            output = receipt.get("result") if isinstance(receipt.get("result"), dict) else {}
            adopted = _json(row["output_json"], {}) if row["decision_task_id"] == task_id else {}
            own_events = [event for event in matching if event["payload"].get("taskId") == task_id
                          and event["payload"].get("attemptId") == attempt["attempt_id"]
                          and event["kind"] in ("router.no_answer", "router.answered")]
            outcome_event = own_events[-1] if own_events else None
            trial.update(attemptId=attempt["attempt_id"], phase="runtime", code=None, reason=None,
                         usage=deepcopy(output.get("usage") or _json(attempt["token_usage_json"])),
                         nativeIdentity=deepcopy(output.get("nativeIdentity")))
            outer_stopped = attempt["execution_state"] == "finished" and attempt["shutdown_confirmed"] == 1
            stop = output.get("stopEvidence") if isinstance(output.get("stopEvidence"), dict) else {}
            native = stop.get("native") if isinstance(stop.get("native"), dict) else {}
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
                             code=outcome_event["payload"].get("code"),
                             reason=(output.get("reason") or output.get("error")) if output.get("status") == "error" else (
                                 output["decision"].get("reason") if outcome_event["kind"] == "router.answered"
                                 and isinstance(output.get("decision"), dict) else None))
            elif (row["decision_task_id"] == task_id and terminal is not None
                  and terminal["kind"] in ("decision.completed", "decision.needs_host")
                  and row["error"] is None and not terminal["payload"].get("errorCode")
                  and not terminal["payload"].get("error") and confirmed
                  and receipt.get("status") == "ok" and output.get("status") == "ok"
                  and isinstance(output.get("decision"), dict)
                  and adopted.get("status") == "ok" and "policyCheck" in adopted
                  and not adopted.get("code") and adopted.get("decision") == output["decision"]):
                # _finish publishes its terminal event before notifying the
                # workflow, then R4 appends router.answered in that transaction.
                # That program event is sufficient during the notification;
                # a null result or model prose by itself never proves adoption.
                trial.update(outcome="answered", phase="publication", reason=output["decision"].get("reason"))
            elif receipt.get("status") == "cancelled" or attempt["cancel_requested_at"] or (task and task["state"] == "cancelled") or (row["decision_task_id"] == task_id and row["status"] == "cancelled"):
                trial.update(outcome="cancelled", code="router-cancelled", reason="The dispatch was cancelled")
            elif row["decision_task_id"] == task_id and (row["status"] == "stale" or row["error"] in _CHANGED_CODES):
                trial.update(outcome="changed", code=row["error"], reason=row["reason"])
            elif not confirmed and (receipt or attempt["execution_state"] in ("uncertain", "finished")):
                trial.update(outcome="stopUnknown", code="router-stop-unconfirmed", reason="The dispatch stop is unconfirmed")
            elif attempt["execution_state"] in ("starting", "executing", "finalizing"):
                trial.update(outcome="executing")
            else:
                trial.update(outcome="unknown", reason="No immutable adopted outcome is recorded")
            trials.append(trial)
    return trials


def _continuation_context(connection, row, run_id, owner_run_id):
    """Read existing permission/lineage/stop/workspace gates; grant no authority."""
    if run_id is None:
        return False, None, None, None
    run = connection.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
    if run is None:
        return False, "No governed goal is recorded", None, None
    own_stopped = _stopped(connection, run_id)
    owner_id = owner_run_id or run_id
    owned = connection.execute(
        "WITH RECURSIVE owned(id) AS (SELECT ? UNION SELECT c.child_task_id FROM workflow_children c JOIN owned o ON c.parent_run_id=o.id) SELECT id FROM owned",
        (owner_id,)).fetchall()
    if run_id not in {item[0] for item in owned}:
        return own_stopped, "The target is not owned by this controlling goal", None, run
    route = connection.execute("SELECT * FROM workflow_routes WHERE decision_id=?", (row["decision_id"],)).fetchone()
    if (run["current_routing_id"] != row["decision_id"] or route is None or route["run_id"] != run_id
            or route["owner_generation"] != run["owner_generation"]):
        return own_stopped, "The routing request or owner was fenced", None, run
    ancestors = connection.execute(
        "WITH RECURSIVE ancestors(id) AS (SELECT ? UNION SELECT c.parent_run_id FROM workflow_children c JOIN ancestors a ON c.child_task_id=a.id) SELECT w.* FROM workflow_runs w JOIN ancestors a ON w.run_id=a.id",
        (run_id,)).fetchall()
    if any(item["state"] in ("accepted", "cancelled") for item in ancestors):
        return own_stopped, "The goal or an owning ancestor is accepted or cancelled", None, run
    if connection.execute("SELECT 1 FROM workspace_cleanup_plans WHERE run_id=? AND state IN ('applying','applied')", (run_id,)).fetchone():
        return own_stopped, "The goal checkout is being cleaned or was removed", None, run
    if not own_stopped:
        return False, "The goal's execution has no confirmed stop", None, run
    descendants = connection.execute(
        "WITH RECURSIVE owned(id) AS (SELECT ? UNION SELECT c.child_task_id FROM workflow_children c JOIN owned o ON c.parent_run_id=o.id) SELECT c.* FROM workflow_children c JOIN owned o ON c.child_task_id=o.id",
        (run_id,)).fetchall()
    helper_policy = "keep" if any(item["state"] in ("active", "attention") or not _stopped(
        connection, item["child_task_id"]) for item in descendants) else None
    for child in descendants:
        attempts = connection.execute("SELECT * FROM attempts WHERE task_id=?", (child["child_task_id"],)).fetchall()
        if any(item["execution_state"] == "uncertain" or (item["execution_state"] == "finished" and item["shutdown_confirmed"] != 1) for item in attempts):
            return False, "An owned helper's stop is unknown", helper_policy, run
    ids = [run_id, *(item["child_task_id"] for item in descendants)]
    markers = ','.join('?' for _ in ids)
    routing_tasks = connection.execute(
        f"SELECT r.task_id FROM decision_requests r JOIN workflow_routes w ON w.decision_id=r.decision_id WHERE w.run_id IN ({markers}) AND r.task_id IS NOT NULL", ids)
    if any(not _stopped(connection, item[0]) for item in routing_tasks):
        return False, "An owned Router dispatch has no confirmed stop", helper_policy, run
    manifest = _json(run["workspace_manifest_json"], {})
    if manifest.get("checkoutId"):
        reservations = connection.execute("SELECT * FROM workspace_reservations WHERE holder_task_id=? ORDER BY rowid DESC", (run_id,)).fetchall()
        current = next((item for item in reservations if item["state"] in ("held", "transferred")), None)
        current = current or next((item for item in reservations if item["checkout_id"] == manifest["checkoutId"]), None)
        task = connection.execute("SELECT cwd FROM tasks WHERE task_id=?", (run_id,)).fetchone()
        if current is None or task is None or task["cwd"] != manifest.get("path") or any(current[key] != manifest.get(field) for key, field in (
                ("checkout_id", "checkoutId"), ("repository_id", "repositoryId"), ("path", "path"), ("access", "access"))):
            return True, "The goal no longer owns its frozen checkout allocation", helper_policy, run
        conflicts = connection.execute(
            "SELECT holder_task_id FROM workspace_reservations WHERE checkout_id=? AND holder_task_id!=? AND state='held' AND (access='write' OR ?='write')",
            (manifest["checkoutId"], run_id, manifest["access"])).fetchall()
        if conflicts and (current["state"] != "transferred" or any(
                item["holder_task_id"] not in ids for item in conflicts)):
            return True, "Another owner holds the goal's checkout", helper_policy, run
    return True, None, helper_policy, run


def _candidate_problem(connection, candidate, request, run, now):
    published = connection.execute("SELECT * FROM evaluation_profiles WHERE profile_id=?", (candidate["profileId"],)).fetchone()
    if published is None or any(published[key] != candidate[key] for key in schemas.CONFIGURATION_FIELDS):
        return "The published identity no longer matches the frozen buddy tuple"
    if not published["enabled"] or not published["available"]:
        return "The frozen buddy is no longer enabled and available"
    constraints = request.get("constraints") or {}
    if run is not None and run["configuration_locked"]:
        constraints = {**constraints, **schemas.configuration_constraints(_json(run["goal_json"], {}))}
    if any(candidate.get(key) != value for key, value in constraints.items()):
        return "The frozen buddy no longer satisfies the goal's hard constraints"
    capabilities = set(_json(published["capabilities_json"], []))
    if not set(request.get("requiredCapabilities") or []) <= capabilities:
        return "The frozen buddy no longer provides the required capabilities"
    preference = connection.execute("SELECT mode FROM effective_preferences WHERE profile_id=?", (candidate["profileId"],)).fetchone()
    pins = connection.execute("SELECT 1 FROM effective_preferences WHERE mode='pin' LIMIT 1").fetchone()
    if preference and preference[0] == "exclude" or pins and (not preference or preference[0] != "pin"):
        return "The frozen buddy no longer satisfies the published pins and exclusions"
    if not _cached_available(connection, candidate["adapter"]):
        return "Cached harness health does not permit this buddy"
    from .native_observations import exhausted
    if exhausted(connection, published, now=now) is not None:
        return "Recorded native quota still blocks this buddy; no recovery is assumed"
    return None


def _retry_at(connection, facts, trials, *, now, kind):
    if any(item["outcome"] in ("stopUnknown", "unknown", "executing") for item in trials if item["taskId"]):
        return None
    if kind in ("routing-changed", "router-abstained"):
        return now
    known = []
    for index, profile_id in enumerate(facts.get("routerProfileIds") or []):
        identity = (facts.get("routerIdentities") or [])[index]
        profile = connection.execute("SELECT * FROM evaluation_profiles WHERE profile_id=?", (profile_id,)).fetchone()
        if profile is None or identity is None or any(profile[key] != identity[key] for key in schemas.CONFIGURATION_FIELDS) or not profile["enabled"] or not profile["available"]:
            continue
        if not _cached_available(connection, profile["adapter"]):
            continue
        quota_blocked, quota_at = _quota_recovery(connection, profile, now)
        if quota_blocked and quota_at is None:
            continue
        trial = next((item for item in reversed(trials) if item["index"] == index), None)
        if trial and trial["phase"] == "preflight" and trial["code"] in (
                "router-no-tool-unsupported", "router-review-unsupported"):
            # Qualification/health/quota has no known restoration time. Its
            # no-answer event is real, but interval expiry cannot repair it.
            continue
        state = _state(connection, profile_id, facts, now)
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
    if row["status"] not in ("needs-host", "failed", "stale", "cancelled") and not execution_changed:
        return None
    request = _json(row["requested_json"], {})
    # Direct sole/explicit selections and no-legal-candidates have their own
    # program boundary; inventing Router trials would misattribute that path.
    if request.get("routerCalled") is False or (request.get("routingBasis") or {}).get("candidateCount") == 0:
        return None
    snapshot = router_sequence.request_snapshot(connection, row["decision_id"])
    if snapshot is None:
        return None  # Never reconstruct a legacy packet from today's catalog.
    trials = router_trials(connection, row, now=now)
    code = row["error"] or (request.get("routerProblem") or {}).get("code")
    current = [item for item in trials if item["taskId"] == row["decision_task_id"] and item["outcome"] == "answered"]
    if execution_changed:
        kind = "routing-changed"
    elif current and row["status"] == "needs-host" and row["error"] is None:
        kind = "router-abstained"
    elif row["status"] == "stale" or any(item["outcome"] == "changed" for item in trials) or (
            code in _CHANGED_CODES and not any(item["taskId"] == row["decision_task_id"] and item["outcome"] == "no_answer" for item in trials)):
        kind = "routing-changed"
    else:
        kind = "router-unavailable"
    reason = (route["reason"] if execution_changed else row["reason"]) or "Router 不可用：没有可用答案"
    if kind == "router-unavailable" and "Router 不可用" not in reason:
        reason = "Router 不可用：" + reason
    facts = deepcopy(snapshot.get("hostPacket") or snapshot["baseInput"])
    facts.update(deepcopy(snapshot["facts"]))
    facts["inspections"] = deepcopy(snapshot["inspections"])
    candidates = facts.get("profiles") or []
    retry_at = _retry_at(connection, snapshot["facts"], trials, now=now, kind=kind)
    if retry_at is None:
        reason += "; cached qualification, health or quota has no recorded recovery time"
    stopped, problem, helper_policy, run = _continuation_context(connection, row, run_id, owner_run_id)
    dispatch_docs = router_sequence.dispatches(connection, row["decision_id"])
    if any(not _stopped(connection, item["taskId"]) for item in dispatch_docs) or any(
            item["outcome"] in ("stopUnknown", "unknown", "executing") for item in trials if item["taskId"]):
        stopped, problem = False, "A Router dispatch stop is unknown"
    boundary = build_boundary(
        kind=kind, code=code, reason=reason, candidates=candidates, facts=facts,
        router_trials=trials, retry_at=retry_at, decision_id=row["decision_id"],
        run_id=owner_run_id or run_id, revision=owner_revision if owner_run_id else revision,
        target_run_id=run_id if owner_run_id and owner_run_id != run_id else None,
        helper_policy=helper_policy, shutdown_confirmed=stopped, continuation_problem=problem)
    choices = boundary["commands"]["continue"]["choices"]
    for choice, candidate in zip(choices, candidates):
        candidate_problem = _candidate_problem(connection, candidate, request, run, now)
        choice.update(blocked=candidate_problem is not None, reason=candidate_problem)
    if choices and all(choice["blocked"] for choice in choices):
        boundary["commands"]["continue"].update(blocked=True, reason="Every frozen candidate is currently outside the hard bounds")
    return boundary
