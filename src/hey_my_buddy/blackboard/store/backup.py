"""Verified, single-generation archive for installation rollback."""
from __future__ import annotations

from contextlib import closing, ExitStack
from ... import locking
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sqlite3
import time
import zlib

from .db import SCHEMA_VERSION, utc_now
from ...protocol.contracts import CONTRACT_VERSION
from ...errors import BoardError
from ... import private_dirs, private_files
from ...private_dirs import linked as _linked
from ...private_files import publish_bytes


def digest(path: Path) -> str:
    with _open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _rename(source: Path, target: Path) -> None:
    os.replace(source, target)


#: Bound for state-relative paths carried in errors.
_SHOWN_PATH_LIMIT = 200


def _shown_path(relative: str) -> str:
    text = relative if len(relative) <= _SHOWN_PATH_LIMIT else relative[:_SHOWN_PATH_LIMIT - 3] + '...'
    return text


def _guard(path: Path) -> None:
    if _linked(path):
        raise BoardError('BACKUP_UNSAFE_PATH', 'Backup entry is linked', path=str(path))


def _open_fd(path: Path, flags: int, mode: int = 0o600) -> int:
    try:
        return private_dirs.open_regular_fd(path, flags, mode)
    except BoardError as error:
        raise BoardError('BACKUP_UNSAFE_PATH', error.message, **error.details) from error


def _open(path: Path, mode: str):
    flags = os.O_RDONLY if mode == 'rb' else os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = _open_fd(path, flags)
    return os.fdopen(fd, mode)


def _regular_files(root: Path):
    """Walk backup payloads, refusing links and special files."""
    _guard(root)
    try:
        root_info = root.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(root_info.st_mode):
        raise BoardError('BACKUP_UNSAFE_PATH', 'Backup source must be a directory', path=str(root))

    def report(path: Path) -> str:
        relative = str(path.relative_to(root))
        return _shown_path(relative)

    def visit(parent):
        with os.scandir(parent) as entries:
            children = sorted(Path(entry.path) for entry in entries)
        for path in children:
            if _linked(path):
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


STATE_FILES = ('console-settings.json',)


def _private_copy(source: Path, target: Path) -> None:
    private_dirs.ensure_private_dir(target.parent)
    with _open(source, 'rb') as src, _open(target, 'xb') as dst:
        shutil.copyfileobj(src, dst)
        dst.flush()
        os.fsync(dst.fileno())


def database_snapshot(connection, *, event_head: int | None = None,
                      column_projections: dict[str, tuple[str, ...]] | None = None) -> dict:
    """Hash the historical sorted JSON format with SQLite's bounded sorter.

    Python retains one row at a time.
    """
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    counts = {name:connection.execute('SELECT COUNT(*) FROM "'+name.replace('"','""')+'"').fetchone()[0]
              for name in tables}
    if event_head is None:
        event_head = connection.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]

    def fingerprint(name):
        if not counts[name]:
            return hashlib.sha256(b'').hexdigest()

        def encode(*row):
            return json.dumps(list(row), ensure_ascii=False, separators=(',', ':'), default=lambda value:value.hex())

        quoted = '"' + name.replace('"','""') + '"'
        columns = (column_projections or {}).get(name)
        if columns is None:
            columns = [column[0] for column in connection.execute('SELECT * FROM ' + quoted + ' LIMIT 0').description]
        arguments = 'hey_my_buddy_snapshot_row(' + ','.join('"' + column.replace('"', '""') + '"' for column in columns) + ')'
        sql, args = 'SELECT ' + arguments + ' AS encoded FROM ' + quoted, ()
        if name == 'events':
            sql += ' WHERE seq<=?'; args = (event_head,)
        elif name == 'sqlite_sequence':
            sql += " WHERE name NOT IN ('events')"
        sql += ' ORDER BY encoded COLLATE BINARY'
        connection.create_function('hey_my_buddy_snapshot_row', -1, encode)
        try:
            rows = (row[0] for row in connection.execute(sql, args))
            result = hashlib.sha256()
            separator = b''
            for encoded in rows:
                result.update(separator)
                result.update(encoded.encode())
                separator = b'\n'
            return result.hexdigest()
        finally:
            connection.create_function('hey_my_buddy_snapshot_row', -1, None)

    fingerprints = {name:fingerprint(name) for name in tables}
    return {'tables':counts, 'fingerprints':fingerprints, 'eventHead':event_head}


def verify(directory: Path) -> dict:
    """Verify a generation, recovering an interrupted current publication first."""
    directory = Path(directory)
    if directory.name == 'current' and directory.parent.name == 'backups':
        recover(directory.parent.parent)
    return _verify(directory)


def _verify(directory: Path) -> dict:
    _guard(directory)
    try:
        with _open(directory / 'manifest.json', 'rb') as stream:
            manifest = json.load(stream)
    except (OSError, ValueError) as error:
        raise BoardError('BACKUP_INVALID', 'Backup manifest is unreadable') from error
    required = {'format', 'createdAt', 'schema', 'contract', 'backupToolContract',
                'databaseSnapshot', 'runtime', 'pluginCommit', 'pluginCommitStatus', 'files'}
    if not isinstance(manifest, dict) or not required <= manifest.keys() or not isinstance(manifest['files'], dict):
        raise BoardError('BACKUP_INVALID', 'Backup manifest is incomplete')
    if manifest.get('format') != 1 or manifest.get('schema') != SCHEMA_VERSION:
        raise BoardError('BACKUP_INVALID', 'Unsupported backup format or schema')
    allowed = {'board.sqlite3.gz', *(str(Path('state') / name) for name in STATE_FILES)}
    if 'board.sqlite3.gz' not in manifest['files'] or set(manifest['files']) - allowed:
        raise BoardError('BACKUP_INVALID', 'Backup contains files outside the rollback inventory')
    actual = {str(p.relative_to(directory)) for p in _regular_files(directory) if p != directory / 'manifest.json'}
    if actual != set(manifest['files']):
        raise BoardError('BACKUP_INVALID', 'Backup inventory mismatch')
    for relative, row in manifest['files'].items():
        if not isinstance(row, dict) or not {'bytes', 'sha256'} <= row.keys():
            raise BoardError('BACKUP_INVALID', 'Backup manifest is incomplete')
        path = directory / relative
        if path.lstat().st_size != row['bytes'] or digest(path) != row['sha256']:
            raise BoardError('BACKUP_INVALID', 'Backup payload hash mismatch', file=relative)
    with ExitStack() as cleanup:
        trial = private_dirs.make_temporary_dir(prefix='.verify-', directory=directory.parent)
        cleanup.callback(private_dirs.remove_tree, trial)
        with _open(directory / 'board.sqlite3.gz', 'rb') as compressed, _open(trial / 'board.sqlite3', 'xb') as destination:
            try:
                with gzip.GzipFile(fileobj=compressed, mode='rb') as source:
                    shutil.copyfileobj(source, destination)
            except (OSError, EOFError, zlib.error) as error:
                raise BoardError('BACKUP_INVALID', 'Backup compressed database is invalid', reason=str(error)) from error
        try:
            with closing(sqlite3.connect(trial / 'board.sqlite3')) as connection:
                schema = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                if schema is None or schema[0] != str(manifest['schema']):
                    raise BoardError('BACKUP_INVALID', 'Backup schema does not match its manifest')
                if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise BoardError('BACKUP_INVALID', 'SQLite integrity check failed')
                if connection.execute('PRAGMA foreign_key_check').fetchall():
                    raise BoardError('BACKUP_INVALID', 'SQLite foreign key check failed')
                if database_snapshot(connection) != manifest['databaseSnapshot']:
                    raise BoardError('BACKUP_INVALID', 'Backup database fingerprint metadata does not match')
        except sqlite3.DatabaseError as error:
            raise BoardError('BACKUP_INVALID', 'Backup database is invalid') from error
    return manifest


def _backup_paths(root: Path) -> tuple[Path, Path, Path, Path]:
    _guard(root)
    if root.exists() and not root.is_dir():
        raise BoardError('BACKUP_UNSAFE_PATH', 'Backup root must be a real directory')
    incoming, current, previous = (root / name for name in ('.incoming', 'current', '.previous'))
    journal = root / '.publish.json'
    for path in (incoming, current, previous):
        if path.exists() and not path.is_dir():
            raise BoardError('BACKUP_UNSAFE_PATH', 'Backup generation must be a directory')
    if journal.exists() and not journal.is_file():
        raise BoardError('BACKUP_UNSAFE_PATH', 'Backup journal must be a regular file')
    return incoming, current, previous, journal


def _remove_generation(path: Path) -> None:
    _guard(path)
    shutil.rmtree(path)


def _publication_journal(root: Path, *, had_current: bool) -> None:
    _, _, _, journal = _backup_paths(root)
    if journal.exists():
        raise BoardError('BACKUP_RECOVERY_REQUIRED', 'Existing publication journal must be recovered')
    raw = json.dumps({'format': 1, 'hadCurrent': had_current}).encode('utf-8')
    publish_bytes(journal, raw, immutable=True)


def _recover_publication(root: Path) -> None:
    """Converge a journaled publication under the backup lock."""
    incoming, current, previous, journal = _backup_paths(root)
    if not journal.exists():
        if previous.exists():
            raise BoardError('BACKUP_RECOVERY_REQUIRED', 'Previous backup exists without a publication journal')
        if not current.exists() and incoming.exists():
            # A first publication can stop immediately before its journal
            # link. A fully verified candidate is the only available copy.
            try:
                _verify(incoming)
            except BoardError:
                # No complete generation has been published yet. A later
                # create may discard this incomplete candidate and rebuild.
                return
            _rename(incoming, current)
            private_files.sync_directory(root, follow_symlinks=False)
        elif current.exists() and incoming.exists():
            _verify(current)
            _remove_generation(incoming)
            private_files.sync_directory(root, follow_symlinks=False)
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
            except BoardError:
                # Retain both copies if the old copy cannot be proved valid.
                _verify(previous)
                _rename(current, incoming)
                private_files.sync_directory(root, follow_symlinks=False)
                _rename(previous, current)
                private_files.sync_directory(root, follow_symlinks=False)
            else:
                _remove_generation(previous)
                private_files.sync_directory(root, follow_symlinks=False)
        else:
            _verify(previous)
            _rename(previous, current)
            private_files.sync_directory(root, follow_symlinks=False)
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
        private_files.sync_directory(root, follow_symlinks=False)
    else:
        raise BoardError('BACKUP_RECOVERY_REQUIRED', 'No complete backup generation can be recovered')

    # The recovered current must be whole before any remaining candidate is
    # discarded. An interrupted cleanup leaves the journal for another pass.
    _verify(current)
    if incoming.exists():
        _remove_generation(incoming)
        private_files.sync_directory(root, follow_symlinks=False)
    journal.unlink()
    private_files.sync_directory(root, follow_symlinks=False)


def recover(state: Path) -> Path:
    """Recover an interrupted publication before reading backups/current.

    Only fixed entries below state/backups are ever renamed/deleted.
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


def create(state: Path, *, runtime_identity: dict | None, plugin_commit: str | None, contract_version: str) -> dict:
    started = time.monotonic()
    if _linked(state):
        raise BoardError('BACKUP_UNSAFE_PATH', 'Backup state root cannot be a linked entry', path='.')
    state = state.resolve()
    wal = state / 'board.sqlite3-wal'
    if wal.exists() and wal.stat().st_size:
        raise BoardError('BACKUP_INVALID', 'Exact stopped-board archive requires a checkpointed WAL')
    root = state / 'backups'
    incoming, current, previous, journal = _backup_paths(root)
    private_dirs.ensure_private_dir(root)
    lock = _open_fd(root / '.lock', os.O_CREAT | os.O_RDWR)
    try:
        locking.lock(lock, blocking=False)
    except BlockingIOError:
        os.close(lock)
        raise BoardError('BACKUP_BUSY', 'Another backup is in progress')
    try:
        _recover_publication(root)
        # An unpublished candidate can be removed after recovery.
        if incoming.exists():
            _remove_generation(incoming)
        private_dirs.ensure_private_dir(incoming)
        snapshot = incoming / 'board.sqlite3'
        # The coordinator owns both writer locks and has detached every owner.
        # Copy the stopped database verbatim so the archive preserves its bytes.
        _private_copy(state / 'board.sqlite3', snapshot)
        with closing(sqlite3.connect(snapshot)) as connection:
            snapshot_metadata = database_snapshot(connection)
            schema = int(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
        with _open(snapshot, 'rb') as source, _open(incoming / 'board.sqlite3.gz', 'xb') as compressed:
            with gzip.GzipFile(fileobj=compressed, mode='wb', compresslevel=6) as target:
                shutil.copyfileobj(source, target)
            compressed.flush()
            os.fsync(compressed.fileno())
        snapshot.unlink()
        for name in STATE_FILES:
            path = state / name
            if path.exists() or _linked(path):
                _private_copy(path, incoming / 'state' / name)
        files = {str(path.relative_to(incoming)): {'bytes': path.lstat().st_size, 'sha256': digest(path)} for path in _regular_files(incoming)}
        manifest = {'format': 1, 'createdAt': utc_now(), 'schema': schema, 'contract': contract_version, 'backupToolContract': CONTRACT_VERSION,
                    'databaseSnapshot':snapshot_metadata, 'runtime': runtime_identity, 'pluginCommit': plugin_commit, 'pluginCommitStatus': 'recorded' if plugin_commit else 'unavailable-in-runtime-metadata',
                    'files': files}
        manifest_path = incoming / 'manifest.json'
        raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode('utf-8')
        publish_bytes(manifest_path, raw, immutable=True)
        verify(incoming)
        for directory, _, _ in os.walk(incoming, topdown=False):
            directory_path = Path(directory)
            private_files.sync_directory(directory_path, follow_symlinks=False)
        _publication_journal(root, had_current=current.exists())
        if current.exists():
            _rename(current, previous)
            private_files.sync_directory(root, follow_symlinks=False)
        _rename(incoming, current)
        private_files.sync_directory(root, follow_symlinks=False)
        if previous.exists():
            _remove_generation(previous)
            private_files.sync_directory(root, follow_symlinks=False)
        journal.unlink()
        private_files.sync_directory(root, follow_symlinks=False)
        return {'path': str(current), 'verified': True, 'schema': schema,
                'bytes': sum(row['bytes'] for row in files.values()) + manifest_path_size(current),
                'fileCount': len(files),
                'durationSeconds': round(time.monotonic() - started, 3)}
    finally:
        locking.unlock(lock)
        os.close(lock)


def manifest_path_size(current: Path) -> int:
    with _open(current / 'manifest.json', 'rb') as stream:
        return os.fstat(stream.fileno()).st_size
