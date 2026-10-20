"""One explicit, transactional upgrade from the installed schema-25 board."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

from ...json_codec import canonical_json
from .db import DB_FILE, ROUTER_CLAIMS_DDL, ROUTER_OUTCOMES_DDL, SCHEMA_VERSION

BASE_SCHEMA_VERSION = 25
_RETIRED_CAPABILITIES = frozenset({"decision", "routing:fast"})
_RETIRED_HEALTH_KEYS = frozenset({"reviewVerification", "systemSandbox", "readOnlyStructured"})


def _statement(ddl: str) -> str:
    return ddl.strip().removesuffix(";")


def _receipt_id(row: sqlite3.Row, event: dict, plane: str) -> str:
    selection_retry = event.get("selectionRetry", False)
    if type(selection_retry) is not bool:
        raise ValueError(f"Invalid selectionRetry for router outcome {row['event_seq']}")
    fields = [plane, row["request_id"], selection_retry, row["router_index"], row["phase"],
              row["task_id"], row["attempt_id"], row["outcome"]]
    return hashlib.sha256(canonical_json(fields).encode("utf-8")).hexdigest()


def _convert_rows(connection: sqlite3.Connection) -> None:
    for row in connection.execute("SELECT buddy_id,capabilities_json FROM buddies"):
        capabilities = json.loads(row["capabilities_json"])
        if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
            raise ValueError(f"Invalid buddy capabilities: {row['buddy_id']}")
        retained = [item for item in capabilities
                    if item not in _RETIRED_CAPABILITIES and not item.startswith("permission:")]
        if retained != capabilities:
            connection.execute("UPDATE buddies SET capabilities_json=? WHERE buddy_id=?",
                               (canonical_json(retained), row["buddy_id"]))

    for row in connection.execute("SELECT harness,record_json FROM harness_health"):
        record = json.loads(row["record_json"])
        if not isinstance(record, dict):
            raise ValueError(f"Invalid harness health record: {row['harness']}")
        if _RETIRED_HEALTH_KEYS.intersection(record):
            for key in _RETIRED_HEALTH_KEYS:
                record.pop(key, None)
            connection.execute("UPDATE harness_health SET record_json=? WHERE harness=?",
                               (canonical_json(record), row["harness"]))

    for row in connection.execute(
            "SELECT seq,kind,payload_json FROM events WHERE kind IN ('router.claimed','router.answered','router.no_answer')"):
        event = json.loads(row["payload_json"])
        if not isinstance(event, dict):
            raise ValueError(f"Invalid router event payload: {row['seq']}")
        if event.get("plane") == "work":
            event["plane"] = "routing"
            connection.execute("UPDATE events SET payload_json=? WHERE seq=?",
                               (canonical_json(event), row["seq"]))


def _rebuild_router_tables(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE TEMP TABLE router_claims_saved AS "
                       "SELECT attempt_id,task_id,generation FROM router_claims")
    connection.execute("DROP TABLE router_claims")
    connection.execute(_statement(ROUTER_CLAIMS_DDL))
    connection.execute("INSERT INTO router_claims(attempt_id,task_id,generation) "
                       "SELECT attempt_id,task_id,generation FROM router_claims_saved")
    connection.execute("DROP TABLE router_claims_saved")

    connection.execute("CREATE TEMP TABLE router_outcomes_saved AS SELECT * FROM router_outcomes")
    connection.execute("DROP TABLE router_outcomes")
    for statement in ROUTER_OUTCOMES_DDL.split(";"):
        if statement.strip():
            connection.execute(statement.strip())
    rows = connection.execute("SELECT * FROM router_outcomes_saved ORDER BY event_seq")
    for row in rows:
        plane = "routing" if row["plane"] == "work" else row["plane"]
        event_row = connection.execute("SELECT kind,payload_json FROM events WHERE seq=?",
                                       (row["event_seq"],)).fetchone()
        if event_row is None or event_row["kind"] != f"router.{row['outcome']}":
            raise ValueError(f"Router outcome event mismatch: {row['event_seq']}")
        event = json.loads(event_row["payload_json"])
        if not isinstance(event, dict) or event.get("plane") != plane:
            raise ValueError(f"Router outcome plane mismatch: {row['event_seq']}")
        receipt_id = _receipt_id(row, event, plane) if plane == "routing" else row["receipt_id"]
        connection.execute(
            "INSERT INTO router_outcomes(receipt_id,plane,request_id,router_index,buddy_id,phase,"
            "task_id,attempt_id,outcome,code,event_seq,recorded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (receipt_id, plane, row["request_id"], row["router_index"], row["buddy_id"], row["phase"],
             row["task_id"], row["attempt_id"], row["outcome"], row["code"], row["event_seq"],
             row["recorded_at"]),
        )
    connection.execute("DROP TABLE router_outcomes_saved")


def upgrade(state: Path) -> None:
    """Convert a stopped schema-25 board in place; reject every other schema."""
    path = Path(state) / DB_FILE
    if not path.is_file():
        raise FileNotFoundError(path)
    with sqlite3.connect(path, isolation_level=None, timeout=10) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("BEGIN IMMEDIATE")
        try:
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version is None or version["value"] != str(BASE_SCHEMA_VERSION):
                raise ValueError(f"Expected schema {BASE_SCHEMA_VERSION} for upgrade; found "
                                 f"{None if version is None else version['value']!r}")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Board integrity check failed before schema upgrade")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("Board foreign key check failed before schema upgrade")
            _convert_rows(connection)
            _rebuild_router_tables(connection)
            connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("Board foreign key check failed after schema upgrade")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Board integrity check failed after schema upgrade")
            connection.execute("COMMIT")
        except BaseException:
            connection.rollback()
            raise
