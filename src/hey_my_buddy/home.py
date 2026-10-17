"""The one default location for hey-my-buddy data (state, runtimes, backups).

POSIX keeps the XDG-style ``~/.local/share/hey-my-buddy``. Windows uses
``%LOCALAPPDATA%\\hey-my-buddy``: machine-local, per-user and not roamed. The data
root is deliberately outside the shared skill directory, which is replaced on every
install and is readable by agents. ``HMB_STATE_DIR`` and ``HMB_RUNTIME_ROOT``
still override the individual locations.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


def data_root() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        return Path(base) / "hey-my-buddy" if base else Path.home() / "AppData" / "Local" / "hey-my-buddy"
    return Path.home() / ".local" / "share" / "hey-my-buddy"


def default_state_dir() -> Path:
    return data_root() / "state"


def default_runtime_root() -> Path:
    return data_root() / "runtime"


def control_file_path(state_dir: Path, run_id: str, generation: int) -> Path:
    """The location of the CLI's saved control capability; no file is read here."""
    return state_dir / "controls" / f"{run_id}.g{generation}.json"


def submission_file_path(state_dir: Path, request_id: str) -> Path:
    """The CLI submission receipt path derived from its durable request identity."""
    name = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:32]
    return state_dir / "submissions" / f"{name}.json"
