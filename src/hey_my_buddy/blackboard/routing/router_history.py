"""Router outcome facts: idempotent recording and read-only per-buddy projections.

One entry point records ``router.answered``/``router.no_answer`` facts for every
Router plane (work, maintenance, portrait); reads never append events. Facts and
pre-list routing records project into one per-buddy timeline: old records keep the
Router their own frozen input named and the effective answer or no-answer their
immutable terminal event and attempt receipt prove, never today's first list item,
and never re-audited by today's evidence rules. Records whose nature or attribution
cannot be proven stay ``unknown`` instead of being guessed, and old and new records
deduplicate by their request/attempt identity.

Skip windows come from the last recorded no-answer plus the current retry interval;
a later valid answered (including a valid abstention) removes it. Reads project
windows without writing and never extend them, and an expired window can retry
again. The single in-flight retry is derived from existing ``router.claimed`` events
plus active or shutdown-unconfirmed attempts — there is no half-open state or new
circuit breaker; queuing belongs to the execution layers.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from ..store.db import canonical_json, utc_now
from ...errors import BoardError
from ...protocol.schemas import IDENTIFIER_PATTERN

PLANES = ("work", "maintenance", "portrait")
OUTCOMES = ("answered", "no_answer")
#: Attempt states that still own capacity: active execution or unconfirmed stop.
#: Missing PIDs and expired leases never prove a stop, so ``uncertain`` stays here.
ACTIVE_ATTEMPT_STATES = ("starting", "executing", "finalizing", "uncertain")
#: The receipt key prefix in ``meta``; every recorded outcome stores one receipt in
#: the same transaction as its event, so a replay either matches it or conflicts.
RECEIPT_PREFIX = "router-outcome:"
#: Terminal decision kinds whose immutable records can prove an effective outcome.
_RECORD_KINDS = ("decision.completed", "decision.failed", "decision.needs_host",
                 "decision.cancelled", "decision.stale")
_RECORD_KIND_LITERALS = "(" + ",".join(f"'{kind}'" for kind in _RECORD_KINDS) + ")"
#: json_valid guards every json_extract over an untrusted stored column: a malformed
#: payload must fail neither the write path's expression index nor a projection read
#: and simply never matches an attribution. Keep the profile expression aligned with
#: the events_router_outcome_idx definition so the index stays usable.
_PROFILE_EXPRESSION = ("json_extract(CASE WHEN json_valid(payload_json) THEN payload_json END,"
                       " '$.profileId')")
_VALID_REQUESTED = "CASE WHEN json_valid(r.requested_json) THEN r.requested_json END"
_VALID_EVENT_PAYLOAD = "CASE WHEN json_valid(e.payload_json) THEN e.payload_json END"
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
        raise BoardError("INVALID_ARGUMENT", f"{name} must be a profileId or null")
    return value


def _append_event(connection, *, kind: str, task_id: str | None, attempt_id: str | None,
                  payload: dict, now: str) -> int:
    """One event inside the caller's transaction, matching the board's append shape."""
    from ..tasks.objectives import record_activity
    cursor = connection.execute(
        "INSERT INTO events(task_id, attempt_id, revision, kind, payload_json, created_at) VALUES(?,?,?,?,?,?)",
        (task_id, attempt_id, None, kind, canonical_json(payload), now),
    )
    seq = int(cursor.lastrowid)
    record_activity(connection, task_id, seq, now)
    return seq


def record_outcome(connection, *, profile_id: str, plane: str, request_id: str, task_id: str | None,
                   attempt_id: str | None, outcome: str, code: str | None, phase: str,
                   facts: dict, now: str) -> dict:
    """Record one idempotent Router outcome fact for any plane; never executes anything.

    The idempotency key is plane/requestId/routerIndex/phase/taskId/attemptId/outcome
    with ``routerIndex`` taken from ``facts``; its receipt lives in ``meta`` and
    commits in the caller's transaction together with the event. A replay with the
    same facts returns the original record, different facts raise ``CONFLICT``.
    Only callers that observed an actual outcome record one: cancellations, input or
    settings changes, fencing and stop-unknown are never recorded as answers here.
    """
    if not isinstance(profile_id, str) or not IDENTIFIER_PATTERN.fullmatch(profile_id):
        raise BoardError("INVALID_ARGUMENT", "profile_id must be a profileId")
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
    router_index = facts["routerIndex"]
    payload = {"plane": plane, "requestId": request_id, "routerIndex": router_index,
               "profileId": profile_id, "phase": phase, "taskId": task_id,
               "attemptId": attempt_id, "outcome": outcome, "code": code}
    key = RECEIPT_PREFIX + hashlib.sha256(canonical_json(
        [plane, request_id, router_index, phase, task_id, attempt_id, outcome]).encode("utf-8")).hexdigest()
    kind = f"router.{outcome}"
    stored = connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if stored is not None:
        try:
            receipt = json.loads(stored[0])
        except ValueError:
            receipt = None
        if receipt is None or receipt.get("payload") != payload:
            raise BoardError("CONFLICT", "router outcome facts conflict with the recorded receipt",
                             profileId=profile_id, requestId=request_id)
        return {"recorded": False, "seq": int(receipt["seq"]), "event": kind}
    seq = _append_event(connection, kind=kind, task_id=task_id, attempt_id=attempt_id,
                        payload=payload, now=now)
    connection.execute("INSERT INTO meta(key,value) VALUES(?,?)",
                       (key, canonical_json({"payload": payload, "seq": seq, "recordedAt": now})))
    return {"recorded": True, "seq": seq, "event": kind}


def _fact_entries(connection, profile_id: str) -> list[dict]:
    """Recorded fact events of one buddy, ordered by the event sequence."""
    entries = []
    rows = connection.execute(
        "SELECT seq, kind, created_at, payload_json FROM events"
        " WHERE kind IN ('router.answered','router.no_answer')"
        f" AND {_PROFILE_EXPRESSION}=? ORDER BY seq", (profile_id,))
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            # A malformed recorded fact stays isolated: it cannot affect this or
            # any other buddy's projection.
            continue
        entries.append({"source": "fact", "seq": int(row["seq"]), "at": row["created_at"],
                        "outcome": "answered" if row["kind"] == "router.answered" else "no_answer",
                        "plane": payload.get("plane"), "requestId": payload.get("requestId"),
                        "routerIndex": payload.get("routerIndex"), "phase": payload.get("phase"),
                        "taskId": payload.get("taskId"), "attemptId": payload.get("attemptId"),
                        "code": payload.get("code")})
    return entries


def _valid_answer(receipt: dict | None, output: dict | None) -> bool:
    """Whether immutable stop evidence proves an adopted Router answer.

    Old accepted answers stay answers: today's tool-evidence rules never re-audit
    them. Unknown elapsed or model-start facts are taken as recorded, never read as
    a model rejection.
    """
    if receipt:
        result = receipt.get("result")
        return (receipt.get("status") == "ok" and isinstance(result, dict) and result.get("status") == "ok"
                and result.get("modelStarted") is not False and receipt.get("shutdownConfirmed") == 1)
    return isinstance(output, dict) and output.get("status") == "ok"


def _abstained(kind: str, output: dict) -> bool:
    decision = output.get("decision") if isinstance(output, dict) else None
    return (kind == "decision.needs_host" and isinstance(output, dict) and output.get("status") == "ok"
            and isinstance(decision, dict) and decision.get("profileId", False) is None
            and set(decision) == {"profileId", "reason", "evidence"})


def _classify_record(kind: str, output: dict, receipt: dict | None, event_payload: dict | None = None) -> tuple[str, str | None]:
    """One old record's effective outcome: answered, no_answer, changed, neutral or unknown."""
    if isinstance(receipt, dict) and isinstance(receipt.get('result'), dict):
        output = receipt['result']
    payload_code = (event_payload or {}).get("errorCode")
    code = payload_code or (output.get("code") if isinstance(output, dict) else None)
    code = code if isinstance(code, str) and code else None
    if kind == "decision.completed":
        return ("answered", None) if _valid_answer(receipt, output) else ("unknown", code)
    if kind == "decision.needs_host":
        if code == "router-input-changed":
            return "changed", code
        if code:
            return "no_answer", code
        if _abstained(kind, output):
            return "answered", None
        return "no_answer", code
    if kind == "decision.failed":
        if code == "router-input-changed":
            return "changed", code
        return "no_answer", code
    if kind in ("decision.cancelled", "decision.stale"):
        return "neutral", code
    return "unknown", code


def _record_rows(connection, profile_id: str | None) -> list[dict]:
    """Terminal select records attributed (or not attributable) through their own frozen input."""
    if profile_id is None:
        attribution = (f"json_extract({_VALID_REQUESTED}, '$.routerProfileId') IS NULL"
                       " AND (json_extract(" + _VALID_REQUESTED + ",'$.routerCalled') IS NULL"
                       " OR json_extract(" + _VALID_REQUESTED + ",'$.routerCalled')!=0)"
                       " AND (json_extract(" + _VALID_REQUESTED + ",'$.routingBasis.candidateCount') IS NULL"
                       " OR json_extract(" + _VALID_REQUESTED + ",'$.routingBasis.candidateCount')!=0)")
        values: tuple = ()
    else:
        attribution = f"json_extract({_VALID_REQUESTED}, '$.routerProfileId')=?"
        values = (profile_id,)
    rows = connection.execute(
        "SELECT e.seq AS event_seq, e.kind AS kind, e.created_at AS at, e.payload_json AS event_payload,"
        " r.decision_id AS decision_id, r.request_id AS request_id, r.task_id AS task_id,"
        " r.attempt_id AS attempt_id, r.generation AS generation, r.requested_json AS requested_json,"
        " r.output_json AS output_json, a.result_json AS receipt_json"
        " FROM events e JOIN decision_requests r"
        f" ON r.decision_id=json_extract({_VALID_EVENT_PAYLOAD}, '$.decisionId')"
        " LEFT JOIN attempts a ON a.attempt_id=r.attempt_id AND a.task_id=r.task_id AND a.generation=r.generation"
        f" WHERE e.kind IN {_RECORD_KIND_LITERALS} AND r.kind='select' AND {attribution}"
        " ORDER BY e.seq", values)
    entries = []
    for row in rows:
        try:
            requested = json.loads(row["requested_json"]) if row["requested_json"] else {}
            output = json.loads(row["output_json"]) if row["output_json"] else {}
        except ValueError:
            requested, output = {}, {}
        # The request output may have been replaced by a late, fenced result; the
        # immutable receipt wins where one exists.
        try:
            receipt = json.loads(row["receipt_json"]) if row["receipt_json"] else None
        except ValueError:
            receipt = None
        try:
            event_payload = json.loads(row["event_payload"]) if row["event_payload"] else {}
        except ValueError:
            event_payload = {}
        entries.append({"row": row, "requested": requested if isinstance(requested, dict) else {},
                        "output": output if isinstance(output, dict) else {},
                        "receipt": receipt if isinstance(receipt, dict) else None,
                        "eventPayload": event_payload if isinstance(event_payload, dict) else {}})
    return entries


def profile_timeline(connection, *, profile_id: str) -> list[dict]:
    """One buddy's merged outcome timeline: recorded facts plus pre-list records.

    Old records are deduplicated against facts by request identity, so a request the
    new facts already recorded is never counted twice. Each entry names its source;
    entries with an unprovable nature are carried as ``unknown`` instead of being
    counted as an answer, a no-answer or a model rejection.
    """
    if not isinstance(profile_id, str) or not IDENTIFIER_PATTERN.fullmatch(profile_id):
        raise BoardError("INVALID_ARGUMENT", "profile_id must be a profileId")
    facts = _fact_entries(connection, profile_id)
    seen_requests = {entry["requestId"] for entry in facts if entry["requestId"] is not None}
    seen_attempts = {entry["attemptId"] for entry in facts if entry["attemptId"] is not None}
    timeline = list(facts)
    for entry in _record_rows(connection, profile_id):
        row = entry["row"]
        if row["request_id"] in seen_requests or (row["attempt_id"] and row["attempt_id"] in seen_attempts):
            continue
        outcome, code = _classify_record(row["kind"], entry["output"], entry["receipt"], entry['eventPayload'])
        if outcome == "changed" or outcome == "neutral":
            continue
        phase = "preflight" if not row["attempt_id"] else "runtime"
        timeline.append({"source": "record", "seq": int(row["event_seq"]), "at": row["at"],
                         "outcome": outcome, "decisionId": row["decision_id"],
                         "requestId": row["request_id"], "taskId": row["task_id"],
                         "attemptId": row["attempt_id"], "phase": phase, "code": code,
                         "kind": row["kind"]})
    timeline.sort(key=lambda entry: entry["seq"])
    return timeline


def unattributed_records(connection) -> list[dict]:
    """Terminal select records whose Router attribution or nature is not provable.

    These are reported separately and never guessed onto the current list's first
    item or any other buddy.
    """
    entries = []
    for entry in _record_rows(connection, None):
        row = entry["row"]
        outcome, code = _classify_record(row["kind"], entry["output"], entry["receipt"], entry['eventPayload'])
        entries.append({"source": "record", "seq": int(row["event_seq"]), "at": row["at"],
                        "outcome": outcome, "decisionId": row["decision_id"],
                        "requestId": row["request_id"], "taskId": row["task_id"],
                        "attemptId": row["attempt_id"], "code": code, "kind": row["kind"]})
    return entries


def active_executions(connection, *, profile_id: str) -> list[dict]:
    """Active or shutdown-unconfirmed executions bound to one buddy, as facts only.

    Bindings come from recorded ``router.claimed`` events and from selection requests
    whose own frozen input named this buddy. Nothing here starts, stops or queues
    anything: the single-retry constraint and capacity queuing belong to the
    execution layers.
    """
    if not isinstance(profile_id, str) or not IDENTIFIER_PATTERN.fullmatch(profile_id):
        raise BoardError("INVALID_ARGUMENT", "profile_id must be a profileId")
    markers = ",".join("?" for _ in ACTIVE_ATTEMPT_STATES)
    executions: dict[str, dict] = {}
    rows = connection.execute(
        "SELECT e.task_id AS task_id, e.attempt_id AS attempt_id, e.created_at AS at, e.payload_json AS payload_json"
        " FROM events e WHERE e.kind='router.claimed'"
        " AND json_extract(CASE WHEN json_valid(e.payload_json) THEN e.payload_json END, '$.profileId')=?"
        " ORDER BY e.seq", (profile_id,))
    for row in rows:
        executions[row["attempt_id"]] = {"taskId": row["task_id"], "attemptId": row["attempt_id"],
                                         "startedAt": row["at"], "source": "claimed"}
    rows = connection.execute(
        "SELECT r.task_id AS task_id, r.attempt_id AS attempt_id, r.generation AS generation,"
        " r.request_id AS request_id, a.execution_state AS execution_state,"
        " COALESCE(a.started_at, a.created_at) AS started_at"
        " FROM decision_requests r JOIN attempts a ON a.task_id=r.task_id AND a.attempt_id=r.attempt_id"
        " AND a.generation=r.generation"
        f" WHERE r.kind='select' AND json_extract({_VALID_REQUESTED}, '$.routerProfileId')=?"
        f" AND a.execution_state IN ({markers})", (profile_id, *ACTIVE_ATTEMPT_STATES))
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


def router_state(connection, *, profile_id: str, interval_seconds: int, now: str | None = None) -> dict:
    """The read-only retry facts of one buddy for the resolution entry.

    ``retryInProgress`` is true only while the buddy still owes an answer and one of
    its executions is active or shutdown-unconfirmed; after a valid answered, the
    same active execution is ordinary capacity, not a retry.
    """
    timeline = profile_timeline(connection, profile_id=profile_id)
    summary = summarize(timeline, interval_seconds=interval_seconds, now=now)
    attempts = active_executions(connection, profile_id=profile_id)
    return {**summary, "profileId": profile_id,
            "retryInProgress": bool(attempts) and summary["consecutiveNoAnswers"] > 0,
            "activeAttempts": attempts}


__all__ = ["ACTIVE_ATTEMPT_STATES", "OUTCOMES", "PLANES", "RECEIPT_PREFIX", "active_executions",
           "profile_timeline", "record_outcome", "router_state", "summarize", "unattributed_records"]
