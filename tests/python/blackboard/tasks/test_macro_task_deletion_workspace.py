"""Physical deletion boundaries against disposable Git worktrees."""
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from blackboard.tasks import test_workspace as workspace_tests
from hey_my_buddy import private_dirs
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.blackboard.tasks import macro_task_deletion_workspace as deletion_workspace
from hey_my_buddy.blackboard.tasks.macro_task_deletion_workspace import delete_workspaces


class DeletionWorkspaceTests(unittest.TestCase):
    setUp = workspace_tests.WorkspaceTests.setUp
    _seed_repository = workspace_tests.WorkspaceTests._seed_repository
    git = workspace_tests.WorkspaceTests.git
    intent = workspace_tests.WorkspaceTests.intent
    prepare = workspace_tests.WorkspaceTests.prepare

    def delete(self, manifest, artifacts=()):
        return delete_workspaces(SimpleNamespace(directory=self.state), [manifest], list(artifacts))

    def test_deletes_checkout_refs_and_sealed_record_then_replays(self):
        manifest = self.prepare()
        directory = self.state / "workspaces" / manifest["workspaceId"]
        checkout = Path(manifest["checkoutRoot"])
        (checkout / "src/file.txt").write_text("result\n", encoding="utf-8", newline="\n")
        output = workspace.seal(self.state, manifest, "task-1", "attempt-1")
        self.assertEqual(self.delete(manifest, [output]), [])
        self.assertFalse(directory.exists())
        self.assertFalse(checkout.exists())
        self.assertFalse(self.git("for-each-ref", "--format=%(refname)",
                                  f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"))
        self.assertEqual(self.delete(manifest, [output]), [])

    def test_changed_lock_and_unsealed_file_preserve_checkout_and_refs(self):
        manifest = self.prepare()
        checkout = Path(manifest["checkoutRoot"])
        (checkout / "src/new.txt").write_text("unsealed\n", encoding="utf-8", newline="\n")
        skipped = self.delete(manifest)
        self.assertIn("macro-task-deletion.unsealed-changes", [item["reason"]["code"] for item in skipped])
        self.assertTrue(checkout.exists())
        self.assertTrue(self.git("show-ref", "--verify", manifest["snapshot"]["inputRef"]))
        (checkout / "src/new.txt").unlink()
        self.git("worktree", "unlock", str(checkout))
        self.git("worktree", "lock", "--reason", "someone-else", str(checkout))
        skipped = self.delete(manifest)
        self.assertIn("macro-task-deletion.lock-mismatch", [item["reason"]["code"] for item in skipped])
        self.assertTrue(checkout.exists())

    def test_enumerates_more_than_sixty_four_fixed_refs(self):
        manifest = self.prepare()
        checkout = Path(manifest["checkoutRoot"])
        (checkout / "src/file.txt").write_text("sealed\n", encoding="utf-8", newline="\n")
        outputs = [workspace.seal(self.state, manifest, "task-1", f"attempt-{number}")
                   for number in range(34)]
        # Each output has one fixed and one retained ref, in addition to the
        # four input refs. This exercises Git enumeration past the old 64 cap.
        self.assertEqual(self.delete(manifest, outputs), [])
        self.assertFalse(checkout.exists())

    def test_unknown_reference_preserves_allocation_record(self):
        manifest = self.prepare()
        identifier = manifest["workspaceId"]
        unknown = f"refs/hey-my-buddy/workspaces/{identifier}/unknown"
        self.git("update-ref", unknown, manifest["inputCommit"])
        skipped = self.delete(manifest)
        self.assertIn("macro-task-deletion.ref-unproven", [item["reason"]["code"] for item in skipped])
        self.assertTrue((self.state / "workspaces" / identifier).exists())
        self.assertTrue(self.git("show-ref", "--verify", unknown))

    def test_interrupted_after_each_phase_replays(self):
        for phase in ("checkout", "refs", "allocation"):
            with self.subTest(phase=phase):
                manifest = self.prepare(request="request-" + phase)
                directory = self.state / "workspaces" / manifest["workspaceId"]
                tomb = directory.parent / (".deleting-" + manifest["workspaceId"])
                if phase == "checkout":
                    owner, name = workspace, "cleanup_remove"
                elif phase == "refs":
                    owner, name = deletion_workspace, "_delete_refs"
                else:
                    owner, name = private_dirs, "remove_tree"
                original = getattr(owner, name)
                def interrupt(*args, **kwargs):
                    result = original(*args, **kwargs)
                    if phase != "allocation" or args[0] == tomb:
                        raise OSError("interrupted")
                    return result
                with patch.object(owner, name, side_effect=interrupt):
                    with self.assertRaisesRegex(OSError, "interrupted"):
                        self.delete(manifest)
                self.assertEqual(self.delete(manifest), [])
                self.assertFalse(directory.exists())

    def test_interrupted_recursive_removal_replays_without_proof_files(self):
        manifest = self.prepare()
        identifier = manifest["workspaceId"]
        directory = self.state / "workspaces" / identifier
        tomb = directory.parent / (".deleting-" + identifier)
        original = private_dirs.remove_tree

        def interrupt(path, **kwargs):
            if path == tomb:
                for name in ("manifest.json", "request.json", "input.json", ".lock"):
                    (path / name).unlink()
                raise OSError("interrupted halfway through removal")
            return original(path, **kwargs)

        with patch.object(private_dirs, "remove_tree", side_effect=interrupt):
            with self.assertRaisesRegex(OSError, "interrupted halfway"):
                self.delete(manifest)
        self.assertFalse(directory.exists())
        self.assertTrue(tomb.exists())
        self.assertFalse((tomb / "manifest.json").exists())
        self.assertEqual(self.delete(manifest), [])
        self.assertFalse(tomb.exists())

    def test_allocation_lock_directory_requires_existing_lock(self):
        manifest = self.prepare()
        identifier = manifest["workspaceId"]
        directory = self.state / "workspaces" / identifier
        self.assertEqual(workspace.allocation_lock_directory(self.state, identifier), directory)
        (directory / ".lock").unlink()
        self.assertIsNone(workspace.allocation_lock_directory(self.state, identifier))
        self.assertFalse((directory / ".lock").exists())

    def test_malformed_reference_inventory_is_board_error(self):
        manifest = self.prepare()
        original = workspace._git
        for oid in (b"\xff", b"not-an-oid"):
            with self.subTest(oid=oid):
                def malformed(root, *args, **kwargs):
                    if args[:2] == ("for-each-ref", "--format=%(refname)%00%(objectname)"):
                        return (b"refs/hey-my-buddy/workspaces/" + manifest["workspaceId"].encode()
                                + b"/bad\x00" + oid + b"\n")
                    return original(root, *args, **kwargs)
                with patch.object(workspace, "_git", side_effect=malformed):
                    with self.assertRaises(BoardError) as caught:
                        self.delete(manifest)
                self.assertEqual(caught.exception.code, "WORKSPACE_GIT_ERROR")
        self.assertTrue((self.state / "workspaces" / manifest["workspaceId"]).exists())

    def test_missing_checkout_still_removes_refs_and_record(self):
        manifest = self.prepare()
        directory = self.state / "workspaces" / manifest["workspaceId"]
        checkout = Path(manifest["checkoutRoot"])
        self.git("worktree", "unlock", str(checkout))
        self.git("worktree", "remove", "--force", str(checkout))
        self.assertEqual(self.delete(manifest), [])
        self.assertFalse(directory.exists())
        self.assertFalse(self.git("for-each-ref", "--format=%(refname)",
                                  f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"))

    def test_missing_allocation_record_still_removes_bound_refs(self):
        manifest = self.prepare()
        directory = self.state / "workspaces" / manifest["workspaceId"]
        checkout = Path(manifest["checkoutRoot"])
        self.git("worktree", "unlock", str(checkout))
        self.git("worktree", "remove", "--force", str(checkout))
        private_dirs.remove_tree(directory)
        self.assertTrue(self.git("show-ref", "--verify", manifest["snapshot"]["inputRef"]))
        self.assertEqual(self.delete(manifest), [])
        self.assertFalse(self.git("for-each-ref", "--format=%(refname)",
                                  f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"))

    def repository_case(self, layout):
        manifest = self.prepare("repository-" + layout, kind="existing" if layout == "logical" else "worktree")
        directory = self.state / "workspaces" / manifest["workspaceId"]
        if layout == "refs":
            directory.rename(directory.with_name("retained-" + manifest["workspaceId"]))
        elif layout == "no-checkout":
            (directory / "checkout").rename(self.root / "retained-checkout")
        return manifest, directory

    def test_missing_repository_removes_only_allocations_without_checkout_and_preserves_refs(self):
        for layout in ("checkout", "logical", "no-checkout", "refs"):
            with self.subTest(layout=layout):
                manifest, directory = self.repository_case(layout)
                prefix = f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"
                refs = self.git("for-each-ref", "--format=%(refname)%00%(objectname)", prefix)
                self.assertTrue(refs)
                retained = self.root / ("retained-repository-" + layout)
                self.repo.rename(retained)
                try:
                    with patch.object(deletion_workspace, "_delete_refs") as delete_refs:
                        skipped = self.delete(manifest)
                    delete_refs.assert_not_called()
                    self.assertEqual(skipped, [{
                        "kind": "ref" if layout == "refs" else "allocation",
                        "path": manifest["snapshot"]["repositoryPath"], "workspaceId": manifest["workspaceId"],
                        "reason": {"code": "macro-task-deletion.repository-missing", "params": {}},
                    }])
                    self.assertEqual(directory.exists(), layout == "checkout")
                    self.assertFalse(deletion_workspace._tombstone(self.state, manifest["workspaceId"]).exists())
                    if layout == "checkout":
                        self.assertTrue(Path(manifest["checkoutRoot"]).exists())
                    self.assertEqual(self.git("for-each-ref", "--format=%(refname)%00%(objectname)", prefix,
                                              cwd=retained), refs)
                finally:
                    retained.rename(self.repo)

    def test_changed_repository_still_skips_without_touching_refs(self):
        for layout in ("checkout", "logical", "no-checkout", "refs"):
            with self.subTest(layout=layout):
                manifest, directory = self.repository_case(layout)
                repository = Path(manifest["snapshot"]["repositoryPath"])
                prefix = f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"
                refs = self.git("for-each-ref", "--format=%(refname)%00%(objectname)", prefix)
                retained = self.root / ("retained-git-" + layout)
                repository.rename(retained)
                repository.mkdir()
                try:
                    with patch.object(deletion_workspace, "_delete_refs") as delete_refs:
                        skipped = self.delete(manifest)
                    delete_refs.assert_not_called()
                    self.assertEqual(skipped, [{
                        "kind": "ref" if layout == "refs" else "allocation",
                        "path": prefix if layout == "refs" else str(directory),
                        "workspaceId": manifest["workspaceId"],
                        "reason": {"code": "macro-task-deletion.repository-changed", "params": {}},
                    }])
                    self.assertEqual(directory.exists(), layout != "refs")
                finally:
                    repository.rename(self.root / ("replacement-git-" + layout))
                    retained.rename(repository)
                self.assertEqual(self.git("for-each-ref", "--format=%(refname)%00%(objectname)", prefix), refs)

    def test_repository_io_errors_are_not_skipped(self):
        original = workspace._identity
        for layout in ("checkout", "logical", "no-checkout", "refs"):
            manifest, _ = self.repository_case(layout)
            repository = Path(manifest["snapshot"]["repositoryPath"])
            for error in (PermissionError("unreadable repository"), NotADirectoryError("invalid repository path"),
                          OSError("repository I/O failed")):
                with self.subTest(layout=layout, error=type(error).__name__):
                    def identity(path):
                        if Path(path) == repository:
                            raise error
                        return original(path)
                    with patch.object(workspace, "_identity", side_effect=identity), \
                            patch.object(deletion_workspace, "_delete_refs") as delete_refs:
                        with self.assertRaises(type(error)):
                            self.delete(manifest)
                    delete_refs.assert_not_called()

    def test_missing_repository_preserves_unproven_allocations(self):
        for layout in ("logical", "no-checkout"):
            with self.subTest(layout=layout):
                manifest, directory = self.repository_case(layout)
                request_path = directory / "request.json"
                request = json.loads(request_path.read_text(encoding="utf-8"))
                request["requestId"] = "another-owner"
                request_path.write_bytes(workspace._json(request))
                retained = self.root / ("retained-repository-" + layout)
                self.repo.rename(retained)
                try:
                    skipped = self.delete(manifest)
                    self.assertEqual(skipped, [{
                        "kind": "allocation", "path": str(directory), "workspaceId": manifest["workspaceId"],
                        "reason": {"code": "macro-task-deletion.owner-unproven", "params": {}},
                    }])
                    self.assertTrue(directory.exists())
                finally:
                    retained.rename(self.repo)

    def test_missing_repository_replays_interrupted_private_record_removal(self):
        manifest, directory = self.repository_case("logical")
        tomb = deletion_workspace._tombstone(self.state, manifest["workspaceId"])
        retained = self.root / "retained-repository"
        self.repo.rename(retained)
        original = private_dirs.remove_tree

        def interrupt(path, **kwargs):
            if path == tomb:
                (path / "manifest.json").rename(self.root / "retained-manifest.json")
                raise OSError("interrupted private record removal")
            return original(path, **kwargs)

        try:
            with patch.object(private_dirs, "remove_tree", side_effect=interrupt):
                with self.assertRaisesRegex(OSError, "interrupted private record removal"):
                    self.delete(manifest)
            self.assertFalse(directory.exists())
            self.assertTrue(tomb.exists())
            self.assertEqual(self.delete(manifest), [{
                "kind": "ref", "path": manifest["snapshot"]["repositoryPath"],
                "workspaceId": manifest["workspaceId"],
                "reason": {"code": "macro-task-deletion.repository-missing", "params": {}},
            }])
            self.assertFalse(tomb.exists())
        finally:
            retained.rename(self.repo)

    def test_repository_skip_preserves_checkout_and_only_reclaims_missing_repository_logical_records(self):
        for missing in (True, False):
            with self.subTest(missing=missing):
                allocation = self.prepare(request="allocation-" + str(missing))
                later = self.prepare(request="later-" + str(missing), kind="existing", cwd=allocation["path"])
                directory = self.state / "workspaces" / later["workspaceId"]
                repository = Path(allocation["snapshot"]["repositoryPath"])
                refs = self.git("for-each-ref", "--format=%(refname)%00%(objectname)", deletion_workspace._PREFIX)
                retained = self.root / ("retained-git-" + str(missing))
                repository.rename(retained)
                if not missing:
                    repository.mkdir()
                try:
                    with patch.object(deletion_workspace, "_delete_refs") as delete_refs:
                        skipped = delete_workspaces(SimpleNamespace(directory=self.state), [later, allocation], [])
                    delete_refs.assert_not_called()
                    self.assertEqual(directory.exists(), not missing)
                    self.assertTrue(Path(allocation["checkoutRoot"]).exists())
                    self.assertEqual({item["reason"]["code"] for item in skipped}, {
                        "macro-task-deletion.repository-missing" if missing else "macro-task-deletion.repository-changed",
                    })
                finally:
                    if not missing:
                        repository.rename(self.root / ("replacement-git-" + str(missing)))
                    retained.rename(repository)
                self.assertEqual(self.git("for-each-ref", "--format=%(refname)%00%(objectname)", deletion_workspace._PREFIX), refs)

    def test_later_turn_on_same_allocation_keeps_its_seal_until_checkout_removal(self):
        allocation = self.prepare(request="allocation")
        later = self.prepare(request="later-turn", kind="existing", cwd=allocation["path"])
        checkout = Path(allocation["checkoutRoot"])
        (checkout / "src/file.txt").write_text("later result\n", encoding="utf-8", newline="\n")
        output = workspace.seal(self.state, later, "task-1", "attempt-1")
        skipped = delete_workspaces(SimpleNamespace(directory=self.state),
                                    [later, allocation, later], [output])
        self.assertEqual(skipped, [])
        self.assertFalse(checkout.exists())
        for manifest in (allocation, later):
            self.assertFalse((self.state / "workspaces" / manifest["workspaceId"]).exists())
            self.assertFalse(self.git("for-each-ref", "--format=%(refname)",
                                      f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"))

    def test_user_checkout_is_preserved(self):
        manifest = self.prepare(kind="existing")
        self.assertEqual(self.delete(manifest), [])
        self.assertTrue(self.repo.is_dir())
        self.assertTrue((self.repo / "src/file.txt").is_file())
        self.assertFalse(self.git("for-each-ref", "--format=%(refname)",
                                  f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"))

    def test_bad_request_binding_does_not_create_a_workspace(self):
        manifest = self.prepare()
        directory = self.state / "workspaces" / manifest["workspaceId"]
        record = directory / "request.json"
        request = json.loads(record.read_text(encoding="utf-8"))
        request["requestId"] = "not-the-allocation-request"
        record.write_text(json.dumps(request), encoding="utf-8", newline="\n")
        before = set(directory.parent.iterdir())
        skipped = self.delete(manifest)
        self.assertIn("macro-task-deletion.owner-unproven", [item["reason"]["code"] for item in skipped])
        self.assertEqual(set(directory.parent.iterdir()), before)

    def test_linked_allocation_lock_does_not_allow_removal(self):
        manifest = self.prepare()
        directory = self.state / "workspaces" / manifest["workspaceId"]
        lock = directory / ".lock"
        lock.unlink()
        outside = self.root / "foreign-lock"
        outside.write_text("owned elsewhere", encoding="utf-8", newline="\n")
        lock.symlink_to(outside)
        with self.assertRaises((OSError, BoardError)):
            self.delete(manifest)
        self.assertEqual(outside.read_text(encoding="utf-8"), "owned elsewhere")
        self.assertTrue(self.git("show-ref", "--verify", manifest["snapshot"]["inputRef"]))

    def test_linked_allocation_directory_is_preserved(self):
        manifest = self.prepare()
        directory = self.state / "workspaces" / manifest["workspaceId"]
        outside = self.root / "outside-allocation"
        directory.rename(outside)
        directory.symlink_to(outside, target_is_directory=True)
        skipped = self.delete(manifest)
        self.assertTrue(skipped)
        self.assertTrue(directory.is_symlink())
        self.assertTrue(outside.is_dir())
        self.assertTrue(self.git("show-ref", "--verify", manifest["snapshot"]["inputRef"]))

    def test_git_remove_failure_with_unchanged_oid_is_an_error(self):
        manifest = self.prepare()
        checkout = Path(manifest["checkoutRoot"])
        self.git("worktree", "unlock", str(checkout))
        self.git("worktree", "remove", "--force", str(checkout))
        original = workspace._git
        def fail_remove(root, *args, **kwargs):
            if args[0] == "update-ref":
                raise BoardError("WORKSPACE_GIT_ERROR", "Git refused ref removal")
            return original(root, *args, **kwargs)
        with patch.object(workspace, "_git", side_effect=fail_remove):
            with self.assertRaises(BoardError) as caught:
                self.delete(manifest)
        self.assertEqual(caught.exception.code, "WORKSPACE_GIT_ERROR")
        self.assertTrue((self.state / "workspaces" / manifest["workspaceId"]).exists())
        self.assertEqual(self.delete(manifest), [])

    def test_changed_reference_is_retained(self):
        manifest = self.prepare()
        checkout = Path(manifest["checkoutRoot"])
        self.git("worktree", "unlock", str(checkout))
        self.git("worktree", "remove", "--force", str(checkout))
        ref = manifest["snapshot"]["inputRef"]
        replacement = self.git("rev-parse", "HEAD").decode().strip()
        if replacement == manifest["inputCommit"]:
            (self.repo / "docs/readme.md").write_text("changed\n", encoding="utf-8", newline="\n")
            self.git("add", ".")
            self.git("commit", "-qm", "replacement")
            replacement = self.git("rev-parse", "HEAD").decode().strip()
        self.git("update-ref", ref, replacement)
        skipped = self.delete(manifest)
        self.assertIn("macro-task-deletion.ref-changed", [item["reason"]["code"] for item in skipped])
        self.assertTrue((self.state / "workspaces" / manifest["workspaceId"]).exists())
        self.assertEqual(self.git("rev-parse", ref).decode().strip(), replacement)

    def test_compare_delete_preserves_a_raced_oid_and_accepts_raced_absence(self):
        for changed in (True,False):
            with self.subTest(changed=changed):
                manifest = self.prepare('race-' + str(changed))
                checkout = Path(manifest['checkoutRoot'])
                self.git('worktree','unlock',str(checkout))
                self.git('worktree','remove','--force',str(checkout))
                ref = manifest['snapshot']['inputRef']
                replacement = self.git('rev-parse','HEAD').decode().strip()
                if replacement == manifest['inputCommit']:
                    (self.repo / 'docs/readme.md').write_text('competing update ' + str(changed), encoding="utf-8", newline="\n")
                    self.git('add','.')
                    self.git('commit','-qm','competing ref update')
                    replacement = self.git('rev-parse','HEAD').decode().strip()
                original = workspace._git
                def race(root,*args,**kwargs):
                    if args[:3] == ('update-ref','-d',ref):
                        if changed:
                            original(root,'update-ref',ref,replacement)
                        else:
                            original(root,*args,**kwargs)
                        raise BoardError('WORKSPACE_GIT_ERROR','Compare delete lost its race')
                    return original(root,*args,**kwargs)
                with patch.object(workspace,'_git',side_effect=race):
                    skipped = self.delete(manifest)
                if changed:
                    self.assertIn('macro-task-deletion.ref-changed',[item['reason']['code'] for item in skipped])
                    self.assertEqual(self.git('rev-parse',ref).decode().strip(),replacement)
                else:
                    self.assertEqual(skipped,[])
                    self.assertFalse((self.state/'workspaces'/manifest['workspaceId']).exists())
