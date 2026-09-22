"""Pinned Git workspaces and immutable artifacts, independent of board state.

The caller owns admission, reservations and process-stop evidence. In particular,
``seal`` may only be called after the Worker has proved that its child stopped.
No operation here changes the source checkout's HEAD, index or working files.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tempfile

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
    if normalized == "." and not allow_root:
        raise BoardError("INVALID_WORKSPACE", "An explicit file or directory path is required", path=value)
    return normalized


def _in_scope(path, scope):
    return any(item == "." or path == item or path.startswith(item + "/") for item in scope)


@contextmanager
def _parent(root, relative, *, create=False):
    """Open each parent by directory descriptor, never following symlinks."""
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


def _file(root, relative):
    # dir_fd and O_NOFOLLOW also cover races in intermediate path components.
    # https://docs.python.org/3/library/os.html#files-and-directories
    try:
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
    return entries


def _untracked(root, selected):
    # Ignored files are still physical inputs. Track their fingerprints so a
    # read-only run or an out-of-scope edit cannot disappear behind .gitignore;
    # only explicit selections are copied into an input snapshot.
    paths = set(_git(root, "ls-files", "--others", "-z").split(b"\0"))
    return sorted(_relative(os.fsdecode(path)) for path in paths if path)


def _observe(root, selected, *, write=False, require_selected=False):
    head = _commit(root, "HEAD")
    index = _entries(root)
    tracked, untracked, included = {}, {}, {}
    for path in index:
        item = _file(root, path)
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
    descriptor = os.open(directory / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
              "baseCommit", "inputCommit", "inputTree", "writeScope", "integrator", "targetRef", "snapshot", "manifestSha256"}
    if not isinstance(manifest, dict) or set(manifest) != fields or type(manifest.get("version")) is not int or manifest["version"] != 1:
        raise BoardError("INVALID_WORKSPACE", "Invalid workspace manifest schema")
    expected = _sha(_json({key: value for key, value in manifest.items() if key != "manifestSha256"}))
    if expected != manifest["manifestSha256"]:
        raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The workspace manifest hash does not match its contents")
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

    Read workspaces always require unchanged input. For a writable workspace,
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
                        "writeScope": intent["writeScope"], "integrator": intent["integrator"], "targetRef": intent["targetRef"], "snapshot": snapshot}
            manifest["manifestSha256"] = _sha(_json(manifest))
            _write_once(directory / "manifest.json", _json(manifest))
            return manifest


def _output_entries(root, manifest, observation):
    initial = _entries(root, manifest["inputCommit"])
    initial_index = _entries(root, manifest["snapshot"]["stagedTree"] if manifest["kind"] == "existing" else manifest["inputTree"])
    excluded = manifest["snapshot"]["excludedEntries"] if manifest["kind"] == "existing" else {}
    scope = manifest["writeScope"]
    entries = dict(observation["tracked"])
    violations = set()
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
        else:
            violations.add(path)
    for path in excluded.keys() - observation["untracked"].keys():
        excluded_changes.add(path)
    changed = sorted(path for path in initial.keys() | entries.keys() if initial.get(path) != entries.get(path))
    index_changes = {path for path in initial_index.keys() | observation["index"].keys()
                     if initial_index.get(path) != observation["index"].get(path)}
    violations.update(path for path in set(changed) | index_changes | excluded_changes if not _in_scope(path, scope))
    if violations:
        raise BoardError("WORKSPACE_SCOPE_VIOLATION", "The workspace contains changes outside its declared write scope",
                         paths=sorted(violations), changedPaths=changed)
    return entries, changed, sorted(excluded_changes)


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
    """Seal a stopped attempt's actual output, preserving its fixed Git ref/diff.

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
                entries, changed, excluded_changes = _output_entries(root, manifest, observation)
            tree = _tree(root, entries)
            if observation != _observe(root, manifest["snapshot"]["executionSelectors"]):
                raise BoardError("WORKSPACE_CHANGED", "The workspace changed during output sealing")
            commit = manifest["inputCommit"] if tree == manifest["inputTree"] else _commit_tree(root, tree, manifest["inputCommit"], workspace_id + " output " + output_id)
            patch_data = _diff(repository, manifest["inputCommit"], commit)
            snapshot = {"headCommit": observation["head"], "indexSha256": _sha(_json(observation["index"])),
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
                      "ref": f"refs/buddy/workspaces/{workspace_id}/outputs/{output_id}",
                      "diffPath": str(directory / "output.patch"), "diffSha256": _sha(patch_data)}
            # Retention precedes the recovery record: GC must not discard a
            # chosen output while final reference publication is interrupted.
            _pin(repository, f"refs/buddy/workspaces/{workspace_id}/retained/output-{output_id}", commit)
            _write_once(directory / "pending.json", _json(output))
            _write_once(directory / "output.patch", patch_data)
            return _finish_output(repository, output, directory)
