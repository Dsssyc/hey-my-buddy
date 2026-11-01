"""Admission checks for a macro task whose deletion has begun."""
from __future__ import annotations

from pathlib import Path

from ...errors import BoardError


def reject_macro_task(connection, macro_task_id: str) -> None:
    """Fence an explicit or inherited macro task inside its admission transaction."""
    deletion = connection.execute(
        "SELECT 1 FROM macro_task_deletions"
        " WHERE target_kind='macro-task' AND target_id=?",
        (macro_task_id,),
    ).fetchone()
    if deletion is not None:
        raise BoardError(
            "MACRO_TASK_DELETING", "This macro task cannot accept more micro tasks",
            macroTaskId=macro_task_id, resolution="create-new-macro-task",
        )


def _deletion_for_run(connection, run_id: str):
    return connection.execute(
        "WITH RECURSIVE ancestors(run_id) AS ("
        " SELECT ? UNION SELECT c.parent_run_id FROM workflow_children c"
        " JOIN ancestors a ON c.child_task_id=a.run_id"
        " UNION SELECT l.lead_run_id FROM workflow_lead_tasks l"
        " JOIN ancestors a ON l.owned_run_id=a.run_id)"
        " SELECT d.deletion_id FROM macro_task_deletions d"
        " LEFT JOIN workflow_runs r ON r.run_id=?"
        " WHERE"
        " ((d.target_kind='macro-task' AND d.target_id=r.macro_task_id)"
        " OR (d.target_kind='micro-task-tree' AND d.target_id IN"
        " (SELECT 'run:' || run_id FROM ancestors)))"
        " LIMIT 1",
        (run_id, run_id),
    ).fetchone()


def reject_deleting_run(connection, run_id: str) -> None:
    """Fence a live run in either a macro task or a standalone tree being deleted."""
    deletion = _deletion_for_run(connection, run_id)
    if deletion is not None:
        raise BoardError("TASK_DELETING", "This micro task is being deleted",
                         deletionId=deletion["deletion_id"])


def reject_deleting_checkout(connection, manifest: dict, state_dir: Path) -> None:
    """Fence claims on the physical worktree allocation of a deleting run."""
    if connection.execute("SELECT 1 FROM macro_task_deletions LIMIT 1").fetchone() is None:
        return
    checkout_root = manifest["checkoutRoot"]
    try:
        allocation = Path(checkout_root).relative_to(state_dir / "workspaces")
    except ValueError:
        return
    if len(allocation.parts) < 2 or allocation.parts[1] != "checkout":
        return
    workspace_id = allocation.parts[0]
    owned_root = str(state_dir / "workspaces" / workspace_id / "checkout")
    owners = connection.execute(
        "SELECT DISTINCT a.run_id"
        " FROM workflow_artifacts a WHERE a.kind='input'"
        " AND json_extract(a.manifest_json, '$.kind')='worktree'"
        " AND json_extract(a.manifest_json, '$.checkoutRoot')=?"
        " AND json_extract(a.manifest_json, '$.workspaceId')=?",
        (owned_root, workspace_id),
    ).fetchall()
    for owner in owners:
        deletion = _deletion_for_run(connection, owner["run_id"])
        if deletion is not None:
            raise BoardError(
                "PREPARATION_CONFLICT",
                "This managed checkout belongs to a micro task being deleted",
                checkoutRoot=checkout_root, ownerRunId=owner["run_id"],
                deletionId=deletion["deletion_id"],
            )
