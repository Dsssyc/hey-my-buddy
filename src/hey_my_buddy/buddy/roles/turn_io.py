"""Private micro task-turn files and workspaces shared by coding harnesses."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ...private_files import publish_bytes
from ...errors import BoardError
from ...protocol.usage import MAX_ASSISTANT_MESSAGE_BYTES
from ...json_codec import canonical_json  # noqa: F401 - the one shared canonical JSON
from ...private_dirs import context_root, ensure_private_dir
from ..harnesses.base import ExecutionContext
from ..harnesses.session_receipts import MAX_OUTCOME_BYTES  # noqa: F401 - the shared byte bound

MAX_RECORD_BYTES = 98304


def input_hash(value: dict) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def private_json(path: Path, value: object, *, exclusive: bool = False) -> None:
    """Publish a complete private JSON fact through the shared publisher."""
    publish_bytes(path, canonical_json(value).encode(), immutable=exclusive)


def prepare_turn(context: ExecutionContext) -> None:
    if not context.environment.get("HMB_STATE_DIR"):
        raise BoardError("INVALID_ARGUMENT", "The turn requires its private state directory")
    verify_workspace(context)
    ensure_private_dir(context.directory)
    publish_bytes(context.task_file(), context.spec["task"].encode())
    write_turn_files(context)


def write_turn_files(context: ExecutionContext) -> None:
    private_json(context.turn_input_file(), context.turn_input)
    if context.agent_credential:
        ensure_private_dir(context_root(context))
        private_json(context.credential_file(), {
            "token": context.agent_credential, "taskId": context.task_id,
            "attemptId": context.attempt_id, "generation": context.generation, "turnId": context.turn_id,
            "stateDir": context.environment["HMB_STATE_DIR"],
        })


def inquiry_paths(context: ExecutionContext) -> dict:
    """The durable checkpoint journal of one attempt; live credentials stay in memory."""
    private_root = ensure_private_dir(context_root(context))
    ensure_private_dir(context.directory)
    return {"directory": private_root, "resultsPath": str(context.directory / "inquiry.results.jsonl")}


def verify_workspace(context: ExecutionContext) -> None:
    manifest = context.turn_input["executionWorkspace"]
    from ...blackboard.tasks.workflow import workspace_module
    routing = (context.turn_input.get("context") or {}).get("routing") or {}
    workspace_module().verify(manifest, require_unchanged=bool(routing.get("decisionId")))
    context.effective_workspace = manifest


def validate_outcome(outcome: object, *, delegation_authority: bool = False) -> str | None:
    fields = {"disposition", "summary", "remaining", "decisions", "artifacts", "request"}
    if not isinstance(outcome, dict) or set(outcome) != fields:
        return "the outcome must contain exactly the current outcome fields"
    def text(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip()) and "\0" not in value
    def strings(value: object) -> bool:
        return isinstance(value, list) and all(text(x) for x in value)
    dispositions = ("completed", "assistance", "attention", "waiting") if delegation_authority else (
        "completed", "assistance", "attention")
    if outcome["disposition"] not in dispositions:
        return "the turn outcome requires a valid disposition and nonblank summary"
    if not text(outcome["summary"]):
        return "the turn summary is blank or contains NUL"
    if not strings(outcome["remaining"]) or not strings(outcome["decisions"]):
        return "remaining and decisions must be nonblank string arrays without NUL"
    artifacts = outcome["artifacts"]
    if not isinstance(artifacts, list):
        return "artifacts must be an array"
    try:
        if any(not (text(x) or isinstance(x, dict) and bool(x)) for x in artifacts):
            return "artifacts must contain nonempty references"
        if len(canonical_json(outcome).encode()) > MAX_OUTCOME_BYTES:
            return "the outcome exceeds its byte bound"
    except (TypeError, ValueError, RecursionError):
        return "the outcome must contain finite JSON values"
    request = outcome["request"]
    if outcome["disposition"] in ("completed", "waiting"):
        return None if request is None else f"a {outcome['disposition']} outcome requires request: null"
    required = {"summary", "attempted", "neededWork", "expectedArtifacts", "acceptance"}
    if not isinstance(request, dict) or not required <= set(request) or set(request) - required - {"suggestedBuddyId"}:
        return "an assistance or attention outcome requires the current request fields"
    if any(not text(request[k]) for k in ("summary", "attempted", "neededWork", "acceptance")):
        return "the turn request requires nonblank summary, attempted, neededWork and acceptance"
    # An explicit ``suggestedBuddyId: null`` is the honest spelling of "no
    # suggestion" and equals an absent key; every other non-string value stays
    # refused so the field can never smuggle an unbounded payload.
    if not strings(request["expectedArtifacts"]) or (
            "suggestedBuddyId" in request and request["suggestedBuddyId"] is not None
            and (not text(request["suggestedBuddyId"]) or len(request["suggestedBuddyId"].encode()) > 256)):
        return "the turn request references are invalid"
    return None


def seal_workspace(context: ExecutionContext) -> tuple[dict | None, str | None]:
    manifest = context.effective_workspace
    state_dir = context.environment["HMB_STATE_DIR"]
    try:
        from ...blackboard.tasks.workflow import workspace_module
        seal = workspace_module().seal(Path(state_dir), manifest, context.task_id, context.attempt_id)
    except BoardError as error:
        return None, f"{error.code}: {error.message}"
    if not isinstance(seal, dict) or not seal.get("manifestSha256"):
        return None, "the workspace seal returned no immutable manifest"
    return seal, None


# Native checkpoint association is a role-owned receipt/continuation check.
def validated_checkpoint(payload: dict, turn_input: dict) -> dict | None:
    """Read only a stopped, exact-attempt native observation from its receipt."""
    value = payload.get("nativeCheckpoint")
    if not isinstance(value, dict) or value.get("version") != 1:
        return None
    expected = {key: turn_input[key] for key in ("taskId", "attemptId", "generation", "turnId")}
    expected["inputSha256"] = input_hash(turn_input)
    if any(type(value.get(key)) is not type(item) or value.get(key) != item for key, item in expected.items()):
        return None
    process = payload.get("processState")
    if (not isinstance(process, dict) or process.get("shutdownConfirmed") is not True
            or value.get("nativeTurnStarted") is not True
            or value.get("nativeTurnStatus") not in ("completed", "failed", "interrupted", "incomplete")
            or type(value.get("bindingSaved")) is not bool
            or type(value.get("eventSeq")) is not int or value["eventSeq"] < 2):
        return None
    for key in ("sessionId", "nativeTurnId"):
        if not isinstance(value.get(key), str) or not value[key] or value[key] != payload.get(key):
            return None
    message = value.get("lastAssistantMessage")
    if message is not None:
        if (not isinstance(message, dict) or not isinstance(message.get("itemId"), str) or not message["itemId"]
                or not isinstance(message.get("text"), str) or type(message.get("truncated")) is not bool
                or type(message.get("sourceBytes")) is not int
                or not isinstance(message.get("sha256"), str) or len(message["sha256"]) != 64
                or len(canonical_json(message["text"]).encode()) > MAX_ASSISTANT_MESSAGE_BYTES):
            return None
        raw = message["text"].encode()
        if message["sourceBytes"] < len(raw) or (not message["truncated"] and (
                message["sourceBytes"] != len(raw) or hashlib.sha256(raw).hexdigest() != message["sha256"])):
            return None
    return value

def checkpoint_resumable(payload: dict, checkpoint: dict) -> bool:
    process = payload.get("processState")
    return (checkpoint.get("nativeTurnStatus") == "completed" and checkpoint.get("bindingSaved") is True
            and isinstance(process, dict) and type(process.get("nativeExitCode")) is int and process["nativeExitCode"] == 0)
