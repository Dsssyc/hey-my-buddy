"""Physical deletion boundaries against disposable Git worktrees."""
import unittest
import os
from protocol.session_case import descendant_case
import json
from pathlib import Path
from unittest.mock import patch

from blackboard.tasks import test_workspace as workspace_tests
from hey_my_buddy import private_dirs
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.blackboard.tasks import macro_task_deletion_workspace as deletion_workspace
from hey_my_buddy.blackboard.tasks.macro_task_deletion_workspace import delete_workspaces
from hey_my_buddy.paths import native_path


class DeletionWorkspaceTests(unittest.TestCase):
    setUp = workspace_tests.WorkspaceTests.setUp
    _seed_repository = workspace_tests.WorkspaceTests._seed_repository
    git = workspace_tests.WorkspaceTests.git
    intent = workspace_tests.WorkspaceTests.intent
    prepare = workspace_tests.WorkspaceTests.prepare

    def delete_many(self, manifests, artifacts=(), **kwargs):
        store, owners, records = workspace_tests.deletion_sql_inputs(self, manifests, artifacts)
        return delete_workspaces(store, owners, records, **kwargs)

    def delete(self, manifest, artifacts=()):
        return self.delete_many([manifest], artifacts)

    def test_later_io_failure_keeps_earlier_skipped_allocations(self):
        manifests = sorted([self.prepare("mixed-one"), self.prepare("mixed-two")],
                           key=lambda item: item["workspaceId"])
        first = self.state / "workspaces" / manifests[0]["workspaceId"]
        request = json.loads((first / 'request.json').read_bytes())
        request['requestId'] = 'different-allocation-owner'
        (first / 'request.json').write_bytes(workspace._json(request))
        second = self.state / "workspaces" / manifests[1]["workspaceId"]
        original = Path.rename
        failure = PermissionError(13, "record directory held open", str(second))

        def rename(path, destination):
            if path == native_path(second):
                raise failure
            return original(path, destination)

        with patch.object(Path, "rename", autospec=True, side_effect=rename):
            with self.assertRaises(PermissionError) as caught:
                self.delete_many(manifests)
        self.assertIs(caught.exception, failure)
        self.assertEqual([item["path"] for item in failure.deletion_skipped], [str(first)])
        self.assertEqual(failure.deletion_skipped[0]["reason"]["code"],
                         "macro-task-deletion.owner-unproven")
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())

    @unittest.skipIf(os.name == "nt", "POSIX cwd guard")
    def test_daemon_cwd_refuses_deletion_and_exit_allows_retry(self):
        manifest = self.prepare()
        checkout = Path(manifest["checkoutRoot"])
        directory = self.state / "workspaces" / manifest["workspaceId"]
        with descendant_case(self, self.state, "daemon", cwd=str(checkout)) as (handle, member):
            self.assertTrue(handle.terminate(grace_seconds=0)[0])
            with self.assertRaises(BoardError) as caught:
                self.delete(manifest)
            self.assertEqual(caught.exception.details["reason"]["code"], "checkout.process-blockers")
            processes = caught.exception.details["reason"]["params"]["processes"]
            self.assertIn(member["pid"], [process["pid"] for process in processes])
            self.assertTrue(checkout.exists())
            self.assertTrue(directory.exists())
            self.assertTrue(self.git("show-ref", "--verify", manifest["snapshot"]["inputRef"]))
        self.assertEqual(self.delete(manifest), [])
        self.assertFalse(directory.exists())

    def test_deletes_checkout_refs_and_sealed_record_then_replays(self):
        for separate in (False, True):
            with self.subTest(separate=separate):
                manifest = self.prepare(request=f"delete-patches-{separate}")
                directory = self.state / "workspaces" / manifest["workspaceId"]
                checkout = Path(manifest["checkoutRoot"])
                (checkout / "src/file.txt").write_text("result\n", encoding="utf-8", newline="\n")
                output = workspace.seal(self.state, manifest, f"task-{separate}", "attempt-1")
                output["cumulativePatch"] = workspace.cumulative_patch(manifest, output, manifest["inputCommit"])
                if separate:
                    # An existing seal retains the older two-file form.
                    legacy = Path(output["diffPath"]).with_name(f"cumulative-{manifest['inputCommit']}.patch")
                    native_path(legacy).write_bytes(native_path(Path(output["diffPath"])).read_bytes())
                    output["cumulativePatch"]["path"] = str(legacy)
                patches = {Path(output["diffPath"]), Path(output["cumulativePatch"]["path"])}
                self.assertEqual(len(patches), 2 if separate else 1)
                self.assertTrue(all(native_path(path).is_file() for path in patches))
                self.assertEqual(self.delete(manifest, [output]), [])
                self.assertFalse(directory.exists())
                self.assertFalse(checkout.exists())
                self.assertTrue(all(not native_path(path).exists() for path in patches))
                self.assertFalse(self.git("for-each-ref", "--format=%(refname)",
                                          f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"))
                self.assertEqual(self.delete(manifest, [output]), [])

    def test_changed_lock_preserves_checkout_and_refs(self):
        manifest = self.prepare()
        checkout = Path(manifest["checkoutRoot"])
        later = self.prepare("retained-later", kind="existing", cwd=str(checkout))
        (checkout / "src/new.txt").write_text("unsealed\n", encoding="utf-8", newline="\n")
        self.git("worktree", "unlock", str(checkout))
        self.git("worktree", "lock", "--reason", "someone-else", str(checkout))
        skipped = self.delete_many([manifest, later])
        self.assertIn("macro-task-deletion.lock-mismatch", [item["reason"]["code"] for item in skipped])
        self.assertIn(str(self.state / "workspaces" / later["workspaceId"]),
                      [item["path"] for item in skipped])
        self.assertTrue(checkout.exists())

    def test_equal_worker_and_host_snapshots_remove_both_physical_ref_names(self):
        manifest = self.prepare()
        worker = workspace.seal(self.state, manifest, 'task-1', 'attempt-1')
        host = workspace.host_seal(self.state, manifest, 'task-1', 'attempt-1')
        self.assertEqual(worker['snapshotSha256'], host['snapshotSha256'])
        # Artifact publication deduplicates these equal snapshots. Only the
        # Worker's record remains in SQLite, while both physical seals exist.
        self.assertEqual(self.delete(manifest, [worker]), [])
        self.assertFalse((self.state / 'workspaces' / manifest['workspaceId']).exists())
        self.assertFalse(self.git('for-each-ref', '--format=%(refname)',
                                  f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"))

    def test_distinct_worker_and_host_outputs_keep_their_explicit_ref_bindings(self):
        manifest = self.prepare()
        worker = workspace.seal(self.state, manifest, 'task-1', 'attempt-1')
        (Path(manifest['checkoutRoot']) / 'src/file.txt').write_text('Host change\n', encoding='utf-8', newline='\n')
        host = workspace.host_seal(self.state, manifest, 'task-1', 'attempt-1')
        self.assertNotEqual(worker['snapshotSha256'], host['snapshotSha256'])
        self.assertEqual(self.delete(manifest, [worker, host]), [])

    def test_unpublished_distinct_host_output_retains_its_different_commit(self):
        manifest = self.prepare()
        worker = workspace.seal(self.state, manifest, 'task-1', 'attempt-1')
        (Path(manifest['checkoutRoot']) / 'src/file.txt').write_text('Unpublished Host change\n', encoding='utf-8', newline='\n')
        host = workspace.host_seal(self.state, manifest, 'task-1', 'attempt-1')
        skipped = self.delete(manifest, [worker])
        self.assertIn('macro-task-deletion.ref-changed', [item['reason']['code'] for item in skipped])
        self.assertEqual(self.git('rev-parse', host['ref']).decode().strip(), host['commit'])
        self.assertTrue((self.state / 'workspaces' / manifest['workspaceId']).exists())

    def test_a_ref_becoming_symbolic_during_delete_never_deletes_its_target_branch(self):
        manifest = self.prepare()
        worker = workspace.seal(self.state, manifest, 'task-1', 'attempt-1')
        branch = self.git('symbolic-ref', 'HEAD').decode().strip()
        branch_commit = self.git('rev-parse', branch)
        original = workspace._git
        raced = False

        def symbolic_before_delete(repository, *args, **kwargs):
            nonlocal raced
            if not raced and args[0] == 'update-ref' and '-d' in args and worker['ref'] in args:
                raced = True
                original(repository, 'symbolic-ref', worker['ref'], branch)
            return original(repository, *args, **kwargs)

        with patch.object(workspace, '_git', side_effect=symbolic_before_delete):
            self.delete(manifest, [worker])
        self.assertTrue(raced)
        self.assertEqual(self.git('rev-parse', '--verify', branch), branch_commit)

    def test_unlocked_checkout_proof_is_read_only_and_cannot_authorize_deletion(self):
        manifest = self.prepare()
        checkout = Path(manifest['checkoutRoot'])
        self.git('worktree', 'unlock', str(checkout))
        before = self.git('for-each-ref', '--format=%(refname) %(objectname)', 'refs/hey-my-buddy/')
        proof = workspace.deletion_inspect(self.state, manifest)
        self.assertEqual(proof['reasons'], ['lock-mismatch'])
        self.assertNotIn('locked', workspace._worktree_record(self.repo, checkout))
        skipped = self.delete(manifest)
        self.assertIn('macro-task-deletion.lock-mismatch', [item['reason']['code'] for item in skipped])
        self.assertTrue(checkout.exists())
        self.assertNotIn('locked', workspace._worktree_record(self.repo, checkout))
        self.assertEqual(self.git('for-each-ref', '--format=%(refname) %(objectname)', 'refs/hey-my-buddy/'), before)

    def test_worker_commit_and_unsealed_files_do_not_block_owned_macro_deletion(self):
        manifest = self.prepare()
        checkout = Path(manifest["checkoutRoot"])
        (checkout / "src/file.txt").write_text("Worker commit\n", encoding="utf-8")
        self.git("add", ".", cwd=checkout)
        self.git("commit", "-qm", "Worker commit", cwd=checkout)
        (checkout / "src/new.txt").write_text("unsealed\n", encoding="utf-8")
        self.assertEqual(self.delete(manifest), [])
        self.assertFalse((self.state / "workspaces" / manifest["workspaceId"]).exists())
        self.assertFalse(self.git("for-each-ref", "--format=%(refname)",
                                  f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"))

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
                    owner, name = workspace, "deletion_remove_checkout"
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
                intercepted = False
                def malformed(root, *args, **kwargs):
                    nonlocal intercepted
                    if args[:2] == ("for-each-ref", "--format=%(refname)%00%(objectname)%00%(symref)"):
                        intercepted = True
                        return (b"refs/hey-my-buddy/workspaces/" + manifest["workspaceId"].encode()
                                + b"/bad\x00" + oid + b"\x00\n")
                    return original(root, *args, **kwargs)
                with patch.object(workspace, "_git", side_effect=malformed):
                    with self.assertRaises(BoardError) as caught:
                        self.delete(manifest)
                self.assertTrue(intercepted)
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

    def repository_case(self, layout, label=""):
        manifest = self.prepare("repository-" + label + layout, kind="existing" if layout == "logical" else "worktree")
        directory = self.state / "workspaces" / manifest["workspaceId"]
        if layout == "refs":
            private_dirs.remove_tree(directory)
        elif layout == "no-checkout":
            private_dirs.remove_tree(directory / "checkout")
        elif layout == "tombstone":
            directory.rename(deletion_workspace._tombstone(self.state, manifest["workspaceId"]))
        return manifest, directory

    def test_deleted_repository_clears_private_allocations_without_git(self):
        self.unavailable_repository_case("deleted")

    def test_moved_repository_clears_private_allocations_and_leaves_its_refs(self):
        self.unavailable_repository_case("moved")

    def test_known_moved_repository_removes_exact_allocation_refs(self):
        manifest = self.prepare("known-moved-repository")
        checkout = Path(manifest["checkoutRoot"])
        moved = self.root / "known-moved-location"
        self.repo.rename(moved)
        skipped = self.delete_many(
            [manifest],
            candidate_paths={manifest["workspaceId"]: (str(moved),)},
        )
        self.assertEqual(skipped, [])
        self.assertFalse(checkout.exists())
        self.assertFalse((self.state / "workspaces" / manifest["workspaceId"]).exists())
        prefix = f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"
        self.assertFalse(self.git("for-each-ref", "--format=%(refname)", prefix, cwd=moved))

    def test_moved_existing_refs_are_removed_without_repairing_user_checkout(self):
        manifest = self.prepare("moved-existing-refs", kind="existing")
        moved = self.root / "moved-existing-location"
        self.repo.rename(moved)
        with patch.object(workspace, "_git_in", side_effect=AssertionError("existing checkout was repaired")):
            skipped = self.delete_many(
                [manifest],
                candidate_paths={manifest["workspaceId"]: (str(moved),)},
            )
        self.assertEqual(skipped, [])
        self.assertTrue(moved.is_dir())
        prefix = f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"
        self.assertFalse(self.git("for-each-ref", "--format=%(refname)", prefix, cwd=moved))

    def test_replaced_repository_clears_private_allocations_without_touching_replacement(self):
        self.unavailable_repository_case("replaced")

    def unavailable_repository_case(self, mode):
        for layout in ("checkout", "logical", "no-checkout", "refs", "tombstone"):
            with self.subTest(mode=mode, layout=layout):
                manifest, directory = self.repository_case(layout, mode + "-")
                prefix = f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"
                refs = self.git("for-each-ref", "--format=%(refname)%00%(objectname)", prefix)
                self.assertTrue(refs)
                retained = self.root / ("old-repository-" + layout)
                if mode == "deleted":
                    private_dirs.remove_tree(self.repo)
                else:
                    self.repo.rename(retained)
                replacement = None
                if mode == "replaced":
                    self.repo.mkdir()
                    self._seed_repository()
                    replacement = {str(path.relative_to(self.repo)): path.read_bytes()
                                   for path in self.repo.rglob("*") if path.is_file()}
                try:
                    self.assertEqual(self.delete(manifest), [])
                    self.assertFalse(directory.exists())
                    self.assertFalse(deletion_workspace._tombstone(self.state, manifest["workspaceId"]).exists())
                    if replacement is not None:
                        self.assertEqual({str(path.relative_to(self.repo)): path.read_bytes()
                                          for path in self.repo.rglob("*") if path.is_file()}, replacement)
                    if mode != "deleted":
                        self.assertEqual(self.git("for-each-ref", "--format=%(refname)%00%(objectname)", prefix,
                                                  cwd=retained), refs)
                finally:
                    if mode == "deleted":
                        self.repo.mkdir()
                        self._seed_repository()
                    else:
                        if mode == "replaced":
                            private_dirs.remove_tree(self.repo)
                        retained.rename(self.repo)

    def test_repository_io_errors_are_not_skipped(self):
        original = workspace.repository_at
        for layout in ("checkout", "logical", "no-checkout", "refs"):
            manifest, _ = self.repository_case(layout)
            repository = Path(manifest["snapshot"]["repositoryPath"])
            for error in (PermissionError("unreadable repository"), OSError("repository I/O failed")):
                with self.subTest(layout=layout, error=type(error).__name__):
                    def read_repository(path):
                        if Path(path) in (repository, Path(manifest["checkoutRoot"])):
                            raise error
                        return original(path)
                    with patch.object(workspace, "repository_at", side_effect=read_repository), \
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

    def test_moved_repository_does_not_block_removal_of_an_interrupted_tombstone(self):
        manifest, directory = self.repository_case("logical")
        tomb = deletion_workspace._tombstone(self.state, manifest["workspaceId"])
        retained = self.root / "retained-repository"
        original = private_dirs.remove_tree

        def interrupt(path, **kwargs):
            if path == tomb:
                (path / "manifest.json").rename(self.root / "retained-manifest.json")
                raise OSError("interrupted private record removal")
            return original(path, **kwargs)

        with patch.object(private_dirs, "remove_tree", side_effect=interrupt):
            with self.assertRaisesRegex(OSError, "interrupted private record removal"):
                self.delete(manifest)
        self.assertFalse(directory.exists())
        self.assertTrue(tomb.exists())
        self.repo.rename(retained)
        try:
            self.assertEqual(self.delete(manifest), [])
            self.assertFalse(tomb.exists())
        finally:
            retained.rename(self.repo)
        self.assertEqual(self.delete(manifest), [])
        self.assertFalse(tomb.exists())

    def test_unavailable_repository_clears_checkout_and_shared_logical_records(self):
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
                        skipped = self.delete_many([later, allocation])
                    delete_refs.assert_not_called()
                    self.assertFalse(directory.exists())
                    self.assertFalse(native_path(Path(allocation["checkoutRoot"])).exists())
                    self.assertEqual(skipped, [])
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
        skipped = self.delete_many([later, allocation, later], [output])
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
                raced = False
                def race(root,*args,**kwargs):
                    nonlocal raced
                    if args[:4] == ('update-ref','--no-deref','-d',ref):
                        raced = True
                        if changed:
                            original(root,'update-ref',ref,replacement)
                        else:
                            original(root,*args,**kwargs)
                        raise BoardError('WORKSPACE_GIT_ERROR','Compare delete lost its race')
                    return original(root,*args,**kwargs)
                with patch.object(workspace,'_git',side_effect=race):
                    skipped = self.delete(manifest)
                self.assertTrue(raced)
                if changed:
                    self.assertIn('macro-task-deletion.ref-changed',[item['reason']['code'] for item in skipped])
                    self.assertEqual(self.git('rev-parse',ref).decode().strip(),replacement)
                else:
                    self.assertEqual(skipped,[])
                    self.assertFalse((self.state/'workspaces'/manifest['workspaceId']).exists())
