"""Board-proven historical Worker sessions, read only.

Native identities come from authoritative receipts alone: this module never
opens a user's native session files and never calls a native harness. Codex
history is deliberately observation-only; DSH sessions are execution-private
since ADR-021 decision 18, so no DSH listing or native archive exists.
"""
from __future__ import annotations

import json

from .errors import BoardError
from .schemas import optional_string, reject_unknown


def _completed_codex_turn(result: dict, row) -> dict | None:
    """Older receipts prove root creation through the strict native turn record."""
    turn = result.get('turn')
    if (not isinstance(turn, dict) or turn.get('taskId') != row['task_id']
            or turn.get('attemptId') != row['attempt_id']):
        return None
    provenance = turn.get('provenance')
    if (not isinstance(provenance, dict) or provenance.get('adapter') != 'codex'
            or provenance.get('nativeThreadId') != turn.get('sessionId')
            or provenance.get('nativeTurnStarted') is not True
            or provenance.get('nativeTurnCompleted') is not True
            or provenance.get('turnEnd') != 'completed'):
        return None
    return turn


def _inventory(store) -> list[dict]:
    rows = []
    with store.db.read() as db:
        attempts = db.execute(
            "SELECT a.attempt_id,a.task_id,a.execution_state,a.shutdown_confirmed,a.result_json "
            "FROM attempts a WHERE a.adapter='codex' AND a.result_json IS NOT NULL ORDER BY a.attempt_id"
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
                native = {'adapter': 'codex', 'sessionId': completed_turn.get('sessionId'),
                          'captured': True, 'storageOwner': 'harness-user-store',
                          'resumeMode': completed_turn.get('resumeMode'), 'nativeAppVisibility': 'unknown'}
            if not isinstance(native, dict) or native.get('adapter') != 'codex':
                continue
            session_id = native.get('sessionId')
            if not isinstance(session_id, str) or not session_id or not native.get('captured'):
                continue
            # A stored native thread identity plus an own stopped attempt is
            # provenance. Never infer ownership from an app title or path.
            if row['execution_state'] != 'finished' or not row['shutdown_confirmed']:
                continue
            if native.get('storageOwner') not in {'harness-user-store', 'buddy-goal'}:
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
            rows.append({'adapter': 'codex', 'taskId': row['task_id'], 'attemptId': row['attempt_id'],
                         'sessionId': session_id, 'shutdownConfirmed': bool(row['shutdown_confirmed']),
                         'executionState': row['execution_state'], 'resumeMode': mode,
                         'storageOwner': native['storageOwner'],
                         'nativeAppVisibility': native.get('nativeAppVisibility')})
        except (TypeError, ValueError, KeyError):
            continue
    created = {(row['taskId'], row['sessionId']) for row in rows
               if row['resumeMode'] in {'initial', 'reconstructed-new-session'}}
    threads = {}
    for row in rows:
        key = (row['taskId'], row['sessionId'])
        if key not in created:
            continue
        if key not in threads:
            threads[key] = {**row, 'attemptIds': []}
        threads[key]['attemptIds'].append(row['attemptId'])
    return list(threads.values())


def handle(store, params: dict) -> dict:
    reject_unknown(params, {'action', 'adapter'}, 'worker.sessions')
    action = optional_string(params, 'action', max_length=16) or 'list'
    adapter = optional_string(params, 'adapter', max_length=16) or 'codex'
    if action != 'list':
        raise BoardError('INVALID_ARGUMENT', 'action must be list; DSH grouped-session archiving was removed with the grouping feature (ADR-021)')
    if adapter != 'codex':
        raise BoardError('UNSUPPORTED', 'Only Codex history is listed here; DSH sessions are execution-private and its grouped listing and archiving were removed (ADR-021)')
    rows = _inventory(store)
    return {'adapter': adapter, 'sessions': rows, 'count': len(rows),
            'nativeAccess': False, 'codexAction': 'confirm native archive/delete separately'}
