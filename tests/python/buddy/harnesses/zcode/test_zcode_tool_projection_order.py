"""Native operation/telemetry/canonical order, using private in-memory fixtures."""
from __future__ import annotations

import unittest

from hey_my_buddy.protocol import tool_evidence
from hey_my_buddy.buddy.harnesses.zcode.tool_evidence import ZcodeToolFacts


BINDING = {"adapter": "zcode", "taskId": "projection", "attemptId": "attempt", "generation": 1}
ROOT = {"sessionId": "session", "turnId": "turn"}


class NativeProjectionTests(unittest.TestCase):
    def setUp(self):
        self.facts = ZcodeToolFacts(BINDING)
        self.facts.add_root("session", "turn")
        self.seq = 0
        self.telemetry_seq = 0
        self.operation_seq = 0
        self.canonical("turn.started", {"inputId": "input"})

    def canonical(self, kind, payload, **identity):
        self.seq += 1
        self.facts.observe({"method": "session/event", "params": {
            **ROOT, **identity, "seq": self.seq, "type": kind, "payload": payload}})

    def metadata(self, phase, call="read", name="Read", *, turn="turn", **extra):
        self.telemetry_seq += 1
        params = {"kind": "tool.lifecycle", "sessionId": "session", "eventSeq": self.telemetry_seq,
                  "phase": phase, "toolCallId": call, **extra}
        if turn is not None:
            params["turnId"] = turn
        if name is not None:
            params["toolName"] = name
        self.facts.observe({"method": "v4/telemetry/event", "params": params})

    def operation(self, phase, call="read", name="Read", *, turn="turn"):
        self.operation_seq += 1
        params = {"kind": "tool-" + phase, "sessionId": "session", "sequenceNumber": self.operation_seq,
                  "toolCallId": call}
        if turn is not None:
            params["turnId"] = turn
        if name is not None:
            params["toolName"] = name
        self.facts.observe({"method": "computer-use/operation-event", "params": params})

    def scheduled(self, call="read", name="Read"):
        self.operation("scheduled", call, name)
        self.metadata("scheduled", call, name)
        self.canonical("tool.updated", {"kind": "scheduled", "toolCallId": call, "toolName": name})

    def result(self, call="read", success=True):
        self.metadata("completed" if success else "failed", call)
        self.canonical("tool.updated", {"kind": "result", "toolCallId": call, "result": {"success": success}})

    def judge(self):
        return tool_evidence.judge_tool_evidence(self.facts.finish(True), "review", False)

    def test_native_order_deduplicates_starts_and_uses_only_canonical_end(self):
        self.scheduled()
        self.operation("started", turn=None, name=None)
        self.metadata("started", turn=None)
        self.canonical("tool.updated", {"kind": "started", "toolCallId": "read"})
        self.metadata("progress")
        self.canonical("tool.updated", {"kind": "progress", "toolCallId": "read"})
        self.result()
        self.canonical("tool.updated", {"kind": "batch", "toolCallIds": ["read"],
                                        "successCount": 1, "errorCount": 0})
        self.assertIsNone(self.judge())
        package = self.facts.finish(True)
        self.assertEqual(package["toolCalls"], 1)
        self.assertEqual([event["phase"] for event in package["events"]], ["start", "end"])

    def test_metadata_terminal_cannot_replace_canonical_result(self):
        self.scheduled()
        self.metadata("completed")
        self.assertIsNotNone(self.judge())
        self.assertEqual([event["phase"] for event in self.facts.finish(True)["events"]], ["start"])

    def test_metadata_start_without_canonical_start_stays_incomplete(self):
        self.operation("scheduled")
        self.metadata("scheduled", turn=None)
        self.canonical("tool.updated", {"kind": "result", "toolCallId": "read", "result": {"success": True}})
        self.assertIsNotNone(self.judge())
        self.assertFalse(self.facts.finish(True)["streamComplete"])

    def test_missing_canonical_identity_cannot_borrow_the_metadata_turn(self):
        self.operation("scheduled")
        self.canonical("tool.updated", {"kind": "scheduled", "toolCallId": "read", "toolName": "Read"}, turnId=None)
        self.canonical("tool.updated", {"kind": "result", "toolCallId": "read"})
        self.assertIsNotNone(self.judge())
        self.assertTrue(any(event["nativeIdentity"] == {"sessionId": "session"}
                            for event in self.facts.finish(True)["events"]))

    def test_unknown_optional_turn_is_not_invented(self):
        self.metadata("started", turn=None)
        self.assertIsNotNone(self.judge())
        self.assertEqual(self.facts.finish(True)["events"][0]["nativeIdentity"], {"sessionId": "session"})

    def test_optional_turn_requires_a_unique_known_native_call(self):
        self.scheduled()
        self.facts.add_root("session", "second-turn")
        self.facts.observe({"method": "session/event", "params": {
            "sessionId": "session", "turnId": "second-turn", "type": "tool.updated", "seq": 3,
            "payload": {"kind": "scheduled", "toolCallId": "read", "toolName": "Read"}}})
        self.metadata("started", turn=None)
        self.assertFalse(self.facts.finish(True)["streamComplete"])
        self.assertEqual(self.facts.finish(True)["events"][-1]["nativeIdentity"], {"sessionId": "session"})

    def test_names_scope_and_foreign_metadata_remain_incomplete(self):
        for extra in ({"toolName": "Bash"}, {"childSessionId": "child"}, {"background": True},
                      {"parentToolCallId": "parent"}, {"phase": "future"}):
            with self.subTest(extra=extra):
                self.setUp()
                self.scheduled()
                params = {"kind": "tool.lifecycle", **ROOT, "eventSeq": 2,
                          "phase": "started", "toolCallId": "read", "toolName": "Read", **extra}
                self.facts.observe({"method": "v4/telemetry/event", "params": params})
                self.canonical("tool.updated", {"kind": "result", "toolCallId": "read"})
                self.assertIsNotNone(self.judge())
        self.setUp()
        self.facts.observe({"method": "v4/telemetry/event", "params": {
            "kind": "tool.lifecycle", "sessionId": "foreign", "turnId": "foreign-turn",
            "eventSeq": 1, "phase": "scheduled", "toolCallId": "read", "toolName": "Bash"}})
        self.assertTrue(self.facts.finish(False)["events"])
        self.assertIsNotNone(self.judge())

    def test_late_metadata_and_conflicting_terminal_facts_remain_incomplete(self):
        self.scheduled()
        self.result()
        self.metadata("progress")
        self.assertIsNotNone(self.judge())
        self.setUp()
        self.scheduled()
        self.metadata("completed")
        self.canonical("tool.updated", {"kind": "error", "toolCallId": "read", "error": {"code": "failed"}})
        self.assertIsNotNone(self.judge())

    def test_batch_cannot_complete_or_hide_unknown_calls(self):
        for ended, payload in (
                (False, {"toolCallIds": ["read"], "successCount": 1, "errorCount": 0}),
                (True, {"toolCallIds": ["missing"], "successCount": 1, "errorCount": 0}),
                (True, {"toolCallIds": ["read", "read"], "successCount": 2, "errorCount": 0}),
                (True, {"toolCallIds": ["read"], "successCount": True, "errorCount": 0}),
                (True, {"toolCallIds": ["read"], "successCount": 0, "errorCount": 1})):
            with self.subTest(payload=payload):
                self.setUp()
                self.scheduled()
                if ended:
                    self.result()
                self.canonical("tool.updated", {"kind": "batch", **payload})
                self.assertIsNotNone(self.judge())

    def test_batch_counts_actual_failures_and_rejects_repeat_or_closed_root(self):
        self.scheduled()
        self.result(success=False)
        payload = {"kind": "batch", "toolCallIds": ["read"], "successCount": 0, "errorCount": 1}
        self.canonical("tool.updated", payload)
        self.assertIsNone(self.judge())
        self.setUp()
        self.scheduled()
        self.result(success=False)
        self.canonical("tool.updated", payload)
        self.canonical("tool.updated", payload)
        self.assertIsNotNone(self.judge())

    def test_conflicting_canonical_ends_cannot_be_hidden_by_the_last_batch_count(self):
        for first, second in ((True, False), (False, True), (None, True), (True, True)):
            with self.subTest(first=first, second=second):
                self.setUp()
                self.scheduled()
                self.canonical("tool.updated", {"kind": "result", "toolCallId": "read",
                                                "result": {"success": first}})
                self.canonical("tool.updated", {"kind": "error", "toolCallId": "read"}
                               if second is False else {"kind": "result", "toolCallId": "read",
                                                        "result": {"success": second}})
                self.canonical("tool.updated", {"kind": "batch", "toolCallIds": ["read"],
                                                "successCount": int(second), "errorCount": int(not second)})
                if first is second:
                    self.assertIsNone(self.judge())
                else:
                    self.assertIsNotNone(self.judge())
        self.setUp()
        self.scheduled()
        self.result()
        self.canonical("turn.completed", {"inputId": "input", "resultType": "success", "response": "{}"})
        self.canonical("tool.updated", {"kind": "batch", "toolCallIds": ["read"], "successCount": 1, "errorCount": 0})
        self.assertIsNotNone(self.judge())


if __name__ == "__main__":
    unittest.main()
