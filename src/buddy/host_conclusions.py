"""Host review of a failed/cancelled execution, separate from successful acceptance."""
from __future__ import annotations

import json
import uuid

from . import schemas
from .db import canonical_json, sha256_text
from .errors import BoardError


def artifact_digest(connection, run_id):
    return sha256_text(canonical_json([tuple(row) for row in connection.execute(
        "SELECT artifact_id,manifest_sha256 FROM workflow_artifacts WHERE run_id=? ORDER BY artifact_id", (run_id,))]))


def current(connection, run, task):
    row = connection.execute("SELECT * FROM workflow_host_conclusions WHERE run_id=? ORDER BY rowid DESC LIMIT 1", (run["run_id"],)).fetchone()
    # A delivered goal keeps its delivered state after its conclusion: the real
    # execution result stays what it was, the conclusion is what closes it.
    if row is None or run["state"] not in ("failed", "cancelled", "delivered"):
        return None
    if (row["execution_status"] != run["state"] or row["attempt_id"] != task["selected_attempt_id"]
            or row["owner_generation"] != run["owner_generation"]):
        return None
    if connection.execute("SELECT 1 FROM events WHERE task_id=? AND kind='workflow.continued' AND revision>? LIMIT 1",
                          (run["run_id"], row["run_revision"])).fetchone():
        return None
    if json.loads(row["evidence_json"])["artifactSetSha256"] != artifact_digest(connection, run["run_id"]):
        return None
    return row


def view(row):
    if row is None:
        return None
    return {"conclusionId": row["conclusion_id"], "attemptId": row["attempt_id"],
            "executionStatus": row["execution_status"], "note": row["note"],
            "evidence": json.loads(row["evidence_json"])["references"], "artifactId": row["artifact_id"],
            "integrationId": row["integration_id"], "actor": row["actor"], "createdAt": row["created_at"],
            "ownerGeneration": row["owner_generation"], "runRevision": row["run_revision"]}
