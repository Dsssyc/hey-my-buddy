"""Remove private allocations and the refs in repositories still at their identity."""
from __future__ import annotations

from pathlib import Path
import re

from ... import private_dirs
from ...paths import native_path
from ...errors import BoardError
from ...protocol.reasons import reason
from ...protocol.workspace_manifest import project_id
from . import workspace
from .macro_task_deletion_progress import collect_skipped


_PREFIX = "refs/hey-my-buddy/workspaces/"
_SKIP_CODES = {
    "REF_UNPROVEN": "macro-task-deletion.ref-unproven",
    "REF_CHANGED": "macro-task-deletion.ref-changed",
    "OWNER_UNPROVEN": "macro-task-deletion.owner-unproven",
    "DEPENDENCY_RETAINED": "macro-task-deletion.dependency-retained",
    "owner-unproven": "macro-task-deletion.owner-unproven",
    "not-a-managed-worktree": "macro-task-deletion.not-a-managed-worktree",
    "unsafe-path": "macro-task-deletion.unsafe-path",
    "checkout-missing": "macro-task-deletion.checkout-missing",
    "identity-changed": "macro-task-deletion.identity-changed",
    "unregistered-checkout": "macro-task-deletion.unregistered-checkout",
    "lock-mismatch": "macro-task-deletion.lock-mismatch",
    "attached-branch": "macro-task-deletion.attached-branch",
}


def _skip(kind: str, path: str, code: str, workspace_id: str | None = None) -> dict:
    return {"kind": kind, "path": path, "workspaceId": workspace_id,
            "reason": reason(_SKIP_CODES[code])}


def _repository_current(manifest: dict) -> Path | None:
    repository = workspace.repository_at(manifest["path"])
    if repository is None or workspace.read_project_id(repository) != project_id(manifest):
        return None
    return Path(repository)


def _fixed_refs(manifest: dict) -> dict[str, str]:
    identifier = manifest["workspaceId"]
    prefix = _PREFIX + identifier + "/"
    snapshot = manifest["snapshot"]
    if (snapshot["inputRef"] != prefix + "input" or snapshot["stagedRef"] != prefix + "staged"):
        raise BoardError("WORKSPACE_MANIFEST_CHANGED", "Input reference does not belong to its workspace")
    return {snapshot["inputRef"]: manifest["inputCommit"],
            snapshot["stagedRef"]: snapshot["stagedCommit"],
            prefix + "retained/input": manifest["inputCommit"],
            prefix + "retained/staged": snapshot["stagedCommit"]}


def _artifact_refs(artifact: dict, manifests: dict[str, dict]) -> dict[str, str]:
    identifier = artifact.get("workspaceId")
    manifest = manifests.get(identifier)
    if manifest is None or artifact.get("manifestSha256") != manifest["manifestSha256"]:
        return {}
    ref, commit = artifact.get("ref"), artifact.get("commit")
    snapshot = artifact.get("snapshot")
    if (not isinstance(ref, str) or not isinstance(commit, str)
            or not isinstance(snapshot, dict)
            or artifact.get("snapshotSha256") != workspace._sha(workspace._json(snapshot))
            or any(artifact.get(key) != snapshot.get(key) for key in
                   ("workspaceId", "taskId", "attemptId", "manifestSha256", "commit", "tree"))):
        return {}
    task_id, attempt_id = artifact.get("taskId"), artifact.get("attemptId")
    if not all(isinstance(value, str) and value for value in (task_id, attempt_id)):
        return {}
    record_id = workspace._sha(workspace._json([task_id, attempt_id]))
    prefix = _PREFIX + identifier + "/"
    kind = artifact.get("kind")
    if kind in (None, "host-output"):
        suffix = record_id + ("-host" if kind == "host-output" else "")
        family, retained_family = "outputs/", "output-"
    elif kind == "resolution" and artifact.get("action") in workspace.RESOLUTION_ACTIONS:
        suffix = record_id + "-" + artifact["action"]
        family, retained_family = "resolutions/", "resolution-"
    elif kind == "abandoned-site":
        suffix = record_id
        family, retained_family = "abandoned/", "abandoned-"
    else:
        return {}
    if ref != prefix + family + suffix:
        return {}
    return {ref: commit, prefix + "retained/" + retained_family + suffix: commit}


def _expected_refs(by_id: dict[str, dict], artifacts: list[dict]) -> dict[str, dict[str, str]]:
    """Bind explicit snapshots and their equivalent physical output names once."""
    expected = {identifier: _fixed_refs(manifest) for identifier, manifest in by_id.items()}
    paired_outputs = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        bound = _artifact_refs(artifact, by_id)
        for ref, oid in bound.items():
            identifier = artifact["workspaceId"]
            previous = expected[identifier].setdefault(ref, oid)
            if previous != oid:
                raise BoardError("WORKSPACE_CONFLICT", "Conflicting retained artifact references", ref=ref)
        if bound and artifact.get("kind") in (None, "host-output"):
            paired_outputs.append(artifact)
    # Publication deduplicates equal Worker/Host snapshots, although their
    # physical seal names differ. A counterpart is bound only to that same
    # commit; a separately published output keeps its own explicit binding.
    for artifact in paired_outputs:
        identifier = artifact["workspaceId"]
        record_id = workspace._sha(workspace._json([artifact["taskId"], artifact["attemptId"]]))
        suffix = record_id + ("" if artifact.get("kind") == "host-output" else "-host")
        prefix = _PREFIX + identifier + "/"
        for ref in (prefix + "outputs/" + suffix, prefix + "retained/output-" + suffix):
            expected[identifier].setdefault(ref, artifact["commit"])
    return expected


def _refs(repository: Path, identifier: str) -> dict[str, str | None]:
    prefix = _PREFIX + identifier + "/"
    output = workspace._git(repository, "for-each-ref", "--format=%(refname)%00%(objectname)%00%(symref)", prefix)
    refs = {}
    for line in output.splitlines():
        parts = line.split(b"\0")
        if len(parts) != 3:
            raise BoardError("WORKSPACE_GIT_ERROR", "Invalid workspace reference inventory")
        ref, oid, symbolic = parts
        try:
            name = ref.decode("utf-8", errors="strict")
            value = oid.decode("ascii", errors="strict")
        except UnicodeError as error:
            raise BoardError("WORKSPACE_GIT_ERROR", "Invalid workspace reference inventory") from error
        if (not name.startswith(prefix)
                or not symbolic and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is None):
            raise BoardError("WORKSPACE_GIT_ERROR", "Invalid workspace reference inventory")
        # A symbolic entry is observed, but is never a proven ordinary ref.
        refs[name] = None if symbolic else value
    return refs


def _delete_refs(repository: Path, identifier: str, expected: dict[str, str]) -> list[dict]:
    with collect_skipped() as skipped:
        anchor = _PREFIX + identifier + "/retained/input"
        for ref, actual in sorted(_refs(repository, identifier).items(), key=lambda item: item[0] == anchor):
            # Keep the relocation proof until every other reference is gone.
            if ref == anchor and skipped:
                continue
            bound = expected.get(ref)
            if bound is None or actual is None:
                skipped.append(_skip("ref", ref, "REF_UNPROVEN", identifier))
                continue
            if actual != bound:
                skipped.append(_skip("ref", ref, "REF_CHANGED", identifier))
                continue
            # A competing update can win after enumeration. Distinguish that
            # identity change from an actual Git failure without deleting its value.
            # Never follow a ref that becomes symbolic after this observation.
            try:
                workspace._git(repository, "update-ref", "--no-deref", "-d", ref, bound)
            except BoardError:
                current = _refs(repository,identifier)
                if current.get(ref) == bound:
                    raise
                if ref in current:
                    skipped.append(_skip("ref",ref,"REF_CHANGED",identifier))
        return skipped


def _record_proven(state: Path, manifest: dict) -> bool:
    identifier = manifest["workspaceId"]
    directory = state / "workspaces" / identifier
    if private_dirs.linked(directory) or workspace.allocation_lock_directory(state, identifier) != directory:
        return False
    if workspace._record(directory / "manifest.json") != manifest:
        return False
    request = workspace._record(directory / "request.json")
    pinned = workspace._record(directory / "input.json")
    if not isinstance(request, dict) or not isinstance(pinned, dict):
        return False
    request_id = request["requestId"]
    if not isinstance(request_id, str) or not request_id or "\0" in request_id:
        return False
    expected = "ws-" + workspace._sha(workspace._json([str(state), request_id]))[:32]
    return (expected == identifier
            and request["version"] == manifest["version"]
            and request["intent"]["kind"] == manifest["kind"]
            and pinned["inputCommit"] == manifest["inputCommit"]
            and pinned["inputTree"] == manifest["inputTree"]
            and pinned["snapshot"]["stagedCommit"] == manifest["snapshot"]["stagedCommit"])


def _tombstone(state: Path, identifier: str) -> Path:
    return state / "workspaces" / (".deleting-" + identifier)


def _remove_tombstone(state: Path, identifier: str) -> bool:
    tomb = _tombstone(state, identifier)
    if not workspace._path_present(tomb):
        return False
    if private_dirs.linked(tomb) or not tomb.is_dir():
        raise BoardError("WORKSPACE_UNSAFE", "The deletion tombstone is unsafe")
    private_dirs.remove_tree(tomb)
    return True


def delete_workspaces(store, manifests: list[dict], artifacts: list[dict], *, first_execution_times=None,
                      candidate_paths=None) -> list[dict]:
    """Remove proven physical allocations, exact refs, then their private records.

    The caller owns the storage lock, shared-dependency check and settled-task
    proof. Input manifests and artifacts come from those task rows while they
    remain in the board. No database transaction is held through this function.
    """
    state = store.directory.resolve()
    first_execution_times = first_execution_times or {}
    candidate_paths = candidate_paths or {}
    by_id = {}
    with collect_skipped() as skipped:
        for manifest in manifests:
            workspace._validate_manifest(manifest)
            identifier = manifest["workspaceId"]
            if identifier in by_id and by_id[identifier] != manifest:
                raise BoardError("WORKSPACE_CONFLICT", "Conflicting retained workspace manifests")
            by_id[identifier] = manifest
        # A later turn may prepare another logical manifest for the same checkout.
        # Its refs must remain available until the physical checkout is proved and
        # removed, even when its workspace id sorts before the allocation id.
        blocked = {}
        for identifier, manifest in sorted(by_id.items()):
            if manifest["kind"] != "worktree":
                continue
            directory = state / "workspaces" / identifier
            if not workspace._path_present(directory):
                continue
            if workspace.allocation_lock_directory(state, identifier) != directory:
                skipped.append(_skip("allocation", str(directory), "OWNER_UNPROVEN", identifier))
                blocked[manifest["checkoutRoot"]] = _SKIP_CODES["OWNER_UNPROVEN"]
                continue
            with workspace._lock(directory, create=False):
                if not _record_proven(state, manifest):
                    skipped.append(_skip("allocation", str(directory), "OWNER_UNPROVEN", identifier))
                    blocked[manifest["checkoutRoot"]] = _SKIP_CODES["OWNER_UNPROVEN"]
                    continue
                repository = workspace.locate_repository(manifest, candidates=candidate_paths.get(identifier, ()))
                if repository is None:
                    # The private allocation record proves its private bytes. Its
                    # former repository's refs and registration are no longer ours
                    # to pursue; the record phase removes this whole allocation.
                    continue
                checkout = directory / "checkout"
                checkout_retained = False
                if workspace._path_present(checkout):
                    from .checkout_processes import require_idle_checkout
                    require_idle_checkout(checkout, first_execution_at=first_execution_times.get(
                        manifest["checkoutRoot"]))
                # A missing checkout may still have its original registration.
                # Complete that same proven boundary before refs or records leave.
                proof = workspace.deletion_inspect(state, manifest, repository=repository)
                if proof["reasons"]:
                    skipped.extend(_skip("checkout", str(checkout), reason, identifier)
                                   for reason in proof["reasons"])
                    checkout_retained = True
                else:
                    workspace.cleanup_remove(state, manifest, proof=proof, repository=repository)
                if checkout_retained or workspace._path_present(checkout):
                    skipped.append(_skip("allocation", str(directory), "DEPENDENCY_RETAINED", identifier))
                    blocked[manifest["checkoutRoot"]] = _SKIP_CODES["DEPENDENCY_RETAINED"]
                    continue

        for identifier, manifest in sorted(by_id.items()):
            directory = state / "workspaces" / identifier
            blocked_reason = blocked.get(manifest["checkoutRoot"])
            if blocked_reason is not None:
                if manifest["kind"] != "worktree" and workspace._path_present(directory):
                    skipped.append(_skip("allocation", str(directory), "DEPENDENCY_RETAINED", identifier))
                continue
            if not workspace._path_present(directory):
                repository = workspace.locate_repository(manifest, candidates=candidate_paths.get(identifier, ()), repair=False)
                if repository is None:
                    _remove_tombstone(state, identifier)
                    continue
                expected = _expected_refs({identifier: manifest}, artifacts)[identifier]
                ref_skips = _delete_refs(repository, identifier, expected)
                skipped.extend(ref_skips)
                if not ref_skips and not _refs(repository, identifier):
                    _remove_tombstone(state, identifier)
                continue
            if workspace._path_present(_tombstone(state, identifier)):
                skipped.append(_skip("allocation", str(directory), "OWNER_UNPROVEN", identifier))
                continue
            if workspace.allocation_lock_directory(state, identifier) != directory:
                skipped.append(_skip("allocation", str(directory), "OWNER_UNPROVEN", identifier))
                continue
            with workspace._lock(directory, create=False):
                if not _record_proven(state, manifest):
                    skipped.append(_skip("allocation", str(directory), "OWNER_UNPROVEN", identifier))
                    continue
                repository = workspace.locate_repository(manifest, candidates=candidate_paths.get(identifier, ()), repair=False)
                current_repository = repository is not None
                checkout = directory / "checkout"
                if current_repository and manifest["kind"] == "worktree" and workspace._path_present(checkout):
                    skipped.append(_skip("allocation", str(directory), "DEPENDENCY_RETAINED", identifier))
                    continue
                if current_repository:
                    expected = _expected_refs({identifier: manifest}, artifacts)[identifier]
                    ref_skips = _delete_refs(repository, identifier, expected)
                    skipped.extend(ref_skips)
                    if ref_skips or _refs(repository, identifier):
                        skipped.append(_skip("allocation", str(directory), "DEPENDENCY_RETAINED", identifier))
                        continue
                elif manifest["kind"] == "worktree" and workspace._path_present(checkout) and not private_dirs.linked(checkout):
                    from .checkout_processes import require_idle_checkout
                    require_idle_checkout(checkout, first_execution_at=first_execution_times.get(
                        manifest["checkoutRoot"]))
                tomb = _tombstone(state, identifier)
                if workspace._path_present(tomb):
                    skipped.append(_skip("allocation", str(tomb), "OWNER_UNPROVEN", identifier))
                    continue
            # Windows cannot rename a directory while its descendant .lock is open.
            # The caller keeps the storage lock. Host sealing and scope resolution
            # check the durable deletion mark under this same workspace lock before
            # writing files or refs, so neither can cross the proof-to-rename gap.
            native_path(directory).rename(native_path(tomb))
            private_dirs.remove_tree(tomb)
        return skipped
