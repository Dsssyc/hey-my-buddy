"""Execution-side append-only JSONL display record."""
from __future__ import annotations

import os
from pathlib import Path

from ...errors import BoardError
from ...json_codec import canonical_json
from ...private_dirs import ensure_private_dir, open_regular_fd
from ...protocol.session_content import SESSION_FORMAT, SessionNotification, SessionUpdate


SESSION_RECORD_WRITE_FAILED = "session-record-write-failed"
# A summary run can reopen the same attempt in this process. Its first failure
# remains authoritative, and later writers for that path stay disabled.
_FAILED_PATHS: dict[Path, dict] = {}


class SessionRecord:
    """One attempt's lazy writer; a failed write disables only this record."""

    def __init__(self, path: Path, attempt_id: str):
        self.path = Path(path).absolute()
        self.session_id = attempt_id
        self._fd: int | None = None
        self.failure: dict | None = _FAILED_PATHS.get(self.path)

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
        fd = open_regular_fd(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        self._fd = fd
        # Bind a protected reader to the write-only inode, then trim an
        # interrupted final line on the pinned writer fd before appending.
        read_fd = open_regular_fd(self.path, os.O_RDONLY)
        try:
            writer_stat, reader_stat = os.fstat(fd), os.fstat(read_fd)
            if (writer_stat.st_dev, writer_stat.st_ino) != (reader_stat.st_dev, reader_stat.st_ino):
                raise BoardError("PRIVATE_PATH_UNSAFE", "Session record changed while opening")
            end = writer_stat.st_size
            complete = 0
            while end > 0:
                start = max(0, end - 8192)
                os.lseek(read_fd, start, os.SEEK_SET)
                chunk = os.read(read_fd, end - start)
                newline = chunk.rfind(b"\n")
                if newline >= 0:
                    complete = start + newline + 1
                    break
                end = start
            if complete != writer_stat.st_size:
                os.ftruncate(fd, complete)
        finally:
            os.close(read_fd)
        if os.fstat(fd).st_size == 0:
            self._write(fd, self._line(SESSION_FORMAT))
        return fd

    def _failed(self, error: Exception) -> None:
        if self.failure is None:
            self.failure = {"code": SESSION_RECORD_WRITE_FAILED,
                            "message": str(error), "cause": type(error).__name__}
            _FAILED_PATHS[self.path] = self.failure
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError as close_error:
                self.failure["message"] += f"; close failed: {close_error}"

    def append(self, update: SessionUpdate) -> None:
        if self.failure is not None:
            return
        notification = SessionNotification(session_id=self.session_id, update=update)
        try:
            self._write(self._fd if self._fd is not None else self._open(), self._line(notification))
        except (OSError, BoardError) as error:
            self._failed(error)

    def close(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError as error:
                self._failed(error)
