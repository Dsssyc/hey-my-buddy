"""The one pending, offline upgrade from 0.48.0 (schema 26) to schema 27."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import json
import sqlite3

from .db import DB_FILE, SCHEMA, SCHEMA_VERSION

BASE_SCHEMA_VERSION = 26
_REBUILT_TABLES = (
    "macro_task_deletions", "commands", "tasks", "resource_claims",
    "attempt_interruptions", "messages", "workflow_runs", "workflow_children",
    "agent_credentials", "workflow_artifacts", "workflow_suggestions",
    "workflow_host_conclusions", "attempt_activity", "attempt_reminders",
    "workflow_routes", "workflow_continuations", "workspace_reservations",
    "workflow_workspace_conflicts", "workflow_integrations", "workspace_cleanup_plans",
)
_DEAD_TABLES = ("cursors", "evaluation_state", "model_facts")
_DEAD_INDEXES = (
    "tasks_request_idx", "attempts_task_idx", "workflow_turns_run_idx",
    "workflow_scope_versions_run_idx", "commands_kind_idx",
)


def _prune_workflow_records(connection: sqlite3.Connection) -> None:
    """Check old same-write copies once, then remove only those copies.

    None of these JSON documents is a digest preimage. The integration binding
    hashes a separate request identity; physical scope evidence and all sealed
    artifacts, receipts and command responses remain byte-for-byte unchanged.
    """
    def rows(table):
        cursor = connection.execute(f'SELECT * FROM "{table}"')
        names = [item[0] for item in cursor.description]
        return [dict(zip(names, row)) for row in cursor]

    def require(actual, expected, name):
        if actual != expected:
            raise ValueError(f"Inconsistent {name} in upgrade stock")

    def encode(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    for row in rows("workflow_workspace_conflicts"):
        evidence = json.loads(row["evidence_json"])
        require(row["manifest_sha256"], evidence["manifestSha256"], "conflict.manifest_sha256")
        require(json.loads(row["blocking_paths_json"]), evidence["blockingPaths"], "conflict.blocking_paths_json")
        artifact = json.loads(connection.execute(
            "SELECT manifest_json FROM workflow_artifacts WHERE artifact_id=?", (row["artifact_id"],)
        ).fetchone()[0]) if row["artifact_id"] else {}
        require(row["output_commit"], artifact.get("commit"), "conflict.output_commit")
        require(row["output_tree"], artifact.get("tree"), "conflict.output_tree")
        if row["delivery_json"] is not None:
            delivery = json.loads(row["delivery_json"])
            redundant = {"state": "delivered", "kind": "host-resolution", "action": row["action"],
                         "artifactId": row["artifact_id"], "attemptId": row["attempt_id"],
                         "turnId": row["turn_id"], "turnOutcome": "completed", "commandId": row["command_id"]}
            for key, value in redundant.items():
                require(delivery.pop(key), value, "conflict.delivery_json." + key)
            connection.execute("UPDATE workflow_workspace_conflicts SET delivery_json=? WHERE conflict_id=?",
                               (encode(delivery), row["conflict_id"]))

    for row in rows("workflow_integrations"):
        attempt = connection.execute("SELECT attempt_id FROM workflow_artifacts WHERE artifact_id=?",
                                     (row["artifact_id"],)).fetchone()[0]
        require(row["attempt_id"], attempt, "integration.attempt_id")
        not_required = row["strategy"] == "not-required"
        require(row["state"], "not-required" if not_required else "verified", "integration.state")
        require(row["target_kind"], "not-required" if not_required else "checkout", "integration.target_kind")
        verification = json.loads(row["verification_json"])
        if not_required:
            require(verification, {}, "integration.verification_json")
            for key in ("source_commit", "source_tree", "before_commit", "after_commit", "before_tree", "after_tree",
                        "target_repository_id", "target_checkout_id"):
                require(row[key], None, "integration." + key)
            for key in ("target_path", "target_ref"):
                require(row[key], "", "integration." + key)
            continue
        redundant = {"verified": True, "strategy": row["strategy"],
                     "target": {"kind": "checkout", "path": row["target_path"], "ref": row["target_ref"],
                                "repositoryId": row["target_repository_id"], "checkoutId": row["target_checkout_id"]},
                     "sourceCommit": row["source_commit"], "sourceTree": row["source_tree"],
                     "beforeCommit": row["before_commit"], "afterCommit": row["after_commit"],
                     "beforeTree": row["before_tree"], "afterTree": row["after_tree"],
                     "artifactAncestor": row["strategy"] == "merge",
                     "adjustments": sorted({item["path"] for item in verification["differingPaths"]}
                                           | set(verification["missingPaths"])),
                     "unrecordedPaths": [], "summary": ""}
        for key, value in redundant.items():
            require(verification.pop(key), value, "integration.verification_json." + key)
        connection.execute("UPDATE workflow_integrations SET verification_json=? WHERE integration_id=?",
                           (encode(verification), row["integration_id"]))

    for row in rows("workspace_cleanup_plans"):
        retention = json.loads(row["retention_json"])
        require(retention.pop("commandReceipts"), True, "cleanup.retention_json.commandReceipts")
        result = json.loads(row["result_json"]) if row["result_json"] is not None else None
        if result is not None:
            redundant = {"removed": row["state"] == "applied", "path": row["path"],
                         "repositoryId": row["repository_id"], "checkoutId": row["checkout_id"],
                         "retention": json.loads(row["retention_json"])}
            for key, value in redundant.items():
                require(result.pop(key), value, "cleanup.result_json." + key)
        connection.execute("UPDATE workspace_cleanup_plans SET retention_json=?,result_json=? WHERE plan_id=?",
                           (encode(retention), encode(result) if result is not None else None, row["plan_id"]))


def _convert(connection: sqlite3.Connection) -> None:
    # These redundant columns fed public views. Refuse inconsistent old stock
    # at the conversion boundary rather than silently changing its answer.
    if connection.execute("SELECT 1 FROM tasks WHERE acceptance_verdict IS NOT "
                          "CASE WHEN accepted_at IS NOT NULL THEN 'accepted' END LIMIT 1").fetchone():
        raise ValueError("Inconsistent tasks.acceptance_verdict in upgrade stock")
    if connection.execute("SELECT 1 FROM messages WHERE correlation_id IS NOT inquiry_id "
                          "OR body_bytes != length(CAST(body AS BLOB)) LIMIT 1").fetchone():
        raise ValueError("Inconsistent messages.correlation_id/body_bytes in upgrade stock")
    checks = (
        ("workflow_runs.micro_task_json/micro_task_fingerprint",
         "SELECT 1 FROM workflow_runs r JOIN tasks t ON t.task_id=r.run_id "
         "WHERE r.micro_task_json IS NOT t.spec_json OR r.micro_task_fingerprint IS NOT t.input_fingerprint"),
        ("workflow_runs.workspace_id/workspace_manifest_sha256",
         "SELECT 1 FROM workflow_runs WHERE workspace_id IS NOT json_extract(workspace_manifest_json,'$.workspaceId') "
         "OR workspace_manifest_sha256 IS NOT json_extract(workspace_manifest_json,'$.manifestSha256')"),
        ("workflow_children.workspace_manifest_sha256/auto_continue",
         "SELECT 1 FROM workflow_children WHERE auto_continue != 0 OR "
         "workspace_manifest_sha256 IS NOT json_extract(workspace_manifest_json,'$.manifestSha256')"),
        ("agent_credentials.task_id/generation/expires_at",
         "SELECT 1 FROM agent_credentials c LEFT JOIN attempts a ON a.attempt_id=c.attempt_id "
         "WHERE c.task_id IS NOT c.run_id OR c.generation IS NOT a.generation OR c.expires_at IS NOT NULL"),
        ("workflow_artifacts.source_task_id",
         "SELECT 1 FROM workflow_artifacts WHERE source_task_id IS NOT CASE WHEN kind='input' THEN NULL ELSE run_id END"),
        ("workflow_suggestions.author",
         "SELECT 1 FROM workflow_suggestions WHERE author IS NOT "
         "CASE WHEN attempt_id IS NOT NULL THEN 'attempt:' || attempt_id ELSE 'agent' END"),
        ("workflow_host_conclusions.evidence_json/integration_id",
         "SELECT 1 FROM workflow_host_conclusions WHERE json(evidence_json) != '{\"references\":[]}' OR integration_id IS NOT NULL"),
    )
    for name, query in checks:
        if connection.execute(query + " LIMIT 1").fetchone():
            raise ValueError(f"Inconsistent {name} in upgrade stock")
    # The same timestamp algorithm generated the old stored elapsed value. The
    # reminder event and any sealed input containing it remain untouched.
    from ..tasks.reminders import _seconds
    for elapsed, created, started in connection.execute(
        "SELECT r.elapsed_seconds,r.created_at,a.started_at FROM attempt_reminders r "
        "JOIN attempts a ON a.attempt_id=r.attempt_id"
    ):
        if elapsed != max(0.0, _seconds(created) - _seconds(started)):
            raise ValueError("Inconsistent attempt_reminders.elapsed_seconds in upgrade stock")
    _prune_workflow_records(connection)
    # Only completed deletions lose their receipts. A physical deletion still in
    # progress retains its fence, tasks and commands so it can finish normally.
    connection.execute("DELETE FROM commands WHERE deletion_id IN "
                       "(SELECT deletion_id FROM macro_task_deletions WHERE state='deleted')")
    connection.execute("DELETE FROM macro_task_deletions WHERE state='deleted'")
    connection.execute("DELETE FROM events WHERE kind='macro_task.deleted'")
    for name in _DEAD_INDEXES:
        connection.execute(f'DROP INDEX "{name}"')
    for name in _DEAD_TABLES:
        connection.execute(f'DROP TABLE "{name}"')

    sequences = dict(connection.execute("SELECT name,seq FROM sqlite_sequence"))

    # Take DDL from the fresh schema, keeping constraints and indexes identical
    # without maintaining a second copy of the current table definitions.
    with closing(sqlite3.connect(":memory:")) as fresh:
        fresh.executescript(SCHEMA)
        for table in _REBUILT_TABLES:
            columns = ",".join(f'"{row[1]}"' for row in fresh.execute(f'PRAGMA table_info("{table}")'))
            connection.execute(f'CREATE TEMP TABLE upgrade_saved AS SELECT {columns} FROM "{table}"')
            connection.execute(f'DROP TABLE "{table}"')
            ddl = fresh.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
            connection.execute(ddl)
            connection.execute(f'INSERT INTO "{table}" ({columns}) SELECT {columns} FROM upgrade_saved')
            connection.execute("DROP TABLE upgrade_saved")
            for ddl, in fresh.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,)):
                connection.execute(ddl)
            # Rebuilding an AUTOINCREMENT table must retain ids consumed by
            # deleted rows, and must not invent a sequence for a never-used table.
            connection.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
            if table in sequences:
                connection.execute("INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)", (table, sequences[table]))


def upgrade(state: Path) -> None:
    """Convert a stopped schema-26 board atomically; reject other schemas."""
    path = Path(state) / DB_FILE
    if not path.is_file():
        raise FileNotFoundError(path)
    with closing(sqlite3.connect(path, isolation_level=None, timeout=10)) as connection:
        # Referenced tables are rebuilt inside one transaction. Validate all
        # relationships before commit, after their original names are restored.
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version is None or version[0] != str(BASE_SCHEMA_VERSION):
                raise ValueError(f"Expected schema {BASE_SCHEMA_VERSION} for upgrade; found {None if version is None else version[0]!r}")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("Board integrity check failed before schema upgrade")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Board foreign key check failed before schema upgrade")
            _convert(connection)
            connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Board foreign key check failed after schema upgrade")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("Board integrity check failed after schema upgrade")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
