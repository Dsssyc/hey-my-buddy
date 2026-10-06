"""Paths and guarded filesystem operations for harness-owned private state."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

from .errors import BoardError

ADAPTERS = frozenset({'codex', 'claude', 'zcode', 'dsh', 'command'})
_ATTEMPT_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
_REPARSE_POINT = 0x400
_CREDENTIAL_FILES = frozenset({
    'agent-credential.json', 'inquiry.json', 'finish-bridge.json',
    'zcode-control.json', 'builtin-provider.json', 'personal-provider.json',
    'role-run-control.json', 'role-run-request.json', 'role-run-verdict.json',
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
            or bool(getattr(metadata, 'st_file_attributes', 0) & _REPARSE_POINT)
            or path.is_junction())


def _absolute(path: Path) -> Path:
    path = Path(path).absolute()
    if not path.is_absolute():
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Private path must be absolute')
    # macOS spells /private/var and /private/tmp through root-level aliases.
    # Canonicalize only these OS-owned aliases; resolving the whole path would hide a hostile link
    # inside the Buddy state tree before the component guard can inspect it.
    if sys.platform == 'darwin' and len(path.parts) > 1 and path.parts[1] in ('var', 'tmp'):
        path = Path('/private', *path.parts[1:])
    return path


def linked_component(path: Path) -> Path | None:
    """Check ancestors before inspecting any entry below them."""
    path = _absolute(path)
    for component in (*reversed(path.parents), path):
        if linked(component):
            return component
    return None


def _guard_parents(path: Path) -> None:
    """Refuse links and reparse points anywhere on a filesystem operation path."""
    component = linked_component(path)
    if component is not None:
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Private path contains a linked component', path=str(component))


def open_regular_fd(path: Path, flags: int, mode: int = 0o600) -> int:
    """Open a guarded ordinary file, pinning real ancestors on POSIX."""
    path = _absolute(path)
    _guard_parents(path)
    if '..' in path.parts or flags & os.O_TRUNC and not flags & os.O_EXCL:
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Unsafe ordinary-file open', path=str(path))
    fd = None
    parent_fd = None
    try:
        flags |= getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        if os.open in os.supports_dir_fd and os.name != 'nt':
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            parent_fd = os.open(path.anchor, directory_flags)
            for name in path.parts[1:-1]:
                next_fd = os.open(name, directory_flags, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = next_fd
            fd = os.open(path.name, flags, mode, dir_fd=parent_fd)
        else:
            fd = os.open(path, flags, mode)
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode)
                or getattr(metadata, 'st_file_attributes', 0) & _REPARSE_POINT):
            raise BoardError('PRIVATE_PATH_UNSAFE', 'Path is not an ordinary file', path=str(path))
        _guard_parents(path)
        return fd
    except BaseException:
        if fd is not None:
            os.close(fd)
        raise
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


def _adapter(adapter: str) -> str:
    if adapter not in ADAPTERS:
        raise BoardError('INVALID_ARGUMENT', 'Unknown private harness adapter', adapter=adapter)
    return adapter


def goal_root(state: Path, adapter: str, task_id: str) -> Path:
    if not isinstance(task_id, str) or not task_id or '\0' in task_id:
        raise BoardError('INVALID_ARGUMENT', 'Private goal requires a task identity')
    return _absolute(state) / 'harnesses' / _adapter(adapter) / 'goals' / hashlib.sha256(task_id.encode()).hexdigest()


def native_root(state: Path, adapter: str, task_id: str) -> Path:
    return goal_root(state, adapter, task_id) / 'native'


def attempt_root(state: Path, adapter: str, task_id: str, attempt_id: str) -> Path:
    if not isinstance(attempt_id, str) or _ATTEMPT_ID.fullmatch(attempt_id) is None or attempt_id in ('.', '..'):
        raise BoardError('INVALID_ARGUMENT', 'Private attempt requires a safe attempt identity')
    return goal_root(state, adapter, task_id) / 'attempts' / attempt_id


def account_root(state: Path, adapter: str) -> Path:
    """Reserved path for later Worker account support; this does not create it."""
    return _absolute(state) / 'harnesses' / _adapter(adapter) / 'accounts' / 'worker'


def context_root(context, adapter: str | None = None) -> Path:
    """Resolve an execution's private root from its explicit service state root."""
    environment = getattr(context, 'environment', None)
    state = environment.get('BUDDY_STATE_DIR') if isinstance(environment, dict) else None
    if not state or not Path(state).is_absolute():
        raise BoardError('PRIVATE_STATE_REQUIRED', 'BUDDY_STATE_DIR must explicitly name the private state root')
    selected = adapter or context.spec.get('adapter')
    return attempt_root(Path(state), selected, context.task_id, context.attempt_id)


def ensure_private_dir(path: Path) -> Path:
    path = _absolute(path)
    _guard_parents(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    _guard_parents(path)
    if not path.is_dir():
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Private path is not a directory', path=str(path))
    if os.name != 'nt':
        path.chmod(0o700)
    return path


def remove_tree(path: Path) -> bool:
    """Remove one real tree without traversing links or reparse points."""
    path = _absolute(path)
    _guard_parents(path.parent)
    if not path.exists() and not linked(path):
        return False
    if linked(path):
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Refusing to remove a linked private root', path=str(path))
    if not path.is_dir():
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Private removal target is not a directory', path=str(path))
    def descend(directory: Path) -> None:
        _guard_parents(directory)
        with os.scandir(directory) as entries:
            children = [Path(entry.path) for entry in entries]
        for child in children:
            if linked(child):
                if child.is_junction():
                    child.rmdir()
                else:
                    child.unlink()
            elif child.is_dir():
                descend(child)
            else:
                child.unlink()
        directory.rmdir()

    descend(path)
    return True


def inquiry_fallback(path: Path, attempt_id: str) -> Path | None:
    """Resolve only the fixed short-socket fallback from a private inquiry file."""
    path = Path(path)
    if linked(path) or not path.is_file():
        return None
    try:
        if path.stat().st_size > 8192:
            return None
        value = json.loads(path.read_text())
        socket = value.get('socketPath') if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None
    if not isinstance(socket, str):
        return None
    socket_path = Path(socket)
    if socket_path.name != 'inquiry.sock' or not socket_path.is_absolute():
        return None
    fallback = socket_path.parent
    allowed = {Path('/tmp').resolve() / 'hey-my-buddy-inquiry' / attempt_id,
               Path(tempfile.gettempdir()).resolve() / 'hey-my-buddy-inquiry' / attempt_id}
    return fallback if fallback.resolve(strict=False) in allowed and not linked(fallback) else None


def cleanup_inquiry_fallback(path: Path, attempt_id: str) -> Path | None:
    fallback = inquiry_fallback(path, attempt_id)
    if fallback is not None and remove_tree(fallback):
        return fallback
    return None


def cleanup_attempt_credentials(state: Path, adapter: str, task_id: str, attempt_id: str) -> dict:
    """Remove credentials after the caller has independently proved native stop."""
    root = attempt_root(state, adapter, task_id, attempt_id)
    _guard_parents(root)
    if not root.exists():
        return {'count': 0, 'paths': []}
    if not root.is_dir():
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Private attempt root is not a directory', path=str(root))
    removed = []
    inquiry = root / 'inquiry.json'
    fallback = cleanup_inquiry_fallback(inquiry, attempt_id)
    if fallback is not None:
        removed.append(str(fallback))
    def inspect(directory: Path) -> None:
        _guard_parents(directory)
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
