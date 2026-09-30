"""Board-proven historical Worker sessions and guarded DSH archive plans.

The DSH bridge owns native registry mutations. This module never opens a user's
native session files, and Codex history is deliberately observation-only.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import tempfile
import threading
import uuid
from pathlib import Path

from .errors import BoardError
from . import private_dirs
from .schemas import optional_string, reject_unknown

_apply_lock = threading.Lock()


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


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


def _inventory(store, adapter: str) -> list[dict]:
    rows = []
    with store.db.read() as db:
        attempts = db.execute(
            "SELECT a.attempt_id,a.task_id,a.execution_state,a.shutdown_confirmed,a.result_json,t.cwd "
            "FROM attempts a JOIN tasks t ON t.task_id=a.task_id WHERE a.adapter=? "
            "AND a.result_json IS NOT NULL ORDER BY a.attempt_id", (adapter,)
        ).fetchall()
    for row in attempts:
        try:
            receipt = json.loads(row['result_json'])
            if not isinstance(receipt, dict):
                continue
            result = receipt.get('result')
            native = result.get('nativeSession') if isinstance(result, dict) else None
            completed_turn = _completed_codex_turn(result, row) if adapter == 'codex' and isinstance(result, dict) else None
            if not isinstance(native, dict) and completed_turn is not None:
                native = {'adapter': 'codex', 'sessionId': completed_turn.get('sessionId'),
                          'captured': True, 'storageOwner': 'harness-user-store',
                          'resumeMode': completed_turn.get('resumeMode'), 'nativeAppVisibility': 'unknown'}
            if not isinstance(native, dict) or native.get('adapter') != adapter:
                continue
            session_id = native.get('sessionId')
            if not isinstance(session_id, str) or not session_id or not native.get('captured'):
                continue
            base = {'adapter': adapter, 'taskId': row['task_id'], 'attemptId': row['attempt_id'],
                    'sessionId': session_id, 'shutdownConfirmed': bool(row['shutdown_confirmed']),
                    'executionState': row['execution_state']}
            if adapter == 'dsh':
                workspace = result.get('workspace')
                if not isinstance(workspace, dict) or workspace.get('bound') is not True:
                    continue
                if native.get('sessionIdConflict') or native.get('ambiguous'):
                    continue
                if workspace.get('sessionId') != session_id or not isinstance(workspace.get('id'), str):
                    continue
                manifest = result.get('workspaceManifest')
                effective_cwd = manifest.get('path') if isinstance(manifest, dict) else row['cwd']
                if workspace.get('path') != effective_cwd:
                    continue
                base.update(workspaceId=workspace['id'], cwd=effective_cwd,
                            groupCreated=workspace.get('created') is True)
            else:
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
                base['resumeMode'] = mode
                base['storageOwner'] = native['storageOwner']
                base['nativeAppVisibility'] = native.get('nativeAppVisibility')
            rows.append(base)
        except (TypeError, ValueError, KeyError):
            continue
    if adapter == 'dsh':
        created_groups = {(row['workspaceId'], row['cwd']) for row in rows if row['groupCreated']}
        for row in rows:
            row['groupCreated'] = (row['workspaceId'], row['cwd']) in created_groups
    if adapter == 'codex':
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
        rows = list(threads.values())
    return rows


def _socket_path() -> Path:
    supplied = os.environ.get('DSH_WORKSPACE_SOCKET')
    if supplied:
        return Path(supplied).expanduser()
    return Path(os.environ.get('DSH_HOME', str(Path.home() / '.dsh'))).expanduser() / 'deepseek-delegate/workspace.sock'


def _native(method: str, payload: dict) -> dict:
    path = _socket_path()
    try:
        parent, endpoint = path.parent.lstat(), path.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or not stat.S_ISSOCK(endpoint.st_mode)
            or stat.S_ISLNK(parent.st_mode) or stat.S_ISLNK(endpoint.st_mode)
            or (os.name != 'nt' and (parent.st_uid != os.geteuid() or endpoint.st_uid != os.geteuid()
                                   or parent.st_mode & 0o077 or endpoint.st_mode & 0o077))):
            raise OSError('insecure workspace bridge')
        request_id = str(uuid.uuid4())
        wire = json.dumps({'version': 1, 'id': request_id, 'method': method, **payload}).encode() + b'\n'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(15)
            connection.connect(str(path))
            connection.sendall(wire)
            chunks = bytearray()
            while b'\n' not in chunks and len(chunks) <= 16384:
                part = connection.recv(4096)
                if not part:
                    break
                chunks.extend(part)
        reply = json.loads(bytes(chunks).split(b'\n', 1)[0])
        if reply.get('id') != request_id or reply.get('version') != 1:
            raise ValueError('mismatched bridge reply')
        if reply.get('ok') is not True:
            raise BoardError('NATIVE_SESSION_UNAVAILABLE', 'DSH rejected the exact session request', reason=reply.get('error'))
        return reply['value']
    except BoardError:
        raise
    except (OSError, ValueError, KeyError, TimeoutError) as exc:
        raise BoardError('NATIVE_SESSION_UNAVAILABLE', 'The owning DSH workspace bridge is unavailable') from exc


def _journal_path(store, digest: str) -> Path:
    if len(digest) != 64 or any(ch not in '0123456789abcdef' for ch in digest):
        raise BoardError('INVALID_ARGUMENT', 'planDigest must be a SHA-256 hex string')
    root = store.directory / 'worker-session-cleanup'
    private_dirs.ensure_private_dir(root)
    return root / f'{digest}.json'


def _save_journal(path: Path, value: dict) -> None:
    if private_dirs.linked_component(path):
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Worker session journal path is linked')
    descriptor, name = tempfile.mkstemp(prefix='.session-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        if private_dirs.linked_component(path):
            raise BoardError('PRIVATE_PATH_UNSAFE', 'Worker session journal path changed')
        os.replace(name, path)
        from .backup import sync_dir
        sync_dir(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def _apply(store, params: dict, selected: list[dict], digest: str, selection_digest: str) -> dict:
    path = _journal_path(store, params.get('planDigest', ''))
    with _apply_lock:
        if private_dirs.linked_component(path):
            raise BoardError('PRIVATE_PATH_UNSAFE', 'Worker session journal path is linked')
        if path.exists():
            descriptor = private_dirs.open_regular_fd(path, os.O_RDONLY)
            with os.fdopen(descriptor, 'r', encoding='utf-8') as stream:
                journal = json.load(stream)
            if journal.get('selectionDigest') != params.get('selectionDigest'):
                raise BoardError('CONFLICT', 'The recorded plan belongs to a different selection')
        else:
            if params.get('planDigest') != digest or params.get('selectionDigest') != selection_digest:
                raise BoardError('PLAN_CHANGED', 'The exact session selection changed; request a fresh plan')
            journal = {'selectionDigest': selection_digest, 'selected': selected,
                       'pending': [], 'completed': {}, 'failed': {}}
            _save_journal(path, journal)
        current = {row['sessionId']: row for row in _inventory(store, 'dsh')
                   if row['shutdownConfirmed'] and row['executionState'] == 'finished'}
        for row in journal['selected']:
            session_id = row['sessionId']
            if session_id in journal['completed']:
                continue
            # The board record must still bind this exact session and group.
            candidate = current.get(session_id)
            if candidate is None or any(candidate.get(key) != row.get(key) for key in candidate):
                journal['failed'][session_id] = 'board-binding-changed'
                _save_journal(path, journal)
                continue
            try:
                try:
                    observed = _native('inspect-session', {k: row[k] for k in ('sessionId', 'workspaceId', 'cwd')})
                except BoardError as error:
                    if error.details.get('reason') != 'session-mismatch' or session_id not in journal['pending']:
                        raise
                    observed = None  # A lost reply may follow completed native detach.
                removed_siblings = {s for s in journal['completed'] if any(
                    selected_row['sessionId'] == s and selected_row['workspaceId'] == row['workspaceId']
                    for selected_row in journal['selected'])}
                expected_membership = [s for s in row['membership'] if s not in removed_siblings]
                if observed is not None and (observed['revision'] != row['revision']
                                              or observed['sessionIds'] != expected_membership):
                    raise BoardError('PLAN_CHANGED', 'Native session changed before archive')
                if session_id not in journal['pending']:
                    journal['pending'].append(session_id)
                    _save_journal(path, journal)
                result = _native('archive-session', {k: row[k] for k in ('sessionId', 'workspaceId', 'cwd', 'revision')}
                                 | {'created': row['groupCreated'], 'replay': observed is None})
                journal['completed'][session_id] = result
                journal['failed'].pop(session_id, None)
            except BoardError as error:
                journal['failed'][session_id] = error.details.get('reason') or error.code
            _save_journal(path, journal)
        retained = {}
        for row in journal['selected']:
            result = journal['completed'].get(row['sessionId'])
            if result:
                retained[row['workspaceId']] = result.get('groupRetainedReason') or 'conditional-delete-unavailable'
        return {'adapter': 'dsh',
                'sessionsArchived': sum(value.get('sessionArchived') is True for value in journal['completed'].values()),
                'groupsRemoved': sum(value.get('groupRemoved') is True for value in journal['completed'].values()),
                'groupsRetained': len(retained),
                'groupRetention': [{'workspaceId': key, 'reason': value} for key, value in retained.items()],
                'failed': [{'sessionId': key, 'reason': value} for key, value in journal['failed'].items()],
                'nativeLogsRetained': True}


def handle(store, params: dict) -> dict:
    reject_unknown(params, {'action', 'adapter', 'planDigest', 'selectionDigest'}, 'worker.sessions')
    action = optional_string(params, 'action', max_length=16) or 'list'
    adapter = optional_string(params, 'adapter', max_length=16) or 'dsh'
    if action not in {'list', 'plan', 'apply'} or adapter not in {'dsh', 'codex'}:
        raise BoardError('INVALID_ARGUMENT', 'action must be list, plan or apply; adapter must be dsh or codex')
    if adapter == 'codex' and action != 'list':
        raise BoardError('UNSUPPORTED', 'Codex historical threads are read-only here; archive or delete requires a separate confirmed native action')
    if action == 'apply':
        for name in ('planDigest', 'selectionDigest'):
            value = optional_string(params, name, max_length=64)
            if not isinstance(value, str) or len(value) != 64 or any(ch not in '0123456789abcdef' for ch in value):
                raise BoardError('INVALID_ARGUMENT', f'{name} must be a SHA-256 hex string')
    rows = _inventory(store, adapter)
    if action == 'list':
        return {'adapter': adapter, 'sessions': rows, 'count': len(rows),
                'nativeAccess': False, 'codexAction': 'confirm native archive/delete separately' if adapter == 'codex' else None}
    # A board row is only a candidate. A native plan proves exact membership and
    # header identity, and apply repeats that proof before each mutation.
    selected, blocked = [], []
    by_session = {}
    for row in rows:
        by_session.setdefault(row['sessionId'], []).append(row)
    for session_id, group in sorted(by_session.items()):
        if len(group) != 1 or group[0]['executionState'] != 'finished' or not group[0]['shutdownConfirmed']:
            blocked.append({'sessionId': session_id, 'reason': 'duplicate-or-stop-unconfirmed'})
            continue
        row = group[0]
        try:
            observed = _native('inspect-session', {k: row[k] for k in ('sessionId', 'workspaceId', 'cwd')})
            selected.append({**row, 'revision': observed['revision'],
                             'membership': observed['sessionIds']})
        except BoardError as error:
            blocked.append({'sessionId': session_id, 'reason': error.details.get('reason') or error.code})
    digest = _digest(selected)
    selection_digest = _digest([row['sessionId'] for row in selected])
    if action == 'plan':
        return {'adapter': 'dsh', 'selected': selected, 'blocked': blocked,
                'sessionsSelected': len(selected), 'planDigest': digest, 'selectionDigest': selection_digest,
                'nativeLogsRetained': True}
    return _apply(store, params, selected, digest, selection_digest)
