"""The service's single receipt-first settlement path after execution death."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from ...errors import BoardError
from ...protocol import process_identity as processes


def read_identity(connection, attempt_id: str) -> dict | None:
    row = connection.execute('SELECT * FROM attempt_process_identities WHERE attempt_id=?', (attempt_id,)).fetchone()
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


def _evidence_directory(board, attempt):
    return board.directory / 'attempts' / attempt['task_id'] / attempt['attempt_id']


def _marker_identity(board, attempt) -> dict | None:
    """Validate the creator's durable identity record at this file boundary."""
    from ...private_dirs import open_regular_fd
    path = _evidence_directory(board, attempt) / 'spawn.marker'
    try:
        fd = open_regular_fd(path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(fd) as source:
        raw = source.read(16385)
    if len(raw) > 16384:
        raise BoardError('INVALID_ARGUMENT', 'Execution spawn marker is too large')
    value = json.loads(raw)
    binding = {'taskId': attempt['task_id'], 'attemptId': attempt['attempt_id'], 'generation': attempt['generation']}
    if not isinstance(value, dict) or set(value) != {*binding, 'processIdentity'} or any(
            value[key] != item for key, item in binding.items()):
        raise BoardError('INVALID_ARGUMENT', 'Execution spawn marker has a different binding')
    return processes.validate_process_identity(value['processIdentity'])


def _stop_native(board, attempt, identity, observed):
    """The caller has established execution death; only native work remains."""
    if observed == 'boot-changed':
        return True, 'boot-changed'
    # Native process creation is behind the acknowledged identity publication.
    # A reaped child that never reached it cannot have created native work.
    if attempt['identity_confirmed_at'] is None:
        return True, observed
    binding = {'taskId': attempt['task_id'], 'attemptId': attempt['attempt_id'], 'generation': attempt['generation']}
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
        # A retry may gain stronger native-stop evidence before it has a result.
        # The interruption record and the eventual result must name that same fact.
        connection.execute("INSERT INTO attempt_interruptions VALUES(?,?,?,'sealing',NULL)"
                           " ON CONFLICT(attempt_id) DO UPDATE SET evidence_kind=excluded.evidence_kind,"
                           "observed_at=excluded.observed_at",
                           (attempt['attempt_id'], kind, board.now()))
    return True


def _partial_result(board, attempt, identity, kind, *, confirmed):
    if not _reserve(board, attempt, identity, kind):
        return None
    result = {'interruptionEvidence': {'kind': kind},
              'partialOutput': {'reason': 'interrupted', 'verified': False, 'final': False}}
    if not confirmed:
        return result
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


def _stop_reason(attempt):
    if attempt['force_reason'] is not None:
        return attempt['force_reason']
    initiator, deadline = attempt['stop_initiator'], attempt['grace_deadline']
    if deadline and initiator in ('host', 'lead'):
        if datetime.now(timezone.utc) >= datetime.fromisoformat(deadline.replace('Z', '+00:00')):
            return 'grace-expired'
        return 'forced-stop'
    return {'console': 'console-stop', 'service': 'service-stop', 'unknown': 'unknown-stop'}.get(initiator,
                                                                                             'forced-stop')


def _cleanup_dead_material(board, attempt, identity, observation):
    from ...protocol.execution import STARTUP, LIVE, cleanup_execution_endpoint
    directory = _directory(board, attempt)
    cleaned = True
    try:
        if identity is not None:
            outcome = cleanup_execution_endpoint(directory, identity, state_dir=board.directory,
                                                 stopped_evidence=observation)
            if outcome['status'] == 'unverified':
                raise BoardError('LIVE_CLEANUP_UNVERIFIED', outcome['reason'])
            (directory / LIVE).unlink(missing_ok=True)
        elif (directory / LIVE).exists():
            raise BoardError('LIVE_CLEANUP_UNVERIFIED', 'Execution identity was not captured')
        board.clear_execution_issue(attempt['attempt_id'], 'endpoint-cleanup')
    except Exception as error:
        cleaned = False
        board.record_execution_issue(attempt['attempt_id'], 'endpoint-cleanup', error)
    finally:
        # The endpoint reader retains its credential until reap succeeds. It is
        # the retry evidence for a failed cleanup, even after result settlement.
        try:
            (directory / STARTUP).unlink(missing_ok=True)
            board.clear_execution_issue(attempt['attempt_id'], 'private-material-cleanup')
        except OSError as error:
            cleaned = False
            board.record_execution_issue(attempt['attempt_id'], 'private-material-cleanup', error)
    if cleaned:
        board.complete_execution_cleanup(attempt['attempt_id'])


def _recover_one(board, attempt, identity, stopped_evidence):
    from ...protocol.execution import read_execution_receipt
    if stopped_evidence is None:
        if identity is None:
            identity = _marker_identity(board, attempt)
            if identity is not None:
                with board.db.write() as connection:
                    record_identity(connection, attempt['attempt_id'], identity)
        if identity is None:
            with board.db.read() as connection:
                proof = connection.execute('SELECT detail_json FROM attempt_shutdown_evidence WHERE attempt_id=?',
                                           (attempt['attempt_id'],)).fetchone()
            if proof is not None:
                stopped_evidence = json.loads(proof['detail_json'])
            # Intent is fsynced before creation. Its absence closes the admission
            # commit -> startup-material crash window without any elapsed-time guess.
            elif attempt['execution_state'] == 'uncertain' and not (
                    _evidence_directory(board, attempt) / 'spawn.intent').exists():
                return board.release_execution(attempt['attempt_id'], reason='No execution process was created')
            else:
                return None
        if stopped_evidence is None:
            observed = processes.inspect_process_identity(identity)
            if observed not in ('boot-changed', 'identity-absent'):
                return None
    if stopped_evidence is not None:
            # Only the service's execution observer calls this internal path. It has
        # already inspected this identity or reaped its own pre-identity child.
        observed = stopped_evidence['kind']
    try:
        if attempt['execution_state'] == 'finished':
            return None  # Only pending endpoint/credential cleanup remains.
        if attempt['result_json'] is not None:
            # The committed result is authoritative even if its private receipt
            # survived an exit between commit and unlink. Do not replay or replace it.
            confirmed, kind = _stop_native(board, attempt, identity, observed)
            if not confirmed:
                return None
            evidence = {**(stopped_evidence or {}), 'kind': kind, 'execution': observed}
            original = json.loads(attempt['result_json'])
            if original['status'] == 'cancelled' and original.get('terminationReason') is None:
                evidence['terminationReason'] = (_stop_reason(attempt) if attempt['stop_initiator'] is not None
                                                 or attempt['cancel_requested_at'] is not None else 'unknown-stop')
            with board.db.read() as connection:
                interrupted = connection.execute('SELECT evidence_kind FROM attempt_interruptions WHERE attempt_id=?',
                                                 (attempt['attempt_id'],)).fetchone()
            if interrupted is not None:
                # The first uncertain recovery could not safely seal native work.
                # Preserve its immutable result; pin the newly safe output beside
                # the supplementary stop proof and use the ordinary turn import.
                partial = _partial_result(board, attempt, identity, interrupted['evidence_kind'], confirmed=True)
                if partial is None:
                    return None
                for key in ('partialWorkspaceSeal', 'workspaceSealError'):
                    if key in partial:
                        evidence[key] = partial[key]
            return board.confirm_execution_shutdown(attempt['attempt_id'], evidence)
        directory = _directory(board, attempt)
        receipt = read_execution_receipt(directory, binding={
            'taskId': attempt['task_id'], 'attemptId': attempt['attempt_id'], 'generation': attempt['generation']})
        if receipt is not None:
            response = board.execution_result({**receipt['report'], 'attemptId': attempt['attempt_id'],
                'generation': attempt['generation'], 'commandId': receipt['commandId']}, recovered=True,
                stop_evidence={**(stopped_evidence or {}), 'kind': observed, 'native': 'receipt'})
            if not response['attempt']['shutdownConfirmed']:
                confirmed, kind = _stop_native(board, attempt, identity, observed)
                if confirmed:
                    response = board.confirm_execution_shutdown(attempt['attempt_id'], {
                        'kind': kind, 'execution': observed})
            return response
        confirmed, kind = _stop_native(board, attempt, identity, observed)
        # Creator reaping is stop evidence, not a historical interruption enum.
        evidence_kind = kind if kind in ('boot-changed', 'identity-absent', 'orphan-stopped') else 'identity-absent'
        result = _partial_result(board, attempt, identity, evidence_kind, confirmed=confirmed)
        if result is None:
            return None
        stopped = attempt['cancel_requested_at'] is not None or attempt['stop_initiator'] is not None
        reason = _stop_reason(attempt) if stopped else 'interrupted'
        result['partialOutput']['reason'] = reason
        report = {'status': 'cancelled' if stopped else 'failed', 'result': result,
                  'error': result.get('workspaceSealError', 'Execution stopped before result delivery' if stopped
                                      else 'Execution was interrupted'),
                  'exitCode': None, 'signal': None, 'shutdownConfirmed': confirmed, 'artifacts': [],
                  'terminationReason': reason if confirmed or not stopped else None,
                  'logPaths': json.loads(attempt['log_paths']), 'runtimeIdentity': attempt['runtime_identity']}
        return board.execution_result({**report, 'attemptId': attempt['attempt_id'],
            'generation': attempt['generation'], 'commandId': f"interrupted:{attempt['attempt_id']}"}, recovered=True,
            stop_evidence={**(stopped_evidence or {}), 'kind': kind, 'execution': observed})
    finally:
        _cleanup_dead_material(board, attempt, identity, observed)


def recover(board, *, attempt_id: str | None = None, stopped_evidence: dict | None = None) -> list[dict]:
    with board.db.read() as connection:
        rows = connection.execute(
            "SELECT * FROM attempts WHERE (execution_state!='finished' OR cleanup_pending=1)" +
            (' AND attempt_id=?' if attempt_id is not None else '') + ' ORDER BY created_at,attempt_id',
            () if attempt_id is None else (attempt_id,)).fetchall()
        snapshots = [(dict(row), read_identity(connection, row['attempt_id'])) for row in rows]
    settled = []
    for attempt, identity in snapshots:
        try:
            response = _recover_one(board, attempt, identity, stopped_evidence)
            if response is not None:
                settled.append(response)
        except Exception as error:
            # A corrupt receipt is evidence about this attempt, never a reason to
            # stop the service or postpone every later execution's settlement.
            board.record_execution_issue(attempt['attempt_id'], 'recovery', error)
    return settled
