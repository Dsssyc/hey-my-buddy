"""Execution-side append-only display record; the protocol side owns its model."""
from __future__ import annotations

import os
from pathlib import Path

from ...json_codec import canonical_json
from ...private_dirs import attempt_root, ensure_private_dir, open_regular_fd
from ...protocol.session_content import SESSION_FORMAT, SessionNotification, SessionUpdate


class SessionRecord:
    def __init__(self, state: Path, harness: str, task_id: str, attempt_id: str):
        root = ensure_private_dir(attempt_root(state, harness, task_id, attempt_id))
        self.session_id = attempt_id
        self.path = root / "session.jsonl"
        fd = open_regular_fd(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        self._stream = os.fdopen(fd, "a", encoding="utf-8", newline="\n")
        try:
            if os.fstat(fd).st_size == 0:
                self._write(SESSION_FORMAT)
        except BaseException:
            self._stream.close()
            raise

    def _write(self, value) -> None:
        self._stream.write(canonical_json(value.model_dump(mode="json", by_alias=True, exclude_none=True)) + "\n")
        self._stream.flush()

    def append(self, update: SessionUpdate) -> None:
        self._write(SessionNotification(session_id=self.session_id, update=update))

    def close(self) -> None:
        self._stream.close()
