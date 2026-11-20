"""Publish complete private facts with one writer and one atomic name change.

Success survives writer-process interruption. POSIX also flushes the parent
directory; Windows does not claim power-loss durability for directory entries.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
from pathlib import Path

from .errors import BoardError
from .json_codec import canonical_json, decode_strict_json
from .paths import native_path


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


def publish_bytes(path: Path, content: bytes, *, immutable: bool = False) -> bool:
    """Flush the complete writer, then replace or compare-and-create its name.

    Immutable publication never replaces an existing object. Identical bytes
    may be reused; different bytes raise FileExistsError. Return whether this
    call installed the bytes.
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
    installed = True
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
                os.link(native_path(temporary), native_path(path))
            except FileExistsError:
                if read_bytes(path) != content:
                    raise
                installed = False
        elif os.name != 'nt':
            os.replace(temporary, path)
    finally:
        native_path(temporary).unlink(missing_ok=True)
    sync_directory(path.parent)
    return installed


def _snapshot_content_root(state: Path, namespace: str, owner: str, round: int | None) -> Path:
    from .private_dirs import linked, snapshot_root

    if namespace == "routing" and round is None:
        raise BoardError("INVALID_ARGUMENT", "Routing snapshot requires a frozen round")
    root = snapshot_root(state, namespace, owner, round)
    roots = (root.parent.parent, root.parent, root) if round is not None else (root.parent, root)
    if any(linked(path) for path in roots):
        raise BoardError("PRIVATE_PATH_UNSAFE", "Snapshot root must be an ordinary private directory")
    return root


def _snapshot_digest(reference: object) -> str:
    if (not isinstance(reference, dict) or set(reference) != {"sha256"}
            or not isinstance(reference["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", reference["sha256"]) is None):
        raise BoardError("INVALID_ARGUMENT", "Invalid private snapshot reference")
    return reference["sha256"]


def publish_snapshot(state: Path, namespace: str, owner: str, value: object, *,
                     round: int | None = None, before_write=None) -> tuple[dict, bool]:
    """Publish immutable content and identify whether this call created the file."""
    from .private_dirs import ensure_private_dir

    raw = canonical_json({"version": 1, "value": value}).encode()
    digest = hashlib.sha256(raw).hexdigest()
    reference = {"sha256": digest}
    if before_write is not None:
        before_write(reference)
    root = ensure_private_dir(_snapshot_content_root(state, namespace, owner, round))
    return reference, publish_bytes(root / (digest + ".json"), raw, immutable=True)


def read_snapshot(state: Path, namespace: str, owner: str, reference: object, *,
                  round: int | None = None):
    digest = _snapshot_digest(reference)
    root = _snapshot_content_root(state, namespace, owner, round)
    raw = read_bytes(root / (digest + ".json"))
    if hashlib.sha256(raw).hexdigest() != digest:
        raise BoardError("ARTIFACT_MISMATCH", "Private snapshot digest differs")
    envelope = decode_strict_json(raw)
    if (not isinstance(envelope, dict) or set(envelope) != {"version", "value"}
            or type(envelope["version"]) is not int or envelope["version"] != 1):
        raise BoardError("INVALID_ARGUMENT", "Invalid private snapshot form")
    return envelope["value"]


def discard_snapshot_reference(state: Path, namespace: str, owner: str, reference: dict, *,
                               round: int | None = None) -> None:
    from .private_dirs import linked, snapshot_root

    digest = _snapshot_digest(reference)
    root = (_snapshot_content_root(state, namespace, owner, round) if namespace == "routing"
            else snapshot_root(state, namespace, owner, round))
    path = root / (digest + ".json")
    if linked(path):
        raise BoardError("PRIVATE_PATH_UNSAFE", "Snapshot file must be an ordinary private file")
    native_path(path).unlink(missing_ok=True)
