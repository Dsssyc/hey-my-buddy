"""Restart-stable directory identity for managed workspaces."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def stable_identity(path) -> str:
    directory = Path(path).resolve(strict=True)
    payload = json.dumps([str(directory), directory.stat().st_ino],
                         sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(payload).hexdigest()
