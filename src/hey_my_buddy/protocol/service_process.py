"""Process creation policies for the service and its direct children."""
from __future__ import annotations

import os
import subprocess

from ..errors import BoardError

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000


def service_creation_flags(*, windows: bool | None = None) -> int:
    """Give console children of a Windows service no visible window."""
    if windows is None:
        windows = os.name == "nt"
    return CREATE_NO_WINDOW if windows else 0


def independent_service_popen(*args, **kwargs):
    """Start a service independently of the caller's terminal and outer Job."""
    if os.name == "nt":
        # The venv launcher must keep its real interpreter windowless too.
        kwargs["creationflags"] = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
    else:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(*args, **kwargs)
    except OSError as error:
        if os.name != "nt" or getattr(error, "winerror", None) != 5:
            raise
        raise BoardError("SERVICE_START_UNSUPPORTED",
                         f"Independent service start is not supported in this environment: {error}") from error
