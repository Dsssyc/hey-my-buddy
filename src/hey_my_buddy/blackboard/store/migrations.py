"""The single explicit upgrade from the frozen 0.27.0 schema-15 board.

DDL here is the frozen target of this pending upgrade, independent of the fresh
schema. A later stored-form change must extend this migration and its fixture
comparison; startup never repairs an existing schema.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from .db import SCHEMA_VERSION, PREVIOUS_SCHEMA_VERSION, canonical_json
from ...errors import BoardError

NEW_TABLES = frozenset({"model_facts", "router_requests", "router_dispatches", "router_claims",
                        "router_outcomes", "catalog_pending"})
_SOURCE_ROUTER_META_KEYS = frozenset({"router_configuration_version", "router_fast_profile_id",
    "router_review_profile_id", "router_default_mode", "router_budget_preset"})
ROUTER_META_KEYS = _SOURCE_ROUTER_META_KEYS | {"router_profile_ids", "router_retry_interval_seconds"}

# Frozen migration DDL, deliberately not derived from db.SCHEMA at execution time.
_NEW_DDL = (
    """CREATE TABLE model_facts (
    adapter          TEXT NOT NULL,
    provider         TEXT NOT NULL,
    model            TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('current','unknown')),
    facts_json       TEXT NOT NULL DEFAULT '{}',
    identity_json    TEXT NOT NULL DEFAULT '{}',
    source           TEXT NOT NULL,
    payload_sha256   TEXT NOT NULL,
    fetched_at       TEXT NOT NULL,
    reason           TEXT,
    PRIMARY KEY (adapter, provider, model)
)""",
    """CREATE INDEX events_router_outcome_idx
    ON events(json_extract(CASE WHEN json_valid(payload_json) THEN payload_json END, '$.profileId'), seq)
    WHERE kind IN ('router.answered','router.no_answer')""",
    """CREATE INDEX events_router_claimed_idx
    ON events(json_extract(CASE WHEN json_valid(payload_json) THEN payload_json END, '$.profileId'), seq)
    WHERE kind='router.claimed'""",
    """CREATE TABLE router_requests (
    decision_id      TEXT NOT NULL PRIMARY KEY REFERENCES decision_requests(decision_id) ON DELETE RESTRICT,
    snapshot_json    TEXT NOT NULL,
    frozen_at        TEXT NOT NULL
)""",
    """CREATE TABLE router_dispatches (
    task_id      TEXT NOT NULL PRIMARY KEY REFERENCES tasks(task_id) ON DELETE RESTRICT,
    decision_id  TEXT NOT NULL REFERENCES router_requests(decision_id) ON DELETE RESTRICT,
    router_index INTEGER NOT NULL CHECK (router_index >= 0),
    profile_id   TEXT NOT NULL,
    adapter      TEXT NOT NULL,
    provider     TEXT NOT NULL,
    model        TEXT NOT NULL,
    effort       TEXT NOT NULL,
    document_json TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    UNIQUE (decision_id, router_index)
)""",
    """CREATE UNIQUE INDEX attempts_router_binding_unique ON attempts(attempt_id, task_id, generation)""",
    """CREATE TABLE router_claims (
    attempt_id TEXT NOT NULL PRIMARY KEY,
    task_id    TEXT NOT NULL REFERENCES router_dispatches(task_id) ON DELETE RESTRICT,
    generation INTEGER NOT NULL CHECK (generation >= 1),
    FOREIGN KEY (attempt_id, task_id, generation) REFERENCES attempts(attempt_id, task_id, generation) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    UNIQUE (task_id, generation)
)""",
    """CREATE UNIQUE INDEX attempts_router_outcome_binding_unique ON attempts(attempt_id, task_id)""",
    """CREATE TABLE router_outcomes (
    receipt_id   TEXT NOT NULL PRIMARY KEY,
    plane        TEXT NOT NULL CHECK (plane IN ('work','maintenance','portrait')),
    request_id   TEXT NOT NULL,
    router_index INTEGER NOT NULL CHECK (router_index >= 0),
    profile_id   TEXT NOT NULL,
    phase        TEXT NOT NULL,
    task_id      TEXT REFERENCES tasks(task_id) ON DELETE RESTRICT,
    attempt_id   TEXT,
    outcome      TEXT NOT NULL CHECK (outcome IN ('answered','no_answer')),
    code         TEXT,
    event_seq    INTEGER NOT NULL UNIQUE REFERENCES events(seq) ON DELETE RESTRICT,
    recorded_at  TEXT NOT NULL,
    CHECK (attempt_id IS NULL OR task_id IS NOT NULL),
    FOREIGN KEY (attempt_id, task_id) REFERENCES attempts(attempt_id, task_id) ON DELETE RESTRICT
)""",
    """CREATE INDEX router_outcomes_profile_idx ON router_outcomes(profile_id, event_seq)""",
    """CREATE INDEX router_outcomes_task_idx ON router_outcomes(task_id, event_seq)""",
    """CREATE INDEX router_outcomes_attempt_idx ON router_outcomes(attempt_id)""",
    """CREATE TABLE catalog_pending (
    adapter      TEXT NOT NULL REFERENCES catalog_current(adapter) ON DELETE RESTRICT,
    provider     TEXT NOT NULL,
    model        TEXT NOT NULL,
    since        TEXT NOT NULL,
    efforts_json TEXT NOT NULL,
    PRIMARY KEY (adapter, provider, model)
)""",
)
_REVIEW_INDEX = """CREATE INDEX events_review_seq_idx
    ON events(seq) WHERE kind IN ('task.accepted','task.rejected','workflow.acknowledged','workflow.review_rejected')"""
_CONCLUSION_TABLE = """
CREATE TABLE workflow_host_conclusions_16 (
    conclusion_id    TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    attempt_id       TEXT REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    run_revision     INTEGER NOT NULL,
    owner_generation INTEGER NOT NULL,
    execution_status TEXT NOT NULL CHECK (execution_status IN ('failed','cancelled','delivered')),
    note             TEXT NOT NULL,
    evidence_json    TEXT NOT NULL DEFAULT '[]',
    artifact_id      TEXT REFERENCES workflow_artifacts(artifact_id) ON DELETE RESTRICT,
    integration_id   TEXT REFERENCES workflow_integrations(integration_id) ON DELETE RESTRICT,
    actor            TEXT NOT NULL,
    command_id       TEXT NOT NULL UNIQUE,
    created_at       TEXT NOT NULL
);
"""
_CONCLUSION_INDEX = "CREATE INDEX workflow_host_conclusions_run_idx ON workflow_host_conclusions(run_id, run_revision)"


# Frozen table replacements and named copy columns for both schema-15 column orders.
_REBUILT_TABLES = (
    ("attempts", """CREATE TABLE attempts_16 (
    attempt_id          TEXT PRIMARY KEY,
    task_id             TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    generation          INTEGER NOT NULL,
    worker_id           TEXT,
    worker_identity     TEXT,
    worker_instance     TEXT,
    capability_version  INTEGER NOT NULL DEFAULT 1,
    nonce_verifier      TEXT NOT NULL,
    claim_request_id    TEXT NOT NULL,
    lease_expires_at    TEXT,
    lease_seconds       INTEGER NOT NULL DEFAULT 120,
    execution_state     TEXT NOT NULL
        CHECK (execution_state IN ('starting','executing','finalizing','uncertain','finished')),
    ownership           TEXT NOT NULL DEFAULT 'owned' CHECK (ownership IN ('owned','uncertain')),
    runtime_identity    TEXT,
    adapter             TEXT NOT NULL,
    model_adapter       TEXT,
    model_provider      TEXT,
    model_model         TEXT,
    log_paths           TEXT NOT NULL DEFAULT '{}',
    result_json         TEXT,
    result_command_id   TEXT,
    error               TEXT,
    shutdown_confirmed  INTEGER NOT NULL DEFAULT 0,
    cancel_requested_at TEXT,
    exit_code           INTEGER,
    signal              TEXT,
    started_at          TEXT,
    finished_at         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    revision            INTEGER NOT NULL DEFAULT 1,
    token_usage_json    TEXT,
    UNIQUE(task_id, generation)
)""",
     ('attempt_id, task_id, generation, worker_id, worker_identity, worker_instance, capability_version, '
      'nonce_verifier, claim_request_id, lease_expires_at, lease_seconds, execution_state, ownership, '
      'runtime_identity, adapter, model_adapter, model_provider, model_model, log_paths, result_json, '
      'result_command_id, error, shutdown_confirmed, cancel_requested_at, exit_code, signal, started_at, '
      'finished_at, created_at, updated_at, revision, token_usage_json'),
     (
         """CREATE INDEX attempts_task_idx ON attempts(task_id, generation)""",
         """CREATE UNIQUE INDEX attempts_effective_unique ON attempts(task_id)
    WHERE execution_state IN ('starting','executing','finalizing','uncertain')""",
         """CREATE INDEX attempts_active_idx ON attempts(execution_state)""",
         """CREATE INDEX attempts_model_family_idx
    ON attempts(model_adapter, model_provider, model_model) WHERE model_model IS NOT NULL""",
     )),
    ("workflow_runs", """CREATE TABLE workflow_runs_16 (
    run_id                  TEXT PRIMARY KEY REFERENCES tasks(task_id) ON DELETE RESTRICT,
    objective_id            TEXT REFERENCES objectives(objective_id) ON DELETE RESTRICT,
    title                   TEXT,
    activity_seq            INTEGER NOT NULL DEFAULT 0,
    activity_at             TEXT,
    host_id                 TEXT NOT NULL,
    owner_generation        INTEGER NOT NULL DEFAULT 1,
    control_verifier        TEXT NOT NULL,
    goal_json               TEXT NOT NULL,
    goal_fingerprint        TEXT NOT NULL,
    request_fingerprint     TEXT NOT NULL,
    execution_configuration_json TEXT,
    execution_configuration_revision INTEGER NOT NULL DEFAULT 0,
    validated_configuration_revision INTEGER,
    current_routing_id      TEXT,
    submission_verifier     TEXT,
    execution_workspace_json TEXT NOT NULL,
    workspace_manifest_json TEXT,
    workspace_id            TEXT,
    workspace_manifest_sha256 TEXT,
    state                   TEXT NOT NULL
        CHECK (state IN ('executing','awaiting-host','waiting-helpers','delivered','accepted','cancelled','failed')),
    active_request_id       TEXT,
    current_turn_id         TEXT,
    current_attempt_id      TEXT,
    continuation_count      INTEGER NOT NULL DEFAULT 0,
    final_artifact_id       TEXT,
    final_attempt_id        TEXT,
    revision                INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    configuration_locked    INTEGER NOT NULL DEFAULT 0 CHECK (configuration_locked IN (0,1))
)""",
     ('run_id, objective_id, title, activity_seq, activity_at, host_id, owner_generation, control_verifier, '
      'goal_json, goal_fingerprint, request_fingerprint, execution_configuration_json, '
      'execution_configuration_revision, validated_configuration_revision, current_routing_id, '
      'submission_verifier, execution_workspace_json, workspace_manifest_json, workspace_id, '
      'workspace_manifest_sha256, state, active_request_id, current_turn_id, current_attempt_id, '
      'continuation_count, final_artifact_id, final_attempt_id, revision, created_at, updated_at, '
      'configuration_locked'),
     (
         """CREATE INDEX workflow_runs_state_idx ON workflow_runs(state, updated_at)""",
         """CREATE INDEX workflow_runs_objective_idx ON workflow_runs(objective_id, activity_seq DESC, run_id DESC)""",
         """CREATE INDEX workflow_runs_activity_idx ON workflow_runs(activity_seq)""",
     )),
    ("evaluation_cards", """CREATE TABLE evaluation_cards_16 (
    profile_id        TEXT PRIMARY KEY REFERENCES evaluation_profiles(profile_id) ON DELETE RESTRICT,
    revision          INTEGER NOT NULL,
    summary           TEXT NOT NULL DEFAULT '',
    origin            TEXT NOT NULL DEFAULT 'unattributed' CHECK(origin IN ('maintenance','unattributed')),
    strengths_json    TEXT NOT NULL DEFAULT '[]',
    limitations_json  TEXT NOT NULL DEFAULT '[]',
    risks_json        TEXT NOT NULL DEFAULT '[]',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    sample_count      INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT NOT NULL
)""",
     ('profile_id, revision, summary, origin, strengths_json, limitations_json, risks_json, '
      'evidence_ids_json, sample_count, updated_at'),
     (
     )),
)


def _rebuilt_fingerprints(connection: sqlite3.Connection) -> dict[str, str]:
    """Compare every retained value by column name, independent of physical order.

    SQLite sorts the rows; Python hashes one row at a time. Ordinary backup
    snapshots retain their historical positional fingerprint contract.
    """
    result = {}
    for table, _ddl, names, _indexes in _REBUILT_TABLES:
        columns = ", ".join(sorted(names.split(", ")))
        digest = hashlib.sha256()
        for row in connection.execute(f"SELECT {columns} FROM {table} ORDER BY {columns}"):
            digest.update(json.dumps(tuple(row), ensure_ascii=False, separators=(",", ":")).encode())
            digest.update(b"\n")
        result[table] = digest.hexdigest()
    return result


def schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def _router_values(values: dict[str, str]) -> dict[str, str]:
    # The upgrade base materializes all five keys on creation, explicit upgrade
    # and configuration publication. Its unmarked legacy read projection is not
    # an input form for this single pending upgrade.
    source_keys = values.keys() & ROUTER_META_KEYS
    missing = sorted(_SOURCE_ROUTER_META_KEYS - source_keys)
    unexpected = sorted(source_keys - _SOURCE_ROUTER_META_KEYS)
    version = values.get("router_configuration_version")
    if version != "1" or missing or unexpected:
        raise BoardError("UPGRADE_MIGRATION_FAILED",
                         "Schema 15 Router configuration must match the upgrade-base version 1 form",
                         tables=["meta"], routerForm={"expectedVersion":"1", "actualVersion":version,
                                                     "missingKeys":missing, "unexpectedKeys":unexpected})
    from ..routing.router_settings import convert_legacy_router_settings
    conversion = convert_legacy_router_settings({
        "fastRouterProfileId": values["router_fast_profile_id"] or None,
        "reviewRouterProfileId": values["router_review_profile_id"] or None,
        "defaultRoutingMode": values["router_default_mode"],
        "routingBudget": values["router_budget_preset"],
    })
    settings = conversion.settings.as_dict()
    keys = {"routerProfileIds": "router_profile_ids", "routerRetryIntervalSeconds": "router_retry_interval_seconds",
            "defaultRoutingMode": "router_default_mode", "routingBudget": "router_budget_preset"}
    converted = {keys[name]: canonical_json(value) if name == "routerProfileIds" else str(value)
                 for name, value in settings.items()}
    converted["router_configuration_version"] = "3"
    return converted


def migrate_15_to_16(connection: sqlite3.Connection) -> dict:
    """Convert SQL, Router settings and catalog trust atomically, retaining history."""
    if schema_version(connection) != PREVIOUS_SCHEMA_VERSION:
        raise BoardError("UPGRADE_MIGRATION_FAILED", "Migration expects schema 15", tables=["meta"])
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    connection.execute("PRAGMA foreign_keys=OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        return _migrate_15_to_16_transaction(connection)
    finally:
        connection.execute(f"PRAGMA foreign_keys={foreign_keys}")


def _migrate_15_to_16_transaction(connection: sqlite3.Connection) -> dict:
    from .backup import database_snapshot

    tables = ["meta"]
    try:
        original_meta = dict(connection.execute("SELECT key,value FROM meta"))
        converted = _router_values(original_meta)
        planned_meta = {key: value for key, value in original_meta.items() if key not in ROUTER_META_KEYS}
        planned_meta.update(converted)
        planned_meta["schema_version"] = str(SCHEMA_VERSION)
        current = [tuple(row) for row in connection.execute("SELECT * FROM catalog_current ORDER BY adapter")]
        adapters = [row[0] for row in current]
        planned_catalog = [(row[0], row[1], row[2], "unknown", "ACCOUNT_STATUS_UNKNOWN", row[5])
                           for row in current]
        retained_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        retained_tables -= {"meta", "catalog_current"} | {table for table, *_rest in _REBUILT_TABLES}
        retained = database_snapshot(connection, exact=True, table_names=retained_tables)
        rebuilt = _rebuilt_fingerprints(connection)
        for table, ddl, columns, indexes in _REBUILT_TABLES:
            tables = [table]
            connection.execute(ddl)
            connection.execute(f"INSERT INTO {table}_16 ({columns}) SELECT {columns} FROM {table}")
            connection.execute(f"DROP TABLE {table}")
            connection.execute(f"ALTER TABLE {table}_16 RENAME TO {table}")
            for statement in indexes:
                connection.execute(statement)
        for statement in _NEW_DDL:
            # The table/index named by this fixed statement is the failure context.
            words = statement.split()
            tables = ["attempts" if words[2] == "INDEX" else "events" if "ON events(" in statement
                      else words[2] if words[1] == "TABLE" else statement.split(" ON ")[1].split("(")[0]]
            connection.execute(statement)
        tables = ["events"]
        connection.execute("DROP INDEX events_review_seq_idx")
        connection.execute(_REVIEW_INDEX)
        tables = ["workflow_host_conclusions"]
        connection.execute(_CONCLUSION_TABLE)
        connection.execute("INSERT INTO workflow_host_conclusions_16 SELECT * FROM workflow_host_conclusions")
        connection.execute("DROP TABLE workflow_host_conclusions")
        connection.execute("ALTER TABLE workflow_host_conclusions_16 RENAME TO workflow_host_conclusions")
        connection.execute(_CONCLUSION_INDEX)
        tables = ["meta"]
        for key in ROUTER_META_KEYS:
            connection.execute("DELETE FROM meta WHERE key=?", (key,))
        connection.executemany("INSERT INTO meta(key,value) VALUES(?,?)", converted.items())
        tables = ["catalog_current"]
        connection.execute("UPDATE catalog_current SET status='unknown',reason='ACCOUNT_STATUS_UNKNOWN'")
        tables = ["meta"]
        connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
        actual = database_snapshot(connection, exact=True, table_names=retained_tables)
        changed = [name for name in retained['fingerprints']
                   if retained['fingerprints'][name] != actual['fingerprints'].get(name)
                   or retained['tables'][name] != actual['tables'].get(name)]
        actual_rebuilt = _rebuilt_fingerprints(connection)
        changed += [name for name in rebuilt if rebuilt[name] != actual_rebuilt[name]]
        changed += [name for name in retained.get('exemptFingerprints', {})
                    if retained['exemptFingerprints'][name] != actual['exemptFingerprints'].get(name)]
        if dict(connection.execute("SELECT key,value FROM meta")) != planned_meta:
            changed.append("meta")
        if [tuple(row) for row in connection.execute("SELECT * FROM catalog_current ORDER BY adapter")] != planned_catalog:
            changed.append("catalog_current")
        changed += [name for name in NEW_TABLES if connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]]
        if changed:
            raise BoardError("UPGRADE_MIGRATION_FAILED", "Migration changed values outside its exact plan",
                             tables=sorted(set(changed)))
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise BoardError("UPGRADE_MIGRATION_FAILED", "Foreign key check failed after migration",
                             tables=sorted({row[0] for row in violations}))
        integrity = [tuple(row) for row in connection.execute("PRAGMA integrity_check")]
        if integrity != [("ok",)]:
            raise BoardError("UPGRADE_MIGRATION_FAILED", "Integrity check failed after migration", tables=[],
                             integrity=integrity)
        connection.execute("COMMIT")
    except BaseException as error:
        connection.execute("ROLLBACK")
        if isinstance(error, (sqlite3.Error, ValueError)):
            raise BoardError("UPGRADE_MIGRATION_FAILED", str(error), tables=tables) from error
        if isinstance(error, BoardError) and error.code != "UPGRADE_MIGRATION_FAILED":
            raise BoardError("UPGRADE_MIGRATION_FAILED", error.message, tables=tables, reason=error.payload()) from error
        raise
    return {"fromSchema": PREVIOUS_SCHEMA_VERSION, "toSchema": SCHEMA_VERSION,
            "newTables": sorted(NEW_TABLES), "routerProfileIds": json.loads(converted['router_profile_ids']),
            "catalogUnknown": adapters}
