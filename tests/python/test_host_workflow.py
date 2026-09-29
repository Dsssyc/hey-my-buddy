"""Host controls are bound to immutable execution evidence and current ownership."""
import json

from buddy.errors import BoardError
from test_workflow import CONFIGURATION, WorkflowTestCase


class HostAdmissionTests(WorkflowTestCase):
    def test_objective_of_is_atomic_scoped_and_idempotent(self):
        board = self.board()
        first = self.submit(board, kind="worktree", objective={"title": "Host agenda"})
        second = self.submit(board, kind="worktree", request_id="second", objectiveOf=first["runId"])
        self.assertEqual(second["objectiveId"], first["objectiveId"])
        replay = self.submit(board, kind="worktree", request_id="second", objectiveOf=first["runId"])
        self.assertTrue(replay["duplicate"])
        with self.assertRaises(BoardError) as caught:
            self.submit(board, kind="worktree", request_id="foreign", host_id="another-host", objectiveOf=first["runId"])
        self.assertEqual(caught.exception.code, "CONFLICT")
        with self.assertRaises(BoardError):
            self.submit(board, kind="worktree", request_id="ambiguous", objectiveOf=first["runId"], objectiveId=first["objectiveId"])
        self.assertEqual(board.store.count_tasks(), 2)

    def test_failed_explicit_configuration_can_change_with_a_reason(self):
        board = self.board()
        submitted = self.submit(board)
        self.register(board)
        claimed = self.claim(board)
        self.finish_turn(board, claimed, runner_status="failed", exit_code=1, seal=False)
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(current["state"], "failed")
        changed = {**CONFIGURATION, "effort": "high"}
        continued = self.continue_run(board, current, configuration=changed)
        self.assertEqual(continued["executionConfiguration"], changed)
        self.assertFalse(continued["configurationLocked"])
        with board.store.db.read() as db:
            original = json.loads(db.execute("SELECT goal_json FROM workflow_runs").fetchone()[0])
            self.assertEqual(original["effort"], "off")
            event = db.execute("SELECT payload_json FROM events WHERE kind='workflow.configuration_overridden'").fetchone()
            self.assertEqual(json.loads(event[0])["configuration"], changed)

    def test_locked_configuration_is_part_of_identity_and_survives_failure(self):
        board = self.board()
        submitted = self.submit(board, configurationLocked=True)
        self.assertTrue(submitted["configurationLocked"])
        with self.assertRaises(BoardError) as caught:
            self.submit(board, configurationLocked=False)
        self.assertEqual(caught.exception.code, "CONFLICT")
        self.register(board)
        claimed = self.claim(board)
        self.finish_turn(board, claimed, runner_status="failed", exit_code=1, seal=False)
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        with self.assertRaises(BoardError) as caught:
            self.continue_run(board, current, configuration={**CONFIGURATION, "effort": "high"})
        self.assertEqual(caught.exception.code, "CONFIGURATION_CONFLICT")


class HostCompletionTests(WorkflowTestCase):
    def test_host_can_integrate_and_finish_an_attention_turn_without_another_execution(self):
        board = self.board()
        submitted = self.submit(board)
        self.register(board)
        claim = self.claim(board)
        self.finish_turn(board, claim, disposition="attention")
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        artifact = next(item for item in current["artifacts"] if item["kind"] == "output")
        params = {"runId": current["runId"], "artifactId": artifact["artifactId"],
                  "commandId": "host-completion", "note": "Host checked and integrated the sealed output",
                  "verdict": "accepted", **self.control(current)}
        with self.assertRaises(BoardError) as caught:
            board.call("workflow_acknowledge", params)
        self.assertEqual(caught.exception.code, "INTEGRATION_REQUIRED")
        self.record_integration(board, current, artifact_id=artifact["artifactId"])
        completed = board.call("workflow_acknowledge", params)
        self.assertEqual((completed["state"], completed["status"]), ("accepted", "completed"))
        self.assertEqual(completed["counts"]["turns"], 1)
        self.assertEqual(completed["counts"]["openRequests"], 0)
        self.assertEqual(completed["currentTurn"]["disposition"], "attention")
        self.assertTrue(board.call("workflow_acknowledge", params)["duplicate"])
        self.assertTrue(board.call("workflow_acknowledge", {**params, "commandId": "repeat"})["duplicate"])

    def test_failure_conclusion_preserves_failure_and_never_becomes_an_acceptance(self):
        board = self.board()
        submitted = self.submit(board)
        self.register(board)
        claim = self.claim(board)
        self.finish_turn(board, claim, runner_status="failed", exit_code=1, seal=False)
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        params = {"runId": current["runId"], "expectedRevision": current["revision"],
                  "commandId": "conclusion", "note": "Partial work retained; unfinished", "verdict": "recorded",
                  **self.control(current)}
        concluded = board.call("workflow_acknowledge", params)
        self.assertEqual((concluded["state"], concluded["status"]), ("failed", "failed"))
        self.assertIsNotNone(concluded["hostConclusion"])
        self.assertTrue(board.call("workflow_acknowledge", params)["duplicate"])
        with board.store.db.read() as db:
            self.assertIsNone(db.execute("SELECT accepted_at FROM tasks").fetchone()[0])
        continued = self.continue_run(board, concluded)
        self.assertIsNone(continued["hostConclusion"])
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_host_conclusions").fetchone()[0], 1)


from test_workspace_lifecycle import LifecycleTestCase


class HostConclusionCleanupTests(LifecycleTestCase):
    def test_integration_records_only_real_separate_host_paths(self):
        from test_workspace_lifecycle import git
        board = self.board()
        self.register(board)
        _submitted, _claim, _manifest, _checkout, _seal, current, artifact = self.run_worktree(board)
        target = self.target_with_artifact(artifact)
        (target["path"] / "host-note.txt").write_text("Host verification\n")
        git(target["path"], "add", "host-note.txt")
        git(target["path"], "commit", "-qm", "Host supplement")
        for paths in (["tracked.txt"], ["absent.txt"], ["../outside"]):
            with self.assertRaises(BoardError):
                self.integrate(board, current, artifact, target=target, hostPaths=paths, command_id="invalid-" + str(paths))
        integrated = self.integrate(board, current, artifact, target=target, hostPaths=["host-note.txt"])
        proof = integrated["integration"]["verification"]
        self.assertEqual(proof["hostPaths"], ["host-note.txt"])
        self.assertNotIn("host-note.txt", proof["matchingPaths"])

    def test_cancelled_goal_can_cleanup_only_after_a_recorded_conclusion(self):
        board = self.board()
        submitted = board.call("workflow_submit", {
            **CONFIGURATION, "requestId": "cancel-before-start", "hostId": "host-1", "task": "cancelled work",
            "cwd": str(self.repo), "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["."]},
        })
        self.controls[submitted["runId"]] = submitted["control"]
        cancelled = board.call("workflow_cancel", {"runId": submitted["runId"], **self.control(submitted)})
        blocked = self.plan(board, cancelled)
        self.assertIn("not-accepted", blocked["plan"]["reasons"])
        concluded = board.call("workflow_acknowledge", {
            "runId": cancelled["runId"], "commandId": "conclude-cancel", "expectedRevision": blocked["revision"],
            "verdict": "recorded", "note": "Cancelled before any model work; pinned input retained",
            **self.control(cancelled),
        })
        planned = self.plan(board, concluded, command_id="after-conclusion")
        self.assertEqual(planned["plan"]["reasons"], [])
        cleaned = board.store.workflow.cleanup_apply({
            "runId": planned["runId"], "expectedRevision": planned["revision"], "commandId": "remove-cancelled",
            "planId": planned["plan"]["planId"], "confirmPath": planned["plan"]["path"], **self.control(planned),
        })
        self.assertTrue(cleaned["removed"])
        self.assertEqual(cleaned["state"], "cancelled")


def load_tests(loader, _tests, _pattern):
    # Reuse lifecycle helpers without rerunning their entire inherited suite here.
    import unittest
    suite = unittest.TestSuite()
    for cls in (HostAdmissionTests, HostCompletionTests, HostConclusionCleanupTests):
        suite.addTests(cls(name) for name in cls.__dict__ if name.startswith("test_"))
    return suite
