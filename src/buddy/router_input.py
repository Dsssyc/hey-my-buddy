"""Materialize only immutable managed input; never give a Router the live checkout."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tarfile

from .errors import BoardError
from . import workspace


def digest(root: Path) -> str:
    value = hashlib.sha256()
    for directory, names, files in os.walk(root, followlinks=False):
        names.sort()
        for name in sorted(names + files):
            path = Path(directory) / name
            value.update(path.relative_to(root).as_posix().encode() + b"\0")
            if path.is_symlink():
                value.update(b"link\0" + os.readlink(path).encode())
            elif path.is_file():
                value.update(b"file\0")
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(65536), b''):
                        value.update(block)
            else:
                value.update(b"directory\0")
    return value.hexdigest()


def prepare(manifest: dict | None, directory: Path) -> tuple[Path, str]:
    if manifest is None:
        # Public standalone selection has no filesystem input. Freeze an empty
        # private directory rather than borrowing any caller checkout.
        root = directory / "frozen-input"
        root.mkdir(mode=0o700)
        return root, digest(root)
    if not isinstance(manifest, dict) or not manifest.get("inputTree"):
        raise BoardError("router-input-unavailable", "Routing requires a frozen Git input manifest")
    workspace.verify(manifest, require_unchanged=True)
    root = directory / "frozen-input"
    root.mkdir(mode=0o700)
    archive = directory / "frozen-input.tar"
    try:
        with archive.open('xb') as output:
            result = subprocess.run(["git", "-C", manifest["checkoutRoot"], "archive", "--format=tar", manifest["inputTree"]],
                                    stdout=output, stderr=subprocess.PIPE, timeout=30, check=False)
        if result.returncode:
            raise BoardError("router-input-unavailable", "The frozen input tree is unavailable")
        with tarfile.open(archive) as packed:
            # Reject escaping links and special files; do not follow live filesystem data.
            packed.extractall(root, filter="data")
        return root, digest(root)
    except (OSError, tarfile.TarError, subprocess.TimeoutExpired):
        raise BoardError("router-input-unavailable", "The frozen input cannot be safely materialized") from None
    finally:
        archive.unlink(missing_ok=True)


def verify(manifest: dict, root: Path, expected: str) -> dict:
    try:
        if manifest is not None:
            workspace.verify(manifest, require_unchanged=True)
        if digest(root) != expected:
            raise BoardError("router-input-changed", "Router snapshot changed")
    except (BoardError, OSError):
        return {"unchanged": False, "code": "router-input-changed", "manifestSha256": (manifest or {}).get("manifestSha256")}
    return {"unchanged": True, "manifestSha256": (manifest or {}).get("manifestSha256"), "snapshotSha256": expected}
