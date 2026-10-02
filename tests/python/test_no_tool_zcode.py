"""No paid model calls: a fake ZCode app-server and native-event adversarial cases.

The fake app-server and its harness moved to ``test_zcode_tool_evidence`` so the
unified tool-evidence wiring runs against the same server; every no-tool case
here keeps asserting the native no-tool parameters and the refusal behavior.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from buddy.adapters.zcode_protocol import NativeError
from buddy.adapters.zcode_runner import NoToolEvidence

from test_zcode_tool_evidence import FakeAppServerTests


class NoToolEvidenceTests(unittest.TestCase):
    def event(self, kind, payload=None, *, session="root", turn="turn", seq=2):
        return {"method": "session/event", "params": {"sessionId": session, "turnId": turn,
                "seq": seq, "type": kind, "payload": payload or {}}}

    def test_all_tool_event_forms_violate_even_without_id_or_in_child(self):
        for kind, payload, session in (
            ("tool.updated", {"kind": "scheduled", "toolName": "shell"}, "root"),
            ("tool.updated", {"kind": "result", "toolName": "mcp"}, "root"),
            ("tool.updated", {"kind": "scheduled", "source": "child", "toolName": "search"}, "child"),
            ("agent.started", {}, "root"),
            ("part.started", {"part": {"type": "tool", "name": "Read"}}, "root"),
            ("message.upserted", {"message": {"parts": [{"type": "tool_use"}]}}, "root"),
        ):
            with self.subTest(kind=kind, session=session):
                evidence = NoToolEvidence("root", "input")
                evidence.observe(self.event("turn.started", {"inputId": "input"}, seq=1), 1)
                with self.assertRaises(NativeError) as caught:
                    evidence.observe(self.event(kind, payload, session=session), 2)
                self.assertEqual(caught.exception.code, "no-tool-violation")

    def test_unknown_and_incomplete_events_cannot_settle(self):
        evidence = NoToolEvidence("root", "input")
        evidence.observe(self.event("turn.started", {"inputId": "input"}, seq=1), 1)
        with self.assertRaises(NativeError) as caught:
            evidence.observe(self.event("future.event"), 2)
        self.assertEqual(caught.exception.code, "invalid-protocol")
        self.assertFalse(evidence.settled)

    def test_projected_metadata_never_proves_completion_and_rejects_tools(self):
        for method, key, kind in (
            ("computer-use/operation-event", "sequenceNumber", "tool-scheduled"),
            ("computer-use/operation-event", "sequenceNumber", "tool-started"),
            ("v4/telemetry/event", "eventSeq", "tool.lifecycle"),
            ("v4/telemetry/event", "eventSeq", "permission.lifecycle"),
            ("v4/telemetry/event", "eventSeq", "subagent.lifecycle"),
            ("v4/telemetry/event", "eventSeq", "workflow.lifecycle"),
        ):
            with self.subTest(kind=kind):
                evidence = NoToolEvidence("root", "input")
                with self.assertRaises(NativeError) as caught:
                    evidence.observe({"method": method, "params": {
                        "sessionId": "child", "turnId": "other", "kind": kind, key: 1}}, 1)
                self.assertEqual(caught.exception.code, "no-tool-violation")
        evidence = NoToolEvidence("root", "input")
        evidence.observe({"method": "computer-use/operation-event", "params": {
            "sessionId": "root", "turnId": "turn", "kind": "turn-completed", "sequenceNumber": 1}}, 1)
        self.assertFalse(evidence.completed)
        self.assertFalse(evidence.settled)
        with self.assertRaises(NativeError) as caught:
            evidence.observe(self.event("turn.started", {"inputId": "input"}, turn="foreign", seq=1), 2)
        self.assertEqual(caught.exception.code, "wrong-native-turn")

    def test_unknown_or_reordered_metadata_fails_closed(self):
        for kinds in (("compaction.terminal",), ("usage.delta", "usage.delta")):
            evidence = NoToolEvidence("root", "input")
            with self.assertRaises(NativeError) as caught:
                for kind in kinds:
                    evidence.observe({"method": "v4/telemetry/event", "params": {
                        "sessionId": "root", "turnId": "turn", "kind": kind, "eventSeq": 1}}, 1)
            self.assertEqual(caught.exception.code, "invalid-protocol")


class NoToolFakeProtocolTests(FakeAppServerTests):
    def test_zero_tool_and_one_format_correction(self):
        for case, count in (("ok", 0), ("correct", 1), ("native-stream", 0)):
            with self.subTest(case=case):
                outcome = self.execute(case)
                self.assertEqual(outcome.status, "ok", outcome.to_report())
                self.assertTrue(outcome.result["zeroToolVerified"])
                self.assertEqual(outcome.result["usage"]["toolCalls"], 0)
                self.assertEqual(outcome.result["correctionCount"], count)
                self.assertNotIn("private-test-secret", json.dumps(outcome.to_report()))

    def test_out_of_bounds_choice_is_not_corrected(self):
        outcome = self.execute("enum")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertFalse(outcome.result["answerValid"])
        self.assertEqual(outcome.result["correctionCount"], 0)

    def test_tool_unknown_and_truncated_stream_fail_closed(self):
        for case, code in (("tool", "no-tool-violation"), ("post-close-tool", "no-tool-violation"),
                           ("unknown", "invalid-protocol"),
                           ("truncated", "native-disconnected")):
            with self.subTest(case=case):
                outcome = self.execute(case)
                self.assertEqual(outcome.status, "failed")
                self.assertEqual(outcome.result["code"], code)
                self.assertNotIn("zeroToolVerified", outcome.result)
                if code == "no-tool-violation":
                    self.assertEqual(outcome.result["usage"]["toolCalls"], 0)
                    self.assertFalse(outcome.result["toolEvidence"]["streamComplete"])
                    self.assertTrue(outcome.result["toolEvidence"]["events"])

    def test_deadline(self):
        outcome = self.execute("timeout", timeout=1)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "timeout")
        self.assertTrue(outcome.shutdown_confirmed)

    def test_cancel(self):
        outcome = self.execute("timeout", timeout=10, cancel=True)
        self.assertEqual(outcome.status, "cancelled", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertNotIn("zeroToolVerified", outcome.result)


if __name__ == "__main__":
    unittest.main()
