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
    if row is None or run["state"] not in ("failed", "cancelled"):
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


def record(workflow, params, *, console_authority=None):
    owner_id = schemas.required_string(params, "runId", max_length=128)
    target_id = schemas.optional_string(params, "targetRunId", max_length=128) or owner_id
    command_id = schemas.required_string(params, "commandId", max_length=128)
    expected = schemas.require_expected_revision(params)
    note, _ = schemas.bounded_text(params, "note", max_bytes=schemas.MAX_NOTE_BYTES)
    evidence = schemas.string_list(params, "evidence", limit=32)
    artifact_id = schemas.optional_string(params, "artifactId", max_length=128)
    integration_id = schemas.optional_string(params, "integrationId", max_length=128)
    key = {"runId": owner_id, "targetRunId": target_id, "expectedRevision": expected, "note": note,
           "evidence": evidence, "artifactId": artifact_id, "integrationId": integration_id, "verdict": "recorded"}
    now = workflow.now()
    with workflow.db.write() as connection:
        owner = workflow._run_row(connection, owner_id)
        actor = workflow._authorize(connection, owner, params, console_authority=console_authority, action="A Host conclusion")
        receipt = workflow.board._receipt(connection, command_id, "workflow.host_conclude", key)
        if receipt is not None:
            return {**receipt, "duplicate": True}
        workflow._expect_revision(owner, expected)
        run = workflow._target_run(connection, owner, target_id)
        task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (target_id,)).fetchone()
        if run["state"] not in ("failed", "cancelled"):
            raise BoardError("NOT_READY", "A recorded conclusion requires a failed or cancelled goal")
        workflow._lineage_stop_evidence(connection, run, "A Host conclusion")
        if integration_id and not artifact_id:
            raise BoardError("INVALID_ARGUMENT", "integrationId requires artifactId")
        if artifact_id:
            artifact = connection.execute("SELECT * FROM workflow_artifacts WHERE artifact_id=? AND run_id=? AND kind IN ('output','resolved-output','partial-output')", (artifact_id, target_id)).fetchone()
            if artifact is None or artifact["attempt_id"] != task["selected_attempt_id"]:
                raise BoardError("CONFLICT", "The conclusion must name an artifact of the reviewed attempt")
            if integration_id:
                workflow._require_integration(connection, target_id, artifact_id, integration_id)
        identifier = "conclusion-" + str(uuid.uuid4())
        connection.execute("INSERT INTO workflow_host_conclusions(conclusion_id,run_id,attempt_id,run_revision,owner_generation,execution_status,note,evidence_json,artifact_id,integration_id,actor,command_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (identifier, target_id, task["selected_attempt_id"], run["revision"] + 1,
                            run["owner_generation"], run["state"], note,
                            canonical_json({"references": evidence, "artifactSetSha256": artifact_digest(connection, target_id)}),
                            artifact_id, integration_id, actor, command_id, now))
        workflow.board._append_event(connection, "workflow.host_concluded", task_id=target_id,
                                     attempt_id=task["selected_attempt_id"], revision=run["revision"] + 1,
                                     payload={"conclusionId": identifier, "executionStatus": run["state"], "actor": actor})
        run = workflow._bump_run(connection, run, now)
        response = {**workflow.compact(connection, run), "verdict": "recorded", "duplicate": False,
                    "targetRunId": target_id, "targetRevision": run["revision"]}
        workflow.board._store_receipt(connection, command_id, "workflow.host_conclude", key, response,
                                      task_id=owner_id, attempt_id=task["selected_attempt_id"])
        head = workflow.board._head_of(connection)
    workflow.board._notify(head)
    return response
