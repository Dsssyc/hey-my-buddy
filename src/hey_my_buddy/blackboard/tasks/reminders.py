"""Durable execution reminders and their deadline-driven service scheduler."""
from __future__ import annotations

from datetime import datetime
import threading


def _seconds(value: str) -> float:
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def view(row) -> dict:
    return {
        'reminderId': row['reminder_id'], 'kind': row['kind'],
        'attemptId': row['attempt_id'], 'runId': row['task_id'],
        'expectedMultiple': row['expected_multiple'],
        'elapsedSeconds': row['elapsed_seconds'],
        'lastNativeEventAt': row['last_native_event_at'],
        'toolInProgress': bool(row['tool_in_progress']), 'createdAt': row['created_at'],
    }


def _native_at(connection, attempt_id: str, activity: dict | None) -> str | None:
    if activity is None:
        return None
    timestamp = activity.get('lastNativeActivityAt')
    if timestamp is not None:
        return timestamp
    if not activity.get('eventSeq'):
        # Phase observations (including sending a prompt) do not prove that a
        # native event occurred. Native ordinals start above zero.
        return None
    # Sequence-only native receipts are valid at the activity boundary. Their
    # service receipt time is the available timestamp, never a lease heartbeat.
    return activity.get('observedAt') or connection.execute(
        'SELECT updated_at FROM attempt_activity WHERE attempt_id=?', (attempt_id,)).fetchone()[0]


def status(connection, attempt, activity: dict | None) -> dict:
    """Current warnings belong only to the selected, still unresolved execution."""
    native_at = _native_at(connection, attempt['attempt_id'], activity)
    active = attempt['execution_state'] != 'finished' and attempt['result_json'] is None
    silence_at = native_at or attempt['started_at']
    row = connection.execute(
        "SELECT EXISTS(SELECT 1 FROM attempt_reminders WHERE attempt_id=? AND kind='expected-duration'),"
        " EXISTS(SELECT 1 FROM attempt_reminders WHERE attempt_id=? AND kind='native-silence' AND silence_started_at=?)",
        (attempt['attempt_id'], attempt['attempt_id'], silence_at),
    ).fetchone() if active else (False, False)
    return {
        'overdue': bool(row[0]),
        'possiblyStuck': bool(row[1]),
        'lastNativeEventAt': native_at,
        'toolInProgress': (activity or {}).get('phase') == 'tool-running',
    }


def collect(store) -> float | None:
    """Record all due reminders atomically; return seconds to the next deadline.

    No active attempts means no deadline. Native activity, claims, results and
    settings changes already notify the store condition and re-arm this schedule.
    """
    now = store.now()
    instant = _seconds(now)
    with store.db.read() as connection:
        delay, due = _scan(store, connection, now, instant, record=False)
    if not due:
        return delay
    # A result, native event or another scheduler may have changed the deadline
    # after the read transaction. Recompute under the writer lock before insert.
    with store.db.write() as connection:
        delay, changed = _scan(store, connection, now, instant, record=True)
        head = store._head_of(connection) if changed else None
    if changed:
        store._notify(head)
    return delay


def _scan(store, connection, now: str, instant: float, *, record: bool) -> tuple[float | None, bool]:
    delay = None
    changed = False
    silence_seconds = connection.execute('SELECT silence_seconds FROM reminder_settings WHERE id=1').fetchone()[0]
    attempts = connection.execute(
        "SELECT a.*, t.expected_seconds FROM attempts a JOIN tasks t ON t.task_id=a.task_id"
        " WHERE a.execution_state IN ('starting','executing','finalizing','uncertain')"
        " AND a.result_json IS NULL AND t.active_attempt_id=a.attempt_id AND t.adapter!='decision'"
    ).fetchall()
    for attempt in attempts:
        started_at = attempt['started_at']
        started = _seconds(started_at)
        elapsed = max(0.0, instant - started)
        activity = store._activity_latest(connection, attempt['attempt_id'])
        native_at = _native_at(connection, attempt['attempt_id'], activity)
        silence_at = native_at or started_at
        tool = (activity or {}).get('phase') == 'tool-running'
        previous = connection.execute(
            "SELECT COALESCE(MAX(expected_multiple),0) FROM attempt_reminders WHERE attempt_id=? AND kind='expected-duration'",
            (attempt['attempt_id'],),
        ).fetchone()[0]
        due = [('expected-duration', multiple, None)
               for multiple in range(previous + 1, int(elapsed // attempt['expected_seconds']) + 1)]
        next_expected = started + (max(previous, int(elapsed // attempt['expected_seconds'])) + 1) * attempt['expected_seconds']
        deadlines = [next_expected]
        silent = connection.execute(
            "SELECT 1 FROM attempt_reminders WHERE attempt_id=? AND kind='native-silence' AND silence_started_at=?",
            (attempt['attempt_id'], silence_at),
        ).fetchone()
        if silent is None:
            silence_deadline = _seconds(silence_at) + silence_seconds
            if instant >= silence_deadline:
                due.append(('native-silence', None, silence_at))
            else:
                deadlines.append(silence_deadline)
        if due:
            changed = True
        if record:
            for kind, multiple, silence in due:
                cursor = connection.execute(
                    'INSERT INTO attempt_reminders(task_id,attempt_id,kind,expected_multiple,silence_started_at,'
                    'elapsed_seconds,last_native_event_at,tool_in_progress,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                    (attempt['task_id'], attempt['attempt_id'], kind, multiple, silence, elapsed, native_at, int(tool), now),
                )
                row = connection.execute('SELECT * FROM attempt_reminders WHERE reminder_id=?', (cursor.lastrowid,)).fetchone()
                connection.execute('UPDATE tasks SET revision=revision+1,updated_at=? WHERE task_id=?', (now, attempt['task_id']))
                revision = connection.execute('SELECT revision FROM tasks WHERE task_id=?', (attempt['task_id'],)).fetchone()[0]
                store._append_event(connection, 'attempt.reminder', task_id=attempt['task_id'],
                                    attempt_id=attempt['attempt_id'], revision=revision, payload=view(row))
        remaining = max(0.0, min(deadlines) - instant)
        delay = remaining if delay is None else min(delay, remaining)
    return delay, changed


def consume(store, selector: dict) -> dict | None:
    """Claim exactly one pending reminder for an await, across CLI invocations."""
    with store.db.write() as connection:
        task = store._task_row(connection, selector)
        delivery_id = selector['reminderWaitId']
        delivered = connection.execute('SELECT * FROM attempt_reminders WHERE delivery_id=?', (delivery_id,)).fetchone()
        if delivered is not None:
            if delivered['task_id'] != task['task_id']:
                from ...errors import BoardError
                raise BoardError('CONFLICT', 'reminderWaitId belongs to a different run')
            return view(delivered)
        attempt = store._selected_attempt(connection, task)
        if attempt is None or attempt['execution_state'] == 'finished' or attempt['result_json'] is not None:
            return None
        row = connection.execute(
            'SELECT * FROM attempt_reminders WHERE attempt_id=? AND delivered_at IS NULL ORDER BY reminder_id LIMIT 1',
            (attempt['attempt_id'],),
        ).fetchone()
        if row is None:
            return None
        connection.execute('UPDATE attempt_reminders SET delivered_at=?,delivery_id=? WHERE reminder_id=?',
                           (store.now(), delivery_id, row['reminder_id']))
        return view(row)


class Scheduler:
    def __init__(self, store):
        self.store = store
        self.thread = threading.Thread(target=self.run, name='hey-my-buddy-reminders', daemon=True)

    def run(self):
        try:
            while True:
                with self.store._condition:
                    if self.store._wait_stopped:
                        return
                    head = self.store._head
                delay = collect(self.store)
                with self.store._condition:
                    self.store._condition.wait_for(
                        lambda: self.store._wait_stopped or self.store._head != head, timeout=delay,
                    )
        except Exception as error:
            # A failed reminder writer is a service persistence failure, not a
            # reason to silently lose warnings or to stop any execution.
            self.store.persistence_error = f'Reminder scheduler: {type(error).__name__}: {error}'
