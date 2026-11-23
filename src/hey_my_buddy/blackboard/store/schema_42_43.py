"""Schema 42 → 43: restore open workspace-conflict boundaries cleared by prior cancellation."""
from __future__ import annotations

import json
import sqlite3
import uuid

_REQUEST_NAMESPACE = uuid.UUID("72a944a4-6ef3-4605-9f88-58c17d40a8a3")


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _request_id(run_id: str, conflict_ids: list[str]) -> str:
    return "req-" + str(uuid.uuid5(_REQUEST_NAMESPACE, _json({"conflictIds": conflict_ids, "runId": run_id})))


def upgrade(connection: sqlite3.Connection) -> None:
    """Open a fresh workspace-conflict boundary on cancelled runs with open conflicts."""
    runs = connection.execute(
        "SELECT r.run_id, r.updated_at FROM workflow_runs r"
        " WHERE r.state='cancelled'"
        " AND EXISTS (SELECT 1 FROM workflow_workspace_conflicts c WHERE c.run_id=r.run_id AND c.state='open')"
        " AND NOT EXISTS (SELECT 1 FROM workflow_requests req"
        " WHERE req.run_id=r.run_id AND req.state='open' AND req.reason_code='workspace-scope-violation')"
        " ORDER BY r.rowid"
    ).fetchall()
    for run in runs:
        run_id = run["run_id"]
        conflicts = connection.execute(
            "SELECT conflict_id, updated_at FROM workflow_workspace_conflicts"
            " WHERE run_id=? AND state='open' ORDER BY created_at, conflict_id",
            (run_id,),
        ).fetchall()
        conflict_ids = [row["conflict_id"] for row in conflicts]
        timestamp = max([run["updated_at"], *(row["updated_at"] for row in conflicts)])
        request_id = _request_id(run_id, conflict_ids)
        connection.execute(
            "INSERT INTO workflow_requests("
            "request_id, run_id, turn_id, attempt_id, summary, reason_code, reason_params_json,"
            " decision_id, payload_json, state, resolution_json, response_kind, response_id,"
            " created_at, updated_at, decided_at)"
            " VALUES(?, ?, NULL, NULL, NULL, 'workspace-scope-violation', ?, NULL, '{}', 'open',"
            " NULL, NULL, NULL, ?, ?, NULL)",
            (request_id, run_id, _json({"conflictIds": conflict_ids}), timestamp, timestamp),
        )
        connection.execute(
            "UPDATE workflow_runs SET state='awaiting-host', active_request_id=?, updated_at=? WHERE run_id=?",
            (request_id, timestamp, run_id),
        )
        connection.execute(
            "UPDATE tasks SET queue_reason='awaiting-host', updated_at=?"
            " WHERE task_id=? AND active_attempt_id IS NULL"
            " AND (queue_reason IS NULL OR queue_reason!='awaiting-host')",
            (timestamp, run_id),
        )
    connection.execute("UPDATE meta SET value='43' WHERE key='schema_version'")
