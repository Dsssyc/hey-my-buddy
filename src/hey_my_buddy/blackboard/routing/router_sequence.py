"""Immutable Router request, dispatch and claim facts in their owned relational tables.

One request freezes its complete snapshot body, including ordered Router facts,
the actor-free base input, admission inspections and any Host packet; each dispatched item owns one
immutable document under its internal task; one claim binds an attempt to that
dispatch and appends the ``router.claimed`` fact in the same transaction. An
existing identity replays only identical content and conflicts on any difference;
nothing here starts a process, and no recorded document, hash or receipt is ever
rewritten.
"""
from __future__ import annotations

import json

from ..store.db import canonical_json, sha256_text
from ...errors import BoardError
from ...protocol.schemas import IDENTIFIER_PATTERN

CLAIM_EVENT = "router.claimed"
_IDENTITY_FIELDS = ("harness", "provider", "model", "effort")


def _bounded_id(value: object, name: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise BoardError("INVALID_ARGUMENT", f"{name} must be a bounded identifier")
    return value


def _store(connection, table: str, key: str, values: dict, *, clock_field: str | None = None) -> tuple[dict, bool]:
    """Insert one immutable row; identical semantic fields replay its first clock."""
    columns = list(values)
    stored = connection.execute(f"SELECT {','.join(columns)} FROM {table} WHERE {key}=?",
                                (values[key],)).fetchone()
    if stored is not None:
        previous = dict(zip(columns, stored))
        if any(value != previous[column] for column, value in values.items() if column != clock_field):
            raise BoardError("CONFLICT", f"{table} record is immutable and the facts differ", identity=values[key])
        return previous, False
    connection.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                       tuple(values.values()))
    return values, True


def _request(row: dict) -> dict:
    return {**json.loads(row["snapshot_json"]), "frozenAt": row["frozen_at"]}


def _dispatch(row: dict) -> dict:
    return {"decisionId": row["decision_id"], "taskId": row["task_id"], "routerIndex": row["router_index"],
            "buddyId": row["buddy_id"], "buddy": {key: row[key] for key in _IDENTITY_FIELDS},
            "document": json.loads(row["document_json"]), "inputSha256": row["input_sha256"],
            "createdAt": row["created_at"]}


def freeze_request(connection, decision_id: str, *, snapshot: dict, now: str) -> dict:
    """Freeze one request's Router facts, shared base input and admission inspections.

    The stored document is the request's own evidence: the settings snapshot the
    request lives in, the complete common candidate packet every dispatch copies
    (never just an unavailability reason), and the inspections admission observed.
    Every admitted snapshot field is retained and participates in replay equality.
    Only the first freeze wins; replays of the same snapshot are idempotent.
    """
    decision_id = _bounded_id(decision_id, "decision_id")
    if not isinstance(snapshot, dict) or not {"facts", "baseInput", "inspections"} <= set(snapshot):
        raise BoardError("INVALID_ARGUMENT", "snapshot must carry facts, baseInput and inspections")
    if not isinstance(snapshot["baseInput"], dict):
        raise BoardError("INVALID_ARGUMENT", "snapshot baseInput must be an object")
    if not isinstance(snapshot["inspections"], list):
        raise BoardError("INVALID_ARGUMENT", "snapshot inspections must be a list")
    body = {key: value for key, value in snapshot.items() if key != "frozenAt"}
    row, _created = _store(connection, "router_requests", "decision_id", {
        "decision_id": decision_id, "snapshot_json": canonical_json(body), "frozen_at": now,
    }, clock_field="frozen_at")
    return _request(row)


def request_snapshot(connection, decision_id: str) -> dict | None:
    """The frozen request snapshot of one decision, as a fresh copy, or None."""
    row = connection.execute("SELECT * FROM router_requests WHERE decision_id=?", (decision_id,)).fetchone()
    return _request(dict(row)) if row is not None else None


def dispatch(connection, task_id: str) -> dict | None:
    """The immutable dispatch document of one internal Router task, or None."""
    row = connection.execute("SELECT * FROM router_dispatches WHERE task_id=?", (task_id,)).fetchone()
    return _dispatch(dict(row)) if row is not None else None


def dispatches(connection, decision_id: str) -> tuple[dict, ...]:
    """Fresh dispatch copies in indexed Router order, each retaining its own input."""
    rows = connection.execute("SELECT * FROM router_dispatches WHERE decision_id=? ORDER BY router_index,task_id",
                              (decision_id,))
    return tuple(_dispatch(dict(row)) for row in rows)


def reserve_dispatch(connection, *, decision_id: str, task_id: str, router_index: int,
                     buddy: dict, document: dict, now: str) -> dict:
    """Record one dispatch under its internal task; the caller owns the transaction.

    ``buddy`` is the complete buddy identity — ``buddyId`` plus the four-part
    tuple — exactly as resolution selected it. ``document`` is the exact input that
    item will consume: the frozen base input plus this actor. Recording never
    starts a process and never rewrites another dispatch's document or hash.
    """
    decision_id = _bounded_id(decision_id, "decision_id")
    task_id = _bounded_id(task_id, "task_id")
    if isinstance(router_index, bool) or not isinstance(router_index, int) or router_index < 0:
        raise BoardError("INVALID_ARGUMENT", "router_index must be a nonnegative integer")
    if not isinstance(buddy, dict) or not isinstance(buddy.get("buddyId"), str):
        raise BoardError("INVALID_ARGUMENT", "buddy must carry the buddy's buddyId")
    if any(not isinstance(buddy.get(key), str) or not buddy[key] for key in _IDENTITY_FIELDS):
        raise BoardError("INVALID_ARGUMENT", "buddy must be the complete four-part identity")
    if not isinstance(document, dict):
        raise BoardError("INVALID_ARGUMENT", "document must be the dispatch input object")
    encoded = canonical_json(document)
    row, _created = _store(connection, "router_dispatches", "task_id", {
        "task_id": task_id, "decision_id": decision_id, "router_index": router_index,
        "buddy_id": buddy["buddyId"], **{key: buddy[key] for key in _IDENTITY_FIELDS},
        "document_json": encoded, "input_sha256": sha256_text(encoded), "created_at": now,
    }, clock_field="created_at")
    return _dispatch(row)


def claim(connection, attempt_id: str) -> dict | None:
    """The attempt's relational binding, with its immutable dispatch identity."""
    row = connection.execute("SELECT c.*,d.decision_id,d.router_index,d.buddy_id FROM router_claims c"
                             " JOIN router_dispatches d ON d.task_id=c.task_id WHERE c.attempt_id=?",
                             (attempt_id,)).fetchone()
    if row is None:
        return None
    return {"attemptId": row["attempt_id"], "taskId": row["task_id"], "generation": row["generation"],
            "decisionId": row["decision_id"], "routerIndex": row["router_index"], "buddyId": row["buddy_id"]}


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
    dispatch_document = dispatch(connection, task_id)
    if dispatch_document is None:
        raise BoardError("INVALID_ARGUMENT", "an attempt may only claim a frozen dispatch", taskId=task_id)
    request = connection.execute("SELECT request_id FROM decision_requests WHERE decision_id=?",
                                 (dispatch_document["decisionId"],)).fetchone()
    _, created = _store(connection, "router_claims", "attempt_id", {
        "attempt_id": attempt_id, "task_id": task_id, "generation": generation,
    })
    if created:
        from .router_history import _append_event
        _append_event(
            connection,
            kind=CLAIM_EVENT,
            task_id=task_id,
            attempt_id=attempt_id,
            payload={
                "buddyId": dispatch_document["buddyId"],
                "plane": "routing",
                "requestId": request["request_id"],
                "routerIndex": dispatch_document["routerIndex"],
                "taskId": task_id,
                "attemptId": attempt_id,
                "generation": generation,
            },
            now=now,
        )
    return claim(connection, attempt_id)


__all__ = [
    "CLAIM_EVENT",
    "claim",
    "dispatch",
    "dispatches",
    "freeze_request",
    "record_claim",
    "request_snapshot",
    "reserve_dispatch",
]
