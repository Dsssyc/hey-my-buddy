"""Execution-side append-only SQLite display record."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from ...errors import BoardError
from ...json_codec import canonical_json
from ...private_dirs import ensure_private_dir, open_regular_fd
from ...protocol.session_content import SessionNotification, SessionUpdate


SESSION_RECORD_WRITE_FAILED = "session-record-write-failed"
# A summary run can reopen this path in the same execution process. Its first
# write failure remains authoritative for the attempt and disables that writer.
_FAILED_PATHS: dict[Path, dict] = {}


class SessionRecord:
    """One attempt's lazy writer; a failed write disables only this display record."""

    def __init__(self, path: Path, attempt_id: str):
        self.path = Path(path).absolute()
        self.session_id = attempt_id
        self._connection: sqlite3.Connection | None = None
        self.failure: dict | None = _FAILED_PATHS.get(self.path)

    def _open(self) -> sqlite3.Connection:
        ensure_private_dir(self.path.parent)
        fd = open_regular_fd(self.path, os.O_RDWR | os.O_CREAT)
        os.close(fd)
        connection = sqlite3.connect(self.path, timeout=60, isolation_level=None)
        self._connection = connection
        connection.execute("PRAGMA busy_timeout=60000")
        mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if mode != "wal":
            raise sqlite3.DatabaseError(f"Session record journal mode is {mode}, expected wal")
        connection.execute("PRAGMA synchronous=NORMAL")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            connection.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, notification TEXT NOT NULL)")
            connection.execute("PRAGMA user_version=1")
        elif version != 1:
            raise sqlite3.DatabaseError(f"Unsupported session record version: {version}")
        return connection

    def _failed(self, error: Exception, *, closing: sqlite3.Connection | None = None) -> None:
        if self.failure is None:
            self.failure = {"code": SESSION_RECORD_WRITE_FAILED,
                            "message": str(error), "cause": type(error).__name__}
            _FAILED_PATHS[self.path] = self.failure
        connection, self._connection = closing or self._connection, None
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error as close_error:
                self.failure["message"] += f"; connection close failed: {close_error}"

    def append(self, update: SessionUpdate) -> None:
        if self.failure is not None:
            return
        notification = SessionNotification(session_id=self.session_id, update=update)
        try:
            connection = self._connection or self._open()
            connection.execute("INSERT INTO events (notification) VALUES (?)", (
                canonical_json(notification.model_dump(mode="json", by_alias=True, exclude_none=True)),))
        except (OSError, BoardError, sqlite3.Error) as error:
            self._failed(error)

    def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error as error:
                self._failed(error, closing=connection)
