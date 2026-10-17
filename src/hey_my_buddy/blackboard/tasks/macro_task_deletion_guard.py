"""Admission checks for a macro task whose deletion has begun."""
from __future__ import annotations

from ...errors import BoardError


def reject_macro_task(connection, macro_task_id: str) -> None:
    """Fence an explicit or inherited macro task inside its admission transaction."""
    deletion = connection.execute(
        "SELECT state,deleted_at FROM macro_task_deletions"
        " WHERE target_kind='macro-task' AND target_id=?",
        (macro_task_id,),
    ).fetchone()
    if deletion is not None:
        raise BoardError(
            "MACRO_TASK_DELETED", "This macro task cannot accept more micro tasks",
            macroTaskId=macro_task_id, state=deletion["state"],
            deletedAt=deletion["deleted_at"], resolution="create-new-macro-task",
        )


def reject_deleting_run(connection, run_id: str) -> None:
    """Fence a live run in either a macro task or a standalone tree being deleted."""
    deletion = connection.execute(
        "WITH RECURSIVE ancestors(run_id) AS ("
        " SELECT ? UNION SELECT c.parent_run_id FROM workflow_children c"
        " JOIN ancestors a ON c.child_task_id=a.run_id"
        " UNION SELECT l.lead_run_id FROM workflow_lead_tasks l"
        " JOIN ancestors a ON l.owned_run_id=a.run_id)"
        " SELECT d.deletion_id FROM macro_task_deletions d"
        " LEFT JOIN workflow_runs r ON r.run_id=?"
        " WHERE d.state='deleting' AND"
        " ((d.target_kind='macro-task' AND d.target_id=r.macro_task_id)"
        " OR (d.target_kind='micro-task-tree' AND d.target_id IN"
        " (SELECT 'run:' || run_id FROM ancestors)))"
        " LIMIT 1",
        (run_id, run_id),
    ).fetchone()
    if deletion is not None:
        raise BoardError("TASK_DELETING", "This micro task is being deleted",
                         deletionId=deletion["deletion_id"])
