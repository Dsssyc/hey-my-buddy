"""Durable authority for finishing a revoked allocation's empty directory."""
import errno
import os

from ...paths import native_path
from . import storage, workspace


def record(store, directory, request_id):
    """The caller holds both the allocation and storage locks."""
    with store.db.write() as connection:
        connection.execute(
            'INSERT INTO allocation_revocations(allocation_id,request_id,revoked_at) VALUES(?,?,?)'
            ' ON CONFLICT(allocation_id) DO NOTHING',
            (directory.name, request_id, store.now()))


def clear(store, directory):
    """The storage lock serializes this with a removed directory's final row."""
    with store.db.write() as connection:
        connection.execute('DELETE FROM allocation_revocations WHERE allocation_id=?', (directory.name,))


def prepare(store, directory):
    """The creator holds and has checked its allocation lock."""
    # An older revoker published its row before unlinking its lock. Once this
    # lock is acquired, a missing row cannot appear from that older generation.
    with store.db.read() as connection:
        if connection.execute('SELECT 1 FROM allocation_revocations WHERE allocation_id=?',
                              (directory.name,)).fetchone() is None:
            return
    with storage.locked(store):
        clear(store, directory)


def finish(store, directory):
    """Unlink under the allocation lock, then do only rmdir and row deletion.

    A creator can install a new lock between unlink and rmdir. That new entry
    makes rmdir fail; no removal follows the failure. The storage lock spans
    rmdir and the row deletion, so a new generation cannot publish a revocation
    between them. It is released between allocations, never held by SQLite.
    """
    try:
        if os.name == 'nt':
            from ...windows_files import unlink_regular_file
            unlink_regular_file(directory / '.lock')
        else:
            native_path(directory / '.lock').unlink()
    except FileNotFoundError:
        return forget_removed(store, directory)
    with storage.locked(store):
        try:
            if os.name == 'nt':
                from ...windows_files import remove_directory
                remove_directory(directory)
            else:
                native_path(directory).rmdir()
        except FileNotFoundError:
            # Directory absence completes this physical step.
            pass
        except OSError as error:
            if error.errno in (errno.ENOTEMPTY, errno.EEXIST):
                return False
            raise
        clear(store, directory)
    return True


def forget_removed(store, directory):
    """Recover death after rmdir; a recreated directory keeps its old row."""
    if workspace._path_present(directory):
        return False
    with store.db.read() as connection:
        if connection.execute('SELECT 1 FROM allocation_revocations WHERE allocation_id=?',
                              (directory.name,)).fetchone() is None:
            return True
    with storage.locked(store):
        if not workspace._path_present(directory):
            clear(store, directory)
            return True
    return False
