"""Inquiry owner and journal semantics over the local endpoint, with zero models."""
from __future__ import annotations
from hey_my_buddy.protocol import inquiry_journal as journal
from hey_my_buddy.protocol.inquiry_journal import INQUIRY_JOURNAL_VERSION, MAX_JOURNAL_BYTES

import hashlib
import inspect
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from hey_my_buddy import locking
from hey_my_buddy.buddy.harnesses import c_two_live as ctl
from hey_my_buddy.buddy.harnesses import inquiry_bridge as ib
from hey_my_buddy.buddy.harnesses import live as lv
from hey_my_buddy.buddy.harnesses.run_contract import RunIdentity
from hey_my_buddy.buddy.harnesses.session_receipts import ReceiptError
from hey_my_buddy.buddy.harnesses.zcode.protocol import NativeError, RootTurnEvidence
from hey_my_buddy.buddy.roles.session_mcp import respond
from hey_my_buddy.buddy.roles import turn_io
from hey_my_buddy.errors import BoardError

TASK_MATERIAL_ROOT = Path(os.environ.get("HMB_INQUIRY_OWNER_MATERIAL_ROOT", tempfile.gettempdir()))
RUN = RunIdentity(task_id="task-owner", attempt_id="attempt-owner", generation=2,
                  invocation_id="invocation-owner", turn_id="turn-owner",
                  input_sha256="a" * 64)
BOUND = {key: RUN.to_payload()[key]
         for key in ("taskId", "attemptId", "generation", "turnId")}


def new_material_directory() -> Path:
    """Leave once-created materials for the Host's registered-root cleanup."""
    return Path(tempfile.mkdtemp(prefix="inquiry-owner-", dir=TASK_MATERIAL_ROOT))


def make_endpoint() -> ctl.CTwoLiveEndpoint:
    return ctl.CTwoLiveEndpoint(RUN, lv.LiveCapabilities(
        inquiry_delivery="cooperative-checkpoint"))


def make_bridge(directory: Path, endpoint=None, *, identity=None) -> ib.InquiryBridge:
    return ib.InquiryBridge(identity=identity or BOUND,
        journal_path=str(directory / "inquiry.jsonl"),
        attention_path=str(directory / "attention.json"),
        error_factory=NativeError,
        event_metadata=lambda message: {"kind": message.get("method", "event")},
        live=endpoint)


def request(question_id="q-owner", question="What has been verified?", request_id=None):
    return lv.LiveRequest(identity=RUN, request_id=request_id or question_id,
                          kind="inquiry", payload=lv.InquiryPayload(
                              question_id=question_id, question=question))


def direct_request(endpoint, question_id="q-owner", question="What has been verified?",
                   request_id=None):
    return lv.LiveReply.from_payload(json.loads(endpoint.request_local(
        request(question_id, question, request_id), timeout_ms=3000,
        deadline_monotonic=time.monotonic() + 3.0)))


def direct_snapshot(endpoint, **kwargs):
    return lv.LiveSnapshot.from_payload(json.loads(endpoint.observe_local(limit=32, **kwargs)))


def records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class NativeReceiptFixture:
    """Feed actual session MCP receipts into the native root evidence owner."""

    def __init__(self, bridge: ib.InquiryBridge):
        self.bridge = bridge
        self.ordinal = 0
        self.config = {"identity": BOUND, "inputSha256": RUN.input_sha256,
                       "key": "b" * 64,
                       "inquiryJournalPath": str(bridge.journal_path),
                       "attentionPath": str(bridge.attention_path)}
        self.evidence = RootTurnEvidence("native-session-owner", RUN.invocation_id,
            "mcp__buddy_fixture__hey_my_buddy_finish_turn", self.config,
            checkpoint_name="mcp__buddy_fixture__hey_my_buddy_checkpoint",
            answer_name="mcp__buddy_fixture__hey_my_buddy_answer_inquiry",
            on_delivery=bridge.deliver_inquiries, on_answer=bridge.record_answer,
            validate_outcome=turn_io.validate_outcome,
            mounted_tools=("hey_my_buddy_checkpoint", "hey_my_buddy_answer_inquiry",
                           "hey_my_buddy_finish_turn"))
        self.event("turn.started", {"inputId": RUN.invocation_id})

    def event(self, kind: str, payload: dict) -> None:
        self.ordinal += 1
        message = {"method": "session/event", "params": {
            "sessionId": "native-session-owner", "turnId": "native-root-turn",
            "seq": self.ordinal, "type": kind, "payload": payload}}
        self.evidence.observe(message, self.ordinal)
        self.bridge.note_event(message, "running")

    def tool(self, *, answer: str | None = None, tampered: bool = False,
             child: bool = False) -> str:
        tool = "hey_my_buddy_answer_inquiry" if answer is not None else "hey_my_buddy_checkpoint"
        arguments = {"inquiryId": "q-owner", "answer": answer} if answer is not None else {}
        response = respond({"jsonrpc": "2.0", "id": self.ordinal + 1, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments}}, self.config)["result"]
        if response.get("isError"):
            raise AssertionError(response)
        text = response["content"][0]["text"]
        call_id = f"call-{tool}-{self.ordinal}"
        tool_name = "mcp__buddy_fixture__" + tool
        self.event("tool.updated", {"kind": "scheduled", "toolName": tool_name,
                                    "toolCallId": call_id})
        if tampered:
            changed = json.loads(text)
            changed["signature"] = "f" * 64
            text = json.dumps(changed)
        payload = {"kind": "result", "toolCallId": call_id,
                   "result": {"success": True, "truncated": False, "content": text}}
        if child:
            payload["childSessionId"] = "native-child"
        self.event("tool.updated", payload)
        return call_id


class InquiryOwnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = new_material_directory()
        self.endpoint = make_endpoint()
        self.bridge = make_bridge(self.directory, self.endpoint)
        self.addCleanup(self.bridge.close)

    def start(self, *, active=True):
        self.bridge.start()
        if active:
            self.bridge.activate("native-session-owner")

    def test_owner_commits_question_and_publishes_original_fields_before_reply(self):
        self.start()
        reply = direct_request(self.endpoint)
        self.assertEqual((reply.status, reply.observed, reply.state), ("queued", True, "queued"))
        committed = records(self.bridge.journal_path)
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0]["question"], "What has been verified?")
        self.assertEqual(committed[0]["questionSha256"], hashlib.sha256(
            committed[0]["question"].encode()).hexdigest())
        self.assertEqual({key: committed[0][key] for key in BOUND}, BOUND)
        snapshot = direct_snapshot(self.endpoint)
        entry = snapshot.inquiries[0]
        self.assertEqual(entry.status, "queued")
        self.assertIsNotNone(entry.delivery)
        self.assertEqual(set(entry.delivery.value), {"admittedDelivery", "at"})
        self.assertEqual(entry.delivery.value["admittedDelivery"], "cooperative-checkpoint")
        self.assertEqual(snapshot.journal.entries, 1)
        self.assertTrue(snapshot.journal.available)
        self.assertTrue(snapshot.observation.ready)
        self.assertEqual(snapshot.observation.session_id, "native-session-owner")
        self.assertEqual(snapshot.observation.delivery_mode, "cooperative-checkpoint")
        self.assertFalse(any(self.directory.glob("*.sock")))

    def test_queue_admission_cannot_report_queued_before_real_fsync(self):
        self.start()
        reached = threading.Event()
        released = threading.Event()
        real_fsync = os.fsync

        def held_fsync(fd):
            reached.set()
            if not released.wait(4):
                raise OSError("test fsync barrier expired")
            real_fsync(fd)

        replies = []
        reader_results = []
        with mock.patch.object(ib.os, "fsync", held_fsync):
            writer = threading.Thread(target=lambda: replies.append(
                direct_request(self.endpoint)), daemon=True)
            writer.start()
            try:
                self.assertTrue(reached.wait(2), "the owner never reached fsync")
                self.assertEqual(replies, [])
                self.assertEqual(self.bridge.entries, {})
                self.assertEqual(direct_snapshot(self.endpoint, fields=("inquiries",)).inquiries, ())
                reader = threading.Thread(target=lambda: reader_results.append(
                    journal.read_inquiry_journal(RUN, self.bridge.journal_path)), daemon=True)
                reader.start()
                time.sleep(0.05)
                self.assertEqual(reader_results, [], "shared reader crossed an uncommitted writer")
            finally:
                released.set()
                writer.join(4)
                if "reader" in locals():
                    reader.join(4)
        self.assertFalse(writer.is_alive())
        self.assertEqual(replies[0].status, "queued")
        self.assertEqual(reader_results[0]["records"][0]["state"], "queued")

    def test_fsync_failure_keeps_native_error_and_request_retryable(self):
        self.start()
        real_fsync = os.fsync
        calls = 0

        def fail_commit_once(fd):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("injected commit refusal")
            real_fsync(fd)

        with mock.patch.object(ib.os, "fsync", fail_commit_once):
            refused = direct_request(self.endpoint)
        self.assertFalse(refused.observed)
        self.assertEqual(refused.error_code, "journal-unavailable")
        self.assertEqual(self.bridge.entries, {})
        self.assertEqual(self.bridge.journal_path.read_bytes(), b"")
        snapshot = direct_snapshot(self.endpoint)
        self.assertEqual(snapshot.inquiries, ())
        self.assertIn("journal-unavailable", snapshot.observation.error)
        retry = direct_request(self.endpoint)
        self.assertEqual(retry.status, "queued")
        self.assertEqual(len(records(self.bridge.journal_path)), 1)

    def test_replay_and_republished_snapshot_keep_seq_and_rich_delivery(self):
        self.start()
        first = direct_request(self.endpoint)
        state = direct_snapshot(self.endpoint).inquiries[0]
        for index in range(3):
            self.bridge.note_event({"method": "state.updated"}, "running")
            self.bridge.snapshot()
            replay = direct_request(self.endpoint, request_id=f"alias-{index}")
            repeated = direct_snapshot(self.endpoint).inquiries[0]
            self.assertEqual(repeated.seq, state.seq)
            self.assertEqual(repeated.delivery, state.delivery)
            self.assertEqual(replay.status, first.status)
        self.assertEqual(len(records(self.bridge.journal_path)), 1)
        conflict = direct_request(self.endpoint, question="changed")
        self.assertFalse(conflict.observed)
        self.assertEqual(len(records(self.bridge.journal_path)), 1)

    def test_constructor_checks_each_native_component_against_real_endpoint_identity(self):
        parameters = inspect.signature(ib.InquiryBridge).parameters
        self.assertNotIn("credentials", parameters)
        self.assertTrue(all(item.kind == inspect.Parameter.KEYWORD_ONLY
                            for item in parameters.values()))
        for key, value in (("taskId", "foreign-task"), ("attemptId", "foreign-attempt"),
                           ("generation", 9), ("generation", True), ("turnId", "foreign-turn")):
            with self.subTest(component=key):
                with self.assertRaises(BoardError):
                    make_bridge(new_material_directory(), self.endpoint,
                                identity={**BOUND, key: value})

    def test_close_terminalizes_before_owner_stops_and_leaves_endpoint_to_controller(self):
        self.start()
        direct_request(self.endpoint)
        thread = self.bridge.thread
        self.bridge.close()
        self.assertFalse(thread.is_alive())
        snapshot = direct_snapshot(self.endpoint)
        self.assertIsNone(snapshot.unavailable)
        self.assertEqual(snapshot.inquiries[0].status, "unavailable")
        self.assertFalse(snapshot.observation.ready)
        self.assertEqual(snapshot.observation.agent_status, "ended")
        self.assertEqual([record["state"] for record in records(self.bridge.journal_path)],
                         ["queued", "unavailable"])
        self.assertIsNone(self.endpoint._closed_reason)
        self.bridge.close()
        self.assertEqual(len(records(self.bridge.journal_path)), 2)

    def test_cooperative_native_receipts_publish_delivery_and_answer(self):
        self.start()
        native = NativeReceiptFixture(self.bridge)
        queued = direct_request(self.endpoint)
        self.assertEqual((queued.status, queued.state), ("queued", "queued"))
        first = direct_snapshot(self.endpoint).inquiries[0]
        self.assertEqual(first.status, "queued")
        self.assertEqual([item["state"] for item in records(self.bridge.journal_path)], ["queued"])
        checkpoint_id = native.tool()
        delivered = direct_snapshot(self.endpoint).inquiries[0]
        self.assertEqual((delivered.status, delivered.tool_call_id), ("delivered", checkpoint_id))
        self.assertGreater(delivered.seq, first.seq)
        answer_id = native.tool(answer="durably verified")
        answered = direct_snapshot(self.endpoint, inquiry_id="q-owner").inquiries[0]
        self.assertEqual((answered.status, answered.answer, answered.bytes, answered.via,
                          answered.tool_call_id, answered.truncated),
                         ("answered", "durably verified", 16,
                          "tool:hey_my_buddy_answer_inquiry", answer_id, False))
        self.assertIsNotNone(answered.at)
        self.assertEqual([item["state"] for item in records(self.bridge.journal_path)],
                         ["queued", "delivered", "answered"])
        self.assertEqual(direct_request(self.endpoint).status, "answered")
        self.assertEqual(direct_snapshot(self.endpoint).inquiries[0].seq, answered.seq)

    def test_child_and_tampered_receipts_cannot_upgrade_a_queued_inquiry(self):
        self.start()
        native = NativeReceiptFixture(self.bridge)
        self.assertEqual(direct_request(self.endpoint).status, "queued")
        native.tool(child=True)
        self.assertEqual(direct_snapshot(self.endpoint).inquiries[0].status, "queued")
        with self.assertRaises(ReceiptError):
            native.tool(tampered=True)
        self.assertEqual(direct_snapshot(self.endpoint).inquiries[0].status, "queued")
        self.assertEqual([item["state"] for item in records(self.bridge.journal_path)], ["queued"])
        self.bridge.close()
        self.assertIsNone(self.endpoint._closed_reason)
        ended = direct_snapshot(self.endpoint)
        self.assertEqual(ended.inquiries[0].status, "unavailable")
        self.assertEqual(ended.observation.agent_status, "ended")
        self.assertIsNone(ended.unavailable)

    def test_local_bridge_maintains_native_journal_without_a_second_channel(self):
        local = make_bridge(new_material_directory())
        self.addCleanup(local.close)
        local.start()
        local.activate("local-session")
        self.assertIsNone(local.thread)
        response = lambda ok, **fields: {"ok": ok, **fields}
        queued = local._ask({"inquiryId": "q-local", "question": "Local checkpoint?"}, response)
        self.assertTrue(queued["ok"])
        self.assertEqual(records(local.journal_path)[0]["state"], "queued")
        checkpoint = respond({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "hey_my_buddy_checkpoint", "arguments": {}}},
            {"identity": BOUND, "inputSha256": RUN.input_sha256, "key": "b" * 64,
             "inquiryJournalPath": str(local.journal_path),
             "attentionPath": str(local.attention_path)})["result"]
        self.assertFalse(checkpoint.get("isError"), checkpoint)
        self.assertEqual(json.loads(checkpoint["content"][0]["text"])["inquiries"][0]["inquiryId"],
                         "q-local")
        self.assertEqual(records(local.journal_path)[0]["state"], "queued")
        self.assertFalse(any(local.journal_path.parent.glob("*.sock")))


class InquiryJournalProjectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = new_material_directory()
        self.path = self.directory / "journal.jsonl"

    def record(self, question_id, state="queued", **extras):
        return {"version": INQUIRY_JOURNAL_VERSION, **BOUND, "inquiryId": question_id,
                "state": state, **extras}

    def write_records(self, values):
        self.path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8", newline="\n")

    def test_missing_empty_unreadable_and_over_limit_keep_complete_read_semantics(self):
        self.assertEqual(journal.read_inquiry_journal(RUN, None)["reason"], "journal-not-written")
        with self.assertRaises(FileNotFoundError):
            journal.read_shared_snapshot(self.path, MAX_JOURNAL_BYTES)
        self.assertEqual(journal.read_inquiry_journal(RUN, self.path)["reason"], "journal-not-written")
        self.path.touch()
        empty = journal.read_inquiry_journal(RUN, self.path)
        self.assertEqual(empty, {"available": True, "reason": None, "entries": 0,
                                 "records": [], "rejections": []})
        with mock.patch.object(journal, "read_shared_snapshot", return_value=None):
            self.assertEqual(journal.read_inquiry_journal(RUN, self.path)["reason"], "journal-unreadable")
        with mock.patch.object(journal.os, "read", side_effect=OSError("journal read failed")):
            self.assertEqual(journal.read_inquiry_journal(RUN, self.path)["reason"], "journal-unreadable")
        with mock.patch.object(journal.locking, "lock", side_effect=OSError("journal lock failed")):
            self.assertEqual(journal.read_inquiry_journal(RUN, self.path)["reason"], "journal-unreadable")
        self.assertEqual(journal.read_inquiry_journal(RUN, self.directory)["reason"], "journal-unreadable")
        if os.name != "nt":
            linked = self.directory / "linked.jsonl"
            linked.symlink_to(self.path)
            self.assertEqual(journal.read_inquiry_journal(RUN, linked)["reason"], "journal-unreadable")
            fifo = self.directory / "journal.fifo"
            os.mkfifo(fifo)
            self.assertEqual(journal.read_inquiry_journal(RUN, fifo)["reason"], "journal-unreadable")
        with self.path.open("r+b") as stream:
            stream.truncate(MAX_JOURNAL_BYTES + 1)
        self.assertEqual(journal.read_inquiry_journal(RUN, self.path)["reason"], "journal-exceeds-limit")

    def test_final_effective_record_rejects_foreign_and_recovers_continuous_bound_history(self):
        queued = self.record("q-recovered", question="question", questionSha256=hashlib.sha256(b"question").hexdigest())
        delivered = self.record("q-recovered", "delivered", via="tool:hey_my_buddy_checkpoint")
        self.write_records([queued, {**queued, "attemptId": "foreign"}, delivered,
                            self.record("q-rejected"),
                            self.record("q-rejected", attemptId="foreign"), [],
                            {"inquiryId": "q-version", "version": 999}])
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write("{torn tail")
        original = self.path.read_bytes()
        fact = journal.read_inquiry_journal(RUN, self.path)
        self.assertTrue(fact["available"])
        self.assertEqual(fact["entries"], 3)
        self.assertEqual(fact["records"], [queued, delivered])
        self.assertEqual({item["questionId"] for item in fact["rejections"]},
                         {"q-rejected", "q-version"})
        self.assertEqual({item["questionId"]: item["reason"] for item in fact["rejections"]},
                         {"q-rejected": "journal-attempt-mismatch",
                          "q-version": "journal-attempt-unbound"})
        self.assertEqual(self.path.read_bytes(), original)

    def test_reader_rejects_a_changed_question_hash_and_noninteger_version(self):
        queued = self.record("q-hash", question="question", questionSha256=hashlib.sha256(b"question").hexdigest())
        self.write_records([queued, self.record("q-hash", "answered", questionSha256="f" * 64,
                                               answer={"text": "wrong question"}),
                            self.record("q-version-type", version=True)])
        fact = journal.read_inquiry_journal(RUN, self.path)
        self.assertEqual(fact["records"], [])
        self.assertEqual({item["questionId"]: item["reason"] for item in fact["rejections"]},
                         {"q-hash": "journal-record-invalid", "q-version-type": "journal-attempt-unbound"})

    def test_projection_uses_only_shared_locked_read_and_preserves_answer_source_fields(self):
        answer = {"text": "verified", "bytes": 8, "truncated": False,
                  "via": "tool:hey_my_buddy_answer_inquiry", "toolCallId": "call-native-answer",
                  "at": "2026-10-07T00:00:00Z"}
        self.write_records([self.record("q-answer", "answered", answer=answer)])
        with mock.patch.object(locking, "lock", wraps=locking.lock) as lock:
            fact = journal.read_inquiry_journal(RUN, self.path)
        self.assertEqual(lock.call_count, 1)
        self.assertTrue(lock.call_args.kwargs["shared"])
        bridge = make_bridge(self.directory, make_endpoint())
        self.addCleanup(bridge.close)
        # Bind the preexisting file without copying or altering its records.
        bridge.journal_path = self.path
        bridge.start()
        entry = direct_snapshot(bridge.live).inquiries[0]
        self.assertEqual((entry.answer, entry.bytes, entry.via, entry.tool_call_id,
                          entry.at, entry.truncated),
                         (answer["text"], answer["bytes"], answer["via"],
                          answer["toolCallId"], answer["at"], False))
        self.assertEqual(len(fact["records"]), 1)

    def test_bridge_preserves_projected_entries_when_second_snapshot_is_missing(self):
        queued = self.record("q-race", question="question",
                             questionSha256=hashlib.sha256(b"question").hexdigest())
        self.write_records([queued])
        bridge = make_bridge(self.directory)
        bridge.journal_path = self.path
        with mock.patch.object(ib, "read_shared_snapshot", side_effect=FileNotFoundError):
            bridge._load_journal()
        self.assertEqual(bridge.entries["q-race"], queued)
        self.assertFalse(bridge._torn_line)

    def test_strict_projection_failure_keeps_owner_alive_and_observation_unavailable(self):
        self.write_records([self.record("q-invalid", "answered", answer={"text": "x" * 4001})])
        endpoint = make_endpoint()
        bridge = make_bridge(self.directory, endpoint)
        bridge.journal_path = self.path
        self.addCleanup(bridge.close)
        bridge.start()
        bridge.activate("native-session-owner")
        snapshot = direct_snapshot(endpoint)
        self.assertTrue(bridge.thread.is_alive())
        self.assertFalse(snapshot.observed)
        self.assertEqual(snapshot.reason, "journal-unavailable")
        self.assertIsNone(snapshot.observation)
        self.assertFalse(snapshot.journal.available)
        self.assertEqual(snapshot.journal.reason, "journal-unavailable")
        self.assertEqual(snapshot.inquiries, ())

    def test_over_limit_source_publishes_original_failure_without_usable_journal(self):
        self.path.touch()
        with self.path.open("r+b") as stream:
            stream.truncate(MAX_JOURNAL_BYTES + 1)
        endpoint = make_endpoint()
        bridge = make_bridge(self.directory, endpoint)
        bridge.journal_path = self.path
        self.addCleanup(bridge.close)
        bridge.start()
        snapshot = direct_snapshot(endpoint)
        self.assertFalse(snapshot.journal.available)
        self.assertEqual(snapshot.journal.reason, "journal-exceeds-limit")
        self.assertEqual(snapshot.observation.error, "journal-exceeds-limit")
        self.assertEqual(snapshot.inquiries, ())


if __name__ == "__main__":
    unittest.main()
