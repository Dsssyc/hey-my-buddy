"""Host controls are bound to immutable execution evidence and current ownership."""
import json
from unittest.mock import patch

from hey_my_buddy.errors import BoardError
from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase


class HostAdmissionTests(WorkflowTestCase):
    def test_override_cannot_enable_a_disabled_buddy(self):
        from support import enable_fixture_buddy
        board = self.board()
        submitted = self.submit(board)
        replacement = {**BUDDY, "effort": "high"}
        enable_fixture_buddy(board.store, replacement)
        with board.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0 WHERE effort='high'")
        with self.assertRaises(BoardError) as caught:
            board.call("workflow_continue", {"runId": submitted["runId"], "commandId": "disabled-override",
                "expectedRevision": submitted["revision"], "input": "Continue", "buddy": replacement,
                "reason": "Try disabled buddy", **self.control(submitted)})
        self.assertEqual(caught.exception.code, "BUDDY_UNAVAILABLE")
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT enabled FROM buddies WHERE effort='high'").fetchone()[0], 0)

    def test_macro_task_of_is_atomic_scoped_and_idempotent(self):
        board = self.board()
        first = self.submit(board, kind="worktree", macroTask={"title": "Host agenda"})
        second = self.submit(board, kind="worktree", request_id="second", macroTaskOf=first["runId"])
        self.assertEqual(second["macroTaskId"], first["macroTaskId"])
        replay = self.submit(board, kind="worktree", request_id="second", macroTaskOf=first["runId"])
        self.assertTrue(replay["duplicate"])
        with self.assertRaises(BoardError) as caught:
            self.submit(board, kind="worktree", request_id="foreign", host_id="another-host", macroTaskOf=first["runId"])
        self.assertEqual(caught.exception.code, "CONFLICT")
        with self.assertRaises(BoardError):
            self.submit(board, kind="worktree", request_id="ambiguous", macroTaskOf=first["runId"], macroTaskId=first["macroTaskId"])
        self.assertEqual(board.store.count_tasks(), 2)

    def test_failed_explicit_buddy_can_change_with_a_reason(self):
        board = self.board()
        submitted = self.submit(board)
        self.register(board)
        claimed = self.claim(board)
        self.finish_turn(board, claimed, runner_status="failed", exit_code=1, seal=False)
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(current["state"], "failed")
        changed = {**BUDDY, "effort": "high"}
        continued = self.continue_run(board, current, buddy=changed)
        self.assertEqual(continued["buddy"], changed)
        self.assertFalse(continued["buddyLocked"])
        with board.store.db.read() as db:
            original = json.loads(db.execute("SELECT goal_json FROM workflow_runs").fetchone()[0])
            self.assertEqual(original["effort"], "off")
            event = db.execute("SELECT payload_json FROM events WHERE kind='workflow.buddy_overridden'").fetchone()
            self.assertEqual(json.loads(event[0])["buddy"], changed)

    def test_locked_buddy_is_part_of_identity_and_survives_failure(self):
        board = self.board()
        submitted = self.submit(board, buddyLocked=True)
        self.assertTrue(submitted["buddyLocked"])
        with self.assertRaises(BoardError) as caught:
            self.submit(board, buddyLocked=False)
        self.assertEqual(caught.exception.code, "CONFLICT")
        self.register(board)
        claimed = self.claim(board)
        self.finish_turn(board, claimed, runner_status="failed", exit_code=1, seal=False)
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        with self.assertRaises(BoardError) as caught:
            self.continue_run(board, current, buddy={**BUDDY, "effort": "high"})
        self.assertEqual(caught.exception.code, "BUDDY_CONFLICT")


class HostCompletionTests(WorkflowTestCase):
    def test_host_correction_is_recorded_by_one_adjusted_acceptance(self):
        board = self.board()
        parent = self.submit(board)
        self.register(board)
        self.finish_turn(board, self.claim(board))
        delivered = board.call("workflow_get", {"runId": parent["runId"]})
        # A target that does not carry the artifact is refused with the differing
        # paths first; there is no separate rejection record to archive.
        blank = self.workdir("blank-target")
        (blank / "keep.txt").write_text("untouched\n")
        import subprocess as sp
        sp.run(["git", "-C", str(blank), "init", "-q"], check=True)
        sp.run(["git", "-C", str(blank), "add", "keep.txt"], check=True)
        sp.run(["git", "-C", str(blank), "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-qm", "blank"], check=True)
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {"runId": parent["runId"], "artifactId": delivered["finalArtifactId"],
                "note": "Host found a required adjustment", "target": {"path": str(blank), "ref": "HEAD"},
                **self.control(parent)})
        self.assertEqual(raised.exception.code, "INTEGRATION_UNVERIFIED")
        self.assertTrue(raised.exception.details["paths"])
        accepted = board.call("workflow_accept", {"runId": parent["runId"], "artifactId": delivered["finalArtifactId"],
            "note": "Host applied its own adjustment to the sealed output", "adjusted": True,
            "target": {"path": str(blank), "ref": "HEAD"}, **self.control(parent)})
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["counts"]["turns"], 1)
        self.assertEqual(accepted["integration"]["verification"]["adjustments"],
                         raised.exception.details["paths"])
        replay = board.call("workflow_accept", {"runId": parent["runId"], "artifactId": delivered["finalArtifactId"],
            "note": "Host applied its own adjustment to the sealed output", "adjusted": True,
            "target": {"path": str(blank), "ref": "HEAD"}, **self.control(parent)})
        self.assertTrue(replay["duplicate"])

    def test_continue_clears_the_current_final_binding_but_retains_the_old_artifact(self):
        board = self.board()
        parent = self.submit(board)
        self.register(board)
        self.finish_turn(board, self.claim(board))
        delivered = board.call("workflow_get", {"runId": parent["runId"]})
        continued = self.continue_run(board, delivered)
        self.assertIsNone(continued["finalArtifactId"])
        self.assertIsNone(continued["finalAttemptId"])
        self.assertIn(delivered["finalArtifactId"], [item["artifactId"] for item in continued["artifacts"]])

    def test_delivered_goal_without_a_conclusion_still_awaits_final_acknowledgement(self):
        board = self.board()
        submitted = self.submit(board)
        self.register(board)
        self.finish_turn(board, self.claim(board))
        delivered = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(delivered["state"], "delivered")
        self.assertFalse(delivered["awaitingHost"])
        self.assertIsNone(delivered["hostConclusion"])
        self.assertEqual(delivered["waitReason"], "the goal work is delivered and awaits final acknowledgement")

    def test_concluded_delivered_goal_reports_the_conclusion_instead_of_acknowledgement(self):
        board = self.board()
        submitted = self.submit(board)
        self.register(board)
        self.finish_turn(board, self.claim(board))
        delivered = board.call("workflow_get", {"runId": submitted["runId"]})
        concluded = board.call("workflow_conclude", {
            "runId": delivered["runId"], "note": "Delivered without acceptance; the goal is closed",
            **self.control(delivered)})
        self.assertEqual(concluded["state"], "delivered")
        self.assertFalse(concluded["awaitingHost"])
        self.assertIsNotNone(concluded["hostConclusion"])
        self.assertEqual(concluded["waitReason"], "the goal work was concluded by its Host without acceptance")
        # A later read keeps the same honest reason; the state stays delivered.
        current = board.call("workflow_get", {"runId": delivered["runId"]})
        self.assertEqual(current["hostConclusion"], concluded["hostConclusion"])
        self.assertFalse(current["awaitingHost"])
        self.assertEqual(current["waitReason"], "the goal work was concluded by its Host without acceptance")

    def test_takeover_preserves_a_conclusion_until_the_new_host_continues(self):
        board = self.board()
        submitted = self.submit(board)
        self.register(board)
        self.finish_turn(board, self.claim(board))
        delivered = board.call("workflow_get", {"runId": submitted["runId"]})
        note = "Delivered without acceptance; the goal is closed"
        concluded = board.call("workflow_conclude", {
            "runId": delivered["runId"], "note": note, **self.control(delivered)})
        taken = board.call("workflow_takeover", {
            "runId": delivered["runId"], "commandId": "takeover-concluded",
            "expectedOwnerGeneration": concluded["ownerGeneration"], "newHostId": "host-2",
            **self.control(concluded)})
        self.controls[taken["runId"]] = taken["control"]
        current = board.call("workflow_get", {"runId": taken["runId"]})
        self.assertEqual(current["ownerGeneration"], concluded["ownerGeneration"] + 1)
        self.assertEqual(current["state"], "delivered")
        self.assertFalse(current["awaitingHost"])
        self.assertEqual(current["hostConclusion"], concluded["hostConclusion"])
        self.assertEqual(current["waitReason"], "the goal work was concluded by its Host without acceptance")
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": current["runId"], "artifactId": current["finalArtifactId"],
                "note": "Host checked the sealed output", "notRequired": "Fixture needs no target",
                **self.control(current)})
        self.assertEqual(raised.exception.code, "CONFLICT")
        self.assertIn("already has a recorded conclusion", str(raised.exception))
        params = {"runId": current["runId"], "note": note, **self.control(current)}
        replay = board.call("workflow_conclude", params)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["hostConclusion"], concluded["hostConclusion"])
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_conclude", {**params, "note": "A different conclusion"})
        self.assertEqual(raised.exception.code, "CONFLICT")
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_host_conclusions").fetchone()[0], 1)
            self.assertIsNone(db.execute("SELECT accepted_at FROM tasks").fetchone()[0])
        continued = self.continue_run(board, current)
        self.assertEqual(continued["state"], "executing")
        self.assertIsNone(continued["hostConclusion"])
        self.assertIsNone(board.call("workflow_get", {"runId": current["runId"]})["hostConclusion"])
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_host_conclusions").fetchone()[0], 1)

    def test_takeover_fences_a_late_conclusion_from_the_old_host(self):
        board = self.board()
        submitted = self.submit(board)
        self.register(board)
        self.finish_turn(board, self.claim(board))
        delivered = board.call("workflow_get", {"runId": submitted["runId"]})
        old_control = self.control(delivered)
        taken = board.call("workflow_takeover", {
            "runId": delivered["runId"], "commandId": "takeover-before-conclusion",
            "expectedOwnerGeneration": delivered["ownerGeneration"], "newHostId": "host-2",
            **old_control})
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_conclude", {
                "runId": taken["runId"], "note": "Late conclusion", **old_control})
        self.assertEqual(raised.exception.code, "STALE_GENERATION")
        current = board.call("workflow_get", {"runId": taken["runId"]})
        self.assertIsNone(current["hostConclusion"])
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_host_conclusions").fetchone()[0], 0)
        concluded = board.call("workflow_conclude", {
            "runId": taken["runId"], "note": "Current Host conclusion", **taken["control"]})
        self.assertEqual(concluded["hostConclusion"]["ownerGeneration"], taken["ownerGeneration"])

    def test_direct_helper_completion_settles_the_owned_graph_without_a_worker_round(self):
        board = self.board()
        parent = self.submit(board, kind="worktree")
        self.register(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        parent = board.call("workflow_get", {"runId": parent["runId"]})
        approved = self.decide(board, parent, parent["activeRequest"]["requestId"], autoContinue=False,
            helpers=[{"requestId": "host-finish-helper", "task": "Produce a checked change", "cwd": str(self.workdir()),
                      "executionWorkspace": {"kind": "worktree", "access": "write"}}])
        child_id = approved["children"][0]["taskId"]
        self.finish_turn(board, self.claim(board, claim_request_id="helper-claim", run_id=child_id), disposition="attention")
        parent = board.call("workflow_get", {"runId": parent["runId"]})
        child = board.call("workflow_get", {"runId": child_id})
        artifact = next(item for item in child["artifacts"] if item["kind"] == "output")
        board.call("workflow_accept", {"runId": parent["runId"], "targetRunId": child_id,
            "artifactId": artifact["artifactId"],
            "note": "Host completed the current sealed helper turn",
            "notRequired": "Fixture evidence checked without a separate target", **self.control(parent)})
        parent = board.call("workflow_get", {"runId": parent["runId"]})
        self.assertEqual(parent["children"][0]["state"], "succeeded")
        self.assertEqual(parent["state"], "awaiting-host")
        self.assertEqual(parent["counts"]["turns"], 1)
        artifact = next(item for item in parent["artifacts"] if item["kind"] == "output")
        finished = board.call("workflow_accept", {"runId": parent["runId"],
            "artifactId": artifact["artifactId"], "note": "Host checked the whole goal",
            "notRequired": "Fixture evidence checked without a separate target", **self.control(parent)})
        self.assertEqual(finished["state"], "accepted")
        self.assertEqual(finished["counts"]["turns"], 1)

    def test_host_can_integrate_and_finish_an_attention_turn_without_another_execution(self):
        board = self.board()
        submitted = self.submit(board)
        self.register(board)
        claim = self.claim(board)
        self.finish_turn(board, claim, disposition="attention")
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        artifact = next(item for item in current["artifacts"] if item["kind"] == "output")
        params = {"runId": current["runId"], "artifactId": artifact["artifactId"],
                  "note": "Host checked and integrated the sealed output",
                  "notRequired": "the fixture output needs no repository target", **self.control(current)}
        # Acceptance always carries an integration decision: neither a target nor a
        # reasoned notRequired decision is no acceptance at all.
        with self.assertRaises(BoardError) as caught:
            board.call("workflow_accept", {**params, "notRequired": None})
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        completed = board.call("workflow_accept", params)
        self.assertEqual((completed["state"], completed["status"]), ("accepted", "completed"))
        self.assertEqual(completed["counts"]["turns"], 1)
        self.assertEqual(completed["counts"]["openRequests"], 0)
        self.assertEqual(completed["currentTurn"]["disposition"], "attention")
        self.assertTrue(board.call("workflow_accept", params)["duplicate"])
        with self.assertRaises(BoardError) as caught:
            board.call("workflow_accept", {**params, "note": "a different review"})
        self.assertEqual(caught.exception.code, "CONFLICT")

    def test_failure_conclusion_preserves_failure_and_never_becomes_an_acceptance(self):
        board = self.board()
        submitted = self.submit(board)
        self.register(board)
        claim = self.claim(board)
        self.finish_turn(board, claim, runner_status="failed", exit_code=1, seal=False)
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        params = {"runId": current["runId"], "note": "Partial work retained; unfinished", **self.control(current)}
        concluded = board.call("workflow_conclude", params)
        self.assertEqual((concluded["state"], concluded["status"]), ("failed", "failed"))
        self.assertIsNotNone(concluded["hostConclusion"])
        self.assertEqual(concluded["verdict"], "concluded")
        self.assertTrue(board.call("workflow_conclude", params)["duplicate"])
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_conclude", {**params, "note": "a different conclusion"})
        self.assertEqual(raised.exception.code, "CONFLICT")
        with board.store.db.read() as db:
            self.assertIsNone(db.execute("SELECT accepted_at FROM tasks").fetchone()[0])
        continued = self.continue_run(board, concluded)
        self.assertIsNone(continued["hostConclusion"])
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_host_conclusions").fetchone()[0], 1)
        # A later execution of the same goal fails again: the same note records a
        # new conclusion for the new attempt instead of replaying the old one.
        second = self.claim(board, claim_request_id="c2")
        self.finish_turn(board, second, runner_status="failed", exit_code=1, seal=False, session_id="sess-2")
        params_again = {"runId": concluded["runId"], "note": "Partial work retained; unfinished",
                        **self.control(board.call("workflow_get", {"runId": concluded["runId"]}))}
        reconcluded = board.call("workflow_conclude", params_again)
        self.assertEqual(reconcluded["state"], "failed")
        self.assertFalse(reconcluded["duplicate"])
        self.assertNotEqual(reconcluded["conclusion"]["conclusionId"],
                            concluded["conclusion"]["conclusionId"])
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_host_conclusions").fetchone()[0], 2)
        self.assertTrue(board.call("workflow_conclude", params_again)["duplicate"])

    def test_conclusion_refuses_an_execution_changed_during_partial_sealing(self):
        board = self.board()
        submitted = self.submit(board, kind="worktree")
        self.register(board)
        first = self.claim(board)
        self.finish_turn(board, first, runner_status="failed", exit_code=1, seal=False)
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        real_seal = self.workspace.host_seal
        later_attempt = []

        def seal_then_continue(*args, **kwargs):
            sealed = real_seal(*args, **kwargs)
            self.continue_run(board, current, command_id="continue-during-conclusion")
            second = self.claim(board, claim_request_id="later-attempt")
            later_attempt.append(second["claim"]["attempt"]["attemptId"])
            self.finish_turn(board, second, runner_status="failed", exit_code=1,
                             seal=False, session_id="later-session")
            return sealed

        with patch.object(self.workspace, "host_seal", side_effect=seal_then_continue):
            with self.assertRaises(BoardError) as raised:
                board.call("workflow_conclude", {"runId": current["runId"],
                    "note": "only the first execution was reviewed", **self.control(current)})
        self.assertEqual(raised.exception.code, "REVISION_CONFLICT")
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_host_conclusions").fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM workflow_artifacts WHERE kind='partial-output'"
            ).fetchone()[0], 0)
        latest = board.call("workflow_get", {"runId": current["runId"]})
        self.assertEqual(latest["state"], "failed")
        self.assertIsNone(latest["hostConclusion"])
        concluded = board.call("workflow_conclude", {"runId": current["runId"],
            "note": "the later execution was reviewed separately", **self.control(latest)})
        self.assertEqual(concluded["conclusion"]["attemptId"], later_attempt[0])


from blackboard.tasks.test_workspace_lifecycle import LifecycleTestCase


class HostConclusionCleanupTests(LifecycleTestCase):
    def test_acceptance_records_only_real_separate_host_paths(self):
        from blackboard.tasks.test_workspace_lifecycle import git
        board = self.board()
        self.register(board)
        _submitted, _claim, _manifest, _checkout, _seal, current, artifact = self.run_worktree(board)
        target = self.target_with_artifact(artifact)
        (target["path"] / "host-note.txt").write_text("Host verification\n")
        git(target["path"], "add", "host-note.txt")
        git(target["path"], "commit", "-qm", "Host supplement")
        for paths in (["tracked.txt"], ["absent.txt"], ["../outside"]):
            with self.assertRaises(BoardError):
                self.accept(board, current, artifact, target=target, hostPaths=paths,
                            beforeCommit=target["before"])
        accepted = self.accept(board, self.view(board, current["runId"]), artifact, target=target,
                               hostPaths=["host-note.txt"], beforeCommit=target["before"])
        self.assertEqual(accepted["state"], "accepted")
        proof = accepted["integration"]["verification"]
        self.assertEqual(proof["hostPaths"], ["host-note.txt"])
        self.assertNotIn("host-note.txt", proof["matchingPaths"])

    def test_cancelled_goal_can_cleanup_only_after_a_recorded_conclusion(self):
        board = self.board()
        submitted = board.call("workflow_submit", {
            **BUDDY, "requestId": "cancel-before-start", "hostId": "host-1", "task": "cancelled work",
            "cwd": str(self.repo), "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["."]},
        })
        self.controls[submitted["runId"]] = submitted["control"]
        cancelled = board.call("workflow_cancel", {"runId": submitted["runId"], **self.control(submitted)})
        with self.assertRaises(BoardError) as raised:
            self.reclaim(board, cancelled)
        self.assertEqual(raised.exception.code, "NOT_READY")
        self.assertIn("not-accepted", raised.exception.details["reasons"])
        concluded = board.call("workflow_conclude", {
            "runId": cancelled["runId"],
            "note": "Cancelled before any model work; pinned input retained",
            **self.control(cancelled),
        })
        self.assertEqual(concluded["state"], "cancelled")
        self.assertIsNotNone(concluded["hostConclusion"])
        # No execution attempt ever ran on this checkout, so the conclusion seals
        # no partial output and still reclaims the prepared worktree.
        self.assertIsNone(concluded["partialArtifactId"])
        # The conclusion reclaims the registered checkout; a blocked attempt would
        # have kept the conclusion and reported its reasons instead.
        self.assertTrue(concluded["reclaim"]["removed"])
        self.assertEqual(self.view(board, cancelled["runId"])["cleanup"]["state"], "applied")


def load_tests(loader, _tests, _pattern):
    # Reuse lifecycle helpers without rerunning their entire inherited suite here.
    import unittest
    suite = unittest.TestSuite()
    for cls in (HostAdmissionTests, HostCompletionTests, HostConclusionCleanupTests):
        suite.addTests(cls(name) for name in cls.__dict__ if name.startswith("test_"))
    return suite
