"""Model-free owned descendant shapes; identity publication precedes readiness."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from hey_my_buddy.protocol.process_start import process_start


def leaf(ready):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    pending = ready.with_suffix(".pending")
    pending.write_text(json.dumps({"pid": os.getpid(),
                                   "startedAt": process_start(os.getpid(), include_exited=True)}))
    pending.replace(ready)
    while True:
        time.sleep(1)


def main():
    mode, ready = sys.argv[1], Path(sys.argv[2])
    if mode == "leaf":
        leaf(ready)
    if mode == "nohup":
        subprocess.Popen(["nohup", sys.executable, __file__, "leaf", str(ready)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        child = os.fork()
        if child == 0:
            if mode == "group":
                os.setpgid(0, 0)
            elif mode == "session":
                os.setsid()
            elif mode in {"double-fork", "daemon"}:
                if mode == "daemon":
                    os.setsid()
                if os.fork():
                    os._exit(0)
            leaf(ready)
        if mode in {"double-fork", "daemon"}:
            os.waitpid(child, 0)
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
