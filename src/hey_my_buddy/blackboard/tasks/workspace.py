"""Pinned Git workspaces and immutable artifacts, independent of board state.

The caller owns admission, reservations and process-stop evidence. In particular,
``seal`` may only be called after the Worker has proved that its child stopped.
No operation here changes the source checkout's HEAD, index or working files.
Stability and scope checks cover Git-managed paths and explicit untracked inputs;
ignored environments and caches are excluded unless selected. This is not an OS
sandbox or a claim that every physical file remains unchanged.
"""
from contextlib import ExitStack, contextmanager
from ...paths import is_absolute_path, native_path
from ... import locking
from ... import private_dirs, private_files
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile

from ...private_dirs import directory_mode
from ...errors import BoardError
from ...protocol.schemas import normalize_access
from ...protocol.service_process import service_creation_flags
from . import windows_paths
from . import workspace_identity

_WINDOWS = os.name == "nt"
_GIT_TIMEOUT_SECONDS = 60


_EXCLUSION_POLICY = {"version": 1, "kind": "git-standard",
                     "managedPaths": "tracked-and-nonignored-untracked", "selectedIgnored": "includeUntracked"}


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
        raise BoardError("WORKSPACE_IO_ERROR", str(error), preparationCleanup=getattr(error, "preparation_cleanup", None)) from error
    except (KeyError, TypeError, ValueError) as error:
        raise BoardError("INVALID_WORKSPACE", "Malformed workspace argument or recovery record") from error


def _git_command(root, *args, repository_only=False):
    # Do not inherit a caller's private index, repository, worktree or config
    # override. Read operations must not refresh the source index or run hooks.
    git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    git_env.update(GIT_OPTIONAL_LOCKS="0", GIT_LITERAL_PATHSPECS="1", GIT_NO_REPLACE_OBJECTS="1",
                   GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
               "-c", "core.untrackedCache=false", "-c", "core.splitIndex=false",
               "-c", "core.longpaths=true", "--git-dir" if repository_only else "-C", str(root), *args]
    return command, git_env


_GIT_DIAGNOSTIC_LIMIT = 2000


def _git_diagnostic(value):
    """Keep the diagnostic tail, including whether its prefix was omitted."""
    skipped = False
    if hasattr(value, "read"):
        # Fast-import already captures into files. Do not load an unbounded
        # file to return a bounded error; four bytes cover one UTF-8 character.
        value.seek(0, os.SEEK_END)
        size = value.tell()
        start = max(0, size - 4 * _GIT_DIAGNOSTIC_LIMIT)
        value.seek(start)
        value = value.read()
        skipped = start > 0
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    text = str(value) if value is not None else ""
    return text[-_GIT_DIAGNOSTIC_LIMIT:], skipped or len(text) > _GIT_DIAGNOSTIC_LIMIT


def _git_failure(command, operation, *, stdout=None, stderr=None, returncode=None,
                 error=None, message="Git workspace operation failed", **details):
    output, output_truncated = _git_diagnostic(stdout)
    diagnostic, diagnostic_truncated = _git_diagnostic(stderr)
    reason, reason_truncated = _git_diagnostic(diagnostic or error or message)
    return BoardError("WORKSPACE_GIT_ERROR", message, argv=list(command), operation=operation,
                      returncode=returncode, stdout=output, stderr=diagnostic,
                      stdoutTruncated=output_truncated, stderrTruncated=diagnostic_truncated,
                      reason=reason, reasonTruncated=diagnostic_truncated or reason_truncated, **details)


def _git_unknown(command, operation, error, *, stdout=None, stderr=None, returncode=None):
    failure = _git_failure(command, operation, stdout=stdout, stderr=stderr,
                           returncode=returncode, error=error)
    return BoardError("WORKSPACE_GIT_UNKNOWN", "Git did not finish within the workspace wait limit",
                      **failure.details)


def _git(root, *args, data=None, env=None, allowed=(0,)):
    command, git_env = _git_command(root, *args)
    git_env.update(env or {})
    try:
        result = subprocess.run(command, input=data, capture_output=True, env=git_env,
                                timeout=_GIT_TIMEOUT_SECONDS, creationflags=service_creation_flags())
    except subprocess.TimeoutExpired as error:
        raise _git_unknown(command, args[0], error, stdout=error.stdout, stderr=error.stderr) from error
    except OSError as error:
        raise _git_failure(command, args[0], stdout=getattr(error, "stdout", None),
                           stderr=getattr(error, "stderr", None), error=error) from error
    if result.returncode not in allowed:
        raise _git_failure(command, args[0], stdout=result.stdout, stderr=result.stderr,
                           returncode=result.returncode)
    return result.stdout


def _line(root, *args, **kwargs):
    return os.fsdecode(_git(root, *args, **kwargs).removesuffix(b"\n"))


def _git_in(git_dir, *args, allowed=(0,)):
    """Run one repository-only Git command against an explicit Git directory.

    ``-C`` into a Git directory makes Git treat the checkout as bare, which
    refuses worktree-requiring commands like ``stash list``. ``--git-dir``
    names the repository exactly; commands that read history and reflogs
    (``rev-parse``, ``reflog show``) need no work tree at all.
    """
    command, git_env = _git_command(git_dir, *args, repository_only=True)
    try:
        result = subprocess.run(command, input=None, capture_output=True, env=git_env,
                                timeout=_GIT_TIMEOUT_SECONDS, creationflags=service_creation_flags())
    except subprocess.TimeoutExpired as error:
        raise _git_unknown(command, args[0], error, stdout=error.stdout, stderr=error.stderr) from error
    except OSError as error:
        raise _git_failure(command, args[0], stdout=getattr(error, "stdout", None),
                           stderr=getattr(error, "stderr", None), error=error) from error
    if result.returncode not in allowed:
        raise _git_failure(command, args[0], stdout=result.stdout, stderr=result.stderr,
                           returncode=result.returncode)
    return result.stdout


def _identity(path):
    # Restart-stable: the device number changes across a system restart, while
    # path plus inode still distinguishes a replaced directory at the same path.
    return workspace_identity.stable_identity(path)


def _commit(root, ref):
    if not isinstance(ref, str) or not ref or "\0" in ref:
        raise BoardError("INVALID_WORKSPACE", "base.ref must be a nonempty Git revision")
    try:
        return _line(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
    except BoardError as error:
        if error.code == "WORKSPACE_GIT_UNKNOWN":
            raise
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
            if error.code == "WORKSPACE_GIT_UNKNOWN":
                raise
            raise BoardError("WORKSPACE_UNSUPPORTED", "A non-bare Git checkout is required", cwd=str(path)) from error
        if not path.is_relative_to(root):
            raise BoardError("WORKSPACE_UNSUPPORTED", "cwd is outside the Git checkout root", cwd=str(path))
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
    if _WINDOWS:
        if normalized != value:
            raise BoardError("WORKSPACE_UNSUPPORTED", "A Git path has a Windows alias", path=value)
        windows_paths.validate_git_path(value)
    if normalized == "." and not allow_root:
        raise BoardError("INVALID_WORKSPACE", "An explicit file or directory path is required", path=value)
    return normalized


def _in_scope(path, scope):
    return any(item == "." or path == item or path.startswith(item + "/") for item in scope)


@contextmanager
def _parent(root, relative, *, create=False):
    """Open each parent by directory descriptor, never following symlinks."""
    if _WINDOWS:
        _relative(relative)
        with windows_paths.parent(Path(root) / relative, create=create, repository=root) as (api, path):
            yield api, path
        return
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = PurePosixPath(relative).parts
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, dir_fd=descriptor)
                except FileExistsError:
                    pass
            next_descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        yield descriptor, parts[-1]
    finally:
        os.close(descriptor)


def _file(root, relative, *, mode_hint=None):
    # dir_fd and O_NOFOLLOW also cover races in intermediate path components.
    # https://docs.python.org/3/library/os.html#files-and-directories
    try:
        if _WINDOWS:
            _relative(relative)
            item = windows_paths.read(Path(root) / relative, allow_symlink=True, repository=root)
            # Windows cannot represent Git's executable bit in chmod. The index
            # remains authoritative for a tracked regular file's Git mode.
            if item is not None and item[0] != "120000" and mode_hint in ("100644", "100755"):
                return mode_hint, item[1]
            return item
        with _parent(root, relative) as (parent, name):
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode):
                return "120000", os.fsencode(os.readlink(name, dir_fd=parent))
            if not stat.S_ISREG(before.st_mode):
                raise BoardError("WORKSPACE_UNSUPPORTED", "Only regular files and symlinks can be snapshotted", path=relative)
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            with os.fdopen(descriptor, "rb") as stream:
                data = stream.read()
                after = os.fstat(stream.fileno())
            attributes = ("st_ino", "st_dev", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")
            if any(getattr(before, key) != getattr(after, key) for key in attributes):
                raise BoardError("WORKSPACE_CHANGED", "A file changed during snapshot capture", path=relative)
            return "100755" if before.st_mode & 0o111 else "100644", data
    except FileNotFoundError:
        return None


def _file_with_git_mode(root, relative, mode):
    return _file(root, relative, mode_hint=mode) if _WINDOWS else _file(root, relative)


def _blob_oid(data, fmt):
    # The object number of already-read bytes: exactly what
    # ``git hash-object --no-filters --stdin`` computes over the payload in the
    # repository's own object format. No path is involved, so clean filters,
    # CRLF conversion and autocrlf cannot change the result. An object format
    # hashlib does not implement fails closed instead of guessing a length.
    if fmt not in ("sha1", "sha256"):
        raise BoardError("WORKSPACE_UNSUPPORTED", "The repository object format is not supported", format=fmt)
    return hashlib.new(fmt, b"blob %d\0" % len(data) + data).hexdigest()


def _known_objects(root, oids):
    query = b"".join(oid.encode() + b"\n" for oid in oids)
    present = set()
    for line in _git(root, "cat-file", "--batch-check", data=query).splitlines():
        name, _, description = line.partition(b" ")
        if description.startswith(b"blob "):
            present.add(name.decode())
    return present


def _write_objects(root, objects, *, state_dir):
    # One fast-import stream stores every missing payload as a blob in the same
    # repository, carrying the exact bytes that were already read. The exported
    # marks must reproduce the computed object numbers, so the write semantics
    # stay hash-object's. Blob commands create no ref, branch, tag or stash.
    # https://git-scm.com/docs/git-fast-import
    with ExitStack() as cleanup:
        directory = private_dirs.make_temporary_dir(prefix="hey-my-buddy-objects-",
            directory=private_dirs.ensure_private_dir(Path(state_dir) / "tmp"))
        cleanup.callback(private_dirs.remove_tree, directory)
        marks = Path(directory) / "marks"
        with tempfile.TemporaryFile(dir=directory) as diagnostics, tempfile.TemporaryFile(dir=directory) as output:
            command, git_env = _git_command(root, "fast-import", "--quiet", "--done",
                                            "--export-marks=" + os.fspath(marks))
            try:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output,
                                           stderr=diagnostics, env=git_env, creationflags=service_creation_flags())
            except OSError as error:
                raise _git_failure(command, "fast-import", error=error) from error
            code, recorded = 1, {}
            try:
                parts = [b"feature done\n"]
                for number, (oid, data) in enumerate(objects.items(), start=1):
                    parts.extend((b"blob\nmark :%d\ndata %d\n" % (number, len(data)), data, b"\n"))
                parts.append(b"done\n")
                process.communicate(input=b"".join(parts), timeout=_GIT_TIMEOUT_SECONDS)
                code = process.returncode
            except (OSError, subprocess.TimeoutExpired) as error:
                # communicate bounds both pipe writes and the final wait. Reap
                # our child before reading captured diagnostics.
                try:
                    if process.poll() is None:
                        process.kill()
                    process.communicate()
                except OSError:
                    pass
                if isinstance(error, subprocess.TimeoutExpired):
                    raise _git_unknown(command, "fast-import", error, stdout=output, stderr=diagnostics,
                                       returncode=process.returncode) from error
                raise _git_failure(command, "fast-import", stdout=output, stderr=diagnostics,
                                   returncode=process.returncode, error=error) from error
            if code != 0:
                raise _git_failure(command, "fast-import", stdout=output, stderr=diagnostics, returncode=code)
            try:
                for line in marks.read_bytes().splitlines():
                    name, _, oid = line.partition(b" ")
                    recorded[name.decode()] = oid.decode()
            except OSError as error:
                raise _git_failure(command, "fast-import", stdout=output, stderr=diagnostics,
                                   returncode=code, error=error) from error
            for number, (oid, _data) in enumerate(objects.items(), start=1):
                if recorded.get(f":{number}") != oid:
                    raise _git_failure(command, "fast-import", stdout=output, stderr=diagnostics, returncode=code,
                                       message="Git numbered workspace bytes differently than the computed object identity",
                                       expected=oid)


def _batch_blobs(root, payloads, *, write, state_dir=None):
    """Object numbers for already-read payloads, in input order.

    One Git process names the repository's object format; write=True adds one
    existence pass and one fast-import stream for the payloads the repository
    does not have yet, so the process count no longer grows with the file
    count. Identities and stored bytes both come from the payloads themselves,
    never from a second read of any path.
    """
    if not payloads:
        return []
    fmt = _line(root, "rev-parse", "--show-object-format")
    oids = [_blob_oid(payload, fmt) for payload in payloads]
    if write:
        present = _known_objects(root, oids)
        missing = {}
        for oid, payload in zip(oids, payloads):
            if oid not in present:
                missing.setdefault(oid, payload)
        if missing:
            _write_objects(root, missing, state_dir=state_dir)
    return oids


def _blob(root, data, *, write=False, state_dir=None):
    return _batch_blobs(root, [data], write=write, state_dir=state_dir)[0]


def _batch_objects(root, oids):
    """Read many committed objects' exact bytes from one Git process."""
    if not oids:
        return []
    query = b"".join(oid.encode() + b"\n" for oid in oids)
    payload = _git(root, "cat-file", "--batch", data=query)
    command, _env = _git_command(root, "cat-file", "--batch")
    results = []
    position = 0
    for oid in oids:
        end = payload.find(b"\n", position)
        name, _, description = payload[position:end].partition(b" ")
        fields = description.split()
        if name.decode() != oid or len(fields) != 2 or fields[0] != b"blob":
            raise _git_failure(command, "cat-file", stdout=payload, returncode=0,
                               message="Git did not return a workspace object", oid=oid)
        start = end + 1
        results.append(payload[start:start + int(fields[1])])
        position = start + int(fields[1]) + 1
    if position != len(payload):
        raise _git_failure(command, "cat-file", stdout=payload, returncode=0,
                           message="Git returned more workspace objects than requested")
    return results


def _entries(root, revision=None):
    args = ("ls-tree", "-rz", revision) if revision else ("ls-files", "--stage", "-z")
    entries = {}
    for entry in _git(root, *args).split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, middle, last = metadata.decode().split()
        name = _relative(os.fsdecode(path))
        if mode not in ("100644", "100755", "120000"):
            raise BoardError("WORKSPACE_UNSUPPORTED", "Submodules and sparse index entries are not supported", path=name)
        if revision:
            oid = last
        else:
            oid = middle
            if last != "0":
                raise BoardError("WORKSPACE_UNMERGED", "Resolve the source index conflicts before preparing a workspace", path=name)
        entries[name] = [mode, oid]
    if not revision and any(entry.startswith(b"S ") for entry in _git(root, "ls-files", "-t", "-z").split(b"\0")):
        raise BoardError("WORKSPACE_UNSUPPORTED", "Sparse checkouts require an explicit full checkout")
    if _WINDOWS:
        windows_paths.validate_unique(entries)
    return entries


def _untracked(root, selected):
    # Git prunes ignored dependency/cache directories before listing files.
    # Explicit selections may include ignored input, but only within their
    # literal pathspecs. https://git-scm.com/docs/git-ls-files#_options
    paths = set(_git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"))
    if selected:
        paths.update(_git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--", *selected).split(b"\0"))
    result = sorted(_relative(os.fsdecode(path)) for path in paths if path)
    if _WINDOWS:
        windows_paths.validate_unique(result)
    return result


def _observe(root, selected, *, write=False, require_selected=False, state_dir=None):
    head = _commit(root, "HEAD")
    index = _entries(root)
    tracked, untracked, included = {}, {}, {}
    blobs = []
    for path in index:
        item = _file_with_git_mode(root, path, index[path][0])
        if item is not None:
            mode, data = item
            entry = [mode, None]
            blobs.append((entry, data))
            tracked[path] = entry
    for path in _untracked(root, selected):
        item = _file(root, path)
        if item is None:
            raise BoardError("WORKSPACE_CHANGED", "An untracked file disappeared during capture", path=path)
        mode, data = item
        untracked[path] = [mode, _sha(data)]
        if _in_scope(path, selected):
            entry = [mode, None]
            blobs.append((entry, data))
            included[path] = entry
    for (entry, _data), oid in zip(blobs, _batch_blobs(root, [data for _entry, data in blobs], write=write, state_dir=state_dir)):
        entry[1] = oid
    if _WINDOWS:
        windows_paths.validate_unique([*index, *untracked])
    if require_selected:
        for path in selected:
            if not any(_in_scope(candidate, [path]) for candidate in included):
                raise BoardError("INVALID_WORKSPACE", "includeUntracked does not select an untracked file", path=path)
    observation = {"head": head, "index": index, "tracked": tracked, "untracked": untracked, "included": included}
    observation["fingerprint"] = _sha(_json(observation))
    return observation


def _stable_observation(root, selected, *, write=False, require_selected=False, state_dir=None):
    observation = _observe(root, selected, write=write, require_selected=require_selected, state_dir=state_dir)
    if observation != _observe(root, selected, state_dir=state_dir):
        raise BoardError("WORKSPACE_CHANGED", "The checkout changed during snapshot capture")
    return observation


def _tree(root, entries, *, state_dir):
    # A private index builds raw blob trees without filters or changes to the
    # source index. https://git-scm.com/docs/git-update-index#_using_index_info
    with ExitStack() as cleanup:
        directory = private_dirs.make_temporary_dir(prefix="hey-my-buddy-index-",
            directory=private_dirs.ensure_private_dir(Path(state_dir) / "tmp"))
        cleanup.callback(private_dirs.remove_tree, directory)
        environment = {"GIT_INDEX_FILE": str(Path(directory) / "index")}
        _git(root, "read-tree", "--empty", env=environment)
        records = b"".join(mode.encode() + b" " + oid.encode() + b"\t" + os.fsencode(path) + b"\0"
                           for path, (mode, oid) in sorted(entries.items()))
        if records:
            _git(root, "update-index", "-z", "--index-info", data=records, env=environment)
        return _line(root, "write-tree", env=environment)


def _commit_tree(root, tree, parent, message):
    # Fixed metadata makes recovery after object creation deterministic; these
    # are snapshot commits, not user-authored history or branch changes.
    # https://git-scm.com/docs/git-commit-tree#_commit_information
    environment = {"GIT_AUTHOR_NAME": "hey-my-buddy snapshot", "GIT_AUTHOR_EMAIL": "hey-my-buddy@localhost",
                   "GIT_COMMITTER_NAME": "hey-my-buddy snapshot", "GIT_COMMITTER_EMAIL": "hey-my-buddy@localhost",
                   "GIT_AUTHOR_DATE": "@946684800 +0000", "GIT_COMMITTER_DATE": "@946684800 +0000"}
    return _line(root, "commit-tree", tree, "-p", parent, "--no-gpg-sign", data=(message + "\n").encode(), env=environment)


def _diff(root, before, after):
    return _git(root, "diff", "--binary", "--full-index", "--no-renames", "--no-ext-diff", "--no-textconv",
                "--no-color", "--src-prefix=a/", "--dst-prefix=b/", before, after, "--")


def _read(path, *, repository=None):
    try:
        if _WINDOWS and repository is not None:
            return windows_paths.read(path, repository=repository)
        with os.fdopen(private_dirs.open_regular_fd(path, os.O_RDONLY), "rb") as stream:
            return stream.read()
    except FileNotFoundError:
        return None


def _record(path):
    data = _read(path)
    if data is None:
        return None
    try:
        return json.loads(data)
    except (ValueError, UnicodeError) as error:
        raise BoardError("WORKSPACE_CONFLICT", "A workspace recovery record is malformed", path=str(path)) from error


def _write_once(path, data, *, repository=None):
    if _WINDOWS and repository is not None:
        windows_paths.write_once(path, data, repository=repository)
        return
    with windows_paths.artifact_publication(path):
        private_files.publish_bytes(path, data, immutable=True)


@contextmanager
def _lock(directory, *, create=True):
    flags = os.O_RDWR | (os.O_CREAT if create else 0)
    descriptor = private_dirs.open_regular_fd(directory / ".lock", flags)
    try:
        try:
            locking.lock(descriptor, blocking=False)
        except BlockingIOError as error:
            raise BoardError("WORKSPACE_BUSY", "Another operation is preparing or sealing this workspace") from error
        yield
    finally:
        os.close(descriptor)


def _pin(root, ref, commit):
    # Compare-and-create, never move an existing reference to new contents.
    # https://git-scm.com/docs/git-update-ref#_description
    if _line(root, "symbolic-ref", "--quiet", ref, allowed=(0, 1)):
        raise BoardError("WORKSPACE_CONFLICT", "An immutable snapshot reference cannot be symbolic", ref=ref)
    current = _line(root, "rev-parse", "--verify", ref, allowed=(0, 128))
    if current == commit:
        return
    if current:
        raise BoardError("WORKSPACE_CONFLICT", "An immutable Git reference has different contents", ref=ref)
    _git(root, "update-ref", "--no-deref", ref, commit, "0" * len(commit))


def _intent(intent):
    allowed = {"kind", "cwd", "access", "accessNote", "base", "includeUntracked", "writeScope", "integrator", "targetRef"}
    if not isinstance(intent, dict) or set(intent) - allowed:
        raise BoardError("INVALID_WORKSPACE", "Unknown executionWorkspace fields")
    if intent.get("kind") not in ("existing", "worktree") or intent.get("access") not in ("read", "write", "full"):
        raise BoardError("INVALID_WORKSPACE", "Workspace kind and access must be explicit")
    normalize_access(intent)
    base = intent.get("base")
    if not isinstance(base, dict) or set(base) - {"kind", "ref"} or base.get("kind") not in ("commit", "working-tree"):
        raise BoardError("INVALID_WORKSPACE", "Workspace base must select commit or working-tree")
    integrator = _actor_identity(intent.get("integrator"), "Workspace integrator", ("host", "lead"))
    if intent.get("targetRef") is not None and (not isinstance(intent["targetRef"], str) or not intent["targetRef"].strip()):
        raise BoardError("INVALID_WORKSPACE", "targetRef must be an attribution string")
    result = dict(intent, base=dict(base), integrator=integrator)
    if not isinstance(intent.get("cwd"), str) or not intent["cwd"] or "\0" in intent["cwd"]:
        raise BoardError("INVALID_WORKSPACE", "cwd must be a directory path")
    # Resolving a ref belongs to first preparation, not replay normalization.
    result["cwd"] = str(Path(intent["cwd"]).resolve())
    for field in ("includeUntracked", "writeScope"):
        values = intent.get(field, [])
        if not isinstance(values, list):
            raise BoardError("INVALID_WORKSPACE", field + " must be a list")
        result[field] = sorted({_relative(value, allow_root=True) for value in values})
    result.setdefault("targetRef", None)
    if result["access"] != "read" and not result["writeScope"]:
        raise BoardError("INVALID_WORKSPACE", "Write access requires an explicit writeScope")
    if base["kind"] == "commit" and result["includeUntracked"]:
        raise BoardError("INVALID_WORKSPACE", "Untracked input requires a working-tree base")
    return result


def _actor_identity(value, label, roles=("host", "lead", "console")):
    if (not isinstance(value, dict) or set(value) != {"role", "id"}
            or value.get("role") not in roles
            or not isinstance(value.get("id"), str) or not value["id"].strip()):
        raise BoardError("INVALID_WORKSPACE", label + " must have an allowed role and nonempty id")
    return dict(value)


def _mkdir(path):
    native_path(path).mkdir(mode=directory_mode(), exist_ok=True)


def _workspace_directory(state_dir, request_id):
    if not isinstance(request_id, str) or not request_id or "\0" in request_id:
        raise BoardError("INVALID_WORKSPACE", "request_id must be a nonempty stable identity")
    state_dir = Path(state_dir).resolve()
    native_path(state_dir).mkdir(mode=directory_mode(), parents=True, exist_ok=True)
    workspace_id = "ws-" + _sha(_json([str(state_dir), request_id]))[:32]
    _mkdir(state_dir / "workspaces")
    directory = state_dir / "workspaces" / workspace_id
    _mkdir(directory)
    return workspace_id, directory


def _snapshot(source, intent, workspace_id, *, state_dir):
    root = Path(source["checkoutRoot"])
    base = _commit(root, intent["base"].get("ref", "HEAD"))
    base_entries = _entries(root, base)
    base_tree = _line(root, "rev-parse", base + "^{tree}")
    snapshot = {"repositoryPath": source["repositoryPath"], "sourceCheckoutId": source["checkoutId"],
                "sourceHead": source["headCommit"], "includeSelectors": intent["includeUntracked"],
                "inputRef": f"refs/hey-my-buddy/workspaces/{workspace_id}/input",
                "stagedRef": f"refs/hey-my-buddy/workspaces/{workspace_id}/staged"}
    if intent["base"]["kind"] == "working-tree":
        observed = _stable_observation(root, intent["includeUntracked"], write=True, require_selected=True, state_dir=state_dir)
        if observed["head"] != source["headCommit"]:
            raise BoardError("WORKSPACE_CHANGED", "The source HEAD changed during preparation")
        staged_tree = _tree(root, observed["index"], state_dir=state_dir)
        input_tree = _tree(root, observed["tracked"] | observed["included"], state_dir=state_dir)
        # A later turn may select its previous sealed output without moving the
        # source HEAD. Such a ref is an exact content handoff, not permission to
        # silently absorb unrelated edits made after that turn was sealed.
        if base != source["headCommit"] and input_tree != base_tree:
            raise BoardError("WORKSPACE_BASE_MISMATCH", "The working tree does not match the explicitly pinned handoff commit")
        snapshot.update(includedUntracked=sorted(observed["included"]),
                        excludedUntracked=sorted(set(observed["untracked"]) - set(observed["included"])),
                        excludedEntries={path: value for path, value in observed["untracked"].items() if path not in observed["included"]},
                        sourceFingerprint=observed["fingerprint"])
    else:
        staged_tree = input_tree = base_tree
        snapshot.update(includedUntracked=[], excludedUntracked=_untracked(root, []), excludedEntries={})
        if intent["kind"] == "existing":
            observed = _stable_observation(root, [], state_dir=state_dir)
            if observed["head"] != base or observed["index"] != base_entries or observed["tracked"] != base_entries or observed["untracked"]:
                raise BoardError("WORKSPACE_BASE_MISMATCH", "The existing checkout does not exactly match the selected commit")
            snapshot["sourceFingerprint"] = observed["fingerprint"]
    staged_commit = base if staged_tree == base_tree else _commit_tree(root, staged_tree, base, workspace_id + " staged input")
    input_commit = base if input_tree == base_tree else _commit_tree(root, input_tree, base, workspace_id + " input")
    snapshot.update(stagedTree=staged_tree, stagedCommit=staged_commit)
    return {"baseCommit": base, "inputCommit": input_commit, "inputTree": input_tree, "snapshot": snapshot}


def _worktree_record(root, target):
    for record in _git(root, "worktree", "list", "--porcelain", "-z").split(b"\0\0"):
        fields = {}
        for line in record.split(b"\0"):
            key, _, value = line.partition(b" ")
            fields[os.fsdecode(key)] = os.fsdecode(value)
        if "worktree" in fields and Path(fields["worktree"]).resolve() == Path(target).resolve():
            return fields
    return None


def _windows_links(root, entries):
    names = [path for path, (mode, _oid) in entries.items() if mode == "120000"]
    targets = dict(zip(names, map(os.fsdecode, _batch_objects(root, [entries[path][1] for path in names]))))
    types = windows_paths.symlink_types(root, entries, targets)
    return {path: (target, types[path]) for path, target in targets.items()}


def _materialize(root, entries, *, tree=None):
    if _WINDOWS:
        windows_paths.validate_unique(entries)
        links = _windows_links(root, entries if tree is None else tree)
        files = [path for path, (mode, _oid) in entries.items() if mode != "120000"]
        contents = iter(_batch_objects(root, [entries[path][1] for path in files]))
        for path, (mode, _oid) in entries.items():
            _relative(path)
            target = Path(root) / path
            if mode == "120000":
                text, directory = links[path]
                windows_paths.symlink(target, text, directory=directory, repository=root)
            else:
                windows_paths.write_new(target, next(contents), create_parents=True, repository=root)
        return
    contents = iter(_batch_objects(root, [oid for _mode, oid in entries.values()]))
    for (path, (mode, _oid)), data in zip(entries.items(), contents):
        with _parent(root, path, create=True) as (parent, name):
            if mode == "120000":
                os.symlink(os.fsdecode(data), name, dir_fd=parent)
            else:
                descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o755 if mode == "100755" else 0o644, dir_fd=parent)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    os.fchmod(stream.fileno(), 0o755 if mode == "100755" else 0o644)


def _prepare_worktree(source, target, pinned, reason):
    root = Path(source["repositoryPath"])
    entries = _entries(root, pinned["inputCommit"])
    if target.exists() or target.is_symlink():
        record = _worktree_record(root, target)
        if target.is_symlink() or not record or record.get("locked") != reason or "detached" not in record:
            raise BoardError("WORKSPACE_CONFLICT", "The worktree target is not owned by this preparation", path=str(target))
        identity = inspect(str(target))
        observed = _observe(target, [])
        if (source["repositoryId"] != identity["repositoryId"]
                or observed["head"] != pinned["inputCommit"] or observed["index"] != entries or observed["tracked"] != entries or observed["untracked"]):
            raise BoardError("WORKSPACE_CONFLICT", "The interrupted worktree is incomplete or changed; it was preserved", path=str(target))
        return
    # The allocation lock excludes another prepare/admission while these bytes
    # are materialized. Capture physical ownership before writing any content.
    identity = None
    try:
        _git(root, "worktree", "add", "--detach", "--no-checkout", "--lock", "--reason", reason,
             str(target), pinned["inputCommit"])
        identity = _new_worktree_identity(target, source)
        _materialize(target, entries)
        _git(target, "read-tree", pinned["inputTree"])
    except Exception as error:
        if identity is None and _path_present(target) and not target.is_symlink():
            try:
                identity = _new_worktree_identity(target, source)
            except Exception as proof_error:
                error.add_note("New allocation identity could not be proved: " + str(proof_error)[:2000])
        if identity is not None:
            _preparation_rollback(error, target, identity, pinned, reason)
        elif _path_present(target):
            _record_preparation_cleanup(error, target, {"removed": False, "path": str(target),
                "inputCommit": pinned["inputCommit"], "repositoryPath": str(root),
                "lock": reason, "reasons": ["allocation-identity-unproven"]})
        raise
    return identity


def _new_worktree_identity(target, source):
    # Git wrote this regular pointer as part of worktree add. Capture the B1
    # git-dir identity and the physical directory before materialization.
    pointer = _read(target / ".git", repository=target).decode().strip()
    if not pointer.startswith("gitdir: "):
        raise BoardError("WORKSPACE_UNSAFE", "The new checkout has no Git directory pointer")
    git_dir = Path(pointer[len("gitdir: "):])
    if not is_absolute_path(git_dir):
        git_dir = target / git_dir
    return {"checkoutId": _identity(git_dir), "repositoryId": source["repositoryId"],
            "physicalId": _identity(target)}


def _preparation_rollback(error, target, identity, pinned, reason):
    """Revoke only this call's newly created, still physically owned worktree.

    An incomplete materialization has no published input manifest yet. Its local
    creation identity and fixed input record are retained even if removal fails.
    The original preparation error remains the exception the caller receives.
    """
    facts = {"path": str(target), "physicalId": identity["physicalId"], "checkoutId": identity["checkoutId"],
             "repositoryId": identity["repositoryId"], "inputCommit": pinned["inputCommit"],
             "repositoryPath": pinned["snapshot"]["repositoryPath"], "lock": reason}
    try:
        actual = inspect(str(target))
        if (target.is_symlink() or actual["checkoutRoot"] != str(target)
                or _identity(target) != identity["physicalId"]):
            raise BoardError("WORKSPACE_UNSAFE", "The new allocation path changed")
        for field in ("checkoutId", "repositoryId"):
            if identity[field] != actual[field]:
                raise BoardError("WORKSPACE_CHANGED", "The new allocation identity changed", field=field)
        root = Path(actual["repositoryPath"])
        record = _worktree_record(root, target)
        if (not record or record.get("locked") != reason or "detached" not in record
                or record.get("HEAD") != pinned["inputCommit"]):
            raise BoardError("WORKSPACE_UNSAFE", "The new allocation registration changed")
        for field, commit in (("inputRef", pinned["inputCommit"]),
                              ("stagedRef", pinned["snapshot"]["stagedCommit"])):
            ref = pinned["snapshot"][field]
            if (_line(root, "symbolic-ref", "--quiet", ref, allowed=(0, 1))
                    or _line(root, "rev-parse", "--verify", ref) != commit):
                raise BoardError("WORKSPACE_CONFLICT", "The new allocation fixed reference changed", ref=ref)
        _git(root, "worktree", "unlock", str(target))
        try:
            _git(root, "worktree", "remove", "--force", str(target))
        except Exception as remove_error:
            if _path_present(target):
                try:
                    _git(root, "worktree", "lock", "--reason", reason, str(target))
                except BoardError as relock_error:
                    if isinstance(remove_error, BoardError):
                        remove_error.details["relockError"] = relock_error.payload()
                    else:
                        remove_error.add_note("Relock failed: " + str(relock_error))
            raise
        facts["removed"] = not _path_present(target)
    except Exception as cleanup_error:
        facts.update(removed=False, cleanupError=(cleanup_error.payload() if isinstance(cleanup_error, BoardError)
                                                 else {"code": type(cleanup_error).__name__, "message": str(cleanup_error)[:2000]}))
    _record_preparation_cleanup(error, target, facts)


def _record_preparation_cleanup(error, target, facts):
    error.preparation_cleanup = facts
    if isinstance(error, BoardError):
        error.details["preparationCleanup"] = facts
    else:
        error.add_note("Preparation cleanup: " + _json(facts).decode())
    try:
        _write_once(target.parent / ("preparation-cleanup-" + _sha(_json(facts))[:16] + ".json"), _json(facts))
    except Exception as record_error:
        error.add_note("Preparation cleanup receipt failed: " + str(record_error)[:2000])


def _validate_manifest(manifest):
    fields = {"version", "workspaceId", "kind", "path", "checkoutRoot", "checkoutId", "repositoryId", "access",
              "baseCommit", "inputCommit", "inputTree", "writeScope", "integrator", "targetRef", "exclusionPolicy", "snapshot", "manifestSha256"}
    if not isinstance(manifest, dict) or type(manifest.get("version")) is not int or manifest["version"] not in (1, 2, 3):
        raise BoardError("INVALID_WORKSPACE", "Invalid workspace manifest schema")
    if manifest["version"] in (1, 2) and manifest.get("access") == "full":
        fields.add("accessReason")
    if set(manifest) != fields:
        raise BoardError("INVALID_WORKSPACE", "Invalid workspace manifest schema")
    expected = _sha(_json({key: value for key, value in manifest.items() if key != "manifestSha256"}))
    if expected != manifest["manifestSha256"]:
        raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The workspace manifest hash does not match its contents")
    if manifest["version"] in (2, 3):
        _actor_identity(manifest["integrator"], "Workspace integrator", ("host", "lead"))
    if manifest["exclusionPolicy"] != _EXCLUSION_POLICY:
        raise BoardError("INVALID_WORKSPACE", "Unsupported workspace exclusion policy")
    snapshot_fields = {"repositoryPath", "stagedCommit", "stagedTree", "inputRef", "stagedRef", "executionSelectors",
                       "executionFingerprint", "excludedEntries"}
    if not isinstance(manifest["snapshot"], dict) or not snapshot_fields <= manifest["snapshot"].keys():
        raise BoardError("INVALID_WORKSPACE", "Invalid workspace snapshot")
    if manifest["kind"] not in ("existing", "worktree") or manifest["access"] not in ("read", "write", "full"):
        raise BoardError("INVALID_WORKSPACE", "Invalid manifest kind or access")
    for value in (manifest["path"], manifest["checkoutRoot"], manifest["snapshot"]["repositoryPath"]):
        if not isinstance(value, str) or not is_absolute_path(value):
            raise BoardError("INVALID_WORKSPACE", "Manifest paths must be absolute")
    for values in (manifest["writeScope"], manifest["snapshot"]["executionSelectors"]):
        if not isinstance(values, list) or any(_relative(value, allow_root=True) != value for value in values):
            raise BoardError("INVALID_WORKSPACE", "Manifest paths must be normalized")


def verify(manifest: dict, *, require_unchanged: bool = True) -> dict:
    """Validate fixed input objects and actual execution checkout identity.

    Read workspaces always require unchanged managed input, as defined by the
    manifest's exclusionPolicy. Ignored caches may change. For a writable workspace,
    ``require_unchanged=False`` validates identity and immutable objects without
    comparing evolving files to the original execution fingerprint.
    """
    with _errors():
        _validate_manifest(manifest)
        return _verify(manifest, require_unchanged=require_unchanged)


def _verify(manifest, *, require_unchanged):
    """Observe a manifest already validated at the operation's input boundary."""
    with _errors():
        actual = inspect(manifest["path"])
        if actual["checkoutRoot"] != manifest["checkoutRoot"]:
            raise BoardError("WORKSPACE_CHANGED", "The execution checkout identity changed", field="checkoutRoot")
        for field in ("checkoutId", "repositoryId"):
            if manifest[field] != actual[field]:
                raise BoardError("WORKSPACE_CHANGED", "The execution checkout identity changed", field=field)
        root = Path(actual["checkoutRoot"])
        snapshot = manifest["snapshot"]
        if _line(root, "rev-parse", manifest["inputCommit"] + "^{tree}") != manifest["inputTree"]:
            raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The pinned input tree is inconsistent")
        if manifest["kind"] == "existing" and snapshot["stagedTree"] != manifest["inputTree"]:
            if _line(root, "rev-parse", snapshot["stagedCommit"] + "^{tree}") != snapshot["stagedTree"]:
                raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The pinned staged tree is inconsistent")
        unchanged = None
        if require_unchanged or manifest["access"] == "read":
            observation = _observe(root, snapshot["executionSelectors"])
            if observation["fingerprint"] != snapshot["executionFingerprint"]:
                raise BoardError("WORKSPACE_CHANGED", "The execution workspace no longer matches its prepared input")
            unchanged = True
        return {"valid": True, "workspaceId": manifest["workspaceId"], "checkoutId": actual["checkoutId"],
                "repositoryId": actual["repositoryId"], "unchanged": unchanged}


def recovery_inputs(manifest: dict) -> list[str]:
    """List exact nonignored partial files in the original write scope.

    The coordinator holds and rechecks the checkout reservation around this read.
    Exact files, rather than directory selectors, keep ignored secrets and caches
    outside the newly authorized continuation snapshot.
    """
    with _errors():
        _validate_manifest(manifest)
        root = Path(manifest["checkoutRoot"])
        return [path for path in _untracked(root, []) if _in_scope(path, manifest["writeScope"])]


@contextmanager
def submission_preparation(state_dir: Path, request_id: str, intent: dict, *, shared_locks=None,
                           _preparation=None):
    """Hold the original allocation lock through preparation and admission.

    Both kinds are new allocations when no manifest existed before this
    preparation. An earlier input record or checkout does not decide that fact;
    the revocation plan must prove every record, reference and physical target.
    Replaying an existing manifest never grants permission to revoke it.
    """
    with _errors():
        intent = _intent(intent)
        workspace_id, directory = _workspace_directory(state_dir, request_id)
    prepared = {"afterRelease": None}

    def after_release():
        if prepared["afterRelease"] is not None:
            prepared["afterRelease"]()

    with ExitStack() as local_stack:
        stack, held = shared_locks if shared_locks is not None else (local_stack, set())
        # This callback precedes the lock entry, so it runs after lock release.
        stack.callback(after_release)
        if directory not in held:
            stack.enter_context(_lock(directory))
            held.add(directory)
        with _errors():
            borrowed = _borrowed_submission_allocation(state_dir, intent)
            if borrowed is not None:
                allocation = borrowed[0]
                if allocation not in held:
                    stack.enter_context(_lock(allocation))
                    held.add(allocation)
                fresh = _borrowed_submission_allocation(state_dir, intent)
                if fresh != borrowed:
                    raise BoardError("WORKSPACE_CHANGED", "The borrowed allocation changed while its lock was acquired")
            new = _record(directory / "manifest.json") is None
            if _preparation is None:
                manifest = _prepare_locked(directory, workspace_id, request_id, intent, state_dir=state_dir)
            else:
                manifest = _prepare_locked(directory, workspace_id, request_id, intent, _preparation, state_dir=state_dir)
        prepared.update(manifest=manifest, newAllocation=new)
        yield prepared


def submission_revoke_plan(state_dir, manifest):
    """Inspect this new allocation under its preparation lock, outside SQLite.

    Any unproven record, changed content, reference or additional evidence keeps
    the entire allocation. The coordinator separately proves no board reference
    under its writer fence before applying this plan with the same lock held.
    """
    with _errors():
        _validate_manifest(manifest)
        state = Path(state_dir).resolve()
        if not _workspace_identifier(manifest["workspaceId"]):
            raise BoardError("WORKSPACE_UNSAFE", "The submission workspace identity is invalid")
        directory = state / "workspaces" / manifest["workspaceId"]
        if private_dirs.linked(directory):
            raise BoardError("WORKSPACE_UNSAFE", "The rejected submission's private directory is linked")
        if _record(directory / "manifest.json") != manifest:
            raise BoardError("WORKSPACE_CONFLICT", "The rejected submission's snapshot changed before removal")
        request = _record(directory / "request.json")
        pinned = _record(directory / "input.json")
        expected_id = "ws-" + _sha(_json([str(state), request["requestId"]]))[:32]
        if (expected_id != manifest["workspaceId"] or request["intent"]["kind"] != manifest["kind"]
                or pinned["inputCommit"] != manifest["inputCommit"]
                or pinned["inputTree"] != manifest["inputTree"]
                or pinned["snapshot"]["stagedCommit"] != manifest["snapshot"]["stagedCommit"]):
            raise BoardError("WORKSPACE_UNSAFE", "The submission records no longer prove this allocation")
        plan = {"manifest": manifest, "directory": directory, "allocation": None, "reasons": []}
        expected = {".lock", "request.json", "input.json", "manifest.json"}
        if manifest["kind"] == "worktree":
            expected.add("checkout")
        if {path.name for path in directory.iterdir()} != expected:
            plan["reasons"].append("additional-evidence")
            return plan
        repository = Path(manifest["snapshot"]["repositoryPath"])
        if _identity(repository) != manifest["repositoryId"]:
            raise BoardError("WORKSPACE_CHANGED", "The rejected submission's repository identity changed")
        prefix = f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"
        refs = set(_line(repository, "for-each-ref", "--format=%(refname)", prefix).splitlines())
        if refs != {prefix + suffix for suffix in ("input", "staged", "retained/input", "retained/staged")}:
            plan["reasons"].append("fixed-reference-set-changed")
            return plan
        for suffix, commit in (("input", manifest["inputCommit"]),
                               ("staged", manifest["snapshot"]["stagedCommit"]),
                               ("retained/input", manifest["inputCommit"]),
                               ("retained/staged", manifest["snapshot"]["stagedCommit"])):
            ref = prefix + suffix
            if (_line(repository, "symbolic-ref", "--quiet", ref, allowed=(0, 1))
                    or _line(repository, "rev-parse", "--verify", ref) != commit):
                plan["reasons"].append("fixed-reference-changed")
        if manifest["kind"] == "worktree":
            proof = _cleanup_inspect(state, manifest)
            plan["allocation"] = proof["allocation"]
            plan["reasons"].extend(proof["reasons"])
            if _git(Path(manifest["checkoutRoot"]), "ls-files", "--others", "--ignored", "--exclude-standard", "-z"):
                plan["reasons"].append("unsealed-changes")
        return plan


def revoke_submission(plan):
    """Remove a proven, unregistered allocation, preserving sweep evidence on failure.

    The original preparation lock and the coordinator's writer fence are held.
    The worktree goes before its manifest so failed Git removal retains the
    record the orphan sweep needs. All remaining removals follow creation in
    reverse order.
    The lock file and directory are removed after release by that context's
    callback. With three callers for one requestId, a second caller can lock the
    unlinked old inode while a third creates and locks a new file, breaking
    mutual exclusion. This window remains accepted; deleting the lock file
    while holding it requires separate Windows verification.
    """
    with _errors():
        manifest, directory = plan["manifest"], plan["directory"]
        repository = Path(manifest["snapshot"]["repositoryPath"])
        if manifest["kind"] == "worktree":
            _remove_worktree(repository, Path(manifest["checkoutRoot"]), plan["allocation"], manifest,
                             {"removed": True, "alreadyRemoved": True})
        (directory / "manifest.json").unlink()
        prefix = f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"
        for suffix, commit in (("staged", manifest["snapshot"]["stagedCommit"]), ("input", manifest["inputCommit"])):
            _git(repository, "update-ref", "--no-deref", "-d", prefix + suffix, commit)
        (directory / "input.json").unlink()
        for suffix, commit in (("retained/staged", manifest["snapshot"]["stagedCommit"]),
                               ("retained/input", manifest["inputCommit"])):
            _git(repository, "update-ref", "--no-deref", "-d", prefix + suffix, commit)
        (directory / "request.json").unlink()
        return {"removed": True}


def _borrowed_submission_allocation(state_dir, intent):
    """Prove a borrowed managed checkout before opening its existing lock.

    The new request's allocation lock cannot fence removal of another request's
    physical checkout. Unknown directories do not authorize creating a lock;
    only the original immutable allocation and current B1 identity do.
    """
    state = Path(state_dir).resolve()
    path = Path(intent["cwd"])
    try:
        parts = path.relative_to(state / "workspaces").parts
    except ValueError:
        return None
    if len(parts) < 2 or not _workspace_identifier(parts[0]) or parts[1] != "checkout":
        return None
    directory = state / "workspaces" / parts[0]
    original = _record(directory / "manifest.json")
    if original is None:
        return None
    _validate_manifest(original)
    allocation = _resolve_allocation(state, original)
    if (allocation is None or allocation["workspaceId"] != parts[0]
            or allocation["path"] != str(directory / "checkout")):
        raise BoardError("WORKSPACE_UNSAFE", "The borrowed checkout has no proven original allocation")
    _verify(original, require_unchanged=False)
    if original["checkoutRoot"] != allocation["path"]:
        raise BoardError("WORKSPACE_CHANGED", "The borrowed execution checkout identity changed")
    registration = _worktree_record(Path(original["snapshot"]["repositoryPath"]), directory / "checkout")
    if registration is None or registration.get("locked") != "hey-my-buddy:" + parts[0]:
        raise BoardError("WORKSPACE_UNSAFE", "The borrowed checkout lost its original Git allocation lock")
    lock = directory / ".lock"
    facts = lock.stat()
    return directory, original, (_identity(directory), _identity(directory / "checkout"),
                                 facts.st_dev, facts.st_ino, facts.st_mode)


def prepare(state_dir: Path, request_id: str, intent: dict, *, _preparation=None) -> dict:
    """Prepare once, then replay the same request's frozen input manifest."""
    with submission_preparation(state_dir, request_id, intent, _preparation=_preparation) as prepared:
        return prepared["manifest"]


def _prepare_locked(directory, workspace_id, request_id, intent, preparation=None, *, state_dir):
    manifest = _record(directory / "manifest.json")
    request_version = manifest["version"] if manifest is not None else 3
    _write_once(directory / "request.json", _json({"version": request_version, "requestId": request_id, "intent": intent}))
    if preparation is not None:
        preparation["replayed"] = manifest is not None
    if manifest is not None:
        verify(manifest, require_unchanged=False)
        return manifest
    pinned = _record(directory / "input.json")
    fresh_input = pinned is None
    if pinned is None:
        source = inspect(intent["cwd"])
        pinned = {"source": source, **_snapshot(source, intent, workspace_id, state_dir=state_dir)}
        # Keep newly created objects reachable before a durable record
        # promises they can be recovered, including across Git GC.
        _pin(source["repositoryPath"], f"refs/hey-my-buddy/workspaces/{workspace_id}/retained/input", pinned["inputCommit"])
        _pin(source["repositoryPath"], f"refs/hey-my-buddy/workspaces/{workspace_id}/retained/staged", pinned["snapshot"]["stagedCommit"])
        _write_once(directory / "input.json", _json(pinned))
    source = pinned["source"]
    repository = Path(pinned["snapshot"]["repositoryPath"])
    _pin(repository, pinned["snapshot"]["inputRef"], pinned["inputCommit"])
    _pin(repository, pinned["snapshot"]["stagedRef"], pinned["snapshot"]["stagedCommit"])
    identity = None
    try:
        path = Path(intent["cwd"])
        if intent["kind"] == "worktree":
            target = directory / "checkout"
            identity = _prepare_worktree(source, target, pinned, "hey-my-buddy:" + workspace_id)
            relative = path.relative_to(source["checkoutRoot"])
            path = target / relative
            if relative.parts:
                with _parent(target, (relative / ".cwd-placeholder").as_posix(), create=True):
                    pass
        actual = inspect(str(path))
        if source["repositoryId"] != actual["repositoryId"]:
            raise BoardError("WORKSPACE_CHANGED", "The execution repository changed during preparation")
        selectors = intent["includeUntracked"] if intent["kind"] == "existing" else []
        if intent["kind"] == "existing" and source["checkoutId"] != actual["checkoutId"]:
            raise BoardError("WORKSPACE_CHANGED", "The existing checkout changed after its input was frozen")
        if intent["kind"] == "existing" and not fresh_input:
            replay = _observe(Path(actual["checkoutRoot"]), selectors, state_dir=state_dir)
            if replay["fingerprint"] != pinned["snapshot"]["sourceFingerprint"]:
                raise BoardError("WORKSPACE_CHANGED", "The existing checkout changed after its input was frozen")
        fingerprint = (pinned["snapshot"]["sourceFingerprint"] if intent["kind"] == "existing" else
                       _observe(Path(actual["checkoutRoot"]), selectors, state_dir=state_dir)["fingerprint"])
        snapshot = dict(pinned["snapshot"], executionSelectors=selectors, executionFingerprint=fingerprint)
        manifest = {"version": 3, "workspaceId": workspace_id, "kind": intent["kind"], "path": str(path),
                    "checkoutRoot": actual["checkoutRoot"], "checkoutId": actual["checkoutId"],
                    "repositoryId": actual["repositoryId"], "access": intent["access"],
                    "baseCommit": pinned["baseCommit"], "inputCommit": pinned["inputCommit"], "inputTree": pinned["inputTree"],
                    "writeScope": intent["writeScope"], "integrator": intent["integrator"], "targetRef": intent["targetRef"],
                    "exclusionPolicy": dict(_EXCLUSION_POLICY), "snapshot": snapshot}
        manifest["manifestSha256"] = _sha(_json(manifest))
        _write_once(directory / "manifest.json", _json(manifest))
        return manifest
    except Exception as error:
        if identity is not None:
            _preparation_rollback(error, target, identity, pinned, "hey-my-buddy:" + workspace_id)
        raise


RESOLUTION_ACTIONS = ("restore", "adopt", "abandon")


def _entry_state(entries, path):
    item = entries.get(path)
    return None if item is None else {"mode": item[0], "oid": item[1]}


def _observed_file(root, path, *, mode_hint=None):
    """The current managed state of one path as mode plus content digest.

    The digest is the raw sha256 of the file bytes, so a later compare-and-swap
    never depends on Git objects that a failed worker could have moved.
    """
    item = _file_with_git_mode(root, path, mode_hint)
    if item is None:
        return None
    mode, data = item
    return {"mode": mode, "sha256": _sha(data)}


def _scope_evidence(root, manifest, observation, violations, changed, index_changes, excluded_changes, initial, initial_index):
    """Complete, content-addressed evidence for one out-of-scope failure site."""
    ordered = sorted(violations)
    entries = []
    for path in ordered:
        entries.append({
            "path": path,
            "authorized": _entry_state(initial, path),
            "authorizedIndex": _entry_state(initial_index, path),
            "observed": _observed_file(root, path, mode_hint=(observation["index"].get(path) or [None])[0]),
            "observedIndex": _entry_state(observation["index"], path),
            "changed": path in changed,
            "indexChanged": path in index_changes,
            "excludedChanged": path in excluded_changes,
        })
    return {
        "version": 1,
        "workspaceId": manifest["workspaceId"],
        "manifestSha256": manifest["manifestSha256"],
        "observedFingerprint": observation["fingerprint"],
        "writeScope": list(manifest["writeScope"]),
        "blockingPaths": ordered,
        "changedPaths": list(changed),
        "entries": entries,
    }


def _output_entries(root, manifest, observation, *, allow_outside_scope=False, allowed_paths=None, write=True, state_dir=None):
    initial = _entries(root, manifest["inputCommit"])
    staged_tree = manifest["snapshot"]["stagedTree"]
    initial_index = (_entries(root, staged_tree)
                     if manifest["kind"] == "existing" and staged_tree != manifest["inputTree"] else initial)
    excluded = manifest["snapshot"]["excludedEntries"] if manifest["kind"] == "existing" else {}
    scope = manifest["writeScope"]
    # Paths an earlier Host decision already promoted (adopt) or a previous Host
    # seal carried stay authorized even though they sit outside the original
    # write scope; anything newly outside is still a violation.
    allowed = set(allowed_paths or ())
    entries = dict(observation["tracked"])
    violations = set()
    adopted = set()
    excluded_changes = set()
    pending = []
    for path, fingerprint in observation["untracked"].items():
        if path in excluded and excluded[path] == fingerprint:
            continue
        if path in excluded:
            excluded_changes.add(path)
        item = _file(root, path)
        if item is None or [item[0], _sha(item[1])] != fingerprint:
            raise BoardError("WORKSPACE_CHANGED", "An output changed during sealing", path=path)
        pending.append((path, item))
    for (path, item), oid in zip(pending, _batch_blobs(root, [item[1] for _path, item in pending], write=write, state_dir=state_dir)):
        entry = [item[0], oid]
        if _in_scope(path, scope) or path in allowed or initial.get(path) == entry:
            entries[path] = entry
        elif allow_outside_scope:
            entries[path] = entry
            adopted.add(path)
        else:
            violations.add(path)
    for path in excluded.keys() - observation["untracked"].keys():
        excluded_changes.add(path)
    changed = sorted(path for path in initial.keys() | entries.keys() if initial.get(path) != entries.get(path))
    index_changes = {path for path in initial_index.keys() | observation["index"].keys()
                     if initial_index.get(path) != observation["index"].get(path)}
    outside = {path for path in set(changed) | index_changes | excluded_changes
               if not _in_scope(path, scope) and path not in allowed}
    if allow_outside_scope:
        # An explicitly adopted site promotes these exact paths; the record keeps
        # them named as adopted so a later reader never mistakes them for authorized.
        adopted |= outside
    else:
        violations |= outside
    if violations:
        evidence = _scope_evidence(root, manifest, observation, violations, changed, index_changes, excluded_changes,
                                   initial, initial_index)
        raise BoardError("WORKSPACE_SCOPE_VIOLATION", "Managed workspace paths contain changes outside the declared write scope",
                         paths=sorted(violations), changedPaths=changed, evidence=evidence)
    return entries, changed, sorted(excluded_changes), sorted(adopted)


def _finish_output(repository, output, directory, *, fresh=False):
    if not fresh and _line(repository, "rev-parse", output["commit"] + "^{tree}") != output["tree"]:
        raise BoardError("WORKSPACE_CONFLICT", "The sealed output record is inconsistent")
    bindings = ("workspaceId", "taskId", "attemptId", "manifestSha256", "baseCommit", "inputCommit", "commit",
                "tree", "changedPaths", "includedUntracked", "diffSha256")
    expected_ref = f"refs/hey-my-buddy/workspaces/{output['workspaceId']}/outputs/{directory.name}"
    if _sha(_json(output["snapshot"])) != output["snapshotSha256"] or any(output.get(key) != output["snapshot"].get(key) for key in bindings) or output["ref"] != expected_ref or output["diffPath"] != str(directory / "output.patch"):
        raise BoardError("WORKSPACE_CONFLICT", "The sealed output record is inconsistent")
    patch_path = directory / "output.patch"
    patch_data = _read(patch_path)
    if patch_data is None:
        patch_data = _diff(repository, output["inputCommit"], output["commit"])
    if _sha(patch_data) != output["diffSha256"]:
        raise BoardError("WORKSPACE_CONFLICT", "The immutable output diff changed", path=str(patch_path))
    _write_once(patch_path, patch_data)
    _pin(repository, output["ref"], output["commit"])
    _write_once(directory / "output.json", _json(output))
    return output


def seal(state_dir: Path, manifest: dict, task_id: str, attempt_id: str) -> dict:
    """Seal a stopped attempt's managed output, preserving its fixed Git ref/diff.

    PRECONDITION: the caller has proved process shutdown and still owns the
    workspace reservation. This function neither inspects processes nor proves
    shutdown. The same identity replays its immutable output even after later
    edits or removal of the execution worktree.
    """
    return _seal_output(state_dir, manifest, task_id, attempt_id, suffix="", kind=None)


def host_seal(state_dir: Path, manifest: dict, task_id: str, attempt_id: str, *, allowed_paths=()) -> dict:
    """Seal the Host's own unsealed managed changes as an independent partial output.

    A Host conclusion must never overwrite the Worker's earlier fixed partial
    output of the same task/attempt, so this record, its Git ref and its patch
    live beside that output under ``<output id>-host`` and replay independently.
    ``allowed_paths`` names the exact paths an earlier Host decision (an adopted
    conflict site or a previous Host seal) already promoted; they are treated as
    authorized while any newly changed path outside the write scope still fails.
    """
    return _seal_output(state_dir, manifest, task_id, attempt_id, suffix="-host", kind="host-output",
                        allowed_paths=tuple(allowed_paths or ()))


def _seal_output(state_dir: Path, manifest: dict, task_id: str, attempt_id: str, *,
                 suffix: str, kind: str | None, allowed_paths: tuple = ()) -> dict:
    with _errors():
        _validate_manifest(manifest)
        workspace_id = manifest["workspaceId"]
        workspace_dir = Path(state_dir).resolve() / "workspaces" / workspace_id
        if _record(workspace_dir / "manifest.json") != manifest:
            raise BoardError("WORKSPACE_CONFLICT", "This state directory does not own the supplied workspace manifest")
        with _lock(workspace_dir):
            _mkdir(workspace_dir / "outputs")
            output_id = _sha(_json([task_id, attempt_id]))
            directory = workspace_dir / "outputs" / (output_id + suffix)
            _mkdir(directory)
            repository = Path(manifest["snapshot"]["repositoryPath"])
            if manifest["repositoryId"] != _identity(repository):
                raise BoardError("WORKSPACE_CHANGED", "The output repository identity changed")
            output = _record(directory / "output.json") or _record(directory / "pending.json")
            if output is not None:
                if output.get("taskId") != task_id or output.get("attemptId") != attempt_id or output.get("manifestSha256") != manifest["manifestSha256"]:
                    raise BoardError("WORKSPACE_CONFLICT", "This output identity belongs to a different attempt or input")
                return _finish_output(repository, output, directory)
            _verify(manifest, require_unchanged=False)
            root = Path(manifest["checkoutRoot"])
            observation = _observe(root, manifest["snapshot"]["executionSelectors"], write=True, state_dir=state_dir)
            if manifest["access"] == "read":
                if observation["fingerprint"] != manifest["snapshot"]["executionFingerprint"]:
                    raise BoardError("WORKSPACE_CHANGED", "Read-only input changed before it could be sealed")
                entries, changed, excluded_changes = _entries(root, manifest["inputTree"]), [], []
            else:
                try:
                    entries, changed, excluded_changes, _adopted = _output_entries(
                        root, manifest, observation, allowed_paths=allowed_paths, state_dir=state_dir)
                except BoardError as error:
                    # The failed site keeps a durable record of exactly
                    # which managed paths diverged and from which authorized state.
                    # It is evidence for a Host decision, never a new baseline.
                    if error.code == "WORKSPACE_SCOPE_VIOLATION" and isinstance(error.details.get("evidence"), dict):
                        _record_scope_evidence(directory, error.details["evidence"], task_id, attempt_id)
                    raise
            tree = _tree(root, entries, state_dir=state_dir)
            changed = sorted(_tree_changes(root, manifest["inputTree"], tree))
            if observation != _observe(root, manifest["snapshot"]["executionSelectors"], state_dir=state_dir):
                raise BoardError("WORKSPACE_CHANGED", "The workspace changed during output sealing")
            commit = manifest["inputCommit"] if tree == manifest["inputTree"] else _commit_tree(root, tree, manifest["inputCommit"], workspace_id + " output " + directory.name)
            patch_data = _diff(repository, manifest["inputCommit"], commit)
            snapshot = {"version": 1, "headCommit": observation["head"], "indexSha256": _sha(_json(observation["index"])),
                        "observationSha256": observation["fingerprint"], "tree": tree, "changedPaths": changed,
                        "excludedChangedPaths": excluded_changes, "manifestSha256": manifest["manifestSha256"],
                        "includedUntracked": sorted(entries.keys() & observation["untracked"].keys()),
                        "workspaceId": workspace_id, "taskId": task_id, "attemptId": attempt_id,
                        "baseCommit": manifest["baseCommit"], "inputCommit": manifest["inputCommit"],
                        "commit": commit, "diffSha256": _sha(patch_data)}
            output = {"version": 1, "workspaceId": workspace_id, "taskId": task_id, "attemptId": attempt_id,
                      "manifestSha256": manifest["manifestSha256"], "baseCommit": manifest["baseCommit"],
                      "inputCommit": manifest["inputCommit"], "commit": commit, "tree": tree, "changedPaths": changed,
                      "includedUntracked": snapshot["includedUntracked"],
                      "snapshot": snapshot, "snapshotSha256": _sha(_json(snapshot)),
                      "ref": f"refs/hey-my-buddy/workspaces/{workspace_id}/outputs/{directory.name}",
                      "diffPath": str(directory / "output.patch"), "diffSha256": _sha(patch_data)}
            if kind is not None:
                output["kind"] = kind
            if allowed_paths:
                # The exact previously promoted paths this record relied on; a later
                # cleanup proof re-derives the same allowance from the sealed record.
                output["adoptedPaths"] = sorted(allowed_paths)
                snapshot["adoptedPaths"] = sorted(allowed_paths)
                output["snapshotSha256"] = _sha(_json(snapshot))
            # Retention precedes the recovery record: GC must not discard a
            # chosen output while final reference publication is interrupted.
            _pin(repository, f"refs/hey-my-buddy/workspaces/{workspace_id}/retained/output-{directory.name}", commit)
            _write_once(directory / "pending.json", _json(output))
            _write_once(directory / "output.patch", patch_data)
            return _finish_output(repository, output, directory, fresh=True)


def cumulative_patch(manifest: dict, artifact: dict, original_commit: str) -> dict:
    """Freeze a whole-micro-task patch using immutable Git objects, independent of checkout edits."""
    with _errors():
        _validate_manifest(manifest)
        if original_commit == manifest.get("inputCommit"):
            _validate_output_record(manifest, artifact)
            data = _read(Path(artifact["diffPath"]))
            if data is None or _sha(data) != artifact["diffSha256"]:
                raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The final output patch changed")
            path = Path(artifact["diffPath"]).with_name("cumulative-" + original_commit + ".patch")
            _write_once(path, data)
            return {"baseCommit": original_commit, "outputCommit": artifact["commit"], "path": str(path),
                    "sha256": _sha(data), "changedPaths": list(artifact["changedPaths"])}
        repository, changed = _artifact_binding(manifest, manifest, artifact)
        base = _commit(repository, original_commit)
        data = _diff(repository, base, artifact["commit"])
        path = Path(artifact["diffPath"]).with_name("cumulative-" + base + ".patch")
        _write_once(path, data)
        return {"baseCommit": base, "outputCommit": artifact["commit"], "path": str(path),
                "sha256": _sha(data),
                "changedPaths": sorted(changed) if base == artifact["inputCommit"] else sorted(_tree_changes(repository, base, artifact["commit"]))}


# -- scope failure evidence and Host-directed recovery ------------------------
def _record_scope_evidence(directory, evidence, task_id, attempt_id):
    """Keep one failure-site record per observed workspace fingerprint."""
    record = dict(evidence, taskId=task_id, attemptId=attempt_id)
    folder = directory / "scope-conflicts"
    _mkdir(folder)
    _write_once(folder / f"{record['observedFingerprint']}.json", _json(record))


def _scope_records(workspace_dir, manifest, task_id, attempt_id):
    folder = workspace_dir / "outputs" / _sha(_json([task_id, attempt_id])) / "scope-conflicts"
    if not native_path(folder).exists():
        return []
    if not native_path(folder).is_dir():
        raise BoardError("WORKSPACE_CONFLICT", "The scope evidence path is not a regular directory", path=str(folder))
    records = []
    for entry in sorted(native_path(folder).glob("*.json")):
        path = folder / entry.name
        record = _record(path)
        if not isinstance(record, dict):
            raise BoardError("INVALID_WORKSPACE", "Malformed scope failure evidence", path=str(path))
        if (record.get("taskId") != task_id or record.get("attemptId") != attempt_id
                or record.get("workspaceId") != manifest["workspaceId"]
                or record.get("manifestSha256") != manifest["manifestSha256"]):
            continue
        records.append((native_path(path).stat().st_mtime_ns, path.name, record))
    records.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [record for _, _, record in records]


def scope_conflicts(state_dir, manifest: dict, task_id: str, attempt_id: str) -> list[dict]:
    """The durable out-of-scope failure-site evidence of one stopped attempt.

    Records are read-only evidence; they are never a workspace baseline. The
    caller holds the checkout reservation and rechecks the owner around this call.
    """
    with _errors():
        _validate_manifest(manifest)
        workspace_dir = Path(state_dir).resolve() / "workspaces" / manifest["workspaceId"]
        return _scope_records(workspace_dir, manifest, task_id, attempt_id)


def normalize_scope(values) -> list[str]:
    """Normalize one explicit relative write scope, rejecting escapes."""
    with _errors():
        if not isinstance(values, list):
            raise BoardError("INVALID_WORKSPACE", "writeScope must be a list")
        return sorted({_relative(value, allow_root=True) for value in values})


# -- shared stash observations (facts, never gates) ---------------------------
def _shell_quoted(value: str) -> str:
    """One POSIX single-quoted shell word; nothing inside is ever interpreted."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def _stash_unknown(repository_path, reason) -> dict:
    return stash_observation_unknown(repository_path, reason)


def stash_observation_unknown(repository_path, reason) -> dict:
    """The honest unread observation for one repository: state ``unknown``.

    An unknown is a fact about a failed reading, never an empty list and never
    an error: callers freeze it beside the reason so a Host can tell "there
    were no stash entries" from "the stash list could not be read".
    """
    return {"version": 1, "repositoryPath": str(repository_path), "state": "unknown",
            "reason": str(reason), "entries": None, "truncated": False, "totalCount": None}


def shared_stash_inventory(repository_path) -> dict:
    """One read-only observation of the stash entries a repository shares.

    Managed checkouts are worktrees of the user's repository: ``refs/stash`` and
    its reflog live in the common Git directory, so a stash created or removed in
    any worktree — the user's own checkout included — is visible and removable in
    every other one. Callers pass the snapshot's common Git directory, which
    owns the shared reflog; ``reflog show`` reads it without a work tree. Each
    entry records its commit id and the full
    description Git itself shows (``%gs``, the reflog subject; Git stores
    newline-bearing messages flattened there, while the commit object keeps the
    full text). A repository that cannot be read is an honest ``unknown``
    observation, never an error: a fact that cannot be read must not block or
    fail otherwise ready work.
    """
    path = str(repository_path)
    try:
        if _git_in(path, "rev-parse", "--verify", "--quiet", "refs/stash", allowed=(0, 1)).strip():
            payload = _git_in(path, "reflog", "show", "--format=%H%x00%gs%x00", "refs/stash")
        else:
            # No stash ref at all is a complete, empty observation.
            payload = b""
    except BoardError as error:
        reason = error.details.get("reason") if isinstance(error.details, dict) else None
        return _stash_unknown(path, reason or error.message)
    tokens = payload.split(b"\0")
    entries = []
    for index in range(0, len(tokens) - 1, 2):
        # ``--format`` terminates every entry with a newline, which lands on the
        # next record's hash token; descriptions keep their own bytes verbatim.
        commit = tokens[index][1:] if tokens[index].startswith(b"\n") else tokens[index]
        name = commit.decode()
        # Valid UTF-8 passes through exactly. Undecodable bytes become U+FFFD
        # deterministically: the description is display text, the commit id is
        # the exact recoverable identity, and canonical JSON must stay encodable.
        description = tokens[index + 1].decode("utf-8", "replace")
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", name) is None:
            return _stash_unknown(path, "the stash reflog did not parse as commit ids and descriptions")
        entries.append({"commit": name, "description": description})
    return {"version": 1, "repositoryPath": path, "state": "observed",
            "entries": entries, "truncated": False, "totalCount": len(entries)}


def shared_stash_comparison(start, seal) -> dict:
    """Compare two stash observations by complete entries.

    An entry is its commit id together with its description: the same commit
    stashed twice with different descriptions is two entries, an identical
    re-registration of the same pair is not a change, and the stash's stack
    position is not part of its identity because popping one entry shifts every
    index without losing content. ``state`` is ``observed`` when both sides
    were read and ``unknown`` when a side could not be read. This record is
    evidence for a Host; it restores nothing, judges nothing and can never
    fail a seal or a verification.
    """
    def observed(record):
        return (isinstance(record, dict) and record.get("version") == 1 and record.get("state") == "observed"
                and isinstance(record.get("entries"), list)
                and all(isinstance(item, dict) and isinstance(item.get("commit"), str)
                        and isinstance(item.get("description"), str) for item in record["entries"]))

    def counted(record):
        return Counter((item["commit"], item["description"]) for item in record["entries"])

    record = {"version": 1, "state": "unknown", "changed": None,
              "lostEntries": [], "newEntries": [], "lostCount": 0, "newCount": 0, "truncated": False}
    unknown = [side for side, value in (("start", start), ("seal", seal)) if not observed(value)]
    if unknown:
        record["unknownSides"] = unknown
        return record
    lost_entries, new_entries = [], []
    lost = counted(start)
    lost.subtract(counted(seal))
    for (commit, description), count in sorted((item for item in lost.items() if item[1] > 0),
                                                key=lambda item: (-item[1], item[0][0], item[0][1])):
        for _ in range(count):
            lost_entries.append({"commit": commit, "description": description, "cause": "unknown",
                                 "recoverCommand": f"git stash store -m {_shell_quoted(description)} {commit}"})
    new = counted(seal)
    new.subtract(counted(start))
    for (commit, description), count in sorted((item for item in new.items() if item[1] > 0),
                                               key=lambda item: (-item[1], item[0][0], item[0][1])):
        for _ in range(count):
            new_entries.append({"commit": commit, "description": description})
    record.update(
        lostEntries=lost_entries,
        newEntries=new_entries,
        lostCount=len(lost_entries),
        newCount=len(new_entries),
        state="observed",
        changed=bool(lost_entries or new_entries),
    )
    return record


def _conflict_index(records):
    entries = {}
    for record in records:
        for entry in record.get("entries") or []:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise BoardError("INVALID_WORKSPACE", "Malformed scope failure evidence")
            entries.setdefault(entry["path"], entry)
    return entries


def _entry_state_matches(current, expected):
    return current == expected


def _site_conflicts(root, recorded, selected, index):
    """Selected paths matching neither the recorded site nor its authorized state.

    Each path keeps the recorded compare-and-swap — the observed state read, the
    authorized content read, then the observed/authorized index check — while
    one batch numbers the authorized bytes, so the Git process count stays
    independent of the selected path count.
    """
    observed = {path: _observed_file(root, path, mode_hint=(index.get(path) or [None])[0]) for path in selected}
    matches, pending = {}, []
    for path in selected:
        entry = recorded[path]
        if _entry_state_matches(observed[path], entry.get("observed")):
            matches[path] = True
            continue
        authorized = entry.get("authorized")
        if authorized is None:
            matches[path] = observed[path] is None
        elif not isinstance(observed[path], dict) or observed[path].get("mode") != authorized.get("mode"):
            matches[path] = False
        else:
            pending.append(path)
    items = {path: _file_with_git_mode(root, path, recorded[path]["authorized"].get("mode")) for path in pending}
    identities = iter(_batch_blobs(root, [items[path][1] for path in pending if items[path] is not None],
                                   write=False))
    for path in pending:
        item = items[path]
        matches[path] = item is not None and next(identities) == recorded[path]["authorized"].get("oid")
    return [path for path in selected
            if not matches[path] or _entry_state(index, path) not in (recorded[path].get("observedIndex"),
                                                                      recorded[path].get("authorizedIndex"))]


def _write_path(root, path, entry, data=None, *, links=None):
    if _WINDOWS:
        _relative(path)
        target = Path(root) / path
        if data is None and entry is not None and entry["mode"] != "120000":
            data = _git(root, "cat-file", "blob", entry["oid"])
        windows_paths.restore(target, None if entry is None or entry["mode"] == "120000" else data,
                              link=links[path] if entry is not None and entry["mode"] == "120000" else None,
                              repository=root)
        return
    with _parent(root, path, create=True) as (parent, name):
        try:
            os.unlink(name, dir_fd=parent)
        except FileNotFoundError:
            pass
        except IsADirectoryError as error:
            raise BoardError("WORKSPACE_UNSUPPORTED", "A managed path is now a directory", path=path) from error
        if entry is None:
            return
        if data is None:
            data = _git(root, "cat-file", "blob", entry["oid"])
        if entry["mode"] == "120000":
            os.symlink(os.fsdecode(data), name, dir_fd=parent)
        else:
            mode = 0o755 if entry["mode"] == "100755" else 0o644
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                os.fchmod(stream.fileno(), mode)


def _restore_index(root, entries):
    """Set exact index entries in one plumbing call; never refresh the worktree."""
    payload = b''.join(
        (b"0 " + b"0" * 40 if entry is None else entry["mode"].encode() + b" " + entry["oid"].encode())
        + b"\t" + os.fsencode(path) + b"\0" for path, entry in sorted(entries.items()))
    if payload:
        _git(root, "update-index", "-z", "--index-info", data=payload)


def _blocking_paths(root, manifest, observation, *, state_dir):
    try:
        _output_entries(root, manifest, observation, state_dir=state_dir)
    except BoardError as error:
        if error.code != "WORKSPACE_SCOPE_VIOLATION":
            raise
        return list(error.details.get("paths") or [])
    return []


def _reset_site(root, manifest, observation):
    """Return every managed path to the authorized input content and index state."""
    initial = _entries(root, manifest["inputCommit"])
    initial_index = _entries(root, manifest["snapshot"]["stagedTree"] if manifest["kind"] == "existing" else manifest["inputTree"])
    excluded = manifest["snapshot"]["excludedEntries"] if manifest["kind"] == "existing" else {}
    current_index = _entries(root)
    selected = set(observation["tracked"]) | set(initial) | set(current_index) | set(initial_index)
    for path, fingerprint in observation["untracked"].items():
        if path in excluded and excluded[path] == fingerprint:
            continue
        selected.add(path)
    ordered = sorted(selected)
    states = {path: _entry_state(initial, path) for path in ordered}
    needed = [path for path in ordered if states[path] is not None]
    contents = iter(_batch_objects(root, [states[path]["oid"] for path in needed]))
    payloads = dict(zip(needed, contents))
    links = _windows_links(root, initial) if _WINDOWS else None
    for path in ordered:
        _write_path(root, path, states[path], data=payloads.get(path), links=links)
    _restore_index(root, {path: _entry_state(initial_index, path) for path in sorted(selected)
                          if _entry_state(current_index, path) != _entry_state(initial_index, path)})
    # An excluded cache was never captured by content, so a modified one cannot be
    # returned mechanically; it is reported instead of being silently accepted.
    remaining = sorted(path for path, fingerprint in excluded.items()
                       if observation["untracked"].get(path) != fingerprint)
    return sorted(selected), remaining


def _snapshot_record(manifest, observation, tree, entries, changed, excluded_changes, task_id, attempt_id, commit, patch_data):
    return {"version": 1, "headCommit": observation["head"], "indexSha256": _sha(_json(observation["index"])),
            "observationSha256": observation["fingerprint"], "tree": tree, "changedPaths": changed,
            "excludedChangedPaths": excluded_changes, "manifestSha256": manifest["manifestSha256"],
            "includedUntracked": sorted(entries.keys() & observation["untracked"].keys()),
            "workspaceId": manifest["workspaceId"], "taskId": task_id, "attemptId": attempt_id,
            "baseCommit": manifest["baseCommit"], "inputCommit": manifest["inputCommit"],
            "commit": commit, "diffSha256": _sha(patch_data)}


def _finish_record(repository, record, directory, *, ref_name, ref_kind, record_name, patch_name, bindings):
    expected_tree = _line(repository, "rev-parse", record["commit"] + "^{tree}")
    expected_ref = f"refs/hey-my-buddy/workspaces/{record['workspaceId']}/{ref_kind}/{ref_name}"
    if (expected_tree != record["tree"] or _sha(_json(record["snapshot"])) != record["snapshotSha256"]
            or any(record.get(key) != record["snapshot"].get(key) for key in bindings)
            or record["ref"] != expected_ref or record["diffPath"] != str(directory / patch_name)):
        raise BoardError("WORKSPACE_CONFLICT", "The resolved workspace record is inconsistent")
    patch_path = directory / patch_name
    patch_data = _read(patch_path)
    if patch_data is None:
        patch_data = _diff(repository, record["inputCommit"], record["commit"])
    if _sha(patch_data) != record["diffSha256"]:
        raise BoardError("WORKSPACE_CONFLICT", "The immutable resolved diff changed", path=str(patch_path))
    _write_once(patch_path, patch_data)
    _pin(repository, record["ref"], record["commit"])
    _write_once(directory / record_name, _json(record))
    return record


_RECORD_BINDINGS = ("workspaceId", "taskId", "attemptId", "manifestSha256", "baseCommit", "inputCommit",
                    "commit", "tree", "changedPaths", "includedUntracked", "diffSha256")


def _validate_resolution_record_schema(record):
    """Keep the field meaning of each immutable resolution format distinct."""
    if not isinstance(record, dict) or type(record.get("version")) is not int or record["version"] not in (1, 2, 3):
        raise BoardError("WORKSPACE_CONFLICT", "Unsupported workspace resolution record version")
    kind, action = record.get("kind"), record.get("action")
    if not ((kind == "resolution" and action in ("restore", "adopt"))
            or (kind == "abandoned-site" and action == "abandon")):
        raise BoardError("WORKSPACE_CONFLICT", "Invalid workspace resolution record kind or action")
    explanation = "note" if record["version"] == 3 else "reason"
    other = "reason" if explanation == "note" else "note"
    if (explanation not in record or other in record
            or record[explanation] is not None and not isinstance(record[explanation], str)):
        raise BoardError("WORKSPACE_CONFLICT", "Invalid workspace resolution record explanation")
    if record["version"] == 1:
        if not isinstance(record.get("actor"), str) or not record["actor"].strip():
            raise BoardError("WORKSPACE_CONFLICT", "Invalid historical workspace resolution actor")
    else:
        _actor_identity(record.get("actor"), "Workspace resolution actor")


def _replay_record(directory, names, *, task_id, attempt_id, manifest):
    """An already published record of the same identity, or None."""
    for name in names:
        record = _record(directory / name)
        if record is None:
            continue
        _validate_resolution_record_schema(record)
        if (record.get("taskId") != task_id or record.get("attemptId") != attempt_id
                or record.get("manifestSha256") != manifest["manifestSha256"]
                or record.get("workspaceId") != manifest["workspaceId"]):
            raise BoardError("WORKSPACE_CONFLICT", "This resolution identity belongs to a different attempt or input")
        return record
    return None


def _resolution_record(root, manifest, repository, directory, observation, *, state_dir, task_id, attempt_id, action, actor, note, conflict_fingerprint):
    workspace_id = manifest["workspaceId"]
    output_id = _sha(_json([task_id, attempt_id]))
    names = (f"resolve-{action}.json", f"resolve-{action}-pending.json")
    existing = _replay_record(directory, names, task_id=task_id, attempt_id=attempt_id, manifest=manifest)
    if existing is not None:
        return _finish_record(repository, existing, directory, ref_name=f"{output_id}-{action}",
                              ref_kind="resolutions", record_name=f"resolve-{action}.json",
                              patch_name=f"resolve-{action}.patch", bindings=_RECORD_BINDINGS)
    entries, changed, excluded_changes, adopted = _output_entries(root, manifest, observation,
                                                                  allow_outside_scope=(action == "adopt"), state_dir=state_dir)
    tree = _tree(root, entries, state_dir=state_dir)
    changed = sorted(_tree_changes(root, manifest["inputTree"], tree))
    if observation != _observe(root, manifest["snapshot"]["executionSelectors"], state_dir=state_dir):
        raise BoardError("WORKSPACE_CHANGED", "The workspace changed during resolution sealing")
    commit = manifest["inputCommit"] if tree == manifest["inputTree"] else _commit_tree(root, tree, manifest["inputCommit"], workspace_id + " resolution " + output_id)
    patch_data = _diff(repository, manifest["inputCommit"], commit)
    snapshot = _snapshot_record(manifest, observation, tree, entries, changed, excluded_changes, task_id, attempt_id, commit, patch_data)
    snapshot["adoptedPaths"] = adopted
    record = {"version": 3, "kind": "resolution", "action": action, "actor": actor, "note": note,
              "conflictFingerprint": conflict_fingerprint, "workspaceId": workspace_id, "taskId": task_id,
              "attemptId": attempt_id, "manifestSha256": manifest["manifestSha256"],
              "baseCommit": manifest["baseCommit"], "inputCommit": manifest["inputCommit"], "commit": commit,
              "tree": tree, "changedPaths": changed, "adoptedPaths": adopted,
              "includedUntracked": snapshot["includedUntracked"],
              "snapshot": snapshot, "snapshotSha256": _sha(_json(snapshot)),
              "ref": f"refs/hey-my-buddy/workspaces/{workspace_id}/resolutions/{output_id}-{action}",
              "diffPath": str(directory / f"resolve-{action}.patch"), "diffSha256": _sha(patch_data)}
    _pin(repository, f"refs/hey-my-buddy/workspaces/{workspace_id}/retained/resolution-{output_id}-{action}", commit)
    _write_once(directory / f"resolve-{action}-pending.json", _json(record))
    _write_once(directory / f"resolve-{action}.patch", patch_data)
    return _finish_record(repository, record, directory, ref_name=f"{output_id}-{action}", ref_kind="resolutions",
                          record_name=f"resolve-{action}.json", patch_name=f"resolve-{action}.patch",
                          bindings=_RECORD_BINDINGS)


def _abandoned_record(root, manifest, repository, directory, observation, *, state_dir, task_id, attempt_id, actor, note, conflict_fingerprint):
    """Preserve the abandoned site as immutable evidence before returning input."""
    workspace_id = manifest["workspaceId"]
    output_id = _sha(_json([task_id, attempt_id]))
    names = ("abandoned.json", "abandoned-pending.json")
    existing = _replay_record(directory, names, task_id=task_id, attempt_id=attempt_id, manifest=manifest)
    if existing is not None:
        return _finish_record(repository, existing, directory, ref_name=output_id, ref_kind="abandoned",
                              record_name="abandoned.json", patch_name="abandoned.patch", bindings=_RECORD_BINDINGS)
    entries = dict(observation["tracked"])
    pending = []
    for path in observation["untracked"]:
        item = _file(root, path)
        if item is None:
            raise BoardError("WORKSPACE_CHANGED", "A managed path disappeared during evidence capture", path=path)
        pending.append((path, item))
    for (path, item), oid in zip(pending, _batch_blobs(root, [item[1] for _path, item in pending], write=True, state_dir=state_dir)):
        entries[path] = [item[0], oid]
    tree = _tree(root, entries, state_dir=state_dir)
    changed = sorted(_tree_changes(root, manifest["inputTree"], tree))
    commit = _commit_tree(root, tree, manifest["inputCommit"], workspace_id + " abandoned " + output_id)
    patch_data = _diff(repository, manifest["inputCommit"], commit)
    snapshot = _snapshot_record(manifest, observation, tree, entries, changed, [], task_id, attempt_id, commit, patch_data)
    record = {"version": 3, "kind": "abandoned-site", "action": "abandon", "actor": actor, "note": note,
              "conflictFingerprint": conflict_fingerprint, "workspaceId": workspace_id, "taskId": task_id,
              "attemptId": attempt_id, "manifestSha256": manifest["manifestSha256"],
              "baseCommit": manifest["baseCommit"], "inputCommit": manifest["inputCommit"], "commit": commit,
              "tree": tree, "changedPaths": changed, "includedUntracked": snapshot["includedUntracked"],
              "snapshot": snapshot, "snapshotSha256": _sha(_json(snapshot)),
              "ref": f"refs/hey-my-buddy/workspaces/{workspace_id}/abandoned/{output_id}",
              "diffPath": str(directory / "abandoned.patch"), "diffSha256": _sha(patch_data)}
    _pin(repository, f"refs/hey-my-buddy/workspaces/{workspace_id}/retained/abandoned-{output_id}", commit)
    _write_once(directory / "abandoned-pending.json", _json(record))
    _write_once(directory / "abandoned.patch", patch_data)
    return _finish_record(repository, record, directory, ref_name=output_id, ref_kind="abandoned",
                          record_name="abandoned.json", patch_name="abandoned.patch", bindings=_RECORD_BINDINGS)


def _resolution_result(action, state, artifact, resolved, preserved, remaining, observed_fingerprint, resolved_fingerprint):
    return {
        "action": action,
        "state": state,
        "artifact": artifact,
        "resolvedPaths": sorted(resolved),
        "preservedPaths": sorted(preserved),
        "remainingPaths": sorted(remaining),
        "observedFingerprint": observed_fingerprint,
        "resolvedFingerprint": resolved_fingerprint,
    }


def resolve(state_dir, manifest: dict, *, task_id: str, attempt_id: str, action: str, paths, observed_fingerprint: str, note: str | None = None, actor: dict | None = None):
    """Mechanically settle one recorded out-of-scope failure site.

    ``restore`` returns the selected paths to their authorized content while
    preserving every other legal change, ``adopt`` explicitly promotes the exact
    recorded site into a new immutable output, and ``abandon`` preserves the whole
    failed site as evidence and returns the checkout to its authorized input state.
    Compare-and-swap failures preserve the site and report the conflicting paths.
    All Git work happens here, outside any database transaction; the caller
    rechecks owner, revision and workspace identity before recording the result.
    """
    with _errors():
        _validate_manifest(manifest)
        actor = _actor_identity(actor, "Workspace resolution actor")
        if action not in RESOLUTION_ACTIONS:
            raise BoardError("INVALID_WORKSPACE", "Resolution action must be restore, adopt or abandon")
        if not isinstance(observed_fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", observed_fingerprint) is None:
            raise BoardError("INVALID_WORKSPACE", "observedFingerprint must be a workspace fingerprint")
        if paths is None:
            selected_paths = []
        elif isinstance(paths, list) and all(isinstance(value, str) for value in paths):
            selected_paths = sorted({_relative(value) for value in paths})
        else:
            raise BoardError("INVALID_WORKSPACE", "paths must be a list of relative workspace paths")
        workspace_id = manifest["workspaceId"]
        workspace_dir = Path(state_dir).resolve() / "workspaces" / workspace_id
        if _record(workspace_dir / "manifest.json") != manifest:
            raise BoardError("WORKSPACE_CONFLICT", "This state directory does not own the supplied workspace manifest")
        with _lock(workspace_dir):
            output_id = _sha(_json([task_id, attempt_id]))
            directory = workspace_dir / "outputs" / output_id
            _mkdir(directory)
            records = _scope_records(workspace_dir, manifest, task_id, attempt_id)
            if not records:
                raise BoardError("WORKSPACE_CONFLICT", "No recorded scope failure evidence exists for this attempt", attemptId=attempt_id)
            entries = _conflict_index(records)
            blocking = sorted({path for record in records for path in record.get("blockingPaths") or []})
            root = Path(manifest["checkoutRoot"])
            repository = Path(manifest["snapshot"]["repositoryPath"])
            if manifest["repositoryId"] != _identity(repository):
                raise BoardError("WORKSPACE_CHANGED", "The output repository identity changed")
            _verify(manifest, require_unchanged=False)
            observation = _stable_observation(root, manifest["snapshot"]["executionSelectors"], state_dir=state_dir)
            if observation["fingerprint"] != observed_fingerprint:
                raise BoardError("WORKSPACE_CHANGED", "The workspace does not match the fingerprint the Host observed",
                                 observedFingerprint=observed_fingerprint, currentFingerprint=observation["fingerprint"])
            if action == "adopt":
                if selected_paths:
                    raise BoardError("INVALID_WORKSPACE", "Adoption binds the whole recorded site and takes no path selection")
                if observed_fingerprint != records[0]["observedFingerprint"]:
                    raise BoardError("WORKSPACE_CONFLICT", "Adoption must bind the exact recorded failure site",
                                     recordedFingerprint=records[0]["observedFingerprint"])
                record = _resolution_record(root, manifest, repository, directory, observation,
                                            task_id=task_id, attempt_id=attempt_id, action="adopt", actor=actor,
                                            note=note, conflict_fingerprint=records[0]["observedFingerprint"], state_dir=state_dir)
                return _resolution_result("adopt", "adopted", record, blocking, [], [],
                                          observation["fingerprint"], record["snapshot"]["observationSha256"])
            if action == "abandon":
                if selected_paths:
                    raise BoardError("INVALID_WORKSPACE", "Abandon returns the whole site and takes no path selection")
                evidence = _abandoned_record(root, manifest, repository, directory, observation,
                                             task_id=task_id, attempt_id=attempt_id, actor=actor,
                                             note=note, conflict_fingerprint=records[0]["observedFingerprint"], state_dir=state_dir)
                reset, remaining = _reset_site(root, manifest, observation)
                after = _stable_observation(root, manifest["snapshot"]["executionSelectors"], state_dir=state_dir)
                remaining = sorted(set(remaining) | set(_blocking_paths(root, manifest, after, state_dir=state_dir)))
                return _resolution_result("abandon", "abandoned" if not remaining else "open", evidence,
                                          reset, [], remaining, observation["fingerprint"], after["fingerprint"])
            selected = selected_paths or blocking
            unknown = [path for path in selected if path not in entries]
            if unknown:
                raise BoardError("INVALID_WORKSPACE", "Selected paths have no recorded failure evidence", paths=unknown[:32])
            if not selected:
                raise BoardError("WORKSPACE_CONFLICT", "The recorded failure site has no blocking path to restore")
            index = _entries(root)
            conflicting = _site_conflicts(root, entries, selected, index)
            if conflicting:
                raise BoardError("WORKSPACE_CONFLICT", "Selected paths changed after the recorded failure; the site is preserved",
                                 conflictingPaths=sorted(conflicting)[:32])
            authorized = {path: entries[path].get("authorized") for path in selected}
            needed = [path for path in selected if authorized[path] is not None]
            contents = iter(_batch_objects(root, [authorized[path]["oid"] for path in needed]))
            payloads = dict(zip(needed, contents))
            links = _windows_links(root, _entries(root, manifest["inputCommit"])) if _WINDOWS else None
            for path in selected:
                _write_path(root, path, authorized[path], data=payloads.get(path), links=links)
            _restore_index(root, {path: entries[path].get("authorizedIndex") for path in selected
                                  if _entry_state(index, path) != entries[path].get("authorizedIndex")})
            after = _stable_observation(root, manifest["snapshot"]["executionSelectors"], write=True, state_dir=state_dir)
            remaining = _blocking_paths(root, manifest, after, state_dir=state_dir)
            preserved = sorted(set(entries) - set(selected))
            if remaining:
                # A partial restore is progress, not a completed baseline: the
                # remaining out-of-scope paths stay visible for another decision.
                return _resolution_result("restore", "open", None, selected, preserved, remaining,
                                          observation["fingerprint"], after["fingerprint"])
            record = _resolution_record(root, manifest, repository, directory, after,
                                        task_id=task_id, attempt_id=attempt_id, action="restore", actor=actor,
                                        note=note, conflict_fingerprint=records[0]["observedFingerprint"], state_dir=state_dir)
            return _resolution_result("restore", "restored", record, selected, preserved, [],
                                      observation["fingerprint"], record["snapshot"]["observationSha256"])


# -- integration verification -------------------------------------------------
def _is_ancestor(root, ancestor, descendant):
    try:
        _git(root, "merge-base", "--is-ancestor", ancestor, descendant)
    except BoardError as error:
        if error.code == "WORKSPACE_GIT_UNKNOWN":
            raise
        return False
    return True


def _tree_changes(root, before_tree, after_tree, *, env=None):
    """Read just the changed paths and their output modes/objects in one Git call."""
    records = _git(root, "diff-tree", "-r", "-z", "--raw", "--no-renames", "--no-abbrev",
                   before_tree, after_tree, "--", env=env).split(b"\0")
    changes = {}
    for metadata, name in zip(records[::2], records[1::2]):
        _before_mode, mode, _before_oid, oid, _status = metadata[1:].decode().split()
        changes[_relative(os.fsdecode(name))] = None if mode == "000000" else {"mode": mode, "oid": oid}
    return changes


def _validate_output_record(final_input, artifact):
    """Validate the retained output record without observing Git or the checkout."""
    if not isinstance(artifact, dict) or not isinstance(artifact.get("snapshot"), dict):
        raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The sealed output snapshot is missing")
    snapshot = artifact["snapshot"]
    bindings = ("workspaceId", "taskId", "attemptId", "manifestSha256", "baseCommit", "inputCommit",
                "commit", "tree", "changedPaths", "includedUntracked", "diffSha256")
    if (_sha(_json(snapshot)) != artifact.get("snapshotSha256")
            or any(artifact.get(key) != snapshot.get(key) for key in bindings)
            or artifact.get("manifestSha256") != final_input["manifestSha256"]
            or artifact.get("workspaceId") != final_input["workspaceId"]
            or artifact.get("inputCommit") != final_input["inputCommit"]):
        raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The final output does not bind its input manifest")
    commit = artifact.get("commit")
    ref = artifact.get("ref")
    output_id = _sha(_json([artifact.get("taskId"), artifact.get("attemptId")]))
    if artifact.get("kind") == "resolution":
        _validate_resolution_record_schema(artifact)
        expected_ref = (f"refs/hey-my-buddy/workspaces/{final_input['workspaceId']}/resolutions/"
                        f"{output_id}-{artifact['action']}")
    elif artifact.get("kind") == "host-output":
        expected_ref = f"refs/hey-my-buddy/workspaces/{final_input['workspaceId']}/outputs/{output_id}-host"
    elif artifact.get("kind") is None:
        expected_ref = f"refs/hey-my-buddy/workspaces/{final_input['workspaceId']}/outputs/{output_id}"
    else:
        raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The final output record kind is invalid")
    patch_path = artifact.get("diffPath")
    if (not isinstance(commit, str) or not isinstance(ref, str) or ref != expected_ref
            or not isinstance(patch_path, str) or not is_absolute_path(patch_path)):
        raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The final output record is inconsistent")


def _artifact_binding(original_input, final_input, artifact):
    """Prove both input manifests and the final output against fixed Git objects."""
    _validate_output_record(final_input, artifact)
    repository = Path(original_input["snapshot"]["repositoryPath"])
    if (original_input["repositoryId"] != _identity(repository)
            or final_input["repositoryId"] != original_input["repositoryId"]
            or final_input["checkoutId"] != original_input["checkoutId"]
            or final_input["snapshot"]["repositoryPath"] != str(repository)):
        raise BoardError("WORKSPACE_CHANGED", "The artifact input repository or checkout identity changed")
    trees = {}
    def check_tree(commit, expected):
        if commit not in trees:
            trees[commit] = _line(repository, "rev-parse", commit + "^{tree}")
        if trees[commit] != expected:
            raise BoardError("WORKSPACE_MANIFEST_CHANGED", "A pinned input tree is inconsistent")

    for manifest in (original_input, final_input):
        snapshot = manifest["snapshot"]
        check_tree(manifest["inputCommit"], manifest["inputTree"])
        check_tree(snapshot["stagedCommit"], snapshot["stagedTree"])
    commit = artifact["commit"]
    patch = _read(Path(artifact["diffPath"]))
    if commit not in trees:
        trees[commit] = _line(repository, "rev-parse", commit + "^{tree}")
    if trees[commit] != artifact.get("tree") or patch is None or _sha(patch) != artifact.get("diffSha256"):
        raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The final output Git objects or patch changed")
    if commit != final_input["inputCommit"]:
        parents = _line(repository, "rev-list", "--parents", "-n", "1", commit).split()
        if parents != [commit, final_input["inputCommit"]]:
            raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The final output commit has the wrong input parent")
    return repository, _tree_changes(repository, original_input["inputTree"], artifact["tree"])


def verify_sealed_output(original_input: dict, final_input: dict, artifact: dict) -> dict:
    """Prove one sealed output/resolution record against its fixed Git objects.

    The record's commit, tree, parent binding and patch hash are checked against
    immutable Git objects; any drift raises. The
    returned changed paths are the whole-micro-task delta the record provably carries,
    so a caller can trust exactly the paths this record names.
    """
    with _errors():
        _validate_manifest(original_input)
        if final_input is not original_input:
            _validate_manifest(final_input)
        _repository, changed = _artifact_binding(original_input, final_input, artifact)
        return {"changedPaths": sorted(changed)}


def integration_verify(artifact: dict, *, original_input: dict, final_input: dict,
                       path: str, ref: str, before_commit: str | None = None,
                       repository_id: str | None = None, checkout_id: str | None = None,
                       host_paths=None, note: str | None = None,
                       _confirm_adjustments: bool = False) -> dict:
    """Verify that one immutable artifact is actually present in a real target.

    The target checkout identity, the resolved ``ref`` commit and both before and
    after trees come from the repository itself, never from the caller. The
    artifact is bound by the blob identity of every changed path, so a cherry-pick
    or an explicitly adjusted integration is verified by content rather than by a
    client-supplied SHA. ``before_commit`` is only needed to prove real Host
    additions inside one commit interval (``host_paths``); without it the check is
    the pure content comparison of the artifact against the target tree.
    """
    with _errors():
        _validate_manifest(original_input)
        if final_input is not original_input:
            _validate_manifest(final_input)
        if not isinstance(artifact, dict):
            raise BoardError("INVALID_WORKSPACE", "The integration source artifact is missing")
        if bool(host_paths) and not before_commit:
            raise BoardError("INVALID_WORKSPACE", "Host paths need a before/after commit interval to verify")
        _repository, changed = _artifact_binding(original_input, final_input, artifact)
        if before_commit is not None and (not isinstance(before_commit, str) or not before_commit):
            raise BoardError("INVALID_WORKSPACE", "beforeCommit must name the target commit before the integration")
        host_paths = sorted({_relative(value) for value in (host_paths or [])})
        overlap = sorted(set(host_paths) & set(changed))
        if overlap:
            raise BoardError("INVALID_WORKSPACE", "Host paths must be separate from artifact output paths", paths=overlap[:32])
        identity = inspect(path)
        if repository_id is not None and repository_id != identity["repositoryId"]:
            raise BoardError("WORKSPACE_CHANGED", "The integration target repository changed",
                             expectedRepositoryId=repository_id, actualRepositoryId=identity["repositoryId"])
        if checkout_id is not None and checkout_id != identity["checkoutId"]:
            raise BoardError("WORKSPACE_CHANGED", "The integration target checkout changed",
                             expectedCheckoutId=checkout_id, actualCheckoutId=identity["checkoutId"])
        root = Path(identity["checkoutRoot"])
        before = _commit(root, before_commit) if before_commit else None
        before_tree = _line(root, "rev-parse", before + "^{tree}") if before else None
        after = _commit(root, ref)
        after_tree = _line(root, "rev-parse", after + "^{tree}")
        target_changes = _tree_changes(root, before_tree, after_tree) if host_paths else {}
        missing_host = sorted(set(host_paths) - set(target_changes))
        if missing_host:
            raise BoardError("INVALID_WORKSPACE", "Host paths must be actual changes in the target commit interval", paths=missing_host[:32])
        source = artifact.get("commit")
        artifact_ancestor = _is_ancestor(root, source, after) if isinstance(source, str) and source else False
        matching, differing, missing = [], [], []
        # Read the target delta against the artifact tree. The source object
        # directory makes that immutable tree readable in an independent target
        # repository without copying objects or listing either complete tree.
        target_changes = _tree_changes(root, artifact["tree"], after_tree, env={
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": json.dumps(str(_repository / "objects"), ensure_ascii=False),
        })
        for name in sorted(changed):
            expected = changed[name]
            if expected is not None:
                expected = [expected["mode"], expected["oid"]]
            actual = target_changes.get(name, changed[name])
            if expected is None:
                if actual is None:
                    matching.append(name)
                else:
                    differing.append({"path": name, "artifactOid": None, "targetOid": actual["oid"],
                                      "targetMode": actual["mode"]})
                continue
            if actual is None:
                missing.append(name)
            elif actual == {"mode": expected[0], "oid": expected[1]}:
                matching.append(name)
            else:
                differing.append({"path": name, "artifactMode": expected[0], "artifactOid": expected[1],
                                  "targetMode": actual["mode"], "targetOid": actual["oid"]})
        adjustments = sorted({item["path"] for item in differing} | set(missing)) if _confirm_adjustments else []
        if adjustments and not (isinstance(note, str) and note.strip()):
            raise BoardError("INVALID_WORKSPACE", "An adjusted integration requires an explicit note")
        unrecorded = sorted({item["path"] for item in differing} - set(adjustments)) + sorted(set(missing) - set(adjustments))
        verification = {
            "verified": bool((before is None or _is_ancestor(root, before, after)) and not unrecorded),
            "strategy": "patch",
            "target": {"kind": "checkout", "path": identity["checkoutRoot"], "checkoutId": identity["checkoutId"],
                       "repositoryId": identity["repositoryId"], "ref": ref},
            "beforeCommit": before, "beforeTree": before_tree, "afterCommit": after, "afterTree": after_tree,
            "sourceCommit": source, "sourceTree": artifact.get("tree"),
            "artifactAncestor": artifact_ancestor,
            "matchingPaths": matching,
            "differingPaths": differing,
            "missingPaths": missing,
            "adjustments": adjustments,
            "hostPaths": host_paths,
            "unrecordedPaths": unrecorded,
            "note": note,
        }
        return verification


# -- cleanup of one registered disposable checkout ----------------------------
def _workspace_identifier(value):
    return (isinstance(value, str) and value.startswith("ws-") and len(value) == 35
            and all(character in "0123456789abcdef" for character in value[3:]))


def allocation_lock_directory(state_dir, workspace_id: str | None) -> Path | None:
    """Return an existing private record directory with its existing lock file.

    Both deletion marking and ordinary reclaim use this exact predicate. It
    never creates a new lock.
    """
    if not _workspace_identifier(workspace_id):
        return None
    directory = Path(state_dir).resolve() / "workspaces" / workspace_id
    lock_path = directory / ".lock"
    return directory if directory.is_dir() and lock_path.is_file() else None


def _attached_branch_report(checkout_root, record) -> dict:
    """Identify an attached branch and the command to detach this checkout's HEAD."""
    ref = record.get("branch") if isinstance(record.get("branch"), str) else ""
    head = record.get("HEAD") if isinstance(record.get("HEAD"), str) else ""
    name = ref.removeprefix("refs/heads/")
    report = {
        "branch": name or None,
        "ref": ref or None,
        "headCommit": head or None,
    }
    if head:
        report["detachCommand"] = f"git -C {_shell_quoted(str(checkout_root))} switch --detach {_shell_quoted(head)}"
    return report


def _path_present(path: Path) -> bool:
    """True while anything still occupies this exact path, including a dangling link.

    Cleanup convergence asks whether the deletion target is gone, not whether it
    resolves: a broken symlink left at the managed path is still an occupant that a
    later proof must refuse, not an achieved removal.
    """
    return path.exists() or path.is_symlink()


def _inside(path, root):
    """True when an absolute path stays inside one known checkout root."""
    if not isinstance(path, str) or not path or "\0" in path:
        return False
    try:
        return Path(path).resolve().is_relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return False


def _allocation_candidates(manifest, retained):
    """The current turn/input manifest plus the authoritative retained material."""
    values = [manifest]
    for candidate in retained or ():
        if isinstance(candidate, dict) and candidate not in values:
            values.append(candidate)
    return values


def resolve_allocation(state_dir, manifest: dict, retained=None) -> dict | None:
    """Resolve the physical managed-worktree allocation that owns one manifest's checkout.

    The current manifest may be a later turn/input snapshot of an earlier
    allocation: a continuation reuses the same physical checkout under a new
    logical workspace id, and a borrowed existing workspace never owns anything.
    Provenance therefore comes only from material the board already retained
    (``retained``: pinned input artifacts, canonical turn inputs and prepared
    continuation manifests), never from a cwd that merely sits under
    ``state/workspaces``. A candidate proves the allocation only when all of these
    hold together: the same repositoryId/checkoutId, the allocation's own
    immutable manifest record under its own workspace directory, the exact
    ``<state>/workspaces/<allocationId>/checkout`` path and a checkout inside that
    path. The caller rechecks the Git ``hey-my-buddy:<allocationId>`` lock against the
    returned identity before any deletion.
    """
    with _errors():
        _validate_manifest(manifest)
        return _resolve_allocation(state_dir, manifest, retained)


def _resolve_allocation(state_dir, manifest, retained=None):
    """Resolve ownership using the input manifest validated by the caller."""
    with _errors():
        state_dir = Path(state_dir).resolve()
        checkout_root = manifest["checkoutRoot"]
        for candidate in _allocation_candidates(manifest, retained):
            if candidate.get("kind") != "worktree":
                continue
            workspace_id = candidate.get("workspaceId")
            if not _workspace_identifier(workspace_id):
                continue
            if (candidate.get("checkoutId") != manifest["checkoutId"]
                    or candidate.get("repositoryId") != manifest["repositoryId"]
                    or candidate.get("checkoutRoot") != checkout_root
                    or not _inside(candidate.get("path"), checkout_root)):
                continue
            directory = state_dir / "workspaces" / workspace_id
            if not directory.is_dir():
                continue
            if _record(directory / "manifest.json") != candidate:
                continue
            if str(directory / "checkout") != checkout_root:
                continue
            return {"workspaceId": workspace_id, "path": checkout_root, "kind": "worktree",
                    "checkoutId": candidate["checkoutId"], "repositoryId": candidate["repositoryId"],
                    "manifestSha256": candidate.get("manifestSha256"),
                    "manifestWorkspaceId": manifest["workspaceId"]}
        return None


def _allocation_refs(repository, allocation_id, manifest, retained):
    """Complete retention view of every fixed ref that belongs to this checkout."""
    identifiers = list(dict.fromkeys((allocation_id, manifest["workspaceId"])))
    for candidate in _allocation_candidates(manifest, retained):
        value = candidate.get("workspaceId")
        if ((candidate.get("checkoutId") == manifest["checkoutId"])
                and _workspace_identifier(value)
                and value not in identifiers):
            identifiers.append(value)
    refs = []
    for identifier in identifiers:
        refs.extend(os.fsdecode(line) for line in _git(
            repository, "for-each-ref", "--format=%(refname)", f"refs/hey-my-buddy/workspaces/{identifier}/").splitlines())
    return sorted(set(refs))


def _cleanup_resume_registration(repository, checkout_root, allocation, manifest):
    """Prove the surviving registration even when removal erased the gitfile."""
    def refuse(reason):
        raise BoardError("WORKSPACE_UNSAFE", "The started cleanup's allocation cannot be proved",
                         reasons=[reason], path=str(checkout_root))

    if checkout_root.is_symlink() or checkout_root.is_junction() or not checkout_root.is_dir():
        refuse("unsafe-path")
    if _identity(repository) != manifest["repositoryId"]:
        refuse("identity-changed")
    record = _worktree_record(repository, checkout_root)
    if record is None:
        if not any(checkout_root.iterdir()):
            return None, None
        refuse("unregistered-checkout")
    git_dir = None
    for candidate in (repository / "worktrees").iterdir():
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        if _identity(candidate) == manifest["checkoutId"]:
            git_dir = candidate
            break
    if git_dir is None:
        refuse("identity-changed")
    backlink = _read(git_dir / "gitdir")
    if backlink is None:
        refuse("identity-changed")
    pointed_back = Path(os.fsdecode(backlink).strip())
    if not is_absolute_path(pointed_back):
        pointed_back = git_dir / pointed_back
    if pointed_back.resolve() != (checkout_root / ".git").resolve():
        refuse("identity-changed")
    pointer_path = checkout_root / ".git"
    if _path_present(pointer_path):
        if pointer_path.is_symlink() or not pointer_path.is_file():
            refuse("identity-changed")
        pointer = os.fsdecode(_read(pointer_path)).strip()
        if not pointer.startswith("gitdir: "):
            refuse("identity-changed")
        pointed = Path(pointer[len("gitdir: "):])
        if not is_absolute_path(pointed):
            pointed = checkout_root / pointed
        if pointed.resolve() != git_dir.resolve():
            refuse("identity-changed")
    reason = "hey-my-buddy:" + allocation["workspaceId"]
    if "locked" in record and record["locked"] != reason:
        refuse("lock-mismatch")
    if "locked" not in record:
        _git(repository, "worktree", "lock", "--reason", reason, str(checkout_root))
        record["locked"] = reason
    return record, git_dir


def _cleanup_restore(checkout_root, repository, git_dir, manifest, sealed):
    """Restore only missing retained files, then use the ordinary cleanup proof."""
    if not _path_present(checkout_root / ".git"):
        _write_once(checkout_root / ".git", os.fsencode(f"gitdir: {git_dir}\n"), repository=checkout_root)
    tree = sealed["tree"] if sealed is not None else manifest["inputTree"]
    entries = _entries(repository, tree)
    missing = {}
    for path, entry in entries.items():
        try:
            with _parent(checkout_root, path) as (parent, name):
                if _WINDOWS:
                    handle = parent.open_leaf(name, action="inspect")
                    parent.close(handle)
                else:
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            missing[path] = entry
    # _materialize pins parents without following links and creates each leaf
    # exclusively. A file that appears meanwhile is preserved and stops recovery.
    _materialize(checkout_root, missing, tree=entries)


def cleanup_inspect(state_dir, manifest: dict, sealed: dict | None = None, retained=None,
                    allowed: list[str] | None = None, *, resume=False) -> dict:
    """Eligibility facts for removing exactly one registered hey-my-buddy worktree.

    The deletion target is the *physical allocation* resolved from authoritative
    retained manifests, not the current turn/input snapshot: a continuation
    reuses the same checkout under a new logical workspace id, so the original
    worktree manifest, the exact allocation path and the original
    ``hey-my-buddy:<allocationId>`` Git lock are what authorize a removal. An arbitrary
    existing cwd under ``state/workspaces`` proves nothing by itself: the source
    checkout, a borrowed directory, a sibling worktree and an unknown path are all
    reported as retention reasons instead of being deleted. ``allowed`` names the
    paths a board-bound Host decision already promoted on this manifest; the
    proof never reads local adoption records on its own authority.
    """
    with _errors():
        _validate_manifest(manifest)
        return _cleanup_inspect(state_dir, manifest, sealed, retained, allowed, resume=resume)


def _cleanup_inspect(state_dir, manifest, sealed=None, retained=None, allowed=None, *, resume=False):
    """Inspect removal using the input manifest validated at the read boundary."""
    with _errors():
        state_dir = Path(state_dir).resolve()
        allocation = _resolve_allocation(state_dir, manifest, retained)
        result = {
            "eligible": False, "reasons": [],
            "workspaceId": (allocation or manifest)["workspaceId"],
            "manifestWorkspaceId": manifest["workspaceId"],
            "kind": (allocation or manifest)["kind"],
            "checkoutId": manifest["checkoutId"], "repositoryId": manifest["repositoryId"],
            "path": allocation["path"] if allocation else manifest["checkoutRoot"], "cwd": manifest["path"],
            "allocation": allocation, "worktree": False, "locked": None,
            "unsealedPaths": [], "refs": [], "sealedObservation": None,
            "attachedBranch": None,
        }
        if allocation is None:
            result["reasons"].append("not-a-managed-worktree")
            if manifest["kind"] == "worktree":
                # A worktree manifest that no longer names its own allocation is unsafe.
                if Path(manifest["checkoutRoot"]) != state_dir / "workspaces" / manifest["workspaceId"] / "checkout":
                    result["reasons"].append("unsafe-path")
            return result
        checkout_root = Path(allocation["path"])
        if not _inside(manifest["path"], checkout_root):
            result["reasons"].append("unsafe-path")
        if resume:
            repository = Path(manifest["snapshot"]["repositoryPath"])
            try:
                record, git_dir = _cleanup_resume_registration(repository, checkout_root, allocation, manifest)
            except BoardError as error:
                if error.code != "WORKSPACE_UNSAFE":
                    raise
                result["reasons"].extend(error.details["reasons"])
                return result
            if record is None:
                result["eligible"] = not result["reasons"]
                return result
            _cleanup_restore(checkout_root, repository, git_dir, manifest, sealed)
        try:
            actual = inspect(str(checkout_root))
        except BoardError as error:
            if error.code == "WORKSPACE_GIT_UNKNOWN":
                raise
            result["reasons"].append("checkout-missing")
            return result
        if actual["checkoutRoot"] != manifest["checkoutRoot"]:
            result["reasons"].append("identity-changed")
        for field in ("checkoutId", "repositoryId"):
            if manifest[field] != actual[field]:
                result["reasons"].append("identity-changed")
        try:
            _cleanup_proof(Path(actual["repositoryPath"]), checkout_root, allocation, manifest, retained, sealed,
                           result, allowed=tuple(allowed or ()))
        except BoardError:
            # The registered owner of this exact path may complete the same removal
            # while its eligibility is still being proven. The checkout then holds no
            # Git facts to prove and nothing left to delete, so it reads exactly like
            # the deterministic missing case. A checkout that is still present keeps
            # the original failure instead of hiding it.
            if _path_present(checkout_root):
                raise
            result["reasons"].append("checkout-missing")
            return result
        result["eligible"] = not result["reasons"]
        return result


def _cleanup_proof(repository, checkout_root, allocation, manifest, retained, sealed, result, *, allowed=()) -> None:
    """Fill one cleanup view with the worktree, seal and unsealed-change proof.

    ``allowed`` carries the exact paths earlier Host decisions already promoted on
    this manifest; they stay authorized for the comparison while anything newly
    outside the write scope still reports ``unsealed-changes``.
    """
    record = _worktree_record(repository, checkout_root)
    if record is None:
        # Registration itself is missing: its own reason, never a claim about
        # locks or branches that were not read.
        result["reasons"].append("unregistered-checkout")
    elif record.get("locked") != "hey-my-buddy:" + allocation["workspaceId"]:
        # A worktree someone else locked stays theirs; the mismatch keeps its
        # own reason instead of hiding behind "unregistered".
        result["reasons"].append("lock-mismatch")
    elif "detached" not in record:
        # Registered and locked by exactly this allocation, but HEAD is attached
        # to a branch. Report the branch and the safe detach; never detach,
        # delete or move any branch or reference here.
        result["reasons"].append("attached-branch")
        result["attachedBranch"] = _attached_branch_report(checkout_root, record)
    else:
        result["worktree"] = True
        result["locked"] = record.get("locked")
    result["refs"] = _allocation_refs(repository, allocation["workspaceId"], manifest, retained)
    def fixed_refs_match(include_output):
        snapshot = manifest["snapshot"]
        expected = ((snapshot["inputRef"], manifest["inputCommit"]),
                    (snapshot["stagedRef"], snapshot["stagedCommit"]))
        if include_output:
            expected += ((sealed["ref"], sealed["commit"]),)
        try:
            return all(_commit(repository, ref) == commit for ref, commit in expected)
        except BoardError as error:
            if error.code == "WORKSPACE_GIT_UNKNOWN":
                raise
            return False

    if not isinstance(sealed, dict) or not isinstance(sealed.get("snapshot"), dict):
        try:
            _verify(manifest, require_unchanged=True)
        except BoardError as error:
            if error.code == "WORKSPACE_GIT_UNKNOWN":
                raise
            result["reasons"].append("unsealed-changes")
            return
        if not fixed_refs_match(False):
            result["reasons"].append("unsealed-changes")
        return
    snapshot = sealed["snapshot"]
    result["sealedObservation"] = snapshot.get("observationSha256")
    # Cleanup compares the latest seal with its own prepared input. Integration
    # separately compares the run's original input with this final output.
    if (sealed.get("manifestSha256") != manifest["manifestSha256"]
            or snapshot.get("inputCommit") != manifest["inputCommit"]):
        result["reasons"].append("unsealed-handoff")
        return
    try:
        _artifact_binding(manifest, manifest, sealed)
    except BoardError as error:
        if error.code == "WORKSPACE_GIT_UNKNOWN":
            raise
        result["reasons"].append("sealed-output-invalid")
        return
    if not fixed_refs_match(True):
        result["reasons"].append("sealed-output-invalid")
        return
    observation = _observe(checkout_root, manifest["snapshot"]["executionSelectors"])
    if observation["fingerprint"] == snapshot.get("observationSha256"):
        return
    unsealed = []
    try:
        entries_now, _changed, excluded_changes, _adopted = _output_entries(checkout_root, manifest, observation,
                                                                           allowed_paths=allowed, write=False)
        sealed_entries = _entries(repository, sealed["tree"])
        unsealed = sorted(path for path in entries_now.keys() | sealed_entries.keys()
                          if entries_now.get(path) != sealed_entries.get(path))
        if sorted(excluded_changes) != sorted(snapshot.get("excludedChangedPaths") or []):
            unsealed = sorted(set(unsealed) | set(excluded_changes) | set(snapshot.get("excludedChangedPaths") or []))
    except BoardError as error:
        if error.code == "WORKSPACE_UNSUPPORTED":
            result["reasons"].append("unsealed-changes")
            return
        if error.code != "WORKSPACE_SCOPE_VIOLATION":
            raise
        unsealed = [path for path in (error.details.get("paths") or []) if path not in set(allowed)]
    result["reasons"].append("unsealed-changes")
    result["unsealedPaths"] = unsealed
    return


def deletion_inspect(state_dir, manifest: dict, artifacts: list[dict], *, retained=()) -> dict:
    """Prove deletion of one checkout without restoring an interrupted removal.

    A missing gitfile can be the result of ``git worktree remove`` stopping
    halfway through. In that state only surviving files are compared with
    retained Git trees; missing files are allowed, but an added or changed file
    keeps the checkout in place. The original registration and lock still have
    to prove the exact physical allocation.
    """
    with _errors():
        _validate_manifest(manifest)
        allocation = _resolve_allocation(state_dir, manifest)
        if allocation is None or allocation["workspaceId"] != manifest["workspaceId"]:
            return {"reasons": ["owner-unproven"], "resume": False}
        checkout = Path(allocation["path"])
        repository = Path(manifest["snapshot"]["repositoryPath"])
        related = [item for item in _allocation_candidates(manifest, retained)
                   if isinstance(item, dict) and item.get("repositoryId") == manifest["repositoryId"]
                   and item.get("checkoutId") == manifest["checkoutId"]
                   and item.get("checkoutRoot") == manifest["checkoutRoot"]]
        for item in related:
            if item is not manifest:
                _validate_manifest(item)
        by_hash = {item["manifestSha256"]: item for item in related}
        candidates = [item for item in artifacts if isinstance(item, dict)
                      and item.get("workspaceId") in {value["workspaceId"] for value in related}
                      and item.get("manifestSha256") in by_hash
                      and item.get("workspaceId") == by_hash[item["manifestSha256"]]["workspaceId"]
                      and isinstance(item.get("snapshot"), dict)
                      and isinstance(item.get("tree"), str)]
        pointer_missing = not _path_present(checkout / ".git")
        try:
            record, _git_dir = _cleanup_resume_registration(repository, checkout, allocation, manifest)
        except BoardError as error:
            if error.code == "WORKSPACE_UNSAFE":
                return {"reasons": error.details["reasons"], "resume": True}
            raise
        if record is None:
            return {"reasons": [], "resume": True}
        if "detached" not in record:
            return {"reasons": ["attached-branch"], "resume": True}
        if record.get("HEAD") != manifest["inputCommit"]:
            return {"reasons": ["identity-changed"], "resume": True}
        if not pointer_missing:
            first = None
            for candidate in candidates:
                selected = by_hash[candidate["manifestSha256"]]
                proof = _cleanup_inspect(state_dir, selected, sealed=candidate, retained=related)
                if proof["eligible"]:
                    return {"reasons": [], "resume": True}
                if first is None:
                    first = proof
            for selected in related:
                proof = _cleanup_inspect(state_dir, selected, retained=related)
                if proof["eligible"]:
                    return {"reasons": [], "resume": True}
                if first is None:
                    first = proof
            return {"reasons": first["reasons"], "resume": True}

        # The registration is still bound to this allocation, but Git removed
        # its gitfile. Never write it back merely to complete deletion.
        fmt = _line(repository, "rev-parse", "--show-object-format")
        surviving = {}
        def collect(directory):
            for entry in os.scandir(directory):
                path = Path(entry.path)
                relative = path.relative_to(checkout).as_posix()
                if relative == ".git":
                    continue
                if entry.is_dir(follow_symlinks=False):
                    collect(path)
                else:
                    value = _file(checkout, relative)
                    if value is None:
                        raise BoardError("WORKSPACE_CHANGED", "A checkout file vanished during deletion proof")
                    surviving[relative] = [value[0], _blob_oid(value[1], fmt)]
        collect(checkout)
        trees = [item["tree"] for item in candidates if
                 _line(repository, "rev-parse", item["commit"] + "^{tree}") == item["tree"]]
        trees.append(manifest["inputTree"])
        differences = []
        for tree in dict.fromkeys(trees):
            expected = _entries(repository, tree)
            differences = [path for path, value in surviving.items() if expected.get(path) != value]
            if not differences:
                return {"reasons": [], "resume": True}
        return {"reasons": ["unsealed-changes"], "unsealedPaths": sorted(differences), "resume": True}


def cleanup_remove(state_dir, manifest: dict, *, retained=None, resume=False) -> dict:
    """Delete exactly the registered worktree of one manifest's physical allocation.

    The caller has rechecked acceptance, integration, shutdown and dependency
    evidence. This function only proves that the exact path still is that
    allocation's own locked worktree and then removes that one path; no parent
    directory, source checkout, outputs directory or Git reference is touched.
    A borrowed existing checkout, a sibling worktree and a registry entry that
    belongs to someone else are never this cleanup's business. No repository-wide
    ``worktree prune`` runs here.

    Removing an already removed allocation is the same achieved outcome, not a
    failure: the accepted plan and the allocation record are the authorization, and
    the daemon's own sweep or a retry may complete the same deletion first. Only
    this exact allocation's path counts as gone; a Git failure with anything still
    occupying the path stays fatal.
    """
    with _errors():
        _validate_manifest(manifest)
        state_dir = Path(state_dir).resolve()
        allocation = _resolve_allocation(state_dir, manifest, retained)
        if allocation is None or not _inside(manifest["path"], allocation["path"]):
            raise BoardError("WORKSPACE_UNSAFE", "Cleanup only removes a registered disposable hey-my-buddy checkout",
                             path=manifest["checkoutRoot"])
        checkout_root = Path(allocation["path"])
        removal = {"removed": True, "path": str(checkout_root),
                   "repositoryPath": manifest["snapshot"]["repositoryPath"],
                   "workspaceId": allocation["workspaceId"], "manifestWorkspaceId": manifest["workspaceId"],
                   "checkoutId": manifest["checkoutId"], "alreadyRemoved": True}
        if not _path_present(checkout_root):
            return removal
        if resume:
            repository = Path(manifest["snapshot"]["repositoryPath"])
            record, git_dir = _cleanup_resume_registration(repository, checkout_root, allocation, manifest)
            if record is None:
                # rmdir is the deletion boundary: a concurrent addition makes
                # it fail, so no unregistered content can be removed here.
                checkout_root.rmdir()
                removal["alreadyRemoved"] = False
                return removal
        else:
            actual = inspect(str(checkout_root))
            if actual["checkoutRoot"] != manifest["checkoutRoot"]:
                raise BoardError("WORKSPACE_CHANGED", "The cleanup target identity changed", field="checkoutRoot")
            for field in ("checkoutId", "repositoryId"):
                if manifest[field] != actual[field]:
                    raise BoardError("WORKSPACE_CHANGED", "The cleanup target identity changed", field=field)
            repository = Path(actual["repositoryPath"])
            record = _worktree_record(repository, checkout_root)
        if record is not None and record.get("locked") == "hey-my-buddy:" + allocation["workspaceId"] and "detached" not in record:
            # Registration and lock are exactly this allocation's; only the
            # attached HEAD blocks the removal. Refuse with the branch, the safe
            # detach command and the retry — never detach, delete or move any
            # branch or reference on the board's own authority.
            report = _attached_branch_report(checkout_root, record)
            raise BoardError(
                "WORKSPACE_UNSAFE",
                "The cleanup target is registered and locked but its HEAD is attached to branch "
                + str(report.get("branch"))
                + "; detach it with the recorded command and retry the cleanup",
                path=str(checkout_root), attachedBranch=report,
            )
        if record is None:
            raise BoardError("WORKSPACE_UNSAFE", "The cleanup target has no worktree registration",
                             path=str(checkout_root))
        if record.get("locked") != "hey-my-buddy:" + allocation["workspaceId"]:
            raise BoardError("WORKSPACE_UNSAFE", "The cleanup target is locked by a different allocation",
                             path=str(checkout_root))
        removal["repositoryPath"] = str(repository)
        return _remove_worktree(repository, checkout_root, allocation, manifest, removal)


def _remove_worktree(repository, checkout_root, allocation, manifest, removal):
    """Remove the proven physical worktree and retain actual removal evidence."""
    try:
        _git(repository, "worktree", "unlock", str(checkout_root), allowed=(0, 1))
        _git(repository, "worktree", "remove", "--force", str(checkout_root), allowed=(0,))
    except BoardError as error:
        # A failed remove must leave the allocation recoverable under its
        # original lock. Only relock this same physical registration.
        if _path_present(checkout_root):
            try:
                _cleanup_resume_registration(repository, checkout_root, allocation, manifest)
            except BoardError as relock_error:
                error.details["relockError"] = relock_error.payload()
        # A concurrent owner of this same allocation completed the removal in
        # this window; the Git refusal is about a registration that no longer
        # names a live checkout, and the target is provably gone.
        if _path_present(checkout_root):
            raise
        return removal
    if _path_present(checkout_root):
        raise BoardError("WORKSPACE_IO_ERROR", "The managed checkout still exists after removal", path=str(checkout_root))
    removal["alreadyRemoved"] = False
    return removal
