"""Strict receipt and native provenance checks independent of process fixtures."""
import errno
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.buddy.harnesses.run_contract import RunBuddy
from hey_my_buddy.buddy.harnesses.session_receipts import ReceiptError, sign_receipt
from hey_my_buddy.buddy.roles.session_mcp import respond
from hey_my_buddy.buddy.harnesses.zcode.protocol import (
    ActivityProjection,
    NativeConnection,
    NativeError,
    NativeRpcError,
    RootTurnEvidence,
    ZcodeAttemptUsage,
    decode_native_failure,
    verify_inquiry_receipt,
    verify_receipt,
    verify_tool_refusal,
    optional_native_call,
)
from hey_my_buddy.buddy.harnesses.zcode.native_run import catalog, configure_session, execution_deadline
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.errors import BoardError

# The session tools one run actually mounts; refusal envelopes verify only
# against this set, never a fixed global one.
MOUNTED_TOOLS = ("hey_my_buddy_answer_inquiry", "hey_my_buddy_finish_turn")
from hey_my_buddy.buddy.roles.turn_io import validate_outcome


FALLBACK_PREFERENCES = {"memoryEnabled": {"value": False, "source": "fallback"},
                        "nativeSearchEnhancementsEnabled": {"value": True, "source": "fallback"}}


class ActivityReasonTests(unittest.TestCase):
    def test_native_authority_requests_publish_structured_reason(self):
        activity = ActivityProjection('session-1')
        for kind, expected in (('permission.requested', 'permission'), ('userInput.requested', 'user-input')):
            activity.note({'method': 'session/event', 'params': {'type': kind, 'payload': {}}}, 1)
            self.assertEqual(activity.payload()['waitingReason'],
                             {'code': 'native-authority-required', 'params': {'kind': expected}})


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="zcode-receipt-attention-")
        self.addCleanup(temporary.cleanup)
        self.bridge = {"identity": {"taskId": "micro-task", "attemptId": "attempt", "generation": 1, "turnId": "logical"},
                       "inputSha256": "a" * 64, "key": "b" * 64,
                       "attentionPath": str(Path(temporary.name) / "absent-attention.json")}
        self.outcome = {"disposition": "completed", "summary": "done", "remaining": [], "decisions": [], "artifacts": [], "request": None}

    def receipt(self):
        result = respond({"id": 1, "method": "tools/call", "params": {"name": "hey_my_buddy_finish_turn", "arguments": self.outcome}}, self.bridge)
        self.assertNotIn("structuredContent", result["result"])
        return result["result"]["content"][0]["text"]

    def test_wrong_input_and_reordered_events_cannot_establish_a_root(self):
        tracker = RootTurnEvidence("sess-root", "input-root", "finish", self.bridge,
                                          validate_outcome=validate_outcome, mounted_tools=("hey_my_buddy_finish_turn",))
        def event(seq, input_id):
            return {"method": "session/event", "params": {"sessionId": "sess-root", "turnId": "native-turn", "seq": seq,
                    "type": "turn.started", "payload": {"inputId": input_id}}}
        with self.assertRaises(NativeError):
            tracker.observe(event(1, "wrong-input"), 1)
        tracker = RootTurnEvidence("sess-root", "input-root", "finish", self.bridge,
                                          validate_outcome=validate_outcome, mounted_tools=("hey_my_buddy_finish_turn",))
        tracker.observe(event(2, "input-root"), 1)
        with self.assertRaises(NativeError):
            tracker.observe(event(1, "input-root"), 2)

    def test_root_relayed_child_finish_does_not_consume_root_receipt(self):
        tracker = RootTurnEvidence("sess-root", "input-root", "finish", self.bridge,
                                          validate_outcome=validate_outcome, mounted_tools=("hey_my_buddy_finish_turn",))
        tracker.observe({"method": "session/event", "params": {"sessionId": "sess-root", "turnId": "native-turn", "seq": 1,
                        "type": "turn.started", "payload": {"inputId": "input-root"}}}, 1)
        tracker.observe({"method": "session/event", "params": {"sessionId": "sess-root", "turnId": "native-turn", "seq": 2,
                        "type": "tool.updated", "payload": {"kind": "scheduled", "toolName": "finish", "toolCallId": "child", "source": "subagent"}}}, 2)
        self.assertIsNone(tracker.call_id)
        self.assertIsNone(tracker.receipt)

    def root_event(self, tracker, seq, kind, payload, **identity):
        tracker.observe({"method": "session/event", "params": {
            "sessionId": "sess-root", "turnId": "native-turn", "seq": seq,
            "type": kind, "payload": payload, **identity,
        }}, seq)

    def started_tracker(self):
        tracker = RootTurnEvidence("sess-root", "input-root", "finish", self.bridge,
                                          validate_outcome=validate_outcome, mounted_tools=("hey_my_buddy_finish_turn",))
        self.root_event(tracker, 1, "turn.started", {"inputId": "input-root"})
        return tracker

    def test_nonobject_ordinary_events_are_retained_and_root_receipt_can_complete(self):
        unknown = []
        tracker = RootTurnEvidence("sess-root", "input-root", "finish", self.bridge,
                                   validate_outcome=validate_outcome, mounted_tools=("hey_my_buddy_finish_turn",),
                                   on_unknown=unknown.append)
        self.root_event(tracker, 1, "turn.started", {"inputId": "input-root"})
        for seq, payload in enumerate(("secret prompt text", ["secret", "content"], None), start=2):
            self.root_event(tracker, seq, "message.upserted", payload)
        self.root_event(tracker, 5, "tool.updated", {"kind": "scheduled", "toolName": "finish", "toolCallId": "finish-1"})
        self.root_event(tracker, 6, "tool.updated", {"kind": "result", "toolCallId": "finish-1",
            "result": {"success": True, "truncated": False, "content": self.receipt()}})
        self.root_event(tracker, 7, "turn.completed", {"inputId": "input-root", "resultType": "success"})
        tracker.observe({"method": "state.updated", "params": {"sessionId": "sess-root", "reason": "prompt_completed"}}, 8)
        tracker.close_ordinal = 9
        self.assertEqual(tracker.provenance()["toolCallId"], "finish-1")
        self.assertEqual(unknown, ["event-payload/message.upserted/string",
                                   "event-payload/message.upserted/array",
                                   "event-payload/message.upserted/null"])
        self.assertNotIn("secret", str(unknown))

    def test_nonobject_evidence_events_keep_identity_and_completion_guards(self):
        for kind, payload, expected in (("turn.started", [], "wrong-native-turn"),
                                        ("turn.completed", None, "invalid-provenance"),
                                        ("turn.failed", "provider secret", "native-turn-failed"),
                                        ("tool.updated", [], "invalid-provenance")):
            with self.subTest(kind=kind):
                unknown = []
                tracker = RootTurnEvidence("sess-root", "input-root", "finish", self.bridge,
                                           validate_outcome=validate_outcome,
                                           mounted_tools=("hey_my_buddy_finish_turn",), on_unknown=unknown.append)
                if kind != "turn.started":
                    self.root_event(tracker, 1, "turn.started", {"inputId": "input-root"})
                if kind == "tool.updated":
                    self.root_event(tracker, 2, kind, {"kind": "scheduled", "toolName": "finish", "toolCallId": "finish-1"})
                with self.assertRaises(NativeError) as error:
                    self.root_event(tracker, 3, kind, payload)
                self.assertEqual(error.exception.code, expected)
                self.assertEqual(len(unknown), 1)
                self.assertTrue(unknown[0].startswith(f"event-payload/{kind}/"))
                self.assertNotIn("secret", str(unknown))

    def test_unrecognized_tool_payload_without_pending_receipt_is_a_fact(self):
        unknown = []
        tracker = RootTurnEvidence("sess-root", "input-root", "finish", self.bridge,
                                   validate_outcome=validate_outcome, mounted_tools=("hey_my_buddy_finish_turn",),
                                   on_unknown=unknown.append)
        self.root_event(tracker, 1, "turn.started", {"inputId": "input-root"})
        self.root_event(tracker, 2, "tool.updated", [])
        self.assertEqual(unknown, ["event-payload/tool.updated/array"])
        self.assertIsNone(tracker.receipt)
        with self.assertRaises(NativeError) as error:
            self.root_event(tracker, 3, "turn.completed", {"inputId": "input-root", "resultType": "success"})
        self.assertEqual(error.exception.code, "missing-finish")

    def test_completion_duplicate_and_wrong_input_remain_provenance_failures(self):
        for completed in (False, True):
            with self.subTest(completed=completed):
                tracker = self.started_tracker()
                self.root_event(tracker, 2, "tool.updated", {"kind": "scheduled", "toolName": "finish", "toolCallId": "finish-1"})
                self.root_event(tracker, 3, "tool.updated", {"kind": "result", "toolCallId": "finish-1",
                    "result": {"success": True, "truncated": False, "content": self.receipt()}})
                if completed:
                    self.root_event(tracker, 4, "turn.completed", {"inputId": "input-root", "resultType": "success"})
                with self.assertRaises(NativeError) as error:
                    self.root_event(tracker, 5, "turn.completed", {"inputId": "input-root" if completed else "wrong",
                                                                    "resultType": "success"})
                self.assertEqual(error.exception.code, "invalid-provenance")
        tracker = self.started_tracker()
        with self.assertRaises(NativeError) as error:
            self.root_event(tracker, 2, "turn.completed", {"inputId": "input-root", "resultType": "failure"})
        self.assertEqual(error.exception.code, "native-turn-failed")

    def test_verified_delivery_keys_come_only_from_the_verified_root_and_call(self):
        tracker = self.started_tracker()
        scheduled = {"kind": "scheduled", "toolName": "finish", "toolCallId": "shared"}
        result = {"kind": "result", "toolCallId": "shared",
                  "result": {"success": True, "truncated": False, "content": self.receipt()}}
        self.root_event(tracker, 2, "tool.updated", scheduled, sessionId="foreign")
        self.root_event(tracker, 3, "tool.updated", result, sessionId="foreign")
        self.assertEqual(tracker.verified_delivery(), frozenset())
        self.root_event(tracker, 2, "tool.updated", scheduled)
        self.assertEqual(tracker.verified_delivery(), frozenset())
        self.root_event(tracker, 3, "tool.updated", result)
        identity = {"sessionId": "sess-root", "turnId": "native-turn"}
        self.assertEqual(tracker.verified_delivery(), frozenset({(canonical_json(identity), "shared")}))

    def test_failed_finish_can_retry_with_one_successful_root_receipt(self):
        failures = ({"kind": "error", "error": "The required parameter request is missing"},
                    {"kind": "result", "result": {"success": False, "truncated": False, "content": "invalid outcome"}})
        for failure in failures:
            with self.subTest(kind=failure["kind"]):
                tracker = self.started_tracker()
                self.root_event(tracker, 2, "tool.updated", {"kind": "scheduled", "toolName": "finish", "toolCallId": "bad"})
                self.root_event(tracker, 3, "tool.updated", {"toolCallId": "bad", **failure})
                self.assertIsNone(tracker.receipt)
                self.root_event(tracker, 4, "tool.updated", {"kind": "scheduled", "toolName": "Bash", "toolCallId": "repair"})
                self.root_event(tracker, 5, "tool.updated", {"kind": "scheduled", "toolName": "finish", "toolCallId": "corrected"})
                self.root_event(tracker, 6, "tool.updated", {"kind": "result", "toolCallId": "corrected",
                    "result": {"success": True, "truncated": False, "content": self.receipt()}})
                self.root_event(tracker, 7, "turn.completed", {"inputId": "input-root", "resultType": "success"})
                tracker.observe({"method": "state.updated", "params": {"sessionId": "sess-root", "reason": "prompt_completed"}}, 8)
                tracker.close_ordinal = 9
                provenance = tracker.provenance()
                self.assertEqual(provenance["toolCallId"], "corrected")
                self.assertEqual(provenance["toolCallSeq"], 5)
                self.assertTrue(provenance["receiptVerified"])

    def test_failed_finish_does_not_allow_child_receipts_or_final_prose(self):
        for identity, source in (({"sessionId": "child"}, {}), ({"turnId": "other-turn"}, {}),
                                 ({}, {"source": "subagent"})):
            with self.subTest(identity=identity, source=source):
                tracker = self.started_tracker()
                self.root_event(tracker, 2, "tool.updated", {"kind": "scheduled", "toolName": "finish", "toolCallId": "bad"})
                self.root_event(tracker, 3, "tool.updated", {"kind": "error", "toolCallId": "bad"})
                self.root_event(tracker, 4, "tool.updated", {"kind": "scheduled", "toolName": "finish", "toolCallId": "child", **source}, **identity)
                self.root_event(tracker, 5, "tool.updated", {"kind": "result", "toolCallId": "child", **source,
                    "result": {"success": True, "truncated": False, "content": self.receipt()}}, **identity)
                with self.assertRaises(NativeError) as error:
                    self.root_event(tracker, 6, "turn.completed", {"inputId": "input-root", "resultType": "success", "response": self.receipt()})
                self.assertEqual(error.exception.code, "finish-tool-failed")
                self.assertIsNone(tracker.receipt)

    def test_successful_finish_cannot_be_replaced_by_a_later_error(self):
        tracker = self.started_tracker()
        self.root_event(tracker, 2, "tool.updated", {"kind": "scheduled", "toolName": "finish", "toolCallId": "accepted"})
        self.root_event(tracker, 3, "tool.updated", {"kind": "result", "toolCallId": "accepted",
            "result": {"success": True, "truncated": False, "content": self.receipt()}})
        accepted = tracker.receipt
        with self.assertRaises(NativeError):
            self.root_event(tracker, 4, "tool.updated", {"kind": "error", "toolCallId": "accepted"})
        self.assertIs(tracker.receipt, accepted)
        with self.assertRaises(NativeError):
            self.root_event(tracker, 5, "tool.updated", {"kind": "scheduled", "toolName": "finish", "toolCallId": "duplicate"})

    def test_a_wrapper_successful_refusal_result_recovers_for_a_corrected_retry(self):
        # The incident shape: the MCP isError refusal (a signed envelope) is
        # surfaced by the native wrapper as a successful result. Only the fully
        # verified envelope recovers the turn; the corrected call then succeeds.
        finish = "mcp__buddy_x__hey_my_buddy_finish_turn"
        refusal = json.dumps(self._signed_refusal())
        tracker = RootTurnEvidence("sess-root", "input-root", finish, self.bridge,
                                          validate_outcome=validate_outcome, mounted_tools=("hey_my_buddy_finish_turn",))
        self.root_event(tracker, 1, "turn.started", {"inputId": "input-root"})
        self.root_event(tracker, 2, "tool.updated", {"kind": "scheduled", "toolName": finish, "toolCallId": "bad"})
        self.root_event(tracker, 3, "tool.updated", {"kind": "result", "toolCallId": "bad",
            "result": {"success": True, "truncated": False, "content": refusal}})
        self.assertIsNone(tracker.receipt)
        self.assertTrue(tracker.finish_failed)
        self.root_event(tracker, 4, "tool.updated", {"kind": "scheduled", "toolName": finish, "toolCallId": "corrected"})
        self.root_event(tracker, 5, "tool.updated", {"kind": "result", "toolCallId": "corrected",
            "result": {"success": True, "truncated": False, "content": self.receipt()}})
        self.root_event(tracker, 6, "turn.completed", {"inputId": "input-root", "resultType": "success"})
        tracker.observe({"method": "state.updated", "params": {"sessionId": "sess-root", "reason": "prompt_completed"}}, 7)
        tracker.close_ordinal = 8
        self.assertEqual(tracker.provenance()["toolCallId"], "corrected")

    def test_an_unverified_refusal_shaped_finish_result_stays_fatal(self):
        finish = "mcp__buddy_x__hey_my_buddy_finish_turn"
        for content in (json.dumps({"kind": "tool-refusal", "detail": "unsigned prose"}),
                        json.dumps(self._signed_refusal(tool="hey_my_buddy_checkpoint")),
                        json.dumps(self._signed_refusal(identity={"taskId": "micro-task", "attemptId": "other",
                                                                 "generation": 1, "turnId": "logical"}))):
            with self.subTest(content=content[:60]):
                tracker = RootTurnEvidence("sess-root", "input-root", finish, self.bridge,
                                          validate_outcome=validate_outcome, mounted_tools=("hey_my_buddy_finish_turn",))
                self.root_event(tracker, 1, "turn.started", {"inputId": "input-root"})
                self.root_event(tracker, 2, "tool.updated", {"kind": "scheduled", "toolName": finish, "toolCallId": "bad"})
                with self.assertRaises(ReceiptError) as error:
                    self.root_event(tracker, 3, "tool.updated", {"kind": "result", "toolCallId": "bad",
                        "result": {"success": True, "truncated": False, "content": content}})
                self.assertEqual(error.exception.code, "invalid-tool-refusal")
                self.assertIsNone(tracker.receipt)

    def test_a_failed_marker_result_verifies_the_refusal_envelope_first(self):
        # A result marked success:false still examines a refusal-shaped content
        # before any ordinary retryable-error path, so the same envelope both
        # recovers when genuine and fails the turn when forged or wrong-tool.
        finish = "mcp__buddy_x__hey_my_buddy_finish_turn"
        header = "MCP tool returned an error:\n"
        verified = header + json.dumps(self._signed_refusal())
        tracker = RootTurnEvidence("sess-root", "input-root", finish, self.bridge,
                                          validate_outcome=validate_outcome, mounted_tools=("hey_my_buddy_finish_turn",))
        self.root_event(tracker, 1, "turn.started", {"inputId": "input-root"})
        self.root_event(tracker, 2, "tool.updated", {"kind": "scheduled", "toolName": finish, "toolCallId": "bad"})
        self.root_event(tracker, 3, "tool.updated", {"kind": "result", "toolCallId": "bad",
            "result": {"success": False, "truncated": False, "content": verified}})
        self.assertTrue(tracker.finish_failed)
        self.assertIsNone(tracker.receipt)
        for content in (header + json.dumps({**self._signed_refusal(), "detail": "changed after signing"}),
                        header + json.dumps(self._signed_refusal(tool="hey_my_buddy_answer_inquiry"))):
            with self.subTest(content=content[:60]):
                tracker = RootTurnEvidence("sess-root", "input-root", finish, self.bridge,
                                          validate_outcome=validate_outcome, mounted_tools=("hey_my_buddy_finish_turn",))
                self.root_event(tracker, 1, "turn.started", {"inputId": "input-root"})
                self.root_event(tracker, 2, "tool.updated", {"kind": "scheduled", "toolName": finish, "toolCallId": "bad"})
                with self.assertRaises(ReceiptError) as error:
                    self.root_event(tracker, 3, "tool.updated", {"kind": "result", "toolCallId": "bad",
                        "result": {"success": False, "truncated": False, "content": content}})
                self.assertEqual(error.exception.code, "invalid-tool-refusal")
                self.assertIsNone(tracker.receipt)

    def _signed_refusal(self, *, tool: str = "hey_my_buddy_finish_turn", reason: str = "invalid-arguments",
                        detail: str = "the turn request references are invalid", identity: dict | None = None) -> dict:
        envelope = {"version": 1, "kind": "tool-refusal", "identity": identity or self.bridge["identity"],
                    "inputSha256": self.bridge["inputSha256"], "tool": tool, "reason": reason,
                    "detail": detail, "receiptId": "f" * 32}
        envelope["signature"] = sign_receipt(envelope, self.bridge["key"])
        return envelope


class ReceiptSeamTests(unittest.TestCase):
    """This module's receipt names run exactly the shared implementation.

    Since the step-5 merge the judgment lives in
    ``harnesses.session_receipts`` (pinned by ``test_session_receipts``); these
    witnesses prove genuine receipts and the shared verification failures.
    """

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="zcode-seam-attention-")
        self.addCleanup(temporary.cleanup)
        self.bridge = {"identity": {"taskId": "micro-task", "attemptId": "attempt", "generation": 1, "turnId": "logical"},
                       "inputSha256": "a" * 64, "key": "b" * 64,
                       "attentionPath": str(Path(temporary.name) / "absent-attention.json")}
        self.outcome = {"disposition": "completed", "summary": "done", "remaining": [], "decisions": [], "artifacts": [], "request": None}
        self.native = "mcp__buddy_x__hey_my_buddy_finish_turn"

    def test_finish_and_inquiry_names_bind_identity_and_refusals_bind_signature(self):
        raw = respond({"id": 1, "method": "tools/call", "params": {"name": "hey_my_buddy_finish_turn", "arguments": self.outcome}},
                      self.bridge)["result"]["content"][0]["text"]
        self.assertEqual(verify_receipt(raw, self.bridge, validate_outcome)["outcome"], self.outcome)
        other = {**self.bridge, "identity": {**self.bridge["identity"], "attemptId": "other"}}
        with self.assertRaises(ReceiptError) as error:
            verify_receipt(raw, other, validate_outcome)
        self.assertEqual(error.exception.code, "invalid-finish")
        answer = {"version": 1, "kind": "inquiry-answer", "identity": self.bridge["identity"],
                  "inquiryId": "q-1", "questionSha256": "c" * 64, "answer": "this", "receiptId": "e" * 32}
        answer["signature"] = sign_receipt(answer, self.bridge["key"])
        self.assertEqual(verify_inquiry_receipt(json.dumps(answer), self.bridge, "inquiry-answer")["answer"], "this")
        with self.assertRaises(ReceiptError) as error:
            verify_inquiry_receipt(json.dumps(answer), other, "inquiry-answer")
        self.assertEqual(error.exception.code, "invalid-inquiry-receipt")
        envelope = {"version": 1, "kind": "tool-refusal", "identity": self.bridge["identity"],
                    "inputSha256": self.bridge["inputSha256"], "tool": "hey_my_buddy_finish_turn",
                    "reason": "invalid-arguments", "detail": "the turn request references are invalid",
                    "receiptId": "f" * 32}
        envelope["signature"] = sign_receipt(envelope, self.bridge["key"])
        self.assertEqual(verify_tool_refusal(json.dumps(envelope), self.bridge, self.native, MOUNTED_TOOLS)["tool"],
                         "hey_my_buddy_finish_turn")
        with self.assertRaises(ReceiptError) as error:
            verify_tool_refusal(json.dumps({**envelope, "detail": "changed after signing"}), self.bridge, self.native,
                                MOUNTED_TOOLS)
        self.assertEqual(error.exception.code, "invalid-tool-refusal")


class InquiryEvidenceTests(unittest.TestCase):
    """Only root signed answers count; late answers preserve the finish."""

    def setUp(self):
        self.bridge = {"identity": {"taskId": "micro-task", "attemptId": "attempt", "generation": 1, "turnId": "logical"},
                       "inputSha256": "a" * 64, "key": "b" * 64}
        self.answers = []
        self.tracker = RootTurnEvidence("sess-root", "input-root", "mcp__buddy_x__hey_my_buddy_finish_turn", self.bridge,
            answer_name="mcp__buddy_x__hey_my_buddy_answer_inquiry", on_answer=lambda receipt, call: self.answers.append((receipt, call)),
            validate_outcome=validate_outcome, mounted_tools=MOUNTED_TOOLS)
        self.event(1, "turn.started", {"inputId": "input-root"})

    def event(self, seq, kind, payload, **identity):
        self.tracker.observe({"method": "session/event", "params": {"sessionId": "sess-root", "turnId": "native-turn",
            "seq": seq, "type": kind, "payload": payload, **identity}}, seq)

    def answer(self, seq, call="answer", **flags):
        self.event(seq, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__hey_my_buddy_answer_inquiry",
                                       "toolCallId": call, **flags})

    def receipt(self):
        value = {"version": 1, "kind": "inquiry-answer", "identity": self.bridge["identity"], "receiptId": "e"*32,
                 "inquiryId": "q", "questionSha256": "c"*64, "answer": "answer"}
        return self.signed(value)

    def signed(self, value):
        value["signature"] = sign_receipt(value, self.bridge["key"])
        return json.dumps(value)

    def result(self, seq, call="answer", content=None, **flags):
        self.event(seq, "tool.updated", {"kind": "result", "toolCallId": call,
            "result": {"success": True, "truncated": False, "content": content or self.receipt(), **flags}})

    def test_root_answer_reaches_callback(self):
        self.answer(2); self.result(3)
        self.assertEqual(self.answers[0][0]["inquiryId"], "q")
        self.assertEqual(self.answers[0][1], "answer")

    def test_child_and_foreign_answers_do_not_reach_callback(self):
        for index, flags in enumerate(({"source": "subagent"}, {"agentId": "agent"}, {"background": True},
                                      {"childSessionId": "child"}, {"parentToolCallId": "parent"})):
            self.answer(2+index*2, "child-"+str(index), **flags)
            self.result(3+index*2, "child-"+str(index))
        self.event(12, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__hey_my_buddy_answer_inquiry",
                                       "toolCallId": "foreign"}, sessionId="foreign")
        self.assertFalse(self.answers)

    def test_forged_truncated_missing_success_and_duplicate_answers_fail(self):
        self.answer(2)
        receipt = json.loads(self.receipt()); receipt["answer"] = "changed"
        with self.assertRaises(ReceiptError):
            self.result(3, content=json.dumps(receipt))
        with self.assertRaises(NativeError):
            self.result(4, truncated=True)
        with self.assertRaises(NativeError):
            self.result(5, success=None)
        self.result(6)
        with self.assertRaises(NativeError):
            self.result(7)

    def test_native_error_and_signed_refusal_allow_a_new_answer_call(self):
        self.answer(2)
        self.event(3, "tool.updated", {"kind": "error", "toolCallId": "answer"})
        for index, success in enumerate((True, False)):
            call = "refusal-"+str(index)
            self.answer(4+index*2, call)
            content = self.signed({"version": 1, "kind": "tool-refusal", "identity": self.bridge["identity"],
                "inputSha256": self.bridge["inputSha256"], "tool": "hey_my_buddy_answer_inquiry",
                "reason": "unknown-inquiry", "detail": "unknown inquiry", "receiptId": "f"*32})
            self.result(5+index*2, call, content="MCP tool returned an error:\n"+content, success=success)
        self.answer(8, "corrected"); self.result(9, "corrected")
        self.assertEqual(len(self.answers), 1)

    def test_an_answer_after_a_refused_finish_is_still_late(self):
        self.event(2, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__hey_my_buddy_finish_turn",
                                       "toolCallId": "finish"})
        self.event(3, "tool.updated", {"kind": "error", "toolCallId": "finish"})
        self.answer(4, "late"); self.result(5, "late")
        self.assertTrue(self.tracker.answer_calls["late"]["late"])
        self.assertEqual(len(self.answers), 1)

    def test_late_answer_after_finish_preserves_successful_completion(self):
        self.event(2, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__hey_my_buddy_finish_turn", "toolCallId": "finish"})
        outcome = {"disposition": "completed", "summary": "work preserved", "remaining": [], "decisions": [], "artifacts": [], "request": None}
        finish = self.signed({"version": 1, "identity": self.bridge["identity"], "inputSha256": self.bridge["inputSha256"],
                              "outcome": outcome, "receiptId": "f"*32})
        self.result(3, "finish", content=finish)
        self.answer(4, "late"); self.result(5, "late")
        self.event(6, "turn.completed", {"inputId": "input-root", "resultType": "success"})
        self.assertTrue(self.tracker.answer_calls["late"]["late"])
        self.assertEqual(self.tracker.receipt["outcome"], outcome)
        self.assertEqual(self.tracker.call_id, "finish")
        self.assertEqual(len(self.answers), 1)


class NativeFailureAttributionTests(unittest.TestCase):
    def test_structured_labels_require_identifier_characters(self):
        failure = decode_native_failure({"error": {
            "type": "https://private.invalid/path", "code": "Bearer-private-token",
            "attribution": {"source": {"bad": "shape"}, "reason": "private prompt text",
                            "providerId": "sk-private-key", "providerErrorCode": "1308", "statusCode": 429},
        }})
        self.assertIsNone(failure["errorType"])
        self.assertEqual(failure["code"], "Bearer-private-token")
        self.assertEqual(failure["attribution"], {"providerId": "sk-private-key",
                                                   "providerErrorCode": "1308", "statusCode": 429})

    """Only whitelisted exported turn.failed fields become observable."""

    QUOTA_ERROR = {"type": "AiSdkModelAdapterError", "code": "model_rate_limited",
                   "message": "raw provider detail with sk-fixture-secret and https://models.example.com/v1",
                   "attribution": {"source": "provider", "reason": "rate_limited", "errorPhase": "stream",
                                   "exceptionKind": "provider_business", "providerId": "zai-api",
                                   "modelId": "GLM-5.3", "providerKind": "anthropic", "transport": "sse",
                                   "statusCode": 429, "providerErrorCode": "1308", "retryable": False}}

    def test_the_quota_attribution_is_decoded_and_the_summary_is_safe(self):
        failure = decode_native_failure({"error": self.QUOTA_ERROR, "turnPhase": "stream"})
        self.assertEqual(failure["errorType"], "AiSdkModelAdapterError")
        self.assertEqual(failure["code"], "model_rate_limited")
        self.assertEqual(failure["turnPhase"], "stream")
        self.assertEqual(failure["attribution"]["statusCode"], 429)
        self.assertEqual(failure["attribution"]["providerErrorCode"], "1308")
        self.assertEqual(failure["attribution"]["reason"], "rate_limited")
        self.assertIs(failure["attribution"]["retryable"], False)
        for fragment in ("provider rate_limited", "code model_rate_limited", "status 429",
                         "providerCode 1308", "phase stream", "not-retryable"):
            self.assertIn(fragment, failure["summary"])
        dumped = json.dumps(failure)
        for secret in ("sk-fixture-secret", "models.example.com", "raw provider detail"):
            self.assertNotIn(secret, dumped)

    def test_missing_attribution_states_its_absence_instead_of_inventing_a_cause(self):
        for payload in ({"error": {"type": "UnknownError", "message": "quiet failure"},
                         "turnPhase": "model"},
                        {"error": {"type": "QuietError"}, "turnPhase": "model"},
                        {"error": "not-an-object", "turnPhase": "model"},
                        {"turnPhase": "model"},
                        "not-an-object"):
            with self.subTest(payload=payload):
                failure = decode_native_failure(payload)
                if failure is None:
                    continue
                self.assertEqual(failure["attribution"], {})
                self.assertEqual(failure["summary"], "no structured failure attribution was exported")
                self.assertIsNone(failure["code"])

    def test_values_outside_the_exported_schema_are_dropped_not_guessed(self):
        failure = decode_native_failure({"error": {"type": "E" * 400, "code": 7, "retryable": "yes",
                                                    "attribution": {"source": "vendor", "reason": "r" * 400,
                                                                    "statusCode": 42, "transport": "grpc",
                                                                    "errorPhase": "telemetry",
                                                                    "providerId": None, "modelId": 5}},
                                         "turnPhase": b"bytes"})
        self.assertIsNone(failure["errorType"])
        self.assertEqual(failure["attribution"], {})
        self.assertIsNone(failure["code"])
        self.assertIsNone(failure["turnPhase"])
        self.assertNotIn("vendor", json.dumps(failure))
        # A boolean retryable on the error itself is still in the exported schema.
        fallback = decode_native_failure({"error": {"type": "T", "retryable": True}, "turnPhase": "p"})
        self.assertIs(fallback["attribution"]["retryable"], True)


    def test_a_root_turn_failed_event_carries_attribution_a_prompt_failure_does_not(self):
        bridge = {"identity": {"taskId": "g", "attemptId": "a", "generation": 1, "turnId": "t"},
                  "inputSha256": "a" * 64, "key": "b" * 64}

        def turn_event(kind, payload):
            return {"method": "session/event", "params": {"sessionId": "sess-root", "turnId": "native-turn",
                                                          "seq": 2, "type": kind, "payload": payload}}

        tracker = RootTurnEvidence("sess-root", "input-root", "finish", bridge,
                                  validate_outcome=validate_outcome, mounted_tools=("hey_my_buddy_finish_turn",))
        tracker.observe({"method": "session/event", "params": {"sessionId": "sess-root", "turnId": "native-turn",
                        "seq": 1, "type": "turn.started", "payload": {"inputId": "input-root"}}}, 1)
        with self.assertRaises(NativeError) as error:
            tracker.observe(turn_event("turn.failed", {"error": self.QUOTA_ERROR, "turnPhase": "stream"}), 2)
        self.assertEqual(error.exception.code, "native-turn-failed")
        self.assertEqual(error.exception.failure["attribution"]["statusCode"], 429)
        self.assertIn("rate_limited", str(error.exception))
        # The state.updated prompt_failed envelope exports only a reason string
        # and an opaque patch, so no attribution is manufactured for it.
        quiet = RootTurnEvidence("sess-root", "input-root", "finish", bridge,
                               validate_outcome=validate_outcome, mounted_tools=("hey_my_buddy_finish_turn",))
        quiet.observe({"method": "session/event", "params": {"sessionId": "sess-root", "turnId": "native-turn",
                       "seq": 1, "type": "turn.started", "payload": {"inputId": "input-root"}}}, 1)
        with self.assertRaises(NativeError) as prompt_error:
            quiet.observe({"method": "state.updated", "params": {"sessionId": "sess-root",
                           "reason": "prompt_failed", "patch": {"anything": "opaque"}}}, 2)
        self.assertEqual(prompt_error.exception.code, "native-turn-failed")
        self.assertIsNone(prompt_error.exception.failure)


class OptionalNativeMetadataTests(unittest.TestCase):
    def test_only_the_current_rpc_failure_is_optional(self):
        connection = mock.Mock(deadline=time.monotonic() + 30)
        original = connection.deadline
        for error in (NativeRpcError("native-rpc-error", "rejected"),
                      NativeRpcError("timeout", "late")):
            connection.call.side_effect = error
            self.assertIsNone(optional_native_call(connection, "session/messages", {}))
            self.assertEqual(connection.deadline, original)
        for error in (NativeError("invalid-provenance", "observer failed"),
                      NativeError("native-rpc-error", "observer failed")):
            connection.call.side_effect = error
            with self.assertRaises(NativeError) as caught:
                optional_native_call(connection, "session/messages", {})
            self.assertIs(caught.exception, error)
            self.assertEqual(connection.deadline, original)

    def test_message_reads_follow_every_cursor_page(self):
        def message(index, *, assistant=False):
            info = {"sessionId": "root", "messageId": f"m-{index}"}
            if assistant:
                info.update(role="assistant", parentMessageId="parent",
                            tokens={"input": 1, "output": 2, "reasoning": 0,
                                    "cache": {"read": 0, "write": 0}})
            return {"info": info, "parts": []}

        class Connection:
            def __init__(self):
                self.deadline = time.monotonic() + 30
                self.messages = [message(index) for index in range(70)]
                self.cursors = []

            def call(self, method, params):
                self.cursors.append(params.get("afterMessageId"))
                after = params.get("afterMessageId")
                start = next((index + 1 for index, item in enumerate(self.messages)
                              if item["info"]["messageId"] == after), 0) if after else 0
                return {"messages": self.messages[start:start + params["limit"]]}

        connection = Connection()
        usage = ZcodeAttemptUsage("root", resumed=True)
        usage.capture_baseline(connection)
        self.assertEqual(len(usage.baseline_ids), 70)
        connection.messages.extend(message(index, assistant=True) for index in range(70, 140))
        usage.capture_final(connection)
        self.assertEqual(usage.message_usage()["nativeRecords"], 70)
        self.assertEqual(usage.message_usage()["completeness"], "complete")
        self.assertEqual(connection.cursors, [None, "m-63", "m-69", "m-69", "m-133", "m-139"])

    def test_short_message_page_does_not_end_the_read(self):
        class Connection:
            deadline = time.monotonic() + 30

            def __init__(self):
                self.cursors = []

            def call(self, method, params):
                self.cursors.append(params.get("afterMessageId"))
                pages = {None: ["m-0"], "m-0": ["m-1"], "m-1": []}
                return {"messages": [{"info": {"sessionId": "root", "messageId": item}, "parts": []}
                                     for item in pages[params.get("afterMessageId")]]}

        connection = Connection()
        usage = ZcodeAttemptUsage("root", resumed=False)
        usage.capture_baseline(connection)
        self.assertTrue(usage.baseline_ready)
        self.assertFalse(usage.read_incomplete)
        self.assertEqual(usage.baseline_ids, {"m-0", "m-1"})
        self.assertEqual(connection.cursors, [None, "m-0", "m-1"])

    def test_seen_message_cursor_marks_read_incomplete(self):
        class Connection:
            deadline = time.monotonic() + 30

            def __init__(self):
                self.cursors = []

            def call(self, method, params):
                after = params.get("afterMessageId")
                self.cursors.append(after)
                repeated = {None: "m-0", "m-0": "m-1", "m-1": "m-0"}[after]
                return {"messages": [{"info": {"sessionId": "root", "messageId": repeated}, "parts": []}]}

        connection = Connection()
        usage = ZcodeAttemptUsage("root", resumed=False)
        usage.capture_baseline(connection)
        self.assertFalse(usage.baseline_ready)
        self.assertTrue(usage.read_incomplete)
        self.assertEqual(connection.cursors, [None, "m-0", "m-1"])


class BuddyTests(unittest.TestCase):
    def test_run_buddy_boundary_refuses_missing_or_blank_fields(self):
        buddy = {"provider": "fixture-api", "model": "fixture-model", "effort": "low"}
        for key in buddy:
            for value in (None, "", False):
                with self.subTest(key=key, value=value):
                    requested = {**buddy, key: value}
                    if value is None:
                        requested.pop(key)
                    with self.assertRaises(BoardError):
                        RunBuddy(**requested)

    def test_unknown_selection_readback_continues_with_retained_facts(self):
        unknown = []
        connection = mock.Mock()
        connection.call.side_effect = [{}, {}]
        result = configure_session(connection, {"session": {"sessionId": "native-default"}},
                                   {"provider": "fixture-api", "model": "fixture-model", "effort": "low"},
                                   note_unknown=unknown.append)
        self.assertEqual(connection.call.call_count, 2)
        self.assertEqual(result, {"provider": None, "model": None, "effort": None})
        self.assertEqual(unknown, ["buddy-catalog-unrecognized",
                                   "buddy-selection-unrecognized",
                                   "buddy-readback-unconfirmed"])

    def test_identified_selection_mismatch_is_rejected_even_with_other_unknown_fields(self):
        requested = {"provider": "fixture-api", "model": "fixture-model", "effort": "low"}
        for current, thought in (
            ({"providerId": "other-api"}, None),
            ({"modelId": "other-model"}, None),
            ({"options": {"reasoningLevel": "high"}}, None),
            ({}, "high"),
            ({"options": {"reasoningLevel": "low"}}, "high"),
            ({"options": {"reasoningLevel": "high"}}, "low"),
            ({"options": {"reasoningLevel": "low"}}, "x" * 129),
        ):
            with self.subTest(current=current, thought=thought):
                connection = mock.Mock()
                connection.call.side_effect = [{}, {"settings": {
                    "model": {"current": current}, "thoughtLevel": {"current": thought}}}]
                with self.assertRaises(NativeError) as error:
                    configure_session(connection, {"session": {"sessionId": "native-default"}}, requested)
                self.assertEqual(error.exception.code, "buddy-mismatch")
                self.assertEqual([call.args[0] for call in connection.call.call_args_list],
                                 ["session/setModel", "session/setThoughtLevel"])

    def test_unrecognized_thought_level_is_unknown_and_buddy_continues(self):
        for thought in (None, [], False, "", " \t"):
            with self.subTest(thought=thought):
                unknown = []
                connection = mock.Mock()
                connection.call.side_effect = [{}, {"settings": {
                    "model": {"current": {"providerId": "fixture-api", "modelId": "fixture-model",
                                          "options": {"reasoningLevel": "low"}}},
                    "thoughtLevel": {"current": thought}}}]
                result = configure_session(connection, {"session": {"sessionId": "native-default"}},
                    {"provider": "fixture-api", "model": "fixture-model", "effort": "low"},
                    note_unknown=unknown.append)
                self.assertEqual(result, {"provider": "fixture-api", "model": "fixture-model", "effort": None})
                self.assertIn("buddy-readback-unconfirmed", unknown)

    def test_catalog_preserves_each_models_real_efforts_and_omits_unconfigurable_models(self):
        def model(identifier, efforts, provider="api"):
            return {"ref": {"providerId": provider, "modelId": identifier},
                    "reasoning": {"levels": [{"value": effort} for effort in efforts]},
                    "properties": {"inputFormat": {"supportsText": True, "supportsImage": identifier == "vision"}}}
        snapshot = {"settings": {"model": {"available": [
            model("vision", ["high", "max"]), model("fast", ["low"]), model("no-reasoning", []),
            model("invalid-efforts", ["", " \t", None]), model("account-only", ["high"], "oauth"),
        ]}}}
        result = catalog(snapshot, {"api": "api-key", "oauth": "zhipu-account"}, "native-test-version")
        self.assertEqual(len(result["providers"]), 2)
        provider = result["providers"][0]
        self.assertEqual(provider["harness"], "zcode")
        self.assertEqual(result["harnessVersion"], "native-test-version")
        models = {entry["id"]: entry for entry in provider["models"]}
        self.assertEqual(set(models), {"vision", "fast"})
        self.assertEqual(models["vision"]["efforts"], ["high", "max"])
        self.assertEqual(models["fast"]["efforts"], ["low"])
        self.assertEqual(models["vision"]["inputModalities"], ["text", "image"])
        self.assertTrue(all(entry["available"] for entry in models.values()))
        self.assertIn({"reason": {"code": "catalog.effort-directory-omitted", "params": {}}},
                      result["warnings"])
        self.assertEqual(result["providers"][1]["provider"], "oauth")
        self.assertEqual(result["providers"][1]["models"][0]["id"], "account-only")


class UnlimitedDeadlineTests(unittest.TestCase):
    def test_absent_timeout_maps_to_an_infinite_overall_deadline_only(self):
        self.assertEqual(execution_deadline(None), math.inf)
        before = time.monotonic()
        deadline = execution_deadline(45)
        self.assertLessEqual(before + 44, deadline)
        self.assertLessEqual(deadline, time.monotonic() + 45)

    def test_infinite_deadline_keeps_each_wait_bounded_and_cancellation_honored(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE)

        def stop():
            process.kill()
            process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()

        self.addCleanup(stop)
        connection = NativeConnection(process, math.inf, threading.Event(), runtime_preferences=FALLBACK_PREFERENCES)
        started = time.monotonic()
        connection.pump()  # no message arrives: the bounded per-pump wait must still return
        self.assertLess(time.monotonic() - started, 5.0)
        connection.cancelled.set()
        with self.assertRaises(NativeError) as error:
            connection.pump()
        self.assertEqual(error.exception.code, "cancelled")


class NativeConnectionShapeTests(unittest.TestCase):
    def test_nonobject_rpc_results_continue_with_bounded_content_free_facts(self):
        unknown = []
        connection = NativeConnection.__new__(NativeConnection)
        connection.next_id = 1
        connection.responses = {}
        connection.send = lambda _message: None
        connection.pump = lambda: None
        connection.on_unknown = unknown.append
        for result in (["provider secret"], "provider secret", 42, False, None):
            connection.responses[connection.next_id] = {"result": result}
            self.assertEqual(connection.call("session/messages", {}), {})
        self.assertEqual(unknown, ["rpc-result/session/messages/array", "rpc-result/session/messages/string",
                                   "rpc-result/session/messages/number", "rpc-result/session/messages/boolean",
                                   "rpc-result/session/messages/null"])
        self.assertNotIn("secret", str(unknown))
        connection.responses[connection.next_id] = {"result": "secret"}
        self.assertEqual(connection.call("provider/secret-token", {}), {})
        self.assertEqual(unknown[-1], "rpc-result/unknown/string")

    def test_native_rpc_error_still_rejects_without_retaining_error_content(self):
        unknown = []
        connection = NativeConnection.__new__(NativeConnection)
        connection.next_id = 1
        connection.responses = {1: {"error": {"message": "provider secret"}}}
        connection.send = lambda _message: None
        connection.pump = lambda: None
        connection.on_unknown = unknown.append
        with self.assertRaises(NativeError) as error:
            connection.call("session/messages", {})
        self.assertEqual(error.exception.code, "native-rpc-error")
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(unknown, [])

    def test_unclaimed_native_responses_are_retained(self):
        connection = NativeConnection.__new__(NativeConnection)
        connection.cancelled = threading.Event()
        connection.deadline = math.inf
        connection.messages = queue.Queue()
        connection.messages.put({"id": 17, "result": {}})
        connection.responses = {index: {"result": {}} for index in range(1, 17)}
        connection.ordinal = 0
        connection._pump()
        self.assertEqual(len(connection.responses), 17)


class OwnedProcessEvidenceTests(unittest.TestCase):
    def test_failed_membership_observation_cannot_confirm_shutdown(self):
        process = mock.Mock(pid=12345)
        process.poll.return_value = 0
        process.returncode = 0
        if os.name == "nt":
            job = mock.Mock()
            process._buddy_job = job
        with mock.patch("hey_my_buddy.protocol.process_identity._creation_time", return_value=10):
            handle = ProcessHandle(process, log_paths={}, stop_channel="signal")
        handle._exit_code = 0
        for error in (OSError(errno.EIO, "process observation unavailable"), PermissionError()):
            with self.subTest(error=type(error).__name__):
                if os.name == "nt":
                    job.active.side_effect = error
                    self.assertFalse(handle.shutdown_confirmed(settle_seconds=0))
                else:
                    with mock.patch.object(handle._members, "observe", side_effect=error):
                        self.assertFalse(handle.shutdown_confirmed(settle_seconds=0))
        if os.name == "nt":
            job.active.side_effect = None
            job.active.return_value = 0
            self.assertTrue(handle.shutdown_confirmed(settle_seconds=0))
            job.close.assert_called_once_with(confirmed=True)
        else:
            with mock.patch.object(handle._members, "observe", return_value="identity-absent"):
                self.assertTrue(handle.shutdown_confirmed(settle_seconds=0))


if __name__ == "__main__":
    unittest.main()
