"""One settlement path after the execution's complete identity proves it ended."""
from __future__ import annotations

import json
from pathlib import Path

from ...errors import BoardError
from ...protocol import process_identity as processes
from ..store.db import canonical_json


def read_identity(connection, attempt_id: str) -> dict | None:
    row = connection.execute('SELECT * FROM attempt_process_identities WHERE attempt_id=?',
                             (attempt_id,)).fetchone()
    if row is None:
        return None
    return {'bootId': row['boot_id'], 'pid': row['root_pid'], 'startedAt': row['root_started_at'],
            'processGroupId': row['process_group_id'], 'jobName': row['job_name']}


def record_identity(connection, attempt_id: str, record: dict) -> None:
    previous = read_identity(connection, attempt_id)
    if previous is not None:
        if previous != record:
            raise BoardError('CONFLICT', 'An execution process identity is immutable', attemptId=attempt_id)
        return
    connection.execute('INSERT INTO attempt_process_identities VALUES(?,?,?,?,?,?)',
                       (attempt_id, record['bootId'], record['pid'], record['startedAt'],
                        record['processGroupId'], record['jobName']))


def _directory(board, attempt):
    from ...private_dirs import attempt_root
    adapter = attempt['model_harness'] if attempt['adapter'] == 'decision' else attempt['adapter']
    return attempt_root(board.directory, adapter, attempt['task_id'], attempt['attempt_id'])


def _marker_identity(board, attempt) -> dict | None:
    """The spawn marker closes the crash window before identity publication."""
    from ...private_dirs import open_regular_fd
    import os
    binding = {'taskId': attempt['task_id'], 'attemptId': attempt['attempt_id'],
               'generation': attempt['generation']}
    path = board.directory / 'attempts' / attempt['task_id'] / attempt['attempt_id'] / 'spawn.marker'
    try:
        fd = open_regular_fd(path, os.O_RDONLY)
        with os.fdopen(fd) as source:
            raw = source.read(16385)
        if len(raw) > 16384:
            raise BoardError('INVALID_ARGUMENT', 'Execution spawn marker is too large')
        value = json.loads(raw)
        if not isinstance(value, dict) or any(value.get(key) != item for key, item in binding.items()):
            raise BoardError('INVALID_ARGUMENT', 'Execution spawn marker has a different binding')
        record = value['processIdentity']
        processes.validate_process_identity(record)
        return record
    except FileNotFoundError:
        return None


def _stop_native(board, attempt, identity, observed):
    if observed == 'boot-changed':
        return True, 'boot-changed'
    binding = {'taskId': attempt['task_id'], 'attemptId': attempt['attempt_id'],
               'generation': attempt['generation']}
    journal = processes.read_native_process_journal(
        processes.native_process_journal_path(_directory(board, attempt), identity), binding)
    if journal is None:
        return False, None
    facts = [processes.stop_orphan_process(native) for native in journal['identities']]
    confirmed = not journal['pending'] and all(fact in ('boot-changed', 'identity-absent', 'orphan-stopped')
                                               for fact in facts)
    return confirmed, ('orphan-stopped' if 'orphan-stopped' in facts else 'identity-absent') if confirmed else None


def _reserve(board, snapshot, identity, kind):
    with board.db.write() as connection:
        attempt = board._attempt_row(connection, snapshot['attempt_id'])
        if attempt['execution_state'] == 'finished' or read_identity(connection, attempt['attempt_id']) != identity:
            return False
        board._require_effective_attempt(connection, attempt)
        connection.execute("INSERT OR IGNORE INTO attempt_interruptions VALUES(?,?,?,'sealing',NULL)",
                           (attempt['attempt_id'], kind, board.now()))
    return True


def _partial_result(board, attempt, identity, kind):
    """Seal only after native shutdown is proved; keep retryable seal evidence."""
    if not _reserve(board, attempt, identity, kind):
        return None
    result = {'interruptionEvidence': {'kind': kind},
              'partialOutput': {'reason': 'interrupted', 'verified': False, 'final': False}}
    try:
        seal = board.workflow.seal_interrupted_turn(attempt['attempt_id'])
        if seal is not None:
            result['partialWorkspaceSeal'] = seal
    except (BoardError, OSError) as error:
        detail = f'{error.code}: {error.message}' if isinstance(error, BoardError) else f'{type(error).__name__}: {error}'
        if isinstance(error, BoardError) and error.code in ('WORKSPACE_SCOPE_VIOLATION', 'WORKSPACE_CHANGED'):
            result['workspaceSealError'] = detail
        else:
            with board.db.write() as connection:
                row = connection.execute('SELECT seal_error FROM attempt_interruptions WHERE attempt_id=?',
                                         (attempt['attempt_id'],)).fetchone()
                connection.execute('UPDATE attempt_interruptions SET seal_error=? WHERE attempt_id=?',
                                   (detail, attempt['attempt_id']))
                if row['seal_error'] != detail:
                    board._append_event(connection, 'attempt.interruption_seal_pending', task_id=attempt['task_id'],
                                        attempt_id=attempt['attempt_id'], payload={'error': detail, 'evidence': kind})
                head = board._head_of(connection)
            board._notify(head)
            return None
    result.update(board.workflow.interrupted_native_facts(attempt['attempt_id']))
    return result


def _stop_reason(board, attempt):
    from datetime import datetime, timezone
    with board.db.read() as connection:
        deadline_event = connection.execute("SELECT payload_json FROM events WHERE attempt_id=? AND kind='execution.deadline'"
                                            " ORDER BY seq DESC LIMIT 1", (attempt['attempt_id'],)).fetchone()
    if deadline_event is not None:
        return json.loads(deadline_event['payload_json'])['reason']
    initiator = attempt['stop_initiator']
    deadline = attempt['grace_deadline']
    if deadline and initiator in ('host', 'lead'):
        if datetime.now(timezone.utc) >= datetime.fromisoformat(deadline.replace('Z', '+00:00')):
            return 'grace-expired'
        return 'forced-stop'
    return {'console': 'console-stop', 'service': 'service-stop', 'unknown': 'unknown-stop'}.get(initiator,
                                                                                             'forced-stop')


def recover(board, *, attempt_id: str | None = None) -> list[dict]:
    from ...protocol.execution import read_execution_receipt
    with board.db.read() as connection:
        rows = connection.execute(
            "SELECT * FROM attempts WHERE execution_state!='finished'" +
            (' AND attempt_id=?' if attempt_id is not None else ''),
            () if attempt_id is None else (attempt_id,)).fetchall()
        snapshots = [(dict(row), read_identity(connection, row['attempt_id'])) for row in rows]
    settled = []
    for attempt, identity in snapshots:
        if identity is None:
            try:
                identity = _marker_identity(board, attempt)
            except (OSError, BoardError, ValueError, KeyError, TypeError):
                # A malformed boundary record proves neither identity nor death.
                continue
            if identity is not None:
                with board.db.write() as connection:
                    record_identity(connection, attempt['attempt_id'], identity)
        if identity is None:
            continue
        observed = processes.inspect_process_identity(identity)
        if observed not in ('boot-changed', 'identity-absent'):
            continue
        directory = _directory(board, attempt)
        receipt = read_execution_receipt(directory)
        if receipt is not None:
            # A different process produced the private envelope; the result API
            # validates the report, artifacts and effective attempt exactly once.
            expected = {'taskId': attempt['task_id'], 'attemptId': attempt['attempt_id'],
                        'generation': attempt['generation']}
            if (not isinstance(receipt, dict) or any(receipt.get(key) != item for key, item in expected.items())
                    or not isinstance(receipt.get('commandId'), str)
                    or not isinstance(receipt.get('report'), dict)
                    or 'nonce' in receipt):
                raise BoardError('INVALID_RESULT_RECEIPT', 'Private execution receipt has an invalid envelope')
            report = dict(receipt['report'])
            if report.get('shutdownConfirmed') is not True:
                report['shutdownConfirmed'], _ = _stop_native(board, attempt, identity, observed)
            response = board.execution_result({**report, 'attemptId': attempt['attempt_id'], 'generation': attempt['generation'],
                                                'commandId': receipt['commandId']}, recovered=True)
        else:
            confirmed, kind = _stop_native(board, attempt, identity, observed)
            if attempt['result_json'] is not None and not confirmed:
                continue  # No new shutdown proof may rewrite an earlier uncertain receipt.
            result = (_partial_result(board, attempt, identity, kind) if confirmed else
                      {'partialOutput': {'reason': 'interrupted', 'verified': False, 'final': False}})
            if result is None:
                continue
            if attempt['result_json'] is not None:
                result['previousReceipt'] = json.loads(attempt['result_json'])
            stopped = attempt['cancel_requested_at'] is not None or attempt['stop_initiator'] is not None
            reason = _stop_reason(board, attempt) if stopped else 'interrupted'
            result['partialOutput']['reason'] = reason
            report = {'status': 'cancelled' if stopped else 'failed', 'result': result,
                      'error': result.get('workspaceSealError', 'Execution stopped before result delivery' if stopped
                                          else 'Execution was interrupted'),
                      'exitCode': None, 'signal': None, 'shutdownConfirmed': confirmed, 'artifacts': [],
                      'terminationReason': reason if confirmed or not stopped else None,
                      'logPaths': json.loads(attempt['log_paths']), 'runtimeIdentity': attempt['runtime_identity']}
            response = board.execution_result({**report, 'attemptId': attempt['attempt_id'],
                'generation': attempt['generation'], 'commandId': f"interrupted:{attempt['attempt_id']}"}, recovered=True)
        settled.append(response)
    return settled
