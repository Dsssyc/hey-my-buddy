"""The dsh tool-fact projection: facts collected by name, never judged.

The ACP stream reports every call with the category ``other``; this harness
classifies through the shared fixed table by native tool name and retains
everything — a known name, an unrecognized name, a call without identity — as
facts for the blackboard's judge. The retired direct-LLM stream's witnesses
(zero-tool completeness, correction accumulation, truncation and stream breaks)
are registered in the wiring disposition table: they now run against the real
ACP stream in the native-run suite, and the shared collector's contract is
pinned here at the unit level where a process adds nothing.
"""
from __future__ import annotations
import unittest

from hey_my_buddy.buddy.harnesses.dsh.protocol import DshToolFacts
from hey_my_buddy.protocol.tool_evidence import (
    MAX_TOOL_EVENTS, TOOL_EVIDENCE_UNVERIFIED, TOOLS_FORBIDDEN, judge_tool_evidence)

BINDING = {"adapter": "dsh", "taskId": "task", "attemptId": "attempt", "generation": 1}


def update(session_id="session-1", call_id="call-1", name="read", kind="tool_call", **extra):
    frame = {"sessionUpdate": kind, "toolCallId": call_id, "kind": "other", "title": name}
    frame.update(extra)
    return {"method": "session/update", "params": {"sessionId": session_id, "update": frame}}


class DshToolFactsTests(unittest.TestCase):
    def test_known_names_classify_through_the_fixed_table_and_unknown_stay_other(self):
        for name, category in (("read", "read"), ("glob", "search"), ("run-command", "other")):
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


if __name__ == "__main__":
    unittest.main()
