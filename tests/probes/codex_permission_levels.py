"""No-model Codex command/exec permission probe; run deliberately, outside checks.

Usage: python tests/probes/codex_permission_levels.py CODEX_EXECUTABLE NEW_RUN_DIRECTORY PYTHON
NEW_RUN_DIRECTORY must be a new directory outside this checkout and platform
temporary directories, with an existing parent. The probe creates private
checkout and outside subdirectories there, starts the supplied real Codex
executable as ``app-server --listen stdio://`` in an owned native process group,
initializes its protocol, and runs permission_level_things.py once for each of
five native sandbox policies. That child uses the supplied Python interpreter;
its eight operations and their cleanup are documented in its own docstring.
Per-policy JSON records are printed, followed by actual group-stop evidence.
Native stderr is retained in the private run directory; it is not printed.

Only initialize, initialized and command/exec are sent: no thread, turn, model
call, login, blackboard access or hey-my-buddy service start occurs. Codex loads its
normal native configuration; this probe does not read or edit credential or
configuration files and creates no replacement configuration home. The report
does not certify a policy's expected permissions: refusals and failures are
observations. RPC/command failures or unconfirmed group shutdown exit nonzero.
Run materials are retained for Host inspection; the probe never deletes them.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from permission_level_things import temporary_roots

CHECKOUT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CHECKOUT / "src"))

from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError, Connection
from hey_my_buddy.buddy.harnesses.discovery import native_environment
from hey_my_buddy.buddy.harnesses.native_support import halt_owned_group
from hey_my_buddy.buddy.runtime.windows_process import owned_popen


def policies(checkout: Path) -> dict:
    return {
        "checkout_write_network_on": {"type": "workspaceWrite",
                                      "writableRoots": [str(checkout)], "networkAccess": True},
        "checkout_write_network_off": {"type": "workspaceWrite",
                                       "writableRoots": [str(checkout)], "networkAccess": False},
        "read_only": {"type": "readOnly", "networkAccess": False},
        "read_only_network_on": {"type": "readOnly", "networkAccess": True},
        "full_access": {"type": "dangerFullAccess"},
    }


def emit(record: dict) -> None:
    print(json.dumps(record, ensure_ascii=False), flush=True)


def run(codex: str, root: Path, python: str) -> int:
    root.mkdir(mode=0o700)
    checkout, outside = root / "checkout", root / "outside"
    checkout.mkdir(mode=0o700)
    outside.mkdir(mode=0o700)
    script = Path(__file__).with_name("permission_level_things.py").resolve()
    command = [codex]
    with (root / "native.stderr.log").open("xb") as stderr:
        os.chmod(stderr.fileno(), 0o600)
        process = owned_popen([*command, "app-server", "--listen", "stdio://"],
                              cwd=str(checkout),
                              env=native_environment(dict(os.environ), command=command),
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                              start_new_session=True, close_fds=True)
    handle = ProcessHandle(process, own_group=True, log_paths={})
    failed = False
    try:
        connection = Connection(process, time.monotonic() + 330, threading.Event())
        connection.strict_responses = True
        connection.call("initialize", {"clientInfo": {
            "name": "permission_level_probe", "version": "0.30.0"}})
        connection.send({"method": "initialized", "params": {}})
        for label, policy in policies(checkout).items():
            record = {"policy": label, "sandboxPolicy": policy}
            try:
                answer = connection.call("command/exec", {
                    "command": [python, "-B", str(script), str(outside)],
                    "cwd": str(checkout), "timeoutMs": 60000, "sandboxPolicy": policy})
            except CodexProtocolError as error:
                emit({**record, "status": "failed", "code": error.code, "reason": str(error)})
                failed = True
                if error.code != "native-rpc-error":
                    break
                continue
            record.update(exitCode=answer.get("exitCode"), stderr=answer.get("stderr"))
            output = (answer.get("stdout") or "").splitlines()
            try:
                facts = json.loads(output[-1])
                if not isinstance(facts, dict) or len(facts) != 8:
                    raise ValueError("the command did not return eight permission observations")
            except (IndexError, ValueError) as error:
                record.update(status="failed", reason=str(error), stdout=answer.get("stdout"))
                failed = True
            else:
                record.update(status="observed" if answer.get("exitCode") == 0 else "failed",
                              results=facts)
                failed |= answer.get("exitCode") != 0
            emit(record)
    except (CodexProtocolError, OSError) as error:
        emit({"status": "failed", "reason": f"{type(error).__name__}: {error}"})
        failed = True
    finally:
        stopped, signalled = halt_owned_group(process, handle, time.monotonic() + 5)
        process.stdout.close()
        emit({"nativeGroupStopped": stopped, "nativeExitCode": process.returncode,
              "stopSignalled": signalled, "modelCalls": 0})
        failed |= not stopped
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("codex_executable")
    parser.add_argument("new_run_directory", type=Path)
    parser.add_argument("python")
    args = parser.parse_args()
    root = args.new_run_directory.expanduser().resolve()
    if root.is_relative_to(CHECKOUT) or any(root.is_relative_to(path) for path in temporary_roots()):
        parser.error("NEW_RUN_DIRECTORY must be outside this checkout and temporary directories")
    if root.exists() or not root.parent.is_dir():
        parser.error("NEW_RUN_DIRECTORY must be new and have an existing parent")
    return run(args.codex_executable, root, args.python)


if __name__ == "__main__":
    raise SystemExit(main())
