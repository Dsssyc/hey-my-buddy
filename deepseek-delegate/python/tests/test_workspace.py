"""Workspace boundaries tested against disposable real Git repositories."""
import os
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from buddy.errors import BoardError
from buddy import workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-workspace-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.state = self.root / "state"
        self.git("init", "-q")
        self.git("config", "user.name", "Workspace Test")
        self.git("config", "user.email", "workspace@example.invalid")
        (self.repo / "src").mkdir()
        (self.repo / "docs").mkdir()
        (self.repo / "src/file.txt").write_text("base\n")
        (self.repo / "docs/readme.md").write_text("base docs\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").decode().strip()

    def git(self, *args, cwd=None, data=None):
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        result = subprocess.run(["git", "-C", str(cwd or self.repo), *args], input=data,
                                capture_output=True, env=env, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout

    def assert_error(self, code, operation, *args, **kwargs):
        with self.assertRaises(BoardError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.payload())
        return caught.exception

    def intent(self, **updates):
        return {"kind": "worktree", "cwd": str(self.repo), "access": "write", "base": {"kind": "working-tree"},
                "writeScope": ["src"], "integrator": "host", **updates}

    def prepare(self, request="request", **updates):
        return workspace.prepare(self.state, request, self.intent(**updates))

    def test_identity_unifies_siblings_and_distinguishes_linked_checkout(self):
        left = workspace.inspect(str(self.repo / "src"))
        right = workspace.inspect(str(self.repo / "docs"))
        linked = self.root / "linked"
        self.git("worktree", "add", "--detach", str(linked), self.base)
        other = workspace.inspect(str(linked / "src"))
        self.assertEqual(left["checkoutId"], right["checkoutId"])
        self.assertNotEqual(left["checkoutId"], other["checkoutId"])
        self.assertEqual(left["repositoryId"], other["repositoryId"])
        self.assertEqual(left["checkoutRoot"], str(self.repo))
        self.assertEqual(left["path"], str(self.repo / "src"))

    def test_non_git_and_unborn_checkout_are_explicit_errors(self):
        self.assert_error("WORKSPACE_UNSUPPORTED", workspace.inspect, str(self.root))
        unborn = self.root / "unborn"
        unborn.mkdir()
        self.git("init", "-q", cwd=unborn)
        self.assert_error("WORKSPACE_REF_INVALID", workspace.inspect, str(unborn))

    def test_dirty_input_preserves_staging_binary_modes_symlinks_and_source(self):
        source = self.repo / "src/file.txt"
        source.write_text("staged\n")
        self.git("add", "src/file.txt")
        source.write_bytes(b"unstaged\x00binary\xff\n")
        source.chmod(0o755)
        selected = self.repo / "src/selected.bin"
        selected.write_bytes(b"selected\x00\xfe")
        (self.repo / "src/excluded.txt").write_text("must not be copied")
        outside = self.root / "outside-secret"
        outside.write_text("do not follow")
        (self.repo / "src/link").symlink_to(outside)
        original_index = (self.repo / ".git/index").read_bytes()
        original_status = self.git("status", "--porcelain=v1", "-z")
        manifest = self.prepare(includeUntracked=["src/selected.bin", "src/link"])
        target = Path(manifest["checkoutRoot"])
        self.assertEqual((target / "src/file.txt").read_bytes(), source.read_bytes())
        self.assertTrue((target / "src/file.txt").stat().st_mode & 0o111)
        self.assertEqual((target / "src/selected.bin").read_bytes(), selected.read_bytes())
        self.assertEqual(os.readlink(target / "src/link"), str(outside))
        self.assertFalse((target / "src/excluded.txt").exists())
        self.assertEqual(self.git("show", manifest["snapshot"]["stagedCommit"] + ":src/file.txt"), b"staged\n")
        self.assertEqual(original_index, (self.repo / ".git/index").read_bytes())
        self.assertEqual(original_status, self.git("status", "--porcelain=v1", "-z"))
        self.assertEqual(self.base, self.git("rev-parse", "HEAD").decode().strip())
        self.assertEqual(outside.read_text(), "do not follow")
        self.assertEqual(manifest["snapshot"]["excludedUntracked"], ["src/excluded.txt"])
        empty_hash = hashlib.sha256(b"").hexdigest()
        self.assertNotEqual(manifest["snapshot"]["stagedSha256"], empty_hash)
        self.assertNotEqual(manifest["snapshot"]["unstagedSha256"], empty_hash)
        self.assertTrue(workspace.verify(manifest)["unchanged"])

    def test_prepare_replay_freezes_source_and_changed_intent_conflicts(self):
        intent = self.intent()
        first = workspace.prepare(self.state, "same", intent)
        (self.repo / "src/file.txt").write_text("new input\n")
        self.git("commit", "-qam", "source advanced")
        self.assertEqual(workspace.prepare(self.state, "same", intent), first)
        second = workspace.prepare(self.state, "new-request", intent)
        self.assertNotEqual(first["inputCommit"], second["inputCommit"])
        self.assertEqual((Path(first["path"]) / "src/file.txt").read_text(), "base\n")
        self.assert_error("WORKSPACE_CONFLICT", workspace.prepare, self.state, "same", self.intent(writeScope=["docs"]))

    def test_existing_commit_requires_exact_checkout(self):
        manifest = self.prepare(kind="existing", base={"kind": "commit", "ref": self.base})
        self.assertEqual(manifest["inputCommit"], self.base)
        (self.repo / "src/file.txt").write_text("dirty\n")
        self.assert_error("WORKSPACE_BASE_MISMATCH", self.prepare, "dirty", kind="existing", base={"kind": "commit", "ref": self.base})
        self.git("add", ".")
        self.git("commit", "-qm", "new head")
        self.assert_error("WORKSPACE_BASE_MISMATCH", self.prepare, "different", kind="existing", base={"kind": "commit", "ref": self.base})

    def test_verify_detects_drift_and_read_cannot_opt_out(self):
        manifest = self.prepare(access="read", writeScope=[])
        (Path(manifest["path"]) / "src/file.txt").write_text("changed\n")
        self.assert_error("WORKSPACE_CHANGED", workspace.verify, manifest)
        self.assert_error("WORKSPACE_CHANGED", workspace.verify, manifest, require_unchanged=False)

    def test_unknown_worktree_target_is_preserved(self):
        _, directory = workspace._workspace_directory(self.state, "collision")
        target = directory / "checkout"
        target.mkdir()
        (target / "precious.txt").write_text("preserve\n")
        self.assert_error("WORKSPACE_CONFLICT", self.prepare, "collision")
        self.assertEqual((target / "precious.txt").read_text(), "preserve\n")

    def test_completed_checkout_recovers_after_interrupted_manifest_write(self):
        original = workspace._write_once
        def interrupt(path, data):
            if path.name == "manifest.json":
                raise OSError("simulated crash")
            return original(path, data)
        with patch.object(workspace, "_write_once", side_effect=interrupt):
            self.assert_error("WORKSPACE_IO_ERROR", self.prepare)
        (self.repo / "src/file.txt").write_text("source advanced after interruption")
        recovered = self.prepare()
        self.assertEqual((Path(recovered["path"]) / "src/file.txt").read_text(), "base\n")
        self.assertTrue(workspace.verify(recovered)["unchanged"])

    def test_changed_interrupted_checkout_is_not_overwritten(self):
        original = workspace._write_once
        def interrupt(path, data):
            if path.name == "manifest.json":
                raise OSError("simulated crash")
            return original(path, data)
        with patch.object(workspace, "_write_once", side_effect=interrupt):
            self.assert_error("WORKSPACE_IO_ERROR", self.prepare)
        _, directory = workspace._workspace_directory(self.state, "request")
        changed = directory / "checkout/src/file.txt"
        changed.write_text("valuable unfinished work")
        self.assert_error("WORKSPACE_CONFLICT", self.prepare)
        self.assertEqual(changed.read_text(), "valuable unfinished work")

    def test_seal_pins_actual_output_binary_diff_and_replays_after_removal(self):
        manifest = self.prepare()
        target = Path(manifest["path"])
        (target / "src/file.txt").write_bytes(b"output\x00\xff\n")
        (target / "src/new.txt").write_text("new output\n")
        (target / "src/link").symlink_to("../../external-missing")
        (target / "src/new.txt").chmod(0o755)
        output = workspace.seal(self.state, manifest, "task", "attempt")
        self.assertEqual(output["changedPaths"], ["src/file.txt", "src/link", "src/new.txt"])
        self.assertEqual(self.git("show", output["commit"] + ":src/file.txt"), b"output\x00\xff\n")
        self.assertEqual(self.git("show", output["commit"] + ":src/link"), b"../../external-missing")
        self.assertIn(b"100755", self.git("ls-tree", output["commit"], "src/new.txt"))
        self.assertEqual(output["diffSha256"], hashlib.sha256(Path(output["diffPath"]).read_bytes()).hexdigest())
        self.assertEqual(self.git("rev-parse", output["commit"] + "^").decode().strip(), manifest["inputCommit"])
        self.assertEqual(workspace.seal(self.state, manifest, "task", "attempt"), output)
        integration = self.root / "integration"
        self.git("worktree", "add", "--detach", str(integration), manifest["inputCommit"])
        self.git("apply", "--binary", output["diffPath"], cwd=integration)
        self.assertEqual((integration / "src/file.txt").read_bytes(), b"output\x00\xff\n")
        self.assertEqual(os.readlink(integration / "src/link"), "../../external-missing")
        self.assertTrue((integration / "src/new.txt").stat().st_mode & 0o111)
        self.git("worktree", "unlock", str(target))
        self.git("worktree", "remove", "--force", str(target))
        self.git("gc", "--prune=now")
        self.assertEqual(workspace.seal(self.state, manifest, "task", "attempt"), output)
        self.assertEqual(self.git("rev-parse", output["ref"]).decode().strip(), output["commit"])

    def test_outside_scope_edits_and_new_files_are_reported(self):
        manifest = self.prepare()
        target = Path(manifest["path"])
        (target / "docs/readme.md").write_text("outside\n")
        (target / "new.txt").write_text("outside new\n")
        error = self.assert_error("WORKSPACE_SCOPE_VIOLATION", workspace.seal, self.state, manifest, "task", "attempt")
        self.assertEqual(error.details["paths"], ["docs/readme.md", "new.txt"])
        self.assertEqual((target / "docs/readme.md").read_text(), "outside\n")

    def test_existing_excluded_inputs_are_not_accidentally_sealed(self):
        (self.repo / "outside.txt").write_text("unselected\n")
        manifest = self.prepare(kind="existing")
        (self.repo / "src/file.txt").write_text("changed\n")
        output = workspace.seal(self.state, manifest, "task", "attempt")
        self.assertEqual(output["changedPaths"], ["src/file.txt"])
        self.assertNotIn(b"outside.txt", self.git("ls-tree", "-r", output["commit"]))
        (self.repo / "outside.txt").write_text("changed excluded input\n")
        self.assert_error("WORKSPACE_SCOPE_VIOLATION", workspace.seal, self.state, manifest, "task", "new-attempt")

    def test_read_only_seal_rejects_index_only_and_untracked_drift(self):
        manifest = self.prepare(access="read", writeScope=[])
        target = Path(manifest["path"])
        output = workspace.seal(self.state, manifest, "task", "initial")
        self.assertEqual(output["commit"], manifest["inputCommit"])
        self.assertEqual(output["changedPaths"], [])
        self.git("rm", "--cached", "src/file.txt", cwd=target)
        self.assert_error("WORKSPACE_CHANGED", workspace.seal, self.state, manifest, "task", "changed-index")
        self.git("add", "src/file.txt", cwd=target)
        (target / "extra.txt").write_text("extra")
        self.assert_error("WORKSPACE_CHANGED", workspace.seal, self.state, manifest, "task", "changed-file")

    def test_seal_recovers_original_output_after_interrupted_publication(self):
        manifest = self.prepare()
        target = Path(manifest["path"])
        (target / "src/file.txt").write_text("first output\n")
        original = workspace._pin
        def interrupt(root, ref, commit):
            if "/outputs/" in ref:
                raise OSError("simulated crash")
            return original(root, ref, commit)
        with patch.object(workspace, "_pin", side_effect=interrupt):
            self.assert_error("WORKSPACE_IO_ERROR", workspace.seal, self.state, manifest, "task", "attempt")
        (target / "src/file.txt").write_text("later edits\n")
        self.git("gc", "--prune=now")
        recovered = workspace.seal(self.state, manifest, "task", "attempt")
        self.assertEqual(self.git("show", recovered["commit"] + ":src/file.txt"), b"first output\n")
        self.assertEqual((target / "src/file.txt").read_text(), "later edits\n")

    def test_output_ref_collision_and_diff_corruption_are_honest_conflicts(self):
        manifest = self.prepare()
        target = Path(manifest["path"])
        (target / "src/file.txt").write_text("output\n")
        output = workspace.seal(self.state, manifest, "task", "attempt")
        self.git("update-ref", output["ref"], self.base)
        self.assert_error("WORKSPACE_CONFLICT", workspace.seal, self.state, manifest, "task", "attempt")
        self.assertEqual(self.git("rev-parse", output["ref"]).decode().strip(), self.base)
        self.git("update-ref", output["ref"], output["commit"])
        Path(output["diffPath"]).write_bytes(b"corrupted")
        self.assert_error("WORKSPACE_CONFLICT", workspace.seal, self.state, manifest, "task", "attempt")

    def test_existing_checkout_continuation_uses_exact_sealed_baseline(self):
        (self.repo / "src/file.txt").write_text("initial staged\n")
        self.git("add", "src/file.txt")
        original_index = (self.repo / ".git/index").read_bytes()
        initial = self.prepare("turn-one", kind="existing")
        (self.repo / "src/file.txt").write_text("first output\n")
        (self.repo / "src/new.txt").write_text("new output\n")
        first = workspace.seal(self.state, initial, "task", "attempt-one")
        self.assertEqual(first["includedUntracked"], ["src/new.txt"])
        self.assertEqual(self.prepare("turn-one", kind="existing"), initial)
        self.assert_error("WORKSPACE_CHANGED", workspace.verify, initial)
        continuation = self.prepare("turn-two", kind="existing", base={"kind": "working-tree", "ref": first["commit"]},
                                    includeUntracked=first["includedUntracked"])
        self.assertEqual(continuation["inputCommit"], first["commit"])
        self.assertEqual(continuation["inputTree"], first["tree"])
        self.assertTrue(workspace.verify(continuation)["unchanged"])
        (self.repo / "src/new.txt").write_text("second output\n")
        second = workspace.seal(self.state, continuation, "task", "attempt-two")
        self.assertEqual(second["inputCommit"], first["commit"])
        self.assertEqual(second["changedPaths"], ["src/new.txt"])
        self.assertNotEqual(first["snapshotSha256"], second["snapshotSha256"])
        self.assertEqual(original_index, (self.repo / ".git/index").read_bytes())
        self.assertEqual(self.base, self.git("rev-parse", "HEAD").decode().strip())
        self.assert_error("WORKSPACE_BASE_MISMATCH", self.prepare, "stale-handoff", kind="existing",
                          base={"kind": "working-tree", "ref": first["commit"]}, includeUntracked=first["includedUntracked"])

    def test_sibling_cwd_maps_to_equivalent_subdirectory_in_isolated_tree(self):
        manifest = self.prepare(cwd=str(self.repo / "src"))
        self.assertEqual(Path(manifest["path"]), Path(manifest["checkoutRoot"]) / "src")
        self.assertTrue((Path(manifest["path"]) / "file.txt").exists())
        self.assertTrue(workspace.verify(manifest)["unchanged"])

    def test_symbolic_input_reference_cannot_modify_a_branch(self):
        workspace_id, _ = workspace._workspace_directory(self.state, "request")
        reference = f"refs/buddy/workspaces/{workspace_id}/input"
        self.git("symbolic-ref", reference, "refs/heads/unborn-target")
        self.assert_error("WORKSPACE_CONFLICT", self.prepare)
        result = subprocess.run(["git", "-C", str(self.repo), "show-ref", "--verify", "refs/heads/unborn-target"], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_explicit_ignored_untracked_input_and_unusual_filenames(self):
        (self.repo / ".gitignore").write_text("*.secret\n")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "ignore secrets")
        name = "src/odd\nname\t.secret"
        (self.repo / name).write_bytes(b"\x00selected")
        (self.repo / "src/unselected.secret").write_text("excluded")
        manifest = self.prepare(includeUntracked=[name])
        self.assertEqual((Path(manifest["path"]) / name).read_bytes(), b"\x00selected")
        self.assertFalse((Path(manifest["path"]) / "src/unselected.secret").exists())
        self.assertTrue(workspace.verify(manifest)["unchanged"])

    def test_ignored_paths_do_not_bypass_read_only_or_write_scope(self):
        (self.repo / ".gitignore").write_text("*.ignored\n")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "ignore generated files")
        reader = self.prepare("reader", access="read", writeScope=[])
        (Path(reader["path"]) / "new.ignored").write_text("read-only violation")
        self.assert_error("WORKSPACE_CHANGED", workspace.verify, reader)
        writer = self.prepare("writer")
        (Path(writer["path"]) / "docs/out.ignored").write_text("scope violation")
        self.assert_error("WORKSPACE_SCOPE_VIOLATION", workspace.seal, self.state, writer, "task", "outside")
        existing_file = self.repo / "docs/source.ignored"
        existing_file.write_text("preexisting excluded input")
        existing = self.prepare("existing", kind="existing", access="read", writeScope=[])
        self.assertIn("docs/source.ignored", existing["snapshot"]["excludedUntracked"])
        existing_file.write_text("changed excluded input")
        self.assert_error("WORKSPACE_CHANGED", workspace.seal, self.state, existing, "task", "changed")

    def test_frozen_input_survives_gc_before_public_input_ref_publication(self):
        (self.repo / "src/file.txt").write_text("frozen uncommitted input\n")
        original = workspace._pin
        def interrupt(root, ref, commit):
            if ref.endswith("/input") and "/retained/" not in ref:
                raise OSError("simulated crash")
            return original(root, ref, commit)
        with patch.object(workspace, "_pin", side_effect=interrupt):
            self.assert_error("WORKSPACE_IO_ERROR", self.prepare)
        (self.repo / "src/file.txt").write_text("later source edits\n")
        self.git("gc", "--prune=now")
        manifest = self.prepare()
        self.assertEqual((Path(manifest["path"]) / "src/file.txt").read_text(), "frozen uncommitted input\n")

    def test_replayed_output_rejects_inconsistent_top_level_fields(self):
        manifest = self.prepare()
        (Path(manifest["path"]) / "src/file.txt").write_text("output\n")
        output = workspace.seal(self.state, manifest, "task", "attempt")
        path = Path(output["diffPath"]).parent / "output.json"
        changed = {**output, "changedPaths": []}
        path.write_text(json.dumps(changed))
        self.assert_error("WORKSPACE_CONFLICT", workspace.seal, self.state, manifest, "task", "attempt")

    def test_malformed_manifest_and_input_fail_with_structured_errors(self):
        manifest = self.prepare()
        changed = {**manifest, "access": "read"}
        self.assert_error("WORKSPACE_MANIFEST_CHANGED", workspace.verify, changed)
        changed = {**manifest, "snapshot": {}}
        changed["manifestSha256"] = hashlib.sha256(json.dumps({key: value for key, value in changed.items() if key != "manifestSha256"},
                                                             sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        self.assert_error("INVALID_WORKSPACE", workspace.verify, changed)
        self.assert_error("INVALID_WORKSPACE", self.prepare, "escape", writeScope=["../outside"])
        self.assert_error("INVALID_WORKSPACE", self.prepare, "git-metadata", includeUntracked=[".git/config"])


if __name__ == "__main__":
    unittest.main()
