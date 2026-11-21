"""Durable message delivery and the minimal facts shared by await and Lead."""
from __future__ import annotations

import json

from ...errors import BoardError
from ...protocol.awaiting import message_kind
from . import reminders


def pending_issue(connection, task_id: str):
    return connection.execute(
        "SELECT i.occurrence_seq,i.observed_at FROM execution_issues i JOIN attempts a ON a.attempt_id=i.attempt_id"
        " WHERE a.task_id=? AND NOT EXISTS(SELECT 1 FROM issue_deliveries d"
        " WHERE d.task_id=? AND d.occurrence_seq=i.occurrence_seq)"
        " UNION ALL SELECT i.occurrence_seq,i.observed_at FROM service_issues i WHERE NOT EXISTS("
        " SELECT 1 FROM issue_deliveries d WHERE d.task_id=? AND d.occurrence_seq=i.occurrence_seq)"
        " ORDER BY occurrence_seq LIMIT 1", (task_id, task_id, task_id),
    ).fetchone()


def pending_pause(connection, task_id: str):
    """An interrupted attempt whose pause has not yet reached a Host await."""
    return connection.execute(
        "SELECT p.* FROM events p WHERE p.task_id=? AND p.kind='workflow.paused'"
        " AND p.attempt_id IS NOT NULL AND NOT EXISTS ("
        " SELECT 1 FROM events d WHERE d.task_id=p.task_id AND d.kind='workflow.pause_delivered'"
        " AND json_extract(d.payload_json,'$.pauseEventId')=p.seq)"
        " ORDER BY p.seq LIMIT 1", (task_id,),
    ).fetchone()


def _pause_message(event) -> dict:
    payload = json.loads(event['payload_json'])
    return {'pause': {'pausedAt': payload['pausedAt'], 'eventId': event['seq']}}


def _issue_message(connection, receipt) -> dict:
    event = connection.execute('SELECT * FROM events WHERE seq=?', (receipt['observation_seq'],)).fetchone()
    payload = json.loads(event['payload_json'])
    detail = {key: payload[key] for key in ('stage', 'code', 'message')}
    detail['observedAt'] = receipt['observed_at']
    kind = 'execution' if event['attempt_id'] is not None else 'service'
    if kind == 'execution':
        detail['attemptId'] = event['attempt_id']
    return {'issue': {'kind': kind, 'detail': detail}}


def _receipt(connection, task_id: str, wait_id: str) -> dict | None:
    issue = connection.execute('SELECT * FROM issue_deliveries WHERE wait_id=?', (wait_id,)).fetchone()
    if issue is not None:
        if issue['task_id'] != task_id:
            raise BoardError('CONFLICT', 'reminderWaitId belongs to a different run')
        return _issue_message(connection, issue)
    pause = connection.execute(
        "SELECT * FROM events WHERE task_id=? AND kind='workflow.pause_delivered'"
        " AND json_extract(payload_json,'$.waitId')=? ORDER BY seq DESC LIMIT 1",
        (task_id, wait_id),
    ).fetchone()
    if pause is not None:
        event = connection.execute('SELECT * FROM events WHERE seq=?',
                                   (json.loads(pause['payload_json'])['pauseEventId'],)).fetchone()
        return _pause_message(event)
    reminder = reminders.receipt(connection, task_id, wait_id)
    return {'reminder': reminder} if reminder is not None else None


def consume(store, selector: dict) -> dict:
    """Replay or wait without a writer, and claim new messages atomically."""
    with store.db.read() as connection:
        task = store._task_row(connection, selector)
        task_id = task['task_id']
        wait_id = selector['reminderWaitId']
        receipt = _receipt(connection, task_id, wait_id)
        if receipt is not None:
            return receipt
        if (reminders.pending(connection, task) is None and pending_issue(connection, task_id) is None
                and pending_pause(connection, task_id) is None):
            return {}

    # A competing waiter or a state change may have resolved the message.
    with store.db.write() as connection:
        task = store._task_row(connection, selector)
        task_id = task['task_id']
        receipt = _receipt(connection, task_id, wait_id)
        if receipt is not None:
            return receipt
        reminder = reminders.consume(store, connection, task, wait_id)
        if reminder is not None:
            return {'reminder': reminder}
        issue = pending_issue(connection, task_id)
        if issue is None:
            pause_event = pending_pause(connection, task_id)
            if pause_event is None:
                return {}
            store._append_event(
                connection, 'workflow.pause_delivered', task_id=task_id,
                payload={'pauseEventId': pause_event['seq'], 'waitId': wait_id},
                wake_owned=False,
            )
            head = store._head_of(connection)
            store.db.after_commit(connection, lambda: store._notify(head))
            return _pause_message(pause_event)
        opening = connection.execute('SELECT * FROM events WHERE seq=?', (issue['occurrence_seq'],)).fetchone()
        # Freeze the latest observation at delivery, not the opening's old cause.
        observation = connection.execute(
            "SELECT MAX(seq) FROM events WHERE task_id IS ? AND kind IN (?,?) AND attempt_id IS ?"
            " AND json_extract(payload_json,'$.stage')=json_extract(?,'$.stage') AND seq>=?",
            (opening['task_id'], opening['kind'], opening['kind'] + '_updated', opening['attempt_id'],
             opening['payload_json'], issue['occurrence_seq']),
        ).fetchone()[0]
        # The observation time can precede the event append time. Keep that
        # independent fact for replay after the live problem row is removed.
        connection.execute(
            'INSERT INTO issue_deliveries(task_id,occurrence_seq,observation_seq,observed_at,wait_id) VALUES(?,?,?,?,?)',
            (task_id, issue['occurrence_seq'], observation, issue['observed_at'], wait_id),
        )
        return _issue_message(connection, {'observation_seq': observation, 'observed_at': issue['observed_at']})


def observation(store, connection, task) -> dict:
    """Only fields used by message_kind and revision/stop comparison; no task view."""
    workflow = connection.execute('SELECT state FROM workflow_runs WHERE run_id=?', (task['task_id'],)).fetchone()
    attempt = connection.execute('SELECT result_json IS NOT NULL AS available FROM attempts WHERE attempt_id=?',
                                 (task['active_attempt_id'] or task['selected_attempt_id'],)).fetchone()
    facts = {'revision': task['revision'], 'state': task['state'],
             'resultAvailable': attempt is not None and bool(attempt['available']),
             'workflowState': workflow['state'] if workflow is not None else None}
    if workflow is not None and workflow['state'] in ('delivered', 'accepted', 'concluded', 'cancelled', 'failed'):
        stop = store.workflow.shutdown_summary(connection, task['task_id'])
        facts['workflowShutdown'] = {key: stop[key] for key in ('selfConfirmed', 'descendantsConfirmed')}
    return facts


def ready(store, connection, task, *, previous: dict | None, lead_run_id: str, cursor: int) -> int | None:
    """Return a doorbell's source event; delivery receipts govern pending content."""
    issue = pending_issue(connection, task['task_id'])
    if issue is not None:
        return issue['occurrence_seq']
    reminder = reminders.pending(connection, task)
    if reminder is not None:
        return reminder['event_seq']
    current = observation(store, connection, task)
    stopped_changed = current.get('workflowShutdown') != (previous or {}).get('workflowShutdown')
    if previous is not None and current['revision'] == previous['revision'] and not stopped_changed:
        return None
    if message_kind(current) is None:
        return None
    own_change = connection.execute(
        "SELECT MAX(seq) FROM events WHERE task_id=? AND seq>? AND revision=? AND actor_role='lead' AND actor_id=?",
        (task['task_id'], cursor, current['revision'], lead_run_id),
    ).fetchone()[0]
    if own_change is not None:
        # Acceptance/conclusion already required whole-lineage stop. A cancel
        # can still gain a later stop proof, independently of the task revision.
        proof = connection.execute(
            "SELECT 1 FROM events WHERE task_id=? AND seq>?"
            " AND kind IN ('execution.shutdown_confirmed','workflow.owned_stop_reported') LIMIT 1",
            (task['task_id'], own_change),
        ).fetchone() if current['workflowState'] == 'cancelled' and stopped_changed else None
        if proof is None:
            return None
    return connection.execute('SELECT MAX(seq) FROM events WHERE task_id=?', (task['task_id'],)).fetchone()[0]
