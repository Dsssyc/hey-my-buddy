"""Router outcome facts: idempotent recording and read-only per-buddy projections.

One entry point records ``router.answered``/``router.no_answer`` facts for every
Router plane (work, maintenance, portrait); reads project only those receipts.

Skip windows come from the last recorded no-answer plus the current retry interval;
a later valid answered (including a valid abstention) removes it. Reads project
windows without writing and never extend them, and an expired window can retry
again. The single in-flight retry is derived from existing ``router.claimed`` events
plus active or shutdown-unconfirmed attempts — there is no half-open state or new
circuit breaker; queuing belongs to the execution layers.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from ..store.db import canonical_json, utc_now
from ...errors import BoardError
from ...protocol.schemas import IDENTIFIER_PATTERN

PLANES = ("work", "maintenance", "portrait")
OUTCOMES = ("answered", "no_answer")
#: Attempt states that still own capacity: active execution or unconfirmed stop.
#: Missing PIDs and expired leases never prove a stop, so ``uncertain`` stays here.
ACTIVE_ATTEMPT_STATES = ("starting", "executing", "finalizing", "uncertain")
_VALID_REQUESTED = "CASE WHEN json_valid(r.requested_json) THEN r.requested_json END"
MAX_CODE = 128
MAX_PHASE = 64


def _parse(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _later(left: str | None, right: str | None) -> str | None:
    """The later of two board timestamps; unparseable values never win silently."""
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right) if _parse(left) and _parse(right) else left


def _bounded(value: object, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or any(
            ord(char) < 32 or ord(char) == 127 for char in value):
        raise BoardError("INVALID_ARGUMENT", f"{name} must be a bounded nonempty string")
    return value


def _optional_identifier(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise BoardError("INVALID_ARGUMENT", f"{name} must be a buddyId or null")
    return value


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


def record_outcome(connection, *, buddy_id: str, plane: str, request_id: str, task_id: str | None,
                   attempt_id: str | None, outcome: str, code: str | None, phase: str,
                   facts: dict, now: str) -> dict:
    """Record one idempotent Router outcome fact for any plane; never executes anything.

    The idempotency key is plane/requestId/selectionRetry/routerIndex/phase/taskId/attemptId/outcome
    with ``routerIndex`` and the retry marker taken from ``facts``; its receipt lives in ``router_outcomes`` and
    commits in the caller's transaction together with the event. A replay with the
    same facts returns the original record, different facts raise ``CONFLICT``.
    Only callers that observed an actual outcome record one: cancellations, input or
    settings changes, fencing and stop-unknown are never recorded as answers here.
    """
    if not isinstance(buddy_id, str) or not IDENTIFIER_PATTERN.fullmatch(buddy_id):
        raise BoardError("INVALID_ARGUMENT", "buddy_id must be a buddyId")
    if plane not in PLANES:
        raise BoardError("INVALID_ARGUMENT", "plane must be work, maintenance or portrait")
    if outcome not in OUTCOMES:
        raise BoardError("INVALID_ARGUMENT", "outcome must be answered or no_answer")
    _bounded(request_id, "request_id", MAX_CODE)
    _optional_identifier(task_id, "task_id")
    _optional_identifier(attempt_id, "attempt_id")
    _bounded(phase, "phase", MAX_PHASE)
    if code is not None:
        _bounded(code, "code", MAX_CODE)
    if not isinstance(facts, dict) or isinstance(facts.get("routerIndex"), bool) \
            or not isinstance(facts.get("routerIndex"), int) or facts["routerIndex"] < 0:
        raise BoardError("INVALID_ARGUMENT", "facts must carry a nonnegative routerIndex")
    if type(facts.get("selectionRetry", False)) is not bool:
        raise BoardError("INVALID_ARGUMENT", "selectionRetry must be boolean")
    router_index = facts["routerIndex"]
    selection_retry = facts.get("selectionRetry", False)
    payload = {"plane": plane, "requestId": request_id, "routerIndex": router_index,
               "buddyId": buddy_id, "phase": phase, "taskId": task_id,
               "attemptId": attempt_id, "outcome": outcome, "code": code}
    if selection_retry:
        payload["selectionRetry"] = True
    key = hashlib.sha256(canonical_json(
        [plane, request_id, selection_retry, router_index, phase, task_id, attempt_id, outcome]).encode("utf-8")).hexdigest()
    kind = f"router.{outcome}"
    stored = connection.execute("SELECT plane,request_id,router_index,buddy_id,phase,task_id,attempt_id,"
                                "outcome,code,event_seq FROM router_outcomes WHERE receipt_id=?", (key,)).fetchone()
    if stored is not None:
        expected = (plane, request_id, router_index, buddy_id, phase, task_id, attempt_id, outcome, code)
        if tuple(stored)[:-1] != expected:
            raise BoardError("CONFLICT", "router outcome facts conflict with the recorded receipt",
                             buddyId=buddy_id, requestId=request_id)
        return {"recorded": False, "seq": int(stored[-1]), "event": kind}
    seq = _append_event(connection, kind=kind, task_id=task_id, attempt_id=attempt_id,
                        payload=payload, now=now)
    connection.execute("INSERT INTO router_outcomes(receipt_id,plane,request_id,router_index,buddy_id,phase,"
                       "task_id,attempt_id,outcome,code,event_seq,recorded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (key, plane, request_id, router_index, buddy_id, phase, task_id, attempt_id,
                        outcome, code, seq, now))
    return {"recorded": True, "seq": seq, "event": kind}


def _fact_entries(connection, buddy_id: str) -> list[dict]:
    """Recorded outcome receipts of one buddy, ordered by the event sequence."""
    entries = []
    rows = connection.execute(
        "SELECT event_seq, recorded_at, plane, request_id, router_index, phase, task_id,"
        " attempt_id, outcome, code FROM router_outcomes WHERE buddy_id=? ORDER BY event_seq", (buddy_id,))
    for row in rows:
        entries.append({"source": "fact", "seq": int(row["event_seq"]), "at": row["recorded_at"],
                        "outcome": row["outcome"], "plane": row["plane"],
                        "requestId": row["request_id"], "routerIndex": row["router_index"],
                        "phase": row["phase"], "taskId": row["task_id"],
                        "attemptId": row["attempt_id"], "code": row["code"]})
    return entries


def buddy_timeline(connection, *, buddy_id: str) -> list[dict]:
    """One buddy's outcome timeline from durable Router outcome receipts."""
    if not isinstance(buddy_id, str) or not IDENTIFIER_PATTERN.fullmatch(buddy_id):
        raise BoardError("INVALID_ARGUMENT", "buddy_id must be a buddyId")
    return _fact_entries(connection, buddy_id)


def active_executions(connection, *, buddy_id: str) -> list[dict]:
    """Active or shutdown-unconfirmed executions bound to one buddy, as facts only.

    Bindings come from recorded ``router.claimed`` events and from selection requests
    whose own frozen input named this buddy. Nothing here starts, stops or queues
    anything: the single-retry constraint and capacity queuing belong to the
    execution layers.
    """
    if not isinstance(buddy_id, str) or not IDENTIFIER_PATTERN.fullmatch(buddy_id):
        raise BoardError("INVALID_ARGUMENT", "buddy_id must be a buddyId")
    markers = ",".join("?" for _ in ACTIVE_ATTEMPT_STATES)
    executions: dict[str, dict] = {}
    rows = connection.execute(
        "SELECT e.task_id AS task_id, e.attempt_id AS attempt_id, e.created_at AS at, e.payload_json AS payload_json"
        " FROM events e WHERE e.kind='router.claimed'"
        " AND json_extract(CASE WHEN json_valid(e.payload_json) THEN e.payload_json END, '$.buddyId')=?"
        " ORDER BY e.seq", (buddy_id,))
    for row in rows:
        executions[row["attempt_id"]] = {"taskId": row["task_id"], "attemptId": row["attempt_id"],
                                         "startedAt": row["at"], "source": "claimed"}
    rows = connection.execute(
        "SELECT r.task_id AS task_id, r.attempt_id AS attempt_id, r.generation AS generation,"
        " r.request_id AS request_id, a.execution_state AS execution_state,"
        " COALESCE(a.started_at, a.created_at) AS started_at"
        " FROM decision_requests r JOIN attempts a ON a.task_id=r.task_id AND a.attempt_id=r.attempt_id"
        " AND a.generation=r.generation"
        f" WHERE r.kind='select' AND json_extract({_VALID_REQUESTED}, '$.routerBuddyId')=?"
        f" AND a.execution_state IN ({markers})", (buddy_id, *ACTIVE_ATTEMPT_STATES))
    for row in rows:
        executions[row["attempt_id"]] = {"taskId": row["task_id"], "attemptId": row["attempt_id"],
                                         "generation": row["generation"], "requestId": row["request_id"],
                                         "executionState": row["execution_state"],
                                         "startedAt": row["started_at"], "source": "request"}
    active = connection.execute(
        f"SELECT attempt_id FROM attempts WHERE execution_state IN ({markers})",
        ACTIVE_ATTEMPT_STATES).fetchall()
    live = {row["attempt_id"] for row in active}
    return [execution for attempt_id, execution in executions.items() if attempt_id in live]


def summarize(timeline: list[dict], *, interval_seconds: int, now: str | None = None) -> dict:
    """Pure projection of one buddy's timeline: streaks, skip window and retry facts.

    The consecutive count covers every recorded no-answer since the last valid
    answered, independently of any display window, and each buddy's counts stay
    separate. ``skipUntil`` is the last no-answer time plus the current interval; a
    later valid answered removes it. Reads never write, so repeated summaries never
    extend a window, and an expired window simply ends.
    """
    if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, int) or interval_seconds < 1:
        raise BoardError("INVALID_ARGUMENT", "interval_seconds must be a positive integer of seconds")
    answered_count = no_answer_count = consecutive = 0
    last_answered = last_no_answer = None
    for entry in timeline:
        outcome = entry.get("outcome")
        if outcome == "answered":
            answered_count += 1
            consecutive = 0
            last_answered = _later(last_answered, entry.get("at"))
        elif outcome == "no_answer":
            no_answer_count += 1
            consecutive += 1
            last_no_answer = _later(last_no_answer, entry.get("at"))
    skip_until = None
    if consecutive > 0 and last_no_answer is not None:
        moment = _parse(last_no_answer)
        if moment is not None:
            skip_until = (moment + timedelta(seconds=interval_seconds)).astimezone(timezone.utc)\
                .isoformat(timespec="milliseconds").replace("+00:00", "Z")
    present = _parse(now) if now is not None else _parse(utc_now())
    boundary = _parse(skip_until)
    in_window = present is not None and boundary is not None and present < boundary
    return {"answeredCount": answered_count, "noAnswerCount": no_answer_count,
            "lastAnsweredAt": last_answered, "lastNoAnswerAt": last_no_answer,
            "consecutiveNoAnswers": consecutive, "skipUntil": skip_until,
            "retryAt": skip_until, "inSkipWindow": in_window}


def router_state(connection, *, buddy_id: str, interval_seconds: int, now: str | None = None) -> dict:
    """The read-only retry facts of one buddy for the resolution entry.

    ``retryInProgress`` is true only while the buddy still owes an answer and one of
    its executions is active or shutdown-unconfirmed; after a valid answered, the
    same active execution is ordinary capacity, not a retry.
    """
    timeline = buddy_timeline(connection, buddy_id=buddy_id)
    summary = summarize(timeline, interval_seconds=interval_seconds, now=now)
    attempts = active_executions(connection, buddy_id=buddy_id)
    return {**summary, "buddyId": buddy_id,
            "retryInProgress": bool(attempts) and summary["consecutiveNoAnswers"] > 0,
            "activeAttempts": attempts}


__all__ = ["ACTIVE_ATTEMPT_STATES", "OUTCOMES", "PLANES", "active_executions",
           "buddy_timeline", "record_outcome", "router_state", "summarize"]
