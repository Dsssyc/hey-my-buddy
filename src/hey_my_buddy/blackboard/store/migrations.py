"""The single explicit upgrade from the frozen 0.27.0 schema-15 board.

DDL here is the frozen target of this pending upgrade, independent of the fresh
schema. A later stored-form change must extend this migration and its fixture
comparison; startup never repairs an existing schema.
"""
from __future__ import annotations

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
    from .backup import database_snapshot

    if schema_version(connection) != PREVIOUS_SCHEMA_VERSION:
        raise BoardError("UPGRADE_MIGRATION_FAILED", "Migration expects schema 15", tables=["meta"])
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("BEGIN IMMEDIATE")
    tables = ["meta"]
    try:
        original_meta = dict(connection.execute("SELECT key,value FROM meta"))
        converted = _router_values(original_meta)
        planned_meta = {key: value for key, value in original_meta.items() if key not in ROUTER_META_KEYS}
        planned_meta.update(converted)
        planned_meta["schema_version"] = str(SCHEMA_VERSION)
        current = [tuple(row) for row in connection.execute("SELECT * FROM catalog_current ORDER BY adapter")]
        adapters = [row[0] for row in current]
        planned_meta.update({"catalog-account-status:" + name: "unknown" for name in adapters})
        planned_catalog = [(row[0], row[1], row[2], "unknown", "ACCOUNT_STATUS_UNKNOWN", row[5])
                           for row in current]
        retained_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        retained_tables -= {"meta", "catalog_current"}
        retained = database_snapshot(connection, exact=True, table_names=retained_tables)
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
        connection.executemany("INSERT INTO meta(key,value) VALUES(?,?)",
                               [("catalog-account-status:" + name, "unknown") for name in adapters])
        tables = ["catalog_current"]
        connection.execute("UPDATE catalog_current SET status='unknown',reason='ACCOUNT_STATUS_UNKNOWN'")
        tables = ["meta"]
        connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
        actual = database_snapshot(connection, exact=True, table_names=retained_tables)
        changed = [name for name in retained['fingerprints']
                   if retained['fingerprints'][name] != actual['fingerprints'].get(name)
                   or retained['tables'][name] != actual['tables'].get(name)]
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
