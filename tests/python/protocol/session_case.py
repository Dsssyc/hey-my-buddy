"""Only the fixture's recorded identities may be stopped by a test cleanup."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from hey_my_buddy.protocol.process_handle import ProcessHandle
from hey_my_buddy.protocol.process_start import process_start


@contextmanager
def descendant_case(case, directory, mode, *, cwd=None):
    ready = Path(directory) / (mode + ".json")
    fixture = Path(__file__).parent / "fixtures" / "session_descendants.py"
    root = subprocess.Popen([sys.executable, str(fixture), mode, str(ready)],
                            cwd=cwd, start_new_session=True, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    handle = ProcessHandle(root, log_paths={}, stop_channel="signal")
    member = None
    try:
        deadline = time.monotonic() + 8
        while not ready.exists():
            case.assertLess(time.monotonic(), deadline, "descendant did not publish its identity")
            time.sleep(0.01)
        member = json.loads(ready.read_text())
        if mode in {"double-fork", "daemon"}:
            import psutil
            while psutil.Process(member["pid"]).ppid() != 1:
                case.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
        yield handle, member
    finally:
        handle.terminate(grace_seconds=0, settle_seconds=2)
        if member is not None:
            if process_start(member["pid"], include_exited=False) == member["startedAt"]:
                os.kill(member["pid"], signal.SIGKILL)
            deadline = time.monotonic() + 5
            while process_start(member["pid"], include_exited=True) == member["startedAt"]:
                case.assertLess(time.monotonic(), deadline, "fixture member identity survived cleanup")
                time.sleep(0.01)
        case.assertIsNotNone(root.returncode, "fixture root was not reaped")
