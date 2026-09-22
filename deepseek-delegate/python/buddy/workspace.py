"""Pinned Git workspaces and immutable artifacts, independent of board state.

The caller owns admission, reservations and process-stop evidence. In particular,
``seal`` may only be called after the Worker has proved that its child stopped.
No operation here changes the source checkout's HEAD, index or working files.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess

from .errors import BoardError


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


@contextmanager
def _errors():
    try:
        yield
    except BoardError:
        raise
    except OSError as error:
        raise BoardError("WORKSPACE_IO_ERROR", str(error)) from error


def _git(root, *args, data=None, env=None, allowed=(0,)):
    # Do not inherit a caller's private index, repository, worktree or config
    # override. Read operations must not refresh the source index or run hooks.
    git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    git_env.update(GIT_OPTIONAL_LOCKS="0", GIT_LITERAL_PATHSPECS="1", LC_ALL="C")
    git_env.update(env or {})
    command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
               "-c", "core.untrackedCache=false", "-c", "core.splitIndex=false", "-C", str(root), *args]
    try:
        result = subprocess.run(command, input=data, capture_output=True, env=git_env, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BoardError("WORKSPACE_GIT_ERROR", str(error)) from error
    if result.returncode not in allowed:
        raise BoardError("WORKSPACE_GIT_ERROR", "Git workspace operation failed", operation=args[0],
                         reason=result.stderr.decode(errors="replace")[-2000:])
    return result.stdout


def _line(root, *args, **kwargs):
    return os.fsdecode(_git(root, *args, **kwargs).removesuffix(b"\n"))


def _identity(path):
    stat = path.stat()
    return _sha(_json([str(path), stat.st_dev, stat.st_ino]))


def _commit(root, ref):
    if not isinstance(ref, str) or not ref or "\0" in ref:
        raise BoardError("INVALID_WORKSPACE", "base.ref must be a nonempty Git revision")
    try:
        return _line(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
    except BoardError as error:
        raise BoardError("WORKSPACE_REF_INVALID", "The workspace revision does not resolve to a commit", ref=ref) from error


def inspect(cwd: str) -> dict:
    """Identify the actual checkout; sibling cwd paths have one checkoutId.

    Git documents the private git-dir/common-dir distinction for worktrees at
    https://git-scm.com/docs/git-worktree#_details . IDs also detect replacement
    of the same path by a different repository or worktree directory.
    """
    with _errors():
        if not isinstance(cwd, str) or not cwd or "\0" in cwd:
            raise BoardError("INVALID_WORKSPACE", "cwd must be a directory path")
        path = Path(cwd).resolve(strict=True)
        if not path.is_dir():
            raise BoardError("INVALID_WORKSPACE", "cwd must be a directory", cwd=str(path))
        try:
            root = Path(_line(path, "rev-parse", "--show-toplevel")).resolve(strict=True)
            git_dir = Path(_line(path, "rev-parse", "--absolute-git-dir")).resolve(strict=True)
            common = Path(_line(path, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve(strict=True)
        except BoardError as error:
            raise BoardError("WORKSPACE_UNSUPPORTED", "A non-bare Git checkout is required", cwd=str(path)) from error
        return {"path": str(path), "checkoutRoot": str(root), "checkoutId": _identity(git_dir),
                "repositoryId": _identity(common), "gitDir": str(git_dir), "repositoryPath": str(common),
                "headCommit": _commit(root, "HEAD")}


def _relative(value, *, allow_root=False):
    if not isinstance(value, str) or not value or "\0" in value:
        raise BoardError("INVALID_WORKSPACE", "Workspace paths must be relative strings")
    parts = PurePosixPath(value).parts
    if PurePosixPath(value).is_absolute() or any(part in ("..", ".git") or part.lower() == ".git" for part in parts):
        raise BoardError("INVALID_WORKSPACE", "Workspace paths cannot escape the checkout or address Git metadata", path=value)
    normalized = str(PurePosixPath(value))
    if normalized == "." and not allow_root:
        raise BoardError("INVALID_WORKSPACE", "An explicit file or directory path is required", path=value)
    return normalized


def _in_scope(path, scope):
    return any(item == "." or path == item or path.startswith(item + "/") for item in scope)
