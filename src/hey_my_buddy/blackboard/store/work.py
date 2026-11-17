"""Pending autonomous work, shared by service shutdown and installation."""
from __future__ import annotations

import sqlite3


def autonomous_tasks(connection: sqlite3.Connection) -> list[dict[str, str]]:
    """A queued Host boundary is passive only without a pending continuation.

    Routing and helper tasks remain in the complete task inventory. Unresolved
    attempts are checked separately, including attempts of passive tasks.
    """
    rows = connection.execute(
        "SELECT t.task_id,t.state FROM tasks t"
        " WHERE t.state IN ('queued','running','cancelling','reconciliation-needed')"
        " AND NOT (t.state='queued' AND EXISTS ("
        " SELECT 1 FROM workflow_runs r WHERE r.run_id=t.task_id AND r.state='awaiting-host'"
        " AND NOT EXISTS (SELECT 1 FROM workflow_continuations c WHERE c.run_id=r.run_id"
        " AND c.state IN ('recorded','queued'))))"
        " ORDER BY t.created_at"
    ).fetchall()
    return [{"runId": row[0], "state": row[1]} for row in rows]
