"""Content-addressed private files for maintenance input, answers and feedback.

Only the location and digest cross process boundaries. The file is bound to one
maintenance run; neither the number of domains nor the task descriptions impose
a limit on the size of an IPC message.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from ..errors import BoardError
from ..json_codec import canonical_json, decode_strict_json
from ..private_dirs import ensure_private_dir, linked
from ..private_files import publish_bytes, read_bytes


def _root(state: Path, run_id: str) -> Path:
    root = Path(state) / "maintenance"
    run = root / hashlib.sha256(run_id.encode()).hexdigest()
    if linked(root) or linked(run):
        raise BoardError("PRIVATE_PATH_UNSAFE", "Maintenance root must be an ordinary private directory")
    return ensure_private_dir(run)


def publish(state: Path, run_id: str, value: object) -> dict:
    raw = canonical_json({"version": 1, "value": value}).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    path = _root(state, run_id) / (digest + ".json")
    publish_bytes(path, raw, immutable=True)
    return {"location": str(path), "sha256": digest}


def read(state: Path, run_id: str, reference: object):
    if (not isinstance(reference, dict) or set(reference) != {"location", "sha256"}
            or not isinstance(reference["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", reference["sha256"]) is None):
        raise BoardError("INVALID_ARGUMENT", "Invalid maintenance file reference")
    expected = _root(state, run_id) / (reference["sha256"] + ".json")
    if reference["location"] != str(expected):
        raise BoardError("PRIVATE_PATH_UNSAFE", "Maintenance file belongs to a different run")
    raw = read_bytes(expected)
    if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise BoardError("ARTIFACT_MISMATCH", "Maintenance file digest differs")
    envelope = decode_strict_json(raw)
    if not isinstance(envelope, dict) or set(envelope) != {"version", "value"} or type(envelope["version"]) is not int or envelope["version"] != 1:
        raise BoardError("INVALID_ARGUMENT", "Invalid maintenance file form")
    return envelope["value"]
