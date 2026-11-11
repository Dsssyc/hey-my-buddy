"""Persisted work-unit admission pauses."""
from __future__ import annotations

from . import macro_tasks, micro_task


TASK_UNITS_SQL = f"""
WITH owners AS ({macro_tasks.RUN_GROUPS_SQL}),
sources AS (
    SELECT run_id AS task_id,run_id FROM workflow_runs
    UNION SELECT task_id,run_id FROM ({micro_task.ROUTING_TASKS_SQL})
)
SELECT sources.task_id,owners.group_id FROM sources JOIN owners ON owners.run_id=sources.run_id
"""


def for_task(connection, task_id: str):
    return connection.execute(
        f"SELECT p.* FROM work_pauses p JOIN ({TASK_UNITS_SQL}) u"
        " ON u.group_id=COALESCE(p.macro_task_id,'run:'||p.run_id) WHERE u.task_id=?",
        (task_id,),
    ).fetchone()
