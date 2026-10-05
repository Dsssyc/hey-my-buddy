"""Strict receipt and native provenance checks independent of process fixtures."""
import errno
import json
import math
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.buddy.harnesses.session_receipts import (
    MAX_INQUIRY_RECEIPT_BYTES,
    MAX_TOOL_REFUSAL_BYTES,
    MAX_TOOL_REFUSAL_DETAIL_BYTES,
    MAX_TOOL_REFUSAL_PREFIX_BYTES,
    sign_receipt,
)
from hey_my_buddy.buddy.harnesses.zcode.mcp import respond
from hey_my_buddy.buddy.harnesses.zcode.protocol import (
    NativeConnection,
    NativeError,
    RootTurnEvidence,
    decode_json,
    decode_native_failure,
    refusal_shaped,
    verify_inquiry_receipt,
    verify_receipt,
    verify_tool_refusal,
)
from hey_my_buddy.buddy.harnesses.zcode.native_run import catalog, configure_session, execution_deadline
from hey_my_buddy.json_codec import canonical_json

# The session tools one run actually mounts; refusal envelopes verify only
# against this set, never a fixed global one.
MOUNTED_TOOLS = ("buddy_checkpoint", "buddy_answer_inquiry", "buddy_finish_turn")
from hey_my_buddy.buddy.roles.turn_io import validate_outcome


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.bridge = {"identity": {"taskId": "goal", "attemptId": "attempt", "generation": 1, "turnId": "logical"},
                       "inputSha256": "a" * 64, "key": "b" * 64}
        self.outcome = {"disposition": "completed", "summary": "done", "remaining": [], "decisions": [], "artifacts": [], "request": None}

    def receipt(self):
        result = respond({"id": 1, "method": "tools/call", "params": {"name": "buddy_finish_turn", "arguments": self.outcome}}, self.bridge)
        self.assertNotIn("structuredContent", result["result"])
        return result["result"]["content"][0]["text"]

    def test_bridge_receipt_is_bound_to_input_identity_and_outcome(self):
        raw = self.receipt()
        self.assertEqual(verify_receipt(raw, self.bridge, validate_outcome)["outcome"], self.outcome)
        for key, value in (("inputSha256", "c" * 64), ("outcome", {**self.outcome, "summary": "forged"}),
                           ("identity", {**self.bridge["identity"], "generation": 2})):
            with self.subTest(key=key):
                changed = json.loads(raw)
                changed[key] = value
                with self.assertRaises(NativeError):
                    verify_receipt(json.dumps(changed), self.bridge, validate_outcome)
        other_attempt = {**self.bridge, "identity": {**self.bridge["identity"], "attemptId": "other"}}
        with self.assertRaises(NativeError):
            verify_receipt(raw, other_attempt, validate_outcome)

    def test_prose_duplicate_members_and_nonfinite_values_are_rejected(self):
        raw = self.receipt()
        for invalid in ("Here is the result: " + raw, "```json\n" + raw + "\n```", raw + raw):
            with self.assertRaises(NativeError):
                verify_receipt(invalid, self.bridge, validate_outcome)
        for invalid in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'):
            with self.assertRaises(ValueError):
                decode_json(invalid)

    def test_wrong_input_and_reordered_events_cannot_establish_a_root(self):
        tracker = RootTurnEvidence("sess-root", "input-root", "finish", self.bridge,
                                          validate_outcome=validate_outcome, mounted_tools=("buddy_finish_turn",))
        def event(seq, input_id):
            return {"method": "session/event", "params": {"sessionId": "sess-root", "turnId": "native-turn", "seq": seq,
                    "type": "turn.started", "payload": {"inputId": input_id}}}
        with self.assertRaises(NativeError):
            tracker.observe(event(1, "wrong-input"), 1)
        tracker = RootTurnEvidence("sess-root", "input-root", "finish", self.bridge,
                                          validate_outcome=validate_outcome, mounted_tools=("buddy_finish_turn",))
        tracker.observe(event(2, "input-root"), 1)
        with self.assertRaises(NativeError):
            tracker.observe(event(1, "input-root"), 2)

    def test_root_relayed_child_finish_does_not_consume_root_receipt(self):
        tracker = RootTurnEvidence("sess-root", "input-root", "finish", self.bridge,
                                          validate_outcome=validate_outcome, mounted_tools=("buddy_finish_turn",))
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
                                          validate_outcome=validate_outcome, mounted_tools=("buddy_finish_turn",))
        self.root_event(tracker, 1, "turn.started", {"inputId": "input-root"})
        return tracker

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
        finish = "mcp__buddy_x__buddy_finish_turn"
        refusal = json.dumps(self._signed_refusal())
        tracker = RootTurnEvidence("sess-root", "input-root", finish, self.bridge,
                                          validate_outcome=validate_outcome, mounted_tools=("buddy_finish_turn",))
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
        finish = "mcp__buddy_x__buddy_finish_turn"
        for content in (json.dumps({"kind": "tool-refusal", "detail": "unsigned prose"}),
                        json.dumps(self._signed_refusal(tool="buddy_checkpoint")),
                        json.dumps(self._signed_refusal(identity={"taskId": "goal", "attemptId": "other",
                                                                 "generation": 1, "turnId": "logical"}))):
            with self.subTest(content=content[:60]):
                tracker = RootTurnEvidence("sess-root", "input-root", finish, self.bridge,
                                          validate_outcome=validate_outcome, mounted_tools=("buddy_finish_turn",))
                self.root_event(tracker, 1, "turn.started", {"inputId": "input-root"})
                self.root_event(tracker, 2, "tool.updated", {"kind": "scheduled", "toolName": finish, "toolCallId": "bad"})
                with self.assertRaises(NativeError) as error:
                    self.root_event(tracker, 3, "tool.updated", {"kind": "result", "toolCallId": "bad",
                        "result": {"success": True, "truncated": False, "content": content}})
                self.assertEqual(error.exception.code, "invalid-tool-refusal")
                self.assertIsNone(tracker.receipt)

    def test_a_failed_marker_result_verifies_the_refusal_envelope_first(self):
        # A result marked success:false still examines a refusal-shaped content
        # before any ordinary retryable-error path, so the same envelope both
        # recovers when genuine and fails the turn when forged or wrong-tool.
        finish = "mcp__buddy_x__buddy_finish_turn"
        header = "MCP tool returned an error:\n"
        verified = header + json.dumps(self._signed_refusal())
        tracker = RootTurnEvidence("sess-root", "input-root", finish, self.bridge,
                                          validate_outcome=validate_outcome, mounted_tools=("buddy_finish_turn",))
        self.root_event(tracker, 1, "turn.started", {"inputId": "input-root"})
        self.root_event(tracker, 2, "tool.updated", {"kind": "scheduled", "toolName": finish, "toolCallId": "bad"})
        self.root_event(tracker, 3, "tool.updated", {"kind": "result", "toolCallId": "bad",
            "result": {"success": False, "truncated": False, "content": verified}})
        self.assertTrue(tracker.finish_failed)
        self.assertIsNone(tracker.receipt)
        for content in (header + json.dumps({**self._signed_refusal(), "detail": "changed after signing"}),
                        header + json.dumps(self._signed_refusal(tool="buddy_answer_inquiry"))):
            with self.subTest(content=content[:60]):
                tracker = RootTurnEvidence("sess-root", "input-root", finish, self.bridge,
                                          validate_outcome=validate_outcome, mounted_tools=("buddy_finish_turn",))
                self.root_event(tracker, 1, "turn.started", {"inputId": "input-root"})
                self.root_event(tracker, 2, "tool.updated", {"kind": "scheduled", "toolName": finish, "toolCallId": "bad"})
                with self.assertRaises(NativeError) as error:
                    self.root_event(tracker, 3, "tool.updated", {"kind": "result", "toolCallId": "bad",
                        "result": {"success": False, "truncated": False, "content": content}})
                self.assertEqual(error.exception.code, "invalid-tool-refusal")
                self.assertIsNone(tracker.receipt)

    def test_receipt_failures_distinguish_malformed_signature_identity_and_outcome(self):
        # Safe, bounded diagnostics: each stage names its own failure without
        # ever quoting the raw content.
        raw = self.receipt()
        malformed = {
            "prose": "the tool says no",
            "wrong object": json.dumps({"version": 1, "identity": {}, "not": "a receipt"}),
            "trailing data": raw + " trailing",
        }
        for name, content in malformed.items():
            with self.subTest(case=name):
                with self.assertRaises(NativeError) as error:
                    verify_receipt(content, self.bridge, validate_outcome)
                self.assertEqual(error.exception.code, "invalid-finish")
                message = str(error.exception)
                self.assertTrue(any(fragment in message for fragment in
                                    ("no bounded JSON receipt", "not the current signed receipt object", "malformed")), message)
        tampered = json.loads(raw)
        tampered["outcome"] = {**tampered["outcome"], "summary": "forged"}
        with self.assertRaises(NativeError) as error:
            verify_receipt(json.dumps(tampered), self.bridge, validate_outcome)
        self.assertIn("signature verification", str(error.exception))
        rekeyed = json.loads(raw)
        rekeyed.pop("signature")
        rekeyed["identity"] = {**rekeyed["identity"], "attemptId": "other"}
        rekeyed["signature"] = sign_receipt(rekeyed, self.bridge["key"])
        with self.assertRaises(NativeError) as error:
            verify_receipt(json.dumps(rekeyed), self.bridge, validate_outcome)
        self.assertIn("attempt-identity binding", str(error.exception))
        reoutcomed = json.loads(raw)
        reoutcomed.pop("signature")
        reoutcomed["outcome"]["disposition"] = "unknown"
        reoutcomed["signature"] = sign_receipt(reoutcomed, self.bridge["key"])
        with self.assertRaises(NativeError) as error:
            verify_receipt(json.dumps(reoutcomed), self.bridge, validate_outcome)
        self.assertIn("outcome validation", str(error.exception))

    def _signed_refusal(self, *, tool: str = "buddy_finish_turn", reason: str = "invalid-arguments",
                        detail: str = "the turn request references are invalid", identity: dict | None = None) -> dict:
        envelope = {"version": 1, "kind": "tool-refusal", "identity": identity or self.bridge["identity"],
                    "inputSha256": self.bridge["inputSha256"], "tool": tool, "reason": reason,
                    "detail": detail, "receiptId": "f" * 32}
        envelope["signature"] = sign_receipt(envelope, self.bridge["key"])
        return envelope


class InquiryReceiptTests(unittest.TestCase):
    def setUp(self):
        self.bridge = {"identity": {"taskId": "goal", "attemptId": "attempt", "generation": 1, "turnId": "logical"},
                       "inputSha256": "a" * 64, "key": "b" * 64}

    def receipt(self, kind: str, payload: dict) -> str:
        receipt = {"version": 1, "kind": kind, "identity": self.bridge["identity"], "receiptId": "e" * 32, **payload}
        receipt["signature"] = sign_receipt(receipt, self.bridge["key"])
        return json.dumps(receipt)

    def test_checkpoint_and_answer_receipts_verify_and_bind_to_identity(self):
        checkpoint = self.receipt("inquiry-checkpoint", {"inquiries": [
            {"inquiryId": "q-1", "question": "what?", "questionSha256": "c" * 64, "state": "queued",
             "askedAt": "2026-01-01T00:00:00Z"}]})
        verified = verify_inquiry_receipt(checkpoint, self.bridge, "inquiry-checkpoint")
        self.assertEqual(verified["inquiries"][0]["inquiryId"], "q-1")
        answer = self.receipt("inquiry-answer", {"inquiryId": "q-1", "questionSha256": "c" * 64, "answer": "this"})
        self.assertEqual(verify_inquiry_receipt(answer, self.bridge, "inquiry-answer")["answer"], "this")

    def test_forged_stale_or_malformed_receipts_are_rejected(self):
        valid = self.receipt("inquiry-answer", {"inquiryId": "q-1", "questionSha256": "c" * 64, "answer": "this"})
        cases = {
            "changed answer": json.dumps({**json.loads(valid), "answer": "that"}),
            "changed signature": json.dumps({**json.loads(valid), "signature": "0" * 64}),
            "foreign key": None,  # verified below with a different configuration
            "foreign identity": json.dumps({**json.loads(valid),
                                            "identity": {**self.bridge["identity"], "attemptId": "other"}}),
            "wrong kind": json.dumps({**json.loads(valid), "kind": "inquiry-checkpoint"}),
            "missing field": json.dumps({k: v for k, v in json.loads(valid).items() if k != "inquiryId"}),
            "extra field": json.dumps({**json.loads(valid), "extra": 1}),
            "oversized answer": self.receipt("inquiry-answer", {"inquiryId": "q-1", "questionSha256": "c" * 64,
                                                                "answer": "x" * 4001}),
            "bad hash": self.receipt("inquiry-answer", {"inquiryId": "q-1", "questionSha256": "not-hex", "answer": "x"}),
            "prose": "the answer is: " + valid,
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                if raw is None:
                    with self.assertRaises(NativeError):
                        verify_inquiry_receipt(valid, {**self.bridge, "key": "d" * 64}, "inquiry-answer")
                    continue
                with self.assertRaises(NativeError):
                    verify_inquiry_receipt(raw, self.bridge, "inquiry-answer")
        oversized = self.receipt("inquiry-checkpoint", {"inquiries": [
            {"inquiryId": f"q-{index}", "question": "x" * 4000, "questionSha256": "c" * 64,
             "state": "queued", "askedAt": "t"} for index in range(33)]})
        with self.assertRaises(NativeError):
            verify_inquiry_receipt(oversized, self.bridge, "inquiry-checkpoint")

    def test_checkpoint_entries_must_be_well_formed(self):
        base = {"inquiryId": "q-1", "question": "what?", "questionSha256": "c" * 64, "state": "queued",
                "askedAt": "2026-01-01T00:00:00Z"}
        for mutation in ({"state": "answered"}, {"question": " "}, {"questionSha256": "z" * 64},
                         {"inquiryId": ""}, {"askedAt": 5}, {"deliveredAt": 7}, {"unknownField": True}):
            with self.subTest(mutation=mutation):
                raw = self.receipt("inquiry-checkpoint", {"inquiries": [{**base, **mutation}]})
                with self.assertRaises(NativeError):
                    verify_inquiry_receipt(raw, self.bridge, "inquiry-checkpoint")


class ToolRefusalEnvelopeTests(unittest.TestCase):
    """The signed refusal envelope itself: exact binding, fatal forgery, dispatch."""

    def setUp(self):
        self.bridge = {"identity": {"taskId": "goal", "attemptId": "attempt", "generation": 1, "turnId": "logical"},
                       "inputSha256": "a" * 64, "key": "b" * 64}
        self.names = {"buddy_finish_turn": "mcp__buddy_x__buddy_finish_turn",
                      "buddy_checkpoint": "mcp__buddy_x__buddy_checkpoint",
                      "buddy_answer_inquiry": "mcp__buddy_x__buddy_answer_inquiry"}

    def refusal(self, **overrides) -> str:
        envelope = {"version": 1, "kind": "tool-refusal", "identity": self.bridge["identity"],
                    "inputSha256": self.bridge["inputSha256"], "tool": "buddy_finish_turn",
                    "reason": "invalid-arguments", "detail": "the turn request references are invalid",
                    "receiptId": "f" * 32}
        key = overrides.pop("key", None) or self.bridge["key"]
        envelope.update(overrides)
        envelope["signature"] = sign_receipt(envelope, key)
        return json.dumps(envelope)

    def test_a_well_bound_envelope_verifies_for_exactly_its_tool(self):
        raw = self.refusal()
        self.assertTrue(refusal_shaped(raw))
        envelope = verify_tool_refusal(raw, self.bridge, self.names["buddy_finish_turn"], MOUNTED_TOOLS)
        self.assertEqual(envelope["reason"], "invalid-arguments")
        self.assertEqual(envelope["tool"], "buddy_finish_turn")
        for bare, native in self.names.items():
            if bare == "buddy_finish_turn":
                continue
            with self.subTest(native=native):
                with self.assertRaises(NativeError) as error:
                    verify_tool_refusal(raw, self.bridge, native, MOUNTED_TOOLS)
                self.assertEqual(error.exception.code, "invalid-tool-refusal")
                self.assertIn("different session tool", str(error.exception))

    def test_the_native_wrapper_error_header_is_tolerated_but_only_bounded(self):
        # The installed wrapper delivers an MCP isError text as a successful
        # result prefixed with one plain header line. Dispatch tolerates exactly
        # that bounded framing; the signature still decides everything else.
        raw = self.refusal()
        framed = "MCP tool returned an error:\n" + raw
        self.assertTrue(refusal_shaped(framed))
        envelope = verify_tool_refusal(framed, self.bridge, self.names["buddy_finish_turn"], MOUNTED_TOOLS)
        self.assertEqual(envelope["reason"], "invalid-arguments")
        tampered = "MCP tool returned an error:\n" + json.dumps({**json.loads(raw), "detail": "changed after signing"})
        with self.assertRaises(NativeError) as error:
            verify_tool_refusal(tampered, self.bridge, self.names["buddy_finish_turn"], MOUNTED_TOOLS)
        self.assertIn("signature", str(error.exception))
        for not_shaped in ("x" * 300 + raw, framed + " trailing", "error: {" + raw,
                           "MCP tool returned an error:\nnot json", " ", ""):
            with self.subTest(case=not_shaped[:40]):
                self.assertFalse(refusal_shaped(not_shaped))

    def test_tampered_foreign_or_unbounded_envelopes_stay_fatal(self):
        cases = {
            "changed detail": json.dumps({**json.loads(self.refusal()), "detail": "changed after signing"}),
            "changed signature": json.dumps({**json.loads(self.refusal()), "signature": "0" * 64}),
            "foreign identity": self.refusal(identity={"taskId": "goal", "attemptId": "other",
                                                       "generation": 1, "turnId": "logical"}),
            "foreign input": self.refusal(inputSha256="c" * 64),
            "unknown reason": self.refusal(reason="quota-exhausted"),
            "blank detail": self.refusal(detail="  "),
            "oversized detail": self.refusal(detail="x" * 65537),
            "extra field": json.dumps({**json.loads(self.refusal()), "extra": 1}),
            "missing field": json.dumps({k: v for k, v in json.loads(self.refusal()).items() if k != "reason"}),
            "not an object": json.dumps([1, 2, 3]),
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                if name == "not an object":
                    self.assertFalse(refusal_shaped(raw))
                with self.assertRaises(NativeError) as error:
                    verify_tool_refusal(raw, self.bridge, self.names["buddy_finish_turn"], MOUNTED_TOOLS)
                self.assertEqual(error.exception.code, "invalid-tool-refusal")
        # Prose before a genuinely signed envelope is just framing: the prefix
        # never enters the signature, so the envelope still verifies and can
        # only ever mean "correct the call and retry".
        prose_framed = "the tool refused: " + self.refusal()
        self.assertTrue(refusal_shaped(prose_framed))
        self.assertEqual(verify_tool_refusal(prose_framed, self.bridge,
                                             self.names["buddy_finish_turn"], MOUNTED_TOOLS)["reason"], "invalid-arguments")
        with self.assertRaises(NativeError):
            verify_tool_refusal(self.refusal(), {**self.bridge, "key": "d" * 64}, self.names["buddy_finish_turn"], MOUNTED_TOOLS)
        with self.assertRaises(NativeError):
            verify_tool_refusal("x" * 70001, self.bridge, self.names["buddy_finish_turn"], MOUNTED_TOOLS)

    def test_dispatch_never_treats_a_receipt_or_prose_as_a_refusal(self):
        outcome = {"disposition": "completed", "summary": "done", "remaining": [], "decisions": [],
                   "artifacts": [], "request": None}
        result = respond({"id": 1, "method": "tools/call", "params": {"name": "buddy_finish_turn",
                                                                      "arguments": outcome}}, self.bridge)["result"]
        self.assertFalse(refusal_shaped(result["content"][0]["text"]))
        self.assertFalse(refusal_shaped(None))
        self.assertFalse(refusal_shaped("plain tool error text"))
        # A genuine MCP refusal is both isError and refusal-shaped, and verifies.
        refused = respond({"id": 1, "method": "tools/call", "params": {"name": "buddy_finish_turn",
                                                                       "arguments": {"disposition": "completed"}}}, self.bridge)["result"]
        self.assertTrue(refused.get("isError"))
        self.assertTrue(refusal_shaped(refused["content"][0]["text"]))
        envelope = verify_tool_refusal(refused["content"][0]["text"], self.bridge, self.names["buddy_finish_turn"], MOUNTED_TOOLS)
        self.assertEqual(envelope["reason"], "invalid-arguments")


class RefusalWireBudgetTests(unittest.TestCase):
    """Every envelope this MCP mints fits the wire budget its verification enforces.

    The review reproduced a real mismatch: raw UTF-8 length does not bound
    canonical JSON escaping (a control character costs six bytes, a quote or
    backslash two) and the native wrapper adds its error header, so a minted
    refusal could exceed the very budget ``verify_tool_refusal`` enforces.
    """

    def setUp(self):
        self.bridge = {"identity": {"taskId": "goal", "attemptId": "attempt", "generation": 1, "turnId": "logical"},
                       "inputSha256": "a" * 64, "key": "b" * 64}
        self.finish = "mcp__buddy_x__buddy_finish_turn"

    def mint(self, detail: str, *, tool: str = "buddy_finish_turn", reason: str = "invalid-arguments") -> str:
        from hey_my_buddy.buddy.roles.worker_services import _refusal
        return _refusal(self.bridge, tool, reason, detail)["content"][0]["text"]

    def test_pathological_details_mint_envelopes_that_verify_byte_for_byte(self):
        cases = {
            "quotes (review repro)": '"' * 65536,
            "backslashes": "\\" * 65536,
            "c0 controls": chr(1) * 65536,
            "mixed escapes": '\\"世🎉\x01\x1f' * 6000,
            "non-ascii literal": "世" * 21845,
            "plain at the raw bound": "x" * MAX_TOOL_REFUSAL_DETAIL_BYTES,
            "plain past the raw bound": "x" * (MAX_TOOL_REFUSAL_DETAIL_BYTES + 5000),
        }
        for name, detail in cases.items():
            with self.subTest(case=name):
                text = self.mint(detail)
                self.assertLessEqual(len(text.encode()) + MAX_TOOL_REFUSAL_PREFIX_BYTES, MAX_TOOL_REFUSAL_BYTES)
                envelope = verify_tool_refusal(text, self.bridge, self.finish, MOUNTED_TOOLS)
                self.assertTrue(envelope["detail"].strip())
                # The kept detail is a prefix of the original, so the leading
                # correction guidance survives and nothing is rewritten.
                self.assertTrue(detail.startswith(envelope["detail"]), name)

    def test_the_review_repro_wire_sizes_now_verify(self):
        text = self.mint('"' * 65536)
        envelope = verify_tool_refusal(text, self.bridge, self.finish, MOUNTED_TOOLS)
        # The unfixed mint emitted 131,450 wire bytes and was refused; the
        # fitted mint now uses most of the budget without exceeding it.
        self.assertGreater(len(text.encode()), MAX_TOOL_REFUSAL_BYTES // 2)
        self.assertEqual(envelope["reason"], "invalid-arguments")

    def test_a_whitespace_detail_falls_back_to_the_retry_instruction(self):
        envelope = verify_tool_refusal(self.mint(" " * 70000), self.bridge, self.finish, MOUNTED_TOOLS)
        self.assertIn("correct it and retry", envelope["detail"])

    def test_checkpoint_receipts_batch_serialized_overflow_explicitly(self):
        from hey_my_buddy.buddy.roles.worker_services import _checkpoint_batch
        from hey_my_buddy.buddy.roles.turn_io import canonical_json
        # 32 accepted-but-pathological questions (control characters expand
        # sixfold) cannot fit one receipt: the batch is the longest serialized
        # prefix and the overflow is counted, never silently dropped.
        pathological = [{"inquiryId": f"q-{index}", "question": chr(1) * 4000, "questionSha256": "c" * 64,
                         "state": "queued", "askedAt": "t"} for index in range(32)]
        batch, more = _checkpoint_batch(pathological)
        self.assertLess(len(batch), 32)
        self.assertEqual(more, 32 - len(batch))
        self.assertLessEqual(len(canonical_json(batch).encode()), MAX_INQUIRY_RECEIPT_BYTES - 1024)
        # Realistic questions at the question budget all fit one receipt.
        realistic = [{"inquiryId": f"q-{index}", "question": "y" * 3990, "questionSha256": "c" * 64,
                      "state": "queued", "askedAt": "t"} for index in range(32)]
        batch, more = _checkpoint_batch(realistic)
        self.assertEqual((len(batch), more), (32, 0))


class InquiryEvidenceTests(unittest.TestCase):
    """The root-turn tracker owns inquiry authority; relays and forgeries fail."""

    def setUp(self):
        self.bridge = {"identity": {"taskId": "goal", "attemptId": "attempt", "generation": 1, "turnId": "logical"},
                       "inputSha256": "a" * 64, "key": "b" * 64}
        self.deliveries: list[tuple[dict, str]] = []
        self.answers: list[tuple[dict, str]] = []
        self.tracker = RootTurnEvidence(
            "sess-root", "input-root", "mcp__buddy_x__buddy_finish_turn", self.bridge,
            checkpoint_name="mcp__buddy_x__buddy_checkpoint",
            answer_name="mcp__buddy_x__buddy_answer_inquiry",
            on_delivery=lambda receipt, call: self.deliveries.append((receipt, call)),
            on_answer=lambda receipt, call: self.answers.append((receipt, call)),
            validate_outcome=validate_outcome, mounted_tools=MOUNTED_TOOLS)

    def event(self, seq: int, kind: str, payload: dict, **identity) -> None:
        self.tracker.observe({"method": "session/event", "params": {
            "sessionId": "sess-root", "turnId": "native-turn", "seq": seq,
            "type": kind, "payload": payload, **identity,
        }}, seq)

    def started(self):
        self.event(1, "turn.started", {"inputId": "input-root"})

    def receipt_text(self, kind: str, payload: dict) -> str:
        receipt = {"version": 1, "kind": kind, "identity": self.bridge["identity"], "receiptId": "e" * 32, **payload}
        receipt["signature"] = sign_receipt(receipt, self.bridge["key"])
        return json.dumps(receipt)

    def test_verified_checkpoint_and_answer_results_reach_the_callbacks(self):
        self.started()
        content = self.receipt_text("inquiry-checkpoint", {"inquiries": [
            {"inquiryId": "q-1", "question": "what?", "questionSha256": "c" * 64, "state": "queued",
             "askedAt": "t"}]})
        self.event(2, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__buddy_checkpoint",
                                       "toolCallId": "ckpt-1"})
        self.event(3, "tool.updated", {"kind": "result", "toolCallId": "ckpt-1",
                                       "result": {"success": True, "truncated": False, "content": content}})
        self.assertEqual(self.deliveries[0][1], "ckpt-1")
        answer = self.receipt_text("inquiry-answer", {"inquiryId": "q-1", "questionSha256": "c" * 64,
                                                      "answer": "this"})
        self.event(4, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__buddy_answer_inquiry",
                                       "toolCallId": "ans-1"})
        self.event(5, "tool.updated", {"kind": "result", "toolCallId": "ans-1",
                                       "result": {"success": True, "truncated": False, "content": answer}})
        self.assertEqual(self.answers[0][0]["inquiryId"], "q-1")
        self.assertEqual(self.answers[0][1], "ans-1")

    def test_child_relays_and_foreign_turns_never_reach_the_callbacks(self):
        self.started()
        content = self.receipt_text("inquiry-checkpoint", {"inquiries": []})
        seq = 2
        for relay in ({"source": "subagent"}, {"agentId": "agent-1"}, {"background": True},
                      {"childSessionId": "sess-child"}, {"childToolCallId": "child-1"}, {"parentToolCallId": "p"}):
            with self.subTest(relay=relay):
                self.event(seq, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__buddy_checkpoint",
                                                 "toolCallId": "relay-call", **relay})
                self.event(seq + 1, "tool.updated", {"kind": "result", "toolCallId": "relay-call", **relay,
                                                     "result": {"success": True, "truncated": False, "content": content}})
                seq += 2
        self.event(seq, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__buddy_checkpoint",
                                         "toolCallId": "other-turn-call"})
        self.event(seq + 1, "tool.updated", {"kind": "result", "toolCallId": "other-turn-call",
                                             "result": {"success": True, "truncated": False, "content": content}},
                   turnId="turn-other")
        self.assertEqual(self.deliveries, [])

    def test_failed_inquiry_tool_results_allow_a_corrected_retry(self):
        self.started()
        self.event(2, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__buddy_checkpoint",
                                       "toolCallId": "ckpt-bad"})
        self.event(3, "tool.updated", {"toolCallId": "ckpt-bad", "kind": "error", "error": "unknown inquiry"})
        self.event(4, "tool.updated", {"toolCallId": "ckpt-bad2", "kind": "error", "error": "again"})
        self.assertEqual(self.deliveries, [])
        content = self.receipt_text("inquiry-checkpoint", {"inquiries": []})
        self.event(5, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__buddy_checkpoint",
                                       "toolCallId": "ckpt-good"})
        self.event(6, "tool.updated", {"kind": "result", "toolCallId": "ckpt-good",
                                       "result": {"success": True, "truncated": False, "content": content}})
        self.assertEqual(len(self.deliveries), 1)

    def refusal_text(self, tool: str, reason: str, detail: str = "correction text") -> str:
        envelope = {"version": 1, "kind": "tool-refusal", "identity": self.bridge["identity"],
                    "inputSha256": self.bridge["inputSha256"], "tool": tool, "reason": reason,
                    "detail": detail, "receiptId": "f" * 32}
        envelope["signature"] = sign_receipt(envelope, self.bridge["key"])
        return json.dumps(envelope)

    def test_failed_marker_results_verify_inquiry_refusals_first(self):
        # The checkpoint and answer tools get the same contract as finish: a
        # refusal-shaped content on a success:false result is verified before
        # the ordinary retryable-error path, so a forged or wrong-tool envelope
        # is fatal even on the failed-marker path.
        header = "MCP tool returned an error:\n"
        self.started()
        self.event(2, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__buddy_checkpoint",
                                       "toolCallId": "ckpt-false"})
        self.event(3, "tool.updated", {"kind": "result", "toolCallId": "ckpt-false",
                                       "result": {"success": False, "truncated": False,
                                                  "content": header + self.refusal_text("buddy_checkpoint", "inquiry-channel-absent")}})
        self.assertEqual(self.tracker.checkpoint_calls["ckpt-false"]["result"], "tool-refusal")
        self.assertEqual(self.deliveries, [])
        self.event(4, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__buddy_answer_inquiry",
                                       "toolCallId": "ans-false"})
        self.event(5, "tool.updated", {"kind": "result", "toolCallId": "ans-false",
                                       "result": {"success": False, "truncated": False,
                                                  "content": header + self.refusal_text("buddy_answer_inquiry", "unknown-inquiry")}})
        self.assertEqual(self.tracker.answer_calls["ans-false"]["result"], "tool-refusal")
        self.assertEqual(self.answers, [])
        with self.assertRaises(NativeError) as error:
            self.event(6, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__buddy_checkpoint",
                                           "toolCallId": "ckpt-forged"})
            self.event(7, "tool.updated", {"kind": "result", "toolCallId": "ckpt-forged",
                                           "result": {"success": False, "truncated": False,
                                                      "content": header + self.refusal_text("buddy_answer_inquiry", "unknown-inquiry")}})
        self.assertEqual(error.exception.code, "invalid-tool-refusal")
        forged = json.loads(self.refusal_text("buddy_checkpoint", "inquiry-channel-absent"))
        forged["detail"] = "changed after signing"
        with self.assertRaises(NativeError) as error:
            self.event(8, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__buddy_answer_inquiry",
                                           "toolCallId": "ans-forged"})
            self.event(9, "tool.updated", {"kind": "result", "toolCallId": "ans-forged",
                                           "result": {"success": False, "truncated": False,
                                                      "content": header + json.dumps(forged)}})
        self.assertEqual(error.exception.code, "invalid-tool-refusal")

    def test_wrapper_successful_refusals_keep_the_inquiry_turn_alive(self):
        # The incident's native-wrapper shape on the checkpoint and answer tools:
        # the MCP isError refusal arrives as a successful result. A verified
        # envelope must not deliver or answer anything, and an unverified one
        # (here: signed for another tool) fails the turn.
        self.started()
        self.event(2, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__buddy_checkpoint",
                                       "toolCallId": "ckpt-bad"})
        self.event(3, "tool.updated", {"kind": "result", "toolCallId": "ckpt-bad",
                                       "result": {"success": True, "truncated": False,
                                                  "content": self.refusal_text("buddy_checkpoint", "inquiry-channel-absent")}})
        self.assertEqual(self.tracker.checkpoint_calls["ckpt-bad"]["result"], "tool-refusal")
        self.event(4, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__buddy_answer_inquiry",
                                       "toolCallId": "ans-bad"})
        self.event(5, "tool.updated", {"kind": "result", "toolCallId": "ans-bad",
                                       "result": {"success": True, "truncated": False,
                                                  "content": self.refusal_text("buddy_answer_inquiry", "unknown-inquiry")}})
        self.assertEqual(self.tracker.answer_calls["ans-bad"]["result"], "tool-refusal")
        self.assertEqual(self.deliveries, [])
        self.assertEqual(self.answers, [])
        content = self.receipt_text("inquiry-checkpoint", {"inquiries": []})
        self.event(6, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__buddy_checkpoint",
                                       "toolCallId": "ckpt-good"})
        self.event(7, "tool.updated", {"kind": "result", "toolCallId": "ckpt-good",
                                       "result": {"success": True, "truncated": False, "content": content}})
        self.assertEqual(len(self.deliveries), 1)
        with self.assertRaises(NativeError) as error:
            self.event(8, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__buddy_answer_inquiry",
                                           "toolCallId": "ans-forged"})
            self.event(9, "tool.updated", {"kind": "result", "toolCallId": "ans-forged",
                                           "result": {"success": True, "truncated": False,
                                                      "content": self.refusal_text("buddy_checkpoint", "inquiry-channel-absent")}})
        self.assertEqual(error.exception.code, "invalid-tool-refusal")

    def test_forged_truncated_or_duplicate_results_fail_the_turn(self):
        content = self.receipt_text("inquiry-checkpoint", {"inquiries": []})
        for case, result in (
            ("forged", {"success": True, "truncated": False, "content": content[:-8] + '"changed"}'}),
            ("truncated", {"success": True, "truncated": True, "content": content}),
            ("no-success", {"truncated": False, "content": content}),
            ("duplicate", {"success": True, "truncated": False, "content": content}),
        ):
            with self.subTest(case=case):
                tracker = RootTurnEvidence(
                    "sess-root", "input-root", "mcp__buddy_x__buddy_finish_turn", self.bridge,
                    checkpoint_name="mcp__buddy_x__buddy_checkpoint",
                    on_delivery=lambda receipt, call: None,
                    validate_outcome=validate_outcome, mounted_tools=MOUNTED_TOOLS)
                tracker.observe({"method": "session/event", "params": {
                    "sessionId": "sess-root", "turnId": "native-turn", "seq": 1, "type": "turn.started",
                    "payload": {"inputId": "input-root"}}}, 1)
                tracker.observe({"method": "session/event", "params": {
                    "sessionId": "sess-root", "turnId": "native-turn", "seq": 2, "type": "tool.updated",
                    "payload": {"kind": "scheduled", "toolName": "mcp__buddy_x__buddy_checkpoint",
                                "toolCallId": "ckpt-1"}}}, 2)
                if case == "duplicate":
                    good = {"success": True, "truncated": False, "content": content}
                    tracker.observe({"method": "session/event", "params": {
                        "sessionId": "sess-root", "turnId": "native-turn", "seq": 3, "type": "tool.updated",
                        "payload": {"kind": "result", "toolCallId": "ckpt-1", "result": good}}}, 3)
                with self.assertRaises(NativeError) as error:
                    tracker.observe({"method": "session/event", "params": {
                        "sessionId": "sess-root", "turnId": "native-turn", "seq": 4, "type": "tool.updated",
                        "payload": {"kind": "result", "toolCallId": "ckpt-1", "result": result}}}, 4)
                self.assertIn(error.exception.code, ("invalid-inquiry-receipt", "invalid-provenance"))

    def test_a_result_without_its_scheduled_call_is_ignored(self):
        self.started()
        content = self.receipt_text("inquiry-checkpoint", {"inquiries": []})
        self.event(2, "tool.updated", {"kind": "result", "toolCallId": "never-scheduled",
                                       "result": {"success": True, "truncated": False, "content": content}})
        self.assertEqual(self.deliveries, [])

    def test_no_inquiry_tool_may_be_scheduled_after_the_finish_call(self):
        self.started()
        self.event(2, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__buddy_finish_turn",
                                       "toolCallId": "finish-1"})
        with self.assertRaises(NativeError) as error:
            self.event(3, "tool.updated", {"kind": "scheduled", "toolName": "mcp__buddy_x__buddy_checkpoint",
                                           "toolCallId": "late-ckpt"})
        self.assertEqual(error.exception.code, "duplicate-finish")

    def test_thousands_of_inquiry_calls_stay_bounded_and_functional(self):
        # An unlimited (timeoutSeconds: 0) turn may checkpoint constantly. Every
        # verified receipt is handed to its callback and never retained, only a
        # bounded recent window of terminal call identities is kept, and no
        # cumulative count ever ends the turn.
        from hey_my_buddy.buddy.harnesses.zcode.protocol import MAX_RETAINED_INQUIRY_CALLS

        self.started()
        content = self.receipt_text("inquiry-checkpoint", {"inquiries": []})
        total = 1200
        for index in range(total):
            self.event(2 * index + 2, "tool.updated", {"kind": "scheduled",
                                                       "toolName": "mcp__buddy_x__buddy_checkpoint",
                                                       "toolCallId": f"ckpt-{index}"})
            self.event(2 * index + 3, "tool.updated", {"kind": "result", "toolCallId": f"ckpt-{index}",
                                                       "result": {"success": True, "truncated": False,
                                                                  "content": content}})
        self.assertEqual(len(self.deliveries), total)
        self.assertLessEqual(len(self.tracker.checkpoint_calls), MAX_RETAINED_INQUIRY_CALLS)
        self.assertTrue(all(call["result"] in (None, "tool-error", "tool-refusal", "receipt-verified")
                            for call in self.tracker.checkpoint_calls.values()),
                        "a verified checkpoint receipt payload must never be retained")
        self.assertFalse(any(isinstance(call["result"], dict) for call in self.tracker.checkpoint_calls.values()))
        # The turn is still fully functional after all those checkpoints...
        self.event(2 * total + 2, "tool.updated", {"kind": "scheduled",
                                                   "toolName": "mcp__buddy_x__buddy_checkpoint",
                                                   "toolCallId": "ckpt-live"})
        self.event(2 * total + 3, "tool.updated", {"kind": "result", "toolCallId": "ckpt-live",
                                                   "result": {"success": True, "truncated": False,
                                                              "content": content}})
        self.assertEqual(len(self.deliveries), total + 1)
        # ...a duplicate terminal result for a retained call still fails...
        with self.assertRaises(NativeError):
            self.event(2 * total + 4, "tool.updated", {"kind": "result", "toolCallId": "ckpt-live",
                                                       "result": {"success": True, "truncated": False,
                                                                  "content": content}})
        # ...and a result for an evicted ancient call identity is ignored (never
        # imported) instead of raising or delivering again.
        self.event(2 * total + 5, "tool.updated", {"kind": "result", "toolCallId": "ckpt-0",
                                                   "result": {"success": True, "truncated": False,
                                                              "content": content}})
        self.assertEqual(len(self.deliveries), total + 1)


class NativeFailureAttributionTests(unittest.TestCase):
    def test_structured_labels_cannot_smuggle_urls_prose_or_credential_prefixes(self):
        failure = decode_native_failure({"error": {
            "type": "https://private.invalid/path", "code": "Bearer-private-token",
            "attribution": {"source": {"bad": "shape"}, "reason": "private prompt text",
                            "providerId": "sk-private-key", "providerErrorCode": "1308", "statusCode": 429},
        }})
        self.assertIsNone(failure["errorType"])
        self.assertIsNone(failure["code"])
        self.assertEqual(failure["attribution"], {"providerErrorCode": "1308", "statusCode": 429})
        self.assertNotIn("private", json.dumps(failure))

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
        self.assertEqual(failure["errorType"], "E" * 160)
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
                                  validate_outcome=validate_outcome, mounted_tools=("buddy_finish_turn",))
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
                               validate_outcome=validate_outcome, mounted_tools=("buddy_finish_turn",))
        quiet.observe({"method": "session/event", "params": {"sessionId": "sess-root", "turnId": "native-turn",
                       "seq": 1, "type": "turn.started", "payload": {"inputId": "input-root"}}}, 1)
        with self.assertRaises(NativeError) as prompt_error:
            quiet.observe({"method": "state.updated", "params": {"sessionId": "sess-root",
                           "reason": "prompt_failed", "patch": {"anything": "opaque"}}}, 2)
        self.assertEqual(prompt_error.exception.code, "native-turn-failed")
        self.assertIsNone(prompt_error.exception.failure)


class ConfigurationTests(unittest.TestCase):
    def test_controller_refuses_incomplete_configuration_before_native_setters(self):
        configuration = {"provider": "fixture-api", "model": "fixture-model", "effort": "low"}
        snapshot = {"session": {"sessionId": "native-default"}, "settings": {"model": {"current": {
            "providerId": "fixture-api", "modelId": "fixture-model", "options": {"reasoningLevel": "low"},
        }}}}
        for key in configuration:
            for value in (None, "", " \t", False):
                with self.subTest(key=key, value=value):
                    requested = {**configuration, key: value}
                    if value is None:
                        requested.pop(key)
                    connection = mock.Mock()
                    with self.assertRaises(NativeError) as error:
                        configure_session(connection, snapshot, requested, {"fixture-api": "api-key"})
                    self.assertEqual(error.exception.code, "invalid-configuration")
                    connection.call.assert_not_called()

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
        self.assertEqual(len(result["providers"]), 1)
        provider = result["providers"][0]
        self.assertEqual(provider["adapter"], "zcode")
        self.assertEqual(provider["packageVersion"], "native-test-version")
        models = {entry["id"]: entry for entry in provider["models"]}
        self.assertEqual(set(models), {"vision", "fast"})
        self.assertEqual(models["vision"]["efforts"], ["high", "max"])
        self.assertEqual(models["fast"]["efforts"], ["low"])
        self.assertEqual(models["vision"]["inputModalities"], ["text", "image"])
        self.assertTrue(all(entry["available"] for entry in models.values()))
        self.assertTrue(any("effort" in warning for warning in result["warnings"]))
        self.assertTrue(any("OAuth" in warning for warning in result["warnings"]))


class UnlimitedDeadlineTests(unittest.TestCase):
    def test_zero_timeout_maps_to_an_infinite_overall_deadline_only(self):
        self.assertEqual(execution_deadline(0), math.inf)
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
        connection = NativeConnection(process, math.inf, threading.Event())
        started = time.monotonic()
        connection.pump()  # no message arrives: the bounded per-pump wait must still return
        self.assertLess(time.monotonic() - started, 5.0)
        connection.cancelled.set()
        with self.assertRaises(NativeError) as error:
            connection.pump()
        self.assertEqual(error.exception.code, "cancelled")


class OwnedProcessEvidenceTests(unittest.TestCase):
    def test_failed_group_observation_cannot_confirm_shutdown(self):
        process = mock.Mock(pid=12345)
        process.poll.return_value = 0
        with mock.patch("hey_my_buddy.buddy.harnesses.base.os.getpgid", return_value=12345):
            handle = ProcessHandle(process, own_group=True, log_paths={})
        for error in (OSError(errno.EIO, "process observation unavailable"), PermissionError()):
            with self.subTest(error=type(error).__name__), mock.patch("hey_my_buddy.buddy.harnesses.base.os.killpg", side_effect=error):
                self.assertFalse(handle.shutdown_confirmed(settle_seconds=0))
        with mock.patch("hey_my_buddy.buddy.harnesses.base.os.killpg", side_effect=ProcessLookupError()):
            self.assertTrue(handle.shutdown_confirmed(settle_seconds=0))


if __name__ == "__main__":
    unittest.main()
