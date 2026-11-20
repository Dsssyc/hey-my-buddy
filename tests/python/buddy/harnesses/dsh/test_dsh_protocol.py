"""Strict DSH root-turn receipt checks independent of process fixtures.

The micro task carrier's RootTurnEvidence rules for a finish that repeats —
after the accepted receipt, or before the first call's result — and for later
root tool calls, each guarded here at the evidence layer the driver owns.
"""
import json
import tempfile
import unittest
from pathlib import Path

from hey_my_buddy.buddy.harnesses.dsh.protocol import NativeError, RootTurnEvidence
from hey_my_buddy.buddy.roles.session_mcp import respond
from hey_my_buddy.protocol.turn_output import validate_outcome

IDENTITY = {"taskId": "micro-task", "attemptId": "attempt", "generation": 1, "turnId": "logical"}


class RootTurnEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="dsh-protocol-receipt-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bridge = {"identity": dict(IDENTITY), "inputSha256": "a" * 64, "key": "b" * 64,
                       "attentionPath": str(self.root / "absent-attention.json"),
                       "finishAcceptedPath": str(self.root / "finish-accepted.json")}
        self.outcome = {"disposition": "completed", "summary": "done", "remaining": [], "decisions": [],
                        "artifacts": [], "request": None}
        self.finish = "mcp__buddy_x__hey_my_buddy_finish_turn"
        self.checkpoint = "mcp__buddy_x__hey_my_buddy_checkpoint"
        self.answer = "mcp__buddy_x__hey_my_buddy_answer_inquiry"

    def receipt(self) -> str:
        result = respond({"id": 1, "method": "tools/call",
                          "params": {"name": "hey_my_buddy_finish_turn", "arguments": self.outcome}},
                         self.bridge)
        self.assertFalse(result["result"].get("isError"))
        return result["result"]["content"][0]["text"]

    def tracker(self, **extra) -> RootTurnEvidence:
        return RootTurnEvidence("sess-root", self.finish, self.bridge,
                                checkpoint_name=self.checkpoint, answer_name=self.answer,
                                validate_outcome=validate_outcome,
                                mounted_tools=("hey_my_buddy_checkpoint", "hey_my_buddy_answer_inquiry",
                                               "hey_my_buddy_finish_turn"), **extra)

    def observe(self, tracker: RootTurnEvidence, ordinal: int, update: dict) -> None:
        tracker.observe({"method": "session/update", "params": {"sessionId": "sess-root", "update": update}},
                        ordinal)

    def schedule(self, tracker: RootTurnEvidence, ordinal: int, call_id: str, title: str) -> None:
        self.observe(tracker, ordinal, {"sessionUpdate": "tool_call", "toolCallId": call_id,
                                        "kind": "other", "title": title, "status": "in_progress"})

    def complete(self, tracker: RootTurnEvidence, ordinal: int, call_id: str, text: str | None = None) -> None:
        update = {"sessionUpdate": "tool_call_update", "toolCallId": call_id, "status": "completed"}
        if text is not None:
            update["content"] = [{"type": "content", "content": {"type": "text", "text": text}}]
        self.observe(tracker, ordinal, update)

    def test_a_repeated_finish_before_any_result_is_a_fact_and_the_first_result_counts(self):
        tracker = self.tracker()
        self.schedule(tracker, 1, "first", self.finish)
        self.schedule(tracker, 2, "second", self.finish)
        # The repeated call's own result never becomes the turn's answer.
        self.complete(tracker, 3, "second", self.receipt())
        self.complete(tracker, 4, "first", self.receipt())
        tracker.settle(5, "end_turn")
        provenance = tracker.provenance(close_ordinal=6, event_count=6)
        self.assertEqual(provenance["toolCallId"], "first")
        self.assertEqual(provenance["postFinishToolCalls"],
                         {"count": 1, "records": [{"toolName": self.finish, "toolCallId": "second"}]})

    def test_later_root_tool_calls_after_the_accepted_receipt_are_facts(self):
        published = []
        tracker = self.tracker(on_finish_receipt=published.append)
        self.schedule(tracker, 1, "first", self.finish)
        self.complete(tracker, 2, "first", self.receipt())
        self.assertEqual([item["receiptId"] for item in published], [tracker.receipt["receiptId"]])
        # A plain task tool and a repeated finish call after the accepted
        # receipt are retained facts; the checkpoint tool keeps its own
        # verified path and stays out of the fact list.
        self.schedule(tracker, 3, "todo-1", "TodoWrite")
        self.schedule(tracker, 4, "check-2", self.checkpoint)
        self.schedule(tracker, 5, "again", self.finish)
        self.complete(tracker, 6, "again", self.receipt())
        tracker.settle(7, "end_turn")
        provenance = tracker.provenance(close_ordinal=8, event_count=8)
        self.assertEqual(provenance["toolCallId"], "first")
        self.assertEqual(provenance["postFinishToolCalls"], {"count": 2, "records": [
            {"toolName": "TodoWrite", "toolCallId": "todo-1"},
            {"toolName": self.finish, "toolCallId": "again"}]})
        self.assertEqual(len(published), 1)

    def test_a_turn_without_any_finish_still_settles_missing_finish(self):
        tracker = self.tracker()
        self.schedule(tracker, 1, "todo-1", "TodoWrite")
        with self.assertRaises(NativeError) as error:
            tracker.settle(2, "end_turn")
        self.assertEqual(error.exception.code, "missing-finish")

    def test_a_finish_call_without_identity_still_lacks_provenance(self):
        tracker = self.tracker()
        with self.assertRaises(NativeError) as error:
            self.schedule(tracker, 1, "", self.finish)
        self.assertEqual(error.exception.code, "invalid-provenance")
