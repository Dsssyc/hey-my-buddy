"""Service-owned, verified, single-generation backup (no schema changes)."""
from __future__ import annotations

from contextlib import closing
import ctypes
import fcntl
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

from .db import SCHEMA_VERSION, Database, utc_now
from .contracts import CONTRACT_VERSION
from .errors import BoardError


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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


def _regular_files(root: Path):
    if not root.exists():
        return
    if root.is_symlink():
        raise BoardError('BACKUP_UNSAFE_PATH', 'Backup source cannot be a symlink')
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        if any((parent / d).is_symlink() for d in dirs):
            raise BoardError('BACKUP_UNSAFE_PATH', 'Backup source contains a linked directory')
        for name in files:
            path = parent / name
            if path.is_symlink() or not path.is_file():
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
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest.get('format') != 1 or manifest.get('schema') != SCHEMA_VERSION:
        raise BoardError('BACKUP_INVALID', 'Unsupported backup format or schema')
    actual = {str(p.relative_to(directory)) for p in _regular_files(directory) if p != directory / 'manifest.json'}
    if actual != set(manifest['files']):
        raise BoardError('BACKUP_INVALID', 'Backup inventory mismatch')
    for relative, row in manifest['files'].items():
        path = directory / relative
        if Path(relative).is_absolute() or '..' in Path(relative).parts or path.is_symlink():
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
        # Exercise the real current-schema opener in a separate private directory.
        Database(trial).initialize()
    return manifest


def create(store, *, runtime_identity: dict | None = None, plugin_commit: str | None = None, contract_version: str = CONTRACT_VERSION) -> dict:
    started = time.monotonic()
    state = store.directory.resolve()
    root = state / 'backups'
    if root.is_symlink():
        raise BoardError('BACKUP_UNSAFE_PATH', 'Backup root cannot be linked')
    root.mkdir(mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    lock = os.open(root / '.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock)
        raise BoardError('BACKUP_BUSY', 'Another backup is in progress')
    incoming, current = root / '.incoming', root / 'current'
    published = False
    try:
        for path in (incoming, current):
            if path.is_symlink():
                raise BoardError('BACKUP_UNSAFE_PATH', 'Backup generation cannot be linked')
        # A crash before swap left an incomplete candidate; after swap it left the
        # previous verified backup. In either case current has never disappeared.
        if incoming.exists():
            shutil.rmtree(incoming)
        incoming.mkdir(mode=0o700)
        snapshot = incoming / 'board.sqlite3'
        with store.db.connect() as source, closing(sqlite3.connect(snapshot)) as target:
            source.backup(target)
        os.chmod(snapshot, 0o600)
        with closing(sqlite3.connect(snapshot)) as connection:
            snapshot_metadata = database_snapshot(connection)
        with snapshot.open('rb') as source, gzip.open(incoming / 'board.sqlite3.gz', 'wb', compresslevel=6) as target:
            shutil.copyfileobj(source, target)
        os.chmod(incoming / 'board.sqlite3.gz', 0o600)
        snapshot.unlink()
        for name in ('attempts', 'controls', 'submissions'):
            for path in _regular_files(state / name):
                _private_copy(path, incoming / 'state' / path.relative_to(state))
        if (state / 'workers').is_symlink():
            raise BoardError('BACKUP_UNSAFE_PATH', 'Worker root cannot be linked')
        for path in sorted([*(state / 'workers').glob('*/receipts/*.json'), *(state / 'workers').glob('*/startup.json'), *(state / 'workers').glob('*/orphaned.json')]):
            if path.is_symlink() or any(p.is_symlink() for p in (path.parent, path.parent.parent)):
                raise BoardError('BACKUP_UNSAFE_PATH', 'Receipt spool cannot be linked')
            _private_copy(path, incoming / 'state' / path.relative_to(state))
        for name in ('console-sessions.json', 'console-settings.json', 'worker-pool.json', 'runtime-retention.json', 'active-runtime.json', 'launch-settings.json'):
            source = state / name
            if source.exists():
                if source.is_symlink():
                    raise BoardError('BACKUP_UNSAFE_PATH', 'State record cannot be linked')
                _private_copy(source, incoming / 'state' / source.name)
        files = {str(path.relative_to(incoming)): {'bytes': path.stat().st_size, 'sha256': digest(path)} for path in _regular_files(incoming)}
        manifest = {'format': 1, 'createdAt': utc_now(), 'schema': SCHEMA_VERSION, 'contract': contract_version, 'backupToolContract': CONTRACT_VERSION,
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
        if current.exists():
            exchange(incoming, current)
        else:
            os.replace(incoming, current)
        published = True
        sync_dir(root)
        if incoming.exists():
            shutil.rmtree(incoming)
            sync_dir(root)
        return {'path': str(current), 'verified': True, 'schema': SCHEMA_VERSION,
                'bytes': sum(row['bytes'] for row in files.values()) + manifest_path_size(current),
                'fileCount': len(files), 'durationSeconds': round(time.monotonic() - started, 3)}
    except Exception:
        if not published and incoming.exists() and not incoming.is_symlink():
            shutil.rmtree(incoming)
        raise
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
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
