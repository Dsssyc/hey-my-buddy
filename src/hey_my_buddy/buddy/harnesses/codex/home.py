"""Goal-private Codex state with an explicitly selected native credential source."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import os
from pathlib import Path
import stat

from ....errors import BoardError
from ....private_dirs import _absolute, ensure_private_dir, linked_component


@contextmanager
def _pinned_home(home: Path):
    """Pin every ancestor; a replaced path cannot redirect credential mutation."""
    home = _absolute(home)
    if (os.open not in os.supports_dir_fd or os.symlink not in os.supports_dir_fd
            or os.unlink not in os.supports_dir_fd or os.stat not in os.supports_dir_fd):
        raise BoardError('CODEX_PRIVATE_HOME_UNSUPPORTED', 'This platform cannot pin private credential operations')
    if linked_component(home) is not None:
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Codex home contains a linked component')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(home.anchor, flags)
    try:
        for name in home.parts[1:]:
            next_fd = os.open(name, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
        identity, actual = os.fstat(fd), home.lstat()
        if linked_component(home) is not None or (identity.st_dev, identity.st_ino) != (actual.st_dev, actual.st_ino):
            raise BoardError('PRIVATE_PATH_UNSAFE', 'Codex home changed during credential cleanup')
    finally:
        os.close(fd)


def prepare_coding_home(root: Path, source: dict) -> Path:
    """Create state locally and link login only inside the private goal root."""
    home = ensure_private_dir(root / 'codex-home')
    auth = home / 'auth.json'
    source_auth = Path(source['home']) / 'auth.json'
    try:
        info = source_auth.stat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if not stat.S_ISREG(info.st_mode):
            raise BoardError('CODEX_ACCOUNT_UNAVAILABLE', 'The selected native credential is not an ordinary file')
    with _pinned_home(home) as fd:
        try:
            os.stat(auth.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            # A retained credential can belong to an unsettled attempt.
            raise BoardError('CODEX_CREDENTIAL_RETAINED', 'The private Codex home still has an unsettled credential')
        if info is not None:
            os.symlink(str(source_auth), auth.name, dir_fd=fd)
    return home


def remove_coding_auth(root: Path) -> int:
    """Caller must prove native and controller shutdown before invoking this."""
    home = root / 'codex-home'
    if linked_component(home) is not None:
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Codex home contains a linked component')
    if not home.exists():
        return 0
    with _pinned_home(home) as fd:
        try:
            metadata = os.stat('auth.json', dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return 0
        if (not (stat.S_ISLNK(metadata.st_mode) or stat.S_ISREG(metadata.st_mode))
                or getattr(metadata, 'st_file_attributes', 0) & 0x400):
            raise BoardError('PRIVATE_PATH_UNSAFE', 'Codex credential is not a file or credential link')
        os.unlink('auth.json', dir_fd=fd)
    return 1


def credential_source(environment: dict) -> dict:
    """Identify the harness's native login location without reading credentials."""
    home = Path(environment.get('CODEX_HOME') or Path(environment.get('HOME') or Path.home()) / '.codex').expanduser().absolute()
    return {'home': str(home), 'source': 'native', 'credentialRevision': 0,
            'identity': hashlib.sha256(str(home).encode()).hexdigest()}
