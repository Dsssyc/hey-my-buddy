"""Host review of a failed/cancelled execution, separate from successful acceptance."""
from __future__ import annotations

import json


def current(connection, run):
    if run["state"] != "concluded":
        return None
    return connection.execute(
        "SELECT * FROM workflow_host_conclusions WHERE run_id=?", (run["run_id"],)
    ).fetchone()


def view(row):
    if row is None:
        return None
    return {"conclusionId": row["conclusion_id"], "attemptId": row["attempt_id"],
            "executionStatus": row["execution_status"], "note": row["note"],
            "evidence": json.loads(row["evidence_json"])["references"], "artifactId": row["artifact_id"],
            "integrationId": row["integration_id"], "actor": row["actor"], "createdAt": row["created_at"],
            "ownerGeneration": row["owner_generation"], "runRevision": row["run_revision"]}
