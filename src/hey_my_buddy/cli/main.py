"""Structured CLI for the hey-my-buddy service (the single supported entrypoint).

Everything the shared skill offers is one ``hey-my-buddy`` command: one method name, one JSON
object argument, one JSON object on stdout. The single micro task lifecycle owns
the bare names (``submit``, ``get``, ``decide``, ``continue``, ``takeover``,
``cancel``, ``accept``, ``conclude``, ``reclaim``, ``await``, ``suggest``); ``execution-*`` names manage
ordinary task records created by the workflow and internal decision
infrastructure. The current command surface is documented in
``docs/reference/cli.md``.

The one JSON object may be given as the positional argument, as ``--params-file
PATH`` or on standard input (``-``); the three are mutually exclusive and the file
or stream content is decoded as UTF-8 and parsed directly, never through a shell.
``hey-my-buddy help`` lists every method and ``hey-my-buddy help METHOD`` prints the parameters
that method actually validates (see :mod:`hey_my_buddy.cli.cli_help`).
"""
import argparse
import io
import json
import secrets
import sys
import time
from pathlib import Path

from . import cli_help, cli_views
from ..protocol import transport
from ..private_files import publish_bytes, open_read
from ..errors import BoardError
from ..home import control_file_path, submission_file_path
from ..install.launcher import agent_credential_document, credential_state_dir, take_agent_credential_document
from ..protocol.transport import METHOD_MAP, _call_service, get_state_dir

METHODS = [
    "ping",
    "health",
    "harnesses",
    "accounts",
    "harness-set",
    "quota-redetect",
    "runtime",
    "install",
    "paths",
    "storage-plan",
    "storage-apply",
    "worker-sessions",
    # -- single micro task lifecycle ------------------------------------
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
    # -- work macro tasks: read-only browsing of grouped micro tasks ----------
    "macro-task-list",
    "macro-task-timeline",
    # -- execution records for command/decision infrastructure -----
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
    "messages",
    "message-get",
    "wait-capacity",
    "console",
    "console-snapshot",
    "user-policy-publish",
    "selection-request",
    "selection-get",
    "selection-list",
    "model-catalog-refresh",
    "buddy-list",
    "restart",
    "stop",
]

#: Bound on a ``--params-file`` file or a standard-input parameter document. It is
#: the same 8 MiB the transport accepts for one request, so the CLI refuses an
#: oversized document before any socket is opened.
MAX_PARAMS_BYTES = transport.MAX_MESSAGE_BYTES

EPILOG = """\
examples:
  Input: one JSON object as the positional argument, --params-file PATH or "-" (stdin); the three are mutually exclusive, decoded as UTF-8 and never re-interpreted by a shell. `hey-my-buddy help [METHOD]` prints the methods or one method's validated parameters and starts no service.
  hey-my-buddy submit '{"requestId":"fix-123","hostId":"host-1","task":"...","cwd":"/abs/path","executionWorkspace":{"kind":"existing","access":"write"}}'
      Admit one micro task in an explicit executionWorkspace: kind existing|worktree,
      access read|write|full (default write; full requires accessNote), base {kind:commit|working-tree,ref?}, includeUntracked,
      writeScope and targetRef; the source cwd and the integrator are derived from
      the top-level cwd and the submitting Host.
      Routing: a complete explicit harness/provider/model/effort quadruple is validated
      and delegated directly without a Router call; a partial quadruple is rejected —
      omit every buddy field and let the Router choose, writing the task's
      needs into the task description, or supply the complete quadruple.
      The first response (and a replay that still presents the private submission
      capability) returns the Host control capability; the CLI saves it to a 0600 file
      and prints only its controlFile path. The control token is never printed.

  hey-my-buddy get '{"runId":"<runId>"}'      hey-my-buddy get '{"runId":"<runId>","includeAudit":true}'
      Read the compact micro-task view (control tokens are never exposed) or the full
      audit. `suggest '{"runId":"...","body":"..."}'` records one bounded suggestion
      on the caller's own run. Unknown never means stopped: read `shutdown` before
      treating a micro task as closed.

  hey-my-buddy decide '{"runId":"<runId>","requestId":"req-1","commandId":"cmd-1","expectedRevision":1,"decision":"approve","helpers":[],"controlFile":"/path/from-submit"}'
  hey-my-buddy continue '{"runId":"<runId>","commandId":"cmd-2","expectedRevision":2,"input":"...","helperPolicy":"keep","controlFile":"..."}'
      continue may set access and accessNote for the next turn; omission keeps the current choice.
  hey-my-buddy takeover '{"runId":"<runId>","commandId":"cmd-3","expectedOwnerGeneration":1,"newHostId":"host-2","controlFile":"..."}'
  hey-my-buddy cancel '{"runId":"<runId>","commandId":"cmd-4","note":"...","controlFile":"..."}'
  hey-my-buddy scope-amend '{"runId":"<runId>","commandId":"cmd-5","expectedRevision":3,"expectedScopeVersion":1,"writeScope":["src"],"note":"...","controlFile":"..."}'
  hey-my-buddy conclude '{"runId":"<runId>","note":"failed after the schema change; partial work retained","controlFile":"..."}'   hey-my-buddy reclaim '{"runId":"<runId>","controlFile":"..."}'
      conclude ends a failed, cancelled or delivered-but-unaccepted micro task: unsealed
      managed changes are sealed as an independent Host partial output, the
      micro task enters terminal concluded and the checkout is reclaimed. reclaim retries the
      removal after a blocked reclaim or a keepCheckout acceptance. All three take
      targetRunId for an owned helper and no command number: the same request
      replays, a changed payload conflicts. controlFile injects the control triple;
      no implicit latest-generation lookup. accepted and concluded micro tasks cannot continue.
  hey-my-buddy workspace-resolve '{"runId":"<runId>","commandId":"cmd-6","expectedRevision":4,"conflictId":"...","action":"restore","paths":["file"],"observedFingerprint":"...","controlFile":"..."}'
  hey-my-buddy accept '{"runId":"<runId>","artifactId":"...","note":"inspected the diff and ran the checks","target":{"path":"/abs/target","ref":"main"},"controlFile":"..."}'
  hey-my-buddy accept '{"runId":"<runId>","artifactId":"...","note":"no repository target needed","notRequired":"the artifact needs no repository target","controlFile":"..."}'
      Accept one delivered artifact. The service compares the artifact with the
      target itself; differing paths are refused with their names first, and your
      own adjustments are confirmed with "adjusted":true and a note.
      Optional hostPaths with beforeCommit verify real Host additions; the checkout
      is reclaimed afterwards unless "keepCheckout":true.

  hey-my-buddy await '{"runId":"<runId>"}'      hey-my-buddy await '{"requestId":"fix-123","waitSeconds":3600}'
      Wait without starting, resuming or cancelling work. waitSeconds is at most
      86400 (the default); wait-timeout leaves the run active. Re-await the same
      runId, use status/get for live state, and result only after a receipt exists.
      Cancel a micro task with cancel, or an execution record with execution-cancel.

  Execution records created by the workflow or internal decision infrastructure:
  hey-my-buddy execution-cancel '{"runId":"<runId>","note":"..."}'
  hey-my-buddy execution-retry '{"runId":"<runId>","note":"..."}'
  hey-my-buddy execution-acknowledge '{"runId":"<runId>","note":"...","verdict":"accepted"}'
      Explicit task operations, separate from the micro task lifecycle. A micro-task
      run's execution record additionally needs its Host control.

  hey-my-buddy status '{"runId":"<runId>"}'   hey-my-buddy result '{"runId":"<runId>"}'   hey-my-buddy list '{"limit":20}'
  hey-my-buddy artifacts '{"runId":"<runId>"}'   hey-my-buddy events '{"after":0}'
  hey-my-buddy wait '{"runId":"<runId>","timeoutMs":30000}'   hey-my-buddy watch '{"after":0,"timeoutMs":30000}'
  hey-my-buddy inquire '{"runId":"<runId>"}'
  hey-my-buddy inquire '{"runId":"<runId>","inquiryId":"q1","question":"what is blocking you?","waitMs":20000}'
      Execution observation stays distinct: status, result, list, artifacts and events
      read execution records, wait/watch use the dedicated bounded wait resource, and
      inquire is bounded read-only observation or one correlated question to a live
      agent whose adapter declares inquiry support.

  hey-my-buddy wait-capacity   hey-my-buddy message '{"runId":"<runId>","inquiryId":"q2","question":"status?"}'
  hey-my-buddy message-get '{"runId":"<runId>","inquiryId":"q2"}'

  hey-my-buddy health   hey-my-buddy harnesses   hey-my-buddy runtime   hey-my-buddy console   hey-my-buddy restart   hey-my-buddy stop
      Service control. `hey-my-buddy stop` cancels queued work, durably requests cancellation
      for active attempts, drains for a bounded interval and lists unresolved attempts.
      `hey-my-buddy restart` detaches the daemon while preserving active executions.

  hey-my-buddy console-snapshot '{}'   hey-my-buddy model-catalog-refresh '{"requestId":"first"}'
      Read the bounded evaluation snapshot, or explicitly discover the installed
      harness model catalog. Neither runs a model; discovery never exposes credentials.

  hey-my-buddy buddy-list '{"includeUnavailable":true,"limit":100,"query":"GLM","harness":"zcode"}'
      Search retained buddy identities before pagination; query has at most
      200 characters and 8 whitespace-separated terms. Read nextCursor for more.

  hey-my-buddy selection-request '{"requestId":"pick-1","task":"make the failing parser test pass","requiredCapabilities":["effort:high"]}'
  hey-my-buddy selection-get '{"decisionId":"dec-..."}'
      One durable, bounded selection decision. The default read is a compact Host
      summary carrying selectedBuddy (the frozen execution identity to delegate
      with); add "includeAudit":true for the persisted model input, envelope and
      proposal. A recommendation is recorded history, never an execution permit;
      a queued decision stays cancellable through `execution-cancel` on its runId.

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


_CONTROL_TRIPLE = ("hostId", "ownerGeneration", "controlToken")


_NO_CREDENTIAL_DOCUMENT = object()


def _agent_credential(document=_NO_CREDENTIAL_DOCUMENT) -> str | None:
    """Use only a private attempt credential file or its in-process document."""
    if document is _NO_CREDENTIAL_DOCUMENT:
        document = agent_credential_document()
    return document["token"].strip() if document is not None else None


def _control_path(run_id: str, generation: int, state_dir: Path) -> Path:
    """The private control path for one run and owner generation, with its 0700 directory."""
    directory = state_dir / "controls"
    from ..private_dirs import ensure_private_dir
    ensure_private_dir(directory)
    return control_file_path(state_dir, run_id, generation)


def _save_control(run_id: str, control: dict, state_dir: Path) -> str:
    """Atomically save one Host control capability to its own 0600 file."""
    host_id = control["hostId"]
    generation = control["ownerGeneration"]
    token = control["controlToken"]
    path = _control_path(run_id, generation, state_dir)
    payload = json.dumps(
        {
            "hostId": host_id,
            "ownerGeneration": generation,
            "controlToken": token,
            "runId": run_id,
            "savedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    publish_bytes(path, payload)
    return str(path)


def _read_control(path_value: str) -> dict:
    """Read the Host's named control file; the service validates its control triple."""
    if not isinstance(path_value, str) or not path_value.strip():
        raise BoardError("INVALID_ARGUMENT", "controlFile must be a nonempty path string")
    try:
        path = Path(path_value).expanduser()
        with open_read(path) as handle:
            raw = handle.read(MAX_PARAMS_BYTES + 1)
        if len(raw) > MAX_PARAMS_BYTES:
            raise ValueError("document is too large")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected a JSON object")
        return {field: value[field] for field in _CONTROL_TRIPLE}
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        raise BoardError("INVALID_ARGUMENT", f"Invalid control file: {exc}") from exc


def _apply_control(params: dict) -> dict:
    """Inject the Host control triple from an explicit controlFile or explicit fields.

    There is deliberately no implicit "latest generation" lookup: adopting another
    Host's newest saved capability from a shared cache is never authority. A caller
    either presents the full triple, or names the exact controlFile it was given.
    """
    prepared = dict(params)
    path_value = prepared.pop("controlFile", None)
    if path_value is None:
        return prepared
    control = _read_control(path_value)
    for field in _CONTROL_TRIPLE:
        if field not in prepared:
            prepared[field] = control[field]
    return prepared


def _submission_token(request_id: object, params: dict, state_dir: Path) -> str:
    """The private submission capability, created before the first submit RPC.

    It is persisted under the state directory keyed by requestId, so a retried
    submission recovers the original owner generation while a client that only
    knows the run's public fields can never mint a control capability.
    """
    if not isinstance(request_id, str) or not request_id:
        raise BoardError("INVALID_ARGUMENT", "submit requires requestId before a submission token")
    directory = state_dir / "submissions"
    from ..private_dirs import ensure_private_dir
    ensure_private_dir(directory)
    path = submission_file_path(state_dir, request_id)
    token = secrets.token_hex(32)
    payload = json.dumps({"requestId": request_id, "submissionToken": token}, sort_keys=True).encode("utf-8")
    try:
        publish_bytes(path, payload, immutable=True)
    except FileExistsError:
        return _read_submission_token(path)
    return token


def _read_submission_token(path: Path) -> str:
    from ..install.launcher import read_private
    return read_private(path)["submissionToken"]


def _scrub_and_save(result: dict, state_dir: Path) -> None:
    """Save a returned Host control capability privately and replace its token with the file path."""
    control = result.get("control")
    if control is None or not control.get("controlToken"):
        return
    run_id = result["runId"]
    path = _save_control(run_id, control, state_dir)
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
        try:
            raw = sys.stdin.buffer.read(MAX_PARAMS_BYTES + 1)
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
    """``hey-my-buddy help [METHOD]``: generated from the validators, starting nothing."""
    if len(arguments) > 1:
        _usage_error({"code": "UNKNOWN_METHOD", "message": "hey-my-buddy help takes at most one method name"})
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


def main(argv: list[str] | None = None, *, credential_document=_NO_CREDENTIAL_DOCUMENT) -> int:
    # The CLI emits UTF-8 JSON through pipes as well as the native console.
    # A caller-supplied in-memory text stream has no byte encoding to configure.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", newline="\n")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["help"]:
        return _help_command(argv[1:])
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
        description="hey-my-buddy service: micro tasks, durable execution records and routed harness execution",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("method", metavar="METHOD", help="run `hey-my-buddy help` for every method and its parameters")
    parser.add_argument("params", nargs="?", default=None, help='one JSON object, or "-" to read it from standard input')
    parser.add_argument("--params-file", metavar="PATH", default=None, help='read the JSON object from PATH ("-" for standard input)')
    args = parser.parse_args(argv)
    try:
        params = _parse_parameters(_parameters_text(args))
        if credential_document is _NO_CREDENTIAL_DOCUMENT:
            credential_document = take_agent_credential_document()
        credential = _agent_credential(credential_document)
        scoped_state = credential_state_dir(credential_document)
        state_dir = scoped_state or get_state_dir()
        # `output` is CLI-local: it selects the printed projection and never reaches RPC.
        # Console validates its own local options, so it keeps its complete response.
        mode = cli_views.OUTPUT_FULL if args.method == "console" else cli_views.pop_output_mode(params)
        if args.method == "install":
            from ..install.skill_install import install
            result = install(params)
        elif args.method == "paths":
            from ..install.skill_install import paths
            result = paths(params)
        elif args.method == "console":
            # Console keeps its one canonical JSON argument, but `browser` and `wait`
            # are CLI-local: console_cli validates and strips them, launches the
            # default browser and waits without ever cold-starting a service. An
            # attempt-scoped credential is refused before any local action or RPC.
            from . import console_cli

            result = console_cli.run(params, state_dir=state_dir, credential=credential)
        elif args.method == "await":
            from .blocking import WaitAbandoned, await_run, recovery_commands

            def wait_service(method, parameters, directory):
                return _call_service(method, {**parameters, **({"credential": credential} if credential is not None else {})}, directory)
            try:
                result = await_run(params, state_dir=state_dir, service=wait_service, scoped=credential is not None)
            except WaitAbandoned as abandoned:
                print(_dumps(_abandoned(abandoned, recovery_commands(abandoned.request_id, abandoned.run_id))))
                return 1
        else:
            if credential is not None:
                params["credential"] = credential
            prepared = _apply_control(params)
            if args.method == "submit" and not prepared.get("submissionToken"):
                prepared["submissionToken"] = _submission_token(prepared.get("requestId"), prepared, state_dir)
            result = _call_service(args.method, prepared, state_dir)
            if isinstance(result, dict):
                _scrub_and_save(result, state_dir)
        print(_dumps(cli_views.render(args.method, result, mode)))
        return 1 if isinstance(result, dict) and result.get("error") else 0
    except Exception as error:  # noqa: BLE001 - the CLI converts every failure into one envelope
        payload = error.payload() if isinstance(error, BoardError) else {"code": "SERVICE_ERROR", "message": str(error)}
        print(_dumps({"error": payload}))
        return 1


def _dumps(value) -> str:
    """One compact JSON object: indentation is whitespace a Host model re-reads on every request."""
    from ..protocol.contracts import CONTRACT_VERSION
    if isinstance(value, dict):
        value = {"contractVersion": CONTRACT_VERSION, **value}
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    sys.exit(main())
