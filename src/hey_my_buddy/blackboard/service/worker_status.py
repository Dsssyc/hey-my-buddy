"""Read the supervisor's published facts without changing its state file."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..tasks.scheduling import valid_worker_id


def supervisor_status(directory: Path, worker_id: str) -> dict:
    """Unknown is a failed observation, never an inferred healthy process.

    These are file observations, not process-liveness evidence. The error, when
    present, was published with updatedAt; a later publication replaces it.
    """
    unknown = {"state": "unknown", "lastError": None, "lastErrorAt": None,
               "startedAt": None, "updatedAt": None}
    if valid_worker_id(worker_id) is None:
        return unknown
    try:
        value = json.loads((directory / "workers" / worker_id / "supervisor.json").read_text())
        if not isinstance(value, dict) or value.get("workerId") != worker_id:
            return unknown
        state = value.get("state")
        if state not in ("starting", "running", "restarting", "retiring", "retired", "stopped"):
            return unknown
        for key in ("startedAt", "updatedAt"):
            stamp = value.get(key)
            if not isinstance(stamp, str) or datetime.fromisoformat(stamp.replace("Z", "+00:00")).tzinfo is None:
                return unknown
        error = value.get("lastError")
        if error is not None and (not isinstance(error, str) or not error):
            return unknown
        if state == "restarting" and error is None:
            return unknown
    except (OSError, ValueError):
        return unknown
    return {"state": state, "lastError": error,
            "lastErrorAt": value["updatedAt"] if error is not None else None,
            "startedAt": value["startedAt"], "updatedAt": value["updatedAt"]}
