"""Paths and guarded filesystem operations for harness-owned private state."""
from __future__ import annotations

import errno
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import secrets

from .paths import is_absolute_path
from .protocol.harness_names import ADAPTER_NAMES as ADAPTERS
from .errors import BoardError

_ATTEMPT_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
_REPARSE_POINT = 0x400
_CREDENTIAL_FILES = frozenset({
    'agent-credential.json', 'execution-start.json',
    'inquiry.json', 'finish-bridge.json',
    'zcode-control.json', 'builtin-provider.json', 'personal-provider.json',
    'auth.json', 'patch.json', 'settings.json', 'inquiry.sock',
})


def linked(path: Path) -> bool:
    """Inspect the directory entry itself, including Windows reparse points."""
    path = Path(path)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return (stat.S_ISLNK(metadata.st_mode)
            or bool(getattr(metadata, 'st_file_attributes', 0) & _REPARSE_POINT))


def _absolute(path: Path) -> Path:
    path = Path(path).absolute()
    if not is_absolute_path(path):
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Private path must be absolute')
    if '..' in path.parts:
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Private path contains a parent traversal', path=str(path))
    return path


def open_regular_fd(path: Path, flags: int, mode: int = 0o600) -> int:
    """Open an ordinary leaf without following a link or reparse point."""
    path = _absolute(path)
    if flags & os.O_TRUNC and not flags & os.O_EXCL:
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Unsafe ordinary-file open', path=str(path))
    if os.name == 'nt':
        from .windows_files import open_regular_fd as windows_open
        return windows_open(path, flags, mode)
    fd = None
    try:
        fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, mode)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise BoardError('PRIVATE_PATH_UNSAFE', 'Path is not an ordinary file', path=str(path))
        return fd
    except BaseException as error:
        if fd is not None:
            os.close(fd)
        if isinstance(error, OSError) and error.errno in (errno.ELOOP, errno.EISDIR):
            raise BoardError('PRIVATE_PATH_UNSAFE', 'Path is not an ordinary file', path=str(path)) from error
        raise


def _adapter(adapter: str) -> str:
    if adapter not in ADAPTERS:
        raise BoardError('INVALID_ARGUMENT', 'Unknown adapter for private state', adapter=adapter)
    return adapter


def micro_task_root(state: Path, adapter: str, task_id: str) -> Path:
    if not isinstance(task_id, str) or not task_id or '\0' in task_id:
        raise BoardError('INVALID_ARGUMENT', 'Private micro task requires a task identity')
    return _absolute(state) / 'harnesses' / _adapter(adapter) / 'micro-tasks' / hashlib.sha256(task_id.encode()).hexdigest()


def native_root(state: Path, adapter: str, task_id: str) -> Path:
    return micro_task_root(state, adapter, task_id) / 'native'


def attempt_root(state: Path, adapter: str, task_id: str, attempt_id: str) -> Path:
    if not isinstance(attempt_id, str) or _ATTEMPT_ID.fullmatch(attempt_id) is None or attempt_id in ('.', '..'):
        raise BoardError('INVALID_ARGUMENT', 'Private attempt requires a safe attempt identity')
    return micro_task_root(state, adapter, task_id) / 'attempts' / attempt_id


def session_group_root(state: Path, group_id: str) -> Path:
    """The session directory of the console's authoritative task group."""
    independent = group_id.startswith('run:')
    identity = group_id[4:] if independent else group_id
    if _ATTEMPT_ID.fullmatch(identity) is None or identity in ('.', '..'):
        raise BoardError('INVALID_ARGUMENT', 'Session records require a safe group identity')
    return _absolute(state) / 'sessions' / ('micro-tasks' if independent else 'macro-tasks') / identity


def session_record_path(state: Path, group_id: str, attempt_id: str) -> Path:
    """One execution's display record, shared by the service and its reader."""
    if not isinstance(attempt_id, str) or _ATTEMPT_ID.fullmatch(attempt_id) is None or attempt_id in ('.', '..'):
        raise BoardError('INVALID_ARGUMENT', 'Session records require a safe attempt identity')
    return session_group_root(state, group_id) / (attempt_id + '.jsonl')


def context_root(context, adapter: str | None = None) -> Path:
    """Resolve an execution's private root from its explicit service state root."""
    state = context.environment['HMB_STATE_DIR']
    selected = adapter or context.spec.get('adapter')
    return attempt_root(Path(state), selected, context.task_id, context.attempt_id)


def directory_mode() -> int:
    """Inherit Windows ACLs; restrict newly created POSIX directories."""
    return 0o777 if os.name == 'nt' else 0o700


def ensure_private_dir(path: Path) -> Path:
    path = _absolute(path)
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=directory_mode())
        except FileExistsError:
            pass
    if not path.is_dir():
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Private path is not a directory', path=str(path))
    return path


def make_temporary_dir(*, prefix: str, directory: Path) -> Path:
    """Keep Windows temporary directories inheriting their parent's ACL too."""
    if os.name != 'nt':
        return Path(tempfile.mkdtemp(prefix=prefix, dir=directory))
    path = Path(directory) / (prefix + secrets.token_urlsafe(6))
    path.mkdir()
    return path


def remove_tree(path: Path, *, keep: frozenset[Path] = frozenset()) -> bool:
    """Remove one real tree, retaining exact files or subtrees and their parents."""
    path = _absolute(path)
    keep = frozenset(_absolute(item) for item in keep)
    if not path.exists() and not linked(path):
        return False
    if linked(path):
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Refusing to remove a linked private root', path=str(path))
    if not path.is_dir():
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Private removal target is not a directory', path=str(path))
    def descend(directory: Path) -> bool:
        with os.scandir(directory) as entries:
            children = [Path(entry.path) for entry in entries]
        retained = False
        for child in children:
            if linked(child):
                if child.is_junction():
                    child.rmdir()
                else:
                    child.unlink()
            elif child in keep:
                retained = True
            elif child.is_dir():
                retained = not descend(child) or retained
            else:
                child.unlink()
        if not retained:
            directory.rmdir()
        return not retained

    return descend(path)


def cleanup_attempt_credentials(state: Path, adapter: str, task_id: str, attempt_id: str) -> dict:
    """Remove credentials after the caller has independently proved native stop."""
    root = attempt_root(state, adapter, task_id, attempt_id)
    if linked(root):
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Private attempt root is linked', path=str(root))
    if not root.exists():
        return {'count': 0, 'paths': []}
    if not root.is_dir():
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Private attempt root is not a directory', path=str(root))
    removed = []
    def inspect(directory: Path) -> None:
        with os.scandir(directory) as entries:
            children = [Path(entry.path) for entry in entries]
        for child in children:
            if linked(child):
                if child.name in _CREDENTIAL_FILES:
                    if child.is_junction():
                        child.rmdir()
                    else:
                        child.unlink()
                    removed.append(str(child))
            elif child.is_dir():
                if child.name in _CREDENTIAL_FILES:
                    raise BoardError('PRIVATE_PATH_UNSAFE', 'Credential path is a directory', path=str(child))
                inspect(child)
            elif child.name in _CREDENTIAL_FILES:
                child.unlink()
                removed.append(str(child))

    inspect(root)
    return {'count': len(removed), 'paths': removed[:20]}


def validate_private_fd(fd: int) -> None:
    """Validate an already opened file without reopening its path."""
    if os.name == 'nt':
        import msvcrt
        from .windows_files import WindowsFileAPI
        api = WindowsFileAPI()
        handle = msvcrt.get_osfhandle(fd)
        attributes, _ = api.info(handle)
        if attributes & 0x10 or api.file_type(handle) != 1:
            raise BoardError('PRIVATE_PATH_UNSAFE', 'Private descriptor is not an ordinary file')
    else:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise BoardError('PRIVATE_PATH_UNSAFE', 'File must be owner-private')


def validate_private_dir(path: Path) -> None:
    path = _absolute(path)
    if os.name != 'nt':
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise BoardError('PRIVATE_PATH_UNSAFE', 'Directory must be owner-private', path=str(path))
