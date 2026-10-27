"""A native answer reaches the board when its execution result is submitted."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from admission import admit_execution
from support import BoardTestCase

from hey_my_buddy.buddy.harnesses.codex.native_run import _NativeInquiry, prepare_inquiry_services
from hey_my_buddy.buddy.harnesses.codex.protocol import Connection, CodexProtocolError, TurnEvidence
from hey_my_buddy.buddy.harnesses.inquiry_bridge import InquiryBridge
from hey_my_buddy.buddy.roles.worker_services import ANSWER_SCHEMA


PEER = Path(__file__).parent / "fixtures" / "inquiry_result_peer.py"
QUESTION = "Has the work finished?"
ANSWER = "The work is complete."
NONCE = "i" * 32


class InquiryResultImportTests(BoardTestCase):
    def exercise_final_answer(self, sampled_state: str) -> None:
        board = self.board()
        task = board.submit_execution(requestId=f"inquiry-result-{sampled_state}", task="fixture work",
                                      cwd=str(self.workdir()), adapter="command", argv=["true"])["task"]
        attempt = admit_execution(board.client(), NONCE, task_id=task["runId"])["claim"]["attempt"]
        task_id, attempt_id = task["runId"], attempt["attemptId"]
        identity = {"taskId": task_id, "attemptId": attempt_id,
                    "generation": attempt["generation"], "turnId": None}
        journal = self.directory / "attempts" / task_id / attempt_id / "inquiry.results.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        services = prepare_inquiry_services(
            invocation_root=journal.parent, identity=identity, input_sha256="a" * 64,
            attention_path=journal.parent / "attention.json", inquiry={"resultsPath": str(journal)},
            answer_schema=ANSWER_SCHEMA)
        bridge = InquiryBridge(identity=identity, journal_path=str(journal),
                               error_factory=CodexProtocolError,
                               event_metadata=lambda message: {"kind": message.get("method", "unknown")},
                               delivery_mode="realtime", checkpoint_tool=None)
        bridge.start()
        bridge.activate("native-thread-1")
        self.addCleanup(bridge.close)

        # The service has posted the question before the native answer exists.
        board.store._message_post(task_id, "q1", QUESTION)
        queued = bridge._ask({"inquiryId": "q1", "question": QUESTION},
                             lambda ok, **fields: {"ok": ok, **fields})
        self.assertTrue(queued["ok"], queued)
        if sampled_state == "queued":
            before = board.store.message_get({"runId": task_id, "inquiryId": "q1"})["message"]
            self.assertEqual(before["state"], "queued")
            self.assertIsNone(before["answer"])

        stderr = open(self.directory / "fake-app-server.stderr", "wb")
        self.addCleanup(stderr.close)
        peer = subprocess.Popen([sys.executable, str(PEER)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=stderr,
                                env=self.child_environment())
        self.children = [peer]
        connection = Connection(peer, time.monotonic() + 5, threading.Event())
        evidence = TurnEvidence("native-thread-1", "native-turn-1")
        native = _NativeInquiry(connection, evidence, "native-thread-1", "native-turn-1",
                                services, bridge, None)
        connection.on_request = lambda request: connection.send({
            "id": request["id"], "result": native.answer(request["params"])})
        native.poll()
        if sampled_state == "delivered":
            # A second service sample sees native delivery, then stops sampling.
            board.store._message_update(task_id, "q1", actor="bridge", state="delivered")
            before = board.store.message_get({"runId": task_id, "inquiryId": "q1"})["message"]
            self.assertEqual(before["state"], "delivered")
            self.assertIsNone(before["answer"])
        while bridge.entries["q1"]["state"] != "answered":
            connection.pump()
        self.assertEqual(peer.wait(timeout=5), 0,
                         (self.directory / "fake-app-server.stderr").read_text(errors="replace"))
        self.assertEqual([json.loads(line)["state"] for line in journal.read_text().splitlines()],
                         ["queued", "delivered", "answered"])
        self.assertEqual(bridge.entries["q1"]["answer"]["text"], ANSWER)

        evidence.completed = {"id": "native-turn-1", "status": "completed"}
        bridge.close()
        board.call("execution_result", {"attemptId": attempt_id, "generation": attempt["generation"],
                                      "nonce": NONCE, "status": "ok", "shutdownConfirmed": True})
        # No inquiry observe or second Host request may be needed to see this.
        message = board.store.message_get({"runId": task_id, "inquiryId": "q1"})["message"]
        self.assertEqual(message["state"], "answered")
        self.assertIsNone(message["reason"])
        self.assertEqual(message["answer"]["text"], ANSWER)
        self.assertEqual(message["answer"]["toolCallId"], "answer-call")
        self.assertEqual(message["delivery"]["nativeCorrelation"]["turnId"], "native-turn-1")

    def test_answer_imports_after_last_queued_sample(self):
        self.exercise_final_answer("queued")

    def test_answer_imports_after_last_delivered_sample(self):
        self.exercise_final_answer("delivered")
