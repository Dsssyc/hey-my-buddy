"""Shared active-turn delivery uses the existing journal and signed session tools."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hey_my_buddy.buddy.harnesses.in_turn_inquiry import prepare_inquiry_services
from hey_my_buddy.buddy.harnesses.inquiry_bridge import InquiryBridge
from hey_my_buddy.buddy.harnesses.session_receipts import ReceiptError
from hey_my_buddy.buddy.roles.session_mcp import respond


class InTurnInquiryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.identity = dict(taskId="task", attemptId="attempt", generation=1, turnId="turn")
        self.services = prepare_inquiry_services(
            invocation_root=root, identity=self.identity, input_sha256="a" * 64,
            attention_path=root / "attention.json", inquiry={"resultsPath": str(root / "inquiries.jsonl")})
        self.bridge = InquiryBridge(
            identity=self.identity, journal_path=self.services.configuration["inquiryJournalPath"],
            error_factory=ReceiptError, event_metadata=lambda _event: {"kind": "test"},
            delivery_mode="realtime")
        self.bridge.start()
        self.addCleanup(self.bridge.close)

    def ask(self, inquiry_id="q", question="What passed?"):
        return self.bridge._ask({"inquiryId": inquiry_id, "question": question},
                                lambda ok, **kwargs: {"ok": ok, **kwargs})

    def call(self, name, arguments):
        return respond({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": name, "arguments": arguments}},
                       self.services.configuration)["result"]

    def accept(self, result, tool="hey_my_buddy_answer_inquiry"):
        self.services.accept_tool_result(self.bridge, f"mcp__{self.services.server_name}__{tool}",
                                         result["content"][0]["text"], "root-call")

    def test_active_window_delivery_duplicate_and_late_question(self):
        self.assertFalse(self.ask()["ok"])
        self.bridge.activate("native-session")
        self.assertTrue(self.ask()["ok"])
        entry, = self.bridge.pending_inquiries()
        self.bridge.deliver_native(entry["inquiryId"], {"sessionId": "native-session", "turnId": "native-turn"})
        self.assertEqual(self.bridge.pending_inquiries(), [])
        self.assertTrue(self.ask()["value"]["duplicate"])
        self.bridge.deactivate()
        self.assertEqual(self.ask("late")["error"], "agent-not-running")
        self.bridge.close()
        self.assertEqual(self.bridge.report()["unansweredDelivered"], 1)
        self.assertEqual(self.bridge.entries["q"]["state"], "unavailable")
        self.assertEqual(self.bridge.snapshot()["deliveryMode"], "realtime")

    def test_only_signed_attempt_bound_tool_receipt_records_answer(self):
        self.bridge.activate("native-session")
        self.ask()
        self.bridge.deliver_native("q", {"turnId": "native-turn"})
        result = self.call("hey_my_buddy_answer_inquiry", {"inquiryId": "q", "answer": "Focused checks passed"})
        self.accept(result)
        self.assertEqual(self.bridge.entries["q"]["answer"]["text"], "Focused checks passed")
        self.assertEqual(self.bridge.report()["unansweredDelivered"], 0)
        forged = json.loads(result["content"][0]["text"])
        forged["identity"]["turnId"] = "foreign-turn"
        result["content"][0]["text"] = json.dumps(forged)
        with self.assertRaises(ReceiptError):
            self.accept(result)

    def test_plain_text_and_foreign_tools_cannot_answer(self):
        self.bridge.activate("native-session")
        self.ask()
        for name, raw in ((f"mcp__{self.services.server_name}__hey_my_buddy_answer_inquiry", "I checked it"),
                          ("mcp__foreign__hey_my_buddy_answer_inquiry", "{}")):
            with self.assertRaises(ReceiptError):
                self.services.accept_tool_result(self.bridge, name, raw, "call")
        self.assertEqual(self.bridge.entries["q"]["state"], "queued")

    def test_unread_native_input_closes_as_unavailable(self):
        self.bridge.activate("native-session")
        self.ask()
        self.bridge.deliver_native("q", {"sessionId": "native-session"})
        self.bridge.close()
        self.assertEqual(self.bridge.entries["q"]["state"], "unavailable")
        self.assertEqual(set(json.loads(Path(self.bridge.journal_path).read_text(encoding="utf-8").splitlines()[-1])),
                         {"version", "taskId", "attemptId", "generation", "turnId", "sessionId",
                          "inquiryId", "state", "unavailableAt"})
        self.assertEqual(self.bridge.report()["unansweredDelivered"], 1)

    def test_client_answer_requires_delivered_question_and_never_overwrites(self):
        self.bridge.checkpoint_tool = None
        self.bridge.activate("native-session")
        self.ask()
        self.assertIsNotNone(self.bridge.answer_native("unknown", "42", "call"))
        self.assertIsNotNone(self.bridge.answer_native("q", "42", "call"))
        self.bridge.deliver_native("q", {"turnId": "native-turn"})
        self.assertIsNone(self.bridge.answer_native("q", "42", "call"))
        self.assertIsNotNone(self.bridge.answer_native("q", "43", "next-call"))
        self.assertEqual(self.bridge.entries["q"]["answer"]["text"], "42")
        self.assertEqual(self.bridge.entries["q"]["answer"]["toolCallId"], "call")
        self.assertNotIn("checkpointTool", self.bridge.snapshot()["replyTool"])

    def test_native_schema_service_exposes_only_inquiry_tools(self):
        response = respond({"id": 1, "method": "tools/list"}, self.services.configuration)
        self.assertEqual([tool["name"] for tool in response["result"]["tools"]],
                         ["hey_my_buddy_checkpoint", "hey_my_buddy_answer_inquiry"])
        self.assertTrue(self.call("hey_my_buddy_finish_turn", {})["isError"])
        self.bridge.activate("native-session")
        refusal = self.call("hey_my_buddy_answer_inquiry", {"inquiryId": "missing", "answer": "x"})
        self.accept(refusal)
        self.assertEqual(self.bridge.entries, {})


if __name__ == "__main__":
    unittest.main()
