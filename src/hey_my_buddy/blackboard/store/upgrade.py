"""The pending schema 29 → 30 upgrade, run only by the stopped-board installer."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

from ...errors import BoardError
from .db import SCHEMA, SCHEMA_VERSION, canonical_json

UPGRADE_BASE_SCHEMA = 29
REMOVED_ATTEMPT_COLUMNS = ("token_usage_json", "exit_code", "signal", "log_paths")


def _result(value):
    if not isinstance(value, dict) or "quotaFailure" not in value:
        return
    rejection = value.pop("quotaFailure")
    if isinstance(rejection, dict) and rejection.get("code") == "model-overloaded":
        rejection["code"] = "overloaded"
    value["providerRejection"] = rejection


def _routing(value):
    """Move known overload facts; never infer a historical user preference."""
    if not isinstance(value, dict):
        return
    overloads = value.pop("excludedModelServices", [])
    basis = value.get("routingBasis")
    if basis is None:
        if not overloads:
            return
        basis = value["routingBasis"] = {}
    services = {}
    for item in overloads:
        identity = tuple(item[key] for key in ("harness", "provider", "model"))
        services.setdefault(identity, {key: item[key] for key in ("harness", "provider", "model", "nativeCode", "turnId")})
    user_excluded = []
    for item in basis.get("excludedBuddies", []):
        if item.get("reason") == "model-overloaded":
            identity = tuple(item[key] for key in ("harness", "provider", "model"))
            services.setdefault(identity, {key: item[key] for key in ("harness", "provider", "model", "nativeCode", "turnId")})
        else:
            user_excluded.append(item)
    basis["excludedBuddies"] = user_excluded
    basis["overloadedModelServices"] = list(services.values())
    basis.pop("excludedCount", None)
    basis.pop("coolingCount", None)


def _snapshot(value):
    _routing(value)
    if isinstance(value, dict) and isinstance(value.get("baseInput"), dict):
        _snapshot(value["baseInput"])


def _convert_json(connection, table, column, transform):
    changed = 0
    for rowid, raw in connection.execute(f'SELECT rowid,"{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'):
        value = json.loads(raw)
        transform(value)
        converted = canonical_json(value)
        if value != json.loads(raw):
            connection.execute(f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?', (converted, rowid))
            changed += 1
    return changed


def upgrade_schema_29_to_30(connection):
    """Convert mutable database projections; digest-bound files are never opened."""
    changed = {}
    converted = 0
    for rowid, raw, usage in connection.execute(
            "SELECT rowid,result_json,token_usage_json FROM attempts WHERE result_json IS NOT NULL"):
        value = json.loads(raw)
        original = json.loads(raw)
        result = value.get("result")
        _result(result)
        if usage is not None or isinstance(result, dict) and "tokenUsage" in result:
            result["tokenUsage"] = json.loads(usage) if usage is not None else None
        if value != original:
            connection.execute("UPDATE attempts SET result_json=? WHERE rowid=?", (canonical_json(value), rowid))
            converted += 1
    changed["attempts.result_json"] = converted
    changed["decision_requests.requested_json"] = _convert_json(connection, "decision_requests", "requested_json", _routing)
    changed["decision_requests.output_json"] = _convert_json(connection, "decision_requests", "output_json", _result)
    changed["router_requests.snapshot_json"] = _convert_json(connection, "router_requests", "snapshot_json", _snapshot)
    event_count = 0
    for seq, raw in connection.execute("SELECT seq,payload_json FROM events WHERE kind='workflow.auto_continued'"):
        value = json.loads(raw)
        if value.get("reason") == "model-overloaded":
            value["reason"] = "overloaded"
            connection.execute("UPDATE events SET payload_json=? WHERE seq=?", (canonical_json(value), seq))
            event_count += 1
    changed["events.payload_json"] = event_count
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
