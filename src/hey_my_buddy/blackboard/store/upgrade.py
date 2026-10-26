"""The pending schema 29 → 30 upgrade, run only by the stopped-board installer."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

from ...errors import BoardError
from ...protocol.usage import normalize_token_usage
from .db import SCHEMA, SCHEMA_VERSION, canonical_json

UPGRADE_BASE_SCHEMA = 29
REMOVED_ATTEMPT_COLUMNS = ("token_usage_json", "exit_code", "signal", "log_paths", "error")


def _result(value):
    if not isinstance(value, dict) or "quotaFailure" not in value:
        return
    value["providerRejection"] = value.pop("quotaFailure")


def _invalid_board(table, row, field):
    raise BoardError("UPGRADE_INVALID_BOARD", "Stored facts do not match the supported upgrade form",
                     table=table, row=row, field=field)


def _routing(value, decision_id):
    """Unify the pre-cooling and cooling forms retained by the upgrade base."""
    basis = value.get("routingBasis") if isinstance(value, dict) else None
    if not isinstance(basis, dict):
        _invalid_board("decision_requests", decision_id, "routingBasis")
    required = {"candidateCount", "excludedCount", "excludedBuddies"}
    cooling = {"coolingCount", "coolingBuddies"}
    missing = required - basis.keys()
    unexpected = basis.keys() - required - cooling
    if missing or unexpected:
        _invalid_board("decision_requests", decision_id, "routingBasis." + sorted(missing or unexpected)[0])
    present = cooling & basis.keys()
    if present and present != cooling:
        _invalid_board("decision_requests", decision_id, "routingBasis." + next(iter(cooling - present)))
    if type(basis["candidateCount"]) is not int or basis["candidateCount"] < 0:
        _invalid_board("decision_requests", decision_id, "routingBasis.candidateCount")
    counts = [("excludedCount", "excludedBuddies")]
    if present:
        counts.append(("coolingCount", "coolingBuddies"))
    for count, items in counts:
        if not isinstance(basis[items], list):
            _invalid_board("decision_requests", decision_id, "routingBasis." + items)
        if type(basis[count]) is not int or basis[count] != len(basis[items]):
            _invalid_board("decision_requests", decision_id, "routingBasis." + count)
        del basis[count]
    if not present:
        basis["coolingBuddies"] = []
    basis["overloadedModelServices"] = []


def upgrade_schema_29_to_30(connection):
    """Convert retained database projections; sealed Router snapshots stay as written."""
    changed = {}
    converted = 0
    rows = connection.execute(
        "SELECT attempt_id,result_json,token_usage_json,exit_code,signal,log_paths,error FROM attempts").fetchall()
    for attempt_id, raw, usage, exit_code, signal, log_paths, error in rows:
        value = json.loads(raw) if raw is not None else {}
        original = json.loads(raw) if raw is not None else {}
        for field, stored, retained in (
                ("exit_code", exit_code, value.get("exitCode")),
                ("signal", signal, value.get("signal")),
                ("log_paths", (json.loads(log_paths) if log_paths is not None else None) or {}, value.get("logPaths") or {}),
                ("error", error, value.get("error"))):
            if stored != retained:
                _invalid_board("attempts", attempt_id, field)
        result = value.get("result")
        usage = json.loads(usage) if usage is not None else None
        if isinstance(result, dict) and "tokenUsage" in result:
            if normalize_token_usage(result["tokenUsage"]) != usage:
                _invalid_board("attempts", attempt_id, "token_usage_json")
            result["tokenUsage"] = usage
        elif usage is not None:
            if not isinstance(result, dict):
                _invalid_board("attempts", attempt_id, "token_usage_json")
            result["tokenUsage"] = usage
        _result(result)
        if value != original:
            connection.execute("UPDATE attempts SET result_json=? WHERE attempt_id=?", (canonical_json(value), attempt_id))
            converted += 1
    changed["attempts.result_json"] = converted
    converted = 0
    rows = connection.execute("SELECT decision_id,requested_json FROM decision_requests").fetchall()
    for decision_id, raw in rows:
        value = json.loads(raw)
        original = json.loads(raw)
        _routing(value, decision_id)
        if value != original:
            connection.execute("UPDATE decision_requests SET requested_json=? WHERE decision_id=?", (canonical_json(value), decision_id))
            converted += 1
    changed["decision_requests.requested_json"] = converted
    # Recreate from the current DDL so constraints and sqlite_master SQL match a
    # fresh board exactly. Incoming foreign keys keep the original table name.
    connection.execute("CREATE TEMP TABLE hmb_attempts_upgrade AS SELECT rowid AS saved_rowid,* FROM attempts")
    connection.execute("DROP TABLE attempts")
    ddl = re.search(r"CREATE TABLE IF NOT EXISTS attempts \(.*?\n\);", SCHEMA, re.S)[0]
    connection.execute(ddl)
    columns = [row[1] for row in connection.execute("PRAGMA table_info(attempts)")]
    names = ",".join('"' + column + '"' for column in columns)
    connection.execute(f"INSERT INTO attempts(rowid,{names}) SELECT saved_rowid,{names} FROM hmb_attempts_upgrade")
    connection.execute("DROP TABLE hmb_attempts_upgrade")
    for ddl in re.findall(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS .*?;", SCHEMA, re.S):
        if re.search(r"\bON attempts\b", ddl):
            connection.execute(ddl)
    return changed


def upgrade_state(state: Path) -> dict:
    """Explicit, atomic upgrade of an idle private/installed board under owner locks."""
    with closing(sqlite3.connect(Path(state) / "board.sqlite3")) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version is None or version[0] != str(UPGRADE_BASE_SCHEMA):
                raise BoardError("UNSUPPORTED_SCHEMA", f"Pending upgrade requires schema {UPGRADE_BASE_SCHEMA}")
            changed = upgrade_schema_29_to_30(connection)
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or connection.execute("PRAGMA foreign_key_check").fetchall():
                raise BoardError("UPGRADE_INVALID_BOARD", "The upgraded board failed integrity checks")
            connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return {"sourceSchema": UPGRADE_BASE_SCHEMA, "targetSchema": SCHEMA_VERSION,
            "converted": changed, "removedAttemptColumns": list(REMOVED_ATTEMPT_COLUMNS)}
