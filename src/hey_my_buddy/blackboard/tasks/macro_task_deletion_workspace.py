"""Remove board-owned workspace bytes using retained, exact Git identities."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Callable

from ... import private_dirs
from ...errors import BoardError
from ...protocol.reasons import reason
from . import workspace


_PREFIX = "refs/hey-my-buddy/workspaces/"
_SKIP_CODES = {
    "REF_UNPROVEN": "macro-task-deletion.ref-unproven",
    "REF_CHANGED": "macro-task-deletion.ref-changed",
    "OWNER_UNPROVEN": "macro-task-deletion.owner-unproven",
    "REPOSITORY_CHANGED": "macro-task-deletion.repository-changed",
    "DEPENDENCY_RETAINED": "macro-task-deletion.dependency-retained",
    "owner-unproven": "macro-task-deletion.owner-unproven",
    "not-a-managed-worktree": "macro-task-deletion.not-a-managed-worktree",
    "unsafe-path": "macro-task-deletion.unsafe-path",
    "checkout-missing": "macro-task-deletion.checkout-missing",
    "identity-changed": "macro-task-deletion.identity-changed",
    "unregistered-checkout": "macro-task-deletion.unregistered-checkout",
    "lock-mismatch": "macro-task-deletion.lock-mismatch",
    "attached-branch": "macro-task-deletion.attached-branch",
    "unsealed-changes": "macro-task-deletion.unsealed-changes",
    "unsealed-handoff": "macro-task-deletion.unsealed-handoff",
    "sealed-output-invalid": "macro-task-deletion.sealed-output-invalid",
}


def _skip(kind: str, path: str, code: str, workspace_id: str | None = None) -> dict:
    return {"kind": kind, "path": path, "workspaceId": workspace_id,
            "reason": reason(_SKIP_CODES[code])}


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


def _refs(repository: Path, identifier: str) -> dict[str, str]:
    prefix = _PREFIX + identifier + "/"
    output = workspace._git(repository, "for-each-ref", "--format=%(refname)%00%(objectname)", prefix)
    refs = {}
    for line in output.splitlines():
        ref, separator, oid = line.partition(b"\0")
        try:
            name = ref.decode("utf-8", errors="strict")
            value = oid.decode("ascii", errors="strict")
        except UnicodeError as error:
            raise BoardError("WORKSPACE_GIT_ERROR", "Invalid workspace reference inventory") from error
        if (not separator or not name.startswith(prefix)
                or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is None):
            raise BoardError("WORKSPACE_GIT_ERROR", "Invalid workspace reference inventory")
        refs[name] = value
    return refs


def _delete_refs(repository: Path, identifier: str, expected: dict[str, str]) -> list[dict]:
    skipped = []
    for ref, actual in _refs(repository, identifier).items():
        bound = expected.get(ref)
        if bound is None:
            skipped.append(_skip("ref", ref, "REF_UNPROVEN", identifier))
            continue
        if actual != bound:
            skipped.append(_skip("ref", ref, "REF_CHANGED", identifier))
            continue
        if workspace._line(repository, "symbolic-ref", "--quiet", ref, allowed=(0, 1)):
            skipped.append(_skip("ref", ref, "REF_UNPROVEN", identifier))
            continue
        # A competing update can win after enumeration. Distinguish that
        # identity change from an actual Git failure without deleting its value.
        try:
            workspace._git(repository, "update-ref", "-d", ref, bound)
        except BoardError:
            current = _refs(repository,identifier).get(ref)
            if current == bound:
                raise
            if current is not None:
                skipped.append(_skip("ref",ref,"REF_CHANGED",identifier))
    return skipped


def _record_proven(state: Path, manifest: dict) -> bool:
    identifier = manifest["workspaceId"]
    directory = state / "workspaces" / identifier
    if workspace.allocation_lock_directory(state, identifier) != directory:
        return False
    records = [directory / name for name in ("manifest.json", "request.json", "input.json")]
    if any(private_dirs.linked_component(path) for path in records):
        return False
    if workspace._record(directory / "manifest.json") != manifest:
        return False
    request = workspace._record(directory / "request.json")
    pinned = workspace._record(directory / "input.json")
    if not isinstance(request, dict) or not isinstance(pinned, dict):
        return False
    try:
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
    except (KeyError, TypeError, ValueError):
        return False


def _tombstone(state: Path, identifier: str) -> Path:
    return state / "workspaces" / (".deleting-" + identifier)


def _remove_tombstone(state: Path, identifier: str) -> bool:
    tomb = _tombstone(state, identifier)
    if not workspace._path_present(tomb):
        return False
    if private_dirs.linked_component(tomb) is not None or not tomb.is_dir():
        raise BoardError("WORKSPACE_UNSAFE", "The deletion tombstone is unsafe")
    private_dirs.remove_tree(tomb)
    return True


def delete_workspaces(store, manifests: list[dict], artifacts: list[dict],
                      phase_completed: Callable[[str], None] | None = None) -> list[dict]:
    """Remove proven physical allocations, exact refs, then their private records.

    The caller owns the storage lock, shared-dependency check and settled-task
    proof. Input manifests and artifacts come from those task rows while they
    remain in the board. No database transaction is held through this function.
    """
    state = store.directory.resolve()
    by_id = {}
    skipped = []
    for manifest in manifests:
        workspace._validate_manifest(manifest)
        identifier = manifest["workspaceId"]
        if identifier in by_id and by_id[identifier] != manifest:
            raise BoardError("WORKSPACE_CONFLICT", "Conflicting retained workspace manifests")
        by_id[identifier] = manifest
    expected = {identifier: _fixed_refs(manifest) for identifier, manifest in by_id.items()}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        for ref, oid in _artifact_refs(artifact, by_id).items():
            identifier = artifact["workspaceId"]
            previous = expected[identifier].setdefault(ref, oid)
            if previous != oid:
                raise BoardError("WORKSPACE_CONFLICT", "Conflicting retained artifact references", ref=ref)

    # A later turn may prepare another logical manifest for the same checkout.
    # Its refs must remain available until the physical checkout is proved and
    # removed, even when its workspace id sorts before the allocation id.
    blocked = set()
    for identifier, manifest in sorted(by_id.items()):
        if manifest["kind"] != "worktree":
            continue
        directory = state / "workspaces" / identifier
        if not directory.exists() and not directory.is_symlink():
            continue
        if not _record_proven(state, manifest):
            skipped.append(_skip("allocation", str(directory), "OWNER_UNPROVEN", identifier))
            blocked.add((manifest["repositoryId"], manifest["checkoutId"]))
            continue
        with workspace._lock(directory, create=False):
            if not _record_proven(state, manifest):
                skipped.append(_skip("allocation", str(directory), "OWNER_UNPROVEN", identifier))
                blocked.add((manifest["repositoryId"], manifest["checkoutId"]))
                continue
            repository = Path(manifest["snapshot"]["repositoryPath"])
            if (private_dirs.linked_component(repository) is not None
                    or workspace._identity(repository) != manifest["repositoryId"]):
                skipped.append(_skip("allocation", str(directory), "REPOSITORY_CHANGED", identifier))
                blocked.add((manifest["repositoryId"], manifest["checkoutId"]))
                continue
            checkout = directory / "checkout"
            checkout_retained = False
            if workspace._path_present(checkout):
                allocation = workspace.resolve_allocation(state, manifest, manifests)
                if allocation is None or allocation["workspaceId"] != identifier:
                    skipped.append(_skip("checkout", str(checkout), "OWNER_UNPROVEN", identifier))
                    checkout_retained = True
                else:
                    # A complete checkout uses the established Git/seal proof;
                    # interrupted removal is checked without restoring files.
                    proof = workspace.deletion_inspect(state, manifest, artifacts, retained=manifests)
                    if proof["reasons"]:
                        skipped.extend(_skip("checkout", str(checkout), reason, identifier)
                                       for reason in proof["reasons"])
                        checkout_retained = True
                    else:
                        workspace.cleanup_remove(state, manifest, retained=manifests,
                                                 resume=proof["resume"])
            if checkout_retained or workspace._path_present(checkout):
                skipped.append(_skip("allocation", str(directory), "DEPENDENCY_RETAINED", identifier))
                blocked.add((manifest["repositoryId"], manifest["checkoutId"]))
                continue
            if phase_completed is not None:
                phase_completed("checkout")

    for identifier, manifest in sorted(by_id.items()):
        directory = state / "workspaces" / identifier
        if (manifest["repositoryId"], manifest["checkoutId"]) in blocked:
            continue
        if not directory.exists() and not directory.is_symlink():
            repository = Path(manifest["snapshot"]["repositoryPath"])
            if private_dirs.linked_component(directory) is not None:
                skipped.append(_skip("allocation", str(directory), "OWNER_UNPROVEN", identifier))
                continue
            if (private_dirs.linked_component(repository) is not None
                    or workspace._identity(repository) != manifest["repositoryId"]):
                skipped.append(_skip("ref", _PREFIX + identifier + "/", "REPOSITORY_CHANGED", identifier))
                continue
            ref_skips = _delete_refs(repository, identifier, expected[identifier])
            skipped.extend(ref_skips)
            if phase_completed is not None and not ref_skips and not _refs(repository, identifier):
                phase_completed("refs")
            if not ref_skips and not _refs(repository, identifier):
                if _remove_tombstone(state, identifier) and phase_completed is not None:
                    phase_completed("allocation")
            continue
        if workspace._path_present(_tombstone(state, identifier)):
            skipped.append(_skip("allocation", str(directory), "OWNER_UNPROVEN", identifier))
            continue
        if not _record_proven(state, manifest):
            skipped.append(_skip("allocation", str(directory), "OWNER_UNPROVEN", identifier))
            continue
        with workspace._lock(directory, create=False):
            if not _record_proven(state, manifest):
                skipped.append(_skip("allocation", str(directory), "OWNER_UNPROVEN", identifier))
                continue
            repository = Path(manifest["snapshot"]["repositoryPath"])
            if (private_dirs.linked_component(repository) is not None
                    or workspace._identity(repository) != manifest["repositoryId"]):
                skipped.append(_skip("allocation", str(directory), "REPOSITORY_CHANGED", identifier))
                continue
            if manifest["kind"] == "worktree" and workspace._path_present(directory / "checkout"):
                skipped.append(_skip("allocation", str(directory), "DEPENDENCY_RETAINED", identifier))
                continue
            ref_skips = _delete_refs(repository, identifier, expected[identifier])
            skipped.extend(ref_skips)
            if ref_skips or _refs(repository, identifier):
                skipped.append(_skip("allocation", str(directory), "DEPENDENCY_RETAINED", identifier))
                continue
            if phase_completed is not None:
                phase_completed("refs")
            tomb = _tombstone(state, identifier)
            if workspace._path_present(tomb):
                skipped.append(_skip("allocation", str(tomb), "OWNER_UNPROVEN", identifier))
                continue
            directory.rename(tomb)
            private_dirs.remove_tree(tomb)
            if phase_completed is not None:
                phase_completed("allocation")
    return skipped
