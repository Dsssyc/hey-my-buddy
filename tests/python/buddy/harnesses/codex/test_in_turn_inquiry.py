"""Codex active-turn questions and current root client-tool correlation."""
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
    _NativeInquiry, RunServices, prepare_inquiry_services, run,
)
from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError, TurnEvidence
from hey_my_buddy.buddy.harnesses.inquiry_bridge import InquiryBridge
from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE, RunContinuation
from hey_my_buddy.buddy.roles.worker_services import ANSWER_SCHEMA

from .test_native_run import FIXTURE, OUTCOME_SCHEMA, SeamCase


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.sent = []
        self.response = {"turnId": "turn-1"}

    def call(self, method, params, *, on_sent):
        self.calls.append((method, params))
        on_sent()
        return self.response

    def send(self, message):
        self.sent.append(message)


class NativeInquiryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-codex-inquiry-",
                                                dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        identity = {"taskId": "task-1", "attemptId": "attempt-1", "generation": 1, "turnId": "buddy-turn-1"}
        self.services = prepare_inquiry_services(
            invocation_root=self.root, identity=identity, input_sha256="a" * 64,
            attention_path=self.root / "attention.json", inquiry={"resultsPath": str(self.root / "inquiries.jsonl")},
            answer_schema=ANSWER_SCHEMA)
        self.bridge = InquiryBridge(identity=identity, journal_path=str(self.root / "inquiries.jsonl"),
                                    error_factory=CodexProtocolError,
                                    event_metadata=lambda message: {"kind": message.get("method", "unknown")},
                                    delivery_mode="realtime", checkpoint_tool=None)
        self.bridge.start()
        self.bridge.activate("thread-1")
        self.addCleanup(self.bridge.close)
        self.connection = FakeConnection()
        self.evidence = TurnEvidence("thread-1", "turn-1")
        self.native = _NativeInquiry(self.connection, self.evidence, "thread-1", "turn-1",
                                     self.services, self.bridge, mock.Mock())

    def ask(self):
        response = self.bridge._ask({"inquiryId": "q1", "question": "What is the answer?"},
                                    lambda ok, **kwargs: {"ok": ok, **kwargs})
        self.assertTrue(response["ok"], response)

    def call(self, **overrides):
        return self.native.answer({"threadId": "thread-1", "turnId": "turn-1", "callId": "call-1",
                                   "tool": "hey_my_buddy_answer_inquiry",
                                   "arguments": {"inquiryId": "q1", "answer": "42"}, **overrides})

    def test_factory_declares_only_answer_without_creating_configuration(self):
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.services.dynamic_tools, [{
            "type": "function", "name": "hey_my_buddy_answer_inquiry",
            "description": "Answer a Host inquiry delivered in the current root turn, then continue the assigned work.",
            "inputSchema": ANSWER_SCHEMA}])
        self.assertEqual(set(self.services.configuration), {"identity", "attentionPath", "inquiryJournalPath"})

    def test_steer_active_turn_then_record_root_client_answer(self):
        self.ask()
        self.native.poll()
        method, params = self.connection.calls[0]
        self.assertEqual(method, "turn/steer")
        self.assertEqual(params["expectedTurnId"], "turn-1")
        self.assertIn("hey_my_buddy_answer_inquiry", params["input"][0]["text"])
        self.assertEqual(self.bridge.entries["q1"]["nativeCorrelation"]["turnId"], "turn-1")
        self.assertTrue(self.call()["success"])
        self.assertEqual(self.bridge.entries["q1"]["answer"]["text"], "42")
        self.assertEqual(self.bridge.entries["q1"]["answer"]["toolCallId"], "call-1")
        self.assertFalse(self.call(callId="call-2")["success"])
        self.assertEqual(self.bridge.report()["answered"], 1)

    def test_invalid_calls_do_not_answer_or_end_the_turn_and_can_be_corrected(self):
        self.ask()
        self.assertFalse(self.call()["success"])  # queued, not delivered
        self.native.poll()
        invalid = [{"threadId": "child-thread"}, {"turnId": "old-turn"}, {"callId": ""},
                   {"callId": "x" * 257}, {"callId": "\ud800"}, {"tool": "hey_my_buddy_checkpoint"}, {"namespace": "other"},
                   {"arguments": None}, {"arguments": []}, {"arguments": '{"inquiryId":"q1","answer":"42"}'},
                   {"arguments": {"inquiryId": "q1", "answer": "42", "extra": True}},
                   {"arguments": {"inquiryId": "q1", "answer": " "}},
                   {"arguments": {"inquiryId": "q1", "answer": "\ud800"}},
                   {"arguments": {"inquiryId": "\ud800", "answer": "42"}},
                   {"arguments": {"inquiryId": "q1", "answer": "答" * 1334}},
                   {"arguments": {"inquiryId": "问" * 43, "answer": "42"}},
                   {"arguments": {"inquiryId": "unknown", "answer": "42"}}]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                result = self.call(**overrides)
                self.assertFalse(result["success"])
                self.assertEqual(result["contentItems"][0]["type"], "inputText")
                self.assertEqual(self.bridge.entries["q1"]["state"], "delivered")
                self.assertIsNone(self.evidence.completed)
        self.assertTrue(self.call()["success"])

    def test_journal_failure_does_not_report_or_record_an_answer_and_can_retry(self):
        self.ask()
        self.native.poll()
        with mock.patch.object(self.bridge, "_journal", return_value=False):
            result = self.call()
        self.assertFalse(result["success"])
        self.assertEqual(self.bridge.entries["q1"]["state"], "delivered")
        self.assertIsNone(self.evidence.completed)
        self.assertTrue(self.call()["success"])

    def test_no_active_or_completed_root_and_unknown_methods_are_refused_separately(self):
        chain = mock.Mock()
        facts = native_run._RunFacts()
        request = mock.Mock(capture_evidence=False)
        state = native_run._RunState()
        native_run._wire(self.connection, chain, facts, lambda _facts: FEEDBACK_CONTINUE,
                         request, state, "worker", [])
        self.connection.on_request({"id": "inactive", "method": "item/tool/call",
                                    "params": {"tool": "hey_my_buddy_answer_inquiry"}})
        self.assertFalse(self.connection.sent[-1]["result"]["success"])
        self.assertEqual(facts.denied, [])
        self.connection.on_request({"id": "unknown", "method": "unknown/method", "params": {}})
        self.assertEqual(self.connection.sent[-1]["error"]["code"], -32601)
        self.assertEqual(len(facts.denied), 1)
        self.connection.on_request({"id": "foreign-tool", "method": "item/tool/call",
                                    "params": {"tool": "other_tool"}})
        self.assertEqual(self.connection.sent[-1]["error"]["code"], -32601)
        self.assertEqual(len(facts.denied), 2)
        self.ask()
        self.native.poll()
        self.evidence.completed = {"id": "turn-1", "status": "completed"}
        self.assertFalse(self.call()["success"])
        self.bridge.close()
        self.assertEqual(self.bridge.entries["q1"]["state"], "unavailable")


class ClientToolWorkerTests(SeamCase):
    def services(self, request):
        root = Path(request.private_state.invocation_root)
        return prepare_inquiry_services(
            invocation_root=root, identity={"taskId": request.identity.task_id,
                "attemptId": request.identity.attempt_id, "generation": request.identity.generation,
                "turnId": request.identity.turn_id}, input_sha256="a" * 64,
            attention_path=root / "attention.json", inquiry={"resultsPath": str(root / "inquiries.jsonl")},
            answer_schema=ANSWER_SCHEMA)

    def test_worker_declares_tool_and_native_resume_reuses_thread_declaration(self):
        os.environ.update(self.environment,
                          HMB_CODEX_FIXTURE_STATE=str(self.base / "trace.json"))
        request = self.request(permission="write", output_schema=OUTCOME_SCHEMA, prompt="Work",
                               turn_id="buddy-turn-1", input_sha256="a" * 64, native="native")
        inquiry = self.services(request)
        result = run(request, observer=lambda _facts: FEEDBACK_CONTINUE,
                     services=RunServices(inquiry=inquiry), cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        next_request = self.request(permission="write", output_schema=OUTCOME_SCHEMA, prompt="Continue",
            turn_id="buddy-turn-2", input_sha256="a" * 64, native="native",
            continuation=RunContinuation(mode="native-session", previous_session_id=result.native_identity.session_id))
        resumed = run(next_request, observer=lambda _facts: FEEDBACK_CONTINUE,
                      services=RunServices(inquiry=self.services(next_request)), cancelled=lambda: False)
        self.assertEqual(resumed.end.status, "ok", resumed.end.message)
        trace = json.loads(Path(os.environ["HMB_CODEX_FIXTURE_STATE"]).read_text(encoding="utf-8"))
        thread = trace["threads"][result.native_identity.session_id]
        self.assertTrue(thread["initialize"]["capabilities"]["experimentalApi"])
        self.assertEqual(thread["start"]["dynamicTools"], inquiry.dynamic_tools)
        self.assertNotIn("config", thread["start"])
        self.assertNotIn("dynamicTools", thread["resumeRequests"][0])
        self.assertFalse((Path(request.private_state.invocation_root) / "inquiry-bridge.json").exists())

    def test_fake_app_server_answers_or_keeps_unanswered_and_invalid_calls_continue(self):
        os.environ.update(self.environment)
        for case, answered in (("inquiry-active", True), ("inquiry-unanswered", False), ("inquiry-invalid-retry", True)):
            with self.subTest(case=case):
                self.set_case(case)
                os.environ["HMB_CODEX_FIXTURE_STATE"] = str(self.base / f"trace-{case}.json")
                request = self.request(permission="write", output_schema=OUTCOME_SCHEMA, prompt="Work",
                                       turn_id="buddy-turn-1", input_sha256="a" * 64, timeout=10)
                ready, captured, outcomes = threading.Event(), [], []
                original = native_run.InquiryBridge
                class CapturedBridge(original):
                    def activate(self, session_id):
                        super().activate(session_id)
                        captured.append(self)
                        ready.set()
                def execute():
                    try:
                        outcomes.append(run(request, observer=lambda _facts: FEEDBACK_CONTINUE,
                            services=RunServices(inquiry=self.services(request)), cancelled=lambda: False))
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
                if isinstance(outcomes[0], BaseException):
                    raise outcomes[0]
                result = outcomes[0]
                self.assertEqual(result.end.status, "ok", result.end.message)
                trace = json.loads(Path(os.environ["HMB_CODEX_FIXTURE_STATE"]).read_text(encoding="utf-8"))
                native_thread = trace["threads"][result.native_identity.session_id]
                self.assertEqual(native_thread["steer"]["expectedTurnId"], result.native_identity.turn_id)
                if case == "inquiry-invalid-retry":
                    self.assertEqual([reply["result"]["success"] for reply in native_thread["clientReplies"]],
                                     [False, False, False, False, True, False])
                report = native_run._read_evidence_ref(result, "inquiry-report")
                self.assertEqual(report["answered"], int(answered))
                self.assertEqual(report["unansweredDelivered"], int(not answered))
                self.assertFalse(any(ref.kind == "inquiry-tool-failures" for ref in result.evidence_refs))
                from hey_my_buddy.buddy.roles.run_execution import worker_delivery
                outcome, provenance = worker_delivery(result)
                self.assertEqual(outcome["disposition"], "completed" if answered else "attention")
                self.assertIsNone(native_run.validate_turn_provenance({
                    "sessionId": result.native_identity.session_id, "provenance": provenance,
                    "outcome": outcome, "resumeMode": "initial", "previousSessionId": None}))
