"""Structured CLI for the Buddy service (the single supported entrypoint).

Everything the plugin offers is one ``buddy`` command: one method name, one JSON
object argument, one JSON object on stdout. The single governed goal lifecycle owns
the bare names (``submit``, ``get``, ``decide``, ``continue``, ``takeover``,
``cancel``, ``accept``, ``conclude``, ``reclaim``, ``await``, ``suggest``); ``execution-*`` names reach the
ordinary task records that the command/external adapters and internal decision
infrastructure own. The current command surface is documented in
``docs/reference/cli.md``.

The one JSON object may be given as the positional argument, as ``--params-file
PATH`` or on standard input (``-``); the three are mutually exclusive and the file
or stream content is decoded as UTF-8 and parsed directly, never through a shell.
``buddy help`` lists every method and ``buddy help METHOD`` prints the parameters
that method actually validates (see :mod:`buddy.cli_help`).
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

from . import cli_help, cli_views, runtime, transport
from .errors import BoardError
from .launcher import service_environment
from .transport import METHOD_MAP, call_service, get_state_dir
from .worker.worker import RETIRE_REQUEST_NAME

METHODS = [
    "ping",
    "health",
    "capabilities",
    "adapters",
    "accounts",
    "account-set",
    "account-login",
    "account-status",
    "account-cancel",
    "account-logout",
    "account-remove",
    "harness-set",
    "quota-redetect",
    "runtime",
    "backup",
    "backup-preflight",
    "upgrade",
    "install",
    "paths",
    "storage-plan",
    "storage-apply",
    "worker-sessions",
    # -- single governed goal lifecycle ------------------------------------
    "submit",
    "get",
    "decide",
    "continue",
    "takeover",
    "cancel",
    "accept",
    "conclude",
    "reclaim",
    "scope-amend",
    "workspace-resolve",
    "await",
    "suggest",
    # -- work objectives: read-only browsing of grouped delegations ----------
    "objective-list",
    "objective-timeline",
    # -- execution records for command/external/decision infrastructure -----
    "execution-submit",
    "execution-cancel",
    "execution-retry",
    "execution-acknowledge",
    # -- execution observation stays distinct -------------------------------
    "status",
    "list",
    "result",
    "artifacts",
    "events",
    "wait",
    "watch",
    "inquire",
    "message",
    "messages",
    "message-get",
    "message-update",
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
    "console",
    "console-snapshot",
    "evaluation-write-begin",
    "evaluation-write-renew",
    "user-policy-publish",
    "assessment-publish",
    "evaluation-write-abort",
    "evaluation-reader-begin",
    "evaluation-reader-release",
    "evaluation-evidence-record",
    "evaluation-prepare",
    "evaluation-history",
    "selection-request",
    "selection-get",
    "selection-list",
    "model-catalog-refresh",
    "model-profiles",
    "restart",
    "stop",
]

LOCAL_METHODS = ("worker-start", "worker-stop")

#: Bound on a ``--params-file`` file or a standard-input parameter document. It is
#: the same 8 MiB the transport accepts for one request, so the CLI refuses an
#: oversized document before any socket is opened.
MAX_PARAMS_BYTES = transport.MAX_MESSAGE_BYTES

EPILOG = """\
examples:
  Input: one JSON object as the positional argument, --params-file PATH or "-" (stdin); the three are mutually exclusive, decoded as UTF-8 and never re-interpreted by a shell. `buddy help [METHOD]` prints the methods or one method's validated parameters and starts no service.
  buddy submit '{"requestId":"fix-123","hostId":"host-1","task":"...","cwd":"/abs/path","executionWorkspace":{"kind":"existing","access":"write"}}'
      Admit one governed goal in an explicit executionWorkspace: kind existing|worktree,
      access read|write, base {kind:commit|working-tree,ref?}, includeUntracked,
      writeScope and targetRef; the source cwd and the integrator are derived from
      the top-level cwd and the submitting Host.
      Routing: a complete explicit adapter/provider/model/effort quadruple is validated
      and delegated directly without a Router call; a partial quadruple is rejected —
      omit every configuration field and let the Router choose, writing the task's
      needs into the task description, or supply the complete quadruple.
      The first response (and a replay that still presents the private submission
      capability) returns the Host control capability; the CLI saves it to a 0600 file
      and prints only its controlFile path. The control token is never printed.

  buddy get '{"runId":"<runId>"}'      buddy get '{"runId":"<runId>","includeAudit":true}'
      Read the compact governed view (control tokens are never exposed) or the full
      audit. `suggest '{"runId":"...","body":"..."}'` records one bounded suggestion
      on the caller's own run. Unknown never means stopped: read `shutdown` before
      treating a goal as closed.

  buddy decide '{"runId":"<runId>","requestId":"req-1","commandId":"cmd-1","expectedRevision":1,"decision":"approve","helpers":[],"controlFile":"/path/from-submit"}'
  buddy continue '{"runId":"<runId>","commandId":"cmd-2","expectedRevision":2,"input":"...","helperPolicy":"keep","controlFile":"..."}'
  buddy takeover '{"runId":"<runId>","commandId":"cmd-3","expectedOwnerGeneration":1,"newHostId":"host-2","controlFile":"..."}'
  buddy cancel '{"runId":"<runId>","commandId":"cmd-4","reason":"...","controlFile":"..."}'
  buddy scope-amend '{"runId":"<runId>","commandId":"cmd-5","expectedRevision":3,"expectedScopeVersion":1,"writeScope":["src"],"reason":"...","controlFile":"..."}'
  buddy conclude '{"runId":"<runId>","note":"failed after the schema change; partial work retained","controlFile":"..."}'   buddy reclaim '{"runId":"<runId>","controlFile":"..."}'
      conclude ends a failed, cancelled or delivered-but-unaccepted goal: unsealed
      managed changes are sealed as an independent Host partial output, the
      conclusion is recorded and the checkout reclaimed. reclaim retries the
      removal after a blocked reclaim or a keepCheckout acceptance. All three take
      targetRunId for an owned helper and no command number: the same request
      replays, a changed payload conflicts. controlFile injects the control triple;
      no implicit latest-generation lookup.
  buddy workspace-resolve '{"runId":"<runId>","commandId":"cmd-6","expectedRevision":4,"conflictId":"...","action":"restore","paths":["file"],"observedFingerprint":"...","controlFile":"..."}'
  buddy accept '{"runId":"<runId>","artifactId":"...","note":"inspected the diff and ran the checks","target":{"path":"/abs/target","ref":"main"},"controlFile":"..."}'
  buddy accept '{"runId":"<runId>","artifactId":"...","note":"no repository target needed","notRequired":"the artifact needs no repository target","controlFile":"..."}'
      Accept one delivered artifact. The service compares the artifact with the
      target itself; differing paths are refused with their names first, and your
      own adjustments are confirmed with "adjusted":true (the note is the reason).
      Optional hostPaths with beforeCommit verify real Host additions; the checkout
      is reclaimed afterwards unless "keepCheckout":true.

  buddy await '{"runId":"<runId>"}'      buddy await '{"requestId":"fix-123","waitSeconds":3600}'
      Wait without starting, resuming or cancelling work. waitSeconds is at most
      86400 (the default); wait-timeout leaves the run active. Re-await the same
      runId, use status/get for live state, and result only after a receipt exists.
      Cancel a goal with cancel, or an execution record with execution-cancel.

  Advanced execution records (command/external adapters and internal decision
  infrastructure only; the service refuses ungoverned dsh/zcode here):
  buddy execution-submit '{"requestId":"probe-1","task":"...","cwd":"/abs/path","adapter":"command","argv":["/bin/echo","hi"],"timeoutSeconds":1800}'
  buddy execution-cancel '{"runId":"<runId>","reason":"..."}'
  buddy execution-retry '{"runId":"<runId>","reason":"..."}'
  buddy execution-acknowledge '{"runId":"<runId>","note":"...","verdict":"accepted"}'
      Explicit task operations, separate from the goal lifecycle. execution-submit
      requires requestId, task and an absolute cwd; use the command or external
      adapter. A governed run's execution record additionally needs its Host control.

  buddy status '{"runId":"<runId>"}'   buddy result '{"runId":"<runId>"}'   buddy list '{"limit":20}'
  buddy artifacts '{"runId":"<runId>"}'   buddy events '{"after":0}'
  buddy wait '{"runId":"<runId>","timeoutMs":30000}'   buddy watch '{"after":0,"timeoutMs":30000}'
  buddy inquire '{"runId":"<runId>"}'
  buddy inquire '{"runId":"<runId>","inquiryId":"q1","question":"what is blocking you?","waitMs":20000}'
      Execution observation stays distinct: status, result, list, artifacts and events
      read execution records, wait/watch use the dedicated bounded wait resource, and
      inquire is bounded read-only observation or one correlated question to a live
      agent whose adapter declares inquiry support.

  buddy workers   buddy wait-capacity   buddy message '{"runId":"<runId>","inquiryId":"q2","question":"status?"}'
  buddy message-get '{"runId":"<runId>","inquiryId":"q2"}'
  buddy worker-start '{"workerId":"local"}'   buddy worker-stop '{"workerId":"local"}'
      Board operations for external workers and operators. The local worker commands
      start or cooperatively stop one independent Python worker supervisor.

  buddy health   buddy capabilities   buddy runtime   buddy console   buddy restart   buddy stop
      Service control. `buddy stop` cancels queued work, durably requests cancellation
      for active attempts, drains for a bounded interval and lists unresolved attempts.
      `buddy restart` detaches the daemon while preserving every independent worker.

  buddy console-snapshot '{}'   buddy model-catalog-refresh '{"requestId":"first"}'
      Read the bounded evaluation snapshot, or explicitly discover the installed
      harness model catalog. Neither runs a model; discovery never exposes credentials.

  buddy evaluation-write-begin '{"requestId":"maint-1","expectedRevision":0,"kind":"maintenance"}'
  buddy assessment-publish '{"commandId":"cmd-1","writerId":"...","generation":1,...}'
      A maintenance grant publishes only changed assessment cards. Human grants and
      user-policy publication belong to the authenticated console; an ordinary Host
      cannot edit user annotations, preferences, enablement or configuration.

  buddy model-profiles '{"includeUnavailable":true,"limit":100,"query":"GLM","adapter":"zcode"}'
      Search retained configuration identities before pagination; query has at most
      200 characters and 8 whitespace-separated terms. Read nextCursor for more.

  buddy selection-request '{"requestId":"pick-1","task":"make the failing parser test pass","requiredCapabilities":["effort:high"]}'
  buddy selection-get '{"decisionId":"dec-..."}'
      One durable, bounded selection decision. The default read is a compact Host
      summary carrying selectedProfile (the frozen execution identity to delegate
      with); add "includeAudit":true for the persisted model input, envelope and
      proposal. A recommendation is recorded history, never an execution permit;
      a queued decision stays cancellable through `execution-cancel` on its runId.

  buddy evaluation-prepare '{"requestId":"maint-1","limit":32}'   buddy evaluation-history '{"limit":20}'
      Harness-owned maintenance: prepare collects a bounded, deterministic batch of real Host-reviewed
      facts (no model call, no lease); synthesize the cards and publish a short card-only patch with
      `assessment-publish`. `evaluation-history` reads bounded publication pages; there is no autoMaintain.

  Repeating an inquiryId never injects the question twice; changed text is an error.
"""


def _abandoned(abandoned, commands: list[str]) -> dict:
    return {
        "error": {"code": "WAIT_ABANDONED", "message": str(abandoned)},
        "runId": abandoned.run_id,
        "requestId": abandoned.request_id,
        "recovery": {
            "action": "The wait was abandoned; the durable run keeps executing and is not cancelled. Inspect the same run and await it again.",
            "commands": commands,
        },
    }


def _worker_command(action: str, params: dict) -> dict:
    if _agent_credential() is not None:
        raise BoardError("FORBIDDEN", "An attempt credential cannot manage service workers")
    if action == "worker-start":
        from .upgrade import file_lock
        state = get_state_dir(params.get("stateDir"))
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        with file_lock(state / "control-start.lock"):
            if (state / "upgrade.json").exists():
                raise BoardError("UPGRADE_IN_PROGRESS", "Worker start is fenced during upgrade")
            return _worker_command_unlocked(action, params)
    return _worker_command_unlocked(action, params)


def _worker_command_unlocked(action: str, params: dict) -> dict:
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
    # Explicit workers have the same lifetime as daemon-started workers: select
    # their runtime before spawning instead of inheriting this replaceable CLI.
    target = runtime.launch_target(log_path=state_dir / "runtime-install.log")
    # The supervisor is a service process: build its environment from the explicit
    # allowlist instead of the CLI's Host session, then select the runtime.
    environment = service_environment({
        "BUDDY_STATE_DIR": str(state_dir),
        "BUDDY_WORKER_ID": worker_id,
        "BUDDY_RUNTIME_IDENTITY": target["identity"],
    })
    if target["pythonPath"]:
        environment["PYTHONPATH"] = target["pythonPath"]
    if target["stable"]:
        environment["BUDDY_RUNTIME"] = target["runtime"]["runtimeDir"]
        environment["BUDDY_PYTHON"] = target["python"]
        # The explicit interpreter selects its own venv; PATH makes it the first
        # interpreter for tools that run in a delegated workspace.
        environment["PATH"] = str(Path(target["python"]).parent) + os.pathsep + environment.get("PATH", os.defpath)
    (directory).mkdir(mode=0o700, parents=True, exist_ok=True)
    # A deliberate start is authoritative for this exact id: prior cooperative
    # stop/retire intents are cleared before the new supervisor can observe them.
    (directory / "stop.request").unlink(missing_ok=True)
    (directory / RETIRE_REQUEST_NAME).unlink(missing_ok=True)
    log_fd = os.open(log_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try:
        child = subprocess.Popen(
            [target["python"], "-m", "buddy.worker.supervisor", "--worker-id", worker_id, "--state-dir", str(state_dir)],
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


#: Bound on a control file this CLI is willing to read.
MAX_CONTROL_FILE_BYTES = 64 * 1024
#: Governed mutations whose Host control triple is required by the service.
CONTROL_METHODS = frozenset({"decide", "continue", "takeover", "cancel", "accept", "conclude", "reclaim", "scope-amend", "workspace-resolve"})
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
    if os.name != 'nt' and info.st_uid != os.geteuid():
        raise BoardError("INSECURE_CONTROL_FILE", "The control file must be owned by the current user")
    if os.name != 'nt' and stat.S_IMODE(info.st_mode) != 0o600:
        raise BoardError("INSECURE_CONTROL_FILE", "The control file must have mode 0600")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise BoardError("INSECURE_CONTROL_FILE", "The control file could not be opened safely") from exc
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino) or (os.name != 'nt' and opened.st_uid != os.geteuid()):
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
        raise BoardError("INVALID_ARGUMENT", "submit requires requestId before a submission token")
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


def _usage_error(payload: dict) -> None:
    """Print one structured error envelope for a command-line usage mistake and exit 2."""
    print(_dumps({"error": payload}))
    raise SystemExit(2)


def _read_params_source(source: str) -> str:
    """Read one parameter document as UTF-8 bytes, bounded and never through a shell."""
    if source == "-":
        label = "standard input"
        stream = getattr(sys.stdin, "buffer", None)
        try:
            if stream is not None:
                raw = stream.read(MAX_PARAMS_BYTES + 1)
            else:  # a caller that replaced sys.stdin with a text stream
                raw = sys.stdin.read(MAX_PARAMS_BYTES + 1).encode("utf-8")
        except OSError as exc:
            raise BoardError("INVALID_ARGUMENT", f"The parameters on {label} could not be read: {exc}") from exc
    else:
        label = f"params file {source!r}"
        try:
            with open(source, "rb") as handle:
                raw = handle.read(MAX_PARAMS_BYTES + 1)
        except OSError as exc:
            raise BoardError("INVALID_ARGUMENT", f"The {label} could not be read: {exc.strerror or exc}") from exc
    if len(raw) > MAX_PARAMS_BYTES:
        raise BoardError("INVALID_ARGUMENT", f"The {label} exceeds {MAX_PARAMS_BYTES} bytes")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BoardError("INVALID_ARGUMENT", f"The {label} is not valid UTF-8") from exc


def _parameters_text(args) -> str:
    """The one JSON object text for this invocation: argument, ``--params-file`` or ``-``.

    The sources are mutually exclusive and the resulting text takes exactly the same
    path afterwards, so ``controlFile``, ``output`` and the attempt-scoped credential
    behave identically whichever input a caller chose. A file or stream is decoded as
    UTF-8 and parsed directly: its content is never re-interpreted by a shell.
    """
    if args.params_file is not None and args.params is not None:
        _usage_error(
            {
                "code": "INVALID_ARGUMENT",
                "message": "Pass the JSON object either as the positional argument or as --params-file, not both",
            }
        )
    if args.params_file is not None:
        return _read_params_source(args.params_file)
    if args.params is not None:
        return _read_params_source("-") if args.params == "-" else args.params
    return "{}"


def _parse_parameters(text: str) -> dict:
    try:
        params = json.loads(text)
    except ValueError as exc:
        raise BoardError("INVALID_ARGUMENT", f"The method parameters are not valid JSON: {exc}") from exc
    if not isinstance(params, dict):
        raise BoardError("INVALID_ARGUMENT", "params must be a JSON object")
    return params


def _help_command(arguments: list[str]) -> int:
    """``buddy help [METHOD]``: generated from the validators, starting nothing."""
    if len(arguments) > 1:
        _usage_error({"code": "UNKNOWN_METHOD", "message": "buddy help takes at most one method name"})
    text, candidates = cli_help.render(arguments[0] if arguments else None, METHODS)
    if text is not None:
        print(text)
        return 0
    _usage_error(
        {
            "code": "UNKNOWN_METHOD",
            "message": f"Unknown method {arguments[0]!r}",
            "didYouMean": candidates,
        }
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["help"]:
        return _help_command(argv[1:])
    # The two spelling aliases are rewritten before the method name is checked.
    if len(argv) >= 2 and argv[0] == "storage" and argv[1] in ("plan", "apply"):
        argv[:2] = ["storage-" + argv[1]]
    if len(argv) == 4 and argv[:2] == ['harness', 'set']:
        argv = ['harness-set', json.dumps({'adapter': argv[2], 'path': None if argv[3] == '--auto' else argv[3]})]
    if argv and not argv[0].startswith("-") and argv[0] not in METHODS:
        # An unknown method is always a structured CLI error with its closest
        # candidates; it is never forwarded to a service with a guessed operation.
        _usage_error(
            {
                "code": "UNKNOWN_METHOD",
                "message": f"Unknown method {argv[0]!r}",
                "didYouMean": cli_help.nearest_methods(argv[0], METHODS),
            }
        )
    parser = argparse.ArgumentParser(
        description="Buddy service: governed goals, durable execution records, independent workers and routed harness execution",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("method", metavar="METHOD", help="run `buddy help` for every method and its parameters")
    parser.add_argument("params", nargs="?", default=None, help='one JSON object, or "-" to read it from standard input')
    parser.add_argument("--params-file", metavar="PATH", default=None, help='read the JSON object from PATH ("-" for standard input)')
    args = parser.parse_args(argv)
    try:
        params = _parse_parameters(_parameters_text(args))
        if (args.method == 'account-login' and 'apiKey' in params
                and not (args.params == '-' or args.params_file == '-')):
            params.pop('apiKey', None)
            raise BoardError('ACCOUNT_KEY_STDIN_REQUIRED', 'API keys must arrive through standard input')
        if args.method == 'account-login' and params.get('mode') == 'api-key':
            if 'apiKey' in params:
                pass
            else:
                if args.params == '-' or args.params_file == '-':
                    raise BoardError('ACCOUNT_KEY_STDIN_REQUIRED', 'Supply metadata separately from the key input stream')
                if sys.stdin.isatty():
                    raise BoardError('ACCOUNT_KEY_STDIN_REQUIRED', 'Pipe the key through standard input or use the authenticated form')
                value = sys.stdin.read(16385)
                from .account_native import key_value
                params['apiKey'] = key_value(value.rstrip('\r\n'))
        # `output` is CLI-local: it selects the printed projection and never reaches RPC.
        # Console validates its own local options, so it keeps its complete response.
        mode = cli_views.OUTPUT_FULL if args.method == "console" else cli_views.pop_output_mode(params)
        if args.method == "upgrade":
            from .skill_install import install
            result = install(params)
        elif args.method == "install":
            from .skill_install import install
            result = install(params)
        elif args.method == "backup-preflight":
            from .backup import preflight_command
            result = preflight_command(params)
        elif args.method == "paths":
            from .skill_install import paths
            result = paths(params)
        elif args.method in LOCAL_METHODS:
            result = _worker_command(args.method, params)
        elif args.method == "console":
            # Console keeps its one canonical JSON argument, but `browser` and `wait`
            # are CLI-local: console_cli validates and strips them, launches the
            # default browser and waits without ever cold-starting a service. An
            # attempt-scoped credential is refused before any local action or RPC.
            from . import console_cli

            result = console_cli.run(params, credential=_agent_credential())
        elif args.method == "await":
            from .blocking import WaitAbandoned, await_run, recovery_commands

            try:
                result = await_run(params)
            except WaitAbandoned as abandoned:
                print(_dumps(_abandoned(abandoned, recovery_commands(abandoned.request_id, abandoned.run_id))))
                return 1
        else:
            credential = _agent_credential()
            if credential is not None:
                if not isinstance(params, dict):
                    raise BoardError("INVALID_ARGUMENT", "params must be a JSON object")
                params["credential"] = credential
            prepared = _apply_control(params, args.method)
            if args.method == "submit" and not prepared.get("submissionToken"):
                # An agent-scoped caller must never mint Host authority.
                if credential is None:
                    prepared["submissionToken"] = _submission_token(prepared.get("requestId"), prepared)
            result = call_service(args.method, prepared)
            if isinstance(result, dict):
                _scrub_and_save(result)
        print(_dumps(_render_output(args.method, result, mode)))
        return 1 if isinstance(result, dict) and result.get("error") else 0
    except Exception as error:  # noqa: BLE001 - the CLI converts every failure into one envelope
        payload = error.payload() if isinstance(error, BoardError) else {"code": "SERVICE_ERROR", "message": str(error)}
        print(_dumps({"error": payload}))
        return 1


def _render_output(method: str, response, mode: str):
    """Keep the new routing DTO in brief output without widening other views."""
    rendered = cli_views.render(method, response, mode)
    if mode != cli_views.OUTPUT_BRIEF or not isinstance(response, dict) or not isinstance(rendered, dict):
        return rendered
    routing = response.get("routing")
    projected = rendered.get("routing")
    if isinstance(routing, dict) and isinstance(projected, dict):
        # cli_views uses a strict key list. Preserve the frozen list alongside
        # its actual current dispatch identity, including explicit absent actors.
        fields = (
            "routerProfileIds", "routerIdentities", "routerProfileId", "routerProfile", "routerIndex",
            "budget", "routingBudget", "routerRetryIntervalSeconds", "configurationRevision", "decisionModel", "routingBoundary",
        )
        rendered = {**rendered, "routing": {**projected, **{key: routing[key] for key in fields if key in routing}}}
    pending = response.get("pendingRequests")
    projected_pending = rendered.get("pendingRequests")
    if isinstance(pending, list) and isinstance(projected_pending, list):
        boundaries = {item.get("requestId"): item["routingBoundary"] for item in pending
                      if isinstance(item, dict) and isinstance(item.get("routingBoundary"), dict)}
        if boundaries:
            rendered = {**rendered, "pendingRequests": [
                {**item, "routingBoundary": boundaries[item.get("requestId")]} if item.get("requestId") in boundaries else item
                for item in projected_pending
            ]}
    if method == "await":
        workflow = response.get("workflow")
        boundary = workflow.get("routingBoundary") if isinstance(workflow, dict) else None
        if isinstance(boundary, dict):
            rendered = {**rendered, "routingBoundary": boundary}
            if isinstance(rendered.get("request"), dict):
                rendered["request"] = {**rendered["request"], "routingBoundary": boundary}
            # Print the program's complete tuples and blocked metadata instead
            # of placeholder configurations supplied by the older wait view.
            commands = boundary.get("commands") or {}
            next_commands = []
            continuation = commands.get("continue") or {}
            for choice in continuation.get("choices", []):
                if not choice.get("blocked"):
                    next_commands.append({"method": choice["method"], "params": choice["params"]})
            reroute = commands.get("reroute") or {}
            if not reroute.get("blocked") and isinstance(reroute.get("params"), dict):
                next_commands.append({"method": reroute["method"], "params": reroute["params"],
                                      "notBefore": reroute.get("notBefore")})
            rendered["nextCommands"] = next_commands
    return rendered


def _dumps(value) -> str:
    """One compact JSON object: indentation is whitespace a Host model re-reads on every request."""
    from .contracts import CONTRACT_VERSION
    if isinstance(value, dict):
        value = {"contractVersion": CONTRACT_VERSION, **value}
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    sys.exit(main())
