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
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

from .db import PREVIOUS_SCHEMA_VERSION, SCHEMA_VERSION, Database, utc_now
from .contracts import CONTRACT_VERSION
from .errors import BoardError


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sync_dir(path: Path) -> None:
    """Flush POSIX directory metadata.

    Python has no portable Windows directory flush. On Windows the backup
    journal and payload files are flushed, but a power-loss guarantee for
    directory renames is not claimed; recovery covers process interruption.
    """
    if _windows():
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _windows() -> bool:
    return sys.platform == 'win32'


def _rename(source: Path, target: Path) -> None:
    os.replace(source, target)


def _linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def exchange(left: Path, right: Path) -> None:
    """Atomic directory exchange: after a crash current is always one whole copy."""
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


def _excluded_attempt_path(relative: Path, *, directory: bool) -> bool:
    parts = relative.parts
    if directory:
        return len(parts) == 4 and parts[2:] == ('native', 'codex-home')
    return len(parts) == 3 and parts[2] in ('builtin-provider.json', 'personal-provider.json')


def _regular_files(root: Path, *, exclude_attempt_private: bool = False):
    if _linked(root):
        raise BoardError('BACKUP_UNSAFE_PATH', 'Backup source cannot be a symlink')
    if not root.exists():
        return
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        if exclude_attempt_private:
            dirs[:] = [name for name in dirs
                       if not _excluded_attempt_path((parent / name).relative_to(root), directory=True)]
            files = [name for name in files
                     if not _excluded_attempt_path((parent / name).relative_to(root), directory=False)]
        if any(_linked(parent / d) for d in dirs):
            raise BoardError('BACKUP_UNSAFE_PATH', 'Backup source contains a linked directory')
        for name in files:
            path = parent / name
            if _linked(path) or not path.is_file():
                raise BoardError('BACKUP_UNSAFE_PATH', 'Backup source contains a nonregular file')
            yield path


def _private_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with source.open('rb') as src, target.open('xb') as dst:
        os.chmod(target, 0o600)
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
    if _linked(directory) or _linked(directory / 'manifest.json'):
        raise BoardError('BACKUP_UNSAFE_PATH', 'Backup generation cannot be linked')
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest.get('format') != 1 or manifest.get('schema') not in (SCHEMA_VERSION, PREVIOUS_SCHEMA_VERSION):
        raise BoardError('BACKUP_INVALID', 'Unsupported backup format or schema')
    actual = {str(p.relative_to(directory)) for p in _regular_files(directory) if p != directory / 'manifest.json'}
    if actual != set(manifest['files']):
        raise BoardError('BACKUP_INVALID', 'Backup inventory mismatch')
    for relative, row in manifest['files'].items():
        path = directory / relative
        if Path(relative).is_absolute() or '..' in Path(relative).parts or _linked(path):
            raise BoardError('BACKUP_INVALID', 'Unsafe manifest path')
        if path.stat().st_size != row['bytes'] or digest(path) != row['sha256']:
            raise BoardError('BACKUP_INVALID', 'Backup payload hash mismatch', file=relative)
    with tempfile.TemporaryDirectory(prefix='.verify-', dir=directory.parent) as raw:
        trial = Path(raw)
        with gzip.open(directory / 'board.sqlite3.gz', 'rb') as source, (trial / 'board.sqlite3').open('wb') as destination:
            shutil.copyfileobj(source, destination)
        os.chmod(trial / 'board.sqlite3', 0o600)
        with closing(sqlite3.connect(trial / 'board.sqlite3')) as connection:
            if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise BoardError('BACKUP_INVALID', 'SQLite integrity check failed')
            if connection.execute('PRAGMA foreign_key_check').fetchall():
                raise BoardError('BACKUP_INVALID', 'SQLite foreign key check failed')
            if manifest.get('databaseSnapshot') is not None and database_snapshot(connection) != manifest['databaseSnapshot']:
                raise BoardError('BACKUP_INVALID', 'Backup database fingerprint metadata does not match')
        if manifest.get('schema') == PREVIOUS_SCHEMA_VERSION:
            # A pre-upgrade backup must also prove it migrates cleanly.
            from .migrations import migrate_13_to_14
            with closing(sqlite3.connect(trial / 'board.sqlite3', isolation_level=None)) as connection:
                migrate_13_to_14(connection)
        # Exercise the real current-schema opener in a separate private directory.
        Database(trial).initialize()
    return manifest


def _backup_paths(root: Path) -> tuple[Path, Path, Path, Path, Path]:
    if _linked(root) or (root.exists() and not root.is_dir()):
        raise BoardError('BACKUP_UNSAFE_PATH', 'Backup root must be a real directory')
    incoming, current, previous = (root / name for name in ('.incoming', 'current', '.previous'))
    journal, temporary = root / '.publish.json', root / '.publish.tmp'
    for path in (incoming, current, previous, journal, temporary, root / '.lock'):
        if _linked(path):
            raise BoardError('BACKUP_UNSAFE_PATH', 'Backup publication path cannot be linked')
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
    with temporary.open('x') as stream:
        os.chmod(temporary, 0o600)
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
        record = json.loads(journal.read_text())
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
    lock = os.open(root / '.lock', os.O_CREAT | os.O_RDWR, 0o600)
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
    state = store.directory.resolve()
    root = state / 'backups'
    incoming, current, previous, journal, _ = _backup_paths(root)
    root.mkdir(mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    lock = os.open(root / '.lock', os.O_CREAT | os.O_RDWR, 0o600)
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
        with store.db.connect() as source, closing(sqlite3.connect(snapshot)) as target:
            source.backup(target)
        os.chmod(snapshot, 0o600)
        with closing(sqlite3.connect(snapshot)) as connection:
            snapshot_metadata = database_snapshot(connection)
            schema = int(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
        with snapshot.open('rb') as source, gzip.open(incoming / 'board.sqlite3.gz', 'wb', compresslevel=6) as target:
            shutil.copyfileobj(source, target)
        os.chmod(incoming / 'board.sqlite3.gz', 0o600)
        snapshot.unlink()
        for name in ('attempts', 'controls', 'submissions'):
            for path in _regular_files(state / name, exclude_attempt_private=name == 'attempts'):
                _private_copy(path, incoming / 'state' / path.relative_to(state))
        if _linked(state / 'workers'):
            raise BoardError('BACKUP_UNSAFE_PATH', 'Worker root cannot be linked')
        for path in sorted([*(state / 'workers').glob('*/receipts/*.json'), *(state / 'workers').glob('*/startup.json'), *(state / 'workers').glob('*/orphaned.json')]):
            if _linked(path) or any(_linked(p) for p in (path.parent, path.parent.parent)):
                raise BoardError('BACKUP_UNSAFE_PATH', 'Receipt spool cannot be linked')
            _private_copy(path, incoming / 'state' / path.relative_to(state))
        for name in ('console-sessions.json', 'console-settings.json', 'worker-pool.json', 'runtime-retention.json', 'active-runtime.json', 'launch-settings.json'):
            source = state / name
            if source.exists():
                if _linked(source):
                    raise BoardError('BACKUP_UNSAFE_PATH', 'State record cannot be linked')
                _private_copy(source, incoming / 'state' / source.name)
        files = {str(path.relative_to(incoming)): {'bytes': path.stat().st_size, 'sha256': digest(path)} for path in _regular_files(incoming)}
        manifest = {'format': 1, 'createdAt': utc_now(), 'schema': schema, 'contract': contract_version, 'backupToolContract': CONTRACT_VERSION,
                    'databaseSnapshot':snapshot_metadata, 'runtime': runtime_identity, 'pluginCommit': plugin_commit, 'pluginCommitStatus': 'recorded' if plugin_commit else 'unavailable-in-source-metadata', 'files': files}
        manifest_path = incoming / 'manifest.json'
        with manifest_path.open('x') as stream:
            os.chmod(manifest_path, 0o600)
            json.dump(manifest, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        verify(incoming)
        for path in _regular_files(incoming):
            with path.open('rb') as stream:
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
                'fileCount': len(files), 'durationSeconds': round(time.monotonic() - started, 3)}
    except Exception:
        if not published and not journal.exists() and incoming.exists() and not _linked(incoming):
            shutil.rmtree(incoming)
        raise
    finally:
        locking.unlock(lock)
        os.close(lock)


def manifest_path_size(current: Path) -> int:
    return (current / 'manifest.json').stat().st_size


def source_commit() -> str | None:
    from .runtime import project_root
    for metadata in (project_root() / 'READY.json', project_root() / 'src/buddy/build-info.json'):
        try:
            value = json.loads(metadata.read_text()).get('sourceCommit')
            if isinstance(value, str) and len(value) == 40:
                return value
        except (OSError, ValueError):
            pass
    try:
        return subprocess.check_output(['git', '-C', str(project_root()), 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
