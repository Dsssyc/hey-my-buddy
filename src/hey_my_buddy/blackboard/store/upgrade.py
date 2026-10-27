"""The pending schema 30 → 31 upgrade, run only by the stopped-board installer."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from ...errors import BoardError
from .db import DOMAIN_EVALUATION_SCHEMA, ROUTER_MAINTENANCE_COLUMNS, SCHEMA_VERSION

UPGRADE_BASE_SCHEMA = 30


def upgrade_state(state: Path) -> dict:
    """Add domain evaluation storage atomically without rewriting existing facts."""
    with closing(sqlite3.connect(Path(state) / "board.sqlite3")) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version is None or version[0] != str(UPGRADE_BASE_SCHEMA):
                raise BoardError("UNSUPPORTED_SCHEMA", f"Pending upgrade requires schema {UPGRADE_BASE_SCHEMA}")
            for ddl in DOMAIN_EVALUATION_SCHEMA.split(";"):
                if ddl.strip():
                    connection.execute(ddl)
            for column in ROUTER_MAINTENANCE_COLUMNS:
                connection.execute("ALTER TABLE router_settings ADD COLUMN " + column)
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or connection.execute("PRAGMA foreign_key_check").fetchall():
                raise BoardError("UPGRADE_INVALID_BOARD", "The upgraded board failed integrity checks")
            connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return {"sourceSchema": UPGRADE_BASE_SCHEMA, "targetSchema": SCHEMA_VERSION}
