"""Persisted work-unit pauses and their recorded execution stop intent."""
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


def stop_request(connection, attempt):
    """A pause action is durable even when resume removes its admission fence."""
    if attempt['stop_initiator'] != 'console' or attempt['cancel_requested_at'] is None:
        return None
    return connection.execute(
        f"SELECT e.* FROM events e JOIN ({TASK_UNITS_SQL}) u"
        " ON u.group_id=json_extract(e.payload_json,'$.macroTaskId')"
        " WHERE u.task_id=? AND e.kind='macro_task.paused'"
        " AND json_extract(e.payload_json,'$.pausedAt')=? ORDER BY e.seq DESC LIMIT 1",
        (attempt['task_id'], attempt['cancel_requested_at']),
    ).fetchone()


def reject_ending(connection, run_id: str) -> None:
    from ...errors import BoardError
    if connection.execute(
        "SELECT 1 FROM events e JOIN json_each(e.payload_json,'$.runIds') r ON r.value=?"
        " JOIN workflow_runs w ON w.run_id=r.value AND w.state NOT IN ('accepted','concluded','delivered')"
        " WHERE e.kind='macro_task.ended' AND NOT EXISTS(SELECT 1 FROM commands c"
        " WHERE c.command_id=json_extract(e.payload_json,'$.commandId')) LIMIT 1", (run_id,),
    ).fetchone() is not None:
        raise BoardError('CONFLICT', 'A console end is still settling this micro task', runId=run_id)
