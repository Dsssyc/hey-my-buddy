"""The claim path's outside-transaction stash collection, through the board double.

The workspace module is the mock double, so these tests pin the claim-side
contract only: the shared-stash reading runs before the claim's write
transaction, reads only work a claim can actually admit, stays bounded per
call, and can never block or fail a claim. An empty board performs no Git read
at all. Every fact is a per-repository observation keyed by the manifest's own
repository path.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from mock_workspace import MockWorkspace
from support import BoardTestCase

from hey_my_buddy.blackboard.tasks import workflow as workflow_module

NONCE = "n" * 16
CONFIGURATION = {"adapter": "dsh", "provider": "deepseek-official", "model": "deepseek-flash", "effort": "off"}


def observed(entries):
    return {"version": 1, "repositoryPath": "unused", "state": "observed", "entries": entries,
            "truncated": False, "totalCount": len(entries)}


def entry(commit: str, description: str):
    return {"commit": commit, "description": description}


class ClaimStashTestCase(BoardTestCase):
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

    def submit(self, board, *, request_id, cwd, **extra):
        return board.call("workflow_submit", {
            **CONFIGURATION, "requestId": request_id, "hostId": "host-1", "task": "claim stash task",
            "cwd": str(cwd), "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "existing", "access": "write", "writeScope": ["."]},
            **extra,
        })

    def claim(self, board, *, claim_request_id="c1", run_id=None):
        params = {"workerId": "w1", "claimRequestId": claim_request_id, "nonce": NONCE}
        if run_id:
            params["runId"] = run_id
        return board.call("worker_claim", params)

    def finish_turn(self, board, claimed, *, worker_id="w1", nonce=NONCE, session_id="sess-1"):
        """A genuine synthetic governed turn: the real import path, no model."""
        from protocol.fixtures import native_turn
        claim = claimed["claim"]
        attempt = claim["attempt"]
        outcome = {"disposition": "completed", "summary": "did the work", "remaining": [], "decisions": [],
                   "artifacts": [], "request": None}
        manifest = claim["turn"]["input"]["executionWorkspace"]
        result = {"status": "ok", "processState": {"shutdownConfirmed": True},
                  "turn": native_turn.claim_record(claim, outcome=outcome, session_id=session_id),
                  "turnResultPath": "/tmp/turn.json",
                  "workspaceSeal": self.workspace.seal(self.directory, manifest, attempt["taskId"],
                                                       attempt["attemptId"])}
        return board.call("worker_result", {
            "workerId": worker_id, "attemptId": attempt["attemptId"], "generation": attempt["generation"],
            "nonce": nonce, "status": "ok", "result": result, "shutdownConfirmed": True, "exitCode": 0,
        })


class ClaimCollectionScopeTests(ClaimStashTestCase):
    def test_an_empty_board_performs_no_stash_read(self):
        board = self.board()
        board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh"]})
        response = self.claim(board)
        self.assertIsNone(response["claim"])
        self.assertEqual(self.workspace.stash_inventory_calls, [])

    def test_collection_mirrors_the_claim_candidates_even_when_blocked(self):
        board = self.board()
        repository = "mock+repo:" + str(self.workdir("blocked"))
        self.workspace.stash_inventories[repository] = observed([entry("d" * 40, "still a candidate")])
        submitted = self.submit(board, request_id="req-1", cwd=self.workdir("blocked"))
        with board.store.db.write() as connection:
            connection.execute(
                "UPDATE tasks SET queue_reason='awaiting-host' WHERE task_id=?", (submitted["runId"],)
            )
        board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh"]})
        claimed = self.claim(board)
        # The collection follows the claim's own candidate window — not a queue
        # reason heuristic — so a run the claim can admit is read even while it
        # carries a blocking reason, and the frozen fact is the real reading.
        self.assertIsNotNone(claimed["claim"])
        self.assertEqual(self.workspace.stash_inventory_calls, [repository])
        start = claimed["claim"]["turn"]["input"]["context"]["sharedStash"]["start"]
        self.assertEqual(start["state"], "observed")
        self.assertEqual(start["entries"][0]["commit"], "d" * 40)

    def test_claimable_work_reads_its_repository_before_the_claim_commits(self):
        board = self.board()
        submitted = self.submit(board, request_id="req-1", cwd=self.workdir("ready"))
        repository = "mock+repo:" + str(self.workdir("ready"))
        self.workspace.stash_inventories[repository] = observed([entry("a" * 40, "ready entry")])
        board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh"]})
        claimed = self.claim(board, run_id=submitted["runId"])
        self.assertIsNotNone(claimed["claim"])
        self.assertEqual(self.workspace.stash_inventory_calls, [repository])
        document = claimed["claim"]["turn"]["input"]
        self.assertEqual(document["context"]["sharedStash"]["start"]["entries"][0]["commit"], "a" * 40)

    def test_one_claim_reads_at_most_a_bounded_set_of_repositories(self):
        board = self.board()
        for number in range(workflow_module.WorkflowCoordinator._CLAIM_STASH_REPOSITORIES + 4):
            self.submit(board, request_id=f"req-{number}", cwd=self.workdir(f"repo-{number}"))
        board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh"]})
        response = self.claim(board)
        self.assertIsNotNone(response["claim"])
        self.assertLessEqual(len(self.workspace.stash_inventory_calls),
                             workflow_module.WorkflowCoordinator._CLAIM_STASH_REPOSITORIES)
        self.assertLessEqual(len(set(self.workspace.stash_inventory_calls)),
                             workflow_module.WorkflowCoordinator._CLAIM_STASH_REPOSITORIES)

    def test_a_miss_surviving_both_passes_defers_rather_than_starting_unobserved(self):
        board = self.board()
        submitted = self.submit(board, request_id="req-1", cwd=self.workdir("uncovered"))
        real_collect = workflow_module.WorkflowCoordinator.collect_claim_stash_facts

        def skipping_collect(coordinator, params, *, selector=None):
            real_collect(coordinator, params, selector=selector)
            # Simulate both collection passes having missed this repository.
            return {"repositories": {}}

        with mock.patch.object(workflow_module.WorkflowCoordinator, "collect_claim_stash_facts", skipping_collect):
            board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh"]})
            deferred = self.claim(board, run_id=submitted["runId"])
        # A coverage miss that survives the bounded retry never starts a turn
        # and never masquerades as a reading: the claim defers with its own
        # external reason, stores no receipt, and leaves the work queued.
        self.assertIsNone(deferred["claim"])
        self.assertEqual(deferred["reason"], "stash-observation-pending")
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM commands WHERE command_id='c1'").fetchone()[0], 0)
        # The next claim collects for real and starts from that observation.
        claimed = self.claim(board, claim_request_id="c2", run_id=submitted["runId"])
        self.assertIsNotNone(claimed["claim"])
        start = claimed["claim"]["turn"]["input"]["context"]["sharedStash"]["start"]
        self.assertEqual(start["state"], "unknown")
        self.assertEqual(start["reason"], "not-configured-in-double")

    def test_a_chosen_run_beyond_the_pass_bound_is_collected_by_the_coverage_retry(self):
        from hey_my_buddy.blackboard.tasks import workflow as workflow_module
        board = self.board()
        # Eight earlier candidates the worker cannot claim fill the per-pass
        # repository bound; the ninth, claimable run is the one the transaction
        # chooses, so the first pass misses its repository and the claim must
        # roll back and re-collect with that exact run as the selector.
        for number in range(workflow_module.WorkflowCoordinator._CLAIM_STASH_REPOSITORIES):
            self.submit(board, request_id=f"blocked-{number}", cwd=self.workdir(f"blocked-{number}"),
                        requiredCapabilities=["capability-this-worker-lacks"])
        target = self.submit(board, request_id="target", cwd=self.workdir("target"))
        repository = "mock+repo:" + str(self.workdir("target"))
        self.workspace.stash_inventories[repository] = observed([entry("e" * 40, "chosen run entry")])
        collect_calls = []
        real_collect = workflow_module.WorkflowCoordinator.collect_claim_stash_facts

        def tracking_collect(coordinator, params, *, selector=None):
            collect_calls.append(selector)
            return real_collect(coordinator, params, selector=selector)

        with mock.patch.object(workflow_module.WorkflowCoordinator, "collect_claim_stash_facts", tracking_collect):
            board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh"]})
            claimed = self.claim(board, claim_request_id="retry-claim")
        self.assertIsNotNone(claimed["claim"], claimed)
        self.assertEqual(claimed["claim"]["attempt"]["taskId"], target["runId"])
        # The first pass covered the candidate window without the chosen run's
        # repository, the rolled-back claim retried with that exact run as the
        # selector, and the frozen start fact is that real observation.
        self.assertEqual(collect_calls, [None, target["runId"]])
        self.assertIn(repository, self.workspace.stash_inventory_calls)
        start = claimed["claim"]["turn"]["input"]["context"]["sharedStash"]["start"]
        self.assertEqual(start["state"], "observed")
        self.assertEqual(start["entries"][0]["commit"], "e" * 40)
        # Exactly one attempt exists: the rolled-back round admitted nothing.
        with board.store.db.read() as connection:
            attempts = connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE task_id=?", (target["runId"],)).fetchone()[0]
        self.assertEqual(attempts, 1)

    def test_a_requeued_same_run_defers_when_the_targeted_pass_saw_it_running(self):
        """The full legal chain: another worker's claim/result, Host continue,
        standard preparation — then the restricted retry must still prove this
        claim's own observation of the repository, not just the run identity."""
        from hey_my_buddy.blackboard.tasks import workflow as workflow_module
        board = self.board()
        for number in range(workflow_module.WorkflowCoordinator._CLAIM_STASH_REPOSITORIES):
            self.submit(board, request_id=f"blocked-{number}", cwd=self.workdir(f"blocked-{number}"),
                        requiredCapabilities=["capability-this-worker-lacks"])
        target = self.submit(board, request_id="retry-same", cwd=self.workdir("same"))
        repository = "mock+repo:" + str(self.workdir("same"))
        self.workspace.stash_inventories[repository] = observed([entry("e" * 40, "same repo entry")])
        board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh"]})
        board.call("worker_register", {"workerId": "w-other", "capabilities": ["dsh"]})
        collect_calls = []
        injected = {"done": False}
        real_collect = workflow_module.WorkflowCoordinator.collect_claim_stash_facts

        def racing_collect(coordinator, params, *, selector=None):
            if params.get("workerId") != "w1":
                return real_collect(coordinator, params, selector=selector)
            collect_calls.append(selector)
            if selector is not None and not injected["done"]:
                injected["done"] = True
                # Another registered worker genuinely claims A first; while A is
                # held, the targeted selector query sees no queued run and no
                # repository key is observed at all.
                other = board.call("worker_claim", {
                    "workerId": "w-other", "claimRequestId": "other-claim", "nonce": "m" * 16,
                    "runId": target["runId"],
                })
                self.assertIsNotNone(other["claim"])
                facts = real_collect(coordinator, params, selector=selector)
                self.assertNotIn(repository, facts["repositories"])
                # The other worker finishes its genuine synthetic turn; the Host
                # explicitly continues A, and the standard continuation
                # preparation completes — all before this claim's transaction.
                self.finish_turn(board, other, worker_id="w-other", nonce="m" * 16, session_id="sess-other")
                view = board.call("workflow_get", {"runId": target["runId"]})
                self.assertEqual(view["state"], "delivered")
                board.call("workflow_continue", {
                    "runId": target["runId"], "commandId": "host-requeue", "expectedRevision": view["revision"],
                    "reason": "explicit private rejection and retry", "input": "repeat the fixture work",
                    **target["control"],
                })
                coordinator.prepare_continuation_workspace({"runId": target["runId"]})
                coordinator.prepare_dispatch({"runId": target["runId"]})
                return facts
            return real_collect(coordinator, params, selector=selector)

        with mock.patch.object(workflow_module.WorkflowCoordinator, "collect_claim_stash_facts",
                               racing_collect):
            response = self.claim(board, claim_request_id="same-drift")
        # The targeted pass observed no repository for A — identity alone is not
        # proof — so this claim admits nothing and stores no receipt; the same
        # identity being claimable again cannot start it unobserved.
        self.assertTrue(injected["done"])
        self.assertIsNone(response["claim"])
        self.assertEqual(response["reason"], "stash-observation-pending")
        self.assertEqual(collect_calls, [None, target["runId"]])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE execution_state NOT IN ('finished','released')"
            ).fetchone()[0], 0)
            # Other actors keep their own legitimate receipts; this claim stored
            # none, so a retry of its claimRequestId re-collects instead of
            # replaying an admitted attempt.
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM commands WHERE command_id='same-drift'"
            ).fetchone()[0], 0)
        # The next claim re-collects for the run it actually chooses: A sits
        # beyond the per-pass repository bound, so its own coverage retry
        # targets A — now queued — and A starts from that real observation.
        again = self.claim(board, claim_request_id="same-next")
        self.assertIsNotNone(again["claim"], again)
        self.assertEqual(again["claim"]["attempt"]["taskId"], target["runId"])
        start = again["claim"]["turn"]["input"]["context"]["sharedStash"]["start"]
        self.assertEqual(start["state"], "observed")
        self.assertEqual([item["commit"] for item in start["entries"]], ["e" * 40])

    def test_a_cancelled_retry_target_defers_instead_of_admitting_an_unobserved_run(self):
        from hey_my_buddy.blackboard.tasks import workflow as workflow_module
        board = self.board()
        for number in range(workflow_module.WorkflowCoordinator._CLAIM_STASH_REPOSITORIES):
            self.submit(board, request_id=f"blocked-{number}", cwd=self.workdir(f"blocked-{number}"),
                        requiredCapabilities=["capability-this-worker-lacks"])
        eligible_a = self.submit(board, request_id="eligible-a", cwd=self.workdir("eligible-a"))
        eligible_b = self.submit(board, request_id="eligible-b", cwd=self.workdir("eligible-b"))
        for key, commit in (("eligible-a", "a" * 40), ("eligible-b", "b" * 40)):
            self.workspace.stash_inventories["mock+repo:" + str(self.workdir(key))] = observed([entry(commit, key)])
        board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh"]})
        collect_calls = []
        real_collect = workflow_module.WorkflowCoordinator.collect_claim_stash_facts

        def cancelling_collect(coordinator, params, *, selector=None):
            collect_calls.append(selector)
            facts = real_collect(coordinator, params, selector=selector)
            if selector == eligible_a["runId"]:
                # A legal concurrent write takes the retry target away after its
                # repository was read and before the retry transaction runs.
                with coordinator.db.write() as connection:
                    connection.execute("UPDATE tasks SET state='cancelled' WHERE task_id=?",
                                       (eligible_a["runId"],))
            return facts

        with mock.patch.object(workflow_module.WorkflowCoordinator, "collect_claim_stash_facts",
                               cancelling_collect):
            response = self.claim(board, claim_request_id="drift-claim")
        # The retry is bounded and restricted: A vanished, B was never observed
        # by this claim, so nothing is admitted and no receipt blocks the retry.
        self.assertIsNone(response["claim"])
        self.assertEqual(collect_calls, [None, eligible_a["runId"]])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0], 0)
        # The next claim re-collects the candidate window; B sits beyond the
        # per-pass repository bound, so its own coverage retry targets B and B
        # starts from its own real observation, never a coverage miss.
        again = self.claim(board, claim_request_id="drift-next")
        self.assertIsNotNone(again["claim"], again)
        self.assertEqual(again["claim"]["attempt"]["taskId"], eligible_b["runId"])
        start = again["claim"]["turn"]["input"]["context"]["sharedStash"]["start"]
        self.assertEqual(start["state"], "observed")
        self.assertEqual([item["commit"] for item in start["entries"]], ["b" * 40])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)

    def test_the_collected_fact_is_bound_to_the_manifest_repository_of_the_claimed_run(self):
        board = self.board()
        first = self.submit(board, request_id="req-1", cwd=self.workdir("first"))
        second = self.submit(board, request_id="req-2", cwd=self.workdir("second"))
        repository_one = "mock+repo:" + str(self.workdir("first"))
        repository_two = "mock+repo:" + str(self.workdir("second"))
        self.workspace.stash_inventories[repository_one] = observed([entry("1" * 40, "first repo entry")])
        self.workspace.stash_inventories[repository_two] = observed([entry("2" * 40, "second repo entry")])
        board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh"]})
        claimed = self.claim(board, run_id=second["runId"])
        self.assertIsNotNone(claimed["claim"])
        frozen = claimed["claim"]["turn"]["input"]["context"]["sharedStash"]
        self.assertEqual(frozen["repositoryPath"], repository_two)
        self.assertEqual(frozen["start"]["entries"][0]["commit"], "2" * 40)
        with board.store.db.read() as connection:
            manifest = json.loads(connection.execute(
                "SELECT workspace_manifest_json FROM workflow_runs WHERE run_id=?", (second["runId"],)
            ).fetchone()[0])
        self.assertEqual(frozen["manifestSha256"], manifest["manifestSha256"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
