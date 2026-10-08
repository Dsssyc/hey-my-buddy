"""Inquiry owner migration over the accepted C-Two endpoint, with zero models.

The owner tests drive real admission and journal commits in process. The peer
tests run the same owner in a private subprocess, use the existing test CRM,
and feed actual session MCP receipts through ZCode's native root evidence.
The endpoint's queued reply is never treated as a native delivery receipt.
"""
from __future__ import annotations

import hashlib
import inspect
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
from unittest import mock

from buddy.harnesses.test_c_two_live import (
    SANITIZED_VARIABLES, TEST_CRM, decode_reply, decode_snapshot, wire_observe,
    wire_request,
)
from hey_my_buddy import locking
from hey_my_buddy.buddy.harnesses import c_two_live as ctl
from hey_my_buddy.buddy.harnesses import inquiry_bridge as ib
from hey_my_buddy.buddy.harnesses import live as lv
from hey_my_buddy.buddy.harnesses.run_contract import RunIdentity
from hey_my_buddy.buddy.harnesses.session_receipts import (
    INQUIRY_JOURNAL_VERSION, MAX_JOURNAL_BYTES,
)
from hey_my_buddy.buddy.harnesses.zcode.protocol import (
    COOPERATIVE_INQUIRY_NOTE, NativeError, RootTurnEvidence,
)
from hey_my_buddy.buddy.roles import turn_io
from hey_my_buddy.buddy.roles.session_mcp import respond
from hey_my_buddy.errors import BoardError

TASK_MATERIAL_ROOT = Path(os.environ.get("BUDDY_INQUIRY_OWNER_MATERIAL_ROOT", tempfile.gettempdir()))
RUN = RunIdentity(task_id="task-owner", attempt_id="attempt-owner", generation=2,
                  invocation_id="invocation-owner", turn_id="turn-owner",
                  input_sha256="a" * 64)
BOUND = {key: RUN.to_payload()[key]
         for key in ("taskId", "attemptId", "generation", "turnId")}
TEST_TOKEN = "d" * 64
TEST_INSTANCE = "c" * 64


def new_material_directory() -> Path:
    """Leave once-created materials for the Host's registered-root cleanup."""
    return Path(tempfile.mkdtemp(prefix="inquiry-owner-", dir=TASK_MATERIAL_ROOT))


def make_endpoint() -> ctl.CTwoLiveEndpoint:
    return ctl.CTwoLiveEndpoint(RUN, lv.LiveCapabilities(
        inquiry_delivery="cooperative-checkpoint"), TEST_CRM,
        instance_id=TEST_INSTANCE, token=TEST_TOKEN)


def make_bridge(directory: Path, endpoint=None, *, identity=None) -> ib.InquiryBridge:
    return ib.InquiryBridge(identity=identity or BOUND,
        journal_path=str(directory / "inquiry.jsonl"),
        attention_path=str(directory / "attention.json"),
        error_factory=NativeError,
        event_metadata=lambda message: {"kind": message.get("method", "event")},
        limitation=COOPERATIVE_INQUIRY_NOTE, live=endpoint)


def request(question_id="q-owner", question="What has been verified?", request_id=None):
    return lv.LiveRequest(identity=RUN, request_id=request_id or question_id,
                          kind="inquiry", payload=lv.InquiryPayload(
                              question_id=question_id, question=question))


def direct_request(endpoint, question_id="q-owner", question="What has been verified?",
                   request_id=None):
    return decode_reply(endpoint.request(wire_request(
        request(question_id, question, request_id), token=TEST_TOKEN,
        instance_id=TEST_INSTANCE, timeout_ms=3000)))


def direct_snapshot(endpoint, **kwargs):
    return decode_snapshot(endpoint.observe(wire_observe(
        RUN, token=TEST_TOKEN, instance_id=TEST_INSTANCE, limit=32, **kwargs)))


def records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


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
        self.assertFalse(entry.delivery.value["startsNewTurn"])
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
                    ib.read_inquiry_journal(RUN, self.bridge.journal_path)), daemon=True)
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

    def test_retired_transport_entrypoints_are_absent(self):
        for name in ("bind_live_channel", "bridge_request", "ExistingLiveChannel"):
            self.assertFalse(hasattr(ib, name), name)
        for name in ("handle", "_serve", "_accept_loop", "_clear_stale_socket",
                     "describe_answer", "_discard"):
            self.assertFalse(hasattr(self.bridge, name), name)

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
            "params": {"name": "buddy_checkpoint", "arguments": {}}},
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
        self.path.write_text("".join(json.dumps(value) + "\n" for value in values))

    def test_missing_empty_unreadable_and_over_limit_keep_complete_read_semantics(self):
        self.assertEqual(ib.read_inquiry_journal(RUN, None)["reason"], "no-journal-path")
        self.assertEqual(ib.read_inquiry_journal(RUN, self.path)["reason"], "journal-not-written")
        self.path.touch()
        empty = ib.read_inquiry_journal(RUN, self.path)
        self.assertEqual(empty, {"available": True, "reason": None, "entries": 0,
                                 "records": [], "rejections": []})
        with mock.patch.object(ib, "read_shared_snapshot", return_value=None):
            self.assertEqual(ib.read_inquiry_journal(RUN, self.path)["reason"], "journal-unreadable")
        self.assertEqual(ib.read_inquiry_journal(RUN, self.directory)["reason"], "journal-unreadable")
        with self.path.open("r+b") as stream:
            stream.truncate(MAX_JOURNAL_BYTES + 1)
        self.assertEqual(ib.read_inquiry_journal(RUN, self.path)["reason"], "journal-exceeds-limit")

    def test_final_effective_record_rejects_foreign_and_recovers_continuous_bound_history(self):
        queued = self.record("q-recovered", question="question", questionSha256="a" * 64)
        delivered = self.record("q-recovered", "delivered", via="tool:buddy_checkpoint")
        self.write_records([queued, {**queued, "attemptId": "foreign"}, delivered,
                            self.record("q-rejected"),
                            self.record("q-rejected", attemptId="foreign"), [],
                            {"inquiryId": "q-version", "version": 999}])
        with self.path.open("a") as stream:
            stream.write("{torn tail")
        original = self.path.read_bytes()
        fact = ib.read_inquiry_journal(RUN, self.path)
        self.assertTrue(fact["available"])
        self.assertEqual(fact["entries"], 3)
        self.assertEqual(fact["records"], [queued, delivered])
        self.assertEqual({item["questionId"] for item in fact["rejections"]},
                         {"q-rejected", "q-version"})
        self.assertEqual(self.path.read_bytes(), original)

    def test_projection_uses_only_shared_locked_read_and_preserves_answer_source_fields(self):
        answer = {"text": "verified", "bytes": 8, "truncated": False,
                  "via": "tool:buddy_answer_inquiry", "toolCallId": "call-native-answer",
                  "at": "2026-10-07T00:00:00Z"}
        self.write_records([self.record("q-answer", "answered", answer=answer)])
        with mock.patch.object(locking, "lock", wraps=locking.lock) as lock:
            fact = ib.read_inquiry_journal(RUN, self.path)
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


def peer_main() -> int:
    """Real C-Two controller peer; stdin commands model native fixture events."""
    os.environ["C2_RELAY_ANCHOR_ADDRESS"] = ""
    os.environ["C2_ENV_FILE"] = ""
    endpoint = make_endpoint()
    bridge = None
    evidence = None
    ordinal = 0

    def event(kind, payload):
        nonlocal ordinal
        ordinal += 1
        message = {"method": "session/event", "params": {
            "sessionId": "native-session-owner", "turnId": "native-root-turn",
            "seq": ordinal, "type": kind, "payload": payload}}
        evidence.observe(message, ordinal)
        bridge.note_event(message, "running")

    for line in sys.stdin:
        command = json.loads(line)
        try:
            op = command["op"]
            if op == "start":
                directory = Path(command["directory"])
                descriptor = endpoint.start()
                bridge = make_bridge(directory, endpoint)
                bridge.start()
                bridge.activate("native-session-owner")
                config = {"identity": BOUND, "inputSha256": RUN.input_sha256,
                          "key": "b" * 64,
                          "inquiryJournalPath": str(bridge.journal_path),
                          "attentionPath": str(bridge.attention_path)}
                evidence = RootTurnEvidence("native-session-owner", RUN.invocation_id,
                    "mcp__buddy_fixture__buddy_finish_turn", config,
                    checkpoint_name="mcp__buddy_fixture__buddy_checkpoint",
                    answer_name="mcp__buddy_fixture__buddy_answer_inquiry",
                    on_delivery=bridge.deliver_inquiries, on_answer=bridge.record_answer,
                    validate_outcome=turn_io.validate_outcome,
                    mounted_tools=("buddy_checkpoint", "buddy_answer_inquiry", "buddy_finish_turn"))
                event("turn.started", {"inputId": RUN.invocation_id})
                result = {"descriptor": descriptor.to_payload()}
            elif op in ("checkpoint", "answer", "tamperedCheckpoint", "childCheckpoint"):
                tool = "buddy_answer_inquiry" if op == "answer" else "buddy_checkpoint"
                arguments = ({"inquiryId": command["questionId"], "answer": command["answer"]}
                             if op == "answer" else {})
                response = respond({"jsonrpc": "2.0", "id": ordinal + 1, "method": "tools/call",
                                    "params": {"name": tool, "arguments": arguments}}, config)["result"]
                if response.get("isError"):
                    raise AssertionError(response)
                text = response["content"][0]["text"]
                call_id = f"call-{op}-{ordinal}"
                tool_name = "mcp__buddy_fixture__" + tool
                event("tool.updated", {"kind": "scheduled", "toolName": tool_name,
                                       "toolCallId": call_id})
                if op == "tamperedCheckpoint":
                    changed = json.loads(text)
                    changed["signature"] = "f" * 64
                    text = json.dumps(changed)
                payload = {"kind": "result", "toolCallId": call_id,
                           "result": {"success": True, "truncated": False, "content": text}}
                if op == "childCheckpoint":
                    payload["childSessionId"] = "native-child"
                event("tool.updated", payload)
                result = {"callId": call_id, "states": {
                    question_id: entry["state"] for question_id, entry in bridge.entries.items()}}
            elif op == "close":
                bridge.close()
                result = {"ownerAlive": bool(bridge.thread and bridge.thread.is_alive()),
                          "endpointClosed": endpoint._closed_reason is not None}
            elif op == "stop":
                bridge.close()
                endpoint.stop()
                print(json.dumps({"ok": True}), flush=True)
                return 0
            else:
                raise AssertionError("unknown peer command")
            print(json.dumps({"ok": True, **result}), flush=True)
        except Exception as error:
            print(json.dumps({"ok": False, "error": type(error).__name__,
                              "code": getattr(error, "code", None),
                              "detail": str(error)[:400]}), flush=True)
    return 0


class InquiryOwnerPeerTests(unittest.TestCase):
    def setUp(self):
        self.directory = new_material_directory()
        environment = {key: value for key, value in os.environ.items()
                       if key not in SANITIZED_VARIABLES}
        environment.update(C2_ENV_FILE="", C2_RELAY_ANCHOR_ADDRESS="")
        self.stderr_path = self.directory / "peer.stderr"
        self.stderr = self.stderr_path.open("x")
        self.addCleanup(self.stderr.close)
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--owner-peer"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
            env=environment, text=True)
        self.addCleanup(self.stop_peer)
        started = self.command("start", directory=str(self.directory))
        self.assertTrue(started["ok"], started)
        self.descriptor = ctl.LiveEndpointDescriptor.from_payload(started["descriptor"])
        self.channel = ctl.CTwoLiveChannel(RUN, TEST_CRM, name=self.descriptor.name,
            address=self.descriptor.address, instance_id=self.descriptor.instance_id,
            token=TEST_TOKEN)
        self.addCleanup(lambda: self.channel.close(reason="test-ended"))

    def command(self, op, **fields):
        self.process.stdin.write(json.dumps({"op": op, **fields}) + "\n")
        self.process.stdin.flush()
        ready, _, _ = select.select([self.process.stdout], [], [], 10)
        if not ready:
            self.fail(f"peer command {op} did not answer; stderr artifact: {self.stderr_path.name}")
        line = self.process.stdout.readline()
        self.assertTrue(line, f"peer exited while handling {op}")
        return json.loads(line)

    def stop_peer(self):
        if self.process.poll() is None:
            try:
                self.command("stop")
                self.process.wait(5)
            except (OSError, subprocess.TimeoutExpired, AssertionError):
                self.process.kill()
                self.process.wait(5)
        self.process.stdin.close()
        self.process.stdout.close()

    def observe(self, **kwargs):
        return self.channel.observe(timeout_ms=3000, limit=32, **kwargs)

    def test_real_ctwo_owner_queue_requires_verified_cooperative_native_delivery_and_answer(self):
        queued = self.channel.request(request(), timeout_ms=4000)
        self.assertEqual((queued.status, queued.state), ("queued", "queued"))
        first = self.observe().inquiries[0]
        self.assertEqual(first.status, "queued")
        self.assertEqual([item["state"] for item in records(self.directory / "inquiry.jsonl")], ["queued"])
        checkpoint = self.command("checkpoint")
        self.assertTrue(checkpoint["ok"], checkpoint)
        delivered = self.observe().inquiries[0]
        self.assertEqual(delivered.status, "delivered")
        self.assertEqual(delivered.tool_call_id, checkpoint["callId"])
        self.assertGreater(delivered.seq, first.seq)
        answered = self.command("answer", questionId="q-owner", answer="durably verified")
        self.assertTrue(answered["ok"], answered)
        answer = self.observe(inquiry_id="q-owner").inquiries[0]
        self.assertEqual((answer.status, answer.answer, answer.bytes, answer.via,
                          answer.tool_call_id, answer.truncated),
                         ("answered", "durably verified", 16, "tool:buddy_answer_inquiry",
                          answered["callId"], False))
        self.assertIsNotNone(answer.at)
        self.assertEqual([item["state"] for item in records(self.directory / "inquiry.jsonl")],
                         ["queued", "delivered", "answered"])
        self.assertEqual(self.channel.request(request(), timeout_ms=3000).status, "answered")
        self.assertEqual(self.observe().inquiries[0].seq, answer.seq)

    def test_tampered_receipt_and_child_native_evidence_cannot_upgrade_rpc_queue_receipt(self):
        self.assertEqual(self.channel.request(request(), timeout_ms=4000).status, "queued")
        child = self.command("childCheckpoint")
        self.assertTrue(child["ok"], child)
        self.assertEqual(self.observe().inquiries[0].status, "queued")
        forged = self.command("tamperedCheckpoint")
        self.assertFalse(forged["ok"])
        self.assertEqual(forged["error"], "NativeError")
        self.assertEqual(self.observe().inquiries[0].status, "queued")
        self.assertEqual([item["state"] for item in records(self.directory / "inquiry.jsonl")], ["queued"])
        closed = self.command("close")
        self.assertFalse(closed["ownerAlive"])
        self.assertFalse(closed["endpointClosed"])
        ended = self.observe()
        self.assertEqual(ended.inquiries[0].status, "unavailable")
        self.assertEqual(ended.observation.agent_status, "ended")
        self.assertIsNone(ended.unavailable)


if __name__ == "__main__":
    if "--owner-peer" in sys.argv:
        raise SystemExit(peer_main())
    unittest.main()
