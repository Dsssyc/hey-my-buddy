"""Private micro task-turn files and workspaces shared by coding harnesses."""
from __future__ import annotations

from pathlib import Path

from ...private_files import publish_bytes
from ...errors import BoardError
from ...protocol.turn_input import NONE_TURN_INPUT_VERSION
from ...protocol.workspace_manifest import turn_manifest
from ...json_codec import canonical_json
from ...private_dirs import context_root, ensure_private_dir
from ..harnesses.base import ExecutionContext

MAX_RECORD_BYTES = 98304


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
    manifest = turn_manifest(Path(context.environment["HMB_STATE_DIR"]), context.turn_input)
    if context.turn_input["version"] == NONE_TURN_INPUT_VERSION:
        context.effective_workspace = manifest
        return
    from ...blackboard.tasks.workflow import workspace_module
    routing = (context.turn_input.get("context") or {}).get("routing") or {}
    workspace_module().verify(manifest, state_dir=Path(context.environment["HMB_STATE_DIR"]),
                              require_unchanged=bool(routing.get("decisionId")))
    context.effective_workspace = manifest


def seal_workspace(context: ExecutionContext) -> tuple[dict | None, str | None]:
    manifest = context.effective_workspace
    if context.turn_input["version"] == NONE_TURN_INPUT_VERSION:
        return None, None
    state_dir = context.environment["HMB_STATE_DIR"]
    try:
        from ...blackboard.tasks.workflow import workspace_module
        seal = workspace_module().seal(Path(state_dir), manifest, context.task_id, context.attempt_id)
    except BoardError as error:
        return None, f"{error.code}: {error.message}"
    if not isinstance(seal, dict) or not seal.get("manifestSha256"):
        return None, "the workspace seal returned no immutable manifest"
    return seal, None
