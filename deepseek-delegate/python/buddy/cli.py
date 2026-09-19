"""Structured CLI for the Python blackboard service (the supported Buddy entrypoint).

Everything the plugin offers is one ``buddy`` command. Existing user-facing methods
keep their names, JSON envelopes and meanings; the board/worker methods below are
additive and are documented in ``references/plugin-service.md``.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .transport import METHOD_MAP, call_service, get_state_dir

METHODS = [
    "health",
    "capabilities",
    "adapters",
    "runtime",
    "run",
    "await",
    "start",
    "submit",
    "status",
    "wait",
    "watch",
    "events",
    "result",
    "list",
    "cancel",
    "retry",
    "acknowledge",
    "inquire",
    "message",
    "messages",
    "message-get",
    "message-update",
    "artifacts",
    "workers",
    "worker-register",
    "worker-claim",
    "worker-reconcile",
    "worker-renew",
    "worker-progress",
    "worker-result",
    "worker-release",
    "worker-start",
    "worker-stop",
    "wait-capacity",
    "dashboard",
    "legacy-import",
    "restart",
    "stop",
]

LOCAL_METHODS = ("worker-start", "worker-stop")

EPILOG = """\
examples:
  buddy run '{"requestId":"fix-123","task":"...","cwd":"/abs/path","timeoutSeconds":7200}'
      One-call convenience: start (or recover) one durable task and stay connected
      until it finishes. Bounded by waitSeconds, which defaults to timeoutSeconds +
      60 s shutdown grace, capped at 86400 s (24 h). The execution deadline
      (timeoutSeconds, default 1800 s, 10..86400) is independent: if the wait window
      ends first you get an honest outcome=wait-timeout envelope and the task keeps
      going - recover it with the SAME requestId or with `buddy await`.

  buddy start '{"requestId":"fix-123","task":"...","cwd":"/abs/path"}'
      Start (or recover) one durable task and print its runId immediately. A task is
      admitted as `queued` and starts when a worker claims it; queued admission is
      intentional and `queueReason` says why it is waiting.

  buddy submit '{"requestId":"fix-124","task":"...","cwd":"/abs/path","adapter":"command","argv":["/bin/echo","hi"]}'
      Explicit board submission for multi-agent use. `command` runs one argv process,
      never a shell.

  buddy await '{"runId":"<runId>"}'
      Wait on an existing task without starting anything. waitSeconds defaults to
      86400 and reaching the window is never an execution failure.

  buddy inquire '{"runId":"<runId>"}'
  buddy inquire '{"runId":"<runId>","inquiryId":"q1","question":"what is blocking you?","waitMs":20000}'
      Read-only bounded observation, or one correlated question to the same live dsh
      agent. Unsupported adapters answer with an honest capability reason.

  buddy workers            buddy wait-capacity        buddy artifacts '{"runId":"<runId>"}'
  buddy events '{"after":0}'   buddy watch '{"after":0,"timeoutMs":30000}'
  buddy message '{"runId":"<runId>","inquiryId":"q2","question":"status?"}'
  buddy message-get '{"runId":"<runId>","inquiryId":"q2"}'
      Board operations for external workers and operators. `watch`/`wait` use the
      dedicated bounded wait resource.

  buddy worker-start '{"workerId":"local"}'   buddy worker-stop '{"workerId":"local"}'
      Start or cooperatively stop one independent Python worker supervisor.

  buddy status '{"runId":"<runId>"}'   buddy result '{"runId":"<runId>"}'
  buddy cancel '{"runId":"<runId>"}'   buddy retry '{"runId":"<runId>"}'
  buddy acknowledge '{"runId":"<runId>","note":"inspected the diff and ran the checks","verdict":"accepted"}'
      inspect the real artifacts first; acknowledgement records that a human/agent
      reviewed the result. It never changes the execution status and never turns a
      failure into a success.

  buddy health   buddy capabilities   buddy runtime   buddy dashboard   buddy restart   buddy stop
      service control. `buddy stop` asks the service to stop: queued work is
      cancelled, active attempts get a durable cancel request, the service drains for
      a bounded interval and its response lists unresolved attempts. `buddy restart`
      detaches the daemon while preserving every independent worker.

  buddy legacy-import '{"sourceDir":"/old/state","dryRun":true}'
      Offline, idempotent, transactional import of the removed Node records. The
      source files are only read.

  Repeating an inquiryId never injects the question twice; the same id with
  different text is an error.
"""


def _abandoned(abandoned, commands: list[str]) -> dict:
    return {
        "error": {"code": "WAIT_ABANDONED", "message": str(abandoned)},
        "runId": abandoned.run_id,
        "requestId": abandoned.request_id,
        "recovery": {
            "action": "The wait was abandoned; the owned task keeps executing and is not cancelled. Recover it with the same task.",
            "commands": commands,
        },
    }


def _worker_command(action: str, params: dict) -> dict:
    """Start or cooperatively stop one independent worker supervisor."""
    worker_id = params.get("workerId") or "local"
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValueError("workerId must be a nonempty string")
    state_dir = get_state_dir(params.get("stateDir"))
    directory = state_dir / "workers" / worker_id
    if action == "worker-stop":
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        request = directory / "stop.request"
        request.write_text(json.dumps({"workerId": worker_id, "requestedBy": "cli"}))
        os.chmod(request, 0o600)
        return {
            "workerId": worker_id,
            "stopRequested": True,
            "note": "The supervisor observes this durable request and stops cooperatively; no signal is sent to a process this CLI did not create.",
        }
    log_path = state_dir / "worker.log"
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_fd = os.open(log_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    environment = {
        **os.environ,
        "BUDDY_STATE_DIR": str(state_dir),
        "BUDDY_WORKER_ID": worker_id,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1])
        + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""),
    }
    (directory).mkdir(mode=0o700, parents=True, exist_ok=True)
    (directory / "stop.request").unlink(missing_ok=True)
    try:
        child = subprocess.Popen(
            [sys.executable, "-m", "buddy.worker.supervisor", "--worker-id", worker_id, "--state-dir", str(state_dir)],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log_fd,
            stderr=log_fd,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        os.close(log_fd)
    return {
        "workerId": worker_id,
        "supervisorPid": child.pid,
        "logPath": str(log_path),
        "stateDir": str(state_dir),
        "note": "The supervisor runs in its own session with file-backed logs and does not depend on this CLI.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Buddy service: transactional Python blackboard, independent workers, dsh and command adapters",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("method", choices=METHODS)
    parser.add_argument("params", nargs="?", default="{}", help="JSON object")
    args = parser.parse_args(argv)
    try:
        params = json.loads(args.params)
        if args.method in LOCAL_METHODS:
            result = _worker_command(args.method, params)
        elif args.method in ("run", "await"):
            from .blocking import WaitAbandoned, await_run, recovery_commands, run_blocking

            try:
                result = run_blocking(params) if args.method == "run" else await_run(params)
            except WaitAbandoned as abandoned:
                print(json.dumps(_abandoned(abandoned, recovery_commands(abandoned.request_id, abandoned.run_id)), ensure_ascii=False))
                return 1
        else:
            result = call_service(args.method, params)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:  # noqa: BLE001 - the CLI converts every failure into one envelope
        print(json.dumps({"error": {"code": getattr(error, "code", "SERVICE_ERROR"), "message": str(error)}}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
