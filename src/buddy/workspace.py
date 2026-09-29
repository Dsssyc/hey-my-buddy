"""Pinned Git workspaces and immutable artifacts, independent of board state.

The caller owns admission, reservations and process-stop evidence. In particular,
``seal`` may only be called after the Worker has proved that its child stopped.
No operation here changes the source checkout's HEAD, index or working files.
Stability and scope checks cover Git-managed paths and explicit untracked inputs;
ignored environments and caches are excluded unless selected. This is not an OS
sandbox or a claim that every physical file remains unchanged.
"""
from contextlib import contextmanager
from . import locking
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile

from .errors import BoardError
from . import windows_paths

_WINDOWS = os.name == "nt"


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
        raise BoardError("WORKSPACE_IO_ERROR", str(error)) from error
    except (KeyError, TypeError, ValueError) as error:
        raise BoardError("INVALID_WORKSPACE", "Malformed workspace argument or recovery record") from error


def _git(root, *args, data=None, env=None, allowed=(0,)):
    # Do not inherit a caller's private index, repository, worktree or config
    # override. Read operations must not refresh the source index or run hooks.
    git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    git_env.update(GIT_OPTIONAL_LOCKS="0", GIT_LITERAL_PATHSPECS="1", GIT_NO_REPLACE_OBJECTS="1", LC_ALL="C")
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
        with windows_paths.parent(Path(root) / relative, create=create) as (api, path):
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
            item = windows_paths.read(Path(root) / relative, allow_symlink=True)
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


def _blob(root, data, *, write=False):
    args = ["hash-object", "--no-filters", "--stdin"]
    if write:
        args.append("-w")
    return _line(root, *args, data=data)


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


def _observe(root, selected, *, write=False, require_selected=False):
    head = _commit(root, "HEAD")
    index = _entries(root)
    tracked, untracked, included = {}, {}, {}
    for path in index:
        item = _file_with_git_mode(root, path, index[path][0])
        if item is not None:
            mode, data = item
            tracked[path] = [mode, _blob(root, data, write=write)]
    for path in _untracked(root, selected):
        item = _file(root, path)
        if item is None:
            raise BoardError("WORKSPACE_CHANGED", "An untracked file disappeared during capture", path=path)
        mode, data = item
        untracked[path] = [mode, _sha(data)]
        if _in_scope(path, selected):
            included[path] = [mode, _blob(root, data, write=write)]
    if _WINDOWS:
        windows_paths.validate_unique([*index, *untracked])
    if require_selected:
        for path in selected:
            if not any(_in_scope(candidate, [path]) for candidate in included):
                raise BoardError("INVALID_WORKSPACE", "includeUntracked does not select an untracked file", path=path)
    observation = {"head": head, "index": index, "tracked": tracked, "untracked": untracked, "included": included}
    observation["fingerprint"] = _sha(_json(observation))
    return observation


def _stable_observation(root, selected, *, write=False, require_selected=False):
    observation = _observe(root, selected, write=write, require_selected=require_selected)
    if observation != _observe(root, selected):
        raise BoardError("WORKSPACE_CHANGED", "The checkout changed during snapshot capture")
    return observation


def _tree(root, entries):
    # A private index builds raw blob trees without filters or changes to the
    # source index. https://git-scm.com/docs/git-update-index#_using_index_info
    with tempfile.TemporaryDirectory(prefix="buddy-index-") as directory:
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
    environment = {"GIT_AUTHOR_NAME": "Buddy snapshot", "GIT_AUTHOR_EMAIL": "buddy@localhost",
                   "GIT_COMMITTER_NAME": "Buddy snapshot", "GIT_COMMITTER_EMAIL": "buddy@localhost",
                   "GIT_AUTHOR_DATE": "@946684800 +0000", "GIT_COMMITTER_DATE": "@946684800 +0000"}
    return _line(root, "commit-tree", tree, "-p", parent, "--no-gpg-sign", data=(message + "\n").encode(), env=environment)


def _diff(root, before, after):
    return _git(root, "diff", "--binary", "--full-index", "--no-renames", "--no-ext-diff", "--no-textconv",
                "--no-color", "--src-prefix=a/", "--dst-prefix=b/", before, after, "--")


def _read(path):
    try:
        if _WINDOWS:
            return windows_paths.read(path)
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
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


def _write_once(path, data):
    if _WINDOWS:
        windows_paths.write_once(path, data)
        return
    existing = _read(path)
    if existing is not None:
        if existing != data:
            raise BoardError("WORKSPACE_CONFLICT", "An immutable workspace artifact already has different contents", path=str(path))
        return
    with tempfile.NamedTemporaryFile(prefix=".pending-", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if _read(path) != data:
                    raise BoardError("WORKSPACE_CONFLICT", "An immutable workspace artifact collided", path=str(path))
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink()


@contextmanager
def _lock(directory):
    descriptor = (windows_paths.lock_fd(directory / ".lock") if _WINDOWS else
                  os.open(directory / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600))
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
    allowed = {"kind", "cwd", "access", "base", "includeUntracked", "writeScope", "integrator", "targetRef"}
    if not isinstance(intent, dict) or set(intent) - allowed:
        raise BoardError("INVALID_WORKSPACE", "Unknown executionWorkspace fields")
    if intent.get("kind") not in ("existing", "worktree") or intent.get("access") not in ("read", "write"):
        raise BoardError("INVALID_WORKSPACE", "Workspace kind and access must be explicit")
    base = intent.get("base")
    if not isinstance(base, dict) or set(base) - {"kind", "ref"} or base.get("kind") not in ("commit", "working-tree"):
        raise BoardError("INVALID_WORKSPACE", "Workspace base must select commit or working-tree")
    if not isinstance(intent.get("integrator"), str) or not intent["integrator"].strip():
        raise BoardError("INVALID_WORKSPACE", "Workspace integrator must be explicit")
    if intent.get("targetRef") is not None and (not isinstance(intent["targetRef"], str) or not intent["targetRef"].strip()):
        raise BoardError("INVALID_WORKSPACE", "targetRef must be an attribution string")
    result = dict(intent, base=dict(base))
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
    if result["access"] == "write" and not result["writeScope"]:
        raise BoardError("INVALID_WORKSPACE", "Write access requires an explicit writeScope")
    if base["kind"] == "commit" and result["includeUntracked"]:
        raise BoardError("INVALID_WORKSPACE", "Untracked input requires a working-tree base")
    return result


def _mkdir(path):
    if path.is_symlink():
        raise BoardError("WORKSPACE_CONFLICT", "A workspace state directory is a symlink", path=str(path))
    path.mkdir(mode=0o700, exist_ok=True)


def _workspace_directory(state_dir, request_id):
    if not isinstance(request_id, str) or not request_id or "\0" in request_id:
        raise BoardError("INVALID_WORKSPACE", "request_id must be a nonempty stable identity")
    state_dir = Path(state_dir).resolve()
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    workspace_id = "ws-" + _sha(_json([str(state_dir), request_id]))[:32]
    _mkdir(state_dir / "workspaces")
    directory = state_dir / "workspaces" / workspace_id
    _mkdir(directory)
    return workspace_id, directory


def _snapshot(source, intent, workspace_id):
    root = Path(source["checkoutRoot"])
    base = _commit(root, intent["base"].get("ref", "HEAD"))
    base_entries = _entries(root, base)
    base_tree = _line(root, "rev-parse", base + "^{tree}")
    snapshot = {"repositoryPath": source["repositoryPath"], "sourceCheckoutId": source["checkoutId"],
                "sourceHead": source["headCommit"], "includeSelectors": intent["includeUntracked"],
                "inputRef": f"refs/buddy/workspaces/{workspace_id}/input",
                "stagedRef": f"refs/buddy/workspaces/{workspace_id}/staged"}
    if intent["base"]["kind"] == "working-tree":
        observed = _stable_observation(root, intent["includeUntracked"], write=True, require_selected=True)
        if observed["head"] != source["headCommit"]:
            raise BoardError("WORKSPACE_CHANGED", "The source HEAD changed during preparation")
        staged_tree = _tree(root, observed["index"])
        tracked_tree = _tree(root, observed["tracked"])
        input_tree = _tree(root, observed["tracked"] | observed["included"])
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
        staged_tree = tracked_tree = input_tree = base_tree
        snapshot.update(includedUntracked=[], excludedUntracked=_untracked(root, []), excludedEntries={})
        if intent["kind"] == "existing":
            observed = _stable_observation(root, [])
            if observed["head"] != base or observed["index"] != base_entries or observed["tracked"] != base_entries or observed["untracked"]:
                raise BoardError("WORKSPACE_BASE_MISMATCH", "The existing checkout does not exactly match the selected commit")
            snapshot["sourceFingerprint"] = observed["fingerprint"]
    staged_commit = base if staged_tree == base_tree else _commit_tree(root, staged_tree, base, workspace_id + " staged input")
    input_commit = base if input_tree == base_tree else _commit_tree(root, input_tree, base, workspace_id + " input")
    snapshot.update(stagedTree=staged_tree, stagedCommit=staged_commit,
                    stagedSha256=_sha(_diff(root, base, staged_tree)),
                    unstagedSha256=_sha(_diff(root, staged_tree, tracked_tree)))
    return {"baseCommit": base, "inputCommit": input_commit, "inputTree": input_tree, "snapshot": snapshot}


def _worktree_record(root, target):
    for record in _git(root, "worktree", "list", "--porcelain", "-z").split(b"\0\0"):
        fields = {}
        for line in record.split(b"\0"):
            key, _, value = line.partition(b" ")
            fields[os.fsdecode(key)] = os.fsdecode(value)
        if fields.get("worktree") == str(target):
            return fields
    return None


def _materialize(root, entries):
    if _WINDOWS:
        windows_paths.validate_unique(entries)
        for path, (mode, oid) in entries.items():
            _relative(path)
            data = _git(root, "cat-file", "blob", oid)
            target = Path(root) / path
            if mode == "120000":
                windows_paths.symlink(target, os.fsdecode(data))
            else:
                windows_paths.write_new(target, data, create_parents=True)
        return
    for path, (mode, oid) in entries.items():
        data = _git(root, "cat-file", "blob", oid)
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
        observed = _stable_observation(target, [])
        if identity["repositoryId"] != source["repositoryId"] or observed["head"] != pinned["inputCommit"] or observed["index"] != entries or observed["tracked"] != entries or observed["untracked"]:
            raise BoardError("WORKSPACE_CONFLICT", "The interrupted worktree is incomplete or changed; it was preserved", path=str(target))
        return
    # --lock records ownership atomically with Git registration. A partially
    # populated checkout is preserved and reported as a conflict on recovery.
    # https://git-scm.com/docs/git-worktree#_options
    _git(root, "worktree", "add", "--detach", "--no-checkout", "--lock", "--reason", reason,
         str(target), pinned["inputCommit"])
    _materialize(target, entries)
    _git(target, "read-tree", pinned["inputTree"])


def _validate_manifest(manifest):
    fields = {"version", "workspaceId", "kind", "path", "checkoutRoot", "checkoutId", "repositoryId", "access",
              "baseCommit", "inputCommit", "inputTree", "writeScope", "integrator", "targetRef", "exclusionPolicy", "snapshot", "manifestSha256"}
    if not isinstance(manifest, dict) or set(manifest) != fields or type(manifest.get("version")) is not int or manifest["version"] != 1:
        raise BoardError("INVALID_WORKSPACE", "Invalid workspace manifest schema")
    expected = _sha(_json({key: value for key, value in manifest.items() if key != "manifestSha256"}))
    if expected != manifest["manifestSha256"]:
        raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The workspace manifest hash does not match its contents")
    if manifest["exclusionPolicy"] != _EXCLUSION_POLICY:
        raise BoardError("INVALID_WORKSPACE", "Unsupported workspace exclusion policy")
    snapshot_fields = {"repositoryPath", "stagedCommit", "stagedTree", "inputRef", "stagedRef", "executionSelectors",
                       "executionFingerprint", "excludedEntries"}
    if not isinstance(manifest["snapshot"], dict) or not snapshot_fields <= manifest["snapshot"].keys():
        raise BoardError("INVALID_WORKSPACE", "Invalid workspace snapshot")
    if manifest["kind"] not in ("existing", "worktree") or manifest["access"] not in ("read", "write"):
        raise BoardError("INVALID_WORKSPACE", "Invalid manifest kind or access")
    for value in (manifest["path"], manifest["checkoutRoot"], manifest["snapshot"]["repositoryPath"]):
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise BoardError("INVALID_WORKSPACE", "Manifest paths must be absolute")
    for values in (manifest["writeScope"], manifest["snapshot"]["executionSelectors"]):
        if not isinstance(values, list) or any(_relative(value, allow_root=True) != value for value in values):
            raise BoardError("INVALID_WORKSPACE", "Manifest paths must be normalized")


def verify(manifest: dict, *, require_unchanged: bool = True) -> dict:
    """Validate fixed input references and actual execution checkout identity.

    Read workspaces always require unchanged managed input, as defined by the
    manifest's exclusionPolicy. Ignored caches may change. For a writable workspace,
    ``require_unchanged=False`` validates identity and immutable objects without
    comparing evolving files to the original execution fingerprint.
    """
    with _errors():
        _validate_manifest(manifest)
        actual = inspect(manifest["path"])
        for field in ("checkoutRoot", "checkoutId", "repositoryId"):
            if actual[field] != manifest[field]:
                raise BoardError("WORKSPACE_CHANGED", "The execution checkout identity changed", field=field)
        root = Path(actual["checkoutRoot"])
        snapshot = manifest["snapshot"]
        for field, commit in (("inputRef", manifest["inputCommit"]), ("stagedRef", snapshot["stagedCommit"])):
            if _commit(root, snapshot[field]) != commit:
                raise BoardError("WORKSPACE_CONFLICT", "An immutable input reference changed", ref=snapshot[field])
        if _line(root, "rev-parse", manifest["inputCommit"] + "^{tree}") != manifest["inputTree"]:
            raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The pinned input tree is inconsistent")
        unchanged = None
        if require_unchanged or manifest["access"] == "read":
            observation = _stable_observation(root, snapshot["executionSelectors"])
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


def prepare(state_dir: Path, request_id: str, intent: dict) -> dict:
    """Prepare once, then replay the same request's frozen input manifest.

    A new request_id explicitly captures new source input. Recovery never resets
    or removes a pre-existing target. Relative scopes address the checkout root.
    """
    with _errors():
        intent = _intent(intent)
        workspace_id, directory = _workspace_directory(state_dir, request_id)
        with _lock(directory):
            _write_once(directory / "request.json", _json({"version": 1, "requestId": request_id, "intent": intent}))
            manifest = _record(directory / "manifest.json")
            if manifest is not None:
                verify(manifest, require_unchanged=False)
                return manifest
            pinned = _record(directory / "input.json")
            if pinned is None:
                source = inspect(intent["cwd"])
                pinned = {"source": source, **_snapshot(source, intent, workspace_id)}
                # Keep newly created objects reachable before a durable record
                # promises they can be recovered, including across Git GC.
                _pin(source["repositoryPath"], f"refs/buddy/workspaces/{workspace_id}/retained/input", pinned["inputCommit"])
                _pin(source["repositoryPath"], f"refs/buddy/workspaces/{workspace_id}/retained/staged", pinned["snapshot"]["stagedCommit"])
                _write_once(directory / "input.json", _json(pinned))
            source = pinned["source"]
            repository = Path(pinned["snapshot"]["repositoryPath"])
            _pin(repository, pinned["snapshot"]["inputRef"], pinned["inputCommit"])
            _pin(repository, pinned["snapshot"]["stagedRef"], pinned["snapshot"]["stagedCommit"])
            path = Path(intent["cwd"])
            if intent["kind"] == "worktree":
                target = directory / "checkout"
                _prepare_worktree(source, target, pinned, "buddy:" + workspace_id)
                relative = path.relative_to(source["checkoutRoot"])
                path = target / relative
                if relative.parts:
                    with _parent(target, str(relative / ".cwd-placeholder"), create=True):
                        pass
            actual = inspect(str(path))
            if actual["repositoryId"] != source["repositoryId"]:
                raise BoardError("WORKSPACE_CHANGED", "The execution repository changed during preparation")
            selectors = intent["includeUntracked"] if intent["kind"] == "existing" else []
            observed = _stable_observation(Path(actual["checkoutRoot"]), selectors)
            if intent["kind"] == "existing" and (actual["checkoutId"] != source["checkoutId"] or observed["fingerprint"] != pinned["snapshot"]["sourceFingerprint"]):
                raise BoardError("WORKSPACE_CHANGED", "The existing checkout changed after its input was frozen")
            snapshot = dict(pinned["snapshot"], executionSelectors=selectors, executionFingerprint=observed["fingerprint"])
            manifest = {"version": 1, "workspaceId": workspace_id, "kind": intent["kind"], "path": str(path),
                        "checkoutRoot": actual["checkoutRoot"], "checkoutId": actual["checkoutId"],
                        "repositoryId": actual["repositoryId"], "access": intent["access"],
                        "baseCommit": pinned["baseCommit"], "inputCommit": pinned["inputCommit"], "inputTree": pinned["inputTree"],
                        "writeScope": intent["writeScope"], "integrator": intent["integrator"], "targetRef": intent["targetRef"],
                        "exclusionPolicy": dict(_EXCLUSION_POLICY), "snapshot": snapshot}
            manifest["manifestSha256"] = _sha(_json(manifest))
            _write_once(directory / "manifest.json", _json(manifest))
            return manifest


#: Bounded per-path evidence for one scope violation. A failed seal keeps this
#: record so a Host can restore, adopt or abandon the exact observed site later
#: without turning the failed working tree into an authorized baseline.
SCOPE_EVIDENCE_LIMIT = 256
#: Bounded per-path content binding of one sealed output. Keeping the blob
#: identity of every changed path lets integration verification compare the
#: immutable artifact with an actual target checkout independently of the
#: artifact's own repository.
OUTPUT_ENTRY_LIMIT = 512
RESOLUTION_ACTIONS = ("restore", "adopt", "abandon")
INTEGRATION_STRATEGIES = ("patch", "cherry-pick", "merge", "not-required")


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


def _scope_evidence(root, manifest, observation, violations, changed, index_changes, excluded_changes):
    """Bounded, content-addressed evidence for one out-of-scope failure site."""
    initial = _entries(root, manifest["inputCommit"])
    initial_index = _entries(root, manifest["snapshot"]["stagedTree"] if manifest["kind"] == "existing" else manifest["inputTree"])
    ordered = sorted(violations)
    entries = []
    for path in ordered[:SCOPE_EVIDENCE_LIMIT]:
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
        "truncated": len(ordered) > SCOPE_EVIDENCE_LIMIT,
        "entries": entries,
    }


def _output_entries(root, manifest, observation, *, allow_outside_scope=False):
    initial = _entries(root, manifest["inputCommit"])
    initial_index = _entries(root, manifest["snapshot"]["stagedTree"] if manifest["kind"] == "existing" else manifest["inputTree"])
    excluded = manifest["snapshot"]["excludedEntries"] if manifest["kind"] == "existing" else {}
    scope = manifest["writeScope"]
    entries = dict(observation["tracked"])
    violations = set()
    adopted = set()
    excluded_changes = set()
    for path, fingerprint in observation["untracked"].items():
        if path in excluded and excluded[path] == fingerprint:
            continue
        if path in excluded:
            excluded_changes.add(path)
        item = _file(root, path)
        if item is None or [item[0], _sha(item[1])] != fingerprint:
            raise BoardError("WORKSPACE_CHANGED", "An output changed during sealing", path=path)
        entry = [item[0], _blob(root, item[1], write=True)]
        if _in_scope(path, scope) or initial.get(path) == entry:
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
    outside = {path for path in set(changed) | index_changes | excluded_changes if not _in_scope(path, scope)}
    if allow_outside_scope:
        # An explicitly adopted site promotes these exact paths; the record keeps
        # them named as adopted so a later reader never mistakes them for authorized.
        adopted |= outside
    else:
        violations |= outside
    if violations:
        evidence = _scope_evidence(root, manifest, observation, violations, changed, index_changes, excluded_changes)
        raise BoardError("WORKSPACE_SCOPE_VIOLATION", "Managed workspace paths contain changes outside the declared write scope",
                         paths=sorted(violations), changedPaths=changed, evidence=evidence)
    return entries, changed, sorted(excluded_changes), sorted(adopted)


def _changed_entries(entries, changed):
    """Per-path output binding, including deletions, bounded for verification."""
    bounded = changed[:OUTPUT_ENTRY_LIMIT]
    return {path: entries.get(path) for path in bounded}, len(changed) > OUTPUT_ENTRY_LIMIT


def _finish_output(repository, output, directory):
    expected_tree = _line(repository, "rev-parse", output["commit"] + "^{tree}")
    bindings = ("workspaceId", "taskId", "attemptId", "manifestSha256", "baseCommit", "inputCommit", "commit",
                "tree", "changedPaths", "includedUntracked", "diffSha256")
    expected_ref = f"refs/buddy/workspaces/{output['workspaceId']}/outputs/{directory.name}"
    if expected_tree != output["tree"] or _sha(_json(output["snapshot"])) != output["snapshotSha256"] or any(output.get(key) != output["snapshot"].get(key) for key in bindings) or output["ref"] != expected_ref or output["diffPath"] != str(directory / "output.patch"):
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
    with _errors():
        _validate_manifest(manifest)
        if any(not isinstance(value, str) or not value or "\0" in value for value in (task_id, attempt_id)):
            raise BoardError("INVALID_WORKSPACE", "task_id and attempt_id must be stable identities")
        workspace_id = manifest["workspaceId"]
        if not isinstance(workspace_id, str) or not workspace_id.startswith("ws-") or len(workspace_id) != 35 or any(value not in "0123456789abcdef" for value in workspace_id[3:]):
            raise BoardError("INVALID_WORKSPACE", "Invalid workspace identity")
        workspace_dir = Path(state_dir).resolve() / "workspaces" / workspace_id
        if workspace_dir.is_symlink() or _record(workspace_dir / "manifest.json") != manifest:
            raise BoardError("WORKSPACE_CONFLICT", "This state directory does not own the supplied workspace manifest")
        with _lock(workspace_dir):
            _mkdir(workspace_dir / "outputs")
            output_id = _sha(_json([task_id, attempt_id]))
            directory = workspace_dir / "outputs" / output_id
            _mkdir(directory)
            repository = Path(manifest["snapshot"]["repositoryPath"])
            if _identity(repository) != manifest["repositoryId"]:
                raise BoardError("WORKSPACE_CHANGED", "The output repository identity changed")
            output = _record(directory / "output.json") or _record(directory / "pending.json")
            if output is not None:
                if output.get("taskId") != task_id or output.get("attemptId") != attempt_id or output.get("manifestSha256") != manifest["manifestSha256"]:
                    raise BoardError("WORKSPACE_CONFLICT", "This output identity belongs to a different attempt or input")
                return _finish_output(repository, output, directory)
            verify(manifest, require_unchanged=False)
            root = Path(manifest["checkoutRoot"])
            observation = _stable_observation(root, manifest["snapshot"]["executionSelectors"], write=True)
            if manifest["access"] == "read":
                if observation["fingerprint"] != manifest["snapshot"]["executionFingerprint"]:
                    raise BoardError("WORKSPACE_CHANGED", "Read-only input changed before it could be sealed")
                entries, changed, excluded_changes = _entries(root, manifest["inputTree"]), [], []
            else:
                try:
                    entries, changed, excluded_changes, _adopted = _output_entries(root, manifest, observation)
                except BoardError as error:
                    # The failed site keeps a durable, bounded record of exactly
                    # which managed paths diverged and from which authorized state.
                    # It is evidence for a Host decision, never a new baseline.
                    if error.code == "WORKSPACE_SCOPE_VIOLATION" and isinstance(error.details.get("evidence"), dict):
                        _record_scope_evidence(directory, error.details["evidence"], task_id, attempt_id)
                    raise
            changed_entries, truncated = _changed_entries(entries, changed)
            tree = _tree(root, entries)
            if observation != _observe(root, manifest["snapshot"]["executionSelectors"]):
                raise BoardError("WORKSPACE_CHANGED", "The workspace changed during output sealing")
            commit = manifest["inputCommit"] if tree == manifest["inputTree"] else _commit_tree(root, tree, manifest["inputCommit"], workspace_id + " output " + output_id)
            patch_data = _diff(repository, manifest["inputCommit"], commit)
            snapshot = {"headCommit": observation["head"], "indexSha256": _sha(_json(observation["index"])),
                        "observationSha256": observation["fingerprint"], "tree": tree, "changedPaths": changed,
                        "changedEntries": changed_entries, "changedEntriesTruncated": truncated,
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
                      "ref": f"refs/buddy/workspaces/{workspace_id}/outputs/{output_id}",
                      "diffPath": str(directory / "output.patch"), "diffSha256": _sha(patch_data)}
            # Retention precedes the recovery record: GC must not discard a
            # chosen output while final reference publication is interrupted.
            _pin(repository, f"refs/buddy/workspaces/{workspace_id}/retained/output-{output_id}", commit)
            _write_once(directory / "pending.json", _json(output))
            _write_once(directory / "output.patch", patch_data)
            return _finish_output(repository, output, directory)


def cumulative_patch(manifest: dict, artifact: dict, original_commit: str) -> dict:
    """Freeze a whole-goal patch using immutable Git objects, independent of checkout edits."""
    with _errors():
        repository, _changed = _artifact_binding(manifest, manifest, artifact)
        base = _commit(repository, original_commit)
        data = _diff(repository, base, artifact["commit"])
        path = Path(artifact["diffPath"]).with_name("cumulative-" + base + ".patch")
        _write_once(path, data)
        return {"baseCommit": base, "outputCommit": artifact["commit"], "path": str(path),
                "sha256": _sha(data), "changedPaths": sorted(_tree_changes(repository, base, artifact["commit"]))}


# -- scope failure evidence and Host-directed recovery ------------------------
def _record_scope_evidence(directory, evidence, task_id, attempt_id):
    """Keep one bounded failure-site record per observed workspace fingerprint."""
    record = dict(evidence, taskId=task_id, attemptId=attempt_id)
    folder = directory / "scope-conflicts"
    _mkdir(folder)
    _write_once(folder / f"{record['observedFingerprint']}.json", _json(record))


def _scope_records(workspace_dir, manifest, task_id, attempt_id):
    folder = workspace_dir / "outputs" / _sha(_json([task_id, attempt_id])) / "scope-conflicts"
    if not folder.exists():
        return []
    if folder.is_symlink() or not folder.is_dir():
        raise BoardError("WORKSPACE_CONFLICT", "The scope evidence path is not a regular directory", path=str(folder))
    records = []
    for path in sorted(folder.glob("*.json")):
        record = _record(path)
        if not isinstance(record, dict):
            raise BoardError("INVALID_WORKSPACE", "Malformed scope failure evidence", path=str(path))
        if (record.get("taskId") != task_id or record.get("attemptId") != attempt_id
                or record.get("workspaceId") != manifest["workspaceId"]
                or record.get("manifestSha256") != manifest["manifestSha256"]):
            continue
        records.append((path.stat().st_mtime_ns, path.name, record))
    records.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [record for _, _, record in records]


def scope_conflicts(state_dir, manifest: dict, task_id: str, attempt_id: str) -> list[dict]:
    """The durable out-of-scope failure-site evidence of one stopped attempt.

    Records are read-only evidence; they are never a workspace baseline. The
    caller holds the checkout reservation and rechecks the owner around this call.
    """
    with _errors():
        _validate_manifest(manifest)
        if any(not isinstance(value, str) or not value or "\0" in value for value in (task_id, attempt_id)):
            raise BoardError("INVALID_WORKSPACE", "task_id and attempt_id must be stable identities")
        workspace_dir = Path(state_dir).resolve() / "workspaces" / manifest["workspaceId"]
        return _scope_records(workspace_dir, manifest, task_id, attempt_id)


def normalize_scope(values) -> list[str]:
    """Normalize one explicit relative write scope, rejecting escapes."""
    with _errors():
        if not isinstance(values, list):
            raise BoardError("INVALID_WORKSPACE", "writeScope must be a list")
        return sorted({_relative(value, allow_root=True) for value in values})


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


def _entry_is_authorized(root, entry, current):
    authorized = entry.get("authorized")
    if authorized is None:
        return current is None
    if not isinstance(current, dict) or current.get("mode") != authorized.get("mode"):
        return False
    item = _file_with_git_mode(root, entry["path"], authorized.get("mode"))
    return item is not None and _blob(root, item[1]) == authorized.get("oid")


def _entry_matches(root, entry, index_entries):
    """Compare-and-swap test: the recorded site or its already-authorized state."""
    current = _observed_file(root, entry["path"], mode_hint=(index_entries.get(entry["path"]) or [None])[0])
    if not _entry_state_matches(current, entry.get("observed")) and not _entry_is_authorized(root, entry, current):
        return False
    index = _entry_state(index_entries, entry["path"])
    return index == entry.get("observedIndex") or index == entry.get("authorizedIndex")


def _write_path(root, path, entry):
    if _WINDOWS:
        _relative(path)
        target = Path(root) / path
        data = _git(root, "cat-file", "blob", entry["oid"]) if entry is not None else None
        windows_paths.restore(target, None if entry is None or entry["mode"] == "120000" else data,
                              symlink_target=os.fsdecode(data) if entry is not None and entry["mode"] == "120000" else None)
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
    payload = b""
    for path, entry in sorted(entries.items()):
        if entry is None:
            payload += b"0 " + b"0" * 40 + b"\t" + os.fsencode(path) + b"\0"
        else:
            payload += entry["mode"].encode() + b" " + entry["oid"].encode() + b"\t" + os.fsencode(path) + b"\0"
    if payload:
        _git(root, "update-index", "-z", "--index-info", data=payload)


def _blocking_paths(root, manifest, observation):
    try:
        _output_entries(root, manifest, observation)
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
    for path in sorted(selected):
        _write_path(root, path, _entry_state(initial, path))
    _restore_index(root, {path: _entry_state(initial_index, path) for path in sorted(selected)
                          if _entry_state(current_index, path) != _entry_state(initial_index, path)})
    # An excluded cache was never captured by content, so a modified one cannot be
    # returned mechanically; it is reported instead of being silently accepted.
    remaining = sorted(path for path, fingerprint in excluded.items()
                       if observation["untracked"].get(path) != fingerprint)
    return sorted(selected), remaining


def _snapshot_record(manifest, observation, tree, entries, changed, excluded_changes, task_id, attempt_id, commit, patch_data):
    changed_entries, truncated = _changed_entries(entries, changed)
    return {"headCommit": observation["head"], "indexSha256": _sha(_json(observation["index"])),
            "observationSha256": observation["fingerprint"], "tree": tree, "changedPaths": changed,
            "changedEntries": changed_entries, "changedEntriesTruncated": truncated,
            "excludedChangedPaths": excluded_changes, "manifestSha256": manifest["manifestSha256"],
            "includedUntracked": sorted(entries.keys() & observation["untracked"].keys()),
            "workspaceId": manifest["workspaceId"], "taskId": task_id, "attemptId": attempt_id,
            "baseCommit": manifest["baseCommit"], "inputCommit": manifest["inputCommit"],
            "commit": commit, "diffSha256": _sha(patch_data)}


def _finish_record(repository, record, directory, *, ref_name, ref_kind, record_name, patch_name, bindings):
    expected_tree = _line(repository, "rev-parse", record["commit"] + "^{tree}")
    expected_ref = f"refs/buddy/workspaces/{record['workspaceId']}/{ref_kind}/{ref_name}"
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


def _replay_record(directory, names, *, task_id, attempt_id, manifest):
    """An already published record of the same identity, or None."""
    for name in names:
        record = _record(directory / name)
        if record is None:
            continue
        if (record.get("taskId") != task_id or record.get("attemptId") != attempt_id
                or record.get("manifestSha256") != manifest["manifestSha256"]
                or record.get("workspaceId") != manifest["workspaceId"]):
            raise BoardError("WORKSPACE_CONFLICT", "This resolution identity belongs to a different attempt or input")
        return record
    return None


def _resolution_record(root, manifest, repository, directory, observation, *, task_id, attempt_id, action, actor, reason, conflict_fingerprint):
    workspace_id = manifest["workspaceId"]
    output_id = _sha(_json([task_id, attempt_id]))
    names = (f"resolve-{action}.json", f"resolve-{action}-pending.json")
    existing = _replay_record(directory, names, task_id=task_id, attempt_id=attempt_id, manifest=manifest)
    if existing is not None:
        return _finish_record(repository, existing, directory, ref_name=f"{output_id}-{action}",
                              ref_kind="resolutions", record_name=f"resolve-{action}.json",
                              patch_name=f"resolve-{action}.patch", bindings=_RECORD_BINDINGS)
    entries, changed, excluded_changes, adopted = _output_entries(root, manifest, observation,
                                                                  allow_outside_scope=(action == "adopt"))
    tree = _tree(root, entries)
    if observation != _observe(root, manifest["snapshot"]["executionSelectors"]):
        raise BoardError("WORKSPACE_CHANGED", "The workspace changed during resolution sealing")
    commit = manifest["inputCommit"] if tree == manifest["inputTree"] else _commit_tree(root, tree, manifest["inputCommit"], workspace_id + " resolution " + output_id)
    patch_data = _diff(repository, manifest["inputCommit"], commit)
    snapshot = _snapshot_record(manifest, observation, tree, entries, changed, excluded_changes, task_id, attempt_id, commit, patch_data)
    snapshot["adoptedPaths"] = adopted
    record = {"version": 1, "kind": "resolution", "action": action, "actor": actor, "reason": reason,
              "conflictFingerprint": conflict_fingerprint, "workspaceId": workspace_id, "taskId": task_id,
              "attemptId": attempt_id, "manifestSha256": manifest["manifestSha256"],
              "baseCommit": manifest["baseCommit"], "inputCommit": manifest["inputCommit"], "commit": commit,
              "tree": tree, "changedPaths": changed, "adoptedPaths": adopted,
              "includedUntracked": snapshot["includedUntracked"],
              "snapshot": snapshot, "snapshotSha256": _sha(_json(snapshot)),
              "ref": f"refs/buddy/workspaces/{workspace_id}/resolutions/{output_id}-{action}",
              "diffPath": str(directory / f"resolve-{action}.patch"), "diffSha256": _sha(patch_data)}
    _pin(repository, f"refs/buddy/workspaces/{workspace_id}/retained/resolution-{output_id}-{action}", commit)
    _write_once(directory / f"resolve-{action}-pending.json", _json(record))
    _write_once(directory / f"resolve-{action}.patch", patch_data)
    return _finish_record(repository, record, directory, ref_name=f"{output_id}-{action}", ref_kind="resolutions",
                          record_name=f"resolve-{action}.json", patch_name=f"resolve-{action}.patch",
                          bindings=_RECORD_BINDINGS)


def _abandoned_record(root, manifest, repository, directory, observation, *, task_id, attempt_id, actor, reason, conflict_fingerprint):
    """Preserve the abandoned site as immutable evidence before returning input."""
    workspace_id = manifest["workspaceId"]
    output_id = _sha(_json([task_id, attempt_id]))
    names = ("abandoned.json", "abandoned-pending.json")
    existing = _replay_record(directory, names, task_id=task_id, attempt_id=attempt_id, manifest=manifest)
    if existing is not None:
        return _finish_record(repository, existing, directory, ref_name=output_id, ref_kind="abandoned",
                              record_name="abandoned.json", patch_name="abandoned.patch", bindings=_RECORD_BINDINGS)
    entries = dict(observation["tracked"])
    for path in observation["untracked"]:
        item = _file(root, path)
        if item is None:
            raise BoardError("WORKSPACE_CHANGED", "A managed path disappeared during evidence capture", path=path)
        entries[path] = [item[0], _blob(root, item[1], write=True)]
    initial = _entries(root, manifest["inputCommit"])
    changed = sorted(path for path in set(initial) | set(entries) if initial.get(path) != entries.get(path))
    tree = _tree(root, entries)
    commit = _commit_tree(root, tree, manifest["inputCommit"], workspace_id + " abandoned " + output_id)
    patch_data = _diff(repository, manifest["inputCommit"], commit)
    snapshot = _snapshot_record(manifest, observation, tree, entries, changed, [], task_id, attempt_id, commit, patch_data)
    record = {"version": 1, "kind": "abandoned-site", "action": "abandon", "actor": actor, "reason": reason,
              "conflictFingerprint": conflict_fingerprint, "workspaceId": workspace_id, "taskId": task_id,
              "attemptId": attempt_id, "manifestSha256": manifest["manifestSha256"],
              "baseCommit": manifest["baseCommit"], "inputCommit": manifest["inputCommit"], "commit": commit,
              "tree": tree, "changedPaths": changed, "includedUntracked": snapshot["includedUntracked"],
              "snapshot": snapshot, "snapshotSha256": _sha(_json(snapshot)),
              "ref": f"refs/buddy/workspaces/{workspace_id}/abandoned/{output_id}",
              "diffPath": str(directory / "abandoned.patch"), "diffSha256": _sha(patch_data)}
    _pin(repository, f"refs/buddy/workspaces/{workspace_id}/retained/abandoned-{output_id}", commit)
    _write_once(directory / "abandoned-pending.json", _json(record))
    _write_once(directory / "abandoned.patch", patch_data)
    return _finish_record(repository, record, directory, ref_name=output_id, ref_kind="abandoned",
                          record_name="abandoned.json", patch_name="abandoned.patch", bindings=_RECORD_BINDINGS)


def _resolution_result(action, state, artifact, resolved, preserved, remaining, observed_fingerprint, resolved_fingerprint):
    return {
        "action": action,
        "state": state,
        "artifact": artifact,
        "resolvedPaths": sorted(resolved)[:SCOPE_EVIDENCE_LIMIT],
        "preservedPaths": sorted(preserved)[:SCOPE_EVIDENCE_LIMIT],
        "remainingPaths": sorted(remaining)[:SCOPE_EVIDENCE_LIMIT],
        "observedFingerprint": observed_fingerprint,
        "resolvedFingerprint": resolved_fingerprint,
    }


def resolve(state_dir, manifest: dict, *, task_id: str, attempt_id: str, action: str, paths, observed_fingerprint: str, reason: str = "", actor: str = ""):
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
        if action not in RESOLUTION_ACTIONS:
            raise BoardError("INVALID_WORKSPACE", "Resolution action must be restore, adopt or abandon")
        if not isinstance(observed_fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", observed_fingerprint) is None:
            raise BoardError("INVALID_WORKSPACE", "observedFingerprint must be a workspace fingerprint")
        if any(not isinstance(value, str) or not value or "\0" in value for value in (task_id, attempt_id)):
            raise BoardError("INVALID_WORKSPACE", "task_id and attempt_id must be stable identities")
        if paths is None:
            selected_paths = []
        elif isinstance(paths, list) and all(isinstance(value, str) for value in paths):
            selected_paths = sorted({_relative(value) for value in paths})
        else:
            raise BoardError("INVALID_WORKSPACE", "paths must be a list of relative workspace paths")
        workspace_id = manifest["workspaceId"]
        workspace_dir = Path(state_dir).resolve() / "workspaces" / workspace_id
        if workspace_dir.is_symlink() or _record(workspace_dir / "manifest.json") != manifest:
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
            if _identity(repository) != manifest["repositoryId"]:
                raise BoardError("WORKSPACE_CHANGED", "The output repository identity changed")
            verify(manifest, require_unchanged=False)
            observation = _stable_observation(root, manifest["snapshot"]["executionSelectors"])
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
                                            reason=reason, conflict_fingerprint=records[0]["observedFingerprint"])
                return _resolution_result("adopt", "adopted", record, blocking, [], [],
                                          observation["fingerprint"], record["snapshot"]["observationSha256"])
            if action == "abandon":
                if selected_paths:
                    raise BoardError("INVALID_WORKSPACE", "Abandon returns the whole site and takes no path selection")
                evidence = _abandoned_record(root, manifest, repository, directory, observation,
                                             task_id=task_id, attempt_id=attempt_id, actor=actor,
                                             reason=reason, conflict_fingerprint=records[0]["observedFingerprint"])
                reset, remaining = _reset_site(root, manifest, observation)
                after = _stable_observation(root, manifest["snapshot"]["executionSelectors"])
                remaining = sorted(set(remaining) | set(_blocking_paths(root, manifest, after)))
                return _resolution_result("abandon", "abandoned" if not remaining else "open", evidence,
                                          reset, [], remaining, observation["fingerprint"], after["fingerprint"])
            selected = selected_paths or blocking
            unknown = [path for path in selected if path not in entries]
            if unknown:
                raise BoardError("INVALID_WORKSPACE", "Selected paths have no recorded failure evidence", paths=unknown[:32])
            if not selected:
                raise BoardError("WORKSPACE_CONFLICT", "The recorded failure site has no blocking path to restore")
            index = _entries(root)
            conflicting = [path for path in selected if not _entry_matches(root, entries[path], index)]
            if conflicting:
                raise BoardError("WORKSPACE_CONFLICT", "Selected paths changed after the recorded failure; the site is preserved",
                                 conflictingPaths=sorted(conflicting)[:32])
            for path in selected:
                _write_path(root, path, entries[path].get("authorized"))
            _restore_index(root, {path: entries[path].get("authorizedIndex") for path in selected
                                  if _entry_state(index, path) != entries[path].get("authorizedIndex")})
            after = _stable_observation(root, manifest["snapshot"]["executionSelectors"], write=True)
            remaining = _blocking_paths(root, manifest, after)
            preserved = sorted(set(entries) - set(selected))
            if remaining:
                # A partial restore is progress, not a completed baseline: the
                # remaining out-of-scope paths stay visible for another decision.
                return _resolution_result("restore", "open", None, selected, preserved, remaining,
                                          observation["fingerprint"], after["fingerprint"])
            record = _resolution_record(root, manifest, repository, directory, after,
                                        task_id=task_id, attempt_id=attempt_id, action="restore", actor=actor,
                                        reason=reason, conflict_fingerprint=records[0]["observedFingerprint"])
            return _resolution_result("restore", "restored", record, selected, preserved, [],
                                      observation["fingerprint"], record["snapshot"]["observationSha256"])


# -- integration verification -------------------------------------------------
def _tree_entry(root, commit, path):
    for record in _git(root, "ls-tree", "-z", commit, "--", path).split(b"\0"):
        if not record:
            continue
        metadata, _, name = record.partition(b"\t")
        if os.fsdecode(name) != path:
            continue
        mode, _kind, oid = metadata.decode().split()
        return {"mode": mode, "oid": oid}
    return None


def _is_ancestor(root, ancestor, descendant):
    try:
        _git(root, "merge-base", "--is-ancestor", ancestor, descendant)
    except BoardError:
        return False
    return True


def _tree_changes(root, before_tree, after_tree):
    """Derive a bounded path binding from two proven immutable Git trees."""
    names = [_relative(os.fsdecode(name)) for name in _git(
        root, "diff-tree", "-r", "--no-renames", "--name-only", "-z", before_tree, after_tree, "--"
    ).split(b"\0") if name]
    if len(names) > OUTPUT_ENTRY_LIMIT or len(set(names)) != len(names):
        raise BoardError("WORKSPACE_UNSUPPORTED", "The artifact has too many changed paths to verify",
                         limit=OUTPUT_ENTRY_LIMIT)
    return {name: (_tree_entry(root, after_tree, name) or None) for name in names}


def _artifact_binding(original_input, final_input, artifact):
    """Prove both input manifests and the final output against fixed Git objects."""
    _validate_manifest(original_input)
    _validate_manifest(final_input)
    if not isinstance(artifact, dict) or not isinstance(artifact.get("snapshot"), dict):
        raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The sealed output snapshot is missing")
    repository = Path(original_input["snapshot"]["repositoryPath"])
    if (_identity(repository) != original_input["repositoryId"]
            or final_input["repositoryId"] != original_input["repositoryId"]
            or final_input["checkoutId"] != original_input["checkoutId"]
            or final_input["snapshot"]["repositoryPath"] != str(repository)):
        raise BoardError("WORKSPACE_CHANGED", "The artifact input repository or checkout identity changed")
    for manifest in (original_input, final_input):
        snapshot = manifest["snapshot"]
        for ref, commit in ((snapshot["inputRef"], manifest["inputCommit"]),
                            (snapshot["stagedRef"], snapshot["stagedCommit"])):
            if _commit(repository, ref) != commit:
                raise BoardError("WORKSPACE_CONFLICT", "An immutable input reference changed", ref=ref)
        if (_line(repository, "rev-parse", manifest["inputCommit"] + "^{tree}") != manifest["inputTree"]
                or _line(repository, "rev-parse", snapshot["stagedCommit"] + "^{tree}") != snapshot["stagedTree"]):
            raise BoardError("WORKSPACE_MANIFEST_CHANGED", "A pinned input tree is inconsistent")
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
    if artifact.get("kind") == "resolution" and artifact.get("action") in ("restore", "adopt"):
        expected_ref = (f"refs/buddy/workspaces/{final_input['workspaceId']}/resolutions/"
                        f"{output_id}-{artifact['action']}")
    elif artifact.get("kind") is None:
        expected_ref = f"refs/buddy/workspaces/{final_input['workspaceId']}/outputs/{output_id}"
    else:
        raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The final output record kind is invalid")
    patch_path = artifact.get("diffPath")
    patch = _read(Path(patch_path)) if isinstance(patch_path, str) and Path(patch_path).is_absolute() else None
    if (not isinstance(commit, str) or not isinstance(ref, str)
            or ref != expected_ref
            or _commit(repository, ref) != commit
            or _line(repository, "rev-parse", commit + "^{tree}") != artifact.get("tree")
            or patch is None or _sha(patch) != artifact.get("diffSha256")
            or _sha(_diff(repository, final_input["inputCommit"], commit)) != artifact.get("diffSha256")):
        raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The final output Git objects or patch changed")
    if commit != final_input["inputCommit"]:
        parents = _line(repository, "rev-list", "--parents", "-n", "1", commit).split()
        if parents != [commit, final_input["inputCommit"]]:
            raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The final output commit has the wrong input parent")
    own_changes = _tree_changes(repository, final_input["inputTree"], artifact["tree"])
    if sorted(own_changes) != artifact.get("changedPaths"):
        raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The final output path list differs from its Git trees")
    return repository, _tree_changes(repository, original_input["inputTree"], artifact["tree"])


def integration_verify(artifact: dict, *, original_input: dict, final_input: dict,
                       path: str, ref: str, strategy: str, before_commit: str,
                       repository_id: str | None = None, checkout_id: str | None = None,
                       adjusted_paths=None, host_paths=None, reason: str | None = None) -> dict:
    """Verify that one immutable artifact is actually present in a real target.

    The target checkout identity, the resolved ``ref`` commit and both before and
    after trees come from the repository itself, never from the caller. The
    artifact is bound by the blob identity of every changed path, so a cherry-pick
    or an explicitly adjusted integration is verified by content rather than by a
    client-supplied SHA.
    """
    with _errors():
        if strategy not in ("patch", "cherry-pick", "merge"):
            raise BoardError("INVALID_WORKSPACE", "A verified integration uses patch, cherry-pick or merge")
        if not isinstance(artifact, dict):
            raise BoardError("INVALID_WORKSPACE", "The integration source artifact is missing")
        _repository, changed = _artifact_binding(original_input, final_input, artifact)
        if not isinstance(before_commit, str) or not before_commit:
            raise BoardError("INVALID_WORKSPACE", "beforeCommit is required to bind a verified integration")
        adjustments = sorted({_relative(value) for value in (adjusted_paths or [])})
        unknown_adjustments = [value for value in adjustments if value not in changed]
        if unknown_adjustments:
            raise BoardError("INVALID_WORKSPACE", "Adjusted paths must be artifact output paths", paths=unknown_adjustments[:32])
        if adjustments and not (isinstance(reason, str) and reason.strip()):
            raise BoardError("INVALID_WORKSPACE", "An adjusted integration requires an explicit reason")
        host_paths = sorted({_relative(value) for value in (host_paths or [])})
        overlap = sorted(set(host_paths) & set(changed))
        if overlap:
            raise BoardError("INVALID_WORKSPACE", "Host paths must be separate from artifact output paths", paths=overlap[:32])
        identity = inspect(path)
        if repository_id is not None and identity["repositoryId"] != repository_id:
            raise BoardError("WORKSPACE_CHANGED", "The integration target repository changed",
                             expectedRepositoryId=repository_id, actualRepositoryId=identity["repositoryId"])
        if checkout_id is not None and identity["checkoutId"] != checkout_id:
            raise BoardError("WORKSPACE_CHANGED", "The integration target checkout changed",
                             expectedCheckoutId=checkout_id, actualCheckoutId=identity["checkoutId"])
        root = Path(identity["checkoutRoot"])
        before = _commit(root, before_commit)
        after = _commit(root, ref)
        before_tree = _line(root, "rev-parse", before + "^{tree}")
        after_tree = _line(root, "rev-parse", after + "^{tree}")
        target_changes = _tree_changes(root, before_tree, after_tree)
        missing_host = sorted(set(host_paths) - set(target_changes))
        if missing_host:
            raise BoardError("INVALID_WORKSPACE", "Host paths must be actual changes in the target commit interval", paths=missing_host[:32])
        source = artifact.get("commit")
        artifact_ancestor = False
        if isinstance(source, str) and source:
            try:
                _commit(root, source)
            except BoardError:
                artifact_ancestor = False
            else:
                artifact_ancestor = _is_ancestor(root, source, after)
        matching, differing, missing = [], [], []
        for name in sorted(changed):
            expected = changed[name]
            if expected is not None:
                expected = [expected["mode"], expected["oid"]]
            actual = _tree_entry(root, after, name)
            if expected is None:
                if actual is None:
                    matching.append(name)
                else:
                    differing.append({"path": name, "artifactOid": None, "targetOid": actual["oid"],
                                      "targetMode": actual["mode"]})
                continue
            if not isinstance(expected, list) or len(expected) != 2:
                raise BoardError("INVALID_WORKSPACE", "The artifact output binding is malformed", path=name)
            if actual is None:
                missing.append(name)
            elif actual == {"mode": expected[0], "oid": expected[1]}:
                matching.append(name)
            else:
                differing.append({"path": name, "artifactMode": expected[0], "artifactOid": expected[1],
                                  "targetMode": actual["mode"], "targetOid": actual["oid"]})
        unrecorded = sorted({item["path"] for item in differing} - set(adjustments)) + sorted(set(missing) - set(adjustments))
        verification = {
            "verified": bool(_is_ancestor(root, before, after) and not unrecorded),
            "strategy": strategy,
            "target": {"kind": "checkout", "path": identity["checkoutRoot"], "checkoutId": identity["checkoutId"],
                       "repositoryId": identity["repositoryId"], "ref": ref},
            "beforeCommit": before, "beforeTree": before_tree, "afterCommit": after, "afterTree": after_tree,
            "sourceCommit": source, "sourceTree": artifact.get("tree"),
            "artifactAncestor": artifact_ancestor,
            "matchingPaths": matching[:OUTPUT_ENTRY_LIMIT],
            "differingPaths": differing[:OUTPUT_ENTRY_LIMIT],
            "missingPaths": missing[:OUTPUT_ENTRY_LIMIT],
            "adjustments": adjustments[:OUTPUT_ENTRY_LIMIT],
            "hostPaths": host_paths,
            "unrecordedPaths": unrecorded[:OUTPUT_ENTRY_LIMIT],
            "reason": reason,
        }
        return verification


# -- cleanup of one registered disposable checkout ----------------------------
def _workspace_identifier(value):
    return (isinstance(value, str) and value.startswith("ws-") and len(value) == 35
            and all(character in "0123456789abcdef" for character in value[3:]))


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
    path. The caller rechecks the Git ``buddy:<allocationId>`` lock against the
    returned identity before any deletion.
    """
    with _errors():
        _validate_manifest(manifest)
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
            if directory.is_symlink() or not directory.is_dir():
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
    """Bounded retention view of every fixed ref that belongs to this checkout."""
    identifiers = [allocation_id, manifest["workspaceId"]]
    for candidate in _allocation_candidates(manifest, retained):
        value = candidate.get("workspaceId")
        if (candidate.get("checkoutId") == manifest["checkoutId"] and _workspace_identifier(value)
                and value not in identifiers):
            identifiers.append(value)
    refs = []
    for identifier in identifiers:
        refs.extend(os.fsdecode(line) for line in _git(
            repository, "for-each-ref", "--format=%(refname)", f"refs/buddy/workspaces/{identifier}/").splitlines())
        if len(refs) >= 64:
            break
    return sorted(set(refs))[:64]


def cleanup_inspect(state_dir, manifest: dict, sealed: dict | None = None, retained=None) -> dict:
    """Eligibility facts for removing exactly one registered Buddy worktree.

    The deletion target is the *physical allocation* resolved from authoritative
    retained manifests, not the current turn/input snapshot: a continuation
    reuses the same checkout under a new logical workspace id, so the original
    worktree manifest, the exact allocation path and the original
    ``buddy:<allocationId>`` Git lock are what authorize a removal. An arbitrary
    existing cwd under ``state/workspaces`` proves nothing by itself: the source
    checkout, a borrowed directory, a sibling worktree and an unknown path are all
    reported as retention reasons instead of being deleted.
    """
    with _errors():
        _validate_manifest(manifest)
        state_dir = Path(state_dir).resolve()
        allocation = resolve_allocation(state_dir, manifest, retained)
        result = {
            "eligible": False, "reasons": [],
            "workspaceId": (allocation or manifest)["workspaceId"],
            "manifestWorkspaceId": manifest["workspaceId"],
            "kind": (allocation or manifest)["kind"],
            "checkoutId": manifest["checkoutId"], "repositoryId": manifest["repositoryId"],
            "path": allocation["path"] if allocation else manifest["checkoutRoot"], "cwd": manifest["path"],
            "allocation": allocation, "worktree": False, "locked": None,
            "unsealedPaths": [], "refs": [], "sealedObservation": None,
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
        try:
            actual = inspect(str(checkout_root))
        except BoardError:
            result["reasons"].append("checkout-missing")
            return result
        for field in ("checkoutRoot", "checkoutId", "repositoryId"):
            if actual[field] != manifest[field]:
                result["reasons"].append("identity-changed")
        try:
            _cleanup_proof(Path(actual["repositoryPath"]), checkout_root, allocation, manifest, retained, sealed, result)
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


def _cleanup_proof(repository, checkout_root, allocation, manifest, retained, sealed, result) -> None:
    """Fill one cleanup view with the worktree, seal and unsealed-change proof."""
    record = _worktree_record(repository, checkout_root)
    if not record or record.get("locked") != "buddy:" + allocation["workspaceId"] or "detached" not in record:
        result["reasons"].append("unregistered-checkout")
    else:
        result["worktree"] = True
        result["locked"] = record.get("locked")
    result["refs"] = _allocation_refs(repository, allocation["workspaceId"], manifest, retained)
    if not isinstance(sealed, dict) or not isinstance(sealed.get("snapshot"), dict):
        try:
            verify(manifest, require_unchanged=True)
        except BoardError:
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
    except BoardError:
        result["reasons"].append("sealed-output-invalid")
        return
    observation = _stable_observation(checkout_root, manifest["snapshot"]["executionSelectors"], write=True)
    unsealed = []
    try:
        entries_now, _changed, excluded_changes, _adopted = _output_entries(checkout_root, manifest, observation)
        current_tree = _tree(checkout_root, entries_now)
        if current_tree != sealed["tree"]:
            unsealed = sorted(_tree_changes(checkout_root, sealed["tree"], current_tree))
        if sorted(excluded_changes) != sorted(snapshot.get("excludedChangedPaths") or []):
            unsealed = sorted(set(unsealed) | set(excluded_changes) | set(snapshot.get("excludedChangedPaths") or []))
    except BoardError as error:
        if error.code == "WORKSPACE_UNSUPPORTED":
            result["reasons"].append("unsealed-changes")
            return
        if error.code != "WORKSPACE_SCOPE_VIOLATION":
            raise
        unsealed = list(error.details.get("paths") or [])
    if unsealed or observation["fingerprint"] != snapshot.get("observationSha256"):
        result["reasons"].append("unsealed-changes")
        result["unsealedPaths"] = unsealed[:32]
    return


def cleanup_remove(state_dir, manifest: dict, *, retained=None) -> dict:
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
        allocation = resolve_allocation(state_dir, manifest, retained)
        if allocation is None or not _inside(manifest["path"], allocation["path"]):
            raise BoardError("WORKSPACE_UNSAFE", "Cleanup only removes a registered disposable Buddy checkout",
                             path=manifest["checkoutRoot"])
        checkout_root = Path(allocation["path"])
        removal = {"removed": True, "path": str(checkout_root),
                   "repositoryPath": manifest["snapshot"]["repositoryPath"],
                   "workspaceId": allocation["workspaceId"], "manifestWorkspaceId": manifest["workspaceId"],
                   "checkoutId": manifest["checkoutId"], "alreadyRemoved": True}
        if not _path_present(checkout_root):
            return removal
        actual = inspect(str(checkout_root))
        for field in ("checkoutRoot", "checkoutId", "repositoryId"):
            if actual[field] != manifest[field]:
                raise BoardError("WORKSPACE_CHANGED", "The cleanup target identity changed", field=field)
        repository = Path(actual["repositoryPath"])
        record = _worktree_record(repository, checkout_root)
        if not record or record.get("locked") != "buddy:" + allocation["workspaceId"] or "detached" not in record:
            raise BoardError("WORKSPACE_UNSAFE", "The cleanup target is not this allocation's registered worktree",
                             path=str(checkout_root))
        removal["repositoryPath"] = str(repository)
        try:
            _git(repository, "worktree", "unlock", str(checkout_root), allowed=(0, 1))
            _git(repository, "worktree", "remove", "--force", str(checkout_root), allowed=(0,))
        except BoardError:
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
