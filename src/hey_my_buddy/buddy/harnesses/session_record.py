"""Execution-side append-only display record; the protocol side owns its model."""
from __future__ import annotations

import os
from pathlib import Path

from ...errors import BoardError
from ...json_codec import canonical_json
from ...private_dirs import attempt_root, ensure_private_dir, open_regular_fd
from ...protocol.session_content import SESSION_FORMAT, SessionNotification, SessionUpdate


SESSION_RECORD_WRITE_FAILED = "session-record-write-failed"


class SessionRecordWriteError(Exception):
    """A private record open, append, flush or close failed, with no source path."""

    def __init__(self):
        super().__init__("Could not write the attempt session record")


class SessionRecord:
    def __init__(self, state: Path, harness: str, task_id: str, attempt_id: str):
        root = attempt_root(state, harness, task_id, attempt_id)
        self.session_id = attempt_id
        self.path = root / "session.jsonl"
        self._stream = None

    def _write(self, value) -> None:
        self._stream.write(canonical_json(value.model_dump(mode="json", by_alias=True, exclude_none=True)) + "\n")
        self._stream.flush()

    def append(self, update: SessionUpdate) -> None:
        notification = SessionNotification(session_id=self.session_id, update=update)
        try:
            if self._stream is None:
                ensure_private_dir(self.path.parent)
                fd = open_regular_fd(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
                self._stream = os.fdopen(fd, "a", encoding="utf-8", newline="\n")
                if os.fstat(fd).st_size == 0:
                    self._write(SESSION_FORMAT)
            self._write(notification)
        except (OSError, BoardError) as error:
            raise SessionRecordWriteError() from error

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            except OSError as error:
                raise SessionRecordWriteError() from error
