"""Process creation policies for the service and its direct children."""
from __future__ import annotations

import os
import subprocess

from ..errors import BoardError

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000


def service_creation_flags(*, windows: bool | None = None) -> int:
    """Give console children of a detached Windows service no visible window."""
    if windows is None:
        windows = os.name == "nt"
    return CREATE_NO_WINDOW if windows else 0


def independent_service_popen(*args, **kwargs):
    """Start a service independently of the caller's terminal and outer Job."""
    if os.name == "nt":
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
    else:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(*args, **kwargs)
    except OSError as error:
        raise BoardError("SERVICE_START_UNSUPPORTED", f"当前环境不支持独立启动: {error}") from error
