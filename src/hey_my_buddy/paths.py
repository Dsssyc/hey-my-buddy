"""Path rules for files on the machine running this process."""
from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath

from .errors import BoardError


def is_absolute_path(value: str | os.PathLike[str]) -> bool:
    """Use the local filesystem syntax, including a drive and root on Windows."""
    return Path(value).is_absolute()


def windows_path(value: str | os.PathLike[str], *, extended: bool = True) -> str:
    """Spell a checked local drive path for Windows file APIs, never for cwd/Git.

    Extended paths bypass Win32 name normalization, so reject traversal and
    device names before adding the prefix. Existing prefixes are idempotent.
    """
    text = os.fspath(value)
    if text.startswith('\\\\?\\'):
        text = text[4:]
    path = PureWindowsPath(text)
    if (len(path.drive) != 2 or path.drive[0] not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
            or path.drive[1] != ':' or path.root != '\\' or '\0' in text
            or '..' in path.parts or any(PureWindowsPath(part).is_reserved() for part in path.parts[1:])):
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Windows file path must be an absolute local drive path without traversal or devices', path=os.fspath(value))
    return ('\\\\?\\' if extended else '') + str(path)


def native_path(path: Path) -> Path:
    """Convert only at a filesystem call; keep the caller's path as evidence."""
    return Path(windows_path(path)) if os.name == 'nt' else Path(path)


def resolved_path(path: Path, *, strict: bool = False) -> Path:
    """Resolve through the file API, returning the ordinary evidence spelling."""
    resolved = native_path(path).resolve(strict=strict)
    return Path(windows_path(resolved, extended=False)) if os.name == 'nt' else resolved
