"""Durable message claims and the common readiness test for await and Lead."""
from __future__ import annotations

import json

from ...errors import BoardError


def issue_occurrences(connection, task_id: str):
    """An issue's initial event identifies its lifetime, even after resolution.

    Issue updates have their own event kind and retain the initial event. On an
    upgraded board the latest old observation names the still-open occurrence;
    no wait claimed issues before this schema introduced delivery receipts.
    """
    return connection.execute(
        "SELECT 'execution' AS issue_kind, MAX(e.seq) AS occurrence_seq"
        " FROM execution_issues i JOIN attempts a ON a.attempt_id=i.attempt_id"
        " JOIN events e ON e.attempt_id=i.attempt_id AND e.kind='execution.issue'"
        " AND json_extract(e.payload_json,'$.stage')=i.stage WHERE a.task_id=?"
        " GROUP BY i.attempt_id,i.stage"
        " UNION ALL SELECT 'service',MAX(e.seq) FROM service_issues i"
        " JOIN events e ON e.kind='service.issue' AND e.attempt_id IS NULL AND json_extract(e.payload_json,'$.stage')=i.stage"
        " GROUP BY i.stage ORDER BY occurrence_seq", (task_id,),
    ).fetchall()


def replay_issue(connection, task_id: str, wait_id: str):
    row = connection.execute('SELECT * FROM issue_deliveries WHERE wait_id=?', (wait_id,)).fetchone()
    if row is not None and row['task_id'] != task_id:
        raise BoardError('CONFLICT', 'reminderWaitId belongs to a different run')
    return row


def pending_issue(connection, task_id: str, *, after: int = 0):
    for row in issue_occurrences(connection, task_id):
        if row['occurrence_seq'] > after and connection.execute(
            'SELECT 1 FROM issue_deliveries WHERE task_id=? AND issue_kind=? AND occurrence_seq=?',
            (task_id, row['issue_kind'], row['occurrence_seq']),
        ).fetchone() is None:
            return row
    return None


def _issue_message(connection, row) -> dict:
    event = connection.execute('SELECT * FROM events WHERE seq=?', (row['occurrence_seq'],)).fetchone()
    payload = json.loads(event['payload_json'])
    detail = {key: payload[key] for key in ('stage', 'code', 'message')}
    detail['observedAt'] = event['created_at']
    if row['issue_kind'] == 'execution':
        detail['attemptId'] = event['attempt_id']
    return {'issue': {'kind': row['issue_kind'], 'detail': detail}}


def consume(store, selector: dict) -> dict:
    """Choose one message atomically, including concurrent retries of one await."""
    from . import reminders
    with store.db.write() as connection:
        task = store._task_row(connection, selector)
        receipt = replay_issue(connection, task['task_id'], selector['reminderWaitId'])
        if receipt is not None:
            return _issue_message(connection, receipt)
        reminder = reminders.consume(store, connection, task, selector['reminderWaitId'])
        if reminder is not None:
            return {'reminder': reminder}
        row = pending_issue(connection, task['task_id'])
        if row is None:
            return {}
        connection.execute(
            'INSERT INTO issue_deliveries(task_id,issue_kind,occurrence_seq,wait_id,delivered_at) VALUES(?,?,?,?,?)',
            (task['task_id'], row['issue_kind'], row['occurrence_seq'], selector['reminderWaitId'], store.now()),
        )
        return _issue_message(connection, row)


def ready(store, connection, task, *, after: int) -> bool:
    """Read the same messages as await, without claiming the Lead's content."""
    if pending_issue(connection, task['task_id'], after=after) is not None:
        return True
    from . import reminders
    if reminders.pending(store, connection, task, after=after) is not None:
        return True
    # Preserve a boundary reached while the Lead was running, even when a later
    # action has already moved the owned task on by the time the Lead parks.
    previous = connection.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND seq<=?"
        " AND json_type(payload_json,'$.awaitMessage')='object' ORDER BY seq DESC LIMIT 1",
        (task['task_id'], after),
    ).fetchone()
    previous_key = json.loads(previous['payload_json'])['awaitMessage']['key'] if previous is not None else None
    rows = connection.execute(
        "SELECT e.payload_json FROM events e JOIN workflow_lead_tasks l ON l.owned_run_id=e.task_id"
        " WHERE e.task_id=? AND e.seq>? AND json_type(e.payload_json,'$.awaitMessage')='object'"
        " AND (COALESCE(e.actor_role,'')!='lead' OR COALESCE(e.actor_id,'')!=l.lead_run_id)",
        (task['task_id'], after),
    ).fetchall()
    return any(json.loads(row['payload_json'])['awaitMessage']['key'] != previous_key for row in rows)
