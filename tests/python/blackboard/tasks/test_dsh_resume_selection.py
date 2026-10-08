"""DSH rides the common governed resume selection; no DSH condition is added.

Every case drives the real WorkflowCoordinator turn creation through the same
worker claim flow as the rest of this package, under this suite's declared
boundary: the workspace is the mock module and the executor is a stand-in, so a
synthetic record in the current provenance format is imported and no harness or
model runs. The one DSH difference is injection only: a clearly candidate-marked
executor instance is what selects ``native-session`` here, and each case
captures the product ``DshAdapter`` declaration before and after the injection
and compares them, so the tests stay valid whichever value the product declares
after real verification — none of them pins that value. A qualified prior DSH
turn reports ``nativeSession.storageOwner == "buddy-goal"``, the
same fact the common ownership judgment consumes for codex; the old
attempt-private reporting shape gets its own reconstruction case. Which
judgments are common blackboard rules and which are a native module's private
bindings is read from the current source and recorded in the R3 acceptance
document, not asserted as new behavior.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from hey_my_buddy.blackboard.store.db import canonical_json
from hey_my_buddy.buddy.harnesses.registry import adapter as registry_adapter

from blackboard.tasks.test_workflow import CONFIGURATION, WorkflowTestCase

CANDIDATE_SESSION = "dsh-candidate-native-1"


class DshResumeSelectionTests(WorkflowTestCase):
    """The common selection exercised with the dsh adapter, capability injected."""

    def elect_candidate(self) -> None:
        # Candidate-only injection: a fresh stand-in carries native_resume True
        # for this case; the registered product adapter is never touched, and
        # every caller compares the product declaration across the injection.
        self.executors["dsh"] = SimpleNamespace(
            native_resume=True, validate_turn_provenance=lambda record: None)

    def elect_false_capability(self) -> None:
        # An explicit local stand-in with the capability off: the reconstruction
        # path below never depends on the product's current declared value.
        self.executors["dsh"] = SimpleNamespace(
            native_resume=False, validate_turn_provenance=lambda record: None)

    def assert_declaration_stable(self, before) -> None:
        self.assertEqual(registry_adapter("dsh").native_resume, before,
                         "the executor instance injection must not change the product declaration")

    def goal_owned_storage(self):
        """The positive prior-turn fact: the goal-owned native storage shape."""
        return lambda result: result.update(nativeSession={"storageOwner": "buddy-goal"})

    def attempt_owned_storage(self):
        """The shape a pre-wiring DSH result reported: attempt-private storage."""
        return lambda result: result.update(nativeSession={"storageOwner": "buddy-attempt"})

    def test_a_candidate_capable_dsh_selects_native_session_and_retains_the_session(self):
        before = registry_adapter("dsh").native_resume
        self.elect_candidate()
        self.assert_declaration_stable(before)
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        first = self.claim(board)
        self.assertEqual(first["claim"]["turn"]["resumeMode"], "initial")
        self.assertIsNone(first["claim"]["turn"]["input"]["previousSessionId"])
        self.finish_turn(board, first, disposition="attention", session_id=CANDIDATE_SESSION,
                         result_mutator=self.goal_owned_storage())

        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, view, command_id="continue-to-native")
        resumed = self.claim(board, claim_request_id="native-turn-2")
        turn = resumed["claim"]["turn"]
        self.assertEqual(turn["resumeMode"], "native-session")
        self.assertEqual(turn["input"]["previousSessionId"], CANDIDATE_SESSION)
        self.assertNotIn("resumeReason", turn["input"]["context"])
        self.assertEqual(turn["input"]["context"]["lastCheckpoint"]["disposition"], "attention")

        # The resumed turn retains the exact previous session: the imported
        # record and both durable turn rows carry exactly that identity.
        done = self.finish_turn(board, resumed, session_id=CANDIDATE_SESSION)
        self.assertEqual(done["taskState"], "completed")
        with board.store.db.read() as db:
            rows = db.execute(
                "SELECT session_id, state FROM workflow_turns WHERE run_id=? ORDER BY turn_index",
                (submitted["runId"],)).fetchall()
        self.assertEqual([row["state"] for row in rows], ["concluded", "concluded"])
        self.assertEqual(rows[-1]["session_id"], CANDIDATE_SESSION)

    def test_a_false_capability_executor_keeps_reconstruction(self):
        before = registry_adapter("dsh").native_resume
        self.elect_false_capability()
        self.assert_declaration_stable(before)
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        first = self.claim(board)
        self.finish_turn(board, first, disposition="attention", session_id=CANDIDATE_SESSION,
                         result_mutator=self.goal_owned_storage())

        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, view, command_id="continue-unwired")
        resumed = self.claim(board, claim_request_id="reconstructed-turn-2")
        turn = resumed["claim"]["turn"]
        self.assertEqual(turn["resumeMode"], "reconstructed-new-session")
        self.assertEqual(turn["input"]["previousSessionId"], CANDIDATE_SESSION)
        self.assertNotIn("resumeReason", turn["input"]["context"])

    def test_a_failed_previous_turn_reconstructs_without_a_resume_reason(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        first = self.claim(board)
        tampered = self.turn_record(first)
        tampered["inputSha256"] = "b" * 64
        rejected = self.finish_turn(board, first, record=tampered)
        self.assertFalse(rejected["workflow"]["accepted"])
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["state"], "failed")
        self.continue_run(board, view, command_id="continue-after-rejected")
        second = self.claim(board, claim_request_id="claim-after-rejected")
        turn = second["claim"]["turn"]
        self.assertEqual(turn["resumeMode"], "reconstructed-new-session")
        self.assertIsNone(turn["input"]["previousSessionId"])
        self.assertNotIn("resumeReason", turn["input"]["context"])

    def test_a_native_session_turn_must_retain_the_exact_previous_session(self):
        self.elect_candidate()
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        first = self.claim(board)
        self.finish_turn(board, first, disposition="attention", session_id=CANDIDATE_SESSION,
                         result_mutator=self.goal_owned_storage())
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, view, command_id="continue-native")
        resumed = self.claim(board, claim_request_id="native-turn-2")
        self.assertEqual(resumed["claim"]["turn"]["resumeMode"], "native-session")
        foreign = self.finish_turn(board, resumed, session_id="foreign-session")
        self.assertFalse(foreign["workflow"]["accepted"])
        self.assertIn("exact previous session", foreign["attempt"]["error"])

        failed = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(failed["state"], "failed")
        self.continue_run(board, failed, command_id="continue-after-retention-failure")
        rebuilt = self.claim(board, claim_request_id="claim-after-retention-failure")
        turn = rebuilt["claim"]["turn"]
        self.assertEqual(turn["resumeMode"], "reconstructed-new-session")
        self.assertIsNone(turn["input"]["previousSessionId"])
        self.assertNotIn("resumeReason", turn["input"]["context"])

    def test_a_harness_version_change_reconstructs_with_the_version_reason(self):
        before = registry_adapter("dsh").native_resume
        self.elect_candidate()
        self.assert_declaration_stable(before)
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        first = self.claim(board)
        self.finish_turn(board, first, disposition="attention", session_id=CANDIDATE_SESSION,
                         result_mutator=self.goal_owned_storage())
        attempt_id = first["claim"]["attempt"]["attemptId"]
        with board.store.db.write() as db:
            result = json.loads(db.execute(
                "SELECT result_json FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()[0])
            result["harnessAttempts"] = [{"harness": {"version": "0.1.5-rc.1"}}]
            db.execute("UPDATE attempts SET result_json=? WHERE attempt_id=?",
                       (json.dumps(result), attempt_id))
            db.execute("INSERT INTO harness_health(adapter, record_json) VALUES('dsh', ?)"
                       " ON CONFLICT(adapter) DO UPDATE SET record_json=excluded.record_json",
                       (canonical_json({"version": "0.2.0"}),))
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, view, command_id="continue-after-upgrade")
        resumed = self.claim(board, claim_request_id="turn-after-upgrade")
        turn = resumed["claim"]["turn"]
        self.assertEqual(turn["resumeMode"], "reconstructed-new-session")
        self.assertEqual(turn["input"]["context"]["resumeReason"], "harness-version-changed")

    def test_an_account_credential_change_reconstructs_with_the_account_reason(self):
        before = registry_adapter("dsh").native_resume
        self.elect_candidate()
        self.assert_declaration_stable(before)
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        first = self.claim(board)
        self.finish_turn(board, first, disposition="attention", session_id=CANDIDATE_SESSION,
                         result_mutator=self.goal_owned_storage())
        # The real credential-revision boundary, with no credential file or login:
        # this fixture bump is exactly what makes the next frozen account identity
        # differ from the previous attempt's.
        with board.service.account_settings.credential_change("dsh", "native",
                                                              expected_credential_revision=0):
            pass
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, view, command_id="continue-after-credential-change")
        resumed = self.claim(board, claim_request_id="turn-after-credential-change")
        self.assertEqual(resumed["claim"]["account"]["credentialRevision"], 1)
        turn = resumed["claim"]["turn"]
        self.assertEqual(turn["resumeMode"], "reconstructed-new-session")
        self.assertEqual(turn["input"]["context"]["resumeReason"], "account-credentials-changed")

    def test_a_configuration_change_reconstructs_without_a_resume_reason(self):
        self.elect_candidate()
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        first = self.claim(board)
        self.finish_turn(board, first, disposition="attention", session_id=CANDIDATE_SESSION,
                         result_mutator=self.goal_owned_storage())
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, view, command_id="continue-new-effort",
                          configuration={**CONFIGURATION, "effort": "high"},
                          reason="Host raised effort after the prior turn")
        resumed = self.claim(board, claim_request_id="turn-new-effort")
        turn = resumed["claim"]["turn"]
        self.assertEqual(turn["resumeMode"], "reconstructed-new-session")
        self.assertEqual(turn["input"]["context"]["executionConfiguration"]["effort"], "high")
        self.assertNotIn("resumeReason", turn["input"]["context"])

    def test_old_attempt_private_native_storage_reconstructs_with_the_home_reason(self):
        """A pre-wiring DSH result reporting attempt-private storage never resumes.

        The prior turn is otherwise fully qualified: concluded, same session,
        configuration, harness version and account. The common ownership
        judgment — the same storageOwner qualification codex already applies —
        must reconstruct with an explicit reason instead of selecting
        native-session and failing later inside the native module.
        """
        before = registry_adapter("dsh").native_resume
        self.elect_candidate()
        self.assert_declaration_stable(before)
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        first = self.claim(board)
        self.finish_turn(board, first, disposition="attention", session_id=CANDIDATE_SESSION,
                         result_mutator=self.attempt_owned_storage())

        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, view, command_id="continue-old-storage")
        resumed = self.claim(board, claim_request_id="turn-after-old-storage")
        turn = resumed["claim"]["turn"]
        self.assertEqual(turn["resumeMode"], "reconstructed-new-session")
        self.assertEqual(turn["input"]["context"]["resumeReason"], "private-native-home-required")
        self.assertEqual(turn["input"]["previousSessionId"], CANDIDATE_SESSION)


if __name__ == "__main__":
    import unittest
    unittest.main()
