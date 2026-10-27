"""The service rejects altered turn receipts before importing a micro-task result.

The board, transaction and harness provenance validators are real. The workspace
and native receipts are private synthetic fixtures; no native harness runs.
"""
from __future__ import annotations

import json

from hey_my_buddy.buddy.harnesses.registry import adapter as registered_adapter
from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase


class ServiceTurnMutationTests(WorkflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.receipt_index = 0
        for harness in ("dsh", "zcode"):
            self.executors[harness].validate_turn_provenance = registered_adapter(harness).validate_turn_provenance

    def exercise_receipt(self, harness: str, mutation=None):
        self.receipt_index += 1
        board = self.board()
        submitted = self.submit(board, request_id=f"receipt-{self.receipt_index}",
                                cwd=str(self.workdir(f"receipt-{self.receipt_index}")),
                                **{**BUDDY, "harness": harness})
        claimed = self.claim(board, claim_request_id=f"claim-{self.receipt_index}")
        claim = claimed["claim"]
        attempt = claim["attempt"]
        record = self.turn_record(claimed)
        if mutation is not None:
            mutation(record)
        result = {
            "status": "ok",
            "processState": {"shutdownConfirmed": True, "nativeExitCode": 0},
            "nativeEvidence": {"stop": {"groupState": "gone", "reason": "completed"}},
            "turn": record,
            "turnResultPath": "/tmp/turn-output.json",
            "workspaceSeal": self.workspace.seal(
                self.directory, claim["turn"]["input"]["executionWorkspace"],
                attempt["taskId"], attempt["attemptId"],
            ),
        }
        receipt = board.call("execution_result", {
            "attemptId": attempt["attemptId"], "generation": attempt["generation"],
            "nonce": "n" * 16, "status": "ok", "terminationReason": "completed",
            "result": result, "shutdownConfirmed": True, "exitCode": 0,
        })
        view = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        task_result = board.call("task_result", {"runId": submitted["runId"]})
        with board.store.db.read() as connection:
            attempt_row = connection.execute(
                "SELECT shutdown_confirmed, result_json FROM attempts WHERE attempt_id=?",
                (attempt["attemptId"],),
            ).fetchone()
            turn_row = connection.execute(
                "SELECT t.state,CASE WHEN t.state='returned'"
                " THEN json_extract(a.result_json,'$.result.turn.sessionId') END AS session_id"
                " FROM workflow_turns t LEFT JOIN attempts a USING(attempt_id) WHERE t.turn_id=?",
                (claim["turn"]["turnId"],),
            ).fetchone()
        stored = json.loads(attempt_row["result_json"])
        self.assertEqual(attempt_row["shutdown_confirmed"], 1)
        self.assertEqual(stored["result"]["processState"], result["processState"])
        self.assertEqual(stored["result"]["nativeEvidence"], result["nativeEvidence"])
        self.assertEqual(task_result["result"]["processState"], result["processState"])
        self.assertEqual(task_result["result"]["nativeEvidence"], result["nativeEvidence"])
        return receipt, view, task_result, turn_row

    def test_current_dsh_and_zcode_provenance_imports_a_valid_turn(self):
        for harness in ("dsh", "zcode"):
            with self.subTest(harness=harness):
                receipt, view, task_result, turn = self.exercise_receipt(harness)
                self.assertEqual(receipt["taskState"], "completed")
                self.assertTrue(receipt["workflow"]["accepted"])
                self.assertEqual(view["state"], "delivered")
                self.assertEqual(turn["state"], "returned")
                self.assertIsNotNone(turn["session_id"])
                self.assertTrue(any(artifact["kind"] == "output" for artifact in view["artifacts"]))
                self.assertNotIn("receiptRejection", task_result["result"])
                self.assertNotIn("microTaskError", task_result["result"])

    def test_changed_identity_and_missing_session_fail_at_service_boundary(self):
        mutations = {
            "taskId": lambda record: record.update(taskId="other-task"),
            "attemptId": lambda record: record.update(attemptId="other-attempt"),
            "generation": lambda record: record.update(generation=record["generation"] + 1),
            "turnId": lambda record: record.update(turnId="other-turn"),
            "resumeMode": lambda record: record.update(resumeMode="native-session"),
            "inputSha256": lambda record: record.update(inputSha256="0" * 64),
            "missing sessionId": lambda record: record.pop("sessionId"),
        }
        for harness in ("dsh", "zcode"):
            for name, mutation in mutations.items():
                with self.subTest(harness=harness, mutation=name):
                    self.assert_refused(harness, mutation)

    def test_changed_native_provenance_fails_at_service_boundary(self):
        mutations = {
            "unverified receipt": lambda record: record["provenance"].update(receiptVerified=False),
            "foreign root session": lambda record: record["provenance"].update(nativeSessionId="other-session"),
            "reversed native order": lambda record: record["provenance"].update(
                **({"settledOrdinal": 1} if record["provenance"]["harness"] == "dsh"
                   else {"toolResultSeq": 1})),
        }
        for harness in ("dsh", "zcode"):
            for name, mutation in mutations.items():
                with self.subTest(harness=harness, mutation=name):
                    self.assert_refused(harness, mutation)

    def assert_refused(self, harness, mutation):
        receipt, view, task_result, turn = self.exercise_receipt(harness, mutation)
        self.assertEqual(receipt["taskState"], "failed")
        self.assertFalse(receipt["workflow"]["accepted"])
        self.assertEqual(view["state"], "failed")
        self.assertEqual(task_result["resultMeta"]["status"], "failed")
        self.assertEqual(task_result["result"]["microTaskError"]["code"], "TURN_INVALID")
        self.assertNotIn("receiptRejection", task_result["result"])
        self.assertNotEqual(turn["state"], "returned")
        self.assertIsNone(turn["session_id"])
        self.assertFalse(any(artifact["kind"] == "output" for artifact in view["artifacts"]))
        self.assertIsNone(view["finalArtifactId"])
