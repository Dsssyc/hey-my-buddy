"""Deterministic in-process workspace double for micro-task workflow tests.

It implements exactly the ``hey_my_buddy.blackboard.tasks.workspace`` interface from the productivity
contract with observable calls and injectable failures, so the workflow
coordinator's transaction, idempotency and exclusion logic can run without Git.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import hashlib
import re
from pathlib import Path

from hey_my_buddy.blackboard.store.db import canonical_json, sha256_text
from hey_my_buddy.errors import BoardError
from support import native_read_bytes, native_read_text, native_write_bytes


class MockWorkspace:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.manifests: dict[tuple[str, str], dict] = {}
        self.input_paths: dict[str, list[str]] = {}
        self.prepare_calls: list[tuple[str, dict]] = []
        self.verify_calls: list[dict] = []
        self.seal_calls: list[tuple[str, str]] = []
        self.fail_prepare: set[str] = set()
        self.fail_seal: set[str] = set()
        self.dirty = False
        self.stash_inventories: dict[str, dict] = {}
        self.stash_inventory_calls: list[str] = []
        self.fail_stash_inventory = False

    # -- helpers ------------------------------------------------------------
    def _repository_root(self, cwd: str) -> str:
        path = Path(cwd)
        for candidate in [path, *path.parents]:
            if (candidate / ".git").exists():
                return str(candidate)
            # Fixtures under a source checkout must not inherit its repository
            # identity; every simulated repository belongs to this private root.
            if candidate == self.root:
                break
        return str(path)

    def repository_at(self, cwd: str, *, allow_parent=False):
        return self._repository_root(cwd)

    def project_root(self, cwd: str):
        return self._repository_root(str(cwd))

    def ensure_project_id(self, repository, *, preferred=None):
        return f"repo:{self._repository_root(str(repository))}"

    def _checkout_identity(self, cwd: str) -> dict:
        """An allocated worktree keeps its identity when reopened as existing.

        There is no real .git file in this double's worktrees; retain the identity
        established by preparation instead of treating the path as a new repository.
        """
        path = Path(cwd).resolve()
        known = [manifest for manifest in self.manifests.values()
                 if path.is_relative_to(Path(manifest["checkoutRoot"]).resolve())]
        if known:
            manifest = max(known, key=lambda item: len(Path(item["checkoutRoot"]).parts))
            return {key: manifest[key] for key in ("checkoutRoot", "projectId")}
        root = self._repository_root(cwd)
        return {"checkoutRoot": root, "projectId": f"repo:{root}"}

    def _manifest(self, *, state_dir: Path, request_id: str, intent: dict, handoff=None) -> dict:
        kind = intent.get("kind", "existing")
        cwd = intent.get("cwd")
        if kind == "worktree":
            path = str(self.root / "worktrees" / request_id)
            Path(path).mkdir(parents=True, exist_ok=True)
        else:
            path = str(cwd)
        identity = self._checkout_identity(intent.get("cwd") or path)
        if kind == "worktree":
            identity = {**identity, "checkoutRoot": path}
        base = intent.get("base") if isinstance(intent.get("base"), dict) else {}
        base_commit = base.get("ref") or "a" * 40
        manifest = {
            "version": 5,
            "workspaceId": "ws-" + sha256_text(json.dumps([str(Path(state_dir).resolve()), request_id],sort_keys=True,separators=(",",":"),ensure_ascii=True))[:32],
            "kind": kind,
            "path": path,
            **identity,
            "access": intent.get("access", "write"),
            "baseCommit": base_commit,
            "inputCommit": sha256_text(f"input:{request_id}"),
            "inputTree": "c" * 40,
            "writeScope": list(intent.get("writeScope", [])),
            "integrator": intent.get("integrator"),
            "targetRef": intent.get("targetRef"),
            # Like the real module's snapshot, every logical workspace of one
            # source repository names the same shared repository path: the stash
            # facts of a run key on it across turns and allocations.
            "snapshot": {"repositoryPath": self._repository_root(intent.get("cwd") or path),
                         "stagedTree": "c" * 40, "excludedEntries": {}},
        }
        selection = intent.get("executionSelection", {"kind": "selectors", "selectors": list(intent.get("includeUntracked", []))})
        if kind == "existing":
            manifest["snapshot"]["executionSelection"] = selection
        self.input_paths[manifest["workspaceId"]] = (
            list(handoff[1]["includedUntracked"]) if handoff is not None else
            list(selection.get("selectors", [])) if kind == "existing" else [])
        prefix = f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"
        manifest["snapshot"].update(inputRef=prefix + "input", stagedRef=prefix + "staged",
                                    stagedCommit=manifest["inputCommit"])
        manifest["manifestSha256"] = sha256_text(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
        return manifest

    # -- interface ----------------------------------------------------------
    @staticmethod
    def execution_selection(manifest: dict) -> dict | None:
        from hey_my_buddy.blackboard.tasks.workspace import execution_selection
        return execution_selection(manifest)

    def shared_stash_inventory(self, repository_path) -> dict:
        """The injected observation for one repository, or an honest unknown.

        The inventory is what tests set (``stash_inventories``); the comparison
        itself reuses the real module's pure function so the double only
        replaces the Git read, exactly like the contract's module boundary.
        """
        self.stash_inventory_calls.append(str(repository_path))
        if self.fail_stash_inventory:
            raise BoardError("WORKSPACE_GIT_ERROR", "injected stash read failure")
        record = self.stash_inventories.get(str(repository_path))
        if isinstance(record, dict):
            return dict(record)
        from hey_my_buddy.blackboard.tasks.workspace import stash_observation_unknown
        return stash_observation_unknown(repository_path, "not-configured-in-double")

    def stash_observation_unknown(self, repository_path, reason) -> dict:
        from hey_my_buddy.blackboard.tasks.workspace import stash_observation_unknown
        return stash_observation_unknown(repository_path, reason)

    @staticmethod
    def shared_stash_comparison(start, seal) -> dict:
        from hey_my_buddy.blackboard.tasks.workspace import shared_stash_comparison
        return shared_stash_comparison(start, seal)

    def normalize_scope(self, values) -> list[str]:
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
            raise BoardError("INVALID_WORKSPACE", "writeScope must be a list of nonempty relative paths")
        return sorted({value for value in values})

    def inspect(self, cwd: str, *, checkout=None) -> dict:
        return {
            "kind": "existing",
            "path": str(cwd),
            **self._checkout_identity(cwd),
            "baseCommit": "a" * 40,
            "clean": not self.dirty,
        }

    def locate_repository(self, manifest, *, cwd=None, candidates=(), repair=True, removal_started=False):
        return manifest["snapshot"]["repositoryPath"]

    @contextmanager
    def submission_preparation(self, state_dir, request_id, intent, *, shared_locks=None, before_prepare=None):
        yield {"manifest": self.prepare(state_dir, request_id, intent), "newAllocation": False}

    def scope_conflicts(self, state_dir, manifest, task_id, attempt_id):
        return []

    def prepare(self, state_dir: Path, request_id: str, intent: dict, *, _preparation=None, before_prepare=None, handoff=None) -> dict:
        self.prepare_calls.append((request_id, dict(intent)))
        if request_id in self.fail_prepare:
            raise BoardError("WORKSPACE_PREPARE_FAILED", "injected prepare failure", requestId=request_id)
        cache_key = (str(Path(state_dir).resolve()), request_id)
        replayed = cache_key in self.manifests
        if _preparation is not None:
            _preparation["replayed"] = replayed
        if replayed:
            return dict(self.manifests[cache_key])
        manifest = self._manifest(state_dir=state_dir, request_id=request_id, intent=intent, handoff=handoff)
        self.manifests[cache_key] = manifest
        from hey_my_buddy.private_files import publish_bytes
        from hey_my_buddy.private_dirs import ensure_private_dir
        directory = Path(state_dir) / "workspaces" / manifest["workspaceId"]
        ensure_private_dir(directory)
        publish_bytes(directory / "manifest.json", json.dumps(manifest,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode(), immutable=True)
        return dict(manifest)

    def verify(self, manifest: dict, *, state_dir: Path, require_unchanged: bool = True) -> dict:
        self.verify_calls.append(dict(manifest))
        self._validate_manifest(manifest)
        if require_unchanged and self.dirty:
            raise BoardError("SNAPSHOT_CHANGED", "the source tree changed after the snapshot was taken")
        if manifest.get("path") and manifest.get("kind") == "worktree":
            Path(manifest["path"]).mkdir(parents=True, exist_ok=True)
        return {**manifest, "verified": True}

    @staticmethod
    def _validate_manifest(manifest):
        digest = manifest.get("manifestSha256") if isinstance(manifest, dict) else None
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BoardError("WORKSPACE_INVALID", "manifest has no valid manifestSha256")

    def seal(self, state_dir: Path, manifest: dict, task_id: str, attempt_id: str) -> dict:
        self.seal_calls.append((task_id, attempt_id))
        if task_id in self.fail_seal:
            raise BoardError("WORKSPACE_SEAL_FAILED", "injected seal failure", taskId=task_id)
        from hey_my_buddy.private_dirs import ensure_private_dir
        output_id = hashlib.sha256(json.dumps([task_id,attempt_id],sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()
        attempt_dir = Path(state_dir) / "workspaces" / manifest["workspaceId"] / "outputs" / output_id
        ensure_private_dir(attempt_dir)
        diff = attempt_dir / "output.patch"
        native_write_bytes(diff, b"diff --git a/src/feature.py b/src/feature.py\nnew file mode 100644\n--- /dev/null\n+++ b/src/feature.py\n@@ -0,0 +1 @@\n+fixture\n")
        seal = {
            "version": 1,
            "workspaceId": manifest.get("workspaceId"),
            "path": manifest.get("path"),
            "checkoutRoot": manifest.get("checkoutRoot"),
            "projectId": manifest.get("projectId"),
            "taskId": task_id,
            "attemptId": attempt_id,
            "baseCommit": manifest.get("baseCommit"),
            "inputCommit": manifest.get("inputCommit"),
            "commit": "d" * 40,
            "tree": "e" * 40,
            "changedPaths": ["src/feature.py"],
            "outsideScope": [],
            "snapshotSha256": (manifest.get("snapshot") and sha256_text(canonical_json(manifest["snapshot"]))) or "",
            "ref": f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/outputs/{output_id}",
            "diffPath": str(diff),
            "diffSha256": sha256_text(native_read_text(diff)),
            "sealedAfterShutdown": True,
            "includedUntracked": self.input_paths[manifest["workspaceId"]],
        }
        # Like the real module: manifestSha256 is the input digest the turn executed
        # on, snapshotSha256 is the output identity derived from the snapshot record.
        seal["manifestSha256"] = manifest["manifestSha256"]
        bindings = ("workspaceId", "taskId", "attemptId", "manifestSha256", "baseCommit", "inputCommit",
                    "commit", "tree", "changedPaths", "includedUntracked", "diffSha256")
        seal["snapshot"] = {"version": 1, **{key: seal[key] for key in bindings}}
        seal["snapshotSha256"] = sha256_text(json.dumps(seal["snapshot"],sort_keys=True,separators=(",",":"),ensure_ascii=True))
        from hey_my_buddy.private_files import publish_bytes
        from hey_my_buddy.private_dirs import ensure_private_dir
        output_id = hashlib.sha256(json.dumps([task_id,attempt_id],sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()
        directory = Path(state_dir) / "workspaces" / manifest["workspaceId"] / "outputs" / output_id
        ensure_private_dir(directory)
        publish_bytes(directory / "output.json", json.dumps(seal,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode(), immutable=True)
        return seal

    @staticmethod
    def _validate_output_record(manifest, artifact):
        from hey_my_buddy.blackboard.tasks.workspace import _validate_output_record
        _validate_output_record(manifest, artifact)

    def cumulative_patch(self, manifest, artifact, original_commit):
        patch = Path(artifact["diffPath"])
        if original_commit != manifest["inputCommit"]:
            from hey_my_buddy.private_files import publish_bytes
            patch = patch.with_name("cumulative-" + original_commit + ".patch")
            publish_bytes(patch, native_read_bytes(Path(artifact["diffPath"])), immutable=True)
        return {"baseCommit": original_commit,
                "path": str(patch), "sha256": sha256_text(native_read_text(patch)),
                "changedPaths": artifact["changedPaths"]}

    # -- read helpers -------------------------------------------------------
    def manifest_for(self, request_id: str) -> dict:
        return dict(next(value for (_, key), value in reversed(self.manifests.items()) if key == request_id))

    # -- Host lifecycle surface: this double owns no disposable checkout -----
    def integration_verify(self, artifact, *, original_input=None, final_input=None, path=None, ref=None,
                           before_commit=None, repository=None,
                           adjusted_paths=None, host_paths=None, note=None, _confirm_adjustments=False):
        """The mock target carries the artifact only once its marker exists, or an
        explicit adjustment names the differing paths."""
        changed = [str(item) for item in (artifact.get("changedPaths") or [])]
        target = Path(path)
        carried = (target / ".hey-my-buddy-integrated").exists()
        if _confirm_adjustments and not carried:
            adjusted_paths = changed
        return {
            "version": 2, "verified": bool(carried or adjusted_paths),
            "strategy": "three-way-adjusted" if adjusted_paths else "three-way",
            "target": {"kind": "checkout", "path": str(target.resolve()),
                       "projectId": f"repo:{target}", "ref": ref},
            "beforeCommit": before_commit, "afterCommit": ref, "beforeTree": None, "afterTree": None,
            "sourceCommit": artifact.get("commit"), "sourceTree": artifact.get("tree"),
            "mergeBaseCommit": original_input["inputCommit"], "expectedTree": artifact.get("tree"), "conflictingPaths": [],
            "matchingPaths": changed if carried else [],
            "differingPaths": [] if carried else [{"path": item} for item in changed],
            "missingPaths": [], "adjustments": sorted(set(adjusted_paths or ())),
            "hostPaths": sorted(host_paths or ()), "unrecordedPaths": [], "note": note,
        }

    def adopted_paths(self, state_dir, manifest):
        return []

    def host_seal(self, state_dir, manifest, task_id, attempt_id, *, allowed_paths=(), admission_check=None):
        if admission_check is not None:
            admission_check()
        return self.seal(state_dir, manifest, task_id, attempt_id)

    def resolve_allocation(self, state_dir, manifest, retained=None):
        return None

    @contextmanager
    def integration_target_lock(self, state_dir, path):
        yield

    def allocation_lock_directory(self, state_dir, workspace_id):
        return None

    @staticmethod
    def _path_present(path):
        from hey_my_buddy.blackboard.tasks.workspace import _path_present
        return _path_present(path)

    def cleanup_inspect(self, state_dir, manifest, sealed=None, retained=None, allowed=None, *, resume=False,
                        repository=None):
        return {
            "eligible": False, "reasons": ["not-a-managed-worktree"],
            "workspaceId": manifest.get("workspaceId"), "manifestWorkspaceId": manifest.get("workspaceId"),
            "kind": manifest.get("kind", "existing"),
            "projectId": manifest.get("projectId"), "path": manifest.get("checkoutRoot"),
            "cwd": manifest.get("path"), "allocation": None, "worktree": False, "locked": None,
            "unsealedPaths": [], "refs": [], "sealedObservation": None, "attachedBranch": None,
        }

    def cleanup_remove(self, state_dir, manifest, *, retained=None, resume=False, proof=None,
                       missing_allocation=None, repository=None):
        raise BoardError("WORKSPACE_UNSAFE", "the mock double owns no disposable checkout")
