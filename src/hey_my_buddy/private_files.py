"""Publish complete private facts with one writer and one atomic name change.

Success survives writer-process interruption. POSIX also flushes the parent
directory; Windows does not claim power-loss durability for directory entries.
"""
from __future__ import annotations

import os
import io
import uuid
from pathlib import Path


def sync_directory(path: Path, *, follow_symlinks: bool = True) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | (0 if follow_symlinks else os.O_NOFOLLOW))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class _PathOperations:
    def __init__(self, directory_fd, validate, open_file):
        self.directory_fd = directory_fd
        self.validate = validate
        self.has_opener = open_file is not None
        self.open_file = open_file if open_file is not None else os.open

    def _path(self, path, *, check=True):
        if check and self.validate is not None:
            self.validate(path)
        return path.name if self.directory_fd is not None else path

    def open(self, path):
        target = self._path(path, check=not self.has_opener)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if self.directory_fd is not None:
            flags |= os.O_NOFOLLOW
        return self.open_file(target, flags, 0o600,
                              **({"dir_fd": self.directory_fd} if self.directory_fd is not None else {}))

    def read(self, path):
        target = self._path(path, check=not self.has_opener)
        flags = os.O_RDONLY | (os.O_NOFOLLOW if self.directory_fd is not None else 0)
        descriptor = self.open_file(target, flags, 0o600,
                                    **({"dir_fd": self.directory_fd} if self.directory_fd is not None else {}))
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


class _WindowsPathOperations(_PathOperations):
    def __init__(self, api):
        from . import windows_files
        super().__init__(None, None, lambda path, flags, mode:
                         windows_files.open_regular_fd(path, flags, api=api,
                                                       delete=bool(flags & os.O_EXCL)))
        self.api = api

    def replace_open(self, descriptor, target):
        self.api.rename(descriptor, target)


def open_read(path: Path):
    """Open a published reader with delete sharing on Windows."""
    if os.name != 'nt':
        return Path(path).open('rb')
    from . import windows_files
    return os.fdopen(windows_files.open_regular_fd(path, os.O_RDONLY), 'rb')


def read_bytes(path: Path) -> bytes:
    with open_read(path) as stream:
        return stream.read()


def read_text(path: Path) -> str:
    """Keep UTF-8 text readers' universal-newline behavior on both platforms."""
    with io.TextIOWrapper(open_read(path), encoding='utf-8') as stream:
        return stream.read()


def publish_bytes(path: Path, content: bytes, *, immutable: bool = False,
                  operations=None, directory_fd: int | None = None, validate=None,
                  open_file=None, directory_nofollow: bool = False) -> None:
    """Flush the complete writer, then replace or compare-and-create its name.

    Immutable publication never replaces an existing object. Identical bytes
    may be reused; different bytes raise FileExistsError. Callers with an
    existing path policy supply its opener, operations or POSIX directory fd.
    """
    path = Path(path)
    if os.name == "nt" and operations is None and directory_fd is None:
        from . import windows_files
        operations = _WindowsPathOperations(windows_files.WindowsFileAPI())
    operations = operations if operations is not None else _PathOperations(directory_fd, validate, open_file)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = operations.open(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            if not immutable and os.name == 'nt':
                operations.replace_open(stream.fileno(), path)
        if immutable:
            try:
                operations.link(temporary, path)
            except FileExistsError:
                if operations.read(path) != content:
                    raise
        elif os.name != 'nt':
            operations.replace(temporary, path)
    finally:
        operations.unlink(temporary)
    if directory_fd is not None:
        os.fsync(directory_fd)
    else:
        sync_directory(path.parent, follow_symlinks=not directory_nofollow)
