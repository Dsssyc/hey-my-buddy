"""Read projections of accepted micro-task fixes."""


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
