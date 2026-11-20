"""Router outcome facts: idempotent recording and read-only per-buddy projections.

The routing plane records ``router.answered``/``router.no_answer`` receipts.

One non-format failure or format failures on two distinct requests open a skip
window. A valid answer, including an abstention, resets it. Reads project
windows without writing and never extend them, and an expired window can retry
again. The single in-flight retry is derived from relational Router claims and
active or shutdown-unconfirmed attempts — there is no half-open state or new
circuit breaker; queuing belongs to the execution layers.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from ..store.db import canonical_json, utc_now
from ...errors import BoardError

OUTCOMES = ("answered", "no_answer")
FORMAT_FAILURES = frozenset({"answer-shape", "answer-invalid-json", "answer-empty",
                             "answer-not-json", "answer-unexpected-field"})
#: Attempt states that still own capacity: active execution or unconfirmed stop.
#: Missing PIDs and expired leases never prove a stop, so ``uncertain`` stays here.
ACTIVE_ATTEMPT_STATES = ("starting", "executing", "finalizing", "uncertain")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _later(left: str | None, right: str | None) -> str | None:
    """The later of two board timestamps."""
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right, key=_parse)


def _append_event(connection, *, kind: str, task_id: str | None, attempt_id: str | None,
                  payload: dict, now: str) -> int:
    """One event inside the caller's transaction, matching the board's append shape."""
    from ..tasks.macro_tasks import record_activity
    cursor = connection.execute(
        "INSERT INTO events(task_id, attempt_id, revision, kind, payload_json, created_at) VALUES(?,?,?,?,?,?)",
        (task_id, attempt_id, None, kind, canonical_json(payload), now),
    )
    seq = int(cursor.lastrowid)
    record_activity(connection, task_id, seq, now)
    return seq


def record_outcome(connection, *, buddy_id: str, request_id: str, task_id: str | None,
                   attempt_id: str | None, outcome: str, code: str | None, phase: str,
                   facts: dict, now: str) -> dict:
    """Record one idempotent routing outcome fact; never executes anything.

    The idempotency key is routing/requestId/selectionRetry/routerIndex/phase/taskId/attemptId/outcome
    with ``routerIndex`` and the retry marker taken from ``facts``; its receipt lives in ``router_outcomes`` and
    commits in the caller's transaction together with the event. A replay with the
    same facts returns the original record, different facts raise ``CONFLICT``.
    Only callers that observed an actual outcome record one: cancellations, input or
    settings changes, fencing and stop-unknown are never recorded as answers here.
    """
    router_index = facts["routerIndex"]
    selection_retry = facts.get("selectionRetry", False)
    payload = {"requestId": request_id, "routerIndex": router_index,
               "buddyId": buddy_id, "phase": phase, "taskId": task_id,
               "attemptId": attempt_id, "outcome": outcome, "code": code}
    if selection_retry:
        payload["selectionRetry"] = True
    key = hashlib.sha256(canonical_json(
        ["routing", request_id, selection_retry, router_index, phase, task_id, attempt_id, outcome]).encode("utf-8")).hexdigest()
    kind = f"router.{outcome}"
    stored = connection.execute("SELECT request_id,router_index,buddy_id,phase,task_id,attempt_id,"
                                "outcome,code,event_seq FROM router_outcomes WHERE receipt_id=?", (key,)).fetchone()
    if stored is not None:
        expected = (request_id, router_index, buddy_id, phase, task_id, attempt_id, outcome, code)
        if tuple(stored)[:-1] != expected:
            raise BoardError("CONFLICT", "router outcome facts conflict with the recorded receipt",
                             buddyId=buddy_id, requestId=request_id)
        return {"recorded": False, "seq": int(stored[-1]), "event": kind}
    seq = _append_event(connection, kind=kind, task_id=task_id, attempt_id=attempt_id,
                        payload=payload, now=now)
    connection.execute("INSERT INTO router_outcomes(receipt_id,request_id,router_index,buddy_id,phase,"
                       "task_id,attempt_id,outcome,code,event_seq) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (key, request_id, router_index, buddy_id, phase, task_id, attempt_id,
                        outcome, code, seq))
    return {"recorded": True, "seq": seq, "event": kind}


def _fact_entries(connection, buddy_id: str) -> list[dict]:
    """Recorded outcome receipts of one buddy, ordered by the event sequence."""
    entries = []
    rows = connection.execute(
        "SELECT o.event_seq, e.created_at AS recorded_at, o.request_id, o.router_index,"
        " o.phase, o.task_id, o.attempt_id, o.outcome, o.code"
        " FROM router_outcomes o JOIN events e ON e.seq=o.event_seq"
        " WHERE o.buddy_id=? ORDER BY o.event_seq", (buddy_id,))
    for row in rows:
        entries.append({"seq": int(row["event_seq"]), "at": row["recorded_at"],
                        "outcome": row["outcome"],
                        "requestId": row["request_id"], "routerIndex": row["router_index"],
                        "phase": row["phase"], "taskId": row["task_id"],
                        "attemptId": row["attempt_id"], "code": row["code"]})
    return entries


def buddy_timeline(connection, *, buddy_id: str) -> list[dict]:
    """One buddy's outcome timeline from durable Router outcome receipts."""
    return _fact_entries(connection, buddy_id)


def active_executions(connection, *, buddy_id: str) -> list[dict]:
    """Active or shutdown-unconfirmed executions bound to one buddy, as facts only.

    Bindings come from immutable claim and dispatch rows. Nothing here starts, stops or queues
    anything: the single-retry constraint and capacity queuing belong to the
    execution layers.
    """
    markers = ",".join("?" for _ in ACTIVE_ATTEMPT_STATES)
    rows = connection.execute(
        "SELECT c.task_id, c.attempt_id, c.generation, r.request_id, a.execution_state,"
        " COALESCE(a.started_at, a.created_at) AS started_at"
        " FROM router_claims c JOIN router_dispatches d ON d.task_id=c.task_id"
        " JOIN attempts a ON a.attempt_id=c.attempt_id"
        " JOIN decision_requests r ON r.decision_id=d.decision_id"
        f" WHERE d.buddy_id=? AND a.execution_state IN ({markers})"
        " ORDER BY a.created_at, c.attempt_id", (buddy_id, *ACTIVE_ATTEMPT_STATES))
    return [{"taskId": row["task_id"], "attemptId": row["attempt_id"],
             "generation": row["generation"], "requestId": row["request_id"],
             "executionState": row["execution_state"], "startedAt": row["started_at"],
             "source": "claimed"} for row in rows]


def summarize(timeline: list[dict], *, interval_seconds: int, now: str | None = None) -> dict:
    """Pure projection of one buddy's timeline: streaks, skip window and retry facts.

    The consecutive count covers every recorded no-answer since the last valid
    answered, independently of any display window, and each buddy's counts stay
    separate. Once triggered, each failure refreshes the window until an answer
    resets it. Expiration alone leaves the next request eligible for a retry.
    """
    answered_count = no_answer_count = consecutive = 0
    last_answered = last_no_answer = None
    window_triggered = False
    format_request_ids: set[str] = set()
    skip_until = None
    for entry in timeline:
        outcome = entry["outcome"]
        if outcome == "answered":
            answered_count += 1
            consecutive = 0
            window_triggered = False
            format_request_ids.clear()
            skip_until = None
            last_answered = _later(last_answered, entry["at"])
        elif outcome == "no_answer":
            no_answer_count += 1
            consecutive += 1
            last_no_answer = _later(last_no_answer, entry["at"])
            if not window_triggered and entry["code"] in FORMAT_FAILURES:
                format_request_ids.add(entry["requestId"])
                if len(format_request_ids) < 2:
                    continue
            window_triggered = True
            deadline = _parse(entry["at"]) + timedelta(seconds=interval_seconds)
            skip_until = deadline.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    present = _parse(now if now is not None else utc_now())
    in_window = skip_until is not None and present < _parse(skip_until)
    return {"answeredCount": answered_count, "noAnswerCount": no_answer_count,
            "lastAnsweredAt": last_answered, "lastNoAnswerAt": last_no_answer,
            "consecutiveNoAnswers": consecutive, "skipUntil": skip_until,
            "retryAt": skip_until, "inSkipWindow": in_window}


def router_state(connection, *, buddy_id: str, interval_seconds: int, now: str | None = None) -> dict:
    """The read-only retry facts of one buddy for the resolution entry.

    ``retryInProgress`` describes a claim after an expired skip window while an
    execution is active or its stop is unconfirmed.
    """
    timeline = buddy_timeline(connection, buddy_id=buddy_id)
    summary = summarize(timeline, interval_seconds=interval_seconds, now=now)
    attempts = active_executions(connection, buddy_id=buddy_id)
    return {**summary, "buddyId": buddy_id,
            "retryInProgress": bool(attempts) and summary["skipUntil"] is not None and not summary["inSkipWindow"],
            "activeAttempts": attempts}


__all__ = ["ACTIVE_ATTEMPT_STATES", "OUTCOMES", "active_executions",
           "buddy_timeline", "record_outcome", "router_state", "summarize"]
