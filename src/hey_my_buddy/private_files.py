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


def open_read(path: Path):
    """Open an ordinary leaf with delete sharing on Windows."""
    from .private_dirs import open_regular_fd
    return os.fdopen(open_regular_fd(path, os.O_RDONLY), 'rb')


def read_bytes(path: Path) -> bytes:
    with open_read(path) as stream:
        return stream.read()


def read_text(path: Path) -> str:
    """Keep UTF-8 text readers' universal-newline behavior on both platforms."""
    with io.TextIOWrapper(open_read(path), encoding='utf-8') as stream:
        return stream.read()


def publish_bytes(path: Path, content: bytes, *, immutable: bool = False) -> None:
    """Flush the complete writer, then replace or compare-and-create its name.

    Immutable publication never replaces an existing object. Identical bytes
    may be reused; different bytes raise FileExistsError.
    """
    from .private_dirs import _absolute, open_regular_fd
    path = _absolute(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if os.name == 'nt':
        from . import windows_files
        api = windows_files.WindowsFileAPI()
        descriptor = windows_files.open_regular_fd(temporary, flags, delete=True, api=api)
    else:
        descriptor = open_regular_fd(temporary, flags)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            if not immutable and os.name == 'nt':
                api.rename(stream.fileno(), path)
        if immutable:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if read_bytes(path) != content:
                    raise
        elif os.name != 'nt':
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    sync_directory(path.parent)
