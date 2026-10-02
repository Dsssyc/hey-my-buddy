"""Immutable Router request, dispatch and claim facts over the board's meta store.

One request freezes its ordered Router facts, the actor-free base input every
dispatch copies, and the admission inspections; each dispatched item owns one
immutable document under its internal task; one claim binds an attempt to that
dispatch and appends the ``router.claimed`` fact in the same transaction. An
existing key replays only identical content and conflicts on any difference;
nothing here starts a process, and no recorded document, hash or receipt is ever
rewritten.
"""
from __future__ import annotations

import json

from .db import canonical_json, sha256_text
from .errors import BoardError
from .schemas import IDENTIFIER_PATTERN

REQUEST_KEY_PREFIX = "router-request:"
DISPATCH_KEY_PREFIX = "router-dispatch:"
ATTEMPT_KEY_PREFIX = "attempt-router:"
CLAIM_EVENT = "router.claimed"
_IDENTITY_FIELDS = ("adapter", "provider", "model", "effort")


def _bounded_id(value: object, name: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise BoardError("INVALID_ARGUMENT", f"{name} must be a bounded identifier")
    return value


def _store(connection, key: str, payload: dict, *, clock_field: str | None = None) -> tuple[dict, bool]:
    """Insert one immutable meta document; identical content replays, any difference conflicts."""
    encoded = canonical_json(payload)
    stored = connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if stored is not None:
        if clock_field:
            previous = json.loads(stored[0])
            # A retry's wall clock is not a new request or dispatch fact. Keep
            # the first timestamp while comparing every semantic field.
            encoded = canonical_json({**payload, clock_field: previous[clock_field]})
        if stored[0] != encoded:
            raise BoardError("CONFLICT", f"{key} is immutable and the recorded facts differ", key=key)
        return json.loads(stored[0]), False
    connection.execute("INSERT INTO meta(key,value) VALUES(?,?)", (key, encoded))
    return json.loads(encoded), True


def freeze_request(connection, decision_id: str, *, snapshot: dict, now: str) -> dict:
    """Freeze one request's Router facts, shared base input and admission inspections.

    The stored document is the request's own evidence: the settings snapshot the
    request lives in, the complete common candidate packet every dispatch copies
    (never just an unavailability reason), and the inspections admission observed.
    Only the first freeze wins; replays of the same snapshot are idempotent.
    """
    decision_id = _bounded_id(decision_id, "decision_id")
    if not isinstance(snapshot, dict) or not {"facts", "baseInput", "inspections"} <= set(snapshot):
        raise BoardError("INVALID_ARGUMENT", "snapshot must carry facts, baseInput and inspections")
    if not isinstance(snapshot["baseInput"], dict):
        raise BoardError("INVALID_ARGUMENT", "snapshot baseInput must be an object")
    if not isinstance(snapshot["inspections"], list):
        raise BoardError("INVALID_ARGUMENT", "snapshot inspections must be a list")
    document, _created = _store(
        connection,
        REQUEST_KEY_PREFIX + decision_id,
        {"frozenAt": now, "facts": snapshot["facts"], "baseInput": snapshot["baseInput"],
         "inspections": snapshot["inspections"]}, clock_field="frozenAt",
    )
    return document


def request_snapshot(connection, decision_id: str) -> dict | None:
    """The frozen request snapshot of one decision, as a fresh copy, or ``None``."""
    row = connection.execute("SELECT value FROM meta WHERE key=?", (REQUEST_KEY_PREFIX + decision_id,)).fetchone()
    return json.loads(row[0]) if row is not None else None


def dispatch(connection, task_id: str) -> dict | None:
    """The immutable dispatch document of one internal Router task, or ``None``."""
    row = connection.execute("SELECT value FROM meta WHERE key=?", (DISPATCH_KEY_PREFIX + task_id,)).fetchone()
    return json.loads(row[0]) if row is not None else None


def reserve_dispatch(connection, *, decision_id: str, task_id: str, router_index: int,
                     profile: dict, document: dict, now: str) -> dict:
    """Record one dispatch under its internal task; the caller owns the transaction.

    ``profile`` is the complete buddy identity — ``profileId`` plus the four-part
    tuple — exactly as resolution selected it. ``document`` is the exact input that
    item will consume: the frozen base input plus this actor. Recording never
    starts a process and never rewrites another dispatch's document or hash.
    """
    decision_id = _bounded_id(decision_id, "decision_id")
    task_id = _bounded_id(task_id, "task_id")
    if isinstance(router_index, bool) or not isinstance(router_index, int) or router_index < 0:
        raise BoardError("INVALID_ARGUMENT", "router_index must be a nonnegative integer")
    if not isinstance(profile, dict) or not isinstance(profile.get("profileId"), str):
        raise BoardError("INVALID_ARGUMENT", "profile must carry the buddy's profileId")
    if any(not isinstance(profile.get(key), str) or not profile[key] for key in _IDENTITY_FIELDS):
        raise BoardError("INVALID_ARGUMENT", "profile must be the complete four-part identity")
    if not isinstance(document, dict):
        raise BoardError("INVALID_ARGUMENT", "document must be the dispatch input object")
    encoded = canonical_json(document)
    stored, _created = _store(
        connection,
        DISPATCH_KEY_PREFIX + task_id,
        {
            "decisionId": decision_id,
            "taskId": task_id,
            "routerIndex": router_index,
            "profileId": profile["profileId"],
            "profile": {key: profile[key] for key in _IDENTITY_FIELDS},
            "document": document,
            "inputSha256": sha256_text(encoded),
            "createdAt": now,
        }, clock_field="createdAt",
    )
    return stored


def record_claim(connection, *, task_id: str, attempt_id: str, generation: int, now: str) -> dict:
    """Bind one attempt to its dispatch and append the atomic ``router.claimed`` fact.

    The binding is immutable per attempt: an identical replay returns the recorded
    binding without a second event, and a different binding for the same attempt
    conflicts. The event commits in the caller's transaction, so a claim and its
    Router fact can never be observed apart.
    """
    task_id = _bounded_id(task_id, "task_id")
    attempt_id = _bounded_id(attempt_id, "attempt_id")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise BoardError("INVALID_ARGUMENT", "generation must be a positive integer")
    dispatch_row = connection.execute(
        "SELECT value FROM meta WHERE key=?", (DISPATCH_KEY_PREFIX + task_id,)
    ).fetchone()
    if dispatch_row is None:
        raise BoardError("INVALID_ARGUMENT", "an attempt may only claim a frozen dispatch", taskId=task_id)
    dispatch_document = json.loads(dispatch_row[0])
    request = connection.execute(
        "SELECT request_id FROM decision_requests WHERE decision_id=?", (dispatch_document["decisionId"],)
    ).fetchone()
    if request is None:
        raise BoardError("INVALID_ARGUMENT", "the dispatch has no decision request",
                         decisionId=dispatch_document["decisionId"])
    binding = {
        "taskId": task_id,
        "attemptId": attempt_id,
        "generation": generation,
        "decisionId": dispatch_document["decisionId"],
        "routerIndex": dispatch_document["routerIndex"],
        "profileId": dispatch_document["profileId"],
    }
    stored, created = _store(connection, ATTEMPT_KEY_PREFIX + attempt_id, binding)
    if created:
        from .router_history import _append_event
        _append_event(
            connection,
            kind=CLAIM_EVENT,
            task_id=task_id,
            attempt_id=attempt_id,
            payload={
                "profileId": dispatch_document["profileId"],
                "plane": "work",
                "requestId": request["request_id"],
                "routerIndex": dispatch_document["routerIndex"],
                "taskId": task_id,
                "attemptId": attempt_id,
                "generation": generation,
            },
            now=now,
        )
    return stored


__all__ = [
    "ATTEMPT_KEY_PREFIX",
    "CLAIM_EVENT",
    "DISPATCH_KEY_PREFIX",
    "REQUEST_KEY_PREFIX",
    "dispatch",
    "freeze_request",
    "record_claim",
    "request_snapshot",
    "reserve_dispatch",
]
