"""Accepted micro-task fixes: submission boundary and read projections."""
from ...errors import BoardError


def submission_macro_task(connection, fixed_run_id: str, *, host_id: str | None,
                          lead_run_id: str | None, presentation: dict) -> str | None:
    """Validate the relation once, inside the new micro task's admission transaction."""
    target = connection.execute(
        "SELECT state,host_id,macro_task_id FROM workflow_runs WHERE run_id=?", (fixed_run_id,)
    ).fetchone()
    if target is None:
        raise BoardError("NOT_FOUND", "fixes names an unknown micro task", fixes=fixed_run_id)
    if lead_run_id is not None:
        owned = connection.execute(
            "SELECT 1 FROM workflow_lead_tasks WHERE owned_run_id=? AND lead_run_id=?",
            (fixed_run_id, lead_run_id),
        ).fetchone()
        authorized = owned is not None
    else:
        authorized = target["host_id"] == host_id
    if not authorized:
        raise BoardError("FORBIDDEN", "fixes must name a micro task owned by the submitting Host or Lead")
    if target["state"] != "accepted":
        raise BoardError("INVALID_STATE", "fixes must name an accepted micro task", fixes=fixed_run_id)
    identifier = target["macro_task_id"]
    if lead_run_id is not None:
        selected = connection.execute(
            "SELECT macro_task_id FROM workflow_runs WHERE run_id=?", (lead_run_id,)
        ).fetchone()["macro_task_id"]
    elif "macroTask" in presentation:
        raise BoardError("CONFLICT", "A fix cannot create a different macro task")
    elif "macroTaskId" in presentation:
        selected = presentation["macroTaskId"]
    elif "macroTaskOf" in presentation:
        source = connection.execute(
            "SELECT macro_task_id FROM workflow_runs WHERE run_id=?", (presentation["macroTaskOf"],)
        ).fetchone()
        if source is None:
            raise BoardError("NOT_FOUND", "macroTaskOf names an unknown micro task")
        selected = source["macro_task_id"]
    else:
        selected = identifier
    if selected != identifier:
        raise BoardError("CONFLICT", "A fix must belong to the fixed micro task's macro task")
    return identifier


def view(connection, run_id: str) -> dict:
    """Optional relation fields; insertion order breaks equal timestamp ties."""
    result = {}
    fixed = connection.execute("SELECT fixed_run_id FROM workflow_fixes WHERE run_id=?", (run_id,)).fetchone()
    if fixed is not None:
        result["fixes"] = fixed["fixed_run_id"]
    corrections = connection.execute(
        "SELECT run_id FROM workflow_fixes WHERE fixed_run_id=? ORDER BY created_at,rowid", (run_id,)
    ).fetchall()
    if corrections:
        result["fixedBy"] = [row["run_id"] for row in corrections]
    return result
