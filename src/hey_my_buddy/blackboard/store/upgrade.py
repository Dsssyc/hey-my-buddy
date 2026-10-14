"""The one pending, offline schema-19 to schema-20 board conversion."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from ...json_codec import canonical_json
from ...protocol import schemas
from .db import SCHEMA, SCHEMA_VERSION


BASE_SCHEMA_VERSION = 19
DEFAULT_EXPECTED_SECONDS = 1800


def _health_record(record: dict, observed_at) -> dict:
    """Give stored health observations the same explicit shape as new reads."""
    record['lastProbe'] = ({'status': 'complete', 'observedAt': observed_at,
                            'reasonCode': record.get('reasonCode')}
                           if observed_at is not None else None)
    failure = record.get('accountReadFailure')
    if failure is not None:
        failure['status'] = 'unknown' if failure['reasonCode'] == 'deadline' else 'failed'
    return record


def _spec(value: dict) -> tuple[dict, int | None, int | None]:
    value = dict(value)
    old = value.pop('timeoutSeconds')
    if value.get('adapter') == 'decision':
        decision = dict(value['decision'])
        decision['routerBudgetSeconds'] = decision.pop('timeoutSeconds')
        value['decision'] = decision
        value['routerBudgetSeconds'] = old
        return value, None, old
    expected = old if old > 0 else DEFAULT_EXPECTED_SECONDS
    value['expectedSeconds'] = expected
    return value, expected, None


def _fresh_ddl(name: str) -> tuple[str, list[str]]:
    with closing(sqlite3.connect(':memory:')) as fresh:
        fresh.executescript(SCHEMA)
        ddl = fresh.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()[0]
        indexes = [row[0] for row in fresh.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL ORDER BY name", (name,))]
    return ddl, indexes


def _rebuild(connection: sqlite3.Connection, name: str, transform) -> None:
    ddl, indexes = _fresh_ddl(name)
    staging = name + '_v20'
    connection.execute(re.sub(r'\b' + name + r'\b', staging, ddl, count=1))
    columns = [row[1] for row in connection.execute(f'PRAGMA table_info({staging})')]
    quoted = ','.join('"' + column + '"' for column in columns)
    placeholders = ','.join('?' for _ in columns)
    for row in connection.execute(f'SELECT * FROM "{name}"'):
        data = transform(dict(row))
        connection.execute(f'INSERT INTO "{staging}"({quoted}) VALUES({placeholders})',
                           tuple(data[column] for column in columns))
    connection.execute(f'DROP TABLE "{name}"')
    connection.execute(ddl)
    connection.execute(f'INSERT INTO "{name}"({quoted}) SELECT {quoted} FROM "{staging}"')
    connection.execute(f'DROP TABLE "{staging}"')
    for index in indexes:
        connection.execute(index)


def _task(row: dict) -> dict:
    spec, expected, budget = _spec(json.loads(row['spec_json']))
    canonical, _, _ = _spec(json.loads(row['spec_canonical_json']))
    row['spec_json'] = canonical_json(spec)
    row['spec_canonical_json'] = canonical_json(canonical)
    row['input_fingerprint'] = schemas.spec_fingerprint(canonical)
    row['expected_seconds'] = expected
    row['router_budget_seconds'] = budget
    return row


def _turn(connection: sqlite3.Connection, row: dict) -> dict:
    old = connection.execute('SELECT expected_seconds FROM tasks WHERE task_id=?', (row['run_id'],)).fetchone()
    row['expected_seconds'] = old[0]
    return row


def _run(row: dict) -> dict:
    spec, _, _ = _spec(json.loads(row['micro_task_json']))
    row['micro_task_json'] = canonical_json(spec)
    row['micro_task_fingerprint'] = schemas.spec_fingerprint(spec)
    row['delegation_authority'] = 0
    return row


def _continuation(row: dict) -> dict:
    row['kind'] = 'manual' if row.pop('authorized_by') == 'manual' else 'helper'
    row['trigger_event_json'] = None
    return row


def _stop_origin(actor) -> tuple[str, str]:
    if isinstance(actor, str):
        if actor.startswith('host:') and actor[5:]:
            return 'host', 'forced-stop'
        if actor.startswith('console:') and actor[8:]:
            return 'console', 'console-stop'
        if actor == 'service-stop':
            return 'service', 'service-stop'
    return 'unknown', 'unknown-stop'


def _legacy_stops(connection: sqlite3.Connection) -> tuple[dict[str, tuple[str, str]], dict[str, list[str]]]:
    stops = {}
    by_task = {}
    for row in connection.execute('SELECT attempt_id,task_id,cancel_requested_at,result_json FROM attempts'):
        by_task.setdefault(row['task_id'], []).append(row['attempt_id'])
        receipt = json.loads(row['result_json']) if row['result_json'] is not None else None
        if not row['cancel_requested_at'] and not (isinstance(receipt, dict) and receipt.get('terminationReason') == 'user-cancel'):
            continue
        claimed = connection.execute(
            "SELECT COALESCE(MAX(seq),0) FROM events WHERE attempt_id=? AND kind='attempt.claimed'",
            (row['attempt_id'],),
        ).fetchone()[0]
        terminal = connection.execute(
            "SELECT seq FROM events WHERE attempt_id=? AND seq>?"
            " AND json_extract(payload_json,'$.taskState') IS NOT NULL ORDER BY seq LIMIT 1",
            (row['attempt_id'], claimed),
        ).fetchone()
        # The cancellation must follow this claim and precede its terminal fact.
        # Timestamps alone cannot distinguish two events in the same clock tick.
        event = connection.execute(
            "SELECT payload_json FROM events WHERE task_id=? AND kind='workflow.cancelled'"
            " AND (attempt_id IS NULL OR attempt_id=?) AND seq>?"
            " AND (? IS NULL OR seq<?) AND created_at>=?"
            " ORDER BY seq LIMIT 1",
            (row['task_id'], row['attempt_id'], claimed, terminal['seq'] if terminal else None,
             terminal['seq'] if terminal else None, row['cancel_requested_at']),
        ).fetchone() if row['cancel_requested_at'] else None
        payload = json.loads(event['payload_json']) if event else None
        stops[row['attempt_id']] = _stop_origin(payload.get('actor') if isinstance(payload, dict) else None)
    return stops, by_task


def _attempt(row: dict, stops: dict[str, tuple[str, str]]) -> dict:
    receipt = json.loads(row['result_json']) if row['result_json'] is not None else None
    stop = stops.get(row['attempt_id'])
    row['stop_initiator'] = stop[0] if stop else None
    row['grace_deadline'] = None
    if receipt is not None:
        converted = _receipt_object(receipt, service_receipt=True, adapter=row['adapter'], stop=stop)
        row['result_json'] = canonical_json(converted)
    return row


def _termination(value: dict, *, adapter: str | None = None,
                 stop: tuple[str, str] | None = None) -> dict:
    reason = value.get('terminationReason')
    if reason == 'user-cancel':
        value['terminationReason'] = (stop or ('unknown', 'unknown-stop'))[1] if value.get('shutdownConfirmed') is True else None
    elif reason == 'deadline' and adapter is not None and adapter != 'decision':
        # A historical Worker deadline was a task time limit, not a Router budget.
        value['terminationReason'] = None
    return value


def _receipt_object(value, member: str | None = None, *, service_receipt: bool = False,
                    adapter: str | None = None, stop: tuple[str, str] | None = None,
                    stops: dict[str, tuple[str, str]] | None = None,
                    by_task: dict[str, list[str]] | None = None):
    """Update durable service task projections; leave native outcomes alone."""
    if isinstance(value, list):
        return [_receipt_object(item, service_receipt=service_receipt, adapter=adapter, stop=stop,
                                stops=stops, by_task=by_task) for item in value]
    if not isinstance(value, dict):
        return value
    if member == 'accountReadFailure':
        value['status'] = 'unknown' if value['reasonCode'] == 'deadline' else 'failed'
    if ('revision' in value and (member == 'harness'
                                or 'harness' in value and 'checkedAt' in value)):
        _health_record(value, value.get('checkedAt'))
    attempt_id = value.get('attemptId')
    selected = value.get('selectedAttempt')
    if attempt_id is None and isinstance(selected, dict):
        attempt_id = selected.get('attemptId')
    if stops is not None and not service_receipt:
        if attempt_id is None and by_task is not None:
            task_attempts = by_task.get(value.get('taskId', value.get('runId')), [])
            if len(task_attempts) == 1:
                attempt_id = task_attempts[0]
        if attempt_id is not None or 'taskId' in value or 'runId' in value:
            stop = stops.get(attempt_id)
    # Native model outcomes and original inputs are evidence. The result immediately
    # under an attempt view is a service receipt; its own result remains native.
    legacy_stop = value.get('terminationReason') == 'user-cancel'
    attempt_view = ('attemptId' in value and 'adapter' in value
                    and any(key in value for key in ('generation', 'executionState', 'cancelRequestedAt', 'result')))
    value = {key: (_receipt_object(item, key, service_receipt=True, adapter=value.get('adapter'), stop=stop,
                                  stops=stops, by_task=by_task)
                   if key == 'result' and attempt_view else
                   item if key in ('result', 'outcome', 'spec', 'input', 'context', 'provenance', 'report')
                   else _receipt_object(item, key, stop=stop, stops=stops, by_task=by_task))
             for key, item in value.items()}
    task_view = ('taskId' in value or 'runId' in value) and 'adapter' in value and not attempt_view
    if task_view or service_receipt or 'terminationReason' in value:
        _termination(value, adapter=value.get('adapter', adapter), stop=stop)
    if attempt_view:
        receipt = value.get('result')
        value['stopInitiator'] = ((stop or ('unknown', 'unknown-stop'))[0] if value.get('cancelRequestedAt')
                                  or isinstance(receipt, dict) and receipt.get('terminationReason') in
                                  ('forced-stop', 'console-stop', 'service-stop', 'unknown-stop') else None)
        value['graceDeadline'] = None
    if task_view:
        selected = value.get('selectedAttempt')
        value['stopInitiator'] = ((stop or ('unknown', 'unknown-stop'))[0]
                                  if isinstance(selected, dict) and selected.get('stopInitiator') is not None
                                  or legacy_stop or value.get('stopInitiator') is not None else None)
        value['graceDeadline'] = None
    if task_view and 'timeoutSeconds' in value:
        old = value.pop('timeoutSeconds')
        if value['adapter'] == 'decision':
            value['routerBudgetSeconds'] = old
        else:
            value['expectedSeconds'] = old if old > 0 else DEFAULT_EXPECTED_SECONDS
    if (task_view and 'spec' in value
            and isinstance(value['spec'], dict) and 'timeoutSeconds' in value['spec']):
        value['spec'], _, _ = _spec(value['spec'])
        if 'inputFingerprint' in value:
            value['inputFingerprint'] = schemas.spec_fingerprint(value['spec'])
    if member == 'microTask' and 'timeoutSeconds' in value:
        old = value.pop('timeoutSeconds')
        value['expectedSeconds'] = old if old > 0 else DEFAULT_EXPECTED_SECONDS
    return value


def migrate(state: Path) -> None:
    """Convert a stopped schema-19 board after a verified backup was published.

    The caller holds the daemon and board-owner locks. SQLite performs the
    complete replacement in one transaction; an exception leaves schema 19.
    """
    with closing(sqlite3.connect(Path(state) / 'board.sqlite3', isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=OFF')
        connection.execute('BEGIN IMMEDIATE')
        try:
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version is None or version[0] != str(BASE_SCHEMA_VERSION):
                raise ValueError('Expected the schema-19 upgrade base')
            stops, by_task = _legacy_stops(connection)
            _rebuild(connection, 'tasks', _task)
            _rebuild(connection, 'workflow_turns', lambda row: _turn(connection, row))
            _rebuild(connection, 'attempts', lambda row: _attempt(row, stops))
            for name in ('attempt_process_identities', 'attempt_legacy_starts',
                         'attempt_interruptions'):
                ddl, indexes = _fresh_ddl(name)
                connection.execute(ddl)
                for index in indexes:
                    connection.execute(index)
            connection.execute(
                "INSERT INTO attempt_legacy_starts(attempt_id,started_at)"
                " SELECT attempt_id,COALESCE(started_at,created_at) FROM attempts"
                " WHERE execution_state='uncertain'")
            _rebuild(connection, 'workflow_runs', _run)
            _rebuild(connection, 'workflow_continuations', _continuation)
            _rebuild(connection, 'agent_credentials', lambda row: row)
            for row in connection.execute('SELECT harness,record_json,checked_at FROM harness_health').fetchall():
                record = _health_record(json.loads(row['record_json']), row['checked_at'])
                connection.execute('UPDATE harness_health SET record_json=? WHERE harness=?',
                                   (canonical_json(record), row['harness']))
            ddl, indexes = _fresh_ddl('workflow_lead_tasks')
            connection.execute(ddl)
            for index in indexes:
                connection.execute(index)
            for row in connection.execute('SELECT decision_id,requested_json FROM decision_requests').fetchall():
                request = json.loads(row['requested_json'])
                old = request.pop('timeoutSeconds', None)
                if old is not None:
                    request['routerBudgetSeconds'] = old
                connection.execute('UPDATE decision_requests SET requested_json=? WHERE decision_id=?',
                                   (canonical_json(request), row['decision_id']))
            for row in connection.execute("SELECT command_id,response_json FROM commands").fetchall():
                response = _receipt_object(json.loads(row['response_json']), stops=stops, by_task=by_task)
                connection.execute('UPDATE commands SET response_json=? WHERE command_id=?',
                                   (canonical_json(response), row['command_id']))
            for row in connection.execute("SELECT seq,task_id,attempt_id,payload_json FROM events WHERE payload_json LIKE '%terminationReason%' OR payload_json LIKE '%accountReadFailure%' OR kind LIKE 'harness.%'").fetchall():
                stop = stops.get(row['attempt_id'])
                if stop is None and row['task_id'] is not None and len(by_task.get(row['task_id'], [])) == 1:
                    stop = stops.get(by_task[row['task_id']][0])
                payload = _receipt_object(json.loads(row['payload_json']), stop=stop, stops=stops, by_task=by_task)
                connection.execute('UPDATE events SET payload_json=? WHERE seq=?',
                                   (canonical_json(payload), row['seq']))
            for row in connection.execute(
                "SELECT c.command_id,t.request_id,t.input_fingerprint FROM commands c"
                " JOIN tasks t ON t.task_id=c.task_id WHERE c.kind='task.submit'"
            ).fetchall():
                request = {'requestId': row['request_id'], 'fingerprint': row['input_fingerprint']}
                digest = hashlib.sha256(canonical_json(request).encode()).hexdigest()
                connection.execute('UPDATE commands SET request_hash=? WHERE command_id=?',
                                   (digest, row['command_id']))
            connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            if connection.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Foreign-key check failed after schema conversion')
            if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Integrity check failed after schema conversion')
            connection.execute('COMMIT')
        except BaseException:
            connection.execute('ROLLBACK')
            raise
    # The schema is current before process proof and Git sealing run. No SQLite
    # transaction spans either boundary, and only explicitly tagged stock without
    # complete identity is eligible for the before-this-boot evidence rule.
    from .store import BoardStore
    board = BoardStore(state)
    try:
        board.recover_interrupted_attempts(legacy_only=True)
    finally:
        board.db.close()
