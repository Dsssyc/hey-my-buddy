"""Physical deletion boundaries against disposable Git worktrees."""
import unittest
from pathlib import Path
from types import SimpleNamespace

from blackboard.tasks.test_workspace import WorkspaceTests
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.blackboard.tasks.macro_task_deletion_workspace import delete_workspaces


class DeletionWorkspaceTests(unittest.TestCase):
    setUp = WorkspaceTests.setUp
    _seed_repository = WorkspaceTests._seed_repository
    git = WorkspaceTests.git
    intent = WorkspaceTests.intent
    prepare = WorkspaceTests.prepare

    def delete(self, manifest, artifacts=()):
        return delete_workspaces(SimpleNamespace(directory=self.state), [manifest], list(artifacts))

    def test_deletes_checkout_refs_and_sealed_record_then_replays(self):
        manifest = self.prepare()
        directory = self.state / "workspaces" / manifest["workspaceId"]
        checkout = Path(manifest["checkoutRoot"])
        (checkout / "src/file.txt").write_text("result\n")
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
        (checkout / "src/new.txt").write_text("unsealed\n")
        skipped = self.delete(manifest)
        self.assertIn("unsealed-changes", [item["reasonCode"] for item in skipped])
        self.assertTrue(checkout.exists())
        self.assertTrue(self.git("show-ref", "--verify", manifest["snapshot"]["inputRef"]))
        (checkout / "src/new.txt").unlink()
        self.git("worktree", "unlock", str(checkout))
        self.git("worktree", "lock", "--reason", "someone-else", str(checkout))
        skipped = self.delete(manifest)
        self.assertIn("lock-mismatch", [item["reasonCode"] for item in skipped])
        self.assertTrue(checkout.exists())

    def test_enumerates_more_than_sixty_four_fixed_refs(self):
        manifest = self.prepare()
        checkout = Path(manifest["checkoutRoot"])
        (checkout / "src/file.txt").write_text("sealed\n")
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
        self.assertIn("REF_UNPROVEN", [item["reasonCode"] for item in skipped])
        self.assertTrue((self.state / "workspaces" / identifier).exists())
        self.assertTrue(self.git("show-ref", "--verify", unknown))
