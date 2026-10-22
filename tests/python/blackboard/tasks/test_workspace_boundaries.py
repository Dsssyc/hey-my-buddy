"""Fixed objects, individual retention refs and read-only cleanup proofs."""
from pathlib import Path
import unittest
from unittest.mock import patch

import blackboard.tasks.test_workspace as fixtures
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.errors import BoardError


class WorkspaceBoundaryTests(unittest.TestCase):
    setUp = fixtures.WorkspaceTests.setUp
    git = fixtures.WorkspaceTests.git
    intent = fixtures.WorkspaceTests.intent
    prepare = fixtures.WorkspaceTests.prepare
    assert_error = fixtures.WorkspaceTests.assert_error

    def clear_refs_and_prune(self, *missing):
        refs = self.git("for-each-ref", "--format=%(refname)", "refs/hey-my-buddy/workspaces/").decode().splitlines()
        self.assertTrue(refs)
        for ref in refs:
            self.git("update-ref", "-d", ref)
        self.git("gc", "--prune=now")
        for commit in missing:
            self.assertEqual(self.git("cat-file", "--batch-check", data=(commit + "\n").encode()),
                             (commit + " missing\n").encode())

    def seal(self, manifest):
        return workspace.seal(self.state, manifest, "micro-task", "attempt")

    def test_thousand_scope_violations_keep_complete_evidence_and_restore(self):
        paths = [f"docs/blocked-{number:04d}.txt" for number in range(1000)]
        for name in paths:
            (self.repo / name).write_text("authorized\n", encoding="utf-8", newline="\n")
        self.git("add", ".")
        self.git("commit", "-qm", "many scoped inputs")
        manifest = self.prepare()
        checkout = Path(manifest["path"])
        for name in paths:
            (checkout / name).write_text("outside declared scope\n", encoding="utf-8", newline="\n")
        self.git("add", "docs", cwd=checkout)
        error = self.assert_error("WORKSPACE_SCOPE_VIOLATION", self.seal, manifest)
        evidence = error.details["evidence"]
        self.assertEqual(evidence["blockingPaths"], paths)
        self.assertEqual([entry["path"] for entry in evidence["entries"]], paths)
        resolved = workspace.resolve(
            self.state, manifest, task_id="micro-task", attempt_id="attempt", action="restore", paths=None,
            observed_fingerprint=evidence["observedFingerprint"], note="restore all paths",
            actor={"role": "host", "id": "host-1"},
        )
        self.assertEqual(resolved["state"], "restored")
        self.assertEqual(resolved["resolvedPaths"], paths)
        self.assertEqual(resolved["remainingPaths"], [])
        for name in paths:
            self.assertEqual((checkout / name).read_text(encoding="utf-8"), "authorized\n")
        self.assertEqual(self.git("diff", "--cached", "--name-only", cwd=checkout), b"")
        self.assertNotIn("changedEntries", resolved["artifact"]["snapshot"])

    def test_first_turn_cumulative_patch_uses_sealed_diff_without_git(self):
        manifest = self.prepare()
        (Path(manifest["path"]) / "src/file.txt").write_text("output\n", encoding="utf-8", newline="\n")
        sealed = self.seal(manifest)
        with patch.object(workspace, "_git", side_effect=AssertionError("unexpected Git read")):
            cumulative = workspace.cumulative_patch(manifest, sealed, manifest["inputCommit"])
        self.assertEqual(cumulative["changedPaths"], ["src/file.txt"])
        self.assertEqual(Path(cumulative["path"]).read_bytes(), Path(sealed["diffPath"]).read_bytes())

    def test_missing_staged_commit_rejects_turn_start_when_input_still_exists(self):
        source = self.repo / "src/file.txt"
        source.write_text("staged only\n", encoding="utf-8", newline="\n")
        self.git("add", "src/file.txt")
        source.write_text("base\n", encoding="utf-8", newline="\n")
        manifest = self.prepare(kind="existing")
        staged = manifest["snapshot"]["stagedCommit"]
        self.assertEqual(manifest["inputCommit"], self.base)
        self.assertNotEqual(manifest["snapshot"]["stagedTree"], manifest["inputTree"])
        self.clear_refs_and_prune(staged)
        self.assertEqual(self.git("cat-file", "-t", manifest["inputCommit"]), b"commit\n")
        self.assert_error("WORKSPACE_GIT_ERROR", workspace.verify, manifest, require_unchanged=False)
        self.assertEqual(source.read_text(encoding="utf-8"), "base\n")
        source.write_text("output after the staged commit disappeared\n", encoding="utf-8", newline="\n")
        with patch.object(workspace, "_write_objects", side_effect=AssertionError("write before input proof")):
            self.assert_error("WORKSPACE_GIT_ERROR", self.seal, manifest)
        self.assertEqual(source.read_text(encoding="utf-8"), "output after the staged commit disappeared\n")

    def test_unused_staged_commit_does_not_add_a_git_read(self):
        source = self.repo / "src/file.txt"
        source.write_text("staged input\n", encoding="utf-8", newline="\n")
        self.git("add", "src/file.txt")
        # Existing inputs with equal trees have separate commits for those trees;
        # worktrees never use the source index as their execution index.
        for kind in ("existing", "worktree"):
            with self.subTest(kind=kind):
                if kind == "worktree":
                    source.write_text("base\n", encoding="utf-8", newline="\n")
                manifest = self.prepare(request=kind, kind=kind)
                snapshot = manifest["snapshot"]
                self.assertNotEqual(snapshot["stagedCommit"], manifest["inputCommit"])
                if kind == "existing":
                    self.assertEqual(snapshot["stagedTree"], manifest["inputTree"])
                refs = self.git("for-each-ref", "--format=%(refname) %(objectname)",
                                "refs/hey-my-buddy/workspaces/").decode().splitlines()
                for row in refs:
                    ref, commit = row.split()
                    if commit == snapshot["stagedCommit"]:
                        self.git("update-ref", "-d", ref)
                self.git("gc", "--prune=now")
                self.assertEqual(self.git("cat-file", "--batch-check", data=(snapshot["stagedCommit"] + "\n").encode()),
                                 (snapshot["stagedCommit"] + " missing\n").encode())
                with patch.object(workspace, "_git", wraps=workspace._git) as calls:
                    self.assertTrue(workspace.verify(manifest, require_unchanged=False)["valid"])
                self.assertFalse(any(snapshot["stagedCommit"] + "^{tree}" in call.args for call in calls.call_args_list))

    def test_missing_input_commit_rejects_turn_start_and_sealing(self):
        source = self.repo / "src/file.txt"
        source.write_text("prepared input\n", encoding="utf-8", newline="\n")
        self.git("add", "src/file.txt")
        manifest = self.prepare(kind="existing")
        self.assertNotEqual(manifest["inputCommit"], self.base)
        self.clear_refs_and_prune(manifest["inputCommit"], manifest["snapshot"]["stagedCommit"])
        source.write_text("output after the input commit disappeared\n", encoding="utf-8", newline="\n")
        for boundary, operation in (
            ("turn-start", lambda: workspace.verify(manifest, require_unchanged=False)),
            ("seal", lambda: self.seal(manifest)),
        ):
            with self.subTest(boundary=boundary):
                with patch.object(workspace, "_write_objects", side_effect=AssertionError("write before input proof")):
                    self.assert_error("WORKSPACE_GIT_ERROR", operation)
        self.assertEqual(source.read_text(encoding="utf-8"), "output after the input commit disappeared\n")

    def test_missing_output_commit_rejects_integration_even_with_matching_target(self):
        manifest = self.prepare(kind="existing")
        (self.repo / "src/file.txt").write_text("sealed output\n", encoding="utf-8", newline="\n")
        sealed = self.seal(manifest)
        self.git("add", "src/file.txt")
        self.git("commit", "-qm", "integrated output")
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}").decode().strip(), sealed["tree"])
        self.clear_refs_and_prune(sealed["commit"])
        self.assert_error("WORKSPACE_GIT_ERROR", workspace.integration_verify, sealed,
                          original_input=manifest, final_input=manifest, path=str(self.repo), ref="HEAD")

    def check_each_cleanup_ref(self, *, sealed_output):
        manifest = self.prepare()
        checkout = Path(manifest["path"])
        sealed = None
        expected = [(manifest["snapshot"]["inputRef"], manifest["inputCommit"]),
                    (manifest["snapshot"]["stagedRef"], manifest["snapshot"]["stagedCommit"])]
        if sealed_output:
            (checkout / "src/file.txt").write_text("sealed output\n", encoding="utf-8", newline="\n")
            sealed = self.seal(manifest)
            expected.append((sealed["ref"], sealed["commit"]))
        proof = lambda: workspace.cleanup_inspect(self.state, manifest, sealed=sealed)
        self.assertTrue(proof()["eligible"])
        files = {str(path.relative_to(checkout)): path.read_bytes()
                 for path in checkout.rglob("*") if path.is_file()}
        for ref, commit in expected:
            with self.subTest(ref=ref):
                self.git("update-ref", "-d", ref)
                rejected = proof()
                self.assertFalse(rejected["eligible"], rejected)
                self.assertEqual(rejected["reasons"], ["sealed-output-invalid" if sealed_output else "unsealed-changes"])
                # No other missing ref may make this proof pass accidentally.
                for other, value in expected:
                    if other != ref:
                        self.assertEqual(self.git("rev-parse", other).decode().strip(), value)
                self.assertTrue(checkout.exists())
                self.assertEqual({str(path.relative_to(checkout)): path.read_bytes()
                                  for path in checkout.rglob("*") if path.is_file()}, files)
                self.git("update-ref", ref, commit)
                self.assertTrue(proof()["eligible"])

    def test_sealed_cleanup_requires_each_fixed_ref(self):
        self.check_each_cleanup_ref(sealed_output=True)

    def test_unsealed_cleanup_requires_each_fixed_ref(self):
        self.check_each_cleanup_ref(sealed_output=False)

    def test_mismatching_fingerprint_cleanup_proof_writes_no_objects(self):
        manifest = self.prepare()
        checkout = Path(manifest["path"])
        (checkout / "src/file.txt").write_text("sealed output\n", encoding="utf-8", newline="\n")
        sealed = self.seal(manifest)
        (checkout / "src/new.txt").write_text("unique unsealed object for read-only cleanup\n", encoding="utf-8", newline="\n")
        before = self.git("cat-file", "--batch-all-objects", "--batch-check=%(objectname)")
        with patch.object(workspace, "_write_objects", side_effect=AssertionError("object write")) as writes, \
                patch.object(workspace, "_tree", side_effect=AssertionError("tree construction")) as trees:
            proof = workspace.cleanup_inspect(self.state, manifest, sealed=sealed)
        self.assertFalse(proof["eligible"], proof)
        self.assertEqual(proof["reasons"], ["unsealed-changes"])
        self.assertEqual(proof["unsealedPaths"], ["src/new.txt"])
        writes.assert_not_called()
        trees.assert_not_called()
        self.assertEqual(self.git("cat-file", "--batch-all-objects", "--batch-check=%(objectname)"), before)
        self.assertEqual((checkout / "src/new.txt").read_text(encoding="utf-8"), "unique unsealed object for read-only cleanup\n")

    def test_cleanup_requires_current_product_lock_reason(self):
        manifest = self.prepare()
        checkout = Path(manifest["path"])
        proof = workspace.cleanup_inspect(self.state, manifest)
        self.assertTrue(proof["eligible"], proof)
        self.assertEqual(proof["locked"], "hey-my-buddy:" + manifest["workspaceId"])
        self.git("worktree", "unlock", str(checkout))
        self.git("worktree", "lock", "--reason", "buddy:" + manifest["workspaceId"], str(checkout))
        rejected = workspace.cleanup_inspect(self.state, manifest)
        self.assertFalse(rejected["eligible"])
        self.assertEqual(rejected["reasons"], ["lock-mismatch"])
        self.assertTrue(checkout.exists())

    def test_started_cleanup_without_an_output_uses_the_retained_input(self):
        manifest = self.prepare()
        checkout = Path(manifest["path"])
        self.git("worktree", "unlock", str(checkout))
        (checkout / "src/file.txt").unlink()
        (checkout / ".git").unlink()
        proof = workspace.cleanup_inspect(self.state, manifest, resume=True)
        self.assertTrue(proof["eligible"], proof)
        self.assertEqual(proof["locked"], "hey-my-buddy:" + manifest["workspaceId"])
        removed = workspace.cleanup_remove(self.state, manifest, resume=True)
        self.assertFalse(removed["alreadyRemoved"])
        self.assertFalse(checkout.exists())

    def test_failed_partial_remove_relocks_without_a_gitfile_and_can_resume(self):
        manifest = self.prepare()
        checkout = Path(manifest["path"])
        real_git = workspace._git

        def failed_remove(root, *args, **kwargs):
            if args[:2] == ("worktree", "remove"):
                (checkout / ".git").unlink()
                (checkout / "src/file.txt").unlink()
                raise BoardError("WORKSPACE_GIT_ERROR", "injected partial deletion")
            return real_git(root, *args, **kwargs)

        with patch.object(workspace, "_git", side_effect=failed_remove):
            error = self.assert_error("WORKSPACE_GIT_ERROR", workspace.cleanup_remove, self.state, manifest)
        self.assertNotIn("relockError", error.details)
        self.assertEqual(workspace._worktree_record(self.repo, checkout)["locked"],
                         "hey-my-buddy:" + manifest["workspaceId"])
        proof = workspace.cleanup_inspect(self.state, manifest, resume=True)
        self.assertTrue(proof["eligible"], proof)
        self.assertFalse(workspace.cleanup_remove(self.state, manifest, resume=True)["alreadyRemoved"])
        self.assertFalse(checkout.exists())

    def test_cleanup_lists_all_fixed_refs_across_retained_workspaces(self):
        manifest = self.prepare()
        repository = manifest["snapshot"]["repositoryPath"]
        related = dict(manifest, workspaceId="ws-" + "f" * 32)
        refs = [f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/retained/extra-{index}"
                for index in range(65)]
        refs.append(f"refs/hey-my-buddy/workspaces/{related['workspaceId']}/retained/later")
        self.git("update-ref", "--stdin", cwd=repository,
                 data="".join(f"create {ref} {manifest['inputCommit']}\n" for ref in refs).encode())
        proof = workspace.cleanup_inspect(self.state, manifest, retained=[related])
        self.assertTrue(proof["eligible"])
        self.assertTrue(set(refs) <= set(proof["refs"]))

    def test_resumed_cleanup_checks_modes_and_symlinks_without_following_them(self):
        for change in ("mode", "file-link", "directory-link"):
            with self.subTest(change=change):
                if change == "mode" and workspace._WINDOWS:
                    continue  # Windows has no independent executable file bit.
                manifest = self.prepare(request=change)
                checkout = Path(manifest["path"])
                sealed = self.seal(manifest)
                outside = self.root / f"outside-{change}"
                outside.mkdir()
                target = outside / "retained.bin"
                target.write_bytes(b"outside the allocation\x00\xff")
                if change == "mode":
                    path = checkout / "src/file.txt"
                    path.chmod(0o755)
                    changed = "src/file.txt"
                elif change == "file-link":
                    path = checkout / "src/file.txt"
                    path.unlink()
                    path.symlink_to(target)
                    changed = "src/file.txt"
                else:
                    (checkout / "external").symlink_to(outside, target_is_directory=True)
                    changed = "external"
                self.git("worktree", "unlock", str(checkout))
                proof = workspace.cleanup_inspect(self.state, manifest, sealed=sealed, resume=True)
                self.assertFalse(proof["eligible"], proof)
                self.assertEqual(proof["reasons"], ["unsealed-changes"])
                self.assertEqual(proof["unsealedPaths"], [changed])
                self.assertEqual(proof["locked"], "hey-my-buddy:" + manifest["workspaceId"])
                self.assertEqual(target.read_bytes(), b"outside the allocation\x00\xff")

    def test_resumed_cleanup_keeps_unsealed_index_content_without_a_gitfile(self):
        manifest = self.prepare()
        checkout = Path(manifest["path"])
        sealed = self.seal(manifest)
        git_dir = Path(workspace.inspect(str(checkout))["gitDir"])
        initial_index = workspace._entries(checkout)
        (checkout / "src/file.txt").write_bytes(b"staged only\x00\xff")
        self.git("add", "src/file.txt", cwd=checkout)
        staged_index = workspace._entries(checkout)
        (checkout / "src/file.txt").write_text("base\n", encoding="utf-8", newline="\n")
        ordinary = workspace.cleanup_inspect(self.state, manifest, sealed=sealed)
        self.git("worktree", "unlock", str(checkout))
        (checkout / ".git").unlink()
        (checkout / "docs/readme.md").unlink()
        self.assertEqual(workspace._entries(git_dir), staged_index)
        proof = workspace.cleanup_inspect(self.state, manifest, sealed=sealed, resume=True)
        self.assertFalse(proof["eligible"], proof)
        self.assertEqual(proof["reasons"], ["unsealed-changes"])
        self.assertEqual(proof["unsealedPaths"], ordinary["unsealedPaths"])
        self.assertEqual(workspace._entries(git_dir), staged_index)
        self.assertEqual((checkout / "src/file.txt").read_text(encoding="utf-8"), "base\n")
        workspace._git(git_dir, "read-tree", manifest["inputTree"])
        self.assertEqual(workspace._entries(git_dir), initial_index)
        self.assertTrue(workspace.cleanup_inspect(self.state, manifest, sealed=sealed, resume=True)["eligible"])
        self.assertFalse(workspace.cleanup_remove(self.state, manifest, resume=True)["alreadyRemoved"])

    def test_resumed_cleanup_keeps_an_unsealed_head_without_a_gitfile(self):
        manifest = self.prepare()
        checkout = Path(manifest["path"])
        sealed = self.seal(manifest)
        git_dir = Path(workspace.inspect(str(checkout))["gitDir"])
        self.git("commit", "--allow-empty", "-qm", "not sealed", cwd=checkout)
        unsealed_head = self.git("rev-parse", "HEAD", cwd=checkout)
        ordinary = workspace.cleanup_inspect(self.state, manifest, sealed=sealed)
        self.git("worktree", "unlock", str(checkout))
        (checkout / ".git").unlink()
        proof = workspace.cleanup_inspect(self.state, manifest, sealed=sealed, resume=True)
        self.assertFalse(proof["eligible"], proof)
        self.assertEqual(proof["reasons"], ["unsealed-changes"])
        self.assertEqual(proof["unsealedPaths"], ordinary["unsealedPaths"])
        self.assertEqual(workspace._git(git_dir, "rev-parse", "HEAD"), unsealed_head)
        workspace._git(git_dir, "update-ref", "--no-deref", "HEAD", sealed["snapshot"]["headCommit"])
        self.assertTrue(workspace.cleanup_inspect(self.state, manifest, sealed=sealed, resume=True)["eligible"])
        self.assertFalse(workspace.cleanup_remove(self.state, manifest, resume=True)["alreadyRemoved"])

    def test_resumed_cleanup_restores_missing_binary_modes_links_and_parents(self):
        payload = self.repo / "src" / "deep" / "payload.bin"
        payload.parent.mkdir()
        payload.write_bytes(b"retained executable\x00\xff")
        payload.chmod(0o755)
        link = self.repo / "src" / "deep" / "link"
        link.symlink_to("payload.bin")
        self.git("add", ".")
        self.git("commit", "-qm", "retained links and binary")
        manifest = self.prepare()
        checkout = Path(manifest["path"])
        sealed = self.seal(manifest)
        (checkout / "src/deep/payload.bin").unlink()
        (checkout / "src/deep/link").unlink()
        (checkout / "src/deep").rmdir()
        (checkout / ".git").unlink()
        self.git("worktree", "unlock", str(checkout))
        proof = workspace.cleanup_inspect(self.state, manifest, sealed=sealed, resume=True)
        self.assertTrue(proof["eligible"], proof)
        self.assertEqual((checkout / "src/deep/payload.bin").read_bytes(), b"retained executable\x00\xff")
        self.assertEqual((checkout / "src/deep/link").readlink(), Path("payload.bin"))
        if not workspace._WINDOWS:
            self.assertEqual((checkout / "src/deep/payload.bin").stat().st_mode & 0o777, 0o755)
        self.assertEqual(workspace.inspect(str(checkout))["checkoutId"], manifest["checkoutId"])
        self.assertTrue(workspace.cleanup_remove(self.state, manifest, resume=True)["removed"])

    def test_resumed_cleanup_never_overwrites_a_file_created_during_restoration(self):
        manifest = self.prepare()
        checkout = Path(manifest["path"])
        sealed = self.seal(manifest)
        (checkout / "src/file.txt").unlink()
        (checkout / ".git").unlink()
        self.git("worktree", "unlock", str(checkout))
        materialize = workspace._materialize

        def competing_write(root, entries):
            self.assertEqual(set(entries), {"src/file.txt"})
            (checkout / "src/file.txt").write_bytes(b"new unsealed bytes\x00\xff")
            materialize(root, entries)

        with patch.object(workspace, "_materialize", side_effect=competing_write):
            self.assert_error("WORKSPACE_IO_ERROR", workspace.cleanup_inspect,
                              self.state, manifest, sealed=sealed, resume=True)
        self.assertEqual((checkout / "src/file.txt").read_bytes(), b"new unsealed bytes\x00\xff")
        self.assertEqual(workspace.inspect(str(checkout))["checkoutId"], manifest["checkoutId"])
        self.assertEqual(workspace._worktree_record(self.repo, checkout)["locked"],
                         "hey-my-buddy:" + manifest["workspaceId"])
        proof = workspace.cleanup_inspect(self.state, manifest, sealed=sealed, resume=True)
        self.assertFalse(proof["eligible"], proof)
        self.assertEqual(proof["unsealedPaths"], ["src/file.txt"])


if __name__ == "__main__":
    unittest.main()
