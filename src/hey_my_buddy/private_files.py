"""Publish complete private facts with one writer and one atomic name change.

Success survives writer-process interruption. POSIX also flushes the parent
directory; Windows does not claim power-loss durability for directory entries.
Path policies remain with the caller until the shared handle boundary is added.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from .private_dirs import open_regular_fd


def sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class _PathOperations:
    def __init__(self, directory_fd, validate):
        self.directory_fd = directory_fd
        self.validate = validate

    def _path(self, path):
        if self.validate is not None:
            self.validate(path)
        return path.name if self.directory_fd is not None else path

    def open(self, path):
        target = self._path(path)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if self.directory_fd is None:
            return open_regular_fd(target, flags)
        return os.open(target, flags | os.O_NOFOLLOW, 0o600, dir_fd=self.directory_fd)

    def read(self, path):
        target = self._path(path)
        descriptor = (open_regular_fd(target, os.O_RDONLY) if self.directory_fd is None else
                      os.open(target, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.directory_fd))
        with os.fdopen(descriptor, "rb") as stream:
            return stream.read()

    def link(self, source, target):
        os.link(self._path(source), self._path(target),
                src_dir_fd=self.directory_fd, dst_dir_fd=self.directory_fd)

    def replace(self, source, target):
        os.replace(self._path(source), self._path(target),
                   src_dir_fd=self.directory_fd, dst_dir_fd=self.directory_fd)

    def unlink(self, path):
        try:
            os.unlink(self._path(path), dir_fd=self.directory_fd)
        except FileNotFoundError:
            pass


def publish_bytes(path: Path, content: bytes, *, immutable: bool = False,
                  operations=None, directory_fd: int | None = None, validate=None) -> None:
    """Flush the complete writer, then replace or compare-and-create its name.

    Immutable publication never replaces an existing object. Identical bytes
    may be reused; different bytes raise FileExistsError. Callers with an
    existing held-path policy supply its operations or its POSIX directory fd.
    """
    path = Path(path)
    operations = operations if operations is not None else _PathOperations(directory_fd, validate)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = operations.open(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            try:
                operations.link(temporary, path)
            except FileExistsError:
                if operations.read(path) != content:
                    raise
        else:
            operations.replace(temporary, path)
        if directory_fd is not None:
            os.fsync(directory_fd)
        else:
            sync_directory(path.parent)
    finally:
        operations.unlink(temporary)
