"""Independent POSIX process observations for native lifecycle regressions."""
from __future__ import annotations

import subprocess
import time


def wait_for_unreaped_child(pid: int, *, timeout: float = 5.0) -> None:
    """Observe a zombie with ps, without polling or waiting on the child handle."""
    deadline = time.monotonic() + timeout
    state = ""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"child {pid} did not become a zombie; last ps state: {state!r}")
        observation = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=remaining,
        )
        state = observation.stdout.strip()
        if state.startswith("Z"):
            return
        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
