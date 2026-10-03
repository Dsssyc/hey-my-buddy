"""ZCode cooperative inquiry bridge, MCP tools and native attention.

The first half drives the controller's private bridge directly: it proves that
a question is queued with one committed identity, that only the controller's
verified transitions move it to delivered/answered/unavailable, and that no
code path can send a native command. The second half exercises the real MCP
tool surface (checkpoint, answer, finish refusal) against a private journal,
and the journal barrier suite proves the cross-process flock protocol between
the controller writer and the session-tool readers. End-to-end turn flows with
the real runner and fixture live in ``test_zcode_checkpoint.py``.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import select
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from buddy.harnesses.zcode.test_zcode import ZcodeFixtureCase

from hey_my_buddy.protocol import activity as activity_module
from hey_my_buddy.blackboard.tasks import inquiry as inquiry_module
from hey_my_buddy.private_dirs import context_root
from hey_my_buddy.buddy.roles import turn_io
from hey_my_buddy.buddy.harnesses.zcode.mcp import attention_requests, pending_inquiries, read_inquiry_entries, respond
from hey_my_buddy.buddy.harnesses.zcode.protocol import (
    COOPERATIVE_INQUIRY_NOTE,
    MAX_ANSWER_BYTES,
    MAX_INQUIRIES,
    NativeError,
    verify_inquiry_receipt,
    verify_receipt,
    verify_tool_refusal,
)
from hey_my_buddy.buddy.harnesses.zcode.runner import MAX_JOURNAL_BYTES, InquiryBridge

IDENTITY = {"taskId": "task-1", "attemptId": "attempt-1", "generation": 1, "turnId": "turn-1"}
OTHER_IDENTITY = {"taskId": "task-2", "attemptId": "attempt-9", "generation": 1, "turnId": "turn-9"}


class BridgeHarness:
    """A mounted, activated bridge with its private journal in a temp directory."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.credentials = {
            "socketPath": str(self.directory / "inquiry.sock"),
            "resultsPath": str(self.directory / "inquiry.results.jsonl"),
            "errorPath": str(self.directory / "inquiry.error.json"),
            "token": "a" * 64,
        }
        self.attention_path = self.directory / "attention.json"
        self.bridge = InquiryBridge(self.credentials, identity=IDENTITY,
                                    journal_path=self.credentials["resultsPath"],
                                    attention_path=str(self.attention_path))
        self.bridge.start()
        self.bridge.activate("sess-1")

    def request(self, method: str, **payload) -> dict:
        return inquiry_module.bridge_request(self.credentials, method, payload)

    def records(self) -> list[dict]:
        path = Path(self.credentials["resultsPath"])
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def close(self):
        self.bridge.close()


def checkpoint_receipt(inquiries: list[dict], config: dict) -> str:
    from hey_my_buddy.buddy.harnesses.zcode.protocol import sign_receipt
    receipt = {"version": 1, "kind": "inquiry-checkpoint", "identity": config["identity"],
               "inquiries": inquiries, "receiptId": "0" * 31 + "1"}
    receipt["signature"] = sign_receipt(receipt, config["key"])
    return json.dumps(receipt)


def answer_receipt(inquiry_id: str, answer: str, config: dict, *, sha: str = "b" * 64) -> str:
    from hey_my_buddy.buddy.harnesses.zcode.protocol import sign_receipt
    receipt = {"version": 1, "kind": "inquiry-answer", "identity": config["identity"],
               "inquiryId": inquiry_id, "questionSha256": sha, "answer": answer, "receiptId": "0" * 31 + "2"}
    receipt["signature"] = sign_receipt(receipt, config["key"])
    return json.dumps(receipt)


class BridgeQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-zcode-inquiry-")
        self.addCleanup(self.temporary.cleanup)
        self.harness = BridgeHarness(Path(self.temporary.name))
        self.addCleanup(self.harness.close)

    def ask(self, inquiry_id="q-1", question="what is blocking you?") -> dict:
        asked = self.harness.request("ask", inquiryId=inquiry_id, question=question)
        self.assertTrue(asked["ok"], asked)
        return asked["value"]

    def test_a_question_queues_with_one_committed_identity_and_no_native_command(self):
        value = self.ask()
        self.assertTrue(value["accepted"])
        self.assertTrue(value["supported"])
        self.assertEqual(value["state"], "queued")
        self.assertFalse(value["duplicate"])
        self.assertEqual(value["delivery"]["admittedDelivery"], "cooperative-checkpoint")
        self.assertFalse(value["delivery"]["startsNewTurn"])
        records = self.harness.records()
        self.assertEqual(len(records), 1, records)
        self.assertEqual(records[0]["state"], "queued")
        self.assertEqual(records[0]["question"], "what is blocking you?")
        self.assertEqual(records[0]["taskId"], "task-1")
        self.assertEqual(records[0]["sessionId"], "sess-1")

    def test_identical_replay_is_a_duplicate_at_every_state_and_changed_text_conflicts(self):
        self.ask()
        duplicate = self.harness.request("ask", inquiryId="q-1", question="what is blocking you?")
        self.assertTrue(duplicate["ok"])
        self.assertTrue(duplicate["value"]["duplicate"])
        self.assertEqual(duplicate["value"]["state"], "queued")
        conflict = self.harness.request("ask", inquiryId="q-1", question="a different question")
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["code"], "conflict")
        self.assertEqual(len(self.harness.records()), 1)
        # After the controller records the answer, the identical replay still
        # returns the same committed identity with the terminal state.
        self.harness.bridge.record_answer(
            verify_inquiry_receipt(answer_receipt("q-1", "resolved", self._config(), sha=self.harness.bridge.entries["q-1"]["questionSha256"]),
                                   self._config(), "inquiry-answer"),
            "root-answer-call")
        replay = self.harness.request("ask", inquiryId="q-1", question="what is blocking you?")
        self.assertTrue(replay["ok"])
        self.assertTrue(replay["value"]["duplicate"])
        self.assertEqual(replay["value"]["state"], "answered")
        changed = self.harness.request("ask", inquiryId="q-1", question="a different question")
        self.assertFalse(changed["ok"])

    def _config(self) -> dict:
        return {"identity": IDENTITY, "key": "c" * 64}

    def test_ask_before_admission_or_after_close_is_not_ready(self):
        harness = BridgeHarness(Path(self.temporary.name) / "not-ready")
        harness.bridge.active = False
        self.addCleanup(harness.close)
        early = harness.request("ask", inquiryId="q-early", question="status?")
        self.assertFalse(early["ok"])
        self.assertEqual(early["code"], "not-ready")
        self.assertEqual(harness.records(), [])
        closed = BridgeHarness(Path(self.temporary.name) / "closed")
        closed.close()
        late = closed.request("ask", inquiryId="q-late", question="status?")
        self.assertFalse(late["ok"])
        self.assertEqual(late["reason"], "bridge-unreachable")

    def test_bounds_are_enforced_before_any_state_change(self):
        for payload, code in (({"inquiryId": "q-big", "question": "x" * 4001}, "bad-request"),
                              ({"inquiryId": "i" * 129, "question": "ok?"}, "bad-request"),
                              ({"inquiryId": "q-blank", "question": "  "}, "bad-request")):
            with self.subTest(code=code):
                result = self.harness.request("ask", **payload)
                self.assertFalse(result["ok"])
                self.assertEqual(result["code"], code)
        for index in range(MAX_INQUIRIES):
            self.ask(f"q-{index}")
        self.assertEqual(self.harness.request("ask", inquiryId="q-over", question="one too many")["code"], "too-many")

    def test_discard_withdraws_an_unanswered_question(self):
        self.ask()
        discarded = self.harness.request("discard", inquiryId="q-1")
        self.assertTrue(discarded["ok"], discarded)
        self.assertEqual(discarded["value"]["state"], "discarded")
        again = self.harness.request("discard", inquiryId="q-1")
        self.assertFalse(again["ok"])
        self.assertEqual(again["code"], "conflict")
        unknown = self.harness.request("discard", inquiryId="q-none")
        self.assertFalse(unknown["ok"])
        self.assertEqual(unknown["code"], "not-ready")
        # A withdrawn question no longer counts as pending for the finish tool.
        self.assertEqual(self.harness.bridge.entries["q-1"]["state"], "discarded")

    def test_controller_transitions_require_committed_root_evidence(self):
        config = self._config()
        self.ask()
        sha = self.harness.bridge.entries["q-1"]["questionSha256"]
        good = verify_inquiry_receipt(checkpoint_receipt(
            [{"inquiryId": "q-1", "question": "what is blocking you?", "questionSha256": sha,
              "state": "queued", "askedAt": "2026-01-01T00:00:00Z"}], config), config, "inquiry-checkpoint")
        self.harness.bridge.deliver_inquiries(good, "root-checkpoint-call")
        self.assertEqual(self.harness.bridge.entries["q-1"]["state"], "delivered")
        self.assertEqual(self.harness.bridge.entries["q-1"]["toolCallId"], "root-checkpoint-call")
        delivered_records = [record for record in self.harness.records() if record.get("state") == "delivered"]
        # An identical redelivery is a true no-op; forgeries fail the turn.
        self.harness.bridge.deliver_inquiries(good, "root-checkpoint-call-2")
        self.assertEqual([record for record in self.harness.records() if record.get("state") == "delivered"],
                         delivered_records)
        forged = verify_inquiry_receipt(checkpoint_receipt(
            [{"inquiryId": "q-unknown", "question": "never committed", "questionSha256": "d" * 64,
              "state": "queued", "askedAt": "2026-01-01T00:00:00Z"}], config), config, "inquiry-checkpoint")
        with self.assertRaises(NativeError):
            self.harness.bridge.deliver_inquiries(forged, "root-checkpoint-call-3")
        hashed_wrong = verify_inquiry_receipt(checkpoint_receipt(
            [{"inquiryId": "q-1", "question": "what is blocking you?", "questionSha256": "e" * 64,
              "state": "queued", "askedAt": "2026-01-01T00:00:00Z"}], config), config, "inquiry-checkpoint")
        with self.assertRaises(NativeError):
            self.harness.bridge.deliver_inquiries(hashed_wrong, "root-checkpoint-call-4")

    def test_answers_bind_to_the_committed_question_and_first_answer_wins(self):
        config = self._config()
        self.ask()
        sha = self.harness.bridge.entries["q-1"]["questionSha256"]
        answer = verify_inquiry_receipt(answer_receipt("q-1", "do this", config, sha=sha), config, "inquiry-answer")
        self.harness.bridge.record_answer(answer, "root-answer-call")
        entry = self.harness.bridge.entries["q-1"]
        self.assertEqual(entry["state"], "answered")
        self.assertEqual(entry["answer"]["text"], "do this")
        self.assertEqual(entry["answer"]["toolCallId"], "root-answer-call")
        self.assertEqual(entry["answer"]["via"], "tool:buddy_answer_inquiry")
        # An identical binding replay changes nothing; a different answer conflicts.
        self.harness.bridge.record_answer(answer, "root-answer-call-2")
        conflicting = verify_inquiry_receipt(answer_receipt("q-1", "do that instead", config, sha=sha), config, "inquiry-answer")
        with self.assertRaises(NativeError):
            self.harness.bridge.record_answer(conflicting, "root-answer-call-3")
        unknown = verify_inquiry_receipt(answer_receipt("q-none", "do this", config, sha=sha), config, "inquiry-answer")
        with self.assertRaises(NativeError):
            self.harness.bridge.record_answer(unknown, "root-answer-call-4")
        self.assertEqual(entry["answer"]["text"], "do this")

    def test_a_late_valid_answer_racing_discard_or_closure_is_ignored(self):
        # A validly signed root answer that arrives after the Host withdrew the
        # question, or after the turn closed, is a normal race: the terminal
        # state is preserved, the late answer is dropped and the turn may finish
        # normally instead of failing as forged evidence.
        config = self._config()
        self.ask("q-discarded", "first?")
        self.ask("q-closed", "second?")
        self.harness.request("discard", inquiryId="q-discarded")
        sha = self.harness.bridge.entries["q-discarded"]["questionSha256"]
        late = verify_inquiry_receipt(answer_receipt("q-discarded", "late but valid", config, sha=sha),
                                      config, "inquiry-answer")
        self.harness.bridge.record_answer(late, "root-answer-late")
        self.assertEqual(self.harness.bridge.entries["q-discarded"]["state"], "discarded")
        self.assertNotIn("answer", self.harness.bridge.entries["q-discarded"])
        self.harness.close()
        closed_sha = next(record["questionSha256"] for record in self.harness.records()
                          if record.get("inquiryId") == "q-closed" and record.get("questionSha256"))
        after_close = verify_inquiry_receipt(answer_receipt("q-closed", "too late", config, sha=closed_sha),
                                             config, "inquiry-answer")
        self.harness.bridge.record_answer(after_close, "root-answer-late")
        self.assertEqual(self.harness.bridge.entries["q-closed"]["state"], "unavailable")
        self.assertNotIn("answer", self.harness.bridge.entries["q-closed"])

    def test_journal_replay_requires_the_current_version_and_this_attempt(self):
        import hashlib

        question = "status?"
        digest = hashlib.sha256(question.encode()).hexdigest()
        records = [
            {"version": 1, "inquiryId": "q-mine", "state": "answered", "question": question,
             "questionSha256": digest, "askedAt": "t", **IDENTITY,
             "answer": {"text": "mine", "bytes": 4, "truncated": False,
                        "via": "tool:buddy_answer_inquiry", "toolCallId": "c", "at": "t"}},
            {"version": 1, "inquiryId": "q-foreign", "state": "answered", "question": question,
             "questionSha256": digest, "askedAt": "t", **OTHER_IDENTITY,
             "answer": {"text": "foreign", "bytes": 7, "truncated": False, "via": "x", "toolCallId": "c", "at": "t"}},
            {"version": 1, "inquiryId": "q-unbound", "state": "queued", "question": question,
             "questionSha256": digest, "askedAt": "t"},
            {"inquiryId": "q-legacy", "state": "answered", "question": question,
             "questionSha256": digest, "askedAt": "t", **IDENTITY,
             "answer": {"text": "legacy", "bytes": 6, "truncated": False, "via": "x", "toolCallId": "c", "at": "t"}},
        ]
        Path(self.harness.credentials["resultsPath"]).write_text(
            "garbage line\n" + "".join(json.dumps(record) + "\n" for record in records))
        restarted = InquiryBridge(self.harness.credentials, identity=IDENTITY,
                                  journal_path=self.harness.credentials["resultsPath"])
        restarted._load_journal()
        self.assertEqual(set(restarted.entries), {"q-mine"})
        self.assertEqual(restarted.entries["q-mine"]["state"], "answered")
        # A foreign or legacy "answered" record never answers a fresh question.
        for inquiry_id in ("q-foreign", "q-legacy"):
            with self.subTest(inquiryId=inquiry_id):
                restarted.entries.clear()
                restarted._load_journal()
                restarted.activate("sess-1")
                self.assertTrue(restarted._journal({**restarted._identity_fields(), "inquiryId": inquiry_id,
                                                    "state": "queued", "question": question,
                                                    "questionSha256": digest, "askedAt": "t"}))
                self.assertEqual(restarted.entries[inquiry_id]["state"], "queued")

    def test_close_marks_unanswered_entries_unavailable_without_waking_anything(self):
        self.ask("q-queued", "first?")
        self.ask("q-answered", "second?")
        config = self._config()
        sha = self.harness.bridge.entries["q-answered"]["questionSha256"]
        self.harness.bridge.record_answer(
            verify_inquiry_receipt(answer_receipt("q-answered", "done", config, sha=sha), config, "inquiry-answer"),
            "root-answer-call")
        self.harness.close()
        states = {record["inquiryId"]: record for record in self.harness.records()}
        self.assertEqual(self.harness.bridge.entries["q-queued"]["state"], "unavailable")
        self.assertIn("ended before this inquiry was answered", states["q-queued"]["reason"])
        self.assertEqual(self.harness.bridge.entries["q-answered"]["state"], "answered")
        self.assertFalse(Path(self.harness.credentials["socketPath"]).exists())
        # A closed bridge is unreachable: a late question cannot even queue.
        self.assertEqual(self.harness.request("ask", inquiryId="q-after", question="late?")["reason"],
                         "bridge-unreachable")

    def test_observation_is_bounded_and_reports_the_cooperative_channel(self):
        self.ask()
        observed = self.harness.request("observe")
        self.assertTrue(observed["ok"], observed)
        value = observed["value"]
        self.assertTrue(value["ready"])
        self.assertEqual(value["capability"], "inquiry")
        self.assertTrue(value["supported"])
        self.assertEqual(value["deliveryMode"], "cooperative-checkpoint")
        self.assertEqual(value["inbox"]["pending"], 1)
        self.assertEqual(value["replyTool"]["name"], "buddy_answer_inquiry")
        self.assertEqual(value["limits"]["inquiry"], "cooperative-checkpoint")
        self.assertFalse(value["limits"]["startsNewTurn"])
        self.assertFalse(value["limits"]["extendsDeadline"])
        self.assertIn("never injected", value["limitation"])
        self.assertIn("immediateDelivery", value["unavailable"])

    def test_live_answer_view_reports_state_and_receipt_shape(self):
        self.ask()
        pending = self.harness.request("answer", inquiryId="q-1")
        self.assertTrue(pending["ok"], pending)
        self.assertFalse(pending["value"]["answer"]["available"])
        self.assertEqual(pending["value"]["state"], "queued")
        config = self._config()
        sha = self.harness.bridge.entries["q-1"]["questionSha256"]
        self.harness.bridge.record_answer(
            verify_inquiry_receipt(answer_receipt("q-1", "all good", config, sha=sha), config, "inquiry-answer"),
            "root-answer-call")
        answered = self.harness.request("answer", inquiryId="q-1")
        self.assertTrue(answered["ok"], answered)
        self.assertEqual(answered["value"]["state"], "answered")
        self.assertEqual(answered["value"]["answer"]["text"], "all good")
        self.assertEqual(answered["value"]["answer"]["toolCallId"], "root-answer-call")
        # The exact shape the board importer normalizes from a live answer view.
        normalized = inquiry_module.normalize_journal_answer({"answer": answered["value"]["answer"]})
        self.assertEqual(normalized["text"], "all good")
        self.assertEqual(normalized["toolCallId"], "root-answer-call")

    def test_a_failed_journal_append_refuses_the_ask_without_fabricating_state(self):
        from unittest import mock

        import os as os_module

        bridge = self.harness.bridge
        real_write = os_module.write

        def flaky_write(fd, data):
            state = flaky_write.state
            state["count"] += 1
            if state["count"] == 1:
                return max(1, real_write(fd, bytes(data[:5])))
            raise OSError("disk gone mid-record")

        for failure in ("raise", "partial-then-raise", "byte-cap"):
            with self.subTest(failure=failure):
                bridge.entries.clear()
                bridge.error = None
                bridge._torn_line = False
                flaky_write.state = {"count": 0}
                if failure == "byte-cap":
                    Path(self.harness.credentials["resultsPath"]).write_bytes(b"x" * MAX_JOURNAL_BYTES)
                    asked = self.harness.request("ask", inquiryId=f"q-{failure}", question="durable?")
                    raw = self.harness.bridge.handle({"version": 1, "id": "code-probe",
                                                      "token": self.harness.credentials["token"],
                                                      "method": "ask", "inquiryId": f"q-{failure}-probe",
                                                      "question": "durable?"})
                elif failure == "raise":
                    with mock.patch("hey_my_buddy.buddy.harnesses.zcode.runner.os.write", side_effect=OSError("disk gone")):
                        asked = self.harness.request("ask", inquiryId=f"q-{failure}", question="durable?")
                        raw = self.harness.bridge.handle({"version": 1, "id": "code-probe",
                                                          "token": self.harness.credentials["token"],
                                                          "method": "ask", "inquiryId": f"q-{failure}-probe",
                                                          "question": "durable?"})
                else:
                    with mock.patch("hey_my_buddy.buddy.harnesses.zcode.runner.os.write", side_effect=flaky_write):
                        asked = self.harness.request("ask", inquiryId=f"q-{failure}", question="durable?")
                        raw = self.harness.bridge.handle({"version": 1, "id": "code-probe",
                                                          "token": self.harness.credentials["token"],
                                                          "method": "ask", "inquiryId": f"q-{failure}-probe",
                                                          "question": "durable?"})
                self.assertFalse(asked["ok"], asked)
                # The bridge refuses with the explicit journal-unavailable code;
                # the board client normalizes unknown codes to "internal" until
                # the Host adds this one to its refusal vocabulary.
                self.assertEqual(asked["reason"], "bridge-refused")
                self.assertFalse(raw["ok"])
                self.assertEqual(raw["error"], "journal-unavailable")
                self.assertNotIn(f"q-{failure}", bridge.entries, "a refused ask must not commit in-memory state")
                self.assertTrue(str(bridge.error).startswith("journal-unavailable"), bridge.error)
                observed = self.harness.request("observe")
                self.assertTrue(observed["ok"])
                self.assertIn("journal-unavailable", observed["value"]["error"])
                self.assertEqual(observed["value"]["inbox"]["pending"], 0)
        # After the fault clears, the identical ask commits durably and the MCP
        # tools can read the question back from the journal.
        Path(self.harness.credentials["resultsPath"]).unlink(missing_ok=True)
        bridge.error = None
        bridge._torn_line = False
        asked = self.harness.request("ask", inquiryId="q-recovered", question="durable?")
        self.assertTrue(asked["ok"], asked)
        self.assertEqual(asked["value"]["state"], "queued")
        self.assertTrue(any(json.loads(line).get("inquiryId") == "q-recovered"
                            for line in Path(self.harness.credentials["resultsPath"]).read_text().splitlines()))

    def test_controller_transitions_never_fabricate_state_on_a_failed_append(self):
        from unittest import mock

        config = self._config()
        self.ask("q-flaky", "delivered anyway?")
        sha = self.harness.bridge.entries["q-flaky"]["questionSha256"]
        bridge = self.harness.bridge
        delivery = verify_inquiry_receipt(checkpoint_receipt(
            [{"inquiryId": "q-flaky", "question": "delivered anyway?", "questionSha256": sha,
              "state": "queued", "askedAt": "t"}], config), config, "inquiry-checkpoint")
        with mock.patch("hey_my_buddy.buddy.harnesses.zcode.runner.os.write", side_effect=OSError("disk gone")):
            bridge.deliver_inquiries(delivery, "root-ckpt")
        self.assertEqual(bridge.entries["q-flaky"]["state"], "queued", "a failed append must not fabricate delivery")
        self.assertTrue(str(bridge.error).startswith("journal-unavailable"))
        # The root can legitimately checkpoint again once the fault clears.
        bridge.deliver_inquiries(delivery, "root-ckpt-2")
        self.assertEqual(bridge.entries["q-flaky"]["state"], "delivered")
        answer = verify_inquiry_receipt(answer_receipt("q-flaky", "still fine", config, sha=sha), config, "inquiry-answer")
        with mock.patch("hey_my_buddy.buddy.harnesses.zcode.runner.os.write", side_effect=OSError("disk gone")):
            bridge.record_answer(answer, "root-answer")
        self.assertEqual(bridge.entries["q-flaky"]["state"], "delivered", "a failed append must not fabricate the answer")
        bridge.record_answer(answer, "root-answer-2")
        self.assertEqual(bridge.entries["q-flaky"]["state"], "answered")
        self.assertEqual(bridge.entries["q-flaky"]["answer"]["toolCallId"], "root-answer-2")
        # A discard that cannot be recorded refuses explicitly instead.
        self.ask("q-flaky-2", "another?")
        with mock.patch("hey_my_buddy.buddy.harnesses.zcode.runner.os.write", side_effect=OSError("disk gone")):
            discarded = self.harness.request("discard", inquiryId="q-flaky-2")
        self.assertFalse(discarded["ok"])
        self.assertEqual(discarded["reason"], "bridge-refused")
        self.assertEqual(bridge.entries["q-flaky-2"]["state"], "queued")

    def test_the_bridge_has_no_native_injection_path_at_all(self):
        # The proof that delivery stays cooperative is structural: this class
        # holds no native connection and exposes no inject or drain API, and the
        # connection has no send_text or command surface for questions.
        self.assertFalse(hasattr(self.harness.bridge, "_inject"))
        self.assertFalse(hasattr(self.harness.bridge, "drain"))
        from hey_my_buddy.buddy.harnesses.zcode.protocol import NativeConnection

        self.assertFalse(hasattr(NativeConnection, "send_text"))
        self.assertNotIn("commandId", json.dumps(self.harness.records()))

    def test_the_journal_merges_identity_hash_and_delivery_across_records_and_restarts(self):
        self.ask("q-merge", "status?")
        committed = self.harness.bridge.entries["q-merge"]
        self.harness.bridge._journal({**self.harness.bridge._identity_fields(), "inquiryId": "q-merge",
                                      "state": "delivered", "deliveredAt": "2026-01-01T00:00:00Z"})
        merged = self.harness.bridge.entries["q-merge"]
        self.assertEqual(merged["questionSha256"], committed["questionSha256"])
        self.assertEqual(merged["question"], "status?")
        self.assertEqual(merged["delivery"], committed["delivery"])
        self.assertEqual(merged["deliveredAt"], "2026-01-01T00:00:00Z")
        # A controller restart replays the journal and keeps the same committed
        # identity, so the same question is still a duplicate.
        restarted = InquiryBridge(self.harness.credentials, identity=IDENTITY,
                                  journal_path=self.harness.credentials["resultsPath"])
        restarted._load_journal()
        self.assertEqual(restarted.entries["q-merge"]["questionSha256"], committed["questionSha256"])
        self.assertEqual(restarted.entries["q-merge"]["question"], "status?")
        self.assertEqual(restarted.entries["q-merge"]["delivery"], committed["delivery"])

    def test_the_board_journal_importer_reads_the_real_records(self):
        from hey_my_buddy.buddy.harnesses.zcode.protocol import sign_receipt

        self.ask("q-import", "what next?")
        sha = self.harness.bridge.entries["q-import"]["questionSha256"]
        receipt = {"version": 1, "kind": "inquiry-answer", "identity": IDENTITY, "inquiryId": "q-import",
                   "questionSha256": sha, "answer": "proceed", "receiptId": "0" * 32}
        receipt["signature"] = sign_receipt(receipt, "c" * 64)
        self.harness.bridge.record_answer(verify_inquiry_receipt(json.dumps(receipt), {"identity": IDENTITY, "key": "c" * 64},
                                                                 "inquiry-answer"), "root-answer-call")
        journal = inquiry_module.read_journal(self.harness.credentials["resultsPath"])
        self.assertTrue(journal["available"])
        merged = journal["entries"]["q-import"]
        self.assertEqual(merged["state"], "answered")
        self.assertEqual(merged["taskId"], "task-1")
        answer = inquiry_module.normalize_journal_answer(merged)
        self.assertEqual(answer["text"], "proceed")
        self.assertEqual(answer["toolCallId"], "root-answer-call")
        self.assertEqual(answer["via"], "tool:buddy_answer_inquiry")

    def test_attention_is_bounded_and_readable_while_the_turn_is_live(self):
        for index in range(10):
            self.harness.bridge.note_attention({"kind": "unsupported-native-request", "method": f"interaction/{index}",
                                                "at": "2026-01-01T00:00:00Z", "outcome": "refused-with-jsonrpc-error"})
        self.assertEqual(self.harness.bridge.attention_report()["requests"], 8)
        written = json.loads(self.harness.attention_path.read_text())
        self.assertEqual(written["requests"][-1]["method"], "interaction/9")
        self.assertEqual(written["attemptId"], "attempt-1")


class JournalBarrierTests(unittest.TestCase):
    """The journal's cross-process flock barrier between writer and readers.

    The controller's append transaction (cap check, append, fsync) holds the
    exclusive side; the session tools' reads take the shared side and wait, so
    no reader can observe an in-flight or rolled-back record.
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-zcode-journal-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.journal = self.root / "inquiry.results.jsonl"
        self.config = {"identity": IDENTITY, "inputSha256": "a" * 64, "key": "b" * 64,
                       "inquiryJournalPath": str(self.journal)}

    def queued_record(self, inquiry_id: str, question: str = "barrier question?") -> bytes:
        return (json.dumps({"version": 1, "inquiryId": inquiry_id, "state": "queued", "question": question,
                            "questionSha256": hashlib.sha256(question.encode()).hexdigest(),
                            "askedAt": "2026-01-01T00:00:00Z", **IDENTITY}) + "\n").encode()

    def bridge(self) -> InquiryBridge:
        return InquiryBridge({"socketPath": str(self.root / "bridge.sock"), "resultsPath": str(self.journal),
                              "errorPath": str(self.root / "inquiry.error.json"), "token": "a" * 64},
                             identity=IDENTITY, journal_path=str(self.journal))

    def test_a_shared_reader_waits_for_the_exclusive_writer_transaction(self):
        record = self.queued_record("q-barrier")
        fd = os.open(self.journal, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        observed: list[dict] = []
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, record[:24])  # a deliberately in-flight, uncommitted record
            reader = threading.Thread(target=lambda: observed.append(read_inquiry_entries(self.config)))
            reader.start()
            reader.join(0.3)
            self.assertTrue(reader.is_alive(), "a reader observed the journal mid-transaction")
            os.write(fd, record[24:])
            os.fsync(fd)
        finally:
            os.close(fd)  # closing releases the exclusive barrier
        reader.join(5)
        self.assertFalse(reader.is_alive())
        self.assertEqual(observed[0]["q-barrier"]["state"], "queued")

    def test_the_mcp_reader_in_a_real_other_process_waits_for_the_barrier(self):
        config_path = self.root / "bridge.json"
        config_path.write_text(json.dumps(self.config))
        record = self.queued_record("q-cross-process")
        fd = os.open(self.journal, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("BUDDY_", "ZCODE_")) and key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        process = subprocess.Popen([sys.executable, "-m", "hey_my_buddy.buddy.harnesses.zcode.mcp", "--config", str(config_path)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=environment)

        def stop():
            try:
                process.stdin.close()
            except OSError:
                pass
            process.wait(timeout=5)
            process.stdout.close()

        self.addCleanup(stop)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                            "params": {"name": "buddy_checkpoint", "arguments": {}}}).encode() + b"\n")
            process.stdin.flush()
            ready, _, _ = select.select([process.stdout], [], [], 0.4)
            self.assertFalse(ready, "the session tool observed the journal while the writer held the barrier")
            os.write(fd, record)
            os.fsync(fd)
        finally:
            os.close(fd)  # closing releases the barrier for the other process
        ready, _, _ = select.select([process.stdout], [], [], 10)
        self.assertTrue(ready, "the session tool never answered after the barrier was released")
        result = json.loads(process.stdout.readline().decode())["result"]
        receipt = verify_inquiry_receipt(result["content"][0]["text"], self.config, "inquiry-checkpoint")
        self.assertEqual(receipt["inquiries"][0]["inquiryId"], "q-cross-process")

    def test_a_failed_append_rolls_back_its_partial_bytes_under_the_barrier(self):
        from unittest import mock

        harness = BridgeHarness(self.root / "harness")
        self.addCleanup(harness.close)
        bridge = harness.bridge
        real_write = os.write

        def flaky_write(fd, data):
            if not flaky_write.triggered:
                flaky_write.triggered = True
                return max(1, real_write(fd, bytes(data[:5])))
            raise OSError("disk gone mid-record")

        flaky_write.triggered = False
        record = {**bridge._identity_fields(), "inquiryId": "q-rollback", "state": "queued",
                  "question": "rolled back?", "questionSha256": "a" * 64, "askedAt": "t"}
        with mock.patch("hey_my_buddy.buddy.harnesses.zcode.runner.os.write", side_effect=flaky_write):
            self.assertFalse(bridge._journal(record))
        path = Path(harness.credentials["resultsPath"])
        self.assertEqual(path.read_bytes(), b"", "a failed append must leave no fragment behind")
        self.assertFalse(bridge._torn_line, "a clean rollback must not mark the journal torn")
        # After the fault clears the same record commits as one clean line that
        # the session tools can read back.
        self.assertTrue(bridge._journal(record))
        lines = [line for line in path.read_text().splitlines() if line.strip()]
        self.assertEqual([json.loads(line)["inquiryId"] for line in lines], ["q-rollback"])

    def test_a_crash_torn_tail_is_isolated_by_the_next_append_and_ignored_by_readers(self):
        torn = b'{"version":1,"inquiryId":"q-torn","state":"que'  # no trailing newline
        self.journal.write_bytes(self.queued_record("q-good") + torn)
        bridge = self.bridge()
        bridge._load_journal()
        self.assertTrue(bridge._torn_line, "a journal tail without its newline is a torn append")
        self.assertEqual(set(bridge.entries), {"q-good"})
        # The session tools ignore the torn tail and still see the committed record.
        self.assertEqual(set(read_inquiry_entries(self.config)), {"q-good"})
        # The next append isolates the remains so only that one line is lost.
        self.assertTrue(bridge._journal({**bridge._identity_fields(), "inquiryId": "q-after",
                                         "state": "queued", "question": "after the crash?",
                                         "questionSha256": "b" * 64, "askedAt": "t"}))
        restarted = self.bridge()
        restarted._load_journal()
        self.assertEqual(set(restarted.entries), {"q-good", "q-after"})
        self.assertEqual(set(read_inquiry_entries(self.config)), {"q-good", "q-after"})


class FinishToolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-zcode-finish-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.attention = self.root / "attention.json"
        self.journal = self.root / "inquiry.results.jsonl"
        self.config = {"identity": IDENTITY, "inputSha256": "a" * 64, "key": "b" * 64,
                       "attentionPath": str(self.attention), "inquiryJournalPath": str(self.journal)}

    def call(self, name: str, arguments: dict) -> dict:
        return respond({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": name, "arguments": arguments}}, self.config)["result"]

    def refusal(self, result: dict, tool: str) -> dict:
        """Verify the isError text as the signed refusal envelope it must be."""
        self.assertTrue(result.get("isError"), result)
        return verify_tool_refusal(result["content"][0]["text"], self.config, f"mcp__buddy_x__{tool}")

    @staticmethod
    def outcome(disposition: str) -> dict:
        request = None if disposition == "completed" else {
            "summary": "help", "attempted": "tried", "neededWork": "decide",
            "expectedArtifacts": [], "acceptance": "decided"}
        return {"disposition": disposition, "summary": "fixture", "remaining": [], "decisions": [],
                "artifacts": [], "request": request}

    def queue(self, inquiry_id="q-1", question="what is the deployment word?", *, identity=IDENTITY) -> None:
        import hashlib

        self.journal.parent.mkdir(parents=True, exist_ok=True)
        with self.journal.open("a") as stream:
            stream.write(json.dumps({"version": 1, "inquiryId": inquiry_id, "state": "queued", "question": question,
                                     "questionSha256": hashlib.sha256(question.encode()).hexdigest(),
                                     "askedAt": "2026-01-01T00:00:00Z",
                                     "taskId": identity["taskId"], "attemptId": identity["attemptId"],
                                     "generation": identity["generation"], "turnId": identity["turnId"]}) + "\n")

    def test_all_three_session_tools_are_exposed(self):
        listed = respond({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, self.config)
        names = [tool["name"] for tool in listed["result"]["tools"]]
        self.assertEqual(names, ["buddy_checkpoint", "buddy_answer_inquiry", "buddy_finish_turn"])

    def test_checkpoint_returns_a_signed_receipt_of_the_pending_questions(self):
        self.queue()
        result = self.call("buddy_checkpoint", {})
        self.assertFalse(result.get("isError"), result)
        receipt = verify_inquiry_receipt(result["content"][0]["text"], self.config, "inquiry-checkpoint")
        self.assertEqual(receipt["inquiries"][0]["inquiryId"], "q-1")
        self.assertEqual(receipt["inquiries"][0]["question"], "what is the deployment word?")
        # The handler is read-only, so another checkpoint still exposes the same
        # unanswered question under an independent receipt.
        again = self.call("buddy_checkpoint", {})
        second = verify_inquiry_receipt(again["content"][0]["text"], self.config, "inquiry-checkpoint")
        self.assertEqual(second["inquiries"], receipt["inquiries"])
        self.assertNotEqual(second["receiptId"], receipt["receiptId"])

    def test_a_foreign_journal_identity_is_never_exposed(self):
        self.queue("q-foreign", "other attempt question?", identity=OTHER_IDENTITY)
        result = self.call("buddy_checkpoint", {})
        receipt = verify_inquiry_receipt(result["content"][0]["text"], self.config, "inquiry-checkpoint")
        self.assertEqual(receipt["inquiries"], [])
        refused = self.call("buddy_answer_inquiry", {"inquiryId": "q-foreign", "answer": "nope"})
        envelope = self.refusal(refused, "buddy_answer_inquiry")
        self.assertEqual(envelope["reason"], "unknown-inquiry")
        self.assertIn("unknown inquiryId", envelope["detail"])

    def test_answer_receipts_are_bound_and_bounded(self):
        self.queue()
        accepted = self.call("buddy_answer_inquiry", {"inquiryId": "q-1", "answer": "deploy-ok"})
        self.assertFalse(accepted.get("isError"), accepted)
        receipt = verify_inquiry_receipt(accepted["content"][0]["text"], self.config, "inquiry-answer")
        self.assertEqual(receipt["answer"], "deploy-ok")
        for arguments, fragment, reason in (
            ({"inquiryId": "q-1", "answer": "  "}, "nonblank", "invalid-arguments"),
            ({"inquiryId": "q-1", "answer": "x" * 4001}, "4000-byte", "invalid-arguments"),
            ({"inquiryId": "q-missing", "answer": "fine"}, "unknown inquiryId", "unknown-inquiry"),
            ({"inquiryId": "q-1"}, "exactly", "invalid-arguments"),
            ({}, "exactly", "invalid-arguments"),
        ):
            with self.subTest(fragment=fragment):
                refused = self.call("buddy_answer_inquiry", arguments)
                envelope = self.refusal(refused, "buddy_answer_inquiry")
                self.assertEqual(envelope["reason"], reason)
                self.assertIn(fragment, envelope["detail"])
        # A tampered receipt must fail controller verification.
        tampered = json.loads(accepted["content"][0]["text"])
        tampered["answer"] = "changed after signing"
        with self.assertRaises(NativeError):
            verify_inquiry_receipt(json.dumps(tampered), self.config, "inquiry-answer")
        with self.assertRaises(NativeError):
            verify_inquiry_receipt(accepted["content"][0]["text"], {**self.config, "key": "d" * 64}, "inquiry-answer")

    def test_an_answered_inquiry_cannot_be_reanswered(self):
        import hashlib

        question = "what is the deployment word?"
        answered = {"version": 1, "inquiryId": "q-1", "state": "answered", "question": question,
                    "questionSha256": hashlib.sha256(question.encode()).hexdigest(),
                    "answer": {"text": "deploy-ok", "bytes": 10, "truncated": False,
                               "via": "tool:buddy_answer_inquiry", "toolCallId": "root-call",
                               "at": "2026-01-01T00:00:00Z"}, **IDENTITY}
        with self.journal.open("a") as stream:
            stream.write(json.dumps(answered) + "\n")
        refused = self.call("buddy_answer_inquiry", {"inquiryId": "q-1", "answer": "another"})
        self.assertTrue(refused["isError"])
        self.assertIn("already has its recorded answer", refused["content"][0]["text"])

    def test_completed_is_refused_while_a_question_is_unanswered(self):
        self.queue()
        refused = self.call("buddy_finish_turn", self.outcome("completed"))
        envelope = self.refusal(refused, "buddy_finish_turn")
        self.assertEqual(envelope["reason"], "inquiry-pending")
        self.assertIn("what is the deployment word?", envelope["detail"])
        self.assertIn("q-1", envelope["detail"])
        self.assertIn("buddy_checkpoint", envelope["detail"])
        # Assistance and attention stay legal with a pending question.
        for disposition in ("assistance", "attention"):
            with self.subTest(disposition=disposition):
                accepted = self.call("buddy_finish_turn", self.outcome(disposition))
                self.assertFalse(accepted.get("isError"), accepted)
        # Once answered, completion passes again.
        import hashlib

        question = "what is the deployment word?"
        with self.journal.open("a") as stream:
            stream.write(json.dumps({"version": 1, "inquiryId": "q-1", "state": "answered", "question": question,
                                     "questionSha256": hashlib.sha256(question.encode()).hexdigest(),
                                     "answer": {"text": "deploy-ok", "bytes": 10, "truncated": False,
                                                "via": "tool:buddy_answer_inquiry", "toolCallId": "root-call",
                                                "at": "2026-01-01T00:00:00Z"}, **IDENTITY}) + "\n")
        settled = self.call("buddy_finish_turn", self.outcome("completed"))
        self.assertFalse(settled.get("isError"), settled)
        verify_receipt(settled["content"][0]["text"], self.config)

    def test_foreign_or_unversioned_journal_records_are_never_exposed(self):
        import hashlib

        question = "what is the deployment word?"
        records = [
            # Current format, this attempt: replays.
            {"version": 1, "inquiryId": "q-mine", "state": "queued", "question": question,
             "questionSha256": hashlib.sha256(question.encode()).hexdigest(), "askedAt": "t", **IDENTITY},
            # Foreign attempt identity: ignored even with the current version.
            {"version": 1, "inquiryId": "q-foreign", "state": "answered", "question": "other",
             "questionSha256": "a" * 64, "askedAt": "t", **OTHER_IDENTITY,
             "answer": {"text": "foreign", "bytes": 7, "truncated": False, "via": "x", "toolCallId": "c", "at": "t"}},
            # Missing identity fields: unbound, ignored.
            {"version": 1, "inquiryId": "q-unbound", "state": "queued", "question": "no identity",
             "questionSha256": "b" * 64, "askedAt": "t"},
            # Old/unversioned record shape: no fallback, ignored.
            {"inquiryId": "q-old", "state": "answered", "question": "legacy", "questionSha256": "c" * 64,
             "askedAt": "t", **IDENTITY, "answer": {"text": "legacy", "bytes": 6, "truncated": False,
                                                    "via": "x", "toolCallId": "c", "at": "t"}},
        ]
        with self.journal.open("a") as stream:
            stream.write("not json at all\n" + "".join(json.dumps(record) + "\n" for record in records))
        result = self.call("buddy_checkpoint", {})
        receipt = verify_inquiry_receipt(result["content"][0]["text"], self.config, "inquiry-checkpoint")
        self.assertEqual([item["inquiryId"] for item in receipt["inquiries"]], ["q-mine"])
        for inquiry_id in ("q-foreign", "q-unbound", "q-old"):
            with self.subTest(inquiryId=inquiry_id):
                refused = self.call("buddy_answer_inquiry", {"inquiryId": inquiry_id, "answer": "x"})
                self.assertTrue(refused["isError"])
                self.assertIn("unknown inquiryId", refused["content"][0]["text"])
        # A finish is not blocked by a foreign "answered" or unversioned record.
        completed = self.call("buddy_finish_turn", self.outcome("completed"))
        self.assertTrue(completed["isError"], "q-mine still blocks completion")
        with self.journal.open("a") as stream:
            stream.write(json.dumps({"version": 1, "inquiryId": "q-mine", "state": "answered",
                                     "question": question, "questionSha256": hashlib.sha256(question.encode()).hexdigest(),
                                     "askedAt": "t", **IDENTITY,
                                     "answer": {"text": "done", "bytes": 4, "truncated": False,
                                                "via": "tool:buddy_answer_inquiry", "toolCallId": "c", "at": "t"}}) + "\n")
        settled = self.call("buddy_finish_turn", self.outcome("completed"))
        self.assertFalse(settled.get("isError"), settled)

    def test_pending_inquiries_is_none_without_a_journal(self):
        bare = {k: v for k, v in self.config.items() if k != "inquiryJournalPath"}
        self.assertIsNone(pending_inquiries(bare))
        checkpoint = respond({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "buddy_checkpoint", "arguments": {}}}, bare)["result"]
        self.assertTrue(checkpoint["isError"])
        refused = respond({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "buddy_answer_inquiry",
                                      "arguments": {"inquiryId": "q-1", "answer": "x"}}}, bare)["result"]
        self.assertTrue(refused["isError"])

    def test_completed_is_refused_while_a_native_request_is_unresolved(self):
        self.assertEqual(attention_requests(self.config), 0)
        self.attention.write_text(json.dumps({"version": 1, "requests": [
            {"kind": "unsupported-native-request", "method": "interaction/requestPermission"}]}))
        self.assertEqual(attention_requests(self.config), 1)
        refused = self.call("buddy_finish_turn", self.outcome("completed"))
        envelope = self.refusal(refused, "buddy_finish_turn")
        self.assertEqual(envelope["reason"], "attention-outstanding")
        self.assertIn("attention", envelope["detail"])
        attention = self.call("buddy_finish_turn", self.outcome("attention"))
        self.assertFalse(attention.get("isError"), attention)
        receipt = verify_receipt(attention["content"][0]["text"], self.config)
        self.assertEqual(receipt["outcome"]["disposition"], "attention")

    def test_a_completed_outcome_still_verifies_without_attention(self):
        accepted = self.call("buddy_finish_turn", self.outcome("completed"))
        receipt = verify_receipt(accepted["content"][0]["text"], self.config)
        self.assertEqual(receipt["outcome"]["disposition"], "completed")
        tampered = json.loads(accepted["content"][0]["text"])
        tampered["outcome"]["summary"] = "changed"
        with self.assertRaises(NativeError):
            verify_receipt(json.dumps(tampered), self.config)

    def test_unknown_tools_and_invalid_outcomes_are_bounded_errors(self):
        unknown = self.call("buddy_something_else", {})
        self.assertTrue(unknown["isError"])
        invalid = self.call("buddy_finish_turn", {"disposition": "completed"})
        envelope = self.refusal(invalid, "buddy_finish_turn")
        self.assertEqual(envelope["reason"], "invalid-arguments")

    def test_expected_refusals_are_signed_envelopes_bound_to_attempt_input_and_tool(self):
        # Every expected refusal this MCP can mint is a signed envelope that the
        # controller can verify on a wrapper-successful result, bound to this
        # attempt, this input and exactly the tool that refused.
        self.queue()
        refusals = [
            ("buddy_finish_turn", self.outcome("completed"), "inquiry-pending"),
            ("buddy_finish_turn", {"disposition": "guessing"}, "invalid-arguments"),
            ("buddy_answer_inquiry", {"inquiryId": "q-none", "answer": "x"}, "unknown-inquiry"),
        ]
        for tool, arguments, reason in refusals:
            with self.subTest(tool=tool, reason=reason):
                result = self.call(tool, arguments)
                envelope = self.refusal(result, tool)
                self.assertEqual(envelope["reason"], reason)
                self.assertEqual(envelope["kind"], "tool-refusal")
                self.assertEqual(envelope["identity"], self.config["identity"])
                self.assertEqual(envelope["inputSha256"], self.config["inputSha256"])
                # The envelope is bound to its exact tool: another tool's native
                # name must not verify it.
                with self.assertRaises(NativeError) as error:
                    verify_tool_refusal(result["content"][0]["text"], self.config, "mcp__buddy_x__buddy_checkpoint"
                                        if tool != "buddy_checkpoint" else "mcp__buddy_x__buddy_finish_turn")
                self.assertEqual(error.exception.code, "invalid-tool-refusal")
        # A foreign attempt configuration cannot verify another attempt's refusal.
        foreign = {**self.config, "identity": OTHER_IDENTITY, "key": "e" * 64}
        with self.assertRaises(NativeError):
            verify_tool_refusal(self.call("buddy_finish_turn", self.outcome("completed"))["content"][0]["text"],
                                foreign, "mcp__buddy_x__buddy_finish_turn")

    def test_an_explicit_null_suggested_profile_is_the_accepted_no_suggestion(self):
        # The incident's finish arguments are now valid: an explicit null
        # suggestedProfileId means no suggestion and mints a success receipt,
        # while a non-string value is refused with a signed envelope whose
        # detail names the correction.
        with_null = self.outcome("attention")
        with_null["request"]["suggestedProfileId"] = None
        accepted = self.call("buddy_finish_turn", with_null)
        self.assertFalse(accepted.get("isError"), accepted)
        receipt = verify_receipt(accepted["content"][0]["text"], self.config)
        self.assertIsNone(receipt["outcome"]["request"]["suggestedProfileId"])
        for invalid in (5, True, {}, []):
            with self.subTest(invalid=invalid):
                bad = self.outcome("attention")
                bad["request"]["suggestedProfileId"] = invalid
                envelope = self.refusal(self.call("buddy_finish_turn", bad), "buddy_finish_turn")
                self.assertEqual(envelope["reason"], "invalid-arguments")
                self.assertIn("references are invalid", envelope["detail"])

    def test_pathological_questions_batch_into_a_verifiable_checkpoint(self):
        # JSON escaping can inflate an accepted 4000-byte question far past
        # the raw length, so 32 control-character questions cannot fit one
        # receipt. The checkpoint mints the longest serialized batch that
        # verifies, counts the overflow explicitly, and every question stays
        # queued — the finish refusal still names them all by count.
        for index in range(32):
            self.queue(f"q-{index}", chr(1) * 4000)
        checkpoint = self.call("buddy_checkpoint", {})
        self.assertFalse(checkpoint.get("isError"), checkpoint)
        receipt = verify_inquiry_receipt(checkpoint["content"][0]["text"], self.config, "inquiry-checkpoint")
        self.assertLess(len(receipt["inquiries"]), 32)
        self.assertEqual(receipt["morePending"], 32 - len(receipt["inquiries"]))
        refused = self.call("buddy_finish_turn", self.outcome("completed"))
        envelope = self.refusal(refused, "buddy_finish_turn")
        self.assertEqual(envelope["reason"], "inquiry-pending")
        self.assertIn("(+28 more)", envelope["detail"])
        self.assertIn("Call buddy_checkpoint", envelope["detail"])

    def test_realistic_questions_all_fit_one_checkpoint_receipt(self):
        for index in range(32):
            self.queue(f"q-{index}", "What is the deployment word? " * 130)
        checkpoint = self.call("buddy_checkpoint", {})
        receipt = verify_inquiry_receipt(checkpoint["content"][0]["text"], self.config, "inquiry-checkpoint")
        self.assertEqual(len(receipt["inquiries"]), 32)
        self.assertEqual(receipt.get("morePending", 0), 0)
        self.assertLessEqual(len(checkpoint["content"][0]["text"].encode()), 200 * 1024)


class ActivitySidecarTests(ZcodeFixtureCase):
    def test_running_same_phase_native_events_refresh_the_published_observation(self):
        context = self.context("live-activity", timeout=30)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        path = activity_module.sidecar_path(context.directory)
        first = latest = None
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            current = activity_module.read_sidecar(path, task_id="goal-1", attempt_id=context.attempt_id, generation=1)
            if current and current["phase"] == "streaming-model":
                if first is None:
                    first = current
                elif current["eventSeq"] > first["eventSeq"]:
                    latest = current
                    break
            time.sleep(0.05)
        from hey_my_buddy.private_dirs import context_root
        (context_root(context, "zcode") / "native-logs" / "release-turn").touch()
        self.assertIsNotNone(handle.wait(10), "controller did not settle")
        self.assertEqual(self.adapter.collect(handle, context).status, "ok")
        self.assertIsNotNone(first, "no initial streaming observation")
        self.assertIsNotNone(latest, "native events in the same phase left the published observation frozen")
        self.assertGreater(latest["lastNativeActivityAt"], first["lastNativeActivityAt"])
        self.assertEqual(latest["counts"], first["counts"])
        self.assertNotIn("fixture-private-progress", json.dumps(latest))

    def test_the_runner_publishes_a_real_bound_metadata_only_sidecar(self):
        context = self.context(timeout=20)
        _, outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        activity = outcome.result["activity"]
        self.assertTrue(activity["published"], activity)
        self.assertIn(activity["phase"], activity_module.PHASES)

        path = activity_module.sidecar_path(context.directory)
        self.assertTrue(path.is_file(), "the runner did not publish activity.json")
        payload = activity_module.read_sidecar(path, task_id="goal-1", attempt_id=context.attempt_id, generation=1)
        self.assertIsNotNone(payload, "the emitted sidecar must satisfy the real hey_my_buddy.protocol.activity reader")
        self.assertIn(payload["phase"], activity_module.PHASES)
        self.assertEqual(payload["nativeSessionId"], outcome.result["turn"]["sessionId"])
        self.assertGreaterEqual(payload["counts"]["toolCalls"], 0)
        # Timestamps are real ISO instants, not an invented heartbeat or percentage.
        from datetime import datetime

        datetime.fromisoformat(payload["observedAt"].replace("Z", "+00:00"))
        raw = path.read_text()
        for forbidden in ("fixture task", "prompt", "toolArguments", "reasoning", "fixture-secret"):
            self.assertNotIn(forbidden, raw)
        # The binding is enforced: another attempt or generation reads nothing.
        self.assertIsNone(activity_module.read_sidecar(path, task_id="goal-1", attempt_id="other", generation=1))
        self.assertIsNone(activity_module.read_sidecar(path, task_id="goal-1", attempt_id=context.attempt_id, generation=2))

    def test_same_phase_updates_are_throttled_but_recorded_phase_changes_are_written(self):
        context = self.context(timeout=20)
        # The projection is what the controller publishes; the helper coalesces
        # same-phase receipts inside its window and always writes a phase change.
        from hey_my_buddy.buddy.harnesses.zcode.protocol import ActivityProjection

        projection = ActivityProjection("sess")
        sidecar = activity_module.ActivitySidecar(context.directory, task_id="goal-1",
                                                  attempt_id="attempt-1", generation=1)
        first = sidecar.publish(projection.payload())
        self.assertIsNotNone(first)
        projection.note({"method": "state.updated", "params": {"reason": "noop"}}, 1)
        self.assertIsNone(sidecar.publish(projection.payload()), "an unchanged receipt must be coalesced")
        projection.phase = "finishing"
        self.assertIsNotNone(sidecar.publish(projection.payload()), "a phase change must be written")


class ZcodeInquiryIntegrationTests(ZcodeFixtureCase):
    def credentials(self, context, timeout=20):
        """Wait until the attempt mounted its bridge AND admitted the root turn."""
        from hey_my_buddy.private_dirs import context_root
        path = context_root(context, "zcode") / "inquiry.json"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.is_file():
                credentials = json.loads(path.read_text())
                observed = inquiry_module.bridge_request(credentials, "observe", {}, timeout_ms=500)
                if observed.get("ok") and (observed.get("value") or {}).get("ready") is True:
                    return credentials
            time.sleep(0.05)
        self.fail("the attempt never mounted its private inquiry bridge and admitted the root turn")

    def records(self, credentials) -> list[dict]:
        path = Path(credentials["resultsPath"])
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def native_log(self, context, name: str) -> str:
        from hey_my_buddy.private_dirs import context_root
        path = context_root(context, "zcode") / "native-logs" / name
        return path.read_text() if path.exists() else ""

    def test_a_live_question_queues_without_touching_the_native_session(self):
        context = self.context("live", timeout=30)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        credentials = self.credentials(context)
        asked = inquiry_module.bridge_request(credentials, "ask", {"inquiryId": "q-live", "question": "what is the status?"}, timeout_ms=4000)
        self.assertTrue(asked["ok"], asked)
        self.assertTrue(asked["value"]["accepted"])
        self.assertEqual(asked["value"]["state"], "queued")
        duplicate = inquiry_module.bridge_request(credentials, "ask", {"inquiryId": "q-live", "question": "what is the status?"}, timeout_ms=4000)
        self.assertTrue(duplicate["value"]["duplicate"])
        answered = inquiry_module.bridge_request(credentials, "answer", {"inquiryId": "q-live"}, timeout_ms=4000)
        self.assertTrue(answered["ok"], answered)
        self.assertFalse(answered["value"]["answer"]["available"])
        (context_root(context, "zcode") / "native-logs" / "release-turn").touch()
        self.assertIsNotNone(handle.wait(30), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        # The root never checkpointed, so its completed finish was refused with
        # the question and it honestly settled on assistance; the unanswered
        # question became unavailable without any native injection.
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "assistance")
        self.assertEqual(self.native_log(context, "commands.jsonl"), "")
        self.assertNotIn("v4/command", self.native_log(context, "methods.jsonl"))
        self.assertEqual(self.native_log(context, "methods.jsonl").split().count("session/send"), 1)
        records = [record for record in self.records(credentials) if record["inquiryId"] == "q-live"]
        self.assertEqual(records[0]["state"], "queued")
        self.assertEqual(records[0]["questionSha256"], asked["value"]["questionSha256"])
        self.assertEqual(records[-1]["state"], "unavailable")
        self.assertFalse(records[0]["delivery"]["startsNewTurn"])
        self.assertEqual(outcome.result["inquiry"]["supported"], True)
        self.assertEqual(outcome.result["inquiry"]["refused"], 1)
        self.assertIn("never injected", outcome.result["inquiry"]["limitation"])
        self.assertEqual(outcome.result["nativeAttention"]["requests"], 0)

    def test_the_question_socket_is_gone_after_a_settled_turn(self):
        context = self.context("live", timeout=30)
        handle = self.adapter.start(context)
        credentials = self.credentials(context)
        (context_root(context, "zcode") / "native-logs" / "release-turn").touch()
        self.assertIsNotNone(handle.wait(30), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertFalse(Path(credentials["socketPath"]).exists(), "the bridge socket must not outlive the turn")
        late = inquiry_module.bridge_request(credentials, "ask", {"inquiryId": "q-late", "question": "still there?"})
        self.assertFalse(late["ok"])
        self.assertEqual(late["reason"], "bridge-unreachable")

    def test_native_interactive_requests_end_as_host_attention(self):
        context = self.context("attention", timeout=30)
        _, outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        responses = json.loads((context_root(context, "zcode") / "native-logs" / "interaction-responses.json").read_text())
        self.assertEqual(responses["permission"]["result"]["decision"], "deny")
        self.assertEqual(responses["userInput"]["result"]["action"], "decline")
        self.assertEqual(responses["unknown"]["error"]["code"], -32601)
        self.assertEqual(outcome.result["nativeAttention"]["requests"], 3)
        self.assertEqual(outcome.result["nativeAttention"]["last"]["method"], "interaction/browserExecute")
        self.assertTrue(outcome.result["attentionRequired"])
        # The refused native request reaches the workflow as a real attention
        # outcome, not as a stored sidecar next to a completed turn.
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "attention")
        self.assertTrue(any(item["kind"] == "native-attention" for item in outcome.artifacts), outcome.artifacts)

    def test_a_completed_receipt_cannot_hide_a_refused_native_request(self):
        context = self.context("attention-after-finish", timeout=30)
        _, outcome = self.execute(context)
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertTrue(outcome.result["attentionRequired"])
        self.assertIn("Host attention", outcome.result["turnError"])
        self.assertNotIn("turn", outcome.result)
        self.assertTrue(any(item["kind"] == "native-attention" for item in outcome.artifacts), outcome.artifacts)

    def test_the_bridge_config_and_prompt_wire_the_cooperative_channel(self):
        context = self.context(timeout=20)
        _, outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        tree = json.loads((context_root(context, "zcode") / "finish-bridge.json").read_text())
        self.assertIn("attentionPath", tree)
        credentials = json.loads((context_root(context, "zcode") / "inquiry.json").read_text())
        self.assertEqual(tree["inquiryJournalPath"], credentials["resultsPath"])
        self.assertEqual(tree["identity"], {"taskId": "goal-1", "attemptId": context.attempt_id,
                                            "generation": 1, "turnId": "turn-1"})
        self.assertIn(COOPERATIVE_INQUIRY_NOTE, outcome.result["inquiry"]["limitation"])
        from hey_my_buddy.buddy.harnesses.zcode.runner import governed_prompt

        prompt = governed_prompt("task", {"turn": 1}, "mcp__buddy_x__buddy_finish_turn",
                                 checkpoint_tool="mcp__buddy_x__buddy_checkpoint",
                                 answer_tool="mcp__buddy_x__buddy_answer_inquiry")
        self.assertIn("mcp__buddy_x__buddy_checkpoint", prompt)
        self.assertIn("mcp__buddy_x__buddy_answer_inquiry", prompt)
        self.assertIn("never on a timer", prompt)


class NativeCatalogEmptyTests(ZcodeFixtureCase):
    def test_an_all_provider_disabled_catalog_is_a_complete_empty_observation(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {**self.environment, "BUDDY_ZCODE_TEST_CASE": "catalog-empty"}, clear=True):
            result = self.adapter.discover_models()
        # A successful native snapshot with no usable provider is a complete
        # observation, so the service can retire missing profiles instead of
        # preserving them as unknown forever.
        self.assertEqual(result["providers"], [])
        self.assertEqual(result["discoveries"], [{"adapter": "zcode", "status": "complete"}])
        self.assertTrue(any("empty" in warning for warning in result["warnings"]), result["warnings"])
        self.assertNotIn("fixture-secret-never-public", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
