"""ACP transport boundary, growth, failure, and stop evidence tests."""
from __future__ import annotations

import json
import io
import threading
import time

from hey_my_buddy.buddy.harnesses.dsh.acp.client import PermissionPolicy
from hey_my_buddy.buddy.harnesses.dsh.acp.connection import (AcpConnectionClosed,
                                                             AcpRequestError, AcpTimeout,
                                                             _WriteJob)

from .support import AcpTestCase


def records(client, kind: str) -> list[dict]:
    path = client.facts()["factsPath"]
    with open(path, encoding="utf-8") as source:
        return [record for line in source
                if (record := json.loads(line))["recordType"] == kind]


class CorrelationTest(AcpTestCase):
    def test_out_of_order_responses_are_correlated(self):
        client = self.start_client("--delay", "session/list:0.6")
        client.initialize(timeout=15)
        client.new_session(str(self.root), timeout=15)
        results = []
        thread = threading.Thread(target=lambda: results.append(
            client.connection.request("session/list", {}, 15)))
        thread.start()
        echoed = client.set_config_option("fake-session-1", "reasoning_effort", "low", timeout=15)
        thread.join(timeout=15)
        self.assertEqual("low", echoed["configOptions"][1]["currentValue"])
        self.assertEqual("fake-session-1", results[0]["sessions"][0]["sessionId"])
        self.assertEqual(0, client.facts()["unclaimedReplyCount"])

    def test_late_reply_only_increases_counter(self):
        late = self.start_client("--delay", "session/list:2")
        late.initialize(timeout=15)
        with self.assertRaises(AcpTimeout):
            late.connection.request("session/list", {}, 0.2)
        self.assertTrue(self.wait_for(lambda: late.facts()["unclaimedReplyCount"] == 1))

    def test_duplicate_reply_only_increases_counter(self):
        duplicate = self.start_client("--duplicate-on", "session/list")
        duplicate.initialize(timeout=15)
        self.assertEqual([], duplicate.connection.request("session/list", {}, 15)["sessions"])
        self.assertTrue(self.wait_for(lambda: duplicate.facts()["unclaimedReplyCount"] == 1))
        self.assertEqual(0, duplicate.facts()["pendingRequestCount"])


class EnvelopeTest(AcpTestCase):
    def test_malformed_json_rpc_frames_never_resolve_request(self):
        client = self.start_client("--raw-frame", "initialize")
        self.assertEqual(1, client.initialize(timeout=15)["protocolVersion"])
        faults = records(client, "protocol-fault")
        self.assertEqual(6, client.facts()["protocolFaultCount"])
        kinds = [item["kind"] for item in faults]
        self.assertEqual(2, kinds.count("bad-id"))
        self.assertEqual(2, kinds.count("bad-response-shape"))
        self.assertEqual(1, client.facts()["unclaimedReplyCount"])
        self.assertNotIn("evil", json.dumps(faults))

    def test_invalid_utf8_and_error_objects_are_faults(self):
        client = self.start_client()
        client.initialize(timeout=15)
        connection = client.connection
        connection._emit_line(b'{"jsonrpc":"2.0","id":1,"result":{"x":"\xff"}}')
        connection._dispatch('{"jsonrpc":"2.0","id":1,"error":{"code":true,"message":"x"}}')
        connection._dispatch('{"jsonrpc":"2.0","id":1,"error":{"code":-1}}')
        faults = records(client, "protocol-fault")
        self.assertEqual(["invalid-utf8", "bad-error-shape", "bad-error-shape"],
                         [fault["kind"] for fault in faults])
        self.assertEqual([], client.connection.request("session/list", {}, 15)["sessions"])

    def test_oversized_inbound_line_is_discarded_and_next_line_survives(self):
        client = self.start_client()
        client.initialize(timeout=15)
        huge = b"x" * (1024 * 1024 + 64)
        stream = io.BytesIO(huge + b"\n" + b'{"jsonrpc":"2.0","id":999,"result":{}}\n')
        lines = list(client.connection._bounded_lines(stream))
        self.assertEqual(1, len(lines))
        self.assertIn(b'"id":999', lines[0])
        fault = records(client, "protocol-fault")[-1]
        self.assertEqual("oversized-line", fault["kind"])
        self.assertEqual(len(huge), fault["bytes"])

    def test_response_resolution_is_atomic(self):
        client = self.start_client()
        client.initialize(timeout=15)
        connection = client.connection
        with connection._pending_lock:
            connection._next_id += 1
            request_id = connection._next_id
            waiter = {"event": threading.Event(), "message": None, "resolved": False}
            connection._pending[request_id] = waiter
        dispatch = threading.Thread(target=lambda: connection._handle_response(
            {"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}}))
        dispatch.start()
        self.assertTrue(waiter["event"].wait(5))
        self.assertTrue(waiter["resolved"])
        self.assertEqual({"ok": True}, waiter["message"]["result"])
        dispatch.join(timeout=5)

    def test_outbound_frame_larger_than_two_mib_reaches_peer_byte_for_byte(self):
        capture = self.test_dir / "outbound-frame.ndjson"
        client = self.start_client("--capture-method", "session/list",
                                   "--capture-path", str(capture))
        client.initialize(timeout=15)
        params = {"filler": "漢字🙂" * 230_000}
        request_id = client.connection._next_id + 1
        expected = (json.dumps({"jsonrpc": "2.0", "id": request_id,
                                "method": "session/list", "params": params},
                               ensure_ascii=False) + "\n").encode("utf-8")
        self.assertGreater(len(expected), 2 * 1024 * 1024)
        self.assertEqual([], client.connection.request("session/list", params, 15)["sessions"])
        self.assertTrue(expected == capture.read_bytes(), "captured outbound frame differs")
        self.assertEqual(0, client.facts()["pendingRequestCount"])


class FailureTest(AcpTestCase):
    def test_observer_error_disconnects_connection(self):
        client = self.start_client()
        client.initialize(timeout=15)
        client.connection.observe(lambda _message: (_ for _ in ()).throw(RuntimeError("observer fault")))
        session = client.new_session(str(self.root), timeout=15)
        with self.assertRaises(AcpConnectionClosed):
            client.prompt(session["sessionId"], "work", timeout=15)
        self.assertIsInstance(client.connection.reader_error, RuntimeError)

    def test_permission_handler_error_disconnects_connection(self):
        class BrokenPolicy:
            def decide(self, _params, *, operation):
                raise RuntimeError("permission fault")
        client = self.start_client(permission_policy=BrokenPolicy())
        client.initialize(timeout=15)
        session = client.new_session(str(self.root), timeout=15)
        with self.assertRaises(AcpConnectionClosed):
            client.prompt(session["sessionId"], "work", timeout=15)
        self.assertIsInstance(client.connection.reader_error, RuntimeError)


class FactsTest(AcpTestCase):
    def test_permission_and_denial_facts_are_complete_in_private_file(self):
        client = self.start_client("--option-count", "20")
        client.initialize(timeout=15)
        session = client.new_session(str(self.root), timeout=15)
        self.assertEqual("end_turn", client.prompt(session["sessionId"], "work", timeout=30)["stopReason"])
        facts = client.facts()
        decisions = records(client, "permission-decision")
        denials = records(client, "denied-interaction")
        self.assertEqual(2, facts["permissionDecisionCount"])
        self.assertEqual(2, len(decisions))
        self.assertEqual(20, len(decisions[1]["options"]))
        self.assertEqual(1, facts["deniedInteractionCount"])
        self.assertEqual("fs/read_text_file", denials[0]["method"])
        self.assertIn("digest", denials[0]["paramsMeta"])

    def test_counters_do_not_stop_at_old_record_limit(self):
        client = self.start_client()
        client.initialize(timeout=15)
        connection = client.connection
        for index in range(220):
            connection._handle_agent_request({"method": "unsupported/operation", "id": index,
                                              "params": {"value": index}})
        self.assertEqual(220, client.facts()["deniedInteractionCount"])
        self.assertEqual(220, len(records(client, "denied-interaction")))

    def test_malformed_permission_fields_are_cancelled_and_recorded(self):
        client = self.start_client("--malformed-permission")
        client.initialize(timeout=15)
        session = client.new_session(str(self.root), timeout=15)
        self.assertEqual("end_turn", client.prompt(session["sessionId"], "work", timeout=30)["stopReason"])
        first = records(client, "permission-decision")[0]
        self.assertEqual("int", first["optionsShape"])
        self.assertEqual("missing-or-not-object", first["toolCallShape"])
        self.assertEqual({"outcome": "cancelled"}, first["outcome"])
        self.assertIn("malformed", first["basis"])

    def test_unknown_permission_option_is_never_selected(self):
        outcome, basis = PermissionPolicy().decide({
            "toolCall": {"title": "mystery"},
            "options": [{"optionId": "allow", "kind": "allow_once"},
                        {"optionId": "unknown", "kind": "weird-kind"}]},
            operation="mystery")
        self.assertEqual({"outcome": "cancelled"}, outcome)
        self.assertIn("never selected", basis)

    def test_malformed_permission_title_is_cancelled(self):
        outcome, basis = PermissionPolicy().decide({
            "toolCall": {"title": 42},
            "options": [{"optionId": "reject", "kind": "reject_once"}]},
            operation="input unavailable")
        self.assertEqual({"outcome": "cancelled"}, outcome)
        self.assertIn("malformed", basis)

    def test_native_error_data_stays_out_of_facts_and_exception_text(self):
        client = self.start_client("--error-data-on", "session/list")
        client.initialize(timeout=15)
        with self.assertRaises(AcpRequestError) as caught:
            client.connection.request("session/list", {}, 15)
        self.assertEqual("SAFE-TEST-MARKER-in-data", caught.exception.error["data"]["marker"])
        self.assertNotIn("SAFE-TEST-MARKER", str(caught.exception))
        self.assertNotIn("SAFE-TEST-MARKER", json.dumps(client.facts()))


class WriterTest(AcpTestCase):
    def test_expired_queued_frame_never_reaches_agent(self):
        client = self.start_client()
        client.initialize(timeout=15)
        connection = client.connection
        request_id = 987654
        job = _WriteJob({"jsonrpc": "2.0", "id": request_id,
                         "method": "session/list", "params": {}}, time.monotonic() - 1)
        with connection._write_queue_lock:
            connection._write_queue.append(job)
        connection._write_wake.set()
        self.assertTrue(job.done.wait(5))
        self.assertEqual(("expired",), job.outcome)
        self.assertFalse(job.started)
        self.assertFalse(any(entry.get("dir") == "in" and
                             isinstance(entry.get("raw"), dict) and
                             entry["raw"].get("id") == request_id for entry in self.agent_log()))

    def test_stalled_write_obeys_deadline_without_growing_threads(self):
        client = self.start_client("--freeze-stdin-on", "session/new")
        client.initialize(timeout=15)
        client.new_session(str(self.root), timeout=15)
        self.assertTrue(self.wait_for(lambda: "parked-frozen" in
                         (self.logs / "fake-agent.log").read_text(encoding="utf-8")))
        writer = client.connection._writer_thread
        for _ in range(3):
            started = time.monotonic()
            with self.assertRaises(AcpTimeout):
                client.set_config_option("fake-session-1", "model", "x" * (300 * 1024), timeout=0.4)
            self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(writer.is_alive())
        self.assertEqual(0, client.facts()["pendingRequestCount"])
        client.handle.terminate(grace_seconds=3)
        client.handle.wait(10)

    def test_shutdown_releases_queued_frames_behind_a_blocked_writer(self):
        client = self.start_client("--freeze-stdin-on", "session/new")
        client.initialize(timeout=15)
        client.new_session(str(self.root), timeout=15)
        self.assertTrue(self.wait_for(lambda: "parked-frozen" in
                         (self.logs / "fake-agent.log").read_text(encoding="utf-8")))
        connection = client.connection
        writing = connection._enqueue_write(
            {"jsonrpc": "2.0", "method": "session/list", "params": {"data": "x" * (300 * 1024)}},
            deadline=time.monotonic() + 30)
        self.assertTrue(self.wait_for(lambda: writing.started))
        queued = connection._enqueue_write(
            {"jsonrpc": "2.0", "method": "session/list", "params": {}},
            deadline=time.monotonic() + 30)
        try:
            connection.shutdown(drain_seconds=0.1)
            self.assertTrue(queued.done.is_set())
            self.assertEqual("failed", queued.outcome[0])
            self.assertIsInstance(queued.outcome[1], AcpConnectionClosed)
        finally:
            client.handle.terminate(grace_seconds=3)
            client.handle.wait(10)

    def test_shutdown_evidence_excludes_writer_state(self):
        client = self.start_client()
        client.initialize(timeout=15)
        evidence = client.shutdown(drain_seconds=10, settle_seconds=2)
        self.assertTrue(evidence["shutdownConfirmed"])
        self.assertEqual("gone", evidence["groupObserved"])
        self.assertNotIn("writerStopped", evidence)
        self.assertNotIn("stdinCloseSkippedWriterBusy", evidence)


class EofTest(AcpTestCase):
    def test_eof_while_request_pending_is_not_accepted_as_a_reply(self):
        client = self.start_client("--die-before-answer", "session/list")
        client.initialize(timeout=15)
        with self.assertRaises(AcpConnectionClosed):
            client.connection.request("session/list", {}, 15)
        evidence = client.shutdown(drain_seconds=10, settle_seconds=2)
        self.assertTrue(evidence["leaderExited"])
        self.assertEqual("gone", evidence["groupObserved"])
        self.assertTrue(evidence["shutdownConfirmed"])
