"""Codex active-turn questions and signed root-tool correlation."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from hey_my_buddy.buddy.harnesses.codex import native_run
from hey_my_buddy.buddy.harnesses.codex.native_run import (
    _NativeInquiry, RunServices, run,
)
from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError, TurnEvidence
from hey_my_buddy.buddy.harnesses.in_turn_inquiry import IN_TURN_NOTE, prepare_inquiry_services
from hey_my_buddy.buddy.harnesses.inquiry_bridge import InquiryBridge
from hey_my_buddy.buddy.harnesses.session_receipts import sign_receipt
from hey_my_buddy.buddy.roles.worker_services import call_session_tool
from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE

from .test_native_run import FIXTURE, OUTCOME_SCHEMA, SeamCase


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.response = {"turnId": "turn-1"}

    def call(self, method, params):
        self.calls.append((method, params))
        return self.response


class NativeInquiryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-codex-inquiry-",
                                                dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        identity = {"taskId": "task-1", "attemptId": "attempt-1", "generation": 1, "turnId": "buddy-turn-1"}
        self.services = prepare_inquiry_services(
            invocation_root=root, identity=identity, input_sha256="a" * 64,
            attention_path=root / "attention.json", inquiry={"resultsPath": str(root / "inquiries.jsonl")})
        self.bridge = InquiryBridge(identity=identity, journal_path=str(root / "inquiries.jsonl"),
                                    error_factory=CodexProtocolError,
                                    event_metadata=lambda message: {"kind": message.get("method", "unknown")},
                                    limitation=IN_TURN_NOTE, delivery_mode="realtime")
        self.bridge.start()
        self.bridge.activate("thread-1")
        self.addCleanup(self.bridge.close)
        self.connection = FakeConnection()
        self.evidence = TurnEvidence("thread-1", "turn-1")
        self.native = _NativeInquiry(self.connection, self.evidence, "thread-1", "turn-1",
                                     self.services, self.bridge)

    def ask(self, inquiry_id="q1", question="What is the answer?"):
        response = self.bridge._ask({"inquiryId": inquiry_id, "question": question},
                                    lambda ok, **kwargs: {"ok": ok, **kwargs})
        self.assertTrue(response["ok"], response)

    def tool_frame(self, method, call_id="call-1", *, tool="hey_my_buddy_answer_inquiry",
                   thread="thread-1", turn="turn-1", status="completed", result=None):
        item = {"type": "mcpToolCall", "id": call_id, "server": self.services.server_name,
                "tool": tool, "status": status}
        if result is not None:
            item["result"] = result
        return {"method": method, "params": {"threadId": thread, "turnId": turn, "item": item}}

    def test_steer_active_turn_then_record_only_signed_root_answer(self):
        self.ask()
        self.native.poll()
        self.assertEqual(len(self.connection.calls), 1)
        method, params = self.connection.calls[0]
        self.assertEqual(method, "turn/steer")
        self.assertEqual(params["expectedTurnId"], "turn-1")
        self.assertIn("q1", params["input"][0]["text"])
        self.assertEqual(self.bridge.entries["q1"]["state"], "delivered")
        self.assertEqual(self.bridge.entries["q1"]["nativeCorrelation"]["turnId"], "turn-1")
        receipt = call_session_tool("hey_my_buddy_answer_inquiry",
                                    {"inquiryId": "q1", "answer": "42"},
                                    self.services.configuration)
        self.native.observe(self.tool_frame("item/started"))
        self.native.observe(self.tool_frame("item/completed", result=receipt))
        self.assertEqual(self.bridge.entries["q1"]["answer"]["text"], "42")
        self.assertEqual(self.bridge.report()["answered"], 1)

    def test_assistant_text_foreign_tool_and_changed_call_identity_cannot_answer(self):
        self.ask()
        self.native.poll()
        self.native.observe({"method": "item/completed", "params": {
            "threadId": "thread-1", "turnId": "turn-1",
            "item": {"type": "agentMessage", "id": "message-1", "text": "The answer is 42"}}})
        receipt = call_session_tool("hey_my_buddy_answer_inquiry",
                                    {"inquiryId": "q1", "answer": "42"}, self.services.configuration)
        self.native.observe(self.tool_frame("item/started", thread="child-thread"))
        self.native.observe(self.tool_frame("item/completed", thread="child-thread", result=receipt))
        self.assertEqual(self.bridge.entries["q1"]["state"], "delivered")
        self.native.observe(self.tool_frame("item/started", tool="hey_my_buddy_checkpoint"))
        with self.assertRaisesRegex(CodexProtocolError, "root call start"):
            self.native.observe(self.tool_frame("item/completed", result=receipt))
        self.assertEqual(self.bridge.entries["q1"]["state"], "delivered")

    def test_failed_tool_cannot_supply_success_answer(self):
        self.ask()
        self.native.poll()
        receipt = call_session_tool("hey_my_buddy_answer_inquiry",
                                    {"inquiryId": "q1", "answer": "42"}, self.services.configuration)
        self.native.observe(self.tool_frame("item/started"))
        failed = self.tool_frame("item/completed", status="failed")
        failed["params"]["item"]["error"] = {"message": receipt["content"][0]["text"]}
        with self.assertRaisesRegex(CodexProtocolError, "signed refusal"):
            self.native.observe(failed)
        self.assertEqual(self.bridge.entries["q1"]["state"], "delivered")

    def test_replayed_call_id_must_keep_the_same_answer(self):
        self.ask()
        self.native.poll()
        first = call_session_tool("hey_my_buddy_answer_inquiry",
                                  {"inquiryId": "q1", "answer": "42"}, self.services.configuration)
        self.native.observe(self.tool_frame("item/started"))
        self.native.observe(self.tool_frame("item/completed", result=first))
        self.native.observe(self.tool_frame("item/completed", result=first))
        altered = json.loads(first["content"][0]["text"])
        altered["answer"] = "43"
        altered.pop("signature")
        altered["signature"] = sign_receipt(altered, self.services.configuration["key"])
        changed = {"content": [{"type": "text", "text": json.dumps(altered)}]}
        with self.assertRaisesRegex(CodexProtocolError, "different answer"):
            self.native.observe(self.tool_frame("item/completed", result=changed))
        self.assertEqual(self.bridge.entries["q1"]["answer"]["text"], "42")

    def test_completed_turn_closes_delivery_and_late_answer_window(self):
        self.ask()
        self.evidence.completed = {"id": "turn-1", "status": "completed"}
        self.native.poll()
        self.assertEqual(self.connection.calls, [])
        self.bridge.close()
        self.assertEqual(self.bridge.entries["q1"]["state"], "unavailable")
        refusal = self.bridge._ask({"inquiryId": "q2", "question": "Too late"},
                                   lambda ok, **kwargs: {"ok": ok, **kwargs})
        self.assertEqual(refusal["error"], "not-ready")


class MountedWorkerTests(SeamCase):
    def test_worker_mounts_attempt_private_mcp_without_removing_user_configuration(self):
        FIXTURE.chmod(0o755)
        os.environ.update(self.environment)
        os.environ["HMB_CODEX_CLI"] = str(FIXTURE)
        os.environ["HMB_CODEX_FIXTURE_STATE"] = str(self.base / "trace.json")
        request = self.request(tool_scope="write", output_schema=OUTCOME_SCHEMA,
                               prompt="Work", turn_id="buddy-turn-1", input_sha256="a" * 64)
        root = Path(request.private_state.invocation_root)
        inquiry = prepare_inquiry_services(
            invocation_root=root, identity={"taskId": request.identity.task_id,
                                            "attemptId": request.identity.attempt_id,
                                            "generation": request.identity.generation,
                                            "turnId": request.identity.turn_id},
            input_sha256="a" * 64, attention_path=root / "attention.json",
            inquiry={"resultsPath": str(root / "inquiries.jsonl")})
        result = run(request, observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=RunServices(inquiry=inquiry), cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        trace = json.loads(Path(os.environ["HMB_CODEX_FIXTURE_STATE"]).read_text())
        thread = trace["threads"][result.native_identity.session_id]
        self.assertEqual(thread["start"]["config"]["mcp_servers"][inquiry.server_name], inquiry.mcp_server)
        self.assertTrue(any(ref.kind == "inquiry-report" for ref in result.evidence_refs))

    def test_fake_app_server_steers_active_turn_and_verifies_tool_or_retains_unanswered(self):
        FIXTURE.chmod(0o755)
        os.environ.update(self.environment)
        os.environ["HMB_CODEX_CLI"] = str(FIXTURE)
        for case, state in (("inquiry-active", "answered"), ("inquiry-unanswered", "delivered")):
            with self.subTest(case=case):
                self.set_case(case)
                os.environ["HMB_CODEX_FIXTURE_STATE"] = str(self.base / f"trace-{case}.json")
                request = self.request(tool_scope="write", output_schema=OUTCOME_SCHEMA,
                                       prompt="Work", turn_id="buddy-turn-1", input_sha256="a" * 64,
                                       timeout=10)
                root = Path(request.private_state.invocation_root)
                inquiry = prepare_inquiry_services(
                    invocation_root=root, identity={"taskId": request.identity.task_id,
                                                    "attemptId": request.identity.attempt_id,
                                                    "generation": request.identity.generation,
                                                    "turnId": request.identity.turn_id},
                    input_sha256="a" * 64, attention_path=root / "attention.json",
                    inquiry={"resultsPath": str(root / "inquiries.jsonl")})
                ready = threading.Event()
                captured = []
                original = native_run.InquiryBridge

                class CapturedBridge(original):
                    def activate(self, session_id):
                        super().activate(session_id)
                        captured.append(self)
                        ready.set()

                outcomes = []

                def execute():
                    try:
                        outcomes.append(run(request, observer=lambda _facts: FEEDBACK_CONTINUE,
                                            services=RunServices(inquiry=inquiry), cancelled=lambda: False))
                    except BaseException as error:
                        outcomes.append(error)

                with mock.patch.object(native_run, "InquiryBridge", CapturedBridge):
                    thread = threading.Thread(target=execute)
                    thread.start()
                    try:
                        self.assertTrue(ready.wait(5), "fake Codex worker did not admit the root turn")
                        response = captured[0]._ask({"inquiryId": "q1", "question": "What is 6 x 7?"},
                                                    lambda ok, **kwargs: {"ok": ok, **kwargs})
                        self.assertTrue(response["ok"], response)
                    finally:
                        thread.join(timeout=12)
                    self.assertFalse(thread.is_alive(), "fake Codex worker did not settle")
                self.assertEqual(len(outcomes), 1)
                if isinstance(outcomes[0], BaseException):
                    raise outcomes[0]
                result = outcomes[0]
                self.assertEqual(result.end.status, "ok", result.end.message)
                trace = json.loads(Path(os.environ["HMB_CODEX_FIXTURE_STATE"]).read_text())
                native_thread = trace["threads"][result.native_identity.session_id]
                self.assertEqual(native_thread["steer"]["expectedTurnId"], result.native_identity.turn_id)
                self.assertEqual(captured[0].entries["q1"]["state"],
                                 state if state == "answered" else "unavailable")
                report = native_run._read_evidence_ref(result, "inquiry-report")
                self.assertEqual(report["answered"], 1 if state == "answered" else 0)
                self.assertEqual(report["unansweredDelivered"], 0 if state == "answered" else 1)
                from hey_my_buddy.buddy.roles.run_execution import worker_delivery
                outcome, provenance, _truncated = worker_delivery(result)
                self.assertEqual(outcome["disposition"], "completed" if state == "answered" else "attention")
                self.assertIsNone(native_run.validate_turn_provenance({
                    "sessionId": result.native_identity.session_id, "provenance": provenance,
                    "outcome": outcome, "resumeMode": "initial", "previousSessionId": None}))


if __name__ == "__main__":
    unittest.main()
