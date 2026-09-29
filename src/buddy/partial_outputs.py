"""Preserve a stopped failed attempt before its durable Worker receipt is written."""
from pathlib import Path

from .errors import BoardError


def capture(context, outcome):
    if not outcome.shutdown_confirmed or outcome.status not in ("failed", "cancelled") or not context.turn_input:
        return
    payload = outcome.result
    if payload.get("modelStarted") is False:
        return
    manifest = getattr(context, "effective_workspace", None) or context.turn_input.get("executionWorkspace")
    state = context.environment.get("BUDDY_STATE_DIR")
    if not manifest or not state:
        return
    try:
        from .workflow import workspace_module
        seal = payload.pop("workspaceSeal", None) or workspace_module().seal(
            Path(state), manifest, context.task_id, context.attempt_id)
        # A protocol preflight with unknown model state must not become model work.
        # Changed managed files are concrete work evidence even after a lost stream.
        if seal and (payload.get("modelStarted") is True or seal.get("changedPaths")):
            payload["partialWorkspaceSeal"] = seal
            payload["workspaceManifest"] = manifest
            payload["partialOutput"] = {"partial": True, "verified": False, "final": False,
                                        "reason": payload.get("code") or outcome.status}
    except BoardError as error:
        payload["partialSealError"] = {"code": error.code, "message": error.message}


def enrich(context, outcome):
    capture(context, outcome)
    if not outcome.shutdown_confirmed or not context.turn_input:
        return
    manifest = getattr(context, "effective_workspace", None) or context.turn_input.get("executionWorkspace")
    original = context.turn_input.get("context", {}).get("goalInputCommit")
    seal = outcome.result.get("workspaceSeal") or outcome.result.get("partialWorkspaceSeal")
    if not manifest or not original or not seal:
        return
    try:
        from .workflow import workspace_module
        seal["cumulativePatch"] = workspace_module().cumulative_patch(manifest, seal, original)
    except BoardError as error:
        outcome.status = "failed"
        outcome.error = "Cumulative patch could not be preserved: " + error.message
        outcome.result["cumulativePatchError"] = error.payload()
        capture(context, outcome)
