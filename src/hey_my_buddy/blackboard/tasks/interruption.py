"""Recover stopped attempts from durable process facts, never from lease age."""
from __future__ import annotations

import json
import uuid

from ...errors import BoardError
from ...protocol import schemas
from ...protocol import process_identity as processes
from ..store.db import canonical_json



def read_identity(connection, attempt_id: str) -> dict | None:
    row = connection.execute('SELECT * FROM attempt_process_identities WHERE attempt_id=? ORDER BY ordinal DESC LIMIT 1',
                             (attempt_id,)).fetchone()
    if row is None:
        return None
    return _identity_view(row)


def _identity_view(row) -> dict:
    return {'bootId': row['boot_id'], 'pid': row['root_pid'], 'startedAt': row['root_started_at'],
            'processGroupId': row['process_group_id'], 'jobName': row['job_name'],
            'worker': {'bootId': row['worker_boot_id'], 'pid': row['worker_pid'],
                       'startedAt': row['worker_started_at']}}


def record_identity(connection, attempt_id: str, record: dict, *, previous_stopped: bool = False) -> None:
    existing = connection.execute(
        'SELECT * FROM attempt_process_identities WHERE attempt_id=? AND boot_id=? AND root_pid=? AND root_started_at=?',
        (attempt_id, record['bootId'], record['pid'], record['startedAt'])).fetchone()
    if existing is not None:
        if _identity_view(existing) != record:
            raise BoardError('CONFLICT', 'An execution process identity is immutable', attemptId=attempt_id)
        return
    previous = read_identity(connection, attempt_id)
    ordinal = 0
    if previous is not None:
        if not previous_stopped or previous['worker'] != record['worker']:
            raise BoardError('CONFLICT', 'An execution process identity is immutable', attemptId=attempt_id)
        ordinal = connection.execute('SELECT MAX(ordinal)+1 FROM attempt_process_identities WHERE attempt_id=?',
                                     (attempt_id,)).fetchone()[0]
    worker = record['worker']
    connection.execute(
        'INSERT INTO attempt_process_identities VALUES(?,?,?,?,?,?,?,?,?,?)',
        (attempt_id, ordinal, record['bootId'], record['pid'], record['startedAt'], record['processGroupId'],
         record['jobName'], worker['bootId'], worker['pid'], worker['startedAt']))


def _snapshot(board, *, attempt_id: str | None = None, unobserved_only: bool = False) -> list[tuple[dict, dict | None]]:
    with board.db.read() as connection:
        rows = connection.execute(
            "SELECT a.* FROM attempts a WHERE a.execution_state='uncertain'" +
            (' AND a.attempt_id=?' if attempt_id is not None else '') +
            (' AND NOT EXISTS(SELECT 1 FROM attempt_interruptions i WHERE i.attempt_id=a.attempt_id)'
             if unobserved_only else ''), (() if attempt_id is None else (attempt_id,))).fetchall()
        return [(dict(row), read_identity(connection, row['attempt_id']))
                for row in rows]


def _native_journal(board, attempt: dict, identity: dict) -> dict | None:
    if attempt['adapter'] not in ('codex', 'dsh', 'zcode', 'claude', 'decision'):
        return None
    from ...private_dirs import attempt_root
    harness = attempt['model_harness'] if attempt['adapter'] == 'decision' else attempt['adapter']
    root = attempt_root(board.directory, harness, attempt['task_id'], attempt['attempt_id'])
    return {'path': str(processes.native_process_journal_path(root, identity)),
            'binding': {'taskId': attempt['task_id'], 'attemptId': attempt['attempt_id'],
                        'generation': attempt['generation']}}


def _native_identities(board, attempt: dict, identity: dict) -> list[dict] | None:
    journal = _native_journal(board, attempt, identity)
    if journal is None:
        return []
    value = processes.read_native_process_journal(journal['path'], journal['binding'])
    if value is None or value['pending']:
        return None
    return value['identities']


def _evidence(board, attempt: dict, record: dict | None) -> str:
    if record is not None:
        observed = processes.inspect_process_identity(record)
        if observed != 'identity-absent':
            return observed
        if record['jobName'] is not None:
            # Native children inherit the outer Job even when also assigned to
            # their own nested Jobs; no spawn requests breakaway permission.
            return 'identity-absent'
        identities = _native_identities(board, attempt, record)
        if identities is None:
            return 'unknown'
        for native in identities:
            observed = processes.inspect_process_identity(native)
            if observed not in ('boot-changed', 'identity-absent'):
                return observed
        return 'identity-absent'
    return 'unknown'


def recover(board) -> int:
    settled = 0
    for attempt, record in _snapshot(board):
        with board.db.read() as connection:
            pending = connection.execute('SELECT evidence_kind FROM attempt_interruptions WHERE attempt_id=?',
                                         (attempt['attempt_id'],)).fetchone()
        if pending is not None:
            settled += _settle(board, attempt, record, pending['evidence_kind'])
            continue
        # A surviving original Worker still owns collection and its durable
        # receipt, including a child that completed while the Worker was asleep.
        if record is not None and processes.worker_identity_alive(record) is not False:
            continue
        evidence = _evidence(board, attempt, record)
        if evidence in ('boot-changed', 'identity-absent'):
            settled += _settle(board, attempt, record, evidence)
    return settled


def worker_recover(board, params: dict) -> dict:
    schemas.reject_unknown(params, {'workerId', 'attemptId', 'evidence', 'observeAttemptId'}, 'worker.recover')
    worker_id = schemas.required_string(params, 'workerId', max_length=128)
    attempt_id = schemas.optional_string(params, 'attemptId', max_length=128)
    evidence = schemas.optional_string(params, 'evidence', max_length=32)
    observe_attempt = schemas.optional_string(params, 'observeAttemptId', max_length=128)
    if observe_attempt is not None and attempt_id is not None:
        raise BoardError('INVALID_ARGUMENT', 'A recovery observation is separate from stop reporting')
    if (attempt_id is None) != (evidence is None):
        raise BoardError('INVALID_ARGUMENT', 'attemptId and evidence must be supplied together')
    if evidence is not None and evidence not in ('worker-stopped', 'identity-absent', 'boot-changed'):
        raise BoardError('INVALID_ARGUMENT', 'Unknown interruption evidence')
    with board.db.read() as connection:
        if connection.execute('SELECT 1 FROM workers WHERE worker_id=?', (worker_id,)).fetchone() is None:
            raise BoardError('NOT_FOUND', 'The recovery Worker is not registered')
        if observe_attempt is not None:
            observed = board._attempt_row(connection, observe_attempt)
            if observed['worker_id'] != worker_id:
                raise BoardError('UNAUTHORIZED', 'The local orphan belongs to a different Worker')
    candidates = []
    for attempt, record in _snapshot(board, attempt_id=attempt_id, unobserved_only=attempt_id is None):
        if record is None or processes.worker_identity_alive(record) is not False:
            continue
        if attempt_id == attempt['attempt_id']:
            observed = _evidence(board, attempt, record)
            if observed not in ('boot-changed', 'identity-absent'):
                return {'settled': False, 'evidence': observed}
            # The new Worker reports its exact stop action; the service independently
            # confirms the recorded execution has no survivor before committing it.
            kind = 'worker-stopped' if evidence == 'worker-stopped' and observed == 'identity-absent' else observed
            reserved = _reserve(board, attempt, record, kind)
            return {'settled': False, 'stopObserved': reserved is not None, 'evidence': reserved}
        observed = processes.inspect_process_identity(record)
        journal = _native_journal(board, attempt, record) if observed in ('alive', 'identity-absent') else None
        if observed == 'alive' or (observed == 'identity-absent' and journal is not None):
            candidate = {'attemptId': attempt['attempt_id'], 'processIdentity': record}
            if journal is not None:
                candidate['nativeJournal'] = journal
            candidates.append(candidate)
    if attempt_id is not None:
        with board.db.read() as connection:
            row = board._attempt_row(connection, attempt_id)
            payload = json.loads(row['result_json']) if row['result_json'] else {}
            confirmed = bool(row['shutdown_confirmed']) and payload.get('terminationReason') == 'interrupted'
            return {'settled': confirmed,
                    'evidence': (payload.get('result') or {}).get('interruptionEvidence', {}).get('kind')}
    response = {'candidates': candidates}
    if observe_attempt is not None:
        with board.db.read() as connection:
            observed = board._attempt_row(connection, observe_attempt)
            response['observedAttempt'] = {'attemptId': observe_attempt,
                                           'executionState': observed['execution_state'],
                                           'shutdownConfirmed': bool(observed['shutdown_confirmed'])}
    return response


def _reserve(board, snapshot: dict, identity: dict | None, kind: str) -> str | None:
    """Fence late receipts after complete stop evidence, before any Git work."""
    attempt_id = snapshot['attempt_id']
    with board.db.write() as connection:
        attempt = board._attempt_row(connection, attempt_id)
        if attempt['execution_state'] != 'uncertain' or read_identity(connection, attempt_id) != identity:
            return None
        board._require_effective_attempt(connection, attempt)
        pending = connection.execute('SELECT * FROM attempt_interruptions WHERE attempt_id=?', (attempt_id,)).fetchone()
        if pending is None:
            connection.execute("INSERT INTO attempt_interruptions VALUES(?,?,?,'sealing',NULL)",
                               (attempt_id, kind, board.now()))
        else:
            kind = pending['evidence_kind']
    return kind


def _settle(board, snapshot: dict, identity: dict | None, kind: str) -> int:
    attempt_id = snapshot['attempt_id']
    kind = _reserve(board, snapshot, identity, kind)
    if kind is None:
        return 0
    result = {'interruptionEvidence': {'kind': kind},
              'partialOutput': {'reason': 'interrupted', 'verified': False, 'final': False}}
    # No SQLite write transaction spans Git. A completed stop proof precedes all
    # sealing; the transaction below still fences a surviving original Worker.
    try:
        seal = board.workflow.seal_interrupted_turn(attempt_id)
        if seal is not None:
            result['partialWorkspaceSeal'] = seal
    except (BoardError, OSError) as error:
        detail = f'{error.code}: {error.message}' if isinstance(error, BoardError) else f'{type(error).__name__}: {error}'
        if isinstance(error, BoardError) and error.code in ('WORKSPACE_SCOPE_VIOLATION', 'WORKSPACE_CHANGED'):
            result['workspaceSealError'] = detail
        else:
            with board.db.write() as connection:
                row = connection.execute('SELECT seal_error FROM attempt_interruptions WHERE attempt_id=?', (attempt_id,)).fetchone()
                connection.execute('UPDATE attempt_interruptions SET seal_error=? WHERE attempt_id=?', (detail, attempt_id))
                if row['seal_error'] != detail:
                    board._append_event(connection, 'attempt.interruption_seal_pending', task_id=snapshot['task_id'],
                                        attempt_id=attempt_id, payload={'error': detail, 'evidence': kind})
                head = board._head_of(connection)
            board._notify(head)
            return 0
    result.update(board.workflow.interrupted_native_facts(attempt_id))
    with board.db.write() as connection:
        attempt = board._attempt_row(connection, attempt_id)
        if attempt['execution_state'] != 'uncertain' or read_identity(connection, attempt_id) != identity:
            return 0
        task = connection.execute('SELECT * FROM tasks WHERE task_id=?', (attempt['task_id'],)).fetchone()
        board._require_effective_attempt(connection, attempt)
        now = board.now()
        payload = {'status': 'failed', 'result': result, 'error': result.get('workspaceSealError', 'Execution was interrupted'),
                   'exitCode': None, 'signal': None, 'shutdownConfirmed': True,
                   'terminationReason': 'interrupted', 'artifacts': [], 'completedAt': now,
                   'logPaths': json.loads(attempt['log_paths']), 'runtimeIdentity': attempt['runtime_identity']}
        board._transition_attempt(connection, attempt, 'finished')
        connection.execute(
            "UPDATE attempts SET result_json=?,result_command_id=?,error=?,shutdown_confirmed=1,"
            "ownership='owned',finished_at=?,updated_at=?,revision=revision+1 WHERE attempt_id=?",
            (canonical_json(payload), 'interrupted:' + str(uuid.uuid4()), payload['error'], now, now, attempt_id))
        connection.execute("UPDATE attempt_interruptions SET state='settled',seal_error=NULL WHERE attempt_id=?", (attempt_id,))
        board._transition_task(connection, task, 'failed')
        connection.execute('UPDATE tasks SET active_attempt_id=NULL,queue_reason=NULL WHERE task_id=?',
                           (attempt['task_id'],))
        board._release_claims(connection, attempt_id)
        connection.execute(
            "UPDATE workers SET state='lost',current_attempt_id=NULL,revision=revision+1"
            ' WHERE worker_id=? AND current_attempt_id=?', (attempt['worker_id'], attempt_id))
        board._append_event(connection, 'task.failed', task_id=attempt['task_id'], attempt_id=attempt_id,
                            revision=task['revision'] + 1,
                            payload={'status': 'failed', 'taskState': 'failed', 'shutdownConfirmed': True,
                                     'terminationReason': 'interrupted', 'interruptionEvidence': result['interruptionEvidence']})
        attempt = board._attempt_row(connection, attempt_id)
        task = connection.execute('SELECT * FROM tasks WHERE task_id=?', (attempt['task_id'],)).fetchone()
        board.decisions.complete(connection, task=task, attempt=attempt, status='failed', result=result,
                                 shutdown_confirmed=True, error=payload['error'], now=now)
        board.workflow.settle_turn(connection, task=task, attempt=attempt, payload=payload, task_state='failed', now=now)
        board.workflow.child_settled(connection, task=task, attempt=attempt, payload=payload, task_state='failed', now=now)
        head = board._head_of(connection)
    board._notify(head)
    return 1
