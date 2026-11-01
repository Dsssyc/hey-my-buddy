"""Content-addressed private files for maintenance input, answers and feedback.

Only the digest crosses process boundaries. The file is bound to one
maintenance run; neither the number of domains nor the task descriptions impose
a limit on the size of an IPC message.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from ..errors import BoardError
from ..json_codec import canonical_json, decode_strict_json
from ..private_dirs import ensure_private_dir, linked, remove_tree
from ..paths import native_path
from ..private_files import publish_bytes, read_bytes


def _root(state: Path, run_id: str) -> Path:
    root = Path(state) / "maintenance"
    return root / hashlib.sha256(run_id.encode()).hexdigest()


def _content_root(state: Path, run_id: str) -> Path:
    run = _root(state, run_id)
    root = run.parent
    if linked(root) or linked(run):
        raise BoardError("PRIVATE_PATH_UNSAFE", "Maintenance root must be an ordinary private directory")
    return run


def publish(state: Path, run_id: str, value: object, *, before_write=None) -> dict:
    raw = canonical_json({"version": 1, "value": value}).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    reference = {"sha256": digest}
    if before_write is not None:
        before_write(reference)
    path = ensure_private_dir(_content_root(state, run_id)) / (digest + ".json")
    publish_bytes(path, raw, immutable=True)
    return reference


def read(state: Path, run_id: str, reference: object):
    if (not isinstance(reference, dict) or set(reference) != {"sha256"}
            or not isinstance(reference["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", reference["sha256"]) is None):
        raise BoardError("INVALID_ARGUMENT", "Invalid maintenance file reference")
    expected = _content_root(state, run_id) / (reference["sha256"] + ".json")
    raw = read_bytes(expected)
    if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise BoardError("ARTIFACT_MISMATCH", "Maintenance file digest differs")
    envelope = decode_strict_json(raw)
    if not isinstance(envelope, dict) or set(envelope) != {"version", "value"} or type(envelope["version"]) is not int or envelope["version"] != 1:
        raise BoardError("INVALID_ARGUMENT", "Invalid maintenance file form")
    return envelope["value"]


def discard(state: Path, run_id: str) -> None:
    """Delete a finished run's transient material; never create its directory."""
    remove_tree(_root(state, run_id))


def discard_reference(state: Path, run_id: str, reference: dict) -> None:
    path = _root(state, run_id) / (reference["sha256"] + ".json")
    if linked(path):
        raise BoardError("PRIVATE_PATH_UNSAFE", "Maintenance file must be an ordinary private file")
    native_path(path).unlink(missing_ok=True)


def discard_orphans(state: Path, active_run_ids) -> None:
    """Reconcile material left by a service that died before its transaction hooks."""
    root = Path(state) / "maintenance"
    if linked(root):
        raise BoardError("PRIVATE_PATH_UNSAFE", "Maintenance root must be an ordinary private directory")
    active = {hashlib.sha256(run_id.encode()).hexdigest() for run_id in active_run_ids}
    for path in native_path(root).glob("*"):
        if path.name not in active:
            remove_tree(path)
