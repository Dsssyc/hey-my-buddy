"""Immutable Router round snapshots, dispatch identities and claim facts."""
from __future__ import annotations

import json

from ..store.db import canonical_json, sha256_text

CLAIM_EVENT = "router.claimed"
_IDENTITY_FIELDS = ("harness", "provider", "model", "effort")


def _store(connection, table: str, values: dict) -> dict:
    """Insert a new immutable row under the caller's transaction."""
    columns = list(values)
    connection.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                       tuple(values.values()))
    return values


def _request(row: dict) -> dict:
    return {**json.loads(row["snapshot_json"]), "frozenAt": row["frozen_at"]}


def _dispatch(connection, row: dict) -> dict:
    snapshot = request_snapshot(connection, row["decision_id"], row["round"])
    buddy = {key: row[key] for key in _IDENTITY_FIELDS}
    document = {**snapshot["baseInput"], "buddy": buddy,
                "routerBuddyId": row["buddy_id"], "routerBuddy": buddy,
                "routerIndex": row["router_index"]}
    return {"decisionId": row["decision_id"], "taskId": row["task_id"], "routerIndex": row["router_index"],
            "round": row["round"], "buddyId": row["buddy_id"], "buddy": buddy,
            "document": document, "inputSha256": sha256_text(canonical_json(document)),
            "createdAt": row["created_at"]}


def freeze_request(connection, decision_id: str, *, snapshot: dict, now: str, round: int = 1) -> dict:
    """Freeze one request's Router facts, shared base input and admission inspections.

    The stored document is the request's own evidence: the settings snapshot the
    request lives in, the complete common candidate packet every dispatch copies
    (never just an unavailability reason), and the inspections admission observed.
    Every admitted snapshot field is retained.
    """
    row = _store(connection, "router_requests", {
        "decision_id": decision_id, "round": round,
        "snapshot_json": canonical_json(snapshot), "frozen_at": now,
    })
    return _request(row)


def request_snapshot(connection, decision_id: str, round: int | None = None) -> dict:
    """A required frozen round snapshot; omitted round selects the latest."""
    if round is None:
        row = connection.execute("SELECT * FROM router_requests WHERE decision_id=? ORDER BY round DESC LIMIT 1",
                                 (decision_id,)).fetchone()
    else:
        row = connection.execute("SELECT * FROM router_requests WHERE decision_id=? AND round=?",
                                 (decision_id, round)).fetchone()
    if row is None:
        raise KeyError((decision_id, round))
    return _request(dict(row))


def dispatch(connection, task_id: str) -> dict | None:
    """The immutable dispatch document of one internal Router task, or None."""
    row = connection.execute("SELECT * FROM router_dispatches WHERE task_id=?", (task_id,)).fetchone()
    return _dispatch(connection, dict(row)) if row is not None else None


def dispatches(connection, decision_id: str) -> tuple[dict, ...]:
    """Fresh dispatch copies in indexed Router order, each retaining its own input."""
    rows = connection.execute("SELECT * FROM router_dispatches WHERE decision_id=? ORDER BY router_index,task_id",
                              (decision_id,))
    return tuple(_dispatch(connection, dict(row)) for row in rows)


def reserve_dispatch(connection, *, decision_id: str, task_id: str, router_index: int,
                     buddy: dict, now: str, round: int = 1) -> dict:
    """Record one dispatch under its internal task; the caller owns the transaction.

    ``buddy`` is the complete buddy identity — ``buddyId`` plus the four-part
    tuple — exactly as resolution selected it. The document is reconstructed
    from the referenced round snapshot and this immutable actor identity.
    """
    row = _store(connection, "router_dispatches", {
        "task_id": task_id, "decision_id": decision_id, "round": round, "router_index": router_index,
        "buddy_id": buddy["buddyId"], **{key: buddy[key] for key in _IDENTITY_FIELDS},
        "created_at": now,
    })
    return _dispatch(connection, row)


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

    The binding and event commit in the caller's transaction, so a claim and its
    Router fact can never be observed apart.
    """
    dispatch_document = dispatch(connection, task_id)
    request = connection.execute("SELECT request_id FROM decision_requests WHERE decision_id=?",
                                 (dispatch_document["decisionId"],)).fetchone()
    _store(connection, "router_claims", {
        "attempt_id": attempt_id, "task_id": task_id, "generation": generation,
    })
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
