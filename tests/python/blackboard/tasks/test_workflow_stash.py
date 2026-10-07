"""The governed turn's shared-stash facts through the in-process board double.

The workspace module is the mock double (the Git read is injected), so these
tests pin the board's own contract: the start observation is collected outside
every write transaction and frozen into the turn input of exactly that turn,
the seal observation is collected outside the result transaction and pinned as
a fixed artifact with the full-entry comparison, and both are projected to the
Host through the fixed get/result/artifact surfaces. A reading that fails is an
honest unknown; a stash change never fails or blocks anything.
"""
from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from unittest import mock

from mock_workspace import MockWorkspace
from support import BoardTestCase

from hey_my_buddy.blackboard.store.db import canonical_json, sha256_text
from hey_my_buddy.blackboard.tasks import workflow as workflow_module
from hey_my_buddy.errors import BoardError

NONCE = "n" * 16
CONFIGURATION = {"adapter": "dsh", "provider": "deepseek-official", "model": "deepseek-flash", "effort": "off"}


def observed(entries):
    return {"version": 1, "repositoryPath": "unused", "state": "observed", "entries": entries,
            "truncated": False, "totalCount": len(entries)}


def entry(commit: str, description: str):
    return {"commit": commit, "description": description}


class WorkflowStashTestCase(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.workspace = MockWorkspace(self.directory)
        self._previous_workspace = workflow_module._workspace_module
        workflow_module._workspace_module = self.workspace
        self.addCleanup(self._restore_workspace)
        self.catalog_validation = self.enterContext(mock.patch(
            "hey_my_buddy.blackboard.catalog.catalog.validate_configuration", create=True,
            side_effect=lambda configuration, **_: dict(configuration)))

    def _restore_workspace(self) -> None:
        workflow_module._workspace_module = self._previous_workspace

    def repo_key(self) -> str:
        """The mock manifest's shared repository path for this case's source cwd."""
        return "mock+repo:" + str(self.workdir())

    def repository_path(self, board) -> str:
        with board.store.db.read() as connection:
            manifest = json.loads(connection.execute(
                "SELECT workspace_manifest_json FROM workflow_runs WHERE run_id=?", (self.run_id,)
            ).fetchone()[0])
        return manifest["snapshot"]["repositoryPath"]

    def submit_and_claim(self, board, *, request_id="req-1"):
        submitted = board.call("workflow_submit", {
            **CONFIGURATION, "requestId": request_id, "hostId": "host-1", "task": "stash facts task",
            "cwd": str(self.workdir()), "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "existing", "access": "write", "writeScope": ["."]},
        })
        self.run_id = submitted["runId"]
        board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh"]})
        return board.call("worker_claim", {
            "workerId": "w1", "claimRequestId": "claim-1", "nonce": NONCE, "runId": self.run_id,
        })

    def finish(self, board, claimed, *, disposition="completed", result_extra=None):
        claim = claimed["claim"]
        attempt = claim["attempt"]
        outcome = {"disposition": disposition, "summary": "did the work", "remaining": [], "decisions": [],
                   "artifacts": [], "request": None}
        from protocol.fixtures import native_turn
        record = native_turn.claim_record(claim, outcome=outcome, session_id="sess-1")
        result = {"status": "ok", "processState": {"shutdownConfirmed": True}, "turn": record,
                  "turnResultPath": "/tmp/turn.json"}
        manifest = claim["turn"]["input"]["executionWorkspace"]
        result["workspaceSeal"] = self.workspace.seal(
            self.directory, manifest, attempt["taskId"], attempt["attemptId"])
        if result_extra:
            result.update(result_extra)
        return board.call("worker_result", {
            "workerId": "w1", "attemptId": attempt["attemptId"], "generation": attempt["generation"],
            "nonce": NONCE, "status": "ok", "result": result, "shutdownConfirmed": True, "exitCode": 0,
        })

    def stored_turn_input(self, board):
        with board.store.db.read() as connection:
            row = connection.execute(
                "SELECT input_json FROM workflow_turns WHERE run_id=? ORDER BY turn_index DESC LIMIT 1",
                (self.run_id,)).fetchone()
        return json.loads(row[0])


class StartFactFreezingTests(WorkflowStashTestCase):
    def test_the_start_observation_is_frozen_into_the_turn_input_outside_write_transactions(self):
        board = self.board()
        repository = self.repo_key()
        inventory = observed([entry("a" * 40, "user's staged work")])
        write_depth = {"active": False}
        real_write = type(board.store.db).write
        read_state = []

        def tracking_write(self_db, immediate=True):
            @contextmanager
            def tracked():
                write_depth["active"] = True
                try:
                    with real_write(self_db, immediate) as connection:
                        yield connection
                finally:
                    write_depth["active"] = False
            return tracked()

        real_inventory = MockWorkspace.shared_stash_inventory

        def tracking_inventory(double, repository_path):
            # The Git read must happen while no SQLite write transaction is held.
            read_state.append(write_depth["active"])
            return real_inventory(double, repository_path)

        with mock.patch.object(type(board.store.db), "write", tracking_write), \
                mock.patch.object(MockWorkspace, "shared_stash_inventory", tracking_inventory):
            self.workspace.stash_inventories[repository] = inventory
            claimed = self.submit_and_claim(board)
        self.assertIsNotNone(claimed["claim"])
        self.assertFalse(write_depth["active"])
        self.assertEqual(read_state, [False], "the stash read must run outside the write transaction")
        self.assertEqual(self.workspace.stash_inventory_calls, [self.repository_path(board)])
        document = self.stored_turn_input(board)
        frozen = document["context"]["sharedStash"]
        self.assertEqual(frozen["repositoryPath"], self.repository_path(board))
        self.assertEqual(frozen["manifestSha256"], document["executionWorkspace"]["manifestSha256"])
        self.assertEqual(frozen["start"]["entries"], inventory["entries"])
        # The frozen fact is covered by the turn input hash itself.
        self.assertEqual(sha256_text(canonical_json(document)), claimed["claim"]["turn"]["inputSha256"])

    def test_changes_while_queued_are_part_of_the_start_fact_not_losses(self):
        board = self.board()
        repository = self.repo_key()
        self.workspace.stash_inventories[repository] = observed([entry("a" * 40, "queued-time entry")])
        submitted = board.call("workflow_submit", {
            **CONFIGURATION, "requestId": "req-1", "hostId": "host-1", "task": "queued change task",
            "cwd": str(self.workdir()), "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "existing", "access": "write", "writeScope": ["."]},
        })
        self.run_id = submitted["runId"]
        # The stash disappears while the task merely sits queued: the turn that
        # eventually starts must freeze the state at its own start, so the entry
        # never counts as disappearing during that turn.
        self.workspace.stash_inventories[repository] = observed([])
        board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh"]})
        claimed = board.call("worker_claim", {
            "workerId": "w1", "claimRequestId": "claim-1", "nonce": NONCE, "runId": self.run_id,
        })
        document = self.stored_turn_input(board)
        self.assertEqual(document["context"]["sharedStash"]["start"]["entries"], [])
        finished = self.finish(board, claimed)
        comparison = finished["workflow"]["sharedStash"]["comparison"]
        self.assertEqual(comparison["state"], "observed")
        self.assertFalse(comparison["changed"])

    def test_a_committed_claim_replay_keeps_the_frozen_start_fact(self):
        board = self.board()
        repository = self.repo_key()
        self.workspace.stash_inventories[repository] = observed([entry("a" * 40, "at claim time")])
        claimed = self.submit_and_claim(board)
        frozen = self.stored_turn_input(board)["context"]["sharedStash"]
        # The repository changes afterwards; a replay of the same committed claim
        # must return the stored turn verbatim, never a re-collected reading.
        self.workspace.stash_inventories[repository] = observed([entry("b" * 40, "later reading")])
        replayed = board.call("worker_claim", {
            "workerId": "w1", "claimRequestId": "claim-1", "nonce": NONCE, "runId": self.run_id,
        })
        self.assertTrue(replayed["replayed"])
        self.assertEqual(replayed["claim"]["turn"]["inputSha256"], claimed["claim"]["turn"]["inputSha256"])
        self.assertEqual(replayed["claim"]["turn"]["input"]["context"]["sharedStash"], frozen)

    def test_a_failed_collection_is_an_honest_unknown_and_never_blocks_the_claim(self):
        board = self.board()
        self.workspace.fail_stash_inventory = True
        claimed = self.submit_and_claim(board)
        self.assertIsNotNone(claimed["claim"])
        document = self.stored_turn_input(board)
        start = document["context"]["sharedStash"]["start"]
        self.assertEqual(start["state"], "unknown")
        self.assertIsNone(start["entries"])
        self.assertIn("injected stash read failure", start["reason"])
        self.workspace.fail_stash_inventory = False
        self.workspace.stash_inventories[self.repo_key()] = observed([])
        finished = self.finish(board, claimed)
        summary = finished["workflow"]["sharedStash"]
        self.assertEqual(summary["comparison"]["state"], "unknown")
        self.assertEqual(summary["comparison"]["unknownSides"], ["start"])


class SealFactPinningTests(WorkflowStashTestCase):
    def test_lost_entries_reach_the_host_through_every_fixed_surface(self):
        board = self.board()
        repository = self.repo_key()
        self.workspace.stash_inventories[repository] = observed([entry("a" * 40, "user's staged work")])
        claimed = self.submit_and_claim(board)
        # Between start and seal the entry disappears.
        self.workspace.stash_inventories[repository] = observed([])
        finished = self.finish(board, claimed)
        # The result response carries the bounded summary.
        summary = finished["workflow"]["sharedStash"]
        self.assertEqual(summary["comparison"]["state"], "observed")
        self.assertTrue(summary["comparison"]["changed"])
        [lost] = summary["comparison"]["lostEntries"]
        self.assertEqual(lost["commit"], "a" * 40)
        self.assertEqual(lost["recoverCommand"], f"git stash store -m 'user'\\''s staged work' {'a' * 40}")
        self.assertEqual(lost["cause"], "unknown")
        self.assertIn("unknown", summary["comparison"]["causeNote"])
        self.assertIn("another session", summary["comparison"]["causeNote"])
        # The fixed get view carries the start fact on the turn and the whole
        # comparison on the pinned shared-refs artifact.
        view = board.call("workflow_get", {"runId": self.run_id})
        [turn] = view["turns"]
        self.assertEqual(turn["sharedStash"]["start"]["entryCount"], 1)
        self.assertEqual(view["currentTurn"]["sharedStash"]["start"]["entryCount"], 1)
        artifact = next(row for row in view["artifacts"] if row["kind"] == "shared-refs")
        self.assertEqual(artifact["sharedStash"]["comparison"]["state"], "observed")
        self.assertTrue(artifact["sharedStash"]["comparison"]["changed"])
        self.assertEqual(artifact["sharedStash"]["comparison"]["lostEntries"][0]["commit"], "a" * 40)
        self.assertEqual(artifact["sharedStash"]["seal"]["state"], "observed")
        audit = board.call("workflow_get", {"runId": self.run_id, "includeAudit": True})
        [recorded] = [row for row in audit["artifacts"] if row["kind"] == "shared-refs"]
        with board.store.db.read() as connection:
            pinned = json.loads(connection.execute(
                "SELECT manifest_json FROM workflow_artifacts WHERE artifact_id=?", (recorded["artifactId"],)
            ).fetchone()[0])
        self.assertEqual(pinned["startStash"]["entries"][0]["commit"], "a" * 40)
        self.assertEqual(pinned["sealStash"]["entries"], [])
        self.assertEqual(pinned["comparison"]["lostCount"], 1)
        # The pinned digest is recomputable from the stored payload alone.
        from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
        self.assertEqual(pinned["manifestSha256"], WorkflowCoordinator.shared_refs_record_digest(pinned))
        self.assertEqual(WorkflowCoordinator.verify_shared_refs_record(pinned), pinned)
        self.assertEqual(pinned["turnId"], claimed["claim"]["turn"]["turnId"])
        self.assertEqual(pinned["attemptId"], claimed["claim"]["attempt"]["attemptId"])
        self.assertEqual(pinned["turnInputSha256"], claimed["claim"]["turn"]["inputSha256"])

    def test_a_tampered_pinned_record_fails_verification(self):
        from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
        from hey_my_buddy.errors import BoardError
        board = self.board()
        repository = self.repo_key()
        self.workspace.stash_inventories[repository] = observed([entry("a" * 40, "original description")])
        claimed = self.submit_and_claim(board)
        self.workspace.stash_inventories[repository] = observed([])
        self.finish(board, claimed)
        with board.store.db.read() as connection:
            row = connection.execute(
                "SELECT manifest_json FROM workflow_artifacts WHERE run_id=? AND kind='shared-refs'",
                (self.run_id,)).fetchone()
        pinned = json.loads(row[0])
        self.assertEqual(WorkflowCoordinator.verify_shared_refs_record(pinned), pinned)
        # Any later drift between the stored payload and its pinned digest —
        # here a rewritten description — is refused as changed evidence.
        pinned["startStash"]["entries"][0]["description"] = "rewritten history"
        with self.assertRaises(BoardError) as raised:
            WorkflowCoordinator.verify_shared_refs_record(pinned)
        self.assertEqual(raised.exception.code, "WORKSPACE_MANIFEST_CHANGED")
        broken = dict(pinned)
        broken["manifestSha256"] = "0" * 64
        with self.assertRaises(BoardError) as shape:
            WorkflowCoordinator.verify_shared_refs_record(broken)
        self.assertEqual(shape.exception.code, "WORKSPACE_MANIFEST_CHANGED")
        with self.assertRaises(BoardError):
            WorkflowCoordinator.verify_shared_refs_record({"version": 2})

    def test_pinning_is_identity_stable_for_the_same_turn(self):
        board = self.board()
        repository = self.repo_key()
        self.workspace.stash_inventories[repository] = observed([entry("a" * 40, "stable")])
        claimed = self.submit_and_claim(board)
        self.finish(board, claimed)
        with board.store.db.read() as connection:
            rows = connection.execute(
                "SELECT manifest_json, manifest_sha256 FROM workflow_artifacts"
                " WHERE run_id=? AND kind='shared-refs'", (self.run_id,)).fetchall()
        self.assertEqual(len(rows), 1)
        pinned = json.loads(rows[0][0])
        self.assertEqual(rows[0][1], pinned["manifestSha256"])
        # Re-running the pin with the same frozen facts yields the same digest.
        coordinator = board.store.workflow
        with board.store.db.read() as connection:
            run_row = coordinator._run_row(connection, self.run_id)
            turn_row = connection.execute(
                "SELECT * FROM workflow_turns WHERE run_id=?", (self.run_id,)).fetchone()
            attempt = connection.execute(
                "SELECT * FROM attempts WHERE attempt_id=?", (turn_row["attempt_id"],)).fetchone()
            repinned = coordinator._pin_shared_refs(
                connection, run_row, attempt, turn_row,
                {"repositoryPath": pinned["repositoryPath"],
                 "inventory": self.workspace.shared_stash_inventory(pinned["repositoryPath"])},
                "2026-01-01T00:00:00.000Z")
        self.assertEqual(repinned["manifestSha256"], pinned["manifestSha256"])

    def test_new_entries_from_another_session_are_reported_without_failing_anything(self):
        board = self.board()
        repository = self.repo_key()
        self.workspace.stash_inventories[repository] = observed([])
        claimed = self.submit_and_claim(board)
        self.workspace.stash_inventories[repository] = observed([entry("c" * 64, "made elsewhere")])
        finished = self.finish(board, claimed)
        summary = finished["workflow"]["sharedStash"]
        self.assertEqual(summary["comparison"]["state"], "observed")
        self.assertTrue(summary["comparison"]["changed"])
        self.assertEqual(summary["comparison"]["newCount"], 1)
        self.assertEqual(summary["comparison"]["lostCount"], 0)
        self.assertEqual(finished["workflow"]["state"], "delivered")

    def test_a_failed_seal_collection_is_an_honest_unknown(self):
        board = self.board()
        repository = self.repo_key()
        self.workspace.stash_inventories[repository] = observed([entry("a" * 40, "kept entry")])
        claimed = self.submit_and_claim(board)
        self.workspace.fail_stash_inventory = True
        finished = self.finish(board, claimed)
        self.workspace.fail_stash_inventory = False
        summary = finished["workflow"]["sharedStash"]
        self.assertEqual(summary["seal"]["state"], "unknown")
        self.assertEqual(summary["comparison"]["state"], "unknown")
        self.assertEqual(summary["comparison"]["unknownSides"], ["seal"])
        view = board.call("workflow_get", {"runId": self.run_id})
        artifact = next(row for row in view["artifacts"] if row["kind"] == "shared-refs")
        self.assertEqual(artifact["sharedStash"]["seal"]["state"], "unknown")

    def test_a_failed_import_still_freezes_the_facts(self):
        board = self.board()
        repository = self.repo_key()
        self.workspace.stash_inventories[repository] = observed([entry("a" * 40, "entry of a failed turn")])
        claimed = self.submit_and_claim(board)
        claim = claimed["claim"]
        # An invalid turn record forces the honest failed import path.
        from protocol.fixtures import native_turn
        record = native_turn.claim_record(claim, outcome={
            "disposition": "completed", "summary": "broken", "remaining": [], "decisions": [], "artifacts": [],
            "request": None}, session_id="sess-1")
        record["inputSha256"] = "0" * 64
        manifest = claim["turn"]["input"]["executionWorkspace"]
        result = {"status": "ok", "processState": {"shutdownConfirmed": True}, "turn": record,
                  "turnResultPath": "/tmp/turn.json",
                  "workspaceSeal": self.workspace.seal(self.directory, manifest, self.run_id,
                                                      claim["attempt"]["attemptId"])}
        finished = board.call("worker_result", {
            "workerId": "w1", "attemptId": claim["attempt"]["attemptId"], "generation": claim["attempt"]["generation"],
            "nonce": NONCE, "status": "ok", "result": result, "shutdownConfirmed": True, "exitCode": 0,
        })
        self.assertEqual(finished["taskState"], "failed")
        self.assertIsNotNone(finished["workflow"]["sharedStash"])
        view = board.call("workflow_get", {"runId": self.run_id})
        self.assertTrue(any(row["kind"] == "shared-refs" for row in view["artifacts"]))

    def test_turns_that_predate_the_freezing_pin_nothing(self):
        board = self.board()
        repository = self.repo_key()
        self.workspace.stash_inventories[repository] = observed([])
        claimed = self.submit_and_claim(board)
        # A turn input written before this fact existed has no start observation;
        # none is invented for it from a later reading.
        with board.store.db.write() as connection:
            document = self.stored_turn_input(board)
            document["context"].pop("sharedStash", None)
            connection.execute(
                "UPDATE workflow_turns SET input_json=? WHERE run_id=?",
                (canonical_json(document), self.run_id),
            )
        finished = self.finish(board, claimed)
        self.assertIsNone(finished["workflow"]["sharedStash"])
        view = board.call("workflow_get", {"runId": self.run_id})
        self.assertFalse(any(row["kind"] == "shared-refs" for row in view["artifacts"]))
        self.assertIsNone(view["turns"][0]["sharedStash"])

    def test_a_released_attempt_still_pins_its_seal_side_fact(self):
        board = self.board()
        repository = self.repo_key()
        self.workspace.stash_inventories[repository] = observed([])
        claimed = self.submit_and_claim(board)
        attempt = claimed["claim"]["attempt"]
        released = board.call("worker_release", {
            "workerId": "w1", "attemptId": attempt["attemptId"], "generation": attempt["generation"],
            "nonce": NONCE, "evidence": {"spawnIntentWritten": False},
        })
        self.assertTrue(released["released"])
        view = board.call("workflow_get", {"runId": self.run_id})
        artifact = next(row for row in view["artifacts"] if row["kind"] == "shared-refs")
        self.assertEqual(artifact["sharedStash"]["seal"]["state"], "observed")
        self.assertEqual(artifact["sharedStash"]["comparison"]["state"], "observed")
        self.assertFalse(artifact["sharedStash"]["comparison"]["changed"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
