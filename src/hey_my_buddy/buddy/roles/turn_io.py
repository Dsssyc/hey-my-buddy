"""Private governed-turn files and workspaces shared by coding harnesses."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import secrets
import uuid
from pathlib import Path
from typing import Callable

from ...errors import BoardError
from ...json_codec import canonical_json  # noqa: F401 - the one shared canonical JSON
from ...private_dirs import context_root, ensure_private_dir, linked
from ..harnesses.base import ExecutionContext
from ..harnesses.session_receipts import MAX_OUTCOME_BYTES  # noqa: F401 - the shared byte bound

MAX_INPUT_BYTES = 262144
MAX_RECORD_BYTES = 98304

#: Bounded, shared capability hints. A coding harness prompt carries exactly these
#: trigger conditions so a Worker ends its turn with assistance/attention instead of
#: silently overreaching, and so it asks the Host for an authorized helper/reviewer
#: rather than creating a peer Buddy itself. The DSH prompt section
#: (harnesses/dsh/plugins/turn-result.mjs) states the same triggers in the agent's
#: own system prompt; this tuple is the harness-neutral wording both sides keep.
ASSISTANCE_HINTS = (
    "End your turn with assistance or attention instead of guessing when any of these is true: "
    "the work needs files or permissions outside the authorized scope; validation keeps failing and "
    "you have no further evidence for the next step; the assigned capability clearly does not fit; "
    "or the Host/user agreed review condition has been reached.",
    "Ask the Host for help through your turn outcome, not by acting outside scope. Put what you "
    "already tried in attempted, the exact work you need in neededWork and the acceptance condition "
    "in acceptance, with expectedArtifacts listing the fixed artifacts the help must produce; a "
    "suggestedProfileId is only a suggestion the Host may ignore.",
    "You may not create or dispatch another Buddy task, worker or peer job yourself, and you must "
    "never claim that a reviewer or helper already ran. The Host decides whether to handle the work, "
    "authorize a helper or continue you; your internal subagents remain available for work inside "
    "this authorized scope.",
)


def input_hash(value: dict) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def guard_private_path(path: Path) -> Path:
    """Inspect every component without resolving away a hostile link/reparse point."""
    path = Path(path).absolute()
    # Only canonicalize the macOS root aliases, as the private-directory API does.
    if sys.platform == "darwin" and len(path.parts) > 1 and path.parts[1] in ("var", "tmp"):
        path = Path("/private", *path.parts[1:])
    for component in (*reversed(path.parents), path):
        if linked(component):
            raise BoardError("PRIVATE_PATH_UNSAFE", "Private path contains a linked component", path=str(component))
    return path


def private_json(path: Path, value: object, *, exclusive: bool = False) -> None:
    """Publish ordinary JSON without following links, including any parent component."""
    _private_bytes(path, canonical_json(value).encode(), exclusive=exclusive)


def _private_bytes(path: Path, raw: bytes, *, exclusive: bool = False) -> None:
    path = guard_private_path(path)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(metadata.st_mode):
            raise BoardError("PRIVATE_PATH_UNSAFE", "Private artifact target is not a regular file", path=str(path))
    target = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    parent = None
    # Pin each POSIX directory without following links so a replacement after the
    # component checks cannot redirect the temporary file or its publication.
    if os.name != "nt":
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        parent = os.open(path.anchor, flags)
        try:
            for part in path.parent.parts[1:]:
                child = os.open(part, flags, dir_fd=parent)
                os.close(parent)
                parent = child
        except BaseException:
            os.close(parent)
            raise
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    created = False
    try:
        guard_private_path(target)
        fd = os.open(target.name if parent is not None else target, flags, 0o600,
                     **({"dir_fd": parent} if parent is not None else {}))
        created = True
        with os.fdopen(fd, "wb") as stream:
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), 0o600)
            else:
                guard_private_path(target)
                os.chmod(target, 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        guard_private_path(path)
        if exclusive:
            os.link(target.name if parent is not None else target, path.name if parent is not None else path,
                    **({"src_dir_fd": parent, "dst_dir_fd": parent} if parent is not None else {}))
        else:
            os.replace(target.name if parent is not None else target, path.name if parent is not None else path,
                       **({"src_dir_fd": parent, "dst_dir_fd": parent} if parent is not None else {}))
        if parent is not None:
            os.fsync(parent)
    finally:
        try:
            if created:
                if parent is not None:
                    try:
                        os.unlink(target.name, dir_fd=parent)
                    except FileNotFoundError:
                        pass
                else:
                    guard_private_path(target)
                    target.unlink(missing_ok=True)
        finally:
            if parent is not None:
                os.close(parent)


def prepare_turn(context: ExecutionContext) -> None:
    ensure_private_dir(context.directory)
    _private_bytes(context.task_file(), context.spec["task"].encode())
    write_turn_files(context)
    verify_workspace(context)


def write_turn_files(context: ExecutionContext) -> None:
    if context.turn_input is None:
        return
    if len(canonical_json(context.turn_input).encode()) > MAX_INPUT_BYTES:
        raise BoardError("INVALID_ARGUMENT", "the governed turn input exceeds its byte bound")
    private_json(context.turn_input_file(), context.turn_input)
    if context.agent_credential:
        ensure_private_dir(context_root(context, getattr(context, "private_adapter", None)))
        private_json(context.credential_file(), {
            "token": context.agent_credential, "taskId": context.task_id,
            "attemptId": context.attempt_id, "generation": context.generation, "turnId": context.turn_id,
        })
        context.environment["BUDDY_AGENT_CREDENTIAL_FILE"] = str(context.credential_file())
        context.environment["BUDDY_TASK_ID"] = context.task_id
        context.environment["BUDDY_ATTEMPT_ID"] = context.attempt_id


def workspace_cwd(context: ExecutionContext) -> str:
    manifest = getattr(context, "effective_workspace", None)
    return str(manifest["path"]) if isinstance(manifest, dict) and manifest.get("path") else context.cwd


#: ``sun_path`` budget for a private Unix socket. The attempt directory can be
#: deep, so callers fall back to a short temp directory and keep the credentials
#: (not the socket) in the harness-private attempt directory the service reads.
UNIX_SOCKET_PATH_BUDGET = 105 if sys.platform.startswith("linux") else 101


def inquiry_paths(context: ExecutionContext) -> dict:
    """Owner-private paths and token for one attempt's inquiry bridge.

    The credentials file always lives in the harness-private attempt directory so the service can
    find it; only the socket may move to a short temp directory. Every directory is
    created 0700 and the token is hex so it can never be parsed as an option.
    """
    private_root = ensure_private_dir(context_root(context, getattr(context, "private_adapter", None)))
    candidates = [
        private_root,
        Path("/tmp") / "hey-my-buddy-inquiry" / context.attempt_id,
        Path(os.environ.get("TMPDIR", "/tmp")) / "hey-my-buddy-inquiry" / context.attempt_id,
    ]
    directory = next(
        (candidate for candidate in candidates if len(str(candidate / "inquiry.sock").encode()) <= UNIX_SOCKET_PATH_BUDGET),
        candidates[-1],
    )
    directory = ensure_private_dir(directory)
    credentials = {
        "socketPath": str(directory / "inquiry.sock"),
        "resultsPath": str(context.directory / "inquiry.results.jsonl"),
        "errorPath": str(context.directory / "inquiry.sock.error.json"),
        "token": secrets.token_hex(32),
    }
    ensure_private_dir(context.directory)
    credentials_path = private_root / "inquiry.json"
    private_json(credentials_path, credentials)
    return {"directory": directory, **credentials}


def verify_workspace(context: ExecutionContext) -> None:
    manifest = (context.turn_input or {}).get("executionWorkspace")
    if not isinstance(manifest, dict) or not manifest:
        context.effective_workspace = None
        return
    from ...blackboard.tasks.workflow import workspace_module
    routing = (context.turn_input.get("context") or {}).get("routing") or {}
    workspace_module().verify(manifest, require_unchanged=manifest.get("access") == "read" or bool(routing.get("decisionId")))
    context.effective_workspace = manifest


def validate_outcome(outcome: object) -> str | None:
    fields = {"disposition", "summary", "remaining", "decisions", "artifacts", "request"}
    if not isinstance(outcome, dict) or set(outcome) != fields:
        return "the outcome must contain exactly the current outcome fields"
    def text(value: object, limit: int = 8000) -> bool:
        return isinstance(value, str) and bool(value.strip()) and "\0" not in value and len(value.encode()) <= limit
    def strings(value: object) -> bool:
        return isinstance(value, list) and len(value) <= 32 and all(text(x, 4096) for x in value)
    if outcome["disposition"] not in ("completed", "assistance", "attention"):
        return "the turn outcome requires a valid disposition and nonblank summary"
    # A report can occupy the bounded outcome. Request text and references keep
    # their smaller bounds; JSON framing and all other fields still count below.
    if not text(outcome["summary"], MAX_OUTCOME_BYTES):
        return "the turn summary is blank, contains NUL or exceeds its byte bound"
    if not strings(outcome["remaining"]) or not strings(outcome["decisions"]):
        return "remaining and decisions must be bounded string arrays"
    artifacts = outcome["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) > 32:
        return "artifacts must be a bounded array"
    try:
        if any(not (text(x, 4096) or isinstance(x, dict) and bool(x)) or len(canonical_json(x).encode()) > 4096 for x in artifacts):
            return "artifacts must contain bounded nonempty references"
        if len(canonical_json(outcome).encode()) > MAX_OUTCOME_BYTES:
            return "the outcome exceeds its byte bound"
    except (TypeError, ValueError, RecursionError):
        return "the outcome must contain finite JSON values"
    request = outcome["request"]
    if outcome["disposition"] == "completed":
        return None if request is None else "a completed outcome requires request: null"
    required = {"summary", "attempted", "neededWork", "expectedArtifacts", "acceptance"}
    if not isinstance(request, dict) or not required <= set(request) or set(request) - required - {"suggestedProfileId"}:
        return "an assistance or attention outcome requires the current request fields"
    if any(not text(request[k]) for k in ("summary", "attempted", "neededWork", "acceptance")):
        return "the turn request requires nonblank summary, attempted, neededWork and acceptance"
    # An explicit ``suggestedProfileId: null`` is the honest spelling of "no
    # suggestion" and equals an absent key; every other non-string value stays
    # refused so the field can never smuggle an unbounded payload.
    if not strings(request["expectedArtifacts"]) or (
            "suggestedProfileId" in request and request["suggestedProfileId"] is not None
            and not text(request["suggestedProfileId"], 256)):
        return "the turn request references are invalid"
    return None


def read_turn(context: ExecutionContext, shutdown_confirmed: bool, exit_code: int | None,
              validate_provenance: Callable[[dict], str | None] | None = None) -> tuple[dict | None, str | None]:
    turn_input = context.turn_input
    if turn_input is None:
        return None, None
    if exit_code != 0:
        return None, "the runner did not exit zero"
    if not shutdown_confirmed:
        return None, "runner shutdown is not confirmed, so no turn outcome is imported"
    try:
        with context.turn_output_file().open("rb") as stream:
            raw = stream.read(MAX_RECORD_BYTES + 1)
        if len(raw) > MAX_RECORD_BYTES:
            return None, "the turn record exceeds its byte bound"
        record = json.loads(raw)
    except (OSError, ValueError):
        return None, "the governed runner wrote no valid JSON turn output"
    if not isinstance(record, dict) or record.get("version") != 1:
        return None, "the turn output version is not supported"
    expected = {"taskId": context.task_id, "attemptId": context.attempt_id,
                "generation": context.generation, "turnId": context.turn_id,
                "resumeMode": turn_input.get("resumeMode"), "previousSessionId": turn_input.get("previousSessionId")}
    for key, value in expected.items():
        if record.get(key) != value:
            return None, f"the turn record {key} does not match the service-owned execution identity"
    if record.get("inputSha256") != input_hash(turn_input):
        return None, "the turn record inputSha256 does not match the turn input the service wrote"
    error = validate_outcome(record.get("outcome"))
    if error:
        return None, error
    if not isinstance(record.get("sessionId"), str) or not record["sessionId"]:
        return None, "the turn record carries no session identity"
    if validate_provenance is None:
        from ..harnesses.registry import adapter
        validate_provenance = adapter(context.spec["adapter"]).validate_turn_provenance
    error = validate_provenance(record)
    return (None, error) if error else (record, None)


def seal_workspace(context: ExecutionContext) -> tuple[dict | None, str | None]:
    manifest = getattr(context, "effective_workspace", None) or (context.turn_input or {}).get("executionWorkspace")
    if not isinstance(manifest, dict) or not manifest:
        return None, None
    state_dir = context.environment.get("BUDDY_STATE_DIR")
    if not state_dir:
        return None, "the attempt has no private state directory to seal into"
    try:
        from ...blackboard.tasks.workflow import workspace_module
        seal = workspace_module().seal(Path(state_dir), manifest, context.task_id, context.attempt_id)
    except BoardError as error:
        return None, f"{error.code}: {error.message}"
    if not isinstance(seal, dict) or not seal.get("manifestSha256"):
        return None, "the workspace seal returned no immutable manifest"
    return seal, None
