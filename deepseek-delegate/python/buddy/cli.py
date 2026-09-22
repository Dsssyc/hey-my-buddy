"""Structured CLI for the Python blackboard service (the supported Buddy entrypoint).

Everything the plugin offers is one ``buddy`` command. Existing user-facing methods
keep their names, JSON envelopes and meanings; the board/worker methods below are
additive and are documented in ``references/plugin-service.md``.
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import transport
from .errors import BoardError
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
    "console",
    "console-snapshot",
    "evaluation-write-begin",
    "evaluation-write-renew",
    "evaluation-write-publish",
    "evaluation-write-abort",
    "evaluation-reader-begin",
    "evaluation-reader-release",
    "evaluation-evidence-record",
    "evaluation-maintain",
    "selection-request",
    "selection-get",
    "model-catalog-refresh",
    "workflow-submit",
    "workflow-get",
    "workflow-decide",
    "workflow-continue",
    "workflow-takeover",
    "workflow-cancel",
    "workflow-acknowledge",
    "workflow-suggest",
    "migrate",
    "legacy-import",
    "restart",
    "stop",
]

LOCAL_METHODS = ("worker-start", "worker-stop", "migrate")

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
      failure into a success. A governed task additionally needs the Host control
      triple: pass "controlFile":"<path>" (from workflow-submit) so it is injected
      locally and the token is never printed.

  buddy workflow-submit '{"requestId":"fix-125","hostId":"host-1","task":"...","cwd":"/abs/path"}'
      Admit a governed task and print its compact view. The first response and a
      same-host replay also carry the private Host control capability; it is saved to a
      0600 file under the state directory and only its controlFile path is printed.

  buddy workflow-get '{"runId":"<runId>"}'   buddy workflow-suggest '{"runId":"<runId>","body":"..."}'
      Read the compact governed view (tokens are never exposed), or record one bounded
      suggestion on the caller's own run.

  buddy workflow-decide '{"runId":"<runId>","requestId":"req-1","commandId":"cmd-1","expectedRevision":1,"decision":"approve","controlFile":"/path/from/workflow-submit"}'
  buddy workflow-continue '{"runId":"<runId>","commandId":"cmd-2","expectedRevision":2,"input":"...","helperPolicy":"keep","controlFile":"..."}'
  buddy workflow-cancel '{"runId":"<runId>","commandId":"cmd-3","reason":"...","controlFile":"..."}'
      Host decisions, continuations and governed cancellation. controlFile injects the
      hostId/ownerGeneration/controlToken triple locally, so the token never appears in
      the command line or the output; without it the latest saved generation for the
      runId is used.

  buddy workflow-takeover '{"runId":"<runId>","commandId":"cmd-4","expectedOwnerGeneration":1,"newHostId":"host-2","controlFile":"..."}'
      Rotate the owner capability; a delayed capability from the old generation is
      fenced. The new capability is saved and only its controlFile path is printed.

  buddy workflow-acknowledge '{"runId":"<runId>","artifactId":"...","note":"reviewed the diff and ran the checks","verdict":"accepted","controlFile":"..."}'
      Review the selected final artifact as the current owner. Acceptance stays
      separate from execution and never turns a failure into a success.

  buddy health   buddy capabilities   buddy runtime   buddy dashboard   buddy restart   buddy stop
      service control. `buddy stop` asks the service to stop: queued work is
      cancelled, active attempts get a durable cancel request, the service drains for
      a bounded interval and its response lists unresolved attempts. `buddy restart`
      detaches the daemon while preserving every independent worker.

  buddy console '{"action":"open"}'
      Open the separate writable local console on a private loopback URL. It is a
      second, session- and CSRF-protected browser surface over the same validated
      Python operations; the read-only `buddy dashboard` URL can never write.

  buddy console-snapshot '{}'
  buddy model-catalog-refresh '{"requestId":"first"}'
      Read the bounded evaluation snapshot, or explicitly discover the installed DSH
      harness model catalog. Neither runs a model; discovery never exposes credentials.

  buddy evaluation-write-begin '{"requestId":"edit-1","expectedRevision":0,"kind":"human"}'
  buddy evaluation-write-publish '{"commandId":"cmd-1","writerId":"...","generation":1,...}'
      The durable table gate: one writer at a time, fenced by generation, lease and a
      per-intent token. Omitted collections keep their published values.

  buddy selection-request '{"requestId":"pick-1","task":"make the failing parser test pass","requiredCapabilities":["effort:high"]}'
  buddy selection-get '{"decisionId":"dec-..."}'
      One durable, bounded selection decision. The default read is a compact Host
      summary carrying selectedProfile (the frozen execution identity to delegate
      with); add "includeAudit":true for the persisted model input, envelope and
      proposal. The recommendation is recorded history, never an execution permit.
      A queued decision holds no execution slot and stays cancellable through
      `buddy cancel` on its runId.

  buddy evaluation-maintain '{"requestId":"tidy-1"}'
      One bounded maintenance call over the pending evidence batch using the fixed
      configured decision profile. With configuration.autoMaintain=false the validated
      card-only proposal is retained as needs-host and nothing is published.

  buddy migrate '{"confirm":true}'
      Explicit offline upgrade of a stopped version-5 board (backup + identity
      preservation). It is never part of a cold start and refuses a running service.

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


def _migrate_command(params: dict) -> dict:
    """Explicit offline schema upgrade; never invoked by a cold start."""
    from .migrate import migrate

    if not isinstance(params, dict):
        raise ValueError("migrate params must be an object")
    unknown = sorted(set(params) - {"stateDir", "confirm", "dryRun"})
    if unknown:
        raise ValueError(f"Unknown migrate parameter: {unknown[0]}")
    return migrate(
        get_state_dir(params.get("stateDir")),
        confirm=params.get("confirm") is True,
        dry_run=params.get("dryRun") is True,
    )


#: Bound on a control file this CLI is willing to read.
MAX_CONTROL_FILE_BYTES = 64 * 1024
#: Governed mutations whose Host control triple is required by the service.
CONTROL_METHODS = frozenset(
    {
        "workflow-decide",
        "workflow-continue",
        "workflow-takeover",
        "workflow-cancel",
        "workflow-acknowledge",
    }
)
_CONTROL_TRIPLE = ("hostId", "ownerGeneration", "controlToken")
_CONTROL_FILE_FIELDS = frozenset({*_CONTROL_TRIPLE, "runId", "savedAt"})
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _agent_credential() -> str | None:
    """The attempt-scoped credential from the environment, never the administrator token."""
    token = os.environ.get("BUDDY_AGENT_CREDENTIAL")
    if token is not None:
        if not token.strip():
            raise BoardError(
                "UNAUTHORIZED",
                "BUDDY_AGENT_CREDENTIAL is empty; an attempt-scoped credential is never replaced by the "
                "administrator token",
            )
        return token.strip()
    path_value = os.environ.get("BUDDY_AGENT_CREDENTIAL_FILE")
    if path_value is None:
        return None
    if not path_value.strip():
        raise BoardError("UNAUTHORIZED", "BUDDY_AGENT_CREDENTIAL_FILE is empty")
    try:
        value = json.loads(Path(path_value).expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise BoardError(
            "UNAUTHORIZED", f"The attempt-scoped credential file {path_value!r} is missing or unreadable"
        ) from exc
    scoped = value.get("token") if isinstance(value, dict) else None
    if not isinstance(scoped, str) or not scoped.strip():
        raise BoardError("UNAUTHORIZED", f"The attempt-scoped credential file {path_value!r} has no token")
    return scoped.strip()


def _control_path(run_id: str, generation: int) -> Path:
    """The private control path for one run and owner generation, with its 0700 directory."""
    if not isinstance(run_id, str) or not _RUN_ID_PATTERN.match(run_id):
        raise BoardError("INVALID_ARGUMENT", "runId must be a plain identifier to locate its control file")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise BoardError("INVALID_ARGUMENT", "ownerGeneration must be a positive integer")
    directory = transport.get_state_dir() / "controls"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    return directory / f"{run_id}.g{generation}.json"


def _save_control(run_id: str, control: dict) -> str:
    """Atomically save one Host control capability to its own 0600 file."""
    control = control if isinstance(control, dict) else {}
    host_id = control.get("hostId")
    generation = control.get("ownerGeneration")
    token = control.get("controlToken")
    if not isinstance(host_id, str) or not host_id.strip():
        raise BoardError("INVALID_ARGUMENT", "control.hostId must be a nonempty string")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise BoardError("INVALID_ARGUMENT", "control.ownerGeneration must be a positive integer")
    if not isinstance(token, str) or not token.strip():
        raise BoardError("INVALID_ARGUMENT", "control.controlToken must be a nonempty string")
    path = _control_path(run_id, generation)
    payload = json.dumps(
        {
            "hostId": host_id.strip(),
            "ownerGeneration": generation,
            "controlToken": token.strip(),
            "runId": run_id,
            "savedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise
    try:  # The directory entry itself is synced best-effort; the file is already durable.
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        pass
    return str(path)


def _read_control(path_value: str) -> dict:
    """Read one 0600 control file owned by this user and return its Host control triple."""
    if not isinstance(path_value, str) or not path_value.strip():
        raise BoardError("INVALID_ARGUMENT", "controlFile must be a nonempty path string")
    path = Path(path_value).expanduser()
    try:
        info = os.lstat(path)
    except FileNotFoundError as exc:
        raise BoardError("INVALID_ARGUMENT", "controlFile does not exist") from exc
    except OSError as exc:
        raise BoardError("INSECURE_CONTROL_FILE", "The control file could not be inspected") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise BoardError("INSECURE_CONTROL_FILE", "The control file must be a regular file, not a symlink")
    if info.st_uid != os.geteuid():
        raise BoardError("INSECURE_CONTROL_FILE", "The control file must be owned by the current user")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise BoardError("INSECURE_CONTROL_FILE", "The control file must have mode 0600")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise BoardError("INSECURE_CONTROL_FILE", "The control file could not be opened safely") from exc
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino) or opened.st_uid != os.geteuid():
            raise BoardError("INSECURE_CONTROL_FILE", "The control file changed while it was being opened")
        raw = b""
        while len(raw) <= MAX_CONTROL_FILE_BYTES:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            raw += chunk
    except OSError as exc:
        raise BoardError("INSECURE_CONTROL_FILE", "The control file could not be read") from exc
    finally:
        os.close(fd)
    if len(raw) > MAX_CONTROL_FILE_BYTES:
        raise BoardError("INVALID_ARGUMENT", "The control file is too large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BoardError("INVALID_ARGUMENT", "The control file is not valid JSON") from exc
    if not isinstance(value, dict):
        raise BoardError("INVALID_ARGUMENT", "The control file must contain a JSON object")
    unknown = sorted(set(value) - _CONTROL_FILE_FIELDS)
    if unknown:
        raise BoardError("INVALID_ARGUMENT", f"Unknown control file field: {unknown[0]}")
    host_id = value.get("hostId")
    generation = value.get("ownerGeneration")
    token = value.get("controlToken")
    if not isinstance(host_id, str) or not host_id.strip():
        raise BoardError("INVALID_ARGUMENT", "controlFile hostId must be a nonempty string")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise BoardError("INVALID_ARGUMENT", "controlFile ownerGeneration must be a positive integer")
    if not isinstance(token, str) or not token.strip():
        raise BoardError("INVALID_ARGUMENT", "controlFile controlToken must be a nonempty string")
    return {"hostId": host_id.strip(), "ownerGeneration": generation, "controlToken": token.strip()}


def _apply_control(params: object, method: str) -> dict:
    """Inject the Host control triple from an explicit controlFile or explicit fields.

    There is deliberately no implicit "latest generation" lookup: adopting another
    Host's newest saved capability from a shared cache is never authority. A caller
    either presents the full triple, or names the exact controlFile it was given.
    """
    if not isinstance(params, dict):
        raise BoardError("INVALID_ARGUMENT", "params must be a JSON object")
    if _agent_credential() is not None:
        if "controlFile" in params:
            raise BoardError("FORBIDDEN", "an attempt-scoped credential cannot use a Host control file")
        return params
    prepared = dict(params)
    path_value = prepared.pop("controlFile", None)
    if path_value is None:
        return prepared
    control = _read_control(path_value)
    for field in _CONTROL_TRIPLE:
        if prepared.get(field) is None:
            prepared[field] = control[field]
    return prepared


def _submission_token(request_id: object, params: dict) -> str:
    """The private submission capability, created before the first submit RPC.

    It is persisted under the state directory keyed by requestId, so a retried
    submission recovers the original owner generation while a client that only
    knows the run's public fields can never mint a control capability.
    """
    if not isinstance(request_id, str) or not request_id:
        raise BoardError("INVALID_ARGUMENT", "workflow-submit requires requestId before a submission token")
    directory = transport.get_state_dir() / "submissions"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    name = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:32]
    path = directory / f"{name}.json"
    token = secrets.token_hex(32)
    payload = json.dumps({"requestId": request_id, "submissionToken": token}, sort_keys=True).encode("utf-8")
    # Write the complete record first, then publish it atomically with a hard link:
    # a concurrent identical submission either links first (the winner) or reads the
    # winner's fully written record. A half-written file is never observable and a
    # malformed existing record is never overwritten.
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return _read_submission_token(path, request_id)
        except OSError as exc:
            raise BoardError("INVALID_ARGUMENT", f"The submission token could not be published: {exc}") from exc
    finally:
        Path(temporary).unlink(missing_ok=True)
    return token


def _read_submission_token(path: Path, request_id: str) -> str:
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise BoardError(
            "INVALID_ARGUMENT", "The saved submission token is unreadable; refusing to overwrite it"
        ) from exc
    if not isinstance(saved, dict) or saved.get("requestId") != request_id:
        raise BoardError("CONFLICT", "The saved submission token belongs to a different request")
    token = saved.get("submissionToken")
    if not isinstance(token, str) or not token:
        raise BoardError("INVALID_ARGUMENT", "The saved submission token is malformed; refusing to overwrite it")
    return token


def _scrub_and_save(result: dict) -> None:
    """Save a returned Host control capability privately and replace its token with the file path."""
    control = result.get("control")
    if not isinstance(control, dict) or not isinstance(control.get("controlToken"), str) or not control["controlToken"]:
        return
    run_id = result.get("runId")
    if not isinstance(run_id, str) or not run_id:
        raise BoardError("INVALID_ARGUMENT", "A control result must carry its runId before its token can be saved")
    path = _save_control(run_id, control)
    result["control"] = {
        "hostId": control.get("hostId"),
        "ownerGeneration": control.get("ownerGeneration"),
        "controlFile": path,
    }
    result["controlFile"] = path


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
            result = _worker_command(args.method, params) if args.method != "migrate" else _migrate_command(params)
        elif args.method in ("run", "await"):
            from .blocking import WaitAbandoned, await_run, recovery_commands, run_blocking

            try:
                result = run_blocking(params) if args.method == "run" else await_run(params)
            except WaitAbandoned as abandoned:
                print(json.dumps(_abandoned(abandoned, recovery_commands(abandoned.request_id, abandoned.run_id)), ensure_ascii=False))
                return 1
        else:
            credential = _agent_credential()
            if credential is not None:
                if not isinstance(params, dict):
                    raise BoardError("INVALID_ARGUMENT", "params must be a JSON object")
                params["credential"] = credential
            prepared = _apply_control(params, args.method)
            if args.method == "workflow-submit" and not prepared.get("submissionToken"):
                # An agent-scoped caller must never mint Host authority.
                if credential is None:
                    prepared["submissionToken"] = _submission_token(prepared.get("requestId"), prepared)
            result = call_service(args.method, prepared)
            if isinstance(result, dict):
                _scrub_and_save(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:  # noqa: BLE001 - the CLI converts every failure into one envelope
        print(json.dumps({"error": {"code": getattr(error, "code", "SERVICE_ERROR"), "message": str(error)}}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
