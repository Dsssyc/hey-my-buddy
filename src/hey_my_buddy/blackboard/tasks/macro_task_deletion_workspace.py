"""Remove board-owned workspace bytes using retained, exact Git identities."""
from __future__ import annotations

from pathlib import Path

from ... import private_dirs
from ...errors import BoardError
from . import workspace


_PREFIX = "refs/hey-my-buddy/workspaces/"


def _skip(kind: str, path: str, reason: str, workspace_id: str | None = None) -> dict:
    return {"kind": kind, "path": path, "workspaceId": workspace_id,
            "reasonCode": reason, "reasons": [reason]}


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
    if not isinstance(ref, str) or not isinstance(commit, str):
        return {}
    prefix = _PREFIX + identifier + "/"
    retained = None
    for family, retain in (("outputs/", "output-"), ("resolutions/", "resolution-"),
                           ("abandoned/", "abandoned-")):
        if ref.startswith(prefix + family):
            suffix = ref[len(prefix + family):]
            if suffix and "/" not in suffix and suffix not in (".", ".."):
                retained = prefix + "retained/" + retain + suffix
            break
    if retained is None:
        return {}
    return {ref: commit, retained: commit}


def _refs(repository: Path, identifier: str) -> dict[str, str]:
    prefix = _PREFIX + identifier + "/"
    output = workspace._git(repository, "for-each-ref", "--format=%(refname)%00%(objectname)", prefix)
    refs = {}
    for line in output.splitlines():
        ref, separator, oid = line.partition(b"\0")
        name = ref.decode("utf-8", errors="strict")
        if not separator or not name.startswith(prefix):
            raise BoardError("WORKSPACE_GIT_ERROR", "Invalid workspace reference inventory")
        refs[name] = oid.decode("ascii", errors="strict")
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
        try:
            workspace._git(repository, "update-ref", "-d", ref, bound)
        except BoardError:
            current = _refs(repository, identifier).get(ref)
            if current is not None:
                skipped.append(_skip("ref", ref, "REF_CHANGED" if current != bound else "REF_REMOVE_FAILED", identifier))
    return skipped


def _record_proven(state: Path, manifest: dict) -> bool:
    identifier = manifest["workspaceId"]
    directory = state / "workspaces" / identifier
    if (not workspace._workspace_identifier(identifier) or private_dirs.linked_component(directory)
            or not directory.is_dir() or workspace._record(directory / "manifest.json") != manifest):
        return False
    request = workspace._record(directory / "request.json")
    pinned = workspace._record(directory / "input.json")
    if not isinstance(request, dict) or not isinstance(pinned, dict):
        return False
    try:
        expected, expected_directory = workspace._workspace_directory(state, request["requestId"])
        return (expected == identifier and expected_directory == directory
                and request["version"] == manifest["version"]
                and request["intent"]["kind"] == manifest["kind"]
                and pinned["inputCommit"] == manifest["inputCommit"]
                and pinned["inputTree"] == manifest["inputTree"]
                and pinned["snapshot"]["stagedCommit"] == manifest["snapshot"]["stagedCommit"])
    except (KeyError, TypeError, ValueError):
        return False


def delete_workspaces(store, manifests: list[dict], artifacts: list[dict]) -> list[dict]:
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

    for identifier, manifest in sorted(by_id.items()):
        directory = state / "workspaces" / identifier
        if not directory.exists() and not directory.is_symlink():
            continue
        if not _record_proven(state, manifest):
            skipped.append(_skip("allocation", str(directory), "OWNER_UNPROVEN", identifier))
            continue
        with workspace._lock(directory):
            if not _record_proven(state, manifest):
                skipped.append(_skip("allocation", str(directory), "OWNER_UNPROVEN", identifier))
                continue
            repository = Path(manifest["snapshot"]["repositoryPath"])
            if (private_dirs.linked_component(repository) is not None
                    or workspace._identity(repository) != manifest["repositoryId"]):
                skipped.append(_skip("allocation", str(directory), "REPOSITORY_CHANGED", identifier))
                continue
            checkout = directory / "checkout"
            checkout_retained = False
            if manifest["kind"] == "worktree" and workspace._path_present(checkout):
                allocation = workspace.resolve_allocation(state, manifest, manifests)
                if allocation is None or allocation["workspaceId"] != identifier:
                    skipped.append(_skip("checkout", str(checkout), "OWNER_UNPROVEN", identifier))
                    checkout_retained = True
                else:
                    # A complete checkout uses the established Git/seal proof;
                    # interrupted removal is checked without restoring files.
                    proof = workspace.deletion_inspect(state, manifest, artifacts)
                    if proof["reasons"]:
                        skipped.extend(_skip("checkout", str(checkout), reason, identifier)
                                       for reason in proof["reasons"])
                        checkout_retained = True
                    else:
                        workspace.cleanup_remove(state, manifest, retained=manifests,
                                                 resume=proof["resume"])
            if checkout_retained or workspace._path_present(checkout):
                skipped.append(_skip("allocation", str(directory), "DEPENDENCY_RETAINED", identifier))
                continue
            ref_skips = _delete_refs(repository, identifier, expected[identifier])
            skipped.extend(ref_skips)
            if ref_skips or _refs(repository, identifier):
                skipped.append(_skip("allocation", str(directory), "DEPENDENCY_RETAINED", identifier))
                continue
            private_dirs.remove_tree(directory)
    return skipped
