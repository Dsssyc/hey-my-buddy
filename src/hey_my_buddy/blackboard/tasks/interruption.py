"""Recover stopped attempts from durable process facts, never from lease age."""
from __future__ import annotations

from datetime import datetime
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
        connection.execute('UPDATE attempt_process_identities SET shutdown_confirmed=1 WHERE attempt_id=?', (attempt_id,))
    worker = record['worker']
    connection.execute(
        'INSERT INTO attempt_process_identities VALUES(?,?,?,?,?,?,?,?,?,?,0)',
        (attempt_id, ordinal, record['bootId'], record['pid'], record['startedAt'], record['processGroupId'],
         record['jobName'], worker['bootId'], worker['pid'], worker['startedAt']))


def legacy_boot_evidence(started_at: str) -> bool:
    """The explicit legacy form proves only executions from before this boot."""
    try:
        boot = processes.boot_started_at()
    except OSError:
        return False
    return datetime.fromisoformat(started_at.replace('Z', '+00:00')).timestamp() < boot


def _snapshot(board, *, legacy_only: bool = False) -> list[tuple[dict, dict | None, str | None]]:
    with board.db.read() as connection:
        rows = connection.execute(
            "SELECT a.*,l.started_at AS legacy_started_at FROM attempts a"
            " LEFT JOIN attempt_legacy_starts l ON l.attempt_id=a.attempt_id"
            " WHERE a.execution_state='uncertain'" +
            (' AND l.attempt_id IS NOT NULL' if legacy_only else '')).fetchall()
        return [(dict(row), read_identity(connection, row['attempt_id']), row['legacy_started_at'])
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
    identities = value['identities']
    # The stopped controller is the only writer, so these identities cannot grow
    # between this read and the durable import. The journal is a process boundary.
    with board.db.write() as connection:
        stored = connection.execute(
            'SELECT COUNT(*) FROM attempt_native_process_identities WHERE attempt_id=?',
            (attempt['attempt_id'],)).fetchone()[0]
        if stored and stored != len(identities):
            raise BoardError('CONFLICT', 'Stopped native process journal changed')
        for ordinal, record in enumerate(identities):
            worker = record['worker']
            values = (attempt['attempt_id'], ordinal, record['bootId'], record['pid'], record['startedAt'],
                      record['processGroupId'], record['jobName'], worker['bootId'], worker['pid'], worker['startedAt'])
            previous = connection.execute(
                'SELECT * FROM attempt_native_process_identities WHERE attempt_id=? AND ordinal=?',
                (attempt['attempt_id'], ordinal)).fetchone()
            if previous is None:
                connection.execute('INSERT INTO attempt_native_process_identities VALUES(?,?,?,?,?,?,?,?,?,?)', values)
            elif tuple(previous) != values:
                raise BoardError('CONFLICT', 'A native execution process identity is immutable')
    return identities


def _evidence(board, attempt: dict, record: dict | None, legacy_start: str | None) -> str:
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
    if legacy_start is not None and legacy_boot_evidence(legacy_start):
        return 'boot-changed'
    return 'unknown'


def recover(board, *, legacy_only: bool = False) -> int:
    settled = 0
    for attempt, record, legacy_start in _snapshot(board, legacy_only=legacy_only):
        # A surviving original Worker still owns collection and its durable
        # receipt, including a child that completed while the Worker was asleep.
        if record is not None and processes.worker_identity_alive(record) is not False:
            continue
        evidence = _evidence(board, attempt, record, legacy_start)
        if evidence in ('boot-changed', 'identity-absent'):
            settled += _settle(board, attempt, record, evidence, legacy_start)
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
    if attempt_id is None:
        recover(board)
    candidates = []
    for attempt, record, legacy_start in _snapshot(board):
        if record is None or processes.worker_identity_alive(record) is not False:
            continue
        observed = _evidence(board, attempt, record, legacy_start)
        if attempt_id == attempt['attempt_id']:
            if observed not in ('boot-changed', 'identity-absent'):
                return {'settled': False, 'evidence': observed}
            # The new Worker reports its exact stop action; the service independently
            # confirms the recorded execution has no survivor before committing it.
            kind = 'worker-stopped' if evidence == 'worker-stopped' and observed == 'identity-absent' else observed
            return {'settled': bool(_settle(board, attempt, record, kind, legacy_start)), 'evidence': kind}
        if observed == 'alive' or _native_journal(board, attempt, record) is not None:
            candidate = {'attemptId': attempt['attempt_id'], 'processIdentity': record}
            journal = _native_journal(board, attempt, record)
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


def _settle(board, snapshot: dict, identity: dict | None, kind: str, legacy_start: str | None) -> int:
    attempt_id = snapshot['attempt_id']
    # Reserve the proven stop before Git side effects. A normal durable result
    # that won this transaction is kept; after this point the recovery owns the
    # finalization, and failed sealing is retried by the existing sweep.
    with board.db.write() as connection:
        attempt = board._attempt_row(connection, attempt_id)
        if attempt['execution_state'] != 'uncertain' or read_identity(connection, attempt_id) != identity:
            return 0
        board._require_effective_attempt(connection, attempt)
        pending = connection.execute('SELECT * FROM attempt_interruptions WHERE attempt_id=?', (attempt_id,)).fetchone()
        if pending is None:
            connection.execute("INSERT INTO attempt_interruptions VALUES(?,?,?,'sealing',NULL)",
                               (attempt_id, kind, board.now()))
        else:
            kind = pending['evidence_kind']
    result = {'interruptionEvidence': {'kind': kind, 'legacyStartedAt': legacy_start},
              'partialOutput': {'reason': 'interrupted', 'verified': False, 'final': False}}
    # No SQLite write transaction spans Git. A completed stop proof precedes all
    # sealing; the transaction below still fences a surviving original Worker.
    try:
        seal = board.workflow.seal_interrupted_turn(attempt_id)
        if seal is not None:
            result['partialWorkspaceSeal'] = seal
    except (BoardError, OSError) as error:
        detail = f'{error.code}: {error.message}' if isinstance(error, BoardError) else f'{type(error).__name__}: {error}'
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
        payload = {'status': 'failed', 'result': result, 'error': 'Execution was interrupted',
                   'exitCode': None, 'signal': None, 'shutdownConfirmed': True,
                   'terminationReason': 'interrupted', 'artifacts': [], 'completedAt': now,
                   'logPaths': json.loads(attempt['log_paths']), 'runtimeIdentity': attempt['runtime_identity']}
        board._transition_attempt(connection, attempt, 'finished')
        connection.execute('UPDATE attempt_process_identities SET shutdown_confirmed=1 WHERE attempt_id=?', (attempt_id,))
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
            "UPDATE workers SET state='idle',current_attempt_id=NULL,revision=revision+1"
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
