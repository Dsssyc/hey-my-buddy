"""Connection-level tests: correlation, faults, permission answers, EOF states.

The fake agent speaks the real wire shapes; every fault (delayed, duplicate,
garbage, hostile frames, silent exit, frozen stdin) is injected through its argv
so the client under test sees byte-level behavior, not mock objects.
"""
from __future__ import annotations

import io
import json
import threading
import time
import unittest

from hey_my_buddy.buddy.harnesses.dsh.acp.client import PermissionPolicy
from hey_my_buddy.buddy.harnesses.dsh.acp.connection import AcpConnectionClosed, AcpTimeout
from hey_my_buddy.errors import BoardError

from .support import AcpTestCase


class InitializeTest(AcpTestCase):
    def test_initialize_and_private_home_contract(self):
        client = self.start_client()
        result = client.initialize(timeout=15.0)
        self.assertEqual(1, result["protocolVersion"])
        self.assertEqual("fake-harness-acp", result["agentInfo"]["name"])
        self.assertEqual([], result["authMethods"])
        self.assertIn("resume", result["agentCapabilities"]["sessionCapabilities"])
        entries = self.agent_log()
        self.assertTrue(entries and entries[0]["event"] == "home-check")
        self.assertTrue(entries[0]["ok"], "the fake agent must run under forced private homes")


class CorrelationTest(AcpTestCase):
    def test_out_of_order_responses_are_each_correlated(self):
        client = self.start_client("--delay", "session/list:0.6")
        client.initialize(timeout=15.0)
        client.new_session(str(self.root), timeout=15.0)
        slow: list = []

        def slow_call():
            slow.append(client.list_sessions(timeout=15.0))

        thread = threading.Thread(target=slow_call)
        thread.start()
        echoed = client.set_config_option("fake-session-1", "reasoning_effort", "low", timeout=15.0)
        self.assertEqual("low", echoed["configOptions"][1]["currentValue"])
        thread.join(timeout=15)
        self.assertEqual(["fake-session-1"], [s["sessionId"] for s in slow[0]["sessions"]])
        self.assertEqual(0, client.facts()["unmatchedResponseCount"])

    def test_timeout_frees_the_id_and_the_late_response_becomes_a_fact(self):
        client = self.start_client("--delay", "session/list:2")
        client.initialize(timeout=15.0)
        with self.assertRaises(AcpTimeout):
            client.list_sessions(timeout=0.2)
        self.wait_for(lambda: client.facts()["unmatchedResponseCount"] >= 1)
        facts = client.facts()
        self.assertEqual(1, facts["unmatchedResponseCount"])
        self.assertEqual("late", facts["unmatchedResponses"][0]["kind"])
        self.assertEqual(0, facts["pendingRequestCount"])

    def test_duplicate_response_is_recorded_not_fatal(self):
        client = self.start_client("--duplicate-on", "session/list")
        client.initialize(timeout=15.0)
        result = client.list_sessions(timeout=15.0)
        self.assertEqual([], result["sessions"])
        self.wait_for(lambda: client.facts()["unmatchedResponseCount"] >= 1)
        facts = client.facts()
        self.assertEqual("duplicate", facts["unmatchedResponses"][0]["kind"])
        self.assertEqual(0, facts["protocolFaultCount"])


class EnvelopeValidationTest(AcpTestCase):
    def test_garbage_frames_are_faults_and_do_not_kill_the_connection(self):
        client = self.start_client("--garbage-on", "initialize")
        result = client.initialize(timeout=15.0)
        self.assertEqual(1, result["protocolVersion"])
        facts = client.facts()
        kinds = [fault["kind"] for fault in facts["protocolFaults"]]
        self.assertIn("invalid-json", kinds)
        self.assertIn("bad-jsonrpc", kinds)
        self.assertIn("not-an-object", kinds)
        self.assertEqual(3, facts["protocolFaultCount"])
        self.assertLessEqual(len(facts["protocolFaults"]), 50)

    def test_hostile_frames_never_correlate_and_never_masquerade(self):
        client = self.start_client("--raw-frame", "initialize")
        result = client.initialize(timeout=15.0)
        # The real answer survived; none of the hostile frames resolved anything.
        self.assertEqual(1, result["protocolVersion"])
        self.assertEqual("fake-harness-acp", result["agentInfo"]["name"])
        facts = client.facts()
        kinds = [fault["kind"] for fault in facts["protocolFaults"]]
        self.assertEqual(6, facts["protocolFaultCount"])
        self.assertEqual(2, kinds.count("bad-jsonrpc"))
        self.assertEqual(2, kinds.count("bad-id"))
        self.assertEqual(2, kinds.count("bad-response-shape"))
        self.assertEqual(1, facts["unmatchedResponseCount"])
        self.assertEqual("unknown", facts["unmatchedResponses"][0]["kind"],
                         "an id this client never sent is unknown, not late")
        self.assertEqual("999", facts["unmatchedResponses"][0]["id"])
        client.new_session(str(self.root), timeout=15.0)
        echoed = client.set_config_option("fake-session-1", "reasoning_effort", "low", timeout=15.0)
        self.assertEqual("low", echoed["configOptions"][1]["currentValue"])
        # Every fault fact is metadata only: no frame content survives.
        blob = json.dumps(facts)
        self.assertNotIn("evil", blob)

    def test_answer_then_immediate_eof_still_delivers_the_response(self):
        client = self.start_client("--respond-then-exit", "session/list")
        client.initialize(timeout=15.0)
        client.new_session(str(self.root), timeout=15.0)
        result = client.list_sessions(timeout=15.0)
        self.assertEqual(["fake-session-1"], [s["sessionId"] for s in result["sessions"]])
        self.assertTrue(self.wait_for(lambda: client.connection.eof.is_set()),
                        "the agent exits right after answering; EOF must follow")
        self.assertEqual(0, client.facts()["unmatchedResponseCount"])

    def test_oversized_lines_become_one_fault_each(self):
        from hey_my_buddy.buddy.harnesses.dsh.acp.connection import AcpConnection

        connection = AcpConnection.__new__(AcpConnection)
        connection.protocol_faults = []
        connection.protocol_fault_count = 0
        connection.frame_log = None
        huge = b"x" * (1024 * 1024 + 64)
        stream = io.BytesIO(huge + b"\n" + b'{"jsonrpc":"2.0","id":1,"result":{}}\n'
                            + b"tail-no-newline")
        lines = list(connection._bounded_lines(stream))
        self.assertEqual(2, len(lines))
        self.assertIn(b'"id":1', lines[0])
        self.assertEqual(b"tail-no-newline", lines[1])
        self.assertEqual(1, connection.protocol_fault_count)
        entry = connection.protocol_faults[0]
        self.assertEqual("oversized-line", entry["kind"])
        self.assertIn("bytes", entry)
        self.assertNotIn("preview", entry)

    def test_line_without_newline_is_discarded_with_fixed_accounting(self):
        from hey_my_buddy.buddy.harnesses.dsh.acp.connection import AcpConnection

        connection = AcpConnection.__new__(AcpConnection)
        connection.protocol_faults = []
        connection.protocol_fault_count = 0
        connection.frame_log = None
        huge = b"y" * (8 * 1024 * 1024)
        stream = io.BytesIO(huge + b"\n" + b'{"jsonrpc":"2.0","id":2,"result":{"ok":true}}\n')
        lines = list(connection._bounded_lines(stream))
        self.assertEqual(1, len(lines))
        self.assertIn(b'"id":2', lines[0])
        self.assertEqual(1, connection.protocol_fault_count)
        entry = connection.protocol_faults[0]
        self.assertEqual(len(huge), entry["bytes"])
        import hashlib

        self.assertEqual(hashlib.sha256(huge).hexdigest()[:12], entry["digest"])

    def test_line_still_oversized_at_eof_is_also_a_fault(self):
        from hey_my_buddy.buddy.harnesses.dsh.acp.connection import AcpConnection

        connection = AcpConnection.__new__(AcpConnection)
        connection.protocol_faults = []
        connection.protocol_fault_count = 0
        connection.frame_log = None
        huge = b"z" * (3 * 1024 * 1024)  # no newline ever arrives
        lines = list(connection._bounded_lines(io.BytesIO(huge)))
        self.assertEqual([], lines)
        self.assertEqual(1, connection.protocol_fault_count)
        entry = connection.protocol_faults[0]
        self.assertEqual("oversized-line", entry["kind"])
        self.assertEqual(len(huge), entry["bytes"])
        import hashlib

        self.assertEqual(hashlib.sha256(huge).hexdigest()[:12], entry["digest"])

    def _bare_connection(self):
        from collections import deque

        from hey_my_buddy.buddy.harnesses.dsh.acp.connection import AcpConnection

        connection = AcpConnection.__new__(AcpConnection)
        connection.protocol_faults = []
        connection.protocol_fault_count = 0
        connection.frame_log = None
        connection._pending = {}
        connection._pending_lock = threading.Lock()
        connection._answered = set()
        connection._answered_order = deque(maxlen=64)
        connection._timed_out = set()
        connection._timed_out_order = deque(maxlen=64)
        connection.unmatched = []
        connection.unmatched_count = 0
        return connection

    def test_invalid_utf8_is_a_fault_not_replaced_content(self):
        connection = self._bare_connection()
        connection._emit_line(b'{"jsonrpc":"2.0","id":1,"result":{"x":"\xff\xfe"}}')
        self.assertEqual(1, connection.protocol_fault_count)
        entry = connection.protocol_faults[0]
        self.assertEqual("invalid-utf8", entry["kind"])
        self.assertIn("bytes", entry)
        self.assertIn("digest", entry)

    def test_deeply_nested_frame_is_a_fault_and_the_reader_survives(self):
        connection = self._bare_connection()
        deep = '{"jsonrpc":"2.0","id":1,"result":' + "[" * 200_000 + "]" * 200_000 + "}"
        connection._dispatch(deep)
        self.assertEqual(1, connection.protocol_fault_count)
        self.assertEqual("too-deep", connection.protocol_faults[0]["kind"])
        connection._dispatch('{"jsonrpc":"2.0","id":1,"result":{}}')  # reader still alive
        self.assertEqual(1, connection.protocol_fault_count)
        self.assertEqual(1, connection.unmatched_count)

    def test_request_frames_carrying_result_or_error_are_faults(self):
        connection = self._bare_connection()
        connection.denied_interactions = []
        connection._dispatch('{"jsonrpc":"2.0","id":1,"method":"m","result":{}}')
        connection._dispatch('{"jsonrpc":"2.0","method":"m","error":{"code":1,"message":"m"}}')
        self.assertEqual(2, connection.protocol_fault_count)
        self.assertEqual(["bad-request-shape", "bad-request-shape"],
                         [fault["kind"] for fault in connection.protocol_faults])
        self.assertEqual([], connection.denied_interactions,
                         "a request with a result must never reach a handler")

    def test_error_object_field_types_are_validated(self):
        connection = self._bare_connection()
        connection._dispatch('{"jsonrpc":"2.0","id":1,"error":{"code":true,"message":"m"}}')
        connection._dispatch('{"jsonrpc":"2.0","id":1,"error":{"code":-1}}')
        connection._dispatch('{"jsonrpc":"2.0","id":1,"error":"plain string"}')
        self.assertEqual(3, connection.protocol_fault_count)
        self.assertEqual({"bad-error-shape"},
                         {fault["kind"] for fault in connection.protocol_faults})


class OutboundBoundTest(AcpTestCase):
    def test_outbound_frames_over_the_cap_are_refused_before_writing(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        with self.assertRaises(BoardError) as caught:
            client.request_update("session/list", {"filler": "x" * (2 * 1024 * 1024)},
                                  timeout=15.0)
        self.assertEqual("ACP_OUTBOUND_FRAME_TOO_LARGE", caught.exception.code)
        self.assertEqual(0, client.facts()["pendingRequestCount"],
                         "a failed send must release its pending entry")
        result = client.list_sessions(timeout=15.0)
        self.assertEqual([], result["sessions"], "the connection stays usable")


class ObserverTest(AcpTestCase):
    def test_observer_failures_are_recorded_not_hidden(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        seen: list = []

        def broken_observer(message):
            seen.append(message)
            raise RuntimeError("observer fault")

        client.connection.observe(broken_observer)
        session = client.new_session(str(self.root), timeout=15.0)
        stop = client.prompt(session["sessionId"], "work", timeout=30.0)
        self.assertEqual("end_turn", stop["stopReason"])
        self.assertTrue(seen, "observers still receive the live notifications")
        facts = client.facts()
        self.assertEqual(len(seen), facts["observationFailureCount"])
        self.assertEqual("RuntimeError", facts["observationFailures"][0]["error"])
        self.assertEqual(0, facts["protocolFaultCount"])


class PermissionAnswerTest(AcpTestCase):
    def prompt_and_collect(self, client) -> dict:
        session = client.new_session(str(self.root), timeout=15.0)
        stop = client.prompt(session["sessionId"], "run the bounded task", timeout=30.0)
        return stop

    def test_default_policy_rejects_and_cancels(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        stop = self.prompt_and_collect(client)
        self.assertEqual("end_turn", stop["stopReason"])
        decisions = client.facts()["permissionDecisions"]
        self.assertEqual(2, len(decisions))
        self.assertEqual("selected", decisions[0]["outcome"]["outcome"])
        self.assertEqual("opt-reject", decisions[0]["outcome"]["optionId"])
        self.assertEqual("reject_once", decisions[0]["basis"])
        self.assertEqual("cancelled", decisions[1]["outcome"]["outcome"])
        self.assertTrue(decisions[0]["titleMeta"], "titles are kept as metadata only")
        denied = client.facts()["deniedInteractions"]
        self.assertEqual(1, len(denied))
        self.assertEqual("fs/read_text_file", denied[0]["method"])
        self.assertIn("digest", denied[0]["paramsMeta"])
        self.assertNotIn("paramsBrief", denied[0])

    def test_allowed_title_selects_the_allow_option(self):
        client = self.start_client("--allowed-title", "write-file",
                                   permission_policy=PermissionPolicy(allowed_titles=("write-file",)))
        client.initialize(timeout=15.0)
        stop = self.prompt_and_collect(client)
        self.assertEqual("end_turn", stop["stopReason"])
        decisions = client.facts()["permissionDecisions"]
        self.assertEqual("opt-allow-only", decisions[1]["outcome"]["optionId"])
        self.assertIn("allowed tool call", decisions[1]["basis"])

    def test_retained_facts_never_carry_content_markers(self):
        client = self.start_client("--allowed-title", "SAFE-TEST-MARKER-title",
                                   "--fs-path", "FULL-ARGS-marker-value")
        client.initialize(timeout=15.0)
        stop = self.prompt_and_collect(client)
        self.assertEqual("end_turn", stop["stopReason"])
        blob = json.dumps(client.facts())
        self.assertNotIn("SAFE-TEST-MARKER", blob)
        self.assertNotIn("FULL-ARGS", blob)
        frames_path = self.logs / "frames.jsonl"
        self.assertTrue(frames_path.is_file())
        for line in frames_path.read_text().splitlines():
            entry = json.loads(line)
            self.assertIn("bytes", entry)
            self.assertIn("digest", entry)
            self.assertNotIn("raw", entry, "the metadata log carries no frame content")
            self.assertNotIn("SAFE-TEST-MARKER", line)
            self.assertNotIn("FULL-ARGS", line)

    def test_option_lists_are_capped_and_say_so(self):
        client = self.start_client("--option-count", "20")
        client.initialize(timeout=15.0)
        self.prompt_and_collect(client)
        decisions = client.facts()["permissionDecisions"]
        self.assertEqual(16, len(decisions[1]["options"]))
        self.assertTrue(decisions[1]["optionsTruncated"])

    def test_unknown_option_kinds_are_never_selected(self):
        policy = PermissionPolicy()
        outcome, basis = policy.decide({
            "toolCall": {"toolCallId": "call_x", "kind": "other", "title": "mystery"},
            "options": [{"optionId": "a", "name": "Allow", "kind": "allow_once"},
                        {"optionId": "b", "name": "Odd", "kind": "weird-kind"}]})
        self.assertEqual({"outcome": "cancelled"}, outcome)
        self.assertIn("never selected", basis)

    def test_allow_kinds_apply_only_to_exact_listed_titles(self):
        policy = PermissionPolicy(allowed_titles=("listed-title",))
        allowed = {"toolCall": {"toolCallId": "call_y", "kind": "other",
                                "title": "listed-title"},
                   "options": [{"optionId": "keep", "name": "Allow", "kind": "allow_once"}]}
        outcome, basis = policy.decide(allowed)
        self.assertEqual({"outcome": "selected", "optionId": "keep"}, outcome)
        prefix = {"toolCall": {"toolCallId": "call_p", "kind": "edit",
                               "title": "listed-title-extra"},
                  "options": [{"optionId": "keep", "name": "Allow", "kind": "allow_once"}]}
        outcome, basis = policy.decide(prefix)
        self.assertEqual({"outcome": "cancelled"}, outcome)
        self.assertIn("not on the explicit allow list", basis)
        other = {"toolCall": {"toolCallId": "call_z", "kind": "edit", "title": "bash"},
                 "options": [{"optionId": "keep", "name": "Allow", "kind": "allow_once"},
                             {"optionId": "no", "name": "Reject", "kind": "reject_once"}]}
        outcome, basis = policy.decide(other)
        self.assertEqual({"outcome": "cancelled"}, outcome,
                         "with a configured allow list, an unlisted title is never "
                         "selected even when a reject option exists")

    def test_malformed_permission_shapes_answer_cancelled(self):
        policy = PermissionPolicy(allowed_titles=("write-file",))
        cases = [
            ["not", "an", "object"],
            {"options": {"not": "an array"}},
            {"toolCall": "not an object"},
            {"toolCall": {"title": 42}, "options": []},
        ]
        for params in cases:
            outcome, basis = policy.decide(params)
            self.assertEqual({"outcome": "cancelled"}, outcome, f"params: {params!r}")
            self.assertIn("malformed", basis)


class AtomicResponseTest(AcpTestCase):
    def test_response_completion_is_atomic_against_the_deadline(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        connection = client.connection
        with connection._pending_lock:
            connection._next_id += 1
            request_id = connection._next_id
            waiter = {"event": threading.Event(), "message": None, "resolved": False}
            connection._pending[request_id] = waiter
        dispatch = threading.Thread(target=lambda: connection._handle_response(
            {"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}}))
        dispatch.start()
        # Hammer the exact transition the deadline path reads: the instant
        # resolved is observed, the message must already be there. There is no
        # sleep to hide behind - any resolved-without-message observation fails.
        observed_resolved = False
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if waiter["resolved"]:
                observed_resolved = True
                self.assertIsNotNone(waiter["message"],
                                     "resolved without a message is the half-arrived race")
                break
        self.assertTrue(observed_resolved)
        dispatch.join(timeout=5)
        self.assertTrue(waiter["event"].is_set())


class WriterBoundTest(AcpTestCase):
    @staticmethod
    def writer_threads() -> int:
        return len([thread for thread in threading.enumerate()
                    if thread.name == "acp-writer" and thread.is_alive()])

    def wait_parked(self) -> None:
        self.assertTrue(
            self.wait_for(
                lambda: "parked-frozen" in (self.logs / "fake-agent.log").read_text()),
            "the agent must be parked before the stalled writes")

    def test_stalled_writes_never_accumulate_threads_or_send_expired_frames(self):
        client = self.start_client("--freeze-stdin-on", "session/new")
        client.initialize(timeout=15.0)
        client.new_session(str(self.root), timeout=15.0)
        self.wait_parked()
        self.assertEqual(1, self.writer_threads())
        for _ in range(5):
            with self.assertRaises(AcpTimeout):
                client.set_config_option("fake-session-1", "model",
                                         "x" * (300 * 1024), timeout=0.4)
        self.assertEqual(1, self.writer_threads(),
                         "timeouts must not accumulate writer threads")
        facts = client.facts()
        self.assertTrue(facts["writeUncertain"],
                        "the one started write is an uncertain delivery")
        # The first write is stuck mid-frame; the other four timed out while
        # queued and were cancelled in place - one active plus four queued.
        self.assertEqual(5, facts["pendingWrites"])
        self.assertEqual(0, facts["pendingRequestCount"])
        frames_path = self.logs / "frames.jsonl"

        def out_count():
            return sum(1 for line in frames_path.read_text().splitlines()
                       if json.loads(line).get("dir") == "out")

        before = out_count()
        time.sleep(0.4)
        self.assertEqual(before, out_count(),
                         "queued-expired frames must never be written")
        # Ending the child settles the uncertain write and drains the queue:
        # the cancelled frames are never sent, and nothing new goes out.
        client.handle.terminate(grace_seconds=3.0)
        client.handle.wait(10)
        self.assertTrue(self.wait_for(lambda: not client.facts()["writeUncertain"]),
                        "the uncertain write must settle once the pipe is gone")
        self.wait_for(lambda: sum(1 for event in client.facts()["writeEvents"]
                                  if event["kind"] in ("cancelled-unsent", "expired-queued")) >= 4)
        self.assertEqual(before, out_count(),
                         "no expired frame may reach the wire even after settling")

    def test_cancel_cannot_stall_past_its_budget(self):
        client = self.start_client("--freeze-stdin-on", "session/new")
        client.initialize(timeout=15.0)
        client.new_session(str(self.root), timeout=15.0)
        self.wait_parked()
        # One big frame stalls the single writer; cancel must then fail within
        # its own budget instead of queueing forever behind it.
        with self.assertRaises(AcpTimeout):
            client.set_config_option("fake-session-1", "model", "x" * (300 * 1024), timeout=0.4)
        started = time.monotonic()
        with self.assertRaises(AcpTimeout):
            client.cancel("fake-session-1", timeout=1.0)
        self.assertLess(time.monotonic() - started, 10.0)
        client.handle.terminate(grace_seconds=3.0)
        client.handle.wait(10)

    def test_reader_replies_are_bounded_and_recorded_when_undeliverable(self):
        client = self.start_client("--freeze-stdin-on", "session/new")
        client.initialize(timeout=15.0)
        client.new_session(str(self.root), timeout=15.0)
        self.wait_parked()
        connection = client.connection
        # Stall the single writer; a reply enqueued now can never be delivered.
        with self.assertRaises(AcpTimeout):
            client.set_config_option("fake-session-1", "model", "x" * (300 * 1024), timeout=0.4)
        connection._submit_reply({"jsonrpc": "2.0", "id": 9999, "result": {}})
        for index in range(15):
            job, enqueued = connection._enqueue_write(
                {"filler": index}, deadline=time.monotonic() + 30.0, kind="reply")
            self.assertTrue(enqueued)
        connection._submit_reply({"jsonrpc": "2.0", "id": 9998, "result": {}})
        facts = client.facts()
        self.assertEqual(1, facts["writeFailureCount"])
        self.assertTrue(any(event["kind"] == "reply-not-delivered"
                            for event in facts["writeEvents"]),
                        "a reply that cannot even be queued is a fact")
        self.assertTrue(connection._reader.is_alive())
        self.assertEqual(0, facts["pendingRequestCount"])
        client.handle.terminate(grace_seconds=3.0)
        client.handle.wait(10)
        # Once the pipe is gone the queued replies settle as recorded facts.
        self.assertTrue(self.wait_for(lambda: any(
            event.get("write") == "reply" and event["kind"] == "write-failed"
            for event in client.facts()["writeEvents"])))

    def test_shutdown_stops_the_writer_and_fails_queued_frames(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        evidence = client.shutdown(drain_seconds=10.0, settle_seconds=2.0)
        self.assertTrue(evidence["writerStopped"])
        self.assertEqual(0, client.facts()["pendingWrites"])
        self.assertFalse(client.connection._writer_thread.is_alive())
        self.assertEqual(0, self.writer_threads())

    def test_write_deadline_covers_a_stalled_write(self):
        client = self.start_client("--freeze-stdin-on", "session/new")
        client.initialize(timeout=15.0)
        client.new_session(str(self.root), timeout=15.0)
        self.wait_parked()
        started = time.monotonic()
        with self.assertRaises(AcpTimeout) as caught:
            client.set_config_option("fake-session-1", "model", "x" * (300 * 1024), timeout=2.0)
        self.assertIn("written", str(caught.exception))
        self.assertLess(time.monotonic() - started, 15.0)
        self.assertEqual(0, client.facts()["pendingRequestCount"])
        client.handle.terminate(grace_seconds=3.0)
        client.handle.wait(10)


class WriteStateMachineTest(AcpTestCase):
    """The exact boundaries Host's probes raced, staged deterministically.

    The states are arranged under the same lock the writer uses, then the
    writer is released - no sleeps decide the race, the lock order does.
    """

    def _job(self, request_id: int):
        from hey_my_buddy.buddy.harnesses.dsh.acp.connection import _WriteJob

        return _WriteJob("request",
                         {"jsonrpc": "2.0", "id": request_id, "method": "session/list",
                          "params": {}},
                         time.monotonic() + 30.0)

    def agent_saw(self, request_id: int) -> bool:
        return any(entry.get("dir") == "in" and isinstance(entry.get("raw"), dict)
                   and entry["raw"].get("id") == request_id
                   for entry in self.agent_log())

    def test_cancelled_job_never_starts_even_after_release(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        connection = client.connection
        job = self._job(987654)
        # The state Host's probe raced: queued, unstarted, already cancelled -
        # recorded under the write-queue lock before the writer is released.
        with connection._write_queue_lock:
            connection._write_queue.append(job)
            job.cancelled = True
        connection._write_wake.set()
        self.assertTrue(self.wait_for(lambda: job.done.is_set()))
        self.assertEqual(("cancelled",), job.outcome)
        self.assertFalse(job.started, "a cancelled job must never start")
        time.sleep(0.2)  # observability only: a wrong build would write here
        self.assertFalse(self.agent_saw(987654),
                         "a cancelled frame must never reach the wire")
        self.assertFalse(client.facts()["writeUncertain"])

    def test_cancel_and_claim_are_mutually_exclusive_under_contention(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        connection = client.connection
        claimed_ids, unclaimed_ids = [], []
        for index in range(40):
            request_id = 700000 + index
            job = self._job(request_id)

            def cancel_if_unstarted(job=job):
                # Exactly what the requester's timeout path does, under the lock.
                with connection._write_queue_lock:
                    if not job.started:
                        job.cancelled = True

            if index % 2 == 0:
                # Cancel fires before the claim: the claim must settle the job
                # unsent, whatever the writer does with it afterwards.
                canceller = threading.Thread(target=cancel_if_unstarted)
                canceller.start()
                canceller.join(timeout=5)
                claimed = connection._claim_job(job)
                self.assertFalse(claimed, "a cancelled job can never be claimed")
                self.assertEqual(("cancelled",), job.outcome)
                self.assertFalse(job.started)
                unclaimed_ids.append(request_id)
            else:
                # The claim wins the lock first: the late cancellation is a
                # no-op and the frame belongs to the writer.
                claimed = connection._claim_job(job)
                self.assertTrue(claimed)
                canceller = threading.Thread(target=cancel_if_unstarted)
                canceller.start()
                canceller.join(timeout=5)
                self.assertFalse(job.cancelled, "a claimed job cannot be cancelled")
                self.assertTrue(job.started)
                connection._write_now(job.payload)
                connection._settle_job(job, ("sent", 10))
                claimed_ids.append(request_id)
        self.assertTrue(claimed_ids and unclaimed_ids,
                        "both orders of the boundary must be exercised")
        self.assertTrue(self.wait_for(
            lambda: all(self.agent_saw(request_id) for request_id in claimed_ids)))
        self.assertFalse(any(self.agent_saw(request_id) for request_id in unclaimed_ids),
                         "an unclaimed (cancelled) frame must never reach the wire")

    def test_completed_job_is_never_reversed_to_uncertain(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        connection = client.connection
        job = self._job(811111)
        # The writer claims, writes and settles before the deadline observation.
        self.assertTrue(connection._claim_job(job))
        connection._write_now(job.payload)
        connection._settle_job(job, ("sent", 10))
        self.assertEqual("settled", connection._observe_write_after_deadline(job))
        self.assertFalse(client.facts()["writeUncertain"])
        self.assertTrue(job.done.is_set())

    def test_started_unsettled_write_marks_uncertain_then_settles(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        connection = client.connection
        job = self._job(822222)
        self.assertTrue(connection._claim_job(job))  # started; completion withheld
        self.assertEqual("uncertain", connection._observe_write_after_deadline(job))
        self.assertTrue(job.uncertain)
        self.assertTrue(client.facts()["writeUncertain"])
        # The write eventually settles: the fact must resolve, not stick.
        connection._settle_job(job, ("sent", 10))
        self.assertEqual(("sent", 10), job.outcome)
        self.assertFalse(client.facts()["writeUncertain"],
                         "a settled uncertain write must not stay uncertain")
        events = client.facts()["writeEvents"]
        self.assertIn("uncertain-settled", [event["kind"] for event in events])
        # The uncertain-write event itself belongs to the real request path and
        # is covered by the stalled-writer wire tests.


class MalformedPermissionTest(AcpTestCase):
    def test_malformed_permission_fields_are_recorded_conservatively(self):
        client = self.start_client("--malformed-permission")
        client.initialize(timeout=15.0)
        session = client.new_session(str(self.root), timeout=15.0)
        stop = client.prompt(session["sessionId"], "work", timeout=30.0)
        self.assertEqual("end_turn", stop["stopReason"],
                         "the reader survives malformed fields and the turn completes")
        decisions = client.facts()["permissionDecisions"]
        self.assertEqual(2, len(decisions))
        first = decisions[0]
        self.assertEqual("int", first["optionsShape"])
        self.assertEqual("missing-or-not-object", first["toolCallShape"])
        self.assertIsNone(first["titleMeta"])
        self.assertEqual({"outcome": "cancelled"}, first["outcome"])
        self.assertIn("malformed", first["basis"])
        self.assertEqual(0, client.facts()["protocolFaultCount"])
        client.close_session(session["sessionId"], timeout=15.0)


class ErrorTextTest(AcpTestCase):
    def test_error_data_stays_out_of_the_exception_text(self):
        from hey_my_buddy.buddy.harnesses.dsh.acp.connection import AcpRequestError

        client = self.start_client("--error-data-on", "session/list")
        client.initialize(timeout=15.0)
        with self.assertRaises(AcpRequestError) as caught:
            client.list_sessions(timeout=15.0)
        error = caught.exception
        self.assertNotIn("SAFE-TEST-MARKER", str(error))
        self.assertNotIn("SAFE-TEST-MARKER", repr(error))
        # The raw error object stays live for the trusted caller.
        self.assertEqual("SAFE-TEST-MARKER-in-data", error.error["data"]["marker"])
        self.assertNotIn("SAFE-TEST-MARKER", json.dumps(client.facts()))


class NotificationKindCapTest(AcpTestCase):
    def test_distinct_notification_kinds_are_capped_honestly(self):
        client = self.start_client("--spam-notifications", "100")
        client.initialize(timeout=15.0)
        facts = client.facts()
        self.assertTrue(facts["notificationKindsTruncated"])
        self.assertLessEqual(len(facts["notificationCounts"]), 65)
        self.assertIn("<overflow>", facts["notificationCounts"])
        self.assertGreaterEqual(facts["notificationCounts"]["<overflow>"], 36)


class EofTest(AcpTestCase):
    def test_eof_while_pending_raises_and_stop_evidence_stays_conservative(self):
        client = self.start_client("--die-before-answer", "session/list")
        client.initialize(timeout=15.0)
        with self.assertRaises(AcpConnectionClosed):
            client.list_sessions(timeout=15.0)
        evidence = client.shutdown(drain_seconds=10.0, settle_seconds=2.0)
        self.assertTrue(evidence["leaderExited"])
        self.assertEqual(0, evidence["leaderExitCode"])
        self.assertEqual("gone", evidence["groupObserved"])
        self.assertTrue(evidence["shutdownConfirmed"])

    def test_clean_eof_after_close_confirms_group_gone(self):
        client = self.start_client()
        client.initialize(timeout=15.0)
        session = client.new_session(str(self.root), timeout=15.0)
        client.close_session(session["sessionId"], timeout=15.0)
        evidence = client.shutdown(drain_seconds=10.0, settle_seconds=2.0)
        self.assertTrue(evidence["leaderExited"])
        self.assertEqual("gone", evidence["groupObserved"])
        self.assertTrue(evidence["shutdownConfirmed"])
        self.assertFalse(client.facts()["metaLogTruncated"])

    def test_group_observation_maps_errors_conservatively(self):
        import os

        from hey_my_buddy.buddy.harnesses.dsh.acp.connection import group_observation

        class FakeHandle:
            job = None
            pgid = 424242

        handle = FakeHandle()
        original = os.killpg
        try:
            os.killpg = lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError())
            self.assertEqual("gone", group_observation(handle))
            os.killpg = lambda pid, sig: (_ for _ in ()).throw(PermissionError())
            self.assertEqual("alive", group_observation(handle))
            os.killpg = lambda pid, sig: (_ for _ in ()).throw(OSError("unavailable"))
            self.assertEqual("unknown", group_observation(handle))
        finally:
            os.killpg = original


if __name__ == "__main__":  # pragma: no cover - direct execution convenience
    unittest.main()
