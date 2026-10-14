"""A proven-stopped interrupted turn keeps its partial work for explicit continuation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from blackboard.tasks.test_workflow import NONCE, WorkflowTestCase
from hey_my_buddy.blackboard.tasks import interruption
from hey_my_buddy.buddy.harnesses.dsh.session_records import session_record_facts
from hey_my_buddy.buddy.roles.turn_io import input_hash
from hey_my_buddy.private_dirs import attempt_root, native_root
from hey_my_buddy.protocol import process_identity as processes


IDENTITY = {"bootId": "macos:00000000-0000-0000-0000-000000000001", "pid": 123,
            "startedAt": 123250000, "processGroupId": 123, "jobName": None,
            "worker": {"bootId": "macos:00000000-0000-0000-0000-000000000001", "pid": 122,
                       "startedAt": 120500000}}


class InterruptedContinuationTests(WorkflowTestCase):
    def interrupted(self, board, claim):
        attempt = claim["claim"]["attempt"]
        attempt_id = attempt["attemptId"]
        board.call("worker_progress", {
            "workerId": "w1", "attemptId": attempt_id,
            "generation": attempt["generation"], "nonce": NONCE,
            "processIdentity": IDENTITY,
        })
        with board.store.db.read() as connection:
            stored = dict(board.store._attempt_row(connection, attempt_id))
        journal = interruption._native_journal(board.store, stored, IDENTITY)
        if journal is not None:
            journal_path = Path(journal["path"])
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            processes.NativeProcessJournal(journal_path, journal["binding"])
        with patch.object(interruption.processes, "worker_identity_alive", return_value=False), \
             patch.object(interruption.processes, "inspect_process_identity", return_value="identity-absent"):
            summary = board.store.reconcile_startup()
        self.assertEqual(summary["interrupted"], 1)
        self.assertEqual(self.workspace.seal_calls[-1], (attempt["taskId"], attempt_id))

    def test_stopped_turn_is_interrupted_and_explicit_continue_uses_partial_commit(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        first = self.claim(board)
        self.interrupted(board, first)
        failed = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["currentTurn"]["state"], "interrupted")
        self.assertEqual(failed["task"]["interruptionEvidence"]["kind"], "identity-absent")
        from hey_my_buddy.cli.cli_views import micro_task_brief
        self.assertEqual(micro_task_brief(failed)['interruptionEvidence']['kind'], 'identity-absent')
        partial = next(item for item in failed["artifacts"] if item["kind"] == "partial-output")
        self.assertFalse(partial["verified"])
        self.continue_run(board, failed)
        second = self.claim(board, claim_request_id="c2")
        document = second["claim"]["turn"]["input"]
        self.assertEqual(document["executionWorkspace"]["baseCommit"], partial["outputCommit"])
        self.assertEqual(document["resumeMode"], "reconstructed-new-session")
        self.assertEqual(document["context"]["interruptedTurn"]["evidence"]["kind"], "identity-absent")
        self.assertEqual(document["context"]["interruptedTurn"]["turnId"], first["claim"]["turn"]["turnId"])
        self.assertIn(partial["artifactId"], [item["artifactId"] for item in document["context"]["artifacts"]])

    def test_codex_terminal_checkpoint_selects_native_before_next_turn(self):
        self.executors["codex"].native_resume = True
        board = self.board()
        board.call("worker_register", {"workerId": "w1", "capabilities": ["codex"]})
        submitted = self.submit(board, harness="codex", provider="openai", model="fixture-model", effort="low")
        first = self.claim(board)
        document = first["claim"]["turn"]["input"]
        attempt_id = document["attemptId"]
        session_id = "terminal-codex-thread"
        native_turn_id = "terminal-codex-turn"
        checkpoint = {"version": 1,
                      **{key: document[key] for key in ("taskId", "attemptId", "generation", "turnId")},
                      "inputSha256": input_hash(document), "sessionId": session_id,
                      "nativeTurnId": native_turn_id, "nativeTurnStarted": True,
                      "nativeTurnStatus": "interrupted", "eventSeq": 3, "bindingSaved": True}
        root = attempt_root(self.directory, "codex", submitted["runId"], attempt_id)
        root.mkdir(parents=True)
        (root / "native-checkpoint.json").write_text(json.dumps(checkpoint))
        binding_root = native_root(self.directory, "codex", submitted["runId"])
        binding_root.mkdir(parents=True)
        binding = {"taskId": submitted["runId"], "threadId": session_id,
                   "cwd": document["executionWorkspace"]["path"],
                   "buddy": {key: document["context"]["buddy"][key]
                             for key in ("provider", "model", "effort")},
                   "lastTurnId": native_turn_id, "lastAttemptId": attempt_id,
                   "lastInputSha256": checkpoint["inputSha256"]}
        (binding_root / (hashlib.sha256(session_id.encode()).hexdigest() + ".json")).write_text(json.dumps(binding))
        native = board.store.workflow.interrupted_native_facts(attempt_id)
        self.assertEqual(native["nativeCheckpoint"], checkpoint)
        self.interrupted(board, first)
        failed = board.call("workflow_get", {"runId": submitted["runId"]})
        with board.store.db.read() as connection:
            result = json.loads(board.store._attempt_row(connection, attempt_id)["result_json"])["result"]
        self.assertEqual(result["nativeCheckpoint"], checkpoint)
        self.continue_run(board, failed)
        resumed = self.claim(board, claim_request_id="c2")["claim"]["turn"]["input"]
        self.assertEqual(resumed["resumeMode"], "native-session")
        self.assertEqual(resumed["previousSessionId"], session_id)
        self.assertEqual(resumed["context"]["nativeResume"]["nativeTurnId"], native_turn_id)

    def test_incomplete_codex_record_reconstructs(self):
        self.executors["codex"].native_resume = True
        board = self.board()
        board.call("worker_register", {"workerId": "w1", "capabilities": ["codex"]})
        submitted = self.submit(board, harness="codex", provider="openai", model="fixture-model", effort="low")
        first = self.claim(board)
        document = first["claim"]["turn"]["input"]
        root = attempt_root(self.directory, "codex", submitted["runId"], document["attemptId"])
        root.mkdir(parents=True)
        (root / "native-checkpoint.json").write_text(json.dumps({
            "version": 1, **{key: document[key] for key in ("taskId", "attemptId", "generation", "turnId")},
            "inputSha256": input_hash(document), "sessionId": "unfinished", "nativeTurnId": "turn",
            "nativeTurnStarted": True, "nativeTurnStatus": "incomplete", "eventSeq": 3,
            "bindingSaved": False,
        }))
        self.assertEqual(board.store.workflow.interrupted_native_facts(document["attemptId"]), {})
        self.interrupted(board, first)
        failed = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, failed)
        resumed = self.claim(board, claim_request_id="c2")["claim"]["turn"]["input"]
        self.assertEqual(resumed["resumeMode"], "reconstructed-new-session")

    def test_dsh_terminal_record_and_binding_select_native(self):
        self.executors["dsh"].native_resume = True
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        first = self.claim(board)
        document = first["claim"]["turn"]["input"]
        session_id = "terminal-dsh-session"
        root = native_root(self.directory, "dsh", submitted["runId"])
        sessions = root / "sessions"
        sessions.mkdir(parents=True)
        record_path = sessions / "session.v3.jsonl"
        events = [
            {"type": "session", "version": 3, "id": session_id},
            {"type": "turn/start", "seq": 1, "data": {"turn": 1}},
            {"type": "turn/end", "seq": 2, "data": {"turn": 1, "reason": {"kind": "error"}}},
        ]
        record_path.write_text("".join(json.dumps(item) + "\n" for item in events))
        record = session_record_facts(sessions, [session_id])
        self.assertTrue(record["turnEndSeen"])
        attempt_dir = attempt_root(self.directory, "dsh", submitted["runId"], document["attemptId"])
        attempt_dir.mkdir(parents=True)
        (attempt_dir / "dsh-session-record.json").write_text(json.dumps(record))
        binding = {"taskId": submitted["runId"], "sessionId": session_id,
                   "cwd": document["executionWorkspace"]["path"],
                   "buddy": {key: document["context"]["buddy"][key]
                             for key in ("provider", "model", "effort")}}
        (root / (hashlib.sha256(session_id.encode()).hexdigest() + ".json")).write_text(json.dumps(binding))
        native = board.store.workflow.interrupted_native_facts(document["attemptId"])
        self.assertEqual(native["nativeSession"]["sessionId"], session_id)
        self.interrupted(board, first)
        failed = board.call("workflow_get", {"runId": submitted["runId"]})
        with board.store.db.read() as connection:
            result = json.loads(board.store._attempt_row(connection, document["attemptId"])["result_json"])["result"]
        self.assertEqual(result["nativeSession"]["sessionId"], session_id)
        self.continue_run(board, failed)
        resumed = self.claim(board, claim_request_id="c2")["claim"]["turn"]["input"]
        self.assertEqual(resumed["resumeMode"], "native-session")
        self.assertEqual(resumed["previousSessionId"], session_id)

    def test_dsh_unfinished_latest_record_reconstructs(self):
        self.executors["dsh"].native_resume = True
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        first = self.claim(board)
        document = first["claim"]["turn"]["input"]
        session_id = "unfinished-dsh-session"
        root = native_root(self.directory, "dsh", submitted["runId"])
        sessions = root / "sessions"
        sessions.mkdir(parents=True)
        record_path = sessions / "session.v3.jsonl"
        events = [
            {"type": "session", "version": 3, "id": session_id},
            {"type": "turn/end", "seq": 1, "data": {"turn": 1, "reason": {"kind": "completed"}}},
            {"type": "turn/start", "seq": 2, "data": {"turn": 2}},
        ]
        record_path.write_text("".join(json.dumps(item) + "\n" for item in events))
        record = session_record_facts(sessions, [session_id])
        self.assertIsNone(record)
        attempt_dir = attempt_root(self.directory, "dsh", submitted["runId"], document["attemptId"])
        attempt_dir.mkdir(parents=True)
        (attempt_dir / "dsh-session-record.json").write_text(json.dumps({
            "sessions": [session_id], "turnEndSeen": False, "truncated": False,
        }))
        self.assertEqual(board.store.workflow.interrupted_native_facts(document["attemptId"]), {})
        self.interrupted(board, first)
        failed = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, failed)
        resumed = self.claim(board, claim_request_id="c2")["claim"]["turn"]["input"]
        self.assertEqual(resumed["resumeMode"], "reconstructed-new-session")

    def assert_interrupted_reconstructs(self, harness):
        board = self.board()
        board.call("worker_register", {"workerId": "w1", "capabilities": [harness]})
        submitted = self.submit(board, harness=harness, provider="fixture", model="fixture", effort="high")
        first = self.claim(board)
        self.interrupted(board, first)
        failed = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, failed)
        resumed = self.claim(board, claim_request_id="c2")["claim"]["turn"]["input"]
        self.assertEqual(resumed["resumeMode"], "reconstructed-new-session")
        self.assertIsNone(resumed["previousSessionId"])
        self.assertEqual(resumed["context"]["interruptedTurn"]["evidence"]["kind"], "identity-absent")

    def test_zcode_app_reconstructs_interrupted_turn(self):
        self.assert_interrupted_reconstructs("zcode")

    def test_claude_cli_reconstructs_interrupted_turn(self):
        self.assert_interrupted_reconstructs("claude")


if __name__ == "__main__":
    import unittest
    unittest.main()
