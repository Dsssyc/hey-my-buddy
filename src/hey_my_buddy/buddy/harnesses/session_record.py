"""Execution-side append-only JSONL display record."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from ...json_codec import canonical_json
from ...private_dirs import ensure_private_dir, open_regular_fd
from ...protocol.reasons import reason
from ...protocol.session_content import SESSION_FORMAT, SessionNotification, SessionUpdate
from .run_contract import SessionRecordFailure


class SessionRecord:
    """One attempt's lazy writer; a failed write disables only this record."""

    def __init__(self, path: Path, attempt_id: str, *, failure: dict | None = None):
        self.path = Path(path).absolute()
        self.session_id = attempt_id
        self._fd: int | None = None
        self.failure = failure

    def project(self, method, *args, **kwargs):
        """Isolate display projection from the native facts it observes."""
        if self.failure is not None:
            return None
        try:
            return method(*args, **kwargs)
        except Exception as error:
            self._failed(error)
            return None

    @contextmanager
    def protect(self):
        """Carry the display reason if the native run itself raises."""
        try:
            yield
        except Exception as error:
            self.project(self.close)
            if self.failure is not None:
                error.session_record_failure = SessionRecordFailure(**self.failure)
            raise

    @staticmethod
    def _line(value) -> bytes:
        return (canonical_json(value.model_dump(mode="json", by_alias=True,
                                                exclude_none=True)) + "\n").encode("utf-8")

    @staticmethod
    def _write(fd: int, line: bytes) -> None:
        while line:
            written = os.write(fd, line)
            if written == 0:
                raise OSError("Session record write made no progress")
            line = line[written:]

    def _open(self) -> int:
        ensure_private_dir(self.path.parent)
        fd = open_regular_fd(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND)
        self._fd = fd
        # Trim an interrupted final line on the same descriptor used to append.
        size = os.fstat(fd).st_size
        end = size
        complete = 0
        while end > 0:
            start = max(0, end - 8192)
            os.lseek(fd, start, os.SEEK_SET)
            chunk = os.read(fd, end - start)
            newline = chunk.rfind(b"\n")
            if newline >= 0:
                complete = start + newline + 1
                break
            end = start
        if complete != size:
            os.ftruncate(fd, complete)
        if complete == 0:
            self._write(fd, self._line(SESSION_FORMAT))
        return fd

    def _failed(self, error: Exception) -> None:
        if self.failure is None:
            encoded = str(error).encode("utf-8", errors="backslashreplace")
            category = type(error).__name__.encode("utf-8", errors="backslashreplace")
            self.failure = reason("session-record-write-failed",
                                  category=category[:128].decode("utf-8", errors="ignore"),
                                  message=encoded[:8192].decode("utf-8", errors="ignore"),
                                  truncated=len(encoded) > 8192)
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)

    def append(self, update: SessionUpdate) -> None:
        if self.failure is not None:
            return
        try:
            notification = SessionNotification(session_id=self.session_id, update=update)
            self._write(self._fd if self._fd is not None else self._open(), self._line(notification))
        except Exception as error:
            self._failed(error)

    def close(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)
            except Exception as error:
                self._failed(error)
