"""The one default location for hey-my-buddy data (state, runtimes, backups).

POSIX keeps the XDG-style ``~/.local/share/hey-my-buddy``. Windows uses
``%LOCALAPPDATA%\\hey-my-buddy``: machine-local, per-user and not roamed. The data
root is deliberately outside the shared skill directory, which is replaced on every
install and is readable by agents. ``BUDDY_STATE_DIR`` and ``BUDDY_RUNTIME_ROOT``
still override the individual locations.
"""
from __future__ import annotations

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
