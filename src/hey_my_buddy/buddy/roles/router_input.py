"""Materialize only immutable managed input; never give a Router the live checkout."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from ...errors import BoardError
from ...blackboard.tasks import workspace
from ...private_dirs import directory_mode, remove_tree
from ...paths import native_path
from ...private_files import open_read


def _git_error(code, message, command, *, stderr=None, returncode=None):
    skipped = False
    if hasattr(stderr, "read"):
        stderr.seek(0, os.SEEK_END)
        size = stderr.tell()
        start = max(0, size - 4 * workspace._GIT_DIAGNOSTIC_LIMIT)
        stderr.seek(start)
        stderr = stderr.read(4 * workspace._GIT_DIAGNOSTIC_LIMIT)
        skipped = start > 0
    diagnostic, truncated = workspace._git_diagnostic(stderr)
    return BoardError(code, message, argv=list(command), returncode=returncode,
                      stderr=diagnostic, stderrTruncated=skipped or truncated)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BoardError("router-input-unknown", "The frozen input read did not finish within its wait")
    return remaining


def digest(root: Path, *, deadline: float) -> str:
    return _digest(root, deadline)


def _digest(root: Path, deadline: float) -> str:
    value = hashlib.sha256()
    filesystem_root = native_path(root)
    for directory, names, files in os.walk(filesystem_root, followlinks=False):
        _remaining(deadline)
        names.sort()
        for name in sorted(names + files):
            path = root / Path(directory).relative_to(filesystem_root) / name
            value.update(path.relative_to(root).as_posix().encode() + b"\0")
            value.update(str(native_path(path).lstat().st_mode & 0o777).encode() + b"\0")
            if native_path(path).is_symlink():
                value.update(b"link\0" + os.readlink(native_path(path)).encode())
            elif native_path(path).is_file():
                value.update(b"file\0")
                with open_read(path) as stream:
                    for block in iter(lambda: stream.read(65536), b''):
                        _remaining(deadline)
                        value.update(block)
            else:
                value.update(b"directory\0")
    _remaining(deadline)
    return value.hexdigest()


def prepare(manifest: dict | None, directory: Path, *, state_dir: Path, deadline: float) -> tuple[Path, str]:
    _remaining(deadline)
    if manifest is None:
        # Public standalone selection has no filesystem input. Freeze an empty
        # private directory rather than borrowing any caller checkout.
        root = directory / "frozen-input"
        native_path(root).mkdir(mode=directory_mode())
        try:
            return root, _digest(root, deadline)
        except BoardError:
            discard(root)
            raise
    if not isinstance(manifest, dict) or not manifest.get("inputTree"):
        raise BoardError("router-input-unavailable", "Routing requires a frozen Git input manifest")
    try:
        workspace.verify(manifest, state_dir=state_dir, require_unchanged=True)
    except BoardError as error:
        code = ("router-input-unknown" if error.code == "WORKSPACE_GIT_UNKNOWN" else
                "router-input-changed" if error.code in {"WORKSPACE_CHANGED", "WORKSPACE_MANIFEST_CHANGED", "WORKSPACE_CONFLICT"}
                else "router-input-unavailable")
        raise BoardError(code, "The frozen Router input could not be verified") from None
    _remaining(deadline)
    root = directory / "frozen-input"
    native_path(root).mkdir(mode=directory_mode())
    def git(*arguments):
        command, environment = workspace._git_command(manifest["checkoutRoot"], *arguments)
        try:
            result = subprocess.run(command, capture_output=True, env=environment,
                                    timeout=_remaining(deadline), check=False)
        except subprocess.TimeoutExpired as error:
            raise _git_error("router-input-unknown", "The frozen Git object read timed out", command,
                             stderr=error.stderr) from error
        if result.returncode:
            raise _git_error("router-input-unavailable", "A frozen Git object is unavailable", command,
                             stderr=result.stderr, returncode=result.returncode)
        return result.stdout
    reader = None
    pool = None
    diagnostics = None
    try:
        # Git archive/checkout can apply export-ignore, export-subst or smudge
        # filters. Read raw blobs so Router bytes equal the immutable input tree,
        # through one `cat-file --batch` process rather than one process per file.
        entries = git("ls-tree", "-rz", "--full-tree", manifest["inputTree"])
        _remaining(deadline)
        command, environment = workspace._git_command(manifest["checkoutRoot"], "cat-file", "--batch")
        diagnostics = tempfile.TemporaryFile(dir=native_path(directory))
        reader = subprocess.Popen(command,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=diagnostics,
                                  env=environment)
        pool = ThreadPoolExecutor(max_workers=1)

        def read_blob(object_id: str) -> bytes:
            reader.stdin.write(object_id.encode("ascii") + b"\n")
            reader.stdin.flush()
            header = reader.stdout.readline().split()
            if len(header) != 3 or header[0].decode("ascii") != object_id or header[1] != b"blob":
                raise _git_error("router-input-unavailable", "A frozen Git object is unavailable", command,
                                 stderr=diagnostics, returncode=reader.poll())
            size = int(header[2])
            contents = reader.stdout.read(size)
            if len(contents) != size or reader.stdout.read(1) != b"\n":
                raise _git_error("router-input-unavailable", "A frozen Git object is truncated", command,
                                 stderr=diagnostics, returncode=reader.poll())
            return contents

        def blob(object_id: str) -> bytes:
            remaining = _remaining(deadline)
            future = pool.submit(read_blob, object_id)
            try:
                return future.result(timeout=remaining)
            except FutureTimeout as error:
                reader.kill()
                raise _git_error("router-input-unknown", "The frozen Git object read timed out", command,
                                 stderr=diagnostics, returncode=reader.poll()) from error
            except (OSError, ValueError) as error:
                raise _git_error("router-input-unavailable", "A frozen Git object is unavailable", command,
                                 stderr=diagnostics, returncode=reader.poll()) from error

        links = []
        for entry in entries.split(b"\0"):
            _remaining(deadline)
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            mode, kind, object_id = metadata.decode('ascii').split()
            try:
                relative = PurePosixPath(workspace._relative(os.fsdecode(raw_path)))
            except BoardError as error:
                raise BoardError("router-input-unavailable", "A frozen tree path is unsafe: absolute paths, parent traversal and Git metadata are forbidden") from error
            if kind != "blob":
                raise BoardError("router-input-unavailable", "Frozen Router input with submodules is not supported")
            path = root.joinpath(*relative.parts)
            native_path(path.parent).mkdir(parents=True, exist_ok=True)
            contents = blob(object_id)
            if mode == "120000":
                native_path(path).symlink_to(os.fsdecode(contents))
                links.append(path)
            else:
                native_path(path).write_bytes(contents)
                if os.name != 'nt':
                    path.chmod(0o555 if mode == "100755" else 0o444)
        for link in links:
            _remaining(deadline)
            try:
                resolved = native_path(link).resolve(strict=True)
            except FileNotFoundError:
                resolved = native_path(link).resolve(strict=False)
            if not resolved.is_relative_to(native_path(root).resolve()):
                raise BoardError("router-input-unavailable", "A frozen symlink escapes the Router checkout")
        result = root, _digest(root, deadline)
        _remaining(deadline)
        return result
    except BoardError:
        discard(root)
        raise
    except (OSError, ValueError, RuntimeError):
        discard(root)
        raise BoardError("router-input-unavailable", "The frozen input cannot be safely materialized") from None
    finally:
        if reader is not None:
            try:
                reader.stdin.close()
            except OSError:
                pass
            try:
                reader.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                reader.kill()
                reader.wait()
            finally:
                if pool is not None:
                    pool.shutdown(wait=True)
                reader.stdout.close()
        if diagnostics is not None:
            diagnostics.close()


def discard(root: Path) -> None:
    """Remove a Router mirror; the manifest and digests remain the durable evidence."""
    remove_tree(root)


def verify(manifest: dict | None, root: Path, expected: str, *, state_dir: Path, deadline: float) -> dict:
    try:
        _remaining(deadline)
        if manifest is not None:
            workspace.verify(manifest, state_dir=state_dir, require_unchanged=True)
        _remaining(deadline)
        if _digest(root, deadline) != expected:
            raise BoardError("router-input-changed", "Router snapshot changed")
        _remaining(deadline)
    except BoardError as error:
        if error.code in {"WORKSPACE_GIT_UNKNOWN", "router-input-unknown"}:
            return {"unchanged": None, "code": "router-input-unknown", "manifestSha256": (manifest or {}).get("manifestSha256")}
        return {"unchanged": False, "code": "router-input-changed", "manifestSha256": (manifest or {}).get("manifestSha256")}
    except OSError:
        return {"unchanged": False, "code": "router-input-changed", "manifestSha256": (manifest or {}).get("manifestSha256")}
    return {"unchanged": True, "manifestSha256": (manifest or {}).get("manifestSha256"), "snapshotSha256": expected}
