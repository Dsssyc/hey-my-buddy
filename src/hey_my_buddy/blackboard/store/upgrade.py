"""The upgrade-floor-to-current chain, run only by the stopped-board installer."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from ...errors import BoardError
from . import schema_30_31, schema_31_32, schema_32_33
from .db import SCHEMA_VERSION

UPGRADE_FLOOR_SCHEMA = 30
UPGRADE_FLOOR_RELEASE = "0.54.1"
STEPS = {30: schema_30_31.upgrade, 31: schema_31_32.upgrade, 32: schema_32_33.upgrade}


def supported_schema(value: str | None) -> int:
    """Validate a board's stored marker at the installation boundary."""
    if value not in {str(version) for version in range(UPGRADE_FLOOR_SCHEMA, SCHEMA_VERSION + 1)}:
        raise BoardError("UNSUPPORTED_SCHEMA",
                         f"Upgrade requires schema {UPGRADE_FLOOR_SCHEMA} through {SCHEMA_VERSION}; "
                         f"for a board below the upgrade floor, install release {UPGRADE_FLOOR_RELEASE} first")
    return int(value)


def upgrade_state(state: Path) -> dict:
    """Run every required step in one transaction, preserving the source on failure."""
    with closing(sqlite3.connect(Path(state) / "board.sqlite3")) as connection:
        # Steps require sqlite3.Row: the frozen 30 → 31 step reads columns by name.
        connection.row_factory = sqlite3.Row
        # SQLite's rebuild sequence disables foreign keys before BEGIN; the
        # complete chain is checked before committing and enforcement resumes.
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            source = supported_schema(version[0] if version is not None else None)
            if source == SCHEMA_VERSION:
                raise BoardError("UNSUPPORTED_SCHEMA", f"The board already has schema {SCHEMA_VERSION}")
            for version in range(source, SCHEMA_VERSION):
                STEPS[version](connection)
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise BoardError("UPGRADE_INVALID_BOARD", "The upgraded board failed its foreign key check")
            if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
                raise BoardError("UPGRADE_INVALID_BOARD", "The upgraded board failed its integrity check")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")
    return {"sourceSchema": source, "targetSchema": SCHEMA_VERSION}
