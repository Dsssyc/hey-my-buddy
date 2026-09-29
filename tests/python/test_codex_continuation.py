"""Failed native observations preserve context without importing an outcome."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

from buddy.adapters.codex import CodexAdapter
from buddy.adapters.base import ExecutionContext
from buddy.adapters.codex_protocol import native_checkpoint
from test_workflow import WorkflowTestCase


class CodexContinuationTests(WorkflowTestCase):
    def test_controller_failure_to_workflow_continue_keeps_the_native_thread(self):
        self.executors["codex"] = adapter = CodexAdapter()
        board = self.board()
        board.call("worker_register", {"workerId": "w1", "capabilities": ["codex"]})
        submitted = self.submit(board, adapter="codex", provider="openai", model="fixture-model", effort="low")

        def execute(claim_response, case):
            claim = claim_response["claim"]
            attempt = claim["attempt"]
            environment = {key: value for key, value in os.environ.items() if not key.startswith("BUDDY_")}
            environment.update(BUDDY_DEV_SOURCE="1", BUDDY_STATE_DIR=str(self.directory),
                               BUDDY_RUNTIME_ROOT=str(self.directory / "runtime"),
                               BUDDY_CODEX_CLI=str(Path(__file__).parent / "fixtures/mock_codex.py"),
                               BUDDY_CODEX_FIXTURE_STATE=str(self.directory / "native-state.json"),
                               BUDDY_CODEX_FIXTURE_CASE=case)
            context = ExecutionContext(task_id=attempt["taskId"], attempt_id=attempt["attemptId"],
                                       generation=attempt["generation"], spec=claim["task"]["spec"],
                                       directory=self.directory / attempt["attemptId"], runtime={},
                                       environment=environment, turn=claim["turn"])
            handle = adapter.start(context)
            self.addCleanup(lambda: handle.terminate(grace_seconds=.2) if handle.group_alive() else None)
            self.assertIsNotNone(handle.wait(15))
            return adapter.collect(handle, context)

        first = self.claim(board)
        failed_result = execute(first, "invalid-json")
        self.assertEqual(failed_result.status, "failed")
        self.finish_turn(board, first, runner_status="failed", exit_code=1, seal=False,
                         result_mutator=lambda payload: (payload.clear(), payload.update(failed_result.result)))
        failed = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, failed)
        second = self.claim(board, claim_request_id="c2")
        self.assertEqual(second["claim"]["turn"]["resumeMode"], "native-session")
        resumed_result = execute(second, "ok")
        self.assertEqual(resumed_result.status, "ok", resumed_result.to_report())
        self.assertEqual(resumed_result.result["sessionId"], failed_result.result["sessionId"])
        self.assertEqual(resumed_result.result["nativeTurnId"], "native-turn-2")
        self.finish_turn(board, second, seal=False,
                         result_mutator=lambda payload: (payload.clear(), payload.update(resumed_result.result)))
        self.assertEqual(board.call("workflow_get", {"runId": submitted["runId"]})["state"], "delivered")

    def failed_run(self, *, native_status="completed", corrupt=False):
        self.executors["codex"] = CodexAdapter()
        board = self.board()
        board.call("worker_register", {"workerId": "w1", "capabilities": ["codex"]})
        submitted = self.submit(board, adapter="codex", provider="openai", model="fixture-model", effort="low")
        first = self.claim(board)
        document = first["claim"]["turn"]["input"]
        evidence = SimpleNamespace(thread_id="native-session-1", turn_id="native-turn-1", started=True,
                                   completed={"status": native_status}, event_seq=3,
                                   final_item={"id": "final-1", "text": "上一轮已经完成的调研内容。"})
        checkpoint = native_checkpoint(evidence, document)
        checkpoint["bindingSaved"] = native_status == "completed"
        if corrupt:
            checkpoint["attemptId"] = "foreign-attempt"

        def payload(result):
            result.pop("turn")
            result.update(sessionId=evidence.thread_id, nativeTurnId=evidence.turn_id, nativeCheckpoint=checkpoint,
                          processState={"shutdownConfirmed": True, "nativeExitCode": 0},
                          harnessAttempts=[{"harness": {"version": "previous-version"}}])

        self.finish_turn(board, first, runner_status="failed", exit_code=1, seal=False, result_mutator=payload)
        failed = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(failed["state"], "failed")
        self.assertIsNone(failed["turns"][0]["sessionId"])
        self.assertIsNone(failed["turns"][0]["disposition"])
        return board, failed

    def continued_input(self, board, failed, **kwargs):
        self.continue_run(board, failed, **kwargs)
        return self.claim(board, claim_request_id="c2")["claim"]["turn"]["input"]

    def test_invalid_outcome_can_resume_exact_completed_native_history(self):
        board, failed = self.failed_run()
        document = self.continued_input(board, failed)
        self.assertEqual(document["resumeMode"], "native-session")
        self.assertEqual(document["previousSessionId"], "native-session-1")
        self.assertEqual(document["context"]["nativeResume"]["nativeTurnId"], "native-turn-1")
        self.assertFalse(document["context"]["lastAssistantMessage"]["validatedOutcome"])

    def test_native_failure_reconstructs_with_previous_assistant_message(self):
        board, failed = self.failed_run(native_status="failed")
        document = self.continued_input(board, failed)
        self.assertEqual(document["resumeMode"], "reconstructed-new-session")
        self.assertNotIn("nativeResume", document["context"])
        self.assertEqual(document["context"]["lastAssistantMessage"]["text"], "上一轮已经完成的调研内容。")

    def test_harness_version_change_reconstructs_with_the_same_message(self):
        board, failed = self.failed_run()
        with board.store.db.write() as connection:
            connection.execute("UPDATE harness_health SET record_json=? WHERE adapter='codex'", (
                json.dumps({"adapter": "codex", "status": "ready", "version": "new-version"}),))
        document = self.continued_input(board, failed)
        self.assertEqual(document["resumeMode"], "reconstructed-new-session")
        self.assertEqual(document["context"]["resumeReason"], "harness-version-changed")
        self.assertEqual(document["context"]["lastAssistantMessage"]["text"], "上一轮已经完成的调研内容。")

    def test_foreign_attempt_cannot_supply_resume_or_reconstructed_context(self):
        board, failed = self.failed_run(corrupt=True)
        document = self.continued_input(board, failed)
        self.assertEqual(document["resumeMode"], "reconstructed-new-session")
        self.assertIsNone(document["previousSessionId"])
        self.assertNotIn("lastAssistantMessage", document["context"])

    def test_refused_resume_does_not_erase_the_last_message_before_reconstruction(self):
        board, failed = self.failed_run()
        self.continue_run(board, failed)
        second = self.claim(board, claim_request_id="c2")
        previous_message = second["claim"]["turn"]["input"]["context"]["lastAssistantMessage"]

        def refusal(result):
            result.pop("turn")
            result.update(code="native-resume-unavailable", modelStarted=False)

        self.finish_turn(board, second, runner_status="failed", exit_code=1, seal=False, result_mutator=refusal)
        failed = board.call("workflow_get", {"runId": failed["runId"]})
        self.continue_run(board, failed, command_id="continue-2")
        third = self.claim(board, claim_request_id="c3")["claim"]["turn"]["input"]
        self.assertEqual(third["resumeMode"], "reconstructed-new-session")
        self.assertNotIn("nativeResume", third["context"])
        self.assertEqual(third["context"]["lastAssistantMessage"], previous_message)
