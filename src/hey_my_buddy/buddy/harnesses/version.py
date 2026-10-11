"""Optional runtime version metadata over the public bounded probe boundary."""
from __future__ import annotations

import time

from . import discovery


class VersionShutdownError(Exception):
    code = "native-shutdown-failed"
    discovery_shutdown_confirmed = False


def version_text(value: object) -> str | None:
    """Normalize external diagnostic text once; unknown has one internal form."""
    if not isinstance(value, str):
        return None
    text = value.strip()[:80]
    return text if text and text.lower() != "unknown" else None


def probe_version(command: list[str], environment: dict, *, cwd: str,
                  deadline: float, owned: list | None) -> str | None:
    """Continue after any diagnostic failure whose child is confirmed stopped.

    The public probe owns creation, bounded reading and stop observation. Its
    unconfirmed stop is retained even when no main process has been registered.
    """
    receipt = owned if owned is not None else []
    try:
        reason, output = discovery.probe([*command, "--version"], environment,
                                         cwd=cwd, deadline=min(deadline, time.monotonic() + 5), owned=receipt)
    except Exception as error:
        if not receipt[0]["shutdown"]:
            raise VersionShutdownError("the native version probe shutdown is unconfirmed") from error
        return None
    if not receipt[0]["shutdown"]:
        raise VersionShutdownError("the native version probe shutdown is unconfirmed")
    return version_text(output.decode(errors="replace")) if reason is None else None
