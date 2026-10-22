"""Board-proven historical Worker sessions, read only.

Native identities come from authoritative receipts alone: this module never
opens a user's native session files and never calls a native harness. Codex
history listing is read only; the deletion engine selects proven identities
through this same inventory. DSH sessions are execution-private
since ADR-021 decision 18, so no DSH listing or native archive exists.
"""
from __future__ import annotations

from contextlib import nullcontext
import json

from ...protocol.schemas import reject_unknown


def _completed_codex_turn(result: dict, row) -> dict | None:
    """Older receipts prove root creation through the strict native turn record."""
    turn = result.get('turn')
    if (not isinstance(turn, dict) or turn.get('taskId') != row['task_id']
            or turn.get('attemptId') != row['attempt_id']):
        return None
    provenance = turn.get('provenance')
    if (not isinstance(provenance, dict) or provenance.get('harness') != 'codex'
            or provenance.get('nativeThreadId') != turn.get('sessionId')
            or provenance.get('nativeTurnStarted') is not True
            or provenance.get('nativeTurnCompleted') is not True
            or provenance.get('turnEnd') != 'completed'):
        return None
    return turn


def _inventory(store, task_ids: set[str] | None = None, *, connection=None) -> list[dict]:
    if task_ids is not None and not task_ids:
        return []
    with store.db.read() if connection is None else nullcontext(connection) as snapshot:
        rows = _session_receipts(snapshot,task_ids)
    return _created_sessions(rows)


def _session_receipts(connection, task_ids: set[str] | None = None, *, session_ids=None) -> list[dict]:
    """Read bound native identities within the caller's existing snapshot."""
    rows = []
    task_filter = " AND a.task_id IN (SELECT value FROM json_each(?))" if task_ids is not None else ""
    args = [json.dumps(sorted(task_ids))] if task_ids is not None else []
    if session_ids is not None:
        valid = "CASE WHEN json_valid(a.result_json) THEN a.result_json END"
        task_filter += (f" AND (json_extract({valid},'$.result.nativeSession.sessionId') IN (SELECT value FROM json_each(?))"
                        f" OR json_extract({valid},'$.result.turn.sessionId') IN (SELECT value FROM json_each(?)))")
        args.extend([json.dumps(sorted(session_ids))] * 2)
    attempts = connection.execute(
        "SELECT a.attempt_id,a.task_id,a.execution_state,a.shutdown_confirmed,a.result_json "
        "FROM attempts a WHERE a.adapter='codex' AND a.result_json IS NOT NULL" + task_filter
        + " ORDER BY a.attempt_id", args
    ).fetchall()
    for row in attempts:
        try:
            receipt = json.loads(row['result_json'])
            if not isinstance(receipt, dict):
                continue
            result = receipt.get('result')
            native = result.get('nativeSession') if isinstance(result, dict) else None
            completed_turn = _completed_codex_turn(result, row) if isinstance(result, dict) else None
            if not isinstance(native, dict) and completed_turn is not None:
                native = {'harness': 'codex', 'sessionId': completed_turn.get('sessionId'),
                          'captured': True, 'storageOwner': 'harness-user-store',
                          'resumeMode': completed_turn.get('resumeMode'), 'nativeAppVisibility': 'unknown'}
            if not isinstance(native, dict) or native.get('harness') != 'codex':
                continue
            session_id = native.get('sessionId')
            if not isinstance(session_id, str) or not session_id or not native.get('captured'):
                continue
            if native.get('storageOwner') not in {'harness-user-store', 'hey-my-buddy-micro-task'}:
                continue
            checkpoint = result.get('nativeCheckpoint')
            checkpoint_bound = (isinstance(checkpoint, dict) and checkpoint.get('version') == 1
                and checkpoint.get('sessionId') == session_id
                and checkpoint.get('taskId') == row['task_id']
                and checkpoint.get('attemptId') == row['attempt_id'])
            if not checkpoint_bound and (completed_turn is None or completed_turn.get('sessionId') != session_id):
                continue
            mode = native.get('resumeMode')
            if mode not in {'initial', 'reconstructed-new-session', 'native-session'}:
                continue
            rows.append({'harness': 'codex', 'taskId': row['task_id'], 'attemptId': row['attempt_id'],
                         'sessionId': session_id, 'shutdownConfirmed': bool(row['shutdown_confirmed']),
                         'executionState': row['execution_state'], 'resumeMode': mode,
                         'storageOwner': native['storageOwner'],
                         'nativeAppVisibility': native.get('nativeAppVisibility')})
        except (TypeError, ValueError, KeyError):
            continue
    return rows


def _created_sessions(rows):
    rows = [row for row in rows if row['executionState'] == 'finished' and row['shutdownConfirmed']]
    created = {(row['taskId'],row['sessionId']) for row in rows
               if row['resumeMode'] in {'initial','reconstructed-new-session'}}
    threads = {}
    for row in rows:
        key = (row['taskId'],row['sessionId'])
        if key not in created:
            continue
        if key not in threads:
            threads[key] = {**row,'attemptIds':[]}
        threads[key]['attemptIds'].append(row['attemptId'])
    return list(threads.values())


def session_references(connection, session_ids: set[str]) -> list[dict]:
    """Exact user-store references, including interrupted turns without a receipt.

    Preparing a native continuation writes previous_session_id before its
    execution returns a result receipt. That pending continuation retains the
    thread even if its execution has not produced a native receipt yet.
    """
    if not session_ids:
        return []
    references = [row for row in _session_receipts(connection,session_ids=session_ids)
                  if row['storageOwner'] == 'harness-user-store']
    ids = json.dumps(sorted(session_ids))
    for row in connection.execute(
            'SELECT t.run_id,t.previous_session_id FROM workflow_turns t'
            ' JOIN attempts a ON a.attempt_id=t.attempt_id'
            " WHERE a.adapter='codex' AND a.result_json IS NULL AND t.resume_mode='native-session'"
            ' AND t.previous_session_id IN (SELECT value FROM json_each(?))',(ids,)):
        references.append({'taskId':row['run_id'],'harness':'codex','sessionId':row['previous_session_id']})
    return references


def deletable_sessions(store, task_ids: set[str], *, connection=None) -> list[dict]:
    """Proven created sessions in the harness user store for execution task IDs.

    Return the inventory records, including task/attempt bindings. Private-root
    sessions are removed with their root by the deletion engine.
    """
    return [row for row in _inventory(store, task_ids, connection=connection) if row['storageOwner'] == 'harness-user-store']


def handle(store, params: dict) -> dict:
    reject_unknown(params, set(), 'worker.sessions')
    rows = _inventory(store)
    return {'harness': 'codex', 'sessions': rows, 'count': len(rows)}
