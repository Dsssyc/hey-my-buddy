"""Read the execution-side display database in harness tests."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def notifications(path: Path) -> list[dict]:
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        rows = connection.execute("SELECT seq, notification FROM events ORDER BY seq").fetchall()
    assert [seq for seq, _ in rows] == list(range(1, len(rows) + 1))
    return [json.loads(value) for _, value in rows]


def updates(path: Path) -> list[dict]:
    return [item["update"] for item in notifications(path)]
