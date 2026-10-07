"""The dsh tool-fact projection: facts collected by name, never judged.

The ACP stream reports every call with the category ``other``; this harness
classifies through the shared fixed table by native tool name and retains
everything — a known name, an unrecognized name, a call without identity — as
facts for the blackboard's judge. The retired direct-LLM stream's witnesses
(zero-tool completeness, correction accumulation, truncation and stream breaks)
are registered in the wiring disposition table: they now run against the real
ACP stream in the native-run suite, and the shared collector's contract is
pinned here at the unit level where a process adds nothing.

Since the ADR-025 step-5 merge this file also pins the dsh receipt seam: the
finish receipt, the tool-refusal envelope and the inquiry receipts verify
through exactly the shared ``harnesses.session_receipts`` implementation — the
same judgment that guards ZCode — and only the failure type is this
protocol's NativeError. The end-to-end native-run suite exercises the same
names through the real root-turn tracker.
"""
from __future__ import annotations
import json
import unittest

from hey_my_buddy.buddy.harnesses.dsh.protocol import (
    DshToolFacts,
    NativeError,
    refusal_shaped,
    verify_finish_receipt,
    verify_inquiry_receipt,
    verify_tool_refusal,
)
from hey_my_buddy.buddy.harnesses.session_receipts import sign_receipt
from hey_my_buddy.buddy.roles.session_mcp import respond
from hey_my_buddy.buddy.roles.turn_io import validate_outcome
from hey_my_buddy.protocol.tool_evidence import (
    MAX_TOOL_EVENTS, TOOL_EVIDENCE_UNVERIFIED, TOOLS_FORBIDDEN, judge_tool_evidence)

BINDING = {"adapter": "dsh", "taskId": "task", "attemptId": "attempt", "generation": 1}


def update(session_id="session-1", call_id="call-1", name="read", kind="tool_call", **extra):
    frame = {"sessionUpdate": kind, "toolCallId": call_id, "kind": "other", "title": name}
    frame.update(extra)
    return {"method": "session/update", "params": {"sessionId": session_id, "update": frame}}


class DshToolFactsTests(unittest.TestCase):
    def test_known_names_classify_through_the_fixed_table_and_unknown_stay_other(self):
        for name, category in (("read", "read"), ("glob", "search"), ("bash", "execute"), ("run-command", "other")):
            with self.subTest(name=name):
                collector = DshToolFacts(dict(BINDING))
                collector.add_root("session-1")
                collector.observe(update(call_id=f"call-{name}", name=name))
                collector.observe(update(call_id=f"call-{name}", name=name, kind="tool_call_update",
                                         status="completed"))
                package = collector.finish(True)
                events = [event for event in package["events"] if event["toolName"] == name]
                self.assertEqual([event["category"] for event in events], [category, category],
                                 "start and end carry the same fixed category")
                self.assertEqual(package["toolCalls"], 1)
                if name == "bash":
                    # Native classification does not grant sandbox authority.
                    collector.close_root("session-1")
                    self.assertEqual(judge_tool_evidence(collector.finish(True), "review", False),
                                     TOOLS_FORBIDDEN)

    def test_a_one_frame_completed_call_carries_start_and_end(self):
        collector = DshToolFacts(dict(BINDING))
        collector.add_root("session-1")
        collector.observe(update(name="grep", kind="tool_call", status="completed"))
        package = collector.finish(True)
        self.assertEqual(package["toolCalls"], 1)
        self.assertEqual([event["phase"] for event in package["events"]], ["start", "end"])

    def test_a_pending_update_settles_only_on_its_terminal_frame(self):
        collector = DshToolFacts(dict(BINDING))
        collector.add_root("session-1")
        collector.observe(update(name="read"))
        self.assertEqual(collector.finish(True)["unsettledToolCalls"], 1)
        collector.observe(update(name="read", kind="tool_call_update", status="completed"))
        package = collector.finish(True)
        self.assertEqual(package["unsettledToolCalls"], 0)
        self.assertEqual(package["toolCalls"], 1)

    def test_missing_identity_or_name_is_retained_and_never_completes_the_stream(self):
        collector = DshToolFacts(dict(BINDING))
        collector.add_root("session-1")
        collector.observe(update(call_id=None, name="read"))
        collector.observe(update(call_id="call-no-name", name=None))
        collector.observe(update(session_id=None, call_id="call-no-session", name="read"))
        package = collector.finish(True)
        self.assertEqual(len(package["events"]), 3, "each broken fact stays a bounded retained fact")
        self.assertEqual([event["callId"] for event in package["events"]],
                         [None, "call-no-name", "call-no-session"])
        self.assertFalse(package["streamComplete"],
                         "a broken fact means the stream can never pass as complete")
        self.assertEqual(package["toolCalls"], 0,
                         "a call with no identity is not a countable call")

    def test_correction_roots_accumulate_without_resetting_counts(self):
        collector = DshToolFacts(dict(BINDING))
        collector.add_root("session-1")
        collector.observe(update(session_id="session-1", call_id="call-1", name="read"))
        collector.observe(update(session_id="session-1", call_id="call-1", name="read",
                                 kind="tool_call_update", status="completed"))
        collector.add_root("session-2")
        collector.observe(update(session_id="session-2", call_id="call-2", name="grep"))
        collector.observe(update(session_id="session-2", call_id="call-2", name="grep",
                                 kind="tool_call_update", status="completed"))
        package = collector.finish(True)
        self.assertEqual(package["toolCalls"], 2)
        self.assertEqual([root["sessionId"] for root in package["nativeIdentity"]],
                         ["session-1", "session-2"])

    def test_the_identity_cap_keeps_every_surplus_call_a_retained_fact(self):
        collector = DshToolFacts(dict(BINDING))
        collector.add_root("session-1")
        for index in range(MAX_TOOL_EVENTS + 3):
            collector.observe(update(call_id=f"call-{index}", name="read"))
        package = collector.finish(True)
        self.assertEqual(package["toolCalls"], MAX_TOOL_EVENTS,
                         "only provable identities count as calls")
        self.assertFalse(package["streamComplete"],
                         "the surplus facts are incomplete and can never prove the stream")
        self.assertGreaterEqual(len(package["events"]), MAX_TOOL_EVENTS)

    def test_stream_and_verdicts_stay_separate_facts(self):
        collector = DshToolFacts(dict(BINDING))
        collector.add_root("session-1")
        collector.observe(update(name="read"))
        collector.observe(update(name="read", kind="tool_call_update", status="completed"))
        collector.close_root("session-1")
        self.assertFalse(collector.finish(False)["streamComplete"])
        self.assertEqual(collector.finish(False)["toolCalls"], 1,
                         "an unproven stream never erases the observed facts")
        proven = DshToolFacts(dict(BINDING))
        proven.add_root("session-1")
        proven.observe(update(name="read"))
        proven.observe(update(name="read", kind="tool_call_update", status="completed"))
        proven.close_root("session-1")
        self.assertTrue(proven.finish(True)["streamComplete"])

    def test_the_blackboard_judge_refuses_tool_facts_in_a_fast_answer(self):
        collector = DshToolFacts(dict(BINDING))
        collector.add_root("session-1")
        collector.observe(update(name="read"))
        collector.observe(update(name="read", kind="tool_call_update", status="completed"))
        package = collector.finish(True)
        self.assertEqual(judge_tool_evidence(package, "fast", True), TOOLS_FORBIDDEN,
                         "any recorded call refuses a fast answer")
        self.assertEqual(judge_tool_evidence({**package, "streamComplete": False}, "fast", True),
                         TOOLS_FORBIDDEN,
                         "a clearly disallowed call invalidates the answer even on a broken stream")
        self.assertEqual(judge_tool_evidence({**package, "truncated": True}, "review", True),
                         TOOL_EVIDENCE_UNVERIFIED,
                         "a truncated package is unverified, never silently allowed")


class DshReceiptSeamTests(unittest.TestCase):
    """The dsh receipt names run the shared implementation and fail natively.

    Three isolated guards, one per merged verification: the finish receipt and
    the inquiry receipt bind the attempt identity, the tool-refusal envelope
    binds its signature — a mutation to any of the three in the shared
    implementation fails the matching witness here and in
    ``test_session_receipts`` alike, for DSH and ZCode at once.
    """

    def setUp(self):
        self.bridge = {"identity": {"taskId": "goal", "attemptId": "attempt", "generation": 1, "turnId": "logical"},
                       "inputSha256": "a" * 64, "key": "b" * 64}
        self.outcome = {"disposition": "completed", "summary": "done", "remaining": [], "decisions": [], "artifacts": [], "request": None}
        self.native = "mcp__buddy_x__buddy_finish_turn"

    def test_the_finish_receipt_verifies_and_binds_identity_through_the_dsh_name(self):
        raw = respond({"id": 1, "method": "tools/call", "params": {"name": "buddy_finish_turn", "arguments": self.outcome}},
                      self.bridge)["result"]["content"][0]["text"]
        self.assertEqual(verify_finish_receipt(raw, self.bridge, validate_outcome)["outcome"], self.outcome)
        other = {**self.bridge, "identity": {**self.bridge["identity"], "attemptId": "other"}}
        with self.assertRaises(NativeError) as error:
            verify_finish_receipt(raw, other, validate_outcome)
        self.assertEqual(error.exception.code, "invalid-finish")

    def test_a_tampered_refusal_envelope_fails_its_signature_through_the_dsh_name(self):
        envelope = {"version": 1, "kind": "tool-refusal", "identity": self.bridge["identity"],
                    "inputSha256": self.bridge["inputSha256"], "tool": "buddy_finish_turn",
                    "reason": "invalid-arguments", "detail": "the turn request references are invalid",
                    "receiptId": "f" * 32}
        envelope["signature"] = sign_receipt(envelope, self.bridge["key"])
        raw = json.dumps(envelope)
        self.assertTrue(refusal_shaped(raw))
        self.assertEqual(verify_tool_refusal(raw, self.bridge, self.native, ("buddy_finish_turn",))["tool"],
                         "buddy_finish_turn")
        tampered = json.dumps({**envelope, "detail": "changed after signing"})
        with self.assertRaises(NativeError) as error:
            verify_tool_refusal(tampered, self.bridge, self.native, ("buddy_finish_turn",))
        self.assertEqual(error.exception.code, "invalid-tool-refusal")
        self.assertIn("signature", str(error.exception))

    def test_an_inquiry_receipt_verifies_and_binds_identity_through_the_dsh_name(self):
        answer = {"version": 1, "kind": "inquiry-answer", "identity": self.bridge["identity"],
                  "inquiryId": "q-1", "questionSha256": "c" * 64, "answer": "this", "receiptId": "e" * 32}
        answer["signature"] = sign_receipt(answer, self.bridge["key"])
        self.assertEqual(verify_inquiry_receipt(json.dumps(answer), self.bridge, "inquiry-answer")["answer"], "this")
        other = {**self.bridge, "identity": {**self.bridge["identity"], "attemptId": "other"}}
        with self.assertRaises(NativeError) as error:
            verify_inquiry_receipt(json.dumps(answer), other, "inquiry-answer")
        self.assertEqual(error.exception.code, "invalid-inquiry-receipt")


if __name__ == "__main__":
    unittest.main()
