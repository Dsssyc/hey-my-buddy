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

from buddy.adapters.base import ProcessHandle
from buddy.adapters.zcode_mcp import respond
from buddy.adapters.zcode_protocol import (NativeConnection, NativeError, RootTurnEvidence, decode_json,
                                           decode_native_failure, sign_receipt, verify_inquiry_receipt,
                                           verify_receipt)
from buddy.adapters.zcode_runner import catalog, configure_session, execution_deadline


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
        self.assertEqual(verify_receipt(raw, self.bridge)["outcome"], self.outcome)
        for key, value in (("inputSha256", "c" * 64), ("outcome", {**self.outcome, "summary": "forged"}),
                           ("identity", {**self.bridge["identity"], "generation": 2})):
            with self.subTest(key=key):
                changed = json.loads(raw)
                changed[key] = value
                with self.assertRaises(NativeError):
                    verify_receipt(json.dumps(changed), self.bridge)
        other_attempt = {**self.bridge, "identity": {**self.bridge["identity"], "attemptId": "other"}}
        with self.assertRaises(NativeError):
            verify_receipt(raw, other_attempt)

    def test_prose_duplicate_members_and_nonfinite_values_are_rejected(self):
        raw = self.receipt()
        for invalid in ("Here is the result: " + raw, "```json\n" + raw + "\n```", raw + raw):
            with self.assertRaises(NativeError):
                verify_receipt(invalid, self.bridge)
        for invalid in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'):
            with self.assertRaises(ValueError):
                decode_json(invalid)

    def test_wrong_input_and_reordered_events_cannot_establish_a_root(self):
        tracker = RootTurnEvidence("sess-root", "input-root", "finish", self.bridge)
        def event(seq, input_id):
            return {"method": "session/event", "params": {"sessionId": "sess-root", "turnId": "native-turn", "seq": seq,
                    "type": "turn.started", "payload": {"inputId": input_id}}}
        with self.assertRaises(NativeError):
            tracker.observe(event(1, "wrong-input"), 1)
        tracker = RootTurnEvidence("sess-root", "input-root", "finish", self.bridge)
        tracker.observe(event(2, "input-root"), 1)
        with self.assertRaises(NativeError):
            tracker.observe(event(1, "input-root"), 2)

    def test_root_relayed_child_finish_does_not_consume_root_receipt(self):
        tracker = RootTurnEvidence("sess-root", "input-root", "finish", self.bridge)
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
        tracker = RootTurnEvidence("sess-root", "input-root", "finish", self.bridge)
        self.root_event(tracker, 1, "turn.started", {"inputId": "input-root"})
        return tracker

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
            on_answer=lambda receipt, call: self.answers.append((receipt, call)))

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
                    on_delivery=lambda receipt, call: None)
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
        from buddy.adapters.zcode_protocol import MAX_RETAINED_INQUIRY_CALLS

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
        self.assertTrue(all(call["result"] in (None, "tool-error", "receipt-verified")
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

        tracker = RootTurnEvidence("sess-root", "input-root", "finish", bridge)
        tracker.observe({"method": "session/event", "params": {"sessionId": "sess-root", "turnId": "native-turn",
                        "seq": 1, "type": "turn.started", "payload": {"inputId": "input-root"}}}, 1)
        with self.assertRaises(NativeError) as error:
            tracker.observe(turn_event("turn.failed", {"error": self.QUOTA_ERROR, "turnPhase": "stream"}), 2)
        self.assertEqual(error.exception.code, "native-turn-failed")
        self.assertEqual(error.exception.failure["attribution"]["statusCode"], 429)
        self.assertIn("rate_limited", str(error.exception))
        # The state.updated prompt_failed envelope exports only a reason string
        # and an opaque patch, so no attribution is manufactured for it.
        quiet = RootTurnEvidence("sess-root", "input-root", "finish", bridge)
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
        with mock.patch("buddy.adapters.base.os.getpgid", return_value=12345):
            handle = ProcessHandle(process, own_group=True, log_paths={})
        for error in (OSError(errno.EIO, "process observation unavailable"), PermissionError()):
            with self.subTest(error=type(error).__name__), mock.patch("buddy.adapters.base.os.killpg", side_effect=error):
                self.assertFalse(handle.shutdown_confirmed(settle_seconds=0))
        with mock.patch("buddy.adapters.base.os.killpg", side_effect=ProcessLookupError()):
            self.assertTrue(handle.shutdown_confirmed(settle_seconds=0))


if __name__ == "__main__":
    unittest.main()
