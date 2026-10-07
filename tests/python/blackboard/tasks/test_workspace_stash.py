"""The workspace module's shared-stash facts over real private Git repositories.

A managed checkout shares ``refs/stash`` with the user's own worktrees, so the
board freezes what existed before a turn and compares it at the seal boundary.
These tests pin the observation itself (commit ids, full descriptions, SHA-1 and
SHA-256 repositories, honest unknowns), the full-entry comparison with its
recovery command and cause-unknown note, and the fact that stash changes never
interact with prepare/seal/verify. Every repository is a private fixture created
by the test; no shared or daily board is touched.
"""
from __future__ import annotations

import json
import os
import subprocess
import unittest

from blackboard.tasks.test_workflow_real import GIT_ENV, RealWorkspaceTestCase

from hey_my_buddy.blackboard.tasks import workspace


class StashInventoryTestCase(RealWorkspaceTestCase):
    def stash(self, message: str) -> str:
        (self.repo / "tracked.txt").write_text(f"change {message}\n")
        self.git("add", "tracked.txt")
        self.git("stash", "push", "-q", "-m", message)
        return self.git("rev-parse", "stash@{0}").strip()


class SharedStashInventoryTests(StashInventoryTestCase):
    def test_reads_commit_ids_and_full_descriptions(self):
        first = self.stash("first message")
        second = self.stash("sécond ünïcødé 描述 with 'quotes'")
        inventory = workspace.shared_stash_inventory(str(self.repo))
        self.assertEqual(inventory["state"], "observed")
        self.assertEqual(inventory["repositoryPath"], str(self.repo))
        self.assertEqual(inventory["totalCount"], 2)
        self.assertFalse(inventory["truncated"])
        self.assertEqual([entry["commit"] for entry in inventory["entries"]], [second, first])
        # Descriptions are exactly what ``git stash list`` itself displays.
        shown = [line.split(": ", 1)[1] for line in self.git("stash", "list").splitlines()]
        self.assertEqual([entry["description"] for entry in inventory["entries"]], shown)
        self.assertIn("ünïcødé", inventory["entries"][0]["description"])

    def test_a_repository_without_stashes_is_an_empty_observation(self):
        inventory = workspace.shared_stash_inventory(str(self.repo))
        self.assertEqual(inventory["state"], "observed")
        self.assertEqual(inventory["entries"], [])
        self.assertEqual(inventory["totalCount"], 0)
        self.assertFalse(inventory["truncated"])

    def test_the_snapshot_repository_path_is_a_git_directory_and_still_reads(self):
        # A workspace snapshot's repositoryPath is the repository's common Git
        # directory, not a worktree: the observation must resolve it with Git's
        # own plumbing and read the shared reflog there without a work tree.
        first = self.stash("from the worktree view")
        from_gitdir = workspace.shared_stash_inventory(str(self.repo / ".git"))
        self.assertEqual(from_gitdir["state"], "observed")
        self.assertEqual([entry["commit"] for entry in from_gitdir["entries"]], [first])
        # A sibling worktree of the same repository sees the same shared entries,
        # because refs/stash and its reflog live in the common directory.
        sibling = self.directory / "sibling-worktree"
        self.git("worktree", "add", "-q", "--detach", str(sibling), "HEAD")
        from_worktree = workspace.shared_stash_inventory(str(sibling))
        self.assertEqual(from_worktree["state"], "observed")
        self.assertEqual([entry["commit"] for entry in from_worktree["entries"]], [first])
        # A worktree's private Git directory also resolves to the common one.
        private_gitdir = self.git("-C", str(sibling), "rev-parse", "--absolute-git-dir").strip()
        from_private_gitdir = workspace.shared_stash_inventory(private_gitdir)
        self.assertEqual(from_private_gitdir["state"], "observed")
        self.assertEqual([entry["commit"] for entry in from_private_gitdir["entries"]], [first])

    def test_unreadable_repositories_are_honest_unknowns(self):
        missing = workspace.shared_stash_inventory(str(self.directory / "not-a-repository"))
        self.assertEqual(missing["state"], "unknown")
        self.assertIsNone(missing["entries"])
        self.assertTrue(missing["reason"])
        plain = self.workdir("plain-directory")
        unreadable = workspace.shared_stash_inventory(str(plain))
        self.assertEqual(unreadable["state"], "unknown")
        self.assertIsNone(unreadable["entries"])

    def test_sha256_repositories_report_full_length_commits(self):
        repository = self.directory / "repo-sha256"
        repository.mkdir()
        completed = subprocess.run(
            ["git", "init", "-q", "-b", "main", "--object-format=sha256"],
            cwd=repository, env={**os.environ, **GIT_ENV}, capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        (repository / "file.txt").write_text("base\n")
        for arguments in (("config", "user.email", "b@example.invalid"), ("config", "user.name", "Buddy Test"),
                         ("add", "."), ("commit", "-qm", "base")):
            subprocess.run(["git", *arguments], cwd=repository, env={**os.environ, **GIT_ENV},
                           capture_output=True, text=True, check=True)
        (repository / "file.txt").write_text("stashed\n")
        subprocess.run(["git", "add", "file.txt"], cwd=repository, env={**os.environ, **GIT_ENV}, check=True)
        subprocess.run(["git", "stash", "push", "-q", "-m", "sha256 stash"], cwd=repository,
                       env={**os.environ, **GIT_ENV}, capture_output=True, text=True, check=True)
        inventory = workspace.shared_stash_inventory(str(repository))
        self.assertEqual(inventory["state"], "observed")
        [entry] = inventory["entries"]
        self.assertEqual(len(entry["commit"]), 64)
        self.assertTrue(all(character in "0123456789abcdef" for character in entry["commit"]))
        self.assertEqual(entry["description"], "On main: sha256 stash")

    def test_the_entry_list_is_bounded_and_marks_truncation(self):
        for number in range(workspace.STASH_ENTRY_LIMIT + 5):
            self.stash(f"flood {number}")
        inventory = workspace.shared_stash_inventory(str(self.repo))
        self.assertTrue(inventory["truncated"])
        self.assertEqual(inventory["totalCount"], workspace.STASH_ENTRY_LIMIT + 5)
        self.assertEqual(len(inventory["entries"]), workspace.STASH_ENTRY_LIMIT)

    def test_undecodable_description_bytes_stay_canonically_encodable(self):
        commit = self.stash("temporary")
        self.git("stash", "drop", "stash@{0}")
        completed = subprocess.run(
            [b"git", b"stash", b"store", b"-m", b"bad \xff\xfe bytes", commit.encode()],
            cwd=self.repo, env={**os.environ, **GIT_ENV}, capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        inventory = workspace.shared_stash_inventory(str(self.repo))
        self.assertEqual(inventory["state"], "observed")
        [recorded] = inventory["entries"]
        self.assertEqual(recorded["commit"], commit)
        self.assertIn("\ufffd", recorded["description"])
        # The frozen record must survive the board's canonical JSON and hashing.
        from hey_my_buddy.blackboard.store.db import canonical_json, sha256_text
        digest = sha256_text(canonical_json({"sharedStash": {"start": inventory}}))
        self.assertEqual(len(digest), 64)
        unchanged = workspace.shared_stash_comparison(inventory, inventory)
        self.assertEqual(unchanged["state"], "observed")
        self.assertFalse(unchanged["changed"])


class SharedStashComparisonTests(unittest.TestCase):
    @staticmethod
    def observed(entries, *, truncated=False):
        return {"version": 1, "repositoryPath": "repo", "state": "observed", "entries": entries,
                "truncated": truncated, "totalCount": len(entries)}

    @staticmethod
    def entry(commit: str, description: str):
        return {"commit": commit, "description": description}

    def test_unchanged_and_reordered_lists_stay_observed_and_unchanged(self):
        entries = [self.entry("a" * 40, "one"), self.entry("b" * 40, "two")]
        same = workspace.shared_stash_comparison(self.observed(entries), self.observed(entries))
        self.assertEqual(same["state"], "observed")
        self.assertFalse(same["changed"])
        reordered = workspace.shared_stash_comparison(self.observed(entries), self.observed(list(reversed(entries))))
        self.assertEqual(reordered["state"], "observed")
        self.assertFalse(reordered["changed"])

    def test_a_lost_entry_carries_its_recovery_command_and_unknown_cause(self):
        lost = self.entry("a" * 40, "user's staged work")
        comparison = workspace.shared_stash_comparison(self.observed([lost]), self.observed([]))
        self.assertEqual(comparison["state"], "observed")
        self.assertTrue(comparison["changed"])
        self.assertEqual(comparison["lostCount"], 1)
        [recorded] = comparison["lostEntries"]
        self.assertEqual(recorded["commit"], "a" * 40)
        self.assertEqual(recorded["description"], "user's staged work")
        self.assertEqual(recorded["cause"], "unknown")
        self.assertEqual(recorded["recoverCommand"], f"git stash store -m 'user'\\''s staged work' {'a' * 40}")
        self.assertIn("unknown", comparison["causeNote"])
        self.assertIn("another session", comparison["causeNote"])

    def test_entries_are_compared_by_commit_and_description_together(self):
        same_commit = "a" * 40
        start = self.observed([self.entry(same_commit, "first description")])
        # Same commit, different description: a different entry, so the start
        # entry is lost and the seal entry is new.
        seal = self.observed([self.entry(same_commit, "second description")])
        comparison = workspace.shared_stash_comparison(start, seal)
        self.assertEqual(comparison["state"], "observed")
        self.assertTrue(comparison["changed"])
        self.assertEqual(comparison["lostEntries"][0]["description"], "first description")
        self.assertEqual(comparison["newEntries"][0]["description"], "second description")
        # An identical re-registration of the same pair is not a change.
        duplicate = self.observed([self.entry(same_commit, "first description")])
        identical = workspace.shared_stash_comparison(start, duplicate)
        self.assertEqual(identical["state"], "observed")
        self.assertFalse(identical["changed"])
        # Duplicates count with multiplicity.
        twice = self.observed([self.entry(same_commit, "first description")] * 2)
        losing = workspace.shared_stash_comparison(twice, start)
        self.assertEqual(losing["lostCount"], 1)

    def test_new_entries_are_reported_without_recovery_commands(self):
        comparison = workspace.shared_stash_comparison(
            self.observed([]), self.observed([self.entry("c" * 40, "made by another session")]))
        self.assertEqual(comparison["state"], "observed")
        self.assertTrue(comparison["changed"])
        [recorded] = comparison["newEntries"]
        self.assertEqual(recorded["commit"], "c" * 40)
        self.assertNotIn("recoverCommand", recorded)

    def test_an_unknown_side_keeps_the_comparison_unknown(self):
        unknown = workspace.stash_observation_unknown("repo", "git failed")
        observed = self.observed([self.entry("a" * 40, "one")])
        self.assertEqual(workspace.shared_stash_comparison(observed, unknown)["state"], "unknown")
        self.assertEqual(workspace.shared_stash_comparison(unknown, observed)["state"], "unknown")
        self.assertEqual(workspace.shared_stash_comparison(observed, None)["unknownSides"], ["seal"])
        self.assertEqual(workspace.shared_stash_comparison(None, observed)["unknownSides"], ["start"])


class TruncatedObservationHonestyTests(StashInventoryTestCase):
    """A bounded observation cannot prove anything about entries beyond its cut."""

    def test_adding_the_65th_stash_never_reports_the_unchanged_oldest_entry_lost(self):
        self.stash("first")
        for number in range(63):
            self.stash(f"entry-{number}")
        before = workspace.shared_stash_inventory(str(self.repo))
        self.assertEqual(before["totalCount"], 64)
        self.assertFalse(before["truncated"])
        newest = self.stash("new-65th-entry")
        after = workspace.shared_stash_inventory(str(self.repo))
        self.assertEqual(after["totalCount"], 65)
        self.assertTrue(after["truncated"])
        comparison = workspace.shared_stash_comparison(before, after)
        # The seal side was truncated, so disappearance is unprovable: the entry
        # that fell off the observed window still exists in the real shared
        # stash, and no loss and no recovery command may be fabricated for it.
        self.assertEqual(comparison["state"], "partial")
        self.assertEqual(comparison["lostCount"], 0)
        self.assertEqual(comparison["lostEntries"], [])
        self.assertNotIn("causeNote", comparison)
        self.assertEqual(comparison["truncatedSides"], ["seal"])
        self.assertEqual(comparison["unprovenDirections"], ["lost"])
        # Appearance stays provable: the start side was read completely.
        self.assertEqual(comparison["newCount"], 1)
        self.assertEqual(comparison["newEntries"][0]["commit"], newest)

    def test_a_truncated_start_side_cannot_claim_new_entries_but_keeps_provable_losses(self):
        for number in range(65):
            self.stash(f"entry-{number}")
        before = workspace.shared_stash_inventory(str(self.repo))
        self.assertTrue(before["truncated"])
        dropped = self.git("rev-parse", "stash@{0}").strip()
        self.git("stash", "drop", "stash@{0}")
        after = workspace.shared_stash_inventory(str(self.repo))
        self.assertFalse(after["truncated"])
        comparison = workspace.shared_stash_comparison(before, after)
        self.assertEqual(comparison["state"], "partial")
        # Appearance is unprovable: an entry beyond the start cut may have
        # existed before the turn and only re-entered the observed window.
        self.assertEqual(comparison["newCount"], 0)
        self.assertEqual(comparison["newEntries"], [])
        self.assertEqual(comparison["truncatedSides"], ["start"])
        self.assertEqual(comparison["unprovenDirections"], ["new"])
        # Disappearance stays provable: the seal side was read completely, and
        # the dropped entry really is gone from the whole shared stash.
        self.assertEqual(comparison["lostCount"], 1)
        self.assertEqual(comparison["lostEntries"][0]["commit"], dropped)
        self.assertIn("causeNote", comparison)


class StashChangesNeverGateWorkTests(StashInventoryTestCase):
    def test_stash_changes_never_break_prepare_verify_or_seal(self):
        self.stash("user's staged work")
        manifest = self.manifest_for("stash-facts")
        self.assertTrue(workspace.verify(manifest, require_unchanged=True)["unchanged"])
        # The user drops their stash while the workspace is alive: the shared
        # reflog changes, but no managed file changed, so verification and the
        # seal are untouched — a stash change is never a workspace conflict.
        self.git("stash", "drop", "stash@{0}")
        self.assertTrue(workspace.verify(manifest, require_unchanged=True)["unchanged"])
        seal = workspace.seal(self.directory, manifest, "task-stash-facts", "attempt-stash-facts")
        self.assertTrue(seal["snapshotSha256"])
        # And a newly created stash is equally outside the managed proof.
        self.stash("a later stash")
        self.assertTrue(workspace.verify(manifest, require_unchanged=True)["unchanged"])

    def test_the_recorded_recovery_command_restores_a_dropped_entry(self):
        commit = self.stash("recoverable work")
        start = workspace.shared_stash_inventory(str(self.repo))
        self.git("stash", "drop", "stash@{0}")
        seal = workspace.shared_stash_inventory(str(self.repo))
        comparison = workspace.shared_stash_comparison(start, seal)
        [lost] = comparison["lostEntries"]
        self.assertEqual(lost["commit"], commit)
        restored = subprocess.run(["sh", "-c", lost["recoverCommand"]], cwd=self.repo,
                                  env={**os.environ, **GIT_ENV}, capture_output=True, text=True)
        self.assertEqual(restored.returncode, 0, restored.stderr)
        listing = self.git("stash", "list")
        self.assertIn(commit, self.git("rev-parse", "stash@{0}"))
        self.assertIn("recoverable work", listing)


class RealBoardClaimStashTests(StashInventoryTestCase):
    """The real module through the real board: one governed turn's stash facts.

    The full governed path runs on a real private repository and real Git —
    submit, claim, the seal-side import and the fixed get projection — exactly
    like a lifecycle turn, with only the executed model replaced by the record
    fixture. The repository and its stashes belong to this test alone.
    """

    def setUp(self) -> None:
        super().setUp()
        self.controls: dict[str, dict] = {}
        self.worker = "w-stash"
        from hey_my_buddy.blackboard.tasks import workflow as workflow_module
        self._previous_workspace = workflow_module._workspace_module
        workflow_module._workspace_module = workspace
        self.addCleanup(self._restore_workspace)

    def _restore_workspace(self) -> None:
        from hey_my_buddy.blackboard.tasks import workflow as workflow_module
        workflow_module._workspace_module = self._previous_workspace

    def register(self, board):
        return board.call("worker_register", {"workerId": self.worker, "capabilities": ["dsh"]})

    def turn_record(self, claim):
        from protocol.fixtures import native_turn
        outcome = {"disposition": "completed", "summary": "did the work", "remaining": [], "decisions": [],
                   "artifacts": [], "request": None}
        return native_turn.claim_record(claim, outcome=outcome)

    def test_claim_and_result_freeze_real_shared_stash_facts_end_to_end(self):
        import shlex
        board = self.board()
        self.register(board)
        queued_only = self.stash("queued only")
        submitted = board.call("workflow_submit", {
            "adapter": "dsh", "provider": "deepseek-official", "model": "deepseek-flash", "effort": "off",
            "requestId": "req-real-stash", "hostId": "host-1", "task": "real shared stash facts",
            "cwd": str(self.repo), "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["."]},
        })
        run_id = submitted["runId"]
        self.controls[run_id] = submitted["control"]
        # The user drops their stash while the task merely sits queued: the turn
        # that eventually starts must freeze its own starting state.
        self.git("stash", "drop", "stash@{0}")
        oid = self.stash("during turn ✓ 'quote'")
        description = workspace.shared_stash_inventory(str(self.repo))["entries"][0]["description"]
        # An identical re-registration beside a separating entry leaves two
        # indistinguishable stack positions of the same commit.
        self.stash("separating entry")
        self.git("stash", "store", "-m", description, oid)
        self.git("stash", "drop", "stash@{1}")
        claimed = board.call("worker_claim", {
            "workerId": self.worker, "claimRequestId": "claim-real-stash", "nonce": "n" * 16, "runId": run_id,
        })
        claim = claimed["claim"]
        manifest = claim["turn"]["input"]["executionWorkspace"]
        frozen = claim["turn"]["input"]["context"]["sharedStash"]
        self.assertEqual(frozen["start"]["state"], "observed")
        self.assertEqual(frozen["repositoryPath"], manifest["snapshot"]["repositoryPath"])
        # Identical duplicate entries are distinct multiset facts.
        self.assertEqual([entry["commit"] for entry in frozen["start"]["entries"]], [oid, oid])
        self.assertNotIn(queued_only, [entry["commit"] for entry in frozen["start"]["entries"]])
        # The turn drops one of the two identical entries; the fixed record names
        # exactly that loss with an executable recovery command.
        self.git("stash", "drop", "stash@{0}")
        refs_before = self.git("for-each-ref", "--format=%(refname) %(objectname)",
                               "refs/heads", "refs/tags", "refs/stash")
        seal = workspace.seal(self.directory, manifest, run_id, claim["attempt"]["attemptId"])
        result = {
            "status": "ok", "processState": {"shutdownConfirmed": True},
            "turn": self.turn_record(claim), "turnResultPath": "/tmp/turn.json", "workspaceSeal": seal,
        }
        board.call("worker_result", {
            "workerId": self.worker, "attemptId": claim["attempt"]["attemptId"],
            "generation": claim["attempt"]["generation"], "nonce": "n" * 16,
            "status": "ok", "result": result, "shutdownConfirmed": True, "exitCode": 0,
        })
        view = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(view["state"], "delivered")
        facts = next(row["sharedStash"] for row in view["artifacts"] if row["kind"] == "shared-refs")
        comparison = facts["comparison"]
        self.assertEqual(comparison["state"], "observed")
        self.assertTrue(comparison["changed"])
        self.assertEqual(comparison["lostCount"], 1)
        [lost] = comparison["lostEntries"]
        self.assertEqual(lost["commit"], oid)
        self.assertEqual(lost["description"], description)
        self.assertEqual(lost["cause"], "unknown")
        self.assertIn("unknown", comparison["causeNote"])
        # The pinned record's digest is recomputable from its stored contents,
        # and the executed workspace manifest keeps its own separate digest.
        with board.store.db.read() as connection:
            pinned = json.loads(connection.execute(
                "SELECT manifest_json FROM workflow_artifacts WHERE run_id=? AND kind='shared-refs'",
                (run_id,)).fetchone()[0])
        from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
        self.assertEqual(pinned["workspaceManifestSha256"], manifest["manifestSha256"])
        self.assertEqual(WorkflowCoordinator.verify_shared_refs_record(pinned), pinned)
        # The board touched no branch, tag or stash ref, and the recorded command
        # restores the dropped entry in the real repository.
        self.assertEqual(self.git("stash", "list", "--format=%H").splitlines(), [oid])
        self.assertEqual(self.git("for-each-ref", "--format=%(refname) %(objectname)",
                                  "refs/heads", "refs/tags", "refs/stash"), refs_before)
        command = shlex.split(lost["recoverCommand"])
        self.assertEqual(command[:3], ["git", "stash", "store"])
        # Re-storing an entry whose identical twin is already at the stack top is
        # Git's own safe no-op: the command succeeds and changes nothing.
        subprocess.run(command, cwd=self.repo, check=True, capture_output=True,
                       env={**os.environ, **GIT_ENV})
        self.assertEqual(self.git("stash", "list", "--format=%H").splitlines(), [oid])
        # From an emptied stack the same recorded command restores the entry.
        self.git("stash", "drop", "stash@{0}")
        self.assertEqual(self.git("stash", "list"), "")
        subprocess.run(command, cwd=self.repo, check=True, capture_output=True,
                       env={**os.environ, **GIT_ENV})
        self.assertEqual(self.git("stash", "list", "--format=%H").splitlines(), [oid])
        self.assertIn("during turn", self.git("stash", "list"))
