"""Verified, single-generation archive for installation rollback."""
from __future__ import annotations

from contextlib import closing
import ctypes
from ... import locking
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sqlite3
import sys
import tempfile
import time

from .db import SCHEMA_VERSION, Database, utc_now
from .upgrade import BASE_SCHEMA_VERSION
from ...protocol.contracts import CONTRACT_VERSION
from ...errors import BoardError
from ... import private_dirs
from ...private_dirs import linked as _linked


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
    """Walk backup payloads, refusing links and special files."""
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


STATE_FILES = ('console-settings.json', 'runtime-retention.json',
               'active-runtime.json', 'launch-settings.json')


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


def database_snapshot(connection, *, event_head: int | None = None, exact: bool = False) -> dict:
    """Hash the historical sorted JSON format with SQLite's bounded sorter.

    Python retains one row at a time. ``exact`` adds the lifecycle rows normally
    exempted by backup without changing ordinary backup metadata.
    """
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    counts = {name:connection.execute('SELECT COUNT(*) FROM "'+name.replace('"','""')+'"').fetchone()[0]
              for name in tables}
    if event_head is None:
        event_head = connection.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]

    def fingerprint(name, *, exempt=False):
        if not counts[name]:
            return hashlib.sha256(b'').hexdigest()

        def encode(*row):
            return json.dumps(list(row), ensure_ascii=False, separators=(',', ':'), default=lambda value:value.hex())

        quoted = '"' + name.replace('"','""') + '"'
        columns = [column[0] for column in connection.execute('SELECT * FROM ' + quoted + ' LIMIT 0').description]
        arguments = ','.join('"' + column.replace('"', '""') + '"' for column in columns)
        limit = connection.getlimit(sqlite3.SQLITE_LIMIT_FUNCTION_ARG)
        wide = len(columns) > limit
        if wide:
            # SELECT * also supports generated columns and tables wider than a
            # SQLite function call. Join bounded chunks in the same JSON format.
            def encode_part(*row):
                return json.dumps(list(row), ensure_ascii=False, separators=(',', ':'), default=lambda value:value.hex())[1:-1]

            connection.create_function('hey_my_buddy_snapshot_part', -1, encode_part)
            quoted_columns = ['"' + column.replace('"', '""') + '"' for column in columns]
            parts = ['hey_my_buddy_snapshot_part(' + ','.join(quoted_columns[offset:offset + limit]) + ')'
                     for offset in range(0, len(columns), limit)]
            arguments = "'[' || " + " || ',' || ".join(parts) + " || ']'"
        else:
            arguments = 'hey_my_buddy_snapshot_row(' + arguments + ')'
        sql, args = 'SELECT ' + arguments + ' AS encoded FROM ' + quoted, ()
        if name == 'events':
            sql += ' WHERE seq<=?'; args = (event_head,)
        elif name == 'sqlite_sequence' and not exempt:
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
            if wide:
                connection.create_function('hey_my_buddy_snapshot_part', -1, None)

    fingerprints = {name:fingerprint(name) for name in tables}
    result = {'tables':counts, 'fingerprints':fingerprints, 'eventHead':event_head}
    if exact:
        result['exemptFingerprints'] = {name:fingerprint(name, exempt=True)
                                      for name in ('sqlite_sequence',) if name in tables}
    return result


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
    if manifest.get('format') != 1 or manifest.get('schema') not in (BASE_SCHEMA_VERSION, SCHEMA_VERSION):
        raise BoardError('BACKUP_INVALID', 'Unsupported backup format or schema')
    allowed = {'board.sqlite3.gz', *(str(Path('state') / name) for name in STATE_FILES)}
    if 'board.sqlite3.gz' not in manifest['files'] or set(manifest['files']) - allowed:
        raise BoardError('BACKUP_INVALID', 'Backup contains files outside the rollback inventory')
    actual = {str(p.relative_to(directory)) for p in _regular_files(directory) if p != directory / 'manifest.json'}
    if actual != set(manifest['files']):
        raise BoardError('BACKUP_INVALID', 'Backup inventory mismatch')
    for relative, row in manifest['files'].items():
        path = directory / relative
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
        with closing(sqlite3.connect(trial / 'board.sqlite3')) as connection:
            schema = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if schema is None or int(schema[0]) != manifest['schema']:
                raise BoardError('BACKUP_INVALID', 'Backup schema does not match its manifest')
        # The pending upgrade's released base is a valid rollback archive, but
        # ordinary Database.initialize deliberately accepts only current schema.
        if manifest['schema'] == SCHEMA_VERSION:
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


def create(state: Path, *, runtime_identity: dict | None = None, plugin_commit: str | None = None, contract_version: str = CONTRACT_VERSION) -> dict:
    started = time.monotonic()
    if _linked_component(state):
        raise BoardError('BACKUP_UNSAFE_PATH', 'Backup state path cannot contain links', path='.')
    state = state.resolve()
    wal = state / 'board.sqlite3-wal'
    if wal.exists() and wal.stat().st_size:
        raise BoardError('BACKUP_INVALID', 'Exact stopped-board archive requires a checkpointed WAL')
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
        # The coordinator owns both writer locks and has detached every owner.
        # Copy the stopped database verbatim so the archive preserves its bytes.
        _private_copy(state / 'board.sqlite3', snapshot)
        os.chmod(snapshot, 0o600)
        with closing(sqlite3.connect(snapshot)) as connection:
            snapshot_metadata = database_snapshot(connection)
            schema = int(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
        with _open(snapshot, 'rb') as source, _open(incoming / 'board.sqlite3.gz', 'xb') as compressed:
            with gzip.GzipFile(fileobj=compressed, mode='wb', compresslevel=6) as target:
                shutil.copyfileobj(source, target)
        snapshot.unlink()
        for name in STATE_FILES:
            path = state / name
            if path.exists() or _linked(path):
                _private_copy(path, incoming / 'state' / name)
        files = {str(path.relative_to(incoming)): {'bytes': path.lstat().st_size, 'sha256': digest(path)} for path in _regular_files(incoming)}
        manifest = {'format': 1, 'createdAt': utc_now(), 'schema': schema, 'contract': contract_version, 'backupToolContract': CONTRACT_VERSION,
                    'databaseSnapshot':snapshot_metadata, 'runtime': runtime_identity, 'pluginCommit': plugin_commit, 'pluginCommitStatus': 'recorded' if plugin_commit else 'unavailable-in-source-metadata',
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
                'fileCount': len(files),
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
