"""Journaled relocation of stopped attempts' historical private files."""
from __future__ import annotations

from contextlib import closing
import hashlib
import os
from pathlib import Path
import re
import sqlite3

from .errors import BoardError
from . import attempt_evidence, private_dirs

_NO_TOOL = re.compile(r'no-tool-[0-9a-f]{32}\Z')
_RUN_DIR = attempt_evidence.OLD_DSH_RUN
_CREDENTIALS = frozenset({'agent-credential.json', 'inquiry.json', 'finish-bridge.json',
                          'zcode-control.json', 'builtin-provider.json', 'personal-provider.json'})
_SEGMENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')


def _state(state: Path) -> Path:
    if private_dirs.linked(state):
        raise BoardError('UPGRADE_UNSAFE', 'State root cannot be linked')
    return Path(state).resolve()


def _entry_paths(path: Path) -> set[str]:
    """Enumerate names, including links, without following them."""
    result = {str(path)}
    if private_dirs.linked(path) or not path.is_dir():
        return result
    with os.scandir(path) as entries:
        children = [Path(entry.path) for entry in entries]
    for child in children:
        result.update(_entry_paths(child))
    return result


def _guard(state: Path, path: Path, *, leaf: bool = True) -> None:
    """Check each state-relative component without resolving through a link."""
    try:
        parts = path.relative_to(state).parts
    except ValueError:
        raise BoardError('UPGRADE_UNSAFE', 'Private migration path is outside the state root') from None
    current = state
    if private_dirs.linked(current):
        raise BoardError('UPGRADE_UNSAFE', 'State root is linked')
    for index, part in enumerate(parts):
        current /= part
        if (leaf or index < len(parts) - 1) and private_dirs.linked(current):
            raise BoardError('UPGRADE_UNSAFE', 'Private migration path crosses a link', path=str(current))


def _safe_segment(value: str) -> bool:
    return isinstance(value, str) and _SEGMENT.fullmatch(value) is not None and value not in ('.', '..')


def _validate_action(state: Path, item: dict, *, deletion: bool) -> tuple[Path, Path | None]:
    """Rebuild both locations from identities; a journal cannot name arbitrary files."""
    task, adapter, attempt = item.get('taskId'), item.get('adapter'), item.get('attemptId')
    if not _safe_segment(task) or adapter not in private_dirs.ADAPTERS:
        raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Private journal identity is invalid')
    if deletion:
        if not _safe_segment(attempt):
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Private journal attempt identity is invalid')
        if not isinstance(item.get('path'), str):
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Private journal deletion path is invalid')
        source = Path(item['path'])
    else:
        if not isinstance(item.get('source'), str) or not isinstance(item.get('target'), str):
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Private journal move paths are invalid')
        source, target = Path(item['source']), Path(item['target'])
    if attempt is None:
        if deletion or adapter not in ('codex', 'zcode'):
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Private journal goal action is invalid')
        expected = state / 'harnesses' / adapter / hashlib.sha256(task.encode()).hexdigest()
        destination = private_dirs.native_root(state, adapter, task)
        if source != expected or target != destination:
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Private journal goal paths changed')
    else:
        if not _safe_segment(attempt):
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Private journal attempt identity is invalid')
        old = state / 'attempts' / task / attempt
        try:
            relative = source.relative_to(old)
        except ValueError:
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Private journal attempt path changed') from None
        parts = relative.parts
        no_tool = len(parts) >= 2 and _NO_TOOL.fullmatch(parts[0])
        old_dsh = len(parts) == 2 and _RUN_DIR.fullmatch(parts[0])
        call = no_tool and len(parts) == 3 and re.fullmatch(r'call-[12]', parts[1])
        if deletion:
            valid = (len(parts) == 1 and parts[0] in _CREDENTIALS or
                     no_tool and len(parts) == 2 and parts[1] in ('builtin-provider.json', 'personal-provider.json') or
                     call and parts[2] == 'patch.json')
            if len(parts) == 1 and parts[0] in ('finish-bridge.json', 'zcode-control.json',
                                                 'builtin-provider.json', 'personal-provider.json'):
                valid = valid and adapter == 'zcode'
            if no_tool and len(parts) == 2:
                valid = valid and adapter == 'zcode'
            if call:
                valid = valid and adapter == 'dsh'
        else:
            valid = (len(parts) == 1 and parts[0] in ('native', 'claude-private', 'sessions', 'frozen-input') or
                     no_tool and len(parts) == 2 and parts[1] in ('native', 'dsh-home') or
                     call and parts[2] in ('sessions', 'preflight', 'native') or
                     old_dsh and _safe_segment(parts[1]) and
                     not attempt_evidence.is_evidence(relative.as_posix()))
            if len(parts) == 1 and parts[0] in ('claude-private', 'sessions', 'frozen-input'):
                valid = valid and adapter == {'claude-private':'claude', 'sessions':'dsh',
                                               'frozen-input':'codex'}[parts[0]]
            if no_tool and len(parts) == 2 and parts[1] == 'dsh-home':
                valid = valid and adapter == 'dsh'
            if call or old_dsh:
                valid = valid and adapter == 'dsh'
            destination = private_dirs.attempt_root(state, adapter, task, attempt) / relative
            if target != destination:
                valid = False
        if not valid:
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Private journal layout action is invalid')
    _guard(state, source, leaf=False if deletion else True)
    if not deletion:
        _guard(state, target, leaf=False)
    return source, None if deletion else target


def validate_journal(state: Path, record: dict) -> None:
    if not isinstance(record, dict) or not isinstance(record.get('moves'), list) or not isinstance(record.get('deletes'), list):
        raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Private relocation journal is malformed')
    sources, targets = set(), set()
    for item in record['moves']:
        if not isinstance(item, dict):
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Private relocation move is malformed')
        source, target = _validate_action(state, item, deletion=False)
        if source in sources or target in targets:
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Private relocation journal duplicates a path')
        sources.add(source); targets.add(target)
    for item in record['deletes']:
        if not isinstance(item, dict):
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Private relocation deletion is malformed')
        source, _ = _validate_action(state, item, deletion=True)
        if source in sources:
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Private relocation journal duplicates a path')
        sources.add(source)


def _no_tool_adapter(state: Path, path: Path, declared: str) -> str | None:
    _guard(state, path)
    for name in ('dsh-home', 'native', 'native/codex-home', 'native/storage', 'native/sessions.sqlite',
                 'builtin-provider.json', 'personal-provider.json'):
        _guard(state, path / name)
    if (path / 'dsh-home').exists():
        return 'dsh'
    if (path / 'native/codex-home').exists():
        return 'codex'
    if (path / 'native/storage').exists() or (path / 'native/sessions.sqlite').exists():
        return 'zcode'
    if any((path / name).exists() for name in ('builtin-provider.json', 'personal-provider.json')):
        return 'zcode'
    if (path / 'native').exists():
        return declared if declared in ('codex', 'zcode', 'claude') else None
    for call in path.iterdir():
        if re.fullmatch(r'call-[12]', call.name) and not private_dirs.linked(call) and call.is_dir():
            if any((call / name).exists() for name in ('patch.json', 'sessions', 'preflight', 'native')):
                return 'dsh'
    return declared if declared in ('codex', 'zcode', 'dsh', 'claude') else None


def _no_tool_private_entries(source: Path, old: Path) -> bool:
    for child in source.iterdir():
        relative = child.relative_to(old).as_posix()
        if attempt_evidence.is_evidence(relative):
            continue
        if re.fullmatch(r'call-[12]', child.name) and not private_dirs.linked(child) and child.is_dir():
            if all(attempt_evidence.is_evidence(nested.relative_to(old).as_posix())
                   for nested in child.iterdir()):
                continue
        return True
    return False


def preview(state: Path) -> dict:
    """Read-only exact relocation plan; unknown ownership never authorizes a move."""
    state = _state(state)
    moves, deletes, blocked, covered = [], [], [], set()
    database = state / 'board.sqlite3'
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        attempts = connection.execute(
            'SELECT a.task_id,a.attempt_id,a.execution_state,a.shutdown_confirmed,t.adapter '
            'FROM attempts a JOIN tasks t ON t.task_id=a.task_id'
        ).fetchall()
        tasks = {row['task_id']: row['adapter'] for row in connection.execute('SELECT task_id,adapter FROM tasks')}

    def add_move(source: Path, target: Path, *, task_id: str, adapter: str, attempt_id: str | None = None):
        _guard(state, source, leaf=False)
        _guard(state, target, leaf=False)
        if private_dirs.linked(source):
            blocked.append({'path': str(source.relative_to(state)), 'reason': 'linked-legacy-root'})
            return
        if target.exists() or private_dirs.linked(target):
            blocked.append({'path': str(source.relative_to(state)), 'reason': 'private-destination-exists'})
            return
        covered.update(str(Path(p).relative_to(state)) for p in _entry_paths(source))
        moves.append({'source': str(source), 'target': str(target), 'taskId': task_id,
                      'adapter': adapter, 'attemptId': attempt_id})

    def add_delete(source: Path, *, task_id: str, adapter: str, attempt_id: str):
        _guard(state, source, leaf=False)
        if source.is_dir() and not private_dirs.linked(source):
            blocked.append({'path': str(source.relative_to(state)), 'reason': 'credential-is-directory'})
            return
        covered.add(str(source.relative_to(state)))
        deletes.append({'path': str(source), 'taskId': task_id, 'adapter': adapter, 'attemptId': attempt_id})

    old_attempts = state / 'attempts'
    _guard(state, old_attempts)
    for row in attempts:
        task_id, attempt_id, declared = row['task_id'], row['attempt_id'], row['adapter']
        old = old_attempts / task_id / attempt_id
        _guard(state, old)
        if not old.exists() and not private_dirs.linked(old):
            continue
        legacy = [child for child in old.iterdir() if (child.name in _CREDENTIALS
                   or child.name in ('native', 'claude-private', 'sessions', 'frozen-input')
                   or _NO_TOOL.fullmatch(child.name) or _RUN_DIR.fullmatch(child.name))]
        if not legacy:
            continue
        if row['execution_state'] != 'finished' or row['shutdown_confirmed'] != 1:
            blocked.extend({'path': str(child.relative_to(state)), 'reason': 'shutdown-unconfirmed'} for child in legacy)
            continue
        for source in legacy:
            if source.name not in _CREDENTIALS:
                _guard(state, source)
            if _NO_TOOL.fullmatch(source.name):
                if not _no_tool_private_entries(source, old):
                    continue
                adapter = _no_tool_adapter(state, source, declared)
                if adapter not in private_dirs.ADAPTERS or private_dirs.linked(source):
                    blocked.append({'path': str(source.relative_to(state)), 'reason': 'adapter-unproven'})
                    continue
                for child in source.iterdir():
                    relative = child.relative_to(old)
                    if attempt_evidence.is_evidence(relative.as_posix()):
                        continue
                    if child.name in ('builtin-provider.json', 'personal-provider.json'):
                        add_delete(child, task_id=task_id, adapter=adapter, attempt_id=attempt_id)
                    elif child.name in ('native', 'dsh-home'):
                        add_move(child, private_dirs.attempt_root(state, adapter, task_id, attempt_id) / relative,
                                 task_id=task_id, adapter=adapter, attempt_id=attempt_id)
                    elif re.fullmatch(r'call-[12]', child.name) and child.is_dir() and not private_dirs.linked(child):
                        for nested in child.iterdir():
                            nested_relative = nested.relative_to(old)
                            if attempt_evidence.is_evidence(nested_relative.as_posix()):
                                continue
                            if nested.name == 'patch.json':
                                add_delete(nested, task_id=task_id, adapter=adapter, attempt_id=attempt_id)
                            elif nested.name in ('sessions', 'preflight', 'native'):
                                add_move(nested, private_dirs.attempt_root(state, adapter, task_id, attempt_id)
                                         / nested_relative, task_id=task_id, adapter=adapter, attempt_id=attempt_id)
                    else:
                        blocked.append({'path': str(child.relative_to(state)), 'reason': 'unrecognized-no-tool-private'})
                continue
            if _RUN_DIR.fullmatch(source.name):
                if private_dirs.linked(source) or not source.is_dir():
                    blocked.append({'path': str(source.relative_to(state)), 'reason': 'linked-legacy-root'})
                    continue
                for child in source.iterdir():
                    relative = child.relative_to(old)
                    if attempt_evidence.is_evidence(relative.as_posix()):
                        continue
                    if not _safe_segment(child.name) or private_dirs.linked(child):
                        blocked.append({'path': str(child.relative_to(state)), 'reason': 'unrecognized-dsh-private'})
                        continue
                    add_move(child, private_dirs.attempt_root(state, 'dsh', task_id, attempt_id) / relative,
                             task_id=task_id, adapter='dsh', attempt_id=attempt_id)
                continue
            if source.name in _CREDENTIALS:
                adapter = 'zcode' if source.name in ('zcode-control.json', 'finish-bridge.json',
                                                    'builtin-provider.json', 'personal-provider.json') else declared
                if adapter not in private_dirs.ADAPTERS:
                    blocked.append({'path': str(source.relative_to(state)), 'reason': 'adapter-unproven'})
                else:
                    add_delete(source, task_id=task_id, adapter=adapter, attempt_id=attempt_id)
                continue
            adapter = ('claude' if source.name == 'claude-private' else 'dsh' if source.name == 'sessions'
                       else 'codex' if source.name == 'frozen-input'
                       else 'codex' if (not private_dirs.linked(source / 'codex-home')
                                        and (source / 'codex-home').exists())
                       else 'zcode' if ((not private_dirs.linked(source / 'sessions.sqlite') and (source / 'sessions.sqlite').exists())
                                        or (not private_dirs.linked(source / 'storage') and (source / 'storage').exists()))
                       else declared)
            if adapter not in private_dirs.ADAPTERS:
                blocked.append({'path': str(source.relative_to(state)), 'reason': 'adapter-unproven'})
                continue
            destination = private_dirs.attempt_root(state, adapter, task_id, attempt_id) / source.name
            add_move(source, destination, task_id=task_id, adapter=adapter, attempt_id=attempt_id)

    # Original Codex and ZCode goal homes predate the goals/<hash>/native layout.
    for adapter in ('codex', 'zcode'):
        old_parent = state / 'harnesses' / adapter
        _guard(state, old_parent)
        if not old_parent.exists():
            continue
        if not old_parent.is_dir():
            blocked.append({'path': str(old_parent.relative_to(state)), 'reason': 'legacy-harness-not-directory'})
            continue
        if private_dirs.linked(old_parent):
            blocked.append({'path': str(old_parent.relative_to(state)), 'reason': 'linked-legacy-root'})
            continue
        hash_to_task = {hashlib.sha256(task.encode()).hexdigest(): task for task in tasks}
        for source in old_parent.iterdir():
            if source.name in ('goals', 'accounts'):
                continue
            if private_dirs.linked(source) or not source.is_dir():
                blocked.append({'path': str(source.relative_to(state)), 'reason': 'legacy-goal-not-directory'})
                continue
            task_id = hash_to_task.get(source.name)
            if task_id is None:
                blocked.append({'path': str(source.relative_to(state)), 'reason': 'owner-unproven'})
                continue
            related = [row for row in attempts if row['task_id'] == task_id]
            if not related or any(row['execution_state'] != 'finished' or row['shutdown_confirmed'] != 1 for row in related):
                blocked.append({'path': str(source.relative_to(state)), 'reason': 'shutdown-unconfirmed'})
                continue
            add_move(source, private_dirs.native_root(state, adapter, task_id), task_id=task_id, adapter=adapter)

    return {'moves': moves, 'deletes': deletes, 'blocked': blocked,
            'coveredPaths': sorted(covered), 'count': len(moves) + len(deletes),
            'paths': [str(Path(item.get('source', item.get('path'))).relative_to(state))
                      for item in (moves + deletes)[:20]]}


def require_readiness(state: Path, preflight_report: dict) -> dict:
    """Permit only skipped entries covered by a stopped, exact migration plan."""
    plan = preview(state)
    from . import backup
    rejected = preflight_report.get('rejected') or {}
    skipped = preflight_report.get('skipped') or {}
    allowed = set(plan['coveredPaths'])
    all_entries = [(kind, path.relative_to(Path(state)).as_posix(), reason)
                   for kind, path, reason in backup.preflight_entries(state)]
    unexpected = [path for kind, path, _reason in all_entries if kind == 'skipped' and path not in allowed]
    rejected_paths = [path for kind, path, _reason in all_entries if kind == 'rejected']
    if (plan['blocked'] or rejected.get('count', 0) or rejected_paths or skipped.get('count', 0)
            != sum(kind == 'skipped' for kind, _path, _reason in all_entries) or unexpected):
        paths = ([row['path'] for row in plan['blocked']] +
                 rejected_paths + unexpected)[:20]
        raise BoardError('BACKUP_PREFLIGHT_FAILED', 'Upgrade has unknown, unsafe or unconfirmed private files', paths=paths)
    return {'ready': True, 'legacy': {'count': plan['count'], 'paths': plan['paths']}, 'plan': plan}


def apply(state: Path, journal: dict, save) -> dict:
    """Complete moves recorded in the upgrade journal before runtime switch."""
    state = _state(state)
    record = journal['privateMigration']
    validate_journal(state, record)
    if record.get('applied'):
        return {key: record[key] for key in ('count', 'movedCount', 'deletedCount',
                                             'credentialCleanupCount', 'paths')}
    from .backup import sync_dir
    for move in record['moves']:
        source, target = Path(move['source']), Path(move['target'])
        if source.exists() or private_dirs.linked(source):
            if target.exists() or private_dirs.linked(target) or private_dirs.linked(source):
                raise BoardError('UPGRADE_UNSAFE', 'Private relocation target changed', path=str(source))
            private_dirs.ensure_private_dir(target.parent)
            os.rename(source, target)
            sync_dir(source.parent)
            sync_dir(target.parent)
        elif not target.exists() or private_dirs.linked(target):
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Private relocation has neither source nor destination', path=str(source))
    if 'cleanupPaths' not in record:
        credential_paths = set()
        for item in record['deletes']:
            if Path(item['path']).name == 'inquiry.json':
                fallback = private_dirs.inquiry_fallback(Path(item['path']), item['attemptId'])
                if fallback is not None and fallback.exists():
                    credential_paths.add(str(fallback))
        roots = {(item['adapter'], item['taskId'], item['attemptId']) for item in record['moves']
                 if item['attemptId'] is not None}
        for adapter, task_id, attempt_id in roots:
            root = private_dirs.attempt_root(state, adapter, task_id, attempt_id)
            if not root.exists():
                continue
            def scan(directory):
                _guard(state, directory)
                with os.scandir(directory) as entries:
                    children = [Path(entry.path) for entry in entries]
                for child in children:
                    if child.name in private_dirs._CREDENTIAL_FILES:
                        credential_paths.add(str(child))
                    elif child.is_dir() and not private_dirs.linked(child):
                        scan(child)
            scan(root)
        record['cleanupPaths'] = sorted(credential_paths)
        save(journal)
    for item in record['deletes']:
        path = Path(item['path'])
        if path.exists() or private_dirs.linked(path):
            if path.is_dir() and not private_dirs.linked(path):
                raise BoardError('UPGRADE_UNSAFE', 'Credential became a directory', path=str(path))
            if path.name == 'inquiry.json':
                private_dirs.cleanup_inquiry_fallback(path, item['attemptId'])
            if path.is_junction():
                path.rmdir()
            else:
                path.unlink()
            sync_dir(path.parent)
    for item in record['moves']:
        if item['attemptId']:
            private_dirs.cleanup_attempt_credentials(state, item['adapter'], item['taskId'], item['attemptId'])
    record['movedCount'] = len(record['moves'])
    record['deletedCount'] = len(record['deletes'])
    record['credentialCleanupCount'] = len(record['cleanupPaths'])
    record['count'] = record['movedCount'] + record['deletedCount'] + record['credentialCleanupCount']
    def shown(path):
        path = Path(path)
        return str(path.relative_to(state)) if path.is_relative_to(state) else str(path)
    record['paths'] = ([shown(item['source']) for item in record['moves']] +
                       [shown(item['path']) for item in record['deletes']] +
                       [shown(path) for path in record['cleanupPaths']])[:20]
    record['applied'] = True
    save(journal)
    return {key: record[key] for key in ('count', 'movedCount', 'deletedCount',
                                         'credentialCleanupCount', 'paths')}


def rollback(state: Path, journal: dict, save) -> None:
    """Reverse session moves for the old runtime; deleted stopped credentials stay deleted."""
    state = _state(state)
    record = journal.get('privateMigration')
    if not record:
        return
    validate_journal(state, record)
    from .backup import sync_dir
    for move in reversed(record['moves']):
        source, target = Path(move['source']), Path(move['target'])
        if target.exists() or private_dirs.linked(target):
            if source.exists() or private_dirs.linked(source) or private_dirs.linked(target):
                raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Private rollback target changed', path=str(source))
            private_dirs.ensure_private_dir(source.parent)
            os.rename(target, source)
            sync_dir(target.parent)
            sync_dir(source.parent)
        elif not source.exists() and not private_dirs.linked(source):
            raise BoardError('UPGRADE_RECOVERY_REQUIRED', 'Private rollback has neither source nor destination', path=str(source))
    record['applied'] = False
    save(journal)
