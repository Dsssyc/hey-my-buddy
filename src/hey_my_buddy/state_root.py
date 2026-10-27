"""Canonical state roots and the one cached privacy boundary per process."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import threading

from . import home, private_dirs
from .errors import BoardError

_checked_roots: dict[Path, Path] = {}
_check_lock = threading.Lock()


def get_state_dir(state_dir: str | Path | None = None) -> Path:
    """Resolve POSIX links; normalize Win32 dot segments without resolving links."""
    path = Path(state_dir or os.environ.get("HMB_STATE_DIR") or home.default_state_dir()).expanduser()
    try:
        return Path(os.path.abspath(path)) if os.name == "nt" else path.resolve()
    except (OSError, RuntimeError) as error:
        raise BoardError('PRIVATE_PATH_UNSAFE', 'State root cannot be resolved', path=str(path)) from error


def _private_primary_group(gid: int) -> bool:
    """Only this user's same-named primary group can grant ancestor write access."""
    import grp
    import pwd

    uid = os.geteuid()
    try:
        account = pwd.getpwuid(uid)
        if account.pw_gid != gid:
            return False
        group = grp.getgrgid(gid)
    except KeyError:
        return False
    return (
        group.gr_name == account.pw_name
        and all(member == account.pw_name for member in group.gr_mem)
        and not any(other.pw_gid == gid and other.pw_uid != uid for other in pwd.getpwall())
    )


def _trusted_ancestor(info) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return info.st_uid in (os.geteuid(), 0)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (os.geteuid(), 0):
        return False
    return bool(info.st_mode & stat.S_ISVTX) or not (
        info.st_mode & 0o002 or info.st_mode & 0o020 and not _private_primary_group(info.st_gid))


def _trusted_ancestors(directory: Path, *, creating: bool = False) -> bool:
    """Check existing real ancestors before creating any missing private parents."""
    missing = []
    try:
        current = directory.parent
        while True:
            try:
                info = os.lstat(current)
            except FileNotFoundError:
                if not creating:
                    return False
                missing.append(current)
            else:
                if not _trusted_ancestor(info):
                    return False
            if current.parent == current:
                break
            current = current.parent
        for parent in reversed(missing):
            try:
                parent.mkdir(mode=0o700)
            except FileExistsError:
                pass
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or not _trusted_ancestor(info):
                return False
        return True
    except OSError:
        return False


def _check_posix(directory: Path, *, create: bool) -> None:
    if not _trusted_ancestors(directory, creating=create):
        raise BoardError('PRIVATE_PATH_UNSAFE', 'State root has an untrusted ancestor', path=str(directory))
    if create:
        private_dirs.ensure_private_dir(directory)
    info = directory.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise BoardError('PRIVATE_PATH_UNSAFE', 'State root must be a directory owned by this account', path=str(directory))
    if info.st_mode & 0o077 and not create:
        raise BoardError('PRIVATE_PATH_UNSAFE', 'State root must be owner-private', path=str(directory))
    if info.st_mode & 0o077:
        directory.chmod(0o700)


def check_state_root(directory: Path, *, create: bool = False) -> Path:
    """Check a root once; only owner entry points may create or repair it.

    Failed read-only checks are not cached: an owner can subsequently create or
    repair that root. A successful check is shared by all callers in this process.
    """
    selected = Path(directory).expanduser().absolute()
    with _check_lock:
        if selected in _checked_roots:
            return _checked_roots[selected]
        root = get_state_dir(selected)
        if root not in _checked_roots:
            try:
                if os.name == 'nt':
                    from .windows_files import check_state_root as check_windows_root
                    check_windows_root(root, create=create)
                else:
                    _check_posix(root, create=create)
            except OSError as error:
                raise BoardError('PRIVATE_PATH_UNSAFE', 'State root cannot be opened privately', path=str(root)) from error
            _checked_roots[root] = root
        _checked_roots[selected] = root
        return root
