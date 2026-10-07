"""Deterministic in-process workspace double for governed workflow tests.

It implements exactly the ``hey_my_buddy.blackboard.tasks.workspace`` interface from the productivity
contract (``inspect``/``prepare``/``verify``/``seal``) with observable calls and
injectable failures, so the workflow coordinator's transaction, idempotency and
exclusion logic can be tested before the real Git-backed module is integrated.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from hey_my_buddy.blackboard.store.db import canonical_json, sha256_text
from hey_my_buddy.errors import BoardError


class MockWorkspace:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.manifests: dict[str, dict] = {}
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
        return str(path)

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
            return {key: manifest[key] for key in ("checkoutRoot", "checkoutId", "repositoryId")}
        root = self._repository_root(cwd)
        return {"checkoutRoot": root, "checkoutId": f"checkout:{root}", "repositoryId": f"repo:{root}"}

    def _manifest(self, *, request_id: str, intent: dict) -> dict:
        kind = intent.get("kind", "existing")
        cwd = intent.get("cwd")
        if kind == "worktree":
            path = str(self.root / "worktrees" / request_id)
            Path(path).mkdir(parents=True, exist_ok=True)
        else:
            path = str(cwd)
        identity = self._checkout_identity(intent.get("cwd") or path)
        if kind == "worktree":
            identity = {**identity, "checkoutRoot": path, "checkoutId": f"checkout:{identity['checkoutRoot']}:{request_id}"}
        base = intent.get("base") if isinstance(intent.get("base"), dict) else {}
        base_commit = base.get("ref") or "a" * 40
        manifest = {
            "version": 1,
            "workspaceId": f"ws-{request_id}",
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
            "snapshot": {"repositoryPath": "mock+repo:" + self._repository_root(intent.get("cwd") or path),
                         "included": list(intent.get("includeUntracked", [])), "excluded": [], "staged": {}, "unstaged": {}},
        }
        manifest["manifestSha256"] = sha256_text(canonical_json(manifest))
        return manifest

    # -- interface ----------------------------------------------------------
    def recovery_inputs(self, manifest: dict) -> list[str]:
        # This double models identities, not Git's file inventory; actual partial
        # file selection is covered by the real-workspace preparation tests.
        return []

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

    def inspect(self, cwd: str) -> dict:
        return {
            "kind": "existing",
            "path": str(cwd),
            **self._checkout_identity(cwd),
            "baseCommit": "a" * 40,
            "clean": not self.dirty,
        }

    def prepare(self, state_dir: Path, request_id: str, intent: dict) -> dict:
        self.prepare_calls.append((request_id, dict(intent)))
        if request_id in self.fail_prepare:
            raise BoardError("WORKSPACE_PREPARE_FAILED", "injected prepare failure", requestId=request_id)
        if request_id in self.manifests:
            return dict(self.manifests[request_id])
        manifest = self._manifest(request_id=request_id, intent=intent)
        self.manifests[request_id] = manifest
        return dict(manifest)

    def verify(self, manifest: dict, *, require_unchanged: bool = True) -> dict:
        self.verify_calls.append(dict(manifest))
        digest = manifest.get("manifestSha256") if isinstance(manifest, dict) else None
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BoardError("WORKSPACE_INVALID", "manifest has no valid manifestSha256")
        if require_unchanged and self.dirty:
            raise BoardError("SNAPSHOT_CHANGED", "the source tree changed after the snapshot was taken")
        if manifest.get("path") and manifest.get("kind") == "worktree":
            Path(manifest["path"]).mkdir(parents=True, exist_ok=True)
        return {**manifest, "verified": True}

    def seal(self, state_dir: Path, manifest: dict, task_id: str, attempt_id: str) -> dict:
        self.seal_calls.append((task_id, attempt_id))
        if task_id in self.fail_seal:
            raise BoardError("WORKSPACE_SEAL_FAILED", "injected seal failure", taskId=task_id)
        attempt_dir = Path(state_dir) / "attempts" / task_id / attempt_id
        attempt_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        diff = attempt_dir / "workspace.diff"
        diff.write_text("--- a\n+++ b\n")
        os.chmod(diff, 0o600)
        seal = {
            "version": 1,
            "workspaceId": manifest.get("workspaceId"),
            "kind": manifest.get("kind"),
            "path": manifest.get("path"),
            "checkoutRoot": manifest.get("checkoutRoot"),
            "checkoutId": manifest.get("checkoutId"),
            "repositoryId": manifest.get("repositoryId"),
            "taskId": task_id,
            "attemptId": attempt_id,
            "baseCommit": manifest.get("baseCommit"),
            "inputCommit": manifest.get("inputCommit"),
            "commit": "d" * 40,
            "tree": "e" * 40,
            "changedPaths": ["src/feature.py"],
            "outsideScope": [],
            "snapshotSha256": (manifest.get("snapshot") and sha256_text(canonical_json(manifest["snapshot"]))) or "",
            "ref": f"refs/buddy/workflow/{task_id}/{attempt_id}",
            "diffPath": str(diff),
            "diffSha256": sha256_text(diff.read_text()),
            "sealedAfterShutdown": True,
            "includedUntracked": (manifest.get("snapshot") or {}).get("included", []),
        }
        # Like the real module: manifestSha256 is the input digest the turn executed
        # on, snapshotSha256 is the output identity derived from the snapshot record.
        seal["manifestSha256"] = manifest["manifestSha256"]
        seal["snapshotSha256"] = sha256_text(canonical_json({k: v for k, v in seal.items() if k != "snapshotSha256"}))
        return seal

    # -- read helpers -------------------------------------------------------
    def manifest_for(self, request_id: str) -> dict:
        return dict(self.manifests[request_id])

    # -- Host lifecycle surface: this double owns no disposable checkout -----
    def integration_verify(self, artifact, *, original_input=None, final_input=None, path=None, ref=None,
                           strategy="patch", before_commit=None, repository_id=None, checkout_id=None,
                           adjusted_paths=None, host_paths=None, reason=None):
        """The mock target carries the artifact only once its marker exists, or an
        explicit adjustment names the differing paths."""
        changed = [str(item) for item in (artifact.get("changedPaths") or [])]
        target = Path(path)
        carried = (target / ".buddy-integrated").exists()
        return {
            "verified": bool(carried or adjusted_paths), "strategy": strategy,
            "target": {"kind": "checkout", "path": str(target.resolve()), "checkoutId": f"checkout:{target}",
                       "repositoryId": f"repo:{target}", "ref": ref},
            "beforeCommit": before_commit, "afterCommit": ref, "beforeTree": None, "afterTree": None,
            "sourceCommit": artifact.get("commit"), "sourceTree": artifact.get("tree"),
            "artifactAncestor": False,
            "matchingPaths": changed if carried else [],
            "differingPaths": [] if carried else [{"path": item} for item in changed],
            "missingPaths": [], "adjustments": sorted(set(adjusted_paths or ())),
            "hostPaths": sorted(host_paths or ()), "unrecordedPaths": [], "reason": reason,
        }

    def adopted_paths(self, state_dir, manifest):
        return []

    def host_seal(self, state_dir, manifest, task_id, attempt_id, *, allowed_paths=()):
        return self.seal(state_dir, manifest, task_id, attempt_id)

    def cleanup_inspect(self, state_dir, manifest, sealed=None, retained=None, allowed=None):
        return {
            "eligible": False, "reasons": ["not-a-managed-worktree"],
            "workspaceId": manifest.get("workspaceId"), "manifestWorkspaceId": manifest.get("workspaceId"),
            "kind": manifest.get("kind", "existing"), "checkoutId": manifest.get("checkoutId"),
            "repositoryId": manifest.get("repositoryId"), "path": manifest.get("checkoutRoot"),
            "cwd": manifest.get("path"), "allocation": None, "worktree": False, "locked": None,
            "unsealedPaths": [], "refs": [], "sealedObservation": None, "attachedBranch": None,
        }

    def cleanup_remove(self, state_dir, manifest, *, retained=None):
        raise BoardError("WORKSPACE_UNSAFE", "the mock double owns no disposable checkout")
