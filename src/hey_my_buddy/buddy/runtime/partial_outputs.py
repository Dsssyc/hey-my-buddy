"""Preserve a stopped failed attempt before its durable Worker receipt is written."""
from pathlib import Path

from ...errors import BoardError


def capture(context, outcome):
    if not outcome.shutdown_confirmed or outcome.status not in ("failed", "cancelled") or not context.turn_input:
        return
    payload = outcome.result
    if payload.get("modelStarted") is False:
        return
    manifest = getattr(context, "effective_workspace", None) or context.turn_input.get("executionWorkspace")
    state = context.environment.get("HMB_STATE_DIR")
    if not manifest or not state:
        return
    try:
        from ...blackboard.tasks.workflow import workspace_module
        seal = payload.pop("workspaceSeal", None) or workspace_module().seal(
            Path(state), manifest, context.task_id, context.attempt_id)
        # A partial output is work evidence, not a process record: the runners
        # set modelStarted before the request is sent, so it only guards retry
        # and never proves work. Changed managed files are the concrete evidence.
        if seal and seal.get("changedPaths"):
            payload["partialWorkspaceSeal"] = seal
            payload["workspaceManifest"] = manifest
            payload["partialOutput"] = {"partial": True, "verified": False, "final": False}
    except BoardError as error:
        payload["partialSealError"] = error.payload()


def enrich(context, outcome):
    capture(context, outcome)
    if not outcome.shutdown_confirmed or not context.turn_input:
        return
    manifest = getattr(context, "effective_workspace", None) or context.turn_input.get("executionWorkspace")
    original = context.turn_input.get("context", {}).get("microTaskInputCommit")
    seal = outcome.result.get("workspaceSeal") or outcome.result.get("partialWorkspaceSeal")
    if not manifest or not original or not seal:
        return
    try:
        from ...blackboard.tasks.workflow import workspace_module
        seal["cumulativePatch"] = workspace_module().cumulative_patch(manifest, seal, original)
    except BoardError as error:
        outcome.status = "failed"
        patch_error = "Cumulative patch could not be preserved: " + error.message
        outcome.error = f"{outcome.error}; {patch_error}" if outcome.error else patch_error
        outcome.result["cumulativePatchError"] = error.payload()
        capture(context, outcome)
