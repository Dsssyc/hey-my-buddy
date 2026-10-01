"""Service-owned, verified, single-generation backup (no schema changes)."""
from __future__ import annotations

from contextlib import closing
import ctypes
from . import locking
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sqlite3
import subprocess
import sys
import tempfile
import time

from .db import PREVIOUS_SCHEMA_VERSION, SCHEMA_VERSION, Database, utc_now
from .contracts import CONTRACT_VERSION
from .errors import BoardError
from . import attempt_evidence, private_dirs
from .private_dirs import linked as _linked


def digest(path: Path) -> str:
    with _open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sync_dir(path: Path) -> None:
    """Flush POSIX directory metadata.

    Python has no portable Windows directory flush. On Windows the backup
    journal and payload files are flushed, but a power-loss guarantee for
    directory renames is not claimed; recovery covers process interruption.
    """
    _guard(path)
    if _windows():
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _windows() -> bool:
    return sys.platform == 'win32'


def _rename(source: Path, target: Path) -> None:
    _guard(source)
    _guard(target)
    os.replace(source, target)


def exchange(left: Path, right: Path) -> None:
    """Atomic directory exchange: after a crash current is always one whole copy."""
    _guard(left)
    _guard(right)
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        fn = libc.renamex_np
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = fn(os.fsencode(left), os.fsencode(right), 2)  # RENAME_SWAP
    elif sys.platform.startswith('linux'):
        fn = libc.renameat2
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = fn(-100, os.fsencode(left), -100, os.fsencode(right), 2)  # RENAME_EXCHANGE
    else:
        raise BoardError('BACKUP_UNSUPPORTED', 'Atomic backup exchange is unavailable on this platform')
    if result:
        raise OSError(ctypes.get_errno(), 'Atomic backup exchange failed')


#: Bound for any state-relative path carried in an error or the manifest.
_SHOWN_PATH_LIMIT = 200


def _shown_path(relative: str) -> str:
    text = relative if len(relative) <= _SHOWN_PATH_LIMIT else relative[:_SHOWN_PATH_LIMIT - 3] + '...'
    return text


def _linked_component(path: Path) -> Path | None:
    return private_dirs.linked_component(path)


def _guard(path: Path) -> None:
    component = _linked_component(path)
    if component is not None:
        raise BoardError('BACKUP_UNSAFE_PATH', 'Backup path contains a linked component', path=str(component))


def _open_fd(path: Path, flags: int) -> int:
    try:
        return private_dirs.open_regular_fd(path, flags)
    except BoardError as error:
        raise BoardError('BACKUP_UNSAFE_PATH', error.message, **error.details) from error


def _open(path: Path, mode: str):
    flags = os.O_RDONLY if mode == 'rb' else os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = _open_fd(path, flags)
    return os.fdopen(fd, mode)


def _regular_files(root: Path, *, scope: str = ''):
    """Walk verified backup payloads and non-attempt state; never follow links."""
    _guard(root)
    try:
        root_info = root.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(root_info.st_mode):
        raise BoardError('BACKUP_UNSAFE_PATH', 'Backup source must be a directory', path=scope or str(root))

    def report(path: Path) -> str:
        relative = str(path.relative_to(root))
        return _shown_path(scope + '/' + relative if scope else relative)

    def visit(parent):
        _guard(parent)
        with os.scandir(parent) as entries:
            children = sorted(Path(entry.path) for entry in entries)
        for path in children:
            if _linked_component(path):
                shown = report(path)
                raise BoardError('BACKUP_UNSAFE_PATH',
                                 'Backup source contains a linked path: ' + shown, path=shown)
            metadata = path.lstat()
            if stat.S_ISDIR(metadata.st_mode):
                yield from visit(path)
            elif stat.S_ISREG(metadata.st_mode):
                yield path
            else:
                shown = report(path)
                raise BoardError('BACKUP_UNSAFE_PATH',
                                 'Backup source contains a nonregular file: ' + shown, path=shown)
    yield from visit(root)


STATE_FILES = ('console-sessions.json', 'console-settings.json', 'worker-pool.json',
               'runtime-retention.json', 'active-runtime.json', 'launch-settings.json')


def _scan(path: Path, *, attempt_depth: int | None = None):
    """Inventory entries only, pruning every undeclared directory and every link.

    ``attempt_depth`` is the number of structural containers still to enter
    before paths become relative to one attempt. Skipped directory trees count
    as one entry, including empty directories; their contents are never read.
    """
    def visit(item, depth, relative=Path()):
        component = _linked_component(item)
        if component is not None and component != private_dirs._absolute(item):
            yield 'rejected', item, 'linked-path'
            return
        try:
            info = item.lstat()
        except FileNotFoundError:
            return
        except OSError:
            yield 'rejected', item, 'unreadable-path'
            return
        link = component is not None or _linked(item)
        is_dir = stat.S_ISDIR(info.st_mode) and not link
        structural = depth is not None and (depth > 0 or not relative.parts)
        expected_file = depth is None or depth == 0 and attempt_evidence.is_evidence(relative)
        expected_dir = depth is None or structural or attempt_evidence.is_evidence_directory(relative)
        declared = expected_file or expected_dir
        if not declared:
            yield 'skipped', item, 'not-evidence-directory' if is_dir else 'not-evidence-file'
        elif link:
            yield 'rejected', item, 'linked-evidence' if depth == 0 else 'linked-path'
        elif is_dir and not expected_dir or not is_dir and not expected_file:
            yield 'rejected', item, 'wrong-evidence-type' if depth == 0 else 'non-directory-path'
        elif is_dir:
            try:
                children = sorted(item.iterdir())
            except OSError:
                yield 'rejected', item, 'unreadable-directory'
                return
            for child in children:
                new_depth = None if depth is None else max(0, depth - 1)
                new_relative = relative / child.name if depth == 0 else Path()
                # Entering the attempt establishes a new relative root.
                yield from visit(child, new_depth, new_relative)
        elif stat.S_ISREG(info.st_mode) and (depth is None or depth == 0):
            yield 'copied', item, 'declared-evidence' if depth == 0 else 'durable-state'
        else:
            yield 'rejected', item, 'nonregular-evidence' if depth == 0 else 'nonregular-path'
    yield from visit(path, attempt_depth)


def _single(path: Path):
    if _linked_component(path):
        yield 'rejected', path, 'linked-path'
        return
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(metadata.st_mode):
        yield 'rejected', path, 'nonregular-path'
    else:
        yield from _scan(path)


def preflight_entries(state: Path):
    """Complete read-only stream used by backup, diagnostics and upgrade admission."""
    state = Path(state)
    if _linked_component(state):
        yield 'rejected', state, 'linked-state-root' if _linked(state) else 'linked-state-ancestor'
        return
    if not state.exists():
        return
    if not state.is_dir():
        yield 'rejected', state, 'non-directory-state-root'
        return
    # Online backup reads SQLite itself; the database path must still be regular.
    yield from _single(state / 'board.sqlite3')
    # Root -> goal -> attempt -> declared relative evidence.
    yield from _scan(state / 'attempts', attempt_depth=2)
    for name in ('controls', 'submissions'):
        yield from _scan(state / name)
    # Retired DSH archive receipts remain ordinary historical evidence. A linked
    # journal root or an undeclared name must never enter a verified backup.
    session_journals = state / 'worker-session-cleanup'
    if _linked(session_journals) or session_journals.exists() and not session_journals.is_dir():
        yield 'rejected', session_journals, 'linked-path' if _linked(session_journals) else 'non-directory-path'
    elif session_journals.exists():
        for journal in sorted(session_journals.iterdir()):
            if re.fullmatch(r'[0-9a-f]{64}\.json', journal.name):
                yield from _single(journal)
            else:
                yield 'rejected', journal, 'undeclared-journal-entry'
    workers = state / 'workers'
    if _linked(workers) or workers.exists() and not workers.is_dir():
        yield 'rejected', workers, 'linked-path' if _linked(workers) else 'non-directory-path'
    elif workers.exists():
        for worker in sorted(workers.iterdir()):
            if _linked(worker) or not worker.is_dir():
                yield 'rejected', worker, 'linked-path' if _linked(worker) else 'non-directory-path'
                continue
            receipts = worker / 'receipts'
            if _linked(receipts) or receipts.exists() and not receipts.is_dir():
                yield 'rejected', receipts, 'linked-path' if _linked(receipts) else 'non-directory-path'
            elif receipts.exists():
                for receipt in sorted(receipts.iterdir()):
                    if receipt.name.endswith('.json'):
                        yield from _single(receipt)
            for name in ('startup.json', 'orphaned.json'):
                yield from _single(worker / name)
    for name in STATE_FILES:
        yield from _single(state / name)


def preflight(state: Path) -> dict:
    """Report copying/skipping/refusal without locks, writes, hashing or startup."""
    from . import private_migration
    result = {kind: {'count': 0, 'paths': [], 'entries': []}
              for kind in ('copied', 'skipped', 'rejected')}
    # Full skipped inventory (the samples below are bounded): the relocation
    # cross-check must judge every skipped entry, not the first twenty.
    skipped: list[str] = []
    for kind, path, reason in preflight_entries(state):
        row = result[kind]
        row['count'] += 1
        if kind == 'skipped':
            skipped.append(path.relative_to(state).as_posix())
        if len(row['entries']) < 20:
            relative = _shown_path(path.relative_to(state).as_posix())
            row['paths'].append(relative)
            row['entries'].append({'path': relative, 'reason': reason})
    legacy = private_migration.legacy_readiness(state, skipped)
    return {'policy': attempt_evidence.POLICY,
            'ok': result['rejected']['count'] == 0 and legacy['ready'],
            'needsAttention': result['skipped']['count'] > 0 or result['rejected']['count'] > 0,
            'legacyPlan': legacy, **result}


def preflight_command(params: dict) -> dict:
    """CLI-local read, deliberately independent of service and runtime startup."""
    from . import schemas, home
    schemas.reject_unknown(params, set(), 'backup-preflight')
    if os.environ.get('BUDDY_AGENT_CREDENTIAL') or os.environ.get('BUDDY_AGENT_CREDENTIAL_FILE'):
        raise BoardError('UNAUTHORIZED', 'A Worker cannot inspect Host backup sources')
    state = Path(os.environ.get('BUDDY_STATE_DIR') or home.default_state_dir()).expanduser().absolute()
    return preflight(state)


def _private_copy(source: Path, target: Path) -> None:
    if _linked_component(source) or not stat.S_ISREG(source.lstat().st_mode):
        raise BoardError('BACKUP_UNSAFE_PATH', 'Backup source changed to a linked or special file', path=str(source))
    if _linked_component(target):
        raise BoardError('BACKUP_UNSAFE_PATH', 'Backup destination cannot be linked', path=str(target))
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with _open(source, 'rb') as src, _open(target, 'xb') as dst:
        shutil.copyfileobj(src, dst)
        dst.flush()
        os.fsync(dst.fileno())


def database_snapshot(connection, *, event_head: int | None = None) -> dict:
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    if event_head is None:
        event_head = connection.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
    fingerprints = {}
    for name in tables:
        if name == 'workers':
            continue
        quoted = '"' + name.replace('"','""') + '"'
        sql, args = 'SELECT * FROM ' + quoted, ()
        if name == 'events':
            sql += ' WHERE seq<=?';args = (event_head,)
        elif name == 'sqlite_sequence':
            sql += " WHERE name NOT IN ('events','workers')"
        rows = sorted(json.dumps(list(row), ensure_ascii=False, separators=(',', ':'), default=lambda value:value.hex()) for row in connection.execute(sql,args))
        fingerprints[name] = hashlib.sha256('\n'.join(rows).encode()).hexdigest()
    return {'tables':{name:connection.execute('SELECT COUNT(*) FROM "'+name.replace('"','""')+'"').fetchone()[0] for name in tables},
            'fingerprints':fingerprints, 'eventHead':event_head}


def verify(directory: Path) -> dict:
    """Verify a generation, recovering an interrupted current publication first."""
    directory = Path(directory)
    if directory.name == 'current' and directory.parent.name == 'backups':
        recover(directory.parent.parent)
    return _verify(directory)


def _verify(directory: Path) -> dict:
    _guard(directory)
    with _open(directory / 'manifest.json', 'rb') as stream:
        manifest = json.load(stream)
    if manifest.get('format') != 1 or manifest.get('schema') not in (SCHEMA_VERSION, PREVIOUS_SCHEMA_VERSION):
        raise BoardError('BACKUP_INVALID', 'Unsupported backup format or schema')
    policy = manifest.get('attemptEvidencePolicy')
    if policy is not None and policy != attempt_evidence.POLICY:
        raise BoardError('BACKUP_INVALID', 'Unsupported attempt evidence policy')
    if policy == attempt_evidence.POLICY:
        for relative in manifest['files']:
            parts = Path(relative).parts
            if parts[:2] == ('state', 'attempts') and (len(parts) < 5 or not attempt_evidence.is_evidence(Path(*parts[4:]).as_posix())):
                raise BoardError('BACKUP_INVALID', 'Backup contains undeclared attempt evidence', file=relative)
            if parts[:2] == ('state', 'worker-session-cleanup') and (len(parts) != 3 or re.fullmatch(r'[0-9a-f]{64}\.json', parts[2]) is None):
                raise BoardError('BACKUP_INVALID', 'Backup contains undeclared Worker session journal', file=relative)
    actual = {str(p.relative_to(directory)) for p in _regular_files(directory) if p != directory / 'manifest.json'}
    if actual != set(manifest['files']):
        raise BoardError('BACKUP_INVALID', 'Backup inventory mismatch')
    for relative, row in manifest['files'].items():
        path = directory / relative
        if Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise BoardError('BACKUP_INVALID', 'Unsafe manifest path')
        _guard(path)
        if path.lstat().st_size != row['bytes'] or digest(path) != row['sha256']:
            raise BoardError('BACKUP_INVALID', 'Backup payload hash mismatch', file=relative)
    _guard(directory.parent)
    with tempfile.TemporaryDirectory(prefix='.verify-', dir=directory.parent) as raw:
        trial = Path(raw)
        with _open(directory / 'board.sqlite3.gz', 'rb') as compressed, _open(trial / 'board.sqlite3', 'xb') as destination:
            with gzip.GzipFile(fileobj=compressed, mode='rb') as source:
                shutil.copyfileobj(source, destination)
        _guard(trial / 'board.sqlite3')
        with closing(sqlite3.connect(trial / 'board.sqlite3')) as connection:
            if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise BoardError('BACKUP_INVALID', 'SQLite integrity check failed')
            if connection.execute('PRAGMA foreign_key_check').fetchall():
                raise BoardError('BACKUP_INVALID', 'SQLite foreign key check failed')
            if manifest.get('databaseSnapshot') is not None and database_snapshot(connection) != manifest['databaseSnapshot']:
                raise BoardError('BACKUP_INVALID', 'Backup database fingerprint metadata does not match')
        if manifest.get('schema') == PREVIOUS_SCHEMA_VERSION:
            # A pre-upgrade backup must also prove it migrates cleanly.
            from .migrations import migrate_14_to_15
            with closing(sqlite3.connect(trial / 'board.sqlite3', isolation_level=None)) as connection:
                migrate_14_to_15(connection)
        # Exercise the real current-schema opener in a separate private directory.
        Database(trial).initialize()
    return manifest


def _backup_paths(root: Path) -> tuple[Path, Path, Path, Path, Path]:
    _guard(root)
    if _linked(root) or (root.exists() and not root.is_dir()):
        raise BoardError('BACKUP_UNSAFE_PATH', 'Backup root must be a real directory')
    incoming, current, previous = (root / name for name in ('.incoming', 'current', '.previous'))
    journal, temporary = root / '.publish.json', root / '.publish.tmp'
    for path in (incoming, current, previous, journal, temporary, root / '.lock'):
        _guard(path)
    for path in (incoming, current, previous):
        if path.exists() and not path.is_dir():
            raise BoardError('BACKUP_UNSAFE_PATH', 'Backup generation must be a directory')
    for path in (journal, temporary):
        if path.exists() and not path.is_file():
            raise BoardError('BACKUP_UNSAFE_PATH', 'Backup journal must be a regular file')
    return incoming, current, previous, journal, temporary


def _publication_journal(root: Path, *, had_current: bool) -> None:
    _, _, _, journal, temporary = _backup_paths(root)
    if journal.exists() or temporary.exists():
        raise BoardError('BACKUP_RECOVERY_REQUIRED', 'Existing publication journal must be recovered')
    with _open(temporary, 'x') as stream:
        json.dump({'format': 1, 'hadCurrent': had_current}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    _rename(temporary, journal)
    sync_dir(root)


def _recover_publication(root: Path) -> None:
    """Converge a journaled Windows publication under the backup lock."""
    incoming, current, previous, journal, temporary = _backup_paths(root)
    if not journal.exists():
        if previous.exists():
            raise BoardError('BACKUP_RECOVERY_REQUIRED', 'Previous backup exists without a publication journal')
        temporary.unlink(missing_ok=True)  # An unpublished journal draft.
        if _windows() and not current.exists() and incoming.exists():
            # A first publication can stop immediately before its journal
            # rename. A fully verified candidate is the only available copy.
            _verify(incoming)
            _rename(incoming, current)
            sync_dir(root)
        elif current.exists() and incoming.exists():
            _verify(current)
            shutil.rmtree(incoming)
            sync_dir(root)
        return
    try:
        with _open(journal, 'rb') as stream:
            record = json.load(stream)
    except (OSError, ValueError) as error:
        raise BoardError('BACKUP_RECOVERY_REQUIRED', 'Publication journal is unreadable') from error
    if (not isinstance(record, dict) or set(record) != {'format', 'hadCurrent'}
            or type(record['format']) is not int or record['format'] != 1
            or type(record['hadCurrent']) is not bool):
        raise BoardError('BACKUP_RECOVERY_REQUIRED', 'Publication journal has invalid or unsafe fields')
    had_current = record['hadCurrent']
    if previous.exists() and not had_current:
        raise BoardError('BACKUP_RECOVERY_REQUIRED', 'Unexpected previous backup for first publication')

    if previous.exists():
        # After the first rename, the old copy is parked in .previous. After
        # the second, current is the candidate and .incoming is absent.
        if current.exists() and incoming.exists():
            raise BoardError('BACKUP_RECOVERY_REQUIRED', 'Ambiguous backup publication paths')
        if current.exists():
            try:
                _verify(current)
            except Exception:
                # Retain both copies if the old copy cannot be proved valid.
                _verify(previous)
                _rename(current, incoming)
                sync_dir(root)
                _rename(previous, current)
                sync_dir(root)
            else:
                shutil.rmtree(previous)
                sync_dir(root)
        else:
            _verify(previous)
            _rename(previous, current)
            sync_dir(root)
    elif current.exists():
        # This is either the pre-first-rename old copy or the published new
        # copy. An incoming candidate alongside current identifies the former.
        _verify(current)
        if had_current and not incoming.exists():
            # Previous was already retired after the new current was verified.
            pass
        elif not had_current and incoming.exists():
            raise BoardError('BACKUP_RECOVERY_REQUIRED', 'Ambiguous first publication paths')
    elif incoming.exists() and not had_current:
        _verify(incoming)
        _rename(incoming, current)
        sync_dir(root)
    else:
        raise BoardError('BACKUP_RECOVERY_REQUIRED', 'No complete backup generation can be recovered')

    # The recovered current must be whole before any remaining candidate is
    # discarded. An interrupted cleanup leaves the journal for another pass.
    _verify(current)
    if incoming.exists():
        shutil.rmtree(incoming)
        sync_dir(root)
    temporary.unlink(missing_ok=True)
    journal.unlink()
    sync_dir(root)


def recover(state: Path) -> Path:
    """Recover an interrupted publication before reading backups/current.

    Call this before resolving a restore path or comparing it with an upgrade
    journal. Only fixed entries below state/backups are ever renamed/deleted.
    """
    root = Path(state) / 'backups'
    _backup_paths(root)
    if not root.exists():
        return root / 'current'
    lock = _open_fd(root / '.lock', os.O_CREAT | os.O_RDWR)
    locked = False
    try:
        locking.lock(lock)
        locked = True
        _recover_publication(root)
        return root / 'current'
    finally:
        if locked:
            locking.unlock(lock)
        os.close(lock)


def create(store, *, runtime_identity: dict | None = None, plugin_commit: str | None = None, contract_version: str = CONTRACT_VERSION) -> dict:
    started = time.monotonic()
    if _linked_component(store.directory):
        raise BoardError('BACKUP_UNSAFE_PATH', 'Backup state path cannot contain links', path='.')
    state = store.directory.resolve()
    inventory = list(preflight_entries(state))
    for kind, path, reason in inventory:
        if kind == 'rejected':
            relative = _shown_path(path.relative_to(state).as_posix())
            raise BoardError('BACKUP_UNSAFE_PATH', 'Backup refuses ' + relative + ': ' + reason,
                             path=relative, reason=reason)
    root = state / 'backups'
    incoming, current, previous, journal, _ = _backup_paths(root)
    root.mkdir(mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    lock = _open_fd(root / '.lock', os.O_CREAT | os.O_RDWR)
    try:
        locking.lock(lock, blocking=False)
    except BlockingIOError:
        os.close(lock)
        raise BoardError('BACKUP_BUSY', 'Another backup is in progress')
    published = False
    try:
        if _windows() and not journal.exists() and not current.exists() and incoming.exists():
            try:
                _verify(incoming)
            except Exception:
                # No valid backup existed yet; let this call rebuild it.
                shutil.rmtree(incoming)
        _recover_publication(root)
        # An unpublished POSIX candidate can be removed; Windows journaled
        # publication has already been recovered above.
        if incoming.exists():
            shutil.rmtree(incoming)
        incoming.mkdir(mode=0o700)
        snapshot = incoming / 'board.sqlite3'
        _guard(state / 'board.sqlite3')
        with _open(snapshot, 'xb'):
            pass
        _guard(snapshot)
        with store.db.connect() as source, closing(sqlite3.connect(snapshot)) as target:
            source.backup(target)
        os.chmod(snapshot, 0o600)
        with closing(sqlite3.connect(snapshot)) as connection:
            snapshot_metadata = database_snapshot(connection)
            schema = int(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
        with _open(snapshot, 'rb') as source, _open(incoming / 'board.sqlite3.gz', 'xb') as compressed:
            with gzip.GzipFile(fileobj=compressed, mode='wb', compresslevel=6) as target:
                shutil.copyfileobj(source, target)
        snapshot.unlink()
        skipped_attempt_entries = sorted(_shown_path(path.relative_to(state).as_posix())
                                         for kind, path, _ in inventory if kind == 'skipped')
        for kind, path, _ in inventory:
            if kind == 'copied' and path != state / 'board.sqlite3':
                _private_copy(path, incoming / 'state' / path.relative_to(state))
        skipped_attempt_entries.sort()
        files = {str(path.relative_to(incoming)): {'bytes': path.lstat().st_size, 'sha256': digest(path)} for path in _regular_files(incoming)}
        manifest = {'format': 1, 'createdAt': utc_now(), 'schema': schema, 'contract': contract_version, 'backupToolContract': CONTRACT_VERSION,
                    'databaseSnapshot':snapshot_metadata, 'runtime': runtime_identity, 'pluginCommit': plugin_commit, 'pluginCommitStatus': 'recorded' if plugin_commit else 'unavailable-in-source-metadata',
                    'attemptEvidencePolicy': attempt_evidence.POLICY,
                    'skippedAttemptEntries': {'count': len(skipped_attempt_entries), 'paths': skipped_attempt_entries[:20]},
                    'files': files}
        manifest_path = incoming / 'manifest.json'
        with _open(manifest_path, 'x') as stream:
            json.dump(manifest, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        verify(incoming)
        for path in _regular_files(incoming):
            with _open(path, 'rb') as stream:
                os.fsync(stream.fileno())
        for directory, _, _ in os.walk(incoming, topdown=False):
            sync_dir(Path(directory))
        if _windows():
            _publication_journal(root, had_current=current.exists())
            if current.exists():
                _rename(current, previous)
                sync_dir(root)
            _rename(incoming, current)
            sync_dir(root)
            _verify(current)
            if previous.exists():
                shutil.rmtree(previous)
                sync_dir(root)
            journal.unlink()
            sync_dir(root)
        elif current.exists():
            exchange(incoming, current)
        else:
            os.replace(incoming, current)
        published = True
        sync_dir(root)
        if incoming.exists():
            shutil.rmtree(incoming)
            sync_dir(root)
        return {'path': str(current), 'verified': True, 'schema': schema,
                'bytes': sum(row['bytes'] for row in files.values()) + manifest_path_size(current),
                'fileCount': len(files), 'skippedAttemptEntries': len(skipped_attempt_entries),
                'durationSeconds': round(time.monotonic() - started, 3)}
    except Exception:
        if not published and not journal.exists() and incoming.exists() and not _linked(incoming):
            shutil.rmtree(incoming)
        raise
    finally:
        locking.unlock(lock)
        os.close(lock)


def manifest_path_size(current: Path) -> int:
    with _open(current / 'manifest.json', 'rb') as stream:
        return os.fstat(stream.fileno()).st_size


def source_commit() -> str | None:
    from .runtime import project_root
    for metadata in (project_root() / 'READY.json', project_root() / 'src/buddy/build-info.json'):
        try:
            with _open(metadata, 'rb') as stream:
                value = json.load(stream).get('sourceCommit')
            if isinstance(value, str) and len(value) == 40:
                return value
        except (OSError, ValueError, BoardError):
            pass
    try:
        return subprocess.check_output(['git', '-C', str(project_root()), 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
