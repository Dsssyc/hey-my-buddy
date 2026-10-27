"""Claude stream-json protocol unit tests over pipes; no native process is started."""
from __future__ import annotations

import json
import os
import threading
import time
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses.claude import protocol

from hey_my_buddy.buddy.harnesses.claude.protocol import (
    ClaudeProtocolError,
    Connection,
    ProviderRejected,
    TurnEvidence,
    model_usage_keys,
    result_quota_denial,
)


class ObservedPipeReader:
    """Keep a fake pipe's read descriptor alive until its reader exits."""

    def __init__(self, stream):
        self.stream = stream
        self.reader_started = threading.Event()
        self.reader = None

    def readline(self, *args):
        self.reader = threading.current_thread()
        self.reader_started.set()
        return self.stream.readline(*args)

    def close(self):
        if self.reader is None or self.reader.is_alive():
            raise AssertionError("the fake stdout reader is still using this descriptor")
        self.stream.close()


class FakeProcess:
    """A process-shaped object over two pipes: the controller side reads stdout and writes stdin."""

    def __init__(self):
        stdin_read, stdin_write = os.pipe()
        stdout_read, stdout_write = os.pipe()
        self.stdin = os.fdopen(stdin_write, "wb", buffering=0)
        self.stdout = ObservedPipeReader(os.fdopen(stdout_read, "rb", buffering=0))
        self.native_reads = os.fdopen(stdin_read, "rb", buffering=0)
        self.native_writes = os.fdopen(stdout_write, "wb", buffering=0)


class QuotaParsingTests(unittest.TestCase):
    def test_result_quota_denial_uses_structured_fields_only(self):
        self.assertTrue(result_quota_denial({"api_error_status": 429}))
        self.assertTrue(result_quota_denial({"api_error_status": "429"}))
        self.assertTrue(result_quota_denial({"subtype": "error_usage_limit_reached"}))
        # Arbitrary prose in free-text fields never infers exhaustion.
        self.assertFalse(result_quota_denial({"subtype": "error_during_execution",
                                              "result": "quota exhausted according to the message"}))
        self.assertFalse(result_quota_denial({}))

    def test_model_usage_keys_retain_the_observed_set(self):
        result = {"modelUsage": {"claude-opus-5": {}, "claude-haiku-4-5": {}, "zzz-other": {}}}
        self.assertEqual(model_usage_keys(result), ["claude-haiku-4-5", "claude-opus-5", "zzz-other"])
        self.assertEqual(len(model_usage_keys({"modelUsage": {str(i): {} for i in range(40)}})), 40)
        self.assertEqual(model_usage_keys({"modelUsage": {"x" * 129: {}}}), ["x" * 129])
        self.assertEqual(model_usage_keys({}), [])


class TurnEvidenceTests(unittest.TestCase):
    def test_structured_root_overload_excludes_error_text_from_model_activity(self):
        for active in (False, True):
            evidence = TurnEvidence("session-1", "/tmp/checkout-a")
            if active:
                evidence.observe({"type": "assistant", "session_id": "session-1",
                                  "message": {"content": [{"type": "text", "text": "work"}]}})
            with self.assertRaises(ProviderRejected) as caught:
                evidence.observe({"type": "assistant", "session_id": "session-1", "error": "overloaded",
                                  "message": {"content": [{"type": "text", "text": "provider failure"}]}})
            self.assertEqual(caught.exception.classification, "overloaded")
            self.assertEqual(caught.exception.native_code, "overloaded")
            self.assertEqual(evidence.rejection_phase(), "during-model" if active else "before-model")
            if not active:
                self.assertIsNone(evidence.assistant_text)

    def test_overload_requires_root_structured_code_not_prose_or_status(self):
        for extra in ({"parent_tool_use_id": "child"}, {"session_id": "other"}):
            evidence = TurnEvidence("session-1", "/tmp/checkout-a")
            evidence.observe({"type": "assistant", "session_id": "session-1", "error": "overloaded", **extra})
        for frame in ({"type": "assistant", "message": {"content": [{"type": "text", "text": "overloaded"}]}},
                      {"type": "assistant", "error": "server_error", "api_error_status": 529},
                      {"type": "result", "subtype": "error_during_execution", "api_error_status": 529}):
            TurnEvidence("session-1", "/tmp/checkout-a").observe(frame)

    def test_init_binds_session_and_cwd_and_records_model_readback(self):
        evidence = TurnEvidence("session-1", "/tmp/checkout-a")
        evidence.observe({"type": "system", "subtype": "init", "session_id": "session-1",
                          "cwd": "/tmp/checkout-a", "model": "claude-opus-5-5[1m]"})
        self.assertTrue(evidence.init_observed)
        self.assertEqual(evidence.init_session_id, "session-1")
        self.assertEqual(evidence.session_model, "claude-opus-5-5[1m]")
        with self.assertRaises(ClaudeProtocolError) as missing:
            TurnEvidence("session-1", "/tmp/checkout-a").observe(
                {"type": "system", "subtype": "init", "cwd": "/tmp/checkout-a"})
        self.assertEqual(missing.exception.code, "native-init-missing")
        with self.assertRaises(ClaudeProtocolError) as mismatch:
            TurnEvidence("session-1", "/tmp/checkout-a").observe(
                {"type": "system", "subtype": "init", "session_id": "session-2", "cwd": "/tmp/checkout-a"})
        self.assertEqual(mismatch.exception.code, "wrong-native-session")

    def test_duplicate_results_and_wrong_workspace_are_rejected(self):
        evidence = TurnEvidence("session-1", "/tmp/checkout-a")
        evidence.observe({"type": "system", "subtype": "init", "session_id": "session-1", "cwd": "/tmp/checkout-a"})
        evidence.observe({"type": "result", "subtype": "success", "session_id": "session-1"})
        with self.assertRaises(ClaudeProtocolError):
            evidence.observe({"type": "result", "subtype": "success", "session_id": "session-1"})
        other = TurnEvidence("session-1", "/tmp/checkout-a")
        with self.assertRaises(ClaudeProtocolError) as caught:
            other.observe({"type": "system", "subtype": "init", "session_id": "session-1",
                           "cwd": "/definitely/elsewhere"})
        self.assertEqual(caught.exception.code, "wrong-native-workspace")

    def test_non_root_frames_never_end_the_root_turn(self):
        evidence = TurnEvidence("session-1", "/tmp/checkout-a")
        evidence.observe({"type": "system", "subtype": "init", "session_id": "session-1", "cwd": "/tmp/checkout-a"})
        evidence.observe({"type": "result", "subtype": "success", "session_id": "session-1",
                          "parent_tool_use_id": "toolu_subagent_1"})
        self.assertIsNone(evidence.result, "a subagent result must not satisfy the root wait")
        evidence.observe({"type": "result", "subtype": "success", "session_id": "session-1"})
        self.assertIsNotNone(evidence.result)

    def test_rejected_rate_limit_raises_quota_with_bounded_facts(self):
        evidence = TurnEvidence("session-1", "/tmp/checkout-a")
        with self.assertRaises(ProviderRejected) as caught:
            evidence.observe({"type": "rate_limit_event", "rate_limit_info": {
                "status": "rejected", "rateLimitType": "five_hour", "resetsAt": "2026-09-26T12:00:00Z"}})
        self.assertEqual(caught.exception.native_code, "five_hour")
        self.assertEqual(caught.exception.resets_at, "2026-09-26T12:00:00Z")

    def test_rejected_rate_limit_fails_even_with_a_malformed_identity(self):
        with self.assertRaises(ProviderRejected) as caught:
            TurnEvidence("session-1", "/tmp/checkout-a").observe(
                {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}})
        self.assertEqual(caught.exception.native_code, "unknown")
        self.assertIsNone(caught.exception.resets_at)
        with self.assertRaises(ProviderRejected) as malformed:
            TurnEvidence("session-1", "/tmp/checkout-a").observe(
                {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected",
                                                                 "rateLimitType": {"bad": "type"}}})
        self.assertEqual(malformed.exception.native_code, "unknown")

    def test_a_free_text_rate_limit_identity_is_never_kept_as_a_fact(self):
        # A rate-limit identity is an identifier, never free text: whitespace,
        # control characters or credential-like fragments are dropped or
        # replaced with "unknown" instead of being published.
        for bad in ("five hour", "Bearer sk-ant-api03-fragment\n", "primary?x=1", "x" * 65):
            with self.subTest(bad=bad):
                with self.assertRaises(ProviderRejected) as caught:
                    TurnEvidence("session-1", "/tmp/checkout-a").observe(
                        {"type": "rate_limit_event",
                         "rate_limit_info": {"status": "rejected", "rateLimitType": bad}})
                self.assertEqual(caught.exception.native_code, "unknown")

    def test_quota_rejection_phase_uses_root_model_activity_only(self):
        init = {"type": "system", "subtype": "init", "session_id": "session-1", "cwd": "/tmp/checkout-a"}
        cases = (
            ({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}, "during-model"),
            ({"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "idea"}]}}, "during-model"),
            ({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}}, "during-model"),
            ({"type": "assistant", "message": {"content": [], "usage": {"output_tokens": 2}}}, "during-model"),
            ({"type": "result", "usage": {"output_tokens": 1}}, "during-model"),
            ({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "thinking_delta"}}}, "during-model"),
            ({"type": "user", "message": {"content": "input"}}, "before-model"),
            ({"type": "stream_event", "event": {"type": "message_start"}}, "before-model"),
            ({"type": "assistant", "parent_tool_use_id": "child", "message": {"content": [{"type": "text"}]}}, "before-model"),
        )
        for frame, expected in cases:
            with self.subTest(frame=frame):
                evidence = TurnEvidence("session-1", "/tmp/checkout-a")
                evidence.observe(init)
                self.assertEqual(evidence.rejection_phase(), "before-model")
                evidence.observe(frame)
                self.assertEqual(evidence.rejection_phase(), expected)

    def test_assistant_tool_use_reports_tool_activity(self):
        evidence = TurnEvidence("session-1", "/tmp/checkout-a")
        phase, tool = evidence.observe({"type": "assistant", "message": {
            "content": [{"type": "tool_use", "name": "Bash", "id": "t1"}]}})
        self.assertEqual((phase, tool), ("tool-running", "Bash"))
        self.assertEqual(evidence.tool_calls, 1)

    def test_background_task_lifecycle_uses_an_exact_terminal_set(self):
        evidence = TurnEvidence("session-1", "/tmp/checkout-a")
        evidence.observe({"type": "system", "subtype": "task_started",
                          "task_id": "task-bg-1", "task_type": "local_agent"})
        self.assertEqual(evidence.unsettled_background_tasks(), ["task-bg-1"])
        # Substring lookalikes must not settle a still-running task.
        evidence.observe({"type": "system", "subtype": "task_updated",
                          "task_id": "task-bg-1", "patch": {"status": "not_completed"}})
        self.assertEqual(evidence.unsettled_background_tasks(), ["task-bg-1"])
        evidence.observe({"type": "system", "subtype": "task_updated",
                          "task_id": "task-bg-1", "patch": {"status": "incomplete"}})
        self.assertEqual(evidence.unsettled_background_tasks(), ["task-bg-1"])
        for terminal in ("completed", "failed", "cancelled"):
            evidence.observe({"type": "system", "subtype": "task_updated",
                              "task_id": "task-bg-1", "patch": {"status": terminal}})
        self.assertEqual(evidence.unsettled_background_tasks(), [])
        evidence.observe({"type": "system", "subtype": "task_started", "task_type": "local_workflow"})
        evidence.observe({"type": "system", "subtype": "task_updated", "patch": {"status": "failed"}})
        self.assertEqual(evidence.unsettled_background_tasks(), ["<unnamed>"])

    def test_a_completed_task_notification_settles_the_started_task(self):
        # The real 2.1.284 pair for a foreground Bash command that ran a few
        # seconds: no task_updated ever arrives, and the notification ends it.
        evidence = TurnEvidence("session-1", "/tmp/checkout-a")
        evidence.observe({"type": "system", "subtype": "task_started", "task_id": "b67sc680t",
                          "tool_use_id": "toolu_1", "description": "Sleep briefly and print short",
                          "is_backgrounded": False, "task_type": "local_bash"})
        self.assertEqual(evidence.unsettled_background_tasks(), ["b67sc680t"])
        evidence.observe({"type": "system", "subtype": "task_notification", "task_id": "b67sc680t",
                          "tool_use_id": "toolu_1", "status": "completed", "output_file": "",
                          "summary": "Sleep briefly and print short"})
        self.assertEqual(evidence.unsettled_background_tasks(), [])

    def test_a_non_terminal_or_absent_task_notification_leaves_the_task_unsettled(self):
        evidence = TurnEvidence("session-1", "/tmp/checkout-a")
        evidence.observe({"type": "system", "subtype": "task_started", "task_id": "task-note-1"})
        # A notification whose status is outside the exact terminal set must
        # not settle a still-running task.
        evidence.observe({"type": "system", "subtype": "task_notification",
                          "task_id": "task-note-1", "status": "running"})
        self.assertEqual(evidence.unsettled_background_tasks(), ["task-note-1"])
        # A task with no end frame at all stays unsettled.
        evidence.observe({"type": "system", "subtype": "task_started", "task_id": "task-note-2"})
        self.assertEqual(evidence.unsettled_background_tasks(), ["task-note-1", "task-note-2"])

    def test_task_notification_failed_and_stopped_each_settle_the_task(self):
        for status in ("failed", "stopped"):
            with self.subTest(status=status):
                evidence = TurnEvidence("session-1", "/tmp/checkout-a")
                evidence.observe({"type": "system", "subtype": "task_started", "task_id": "task-note-1"})
                evidence.observe({"type": "system", "subtype": "task_notification",
                                  "task_id": "task-note-1", "status": status})
                self.assertEqual(evidence.unsettled_background_tasks(), [])

    def test_a_notification_without_identity_matches_an_update_without_identity(self):
        notified = TurnEvidence("session-1", "/tmp/checkout-a")
        notified.observe({"type": "system", "subtype": "task_started", "task_type": "local_bash"})
        notified.observe({"type": "system", "subtype": "task_notification",
                          "status": "completed", "summary": "Sleep briefly and print short"})
        updated = TurnEvidence("session-1", "/tmp/checkout-a")
        updated.observe({"type": "system", "subtype": "task_started", "task_type": "local_bash"})
        updated.observe({"type": "system", "subtype": "task_updated", "patch": {"status": "completed"}})
        self.assertEqual(notified.unsettled_background_tasks(), ["<unnamed>"])
        self.assertEqual(notified.unsettled_background_tasks(), updated.unsettled_background_tasks())

    def test_background_tasks_beyond_the_old_limit_stay_observable(self):
        evidence = TurnEvidence("session-1", "/tmp/checkout-a")
        for index in range(33):
            evidence.observe({"type": "system", "subtype": "task_started", "task_id": str(index)})
        self.assertEqual(len(evidence.unsettled_background_tasks()), 33)
        evidence.observe({"type": "system", "subtype": "task_notification",
                          "task_id": "32", "status": "completed"})
        self.assertEqual(len(evidence.unsettled_background_tasks()), 32)
        self.assertNotIn("32", evidence.unsettled_background_tasks())

    def test_fallback_usage_keeps_messages_beyond_the_old_limit(self):
        evidence = TurnEvidence("session-1", "/tmp/checkout-a")
        for index in range(257):
            evidence.observe({"type": "assistant", "session_id": "session-1",
                              "message": {"id": f"msg-{index}", "usage": {"input_tokens": 1}}})
        self.assertEqual(len(evidence.assistant_usage), 257)
        self.assertEqual(evidence.assistant_usage["msg-256"], {"input_tokens": 1})


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.process = FakeProcess()
        self.addCleanup(self._close)
        self.connection = Connection(self.process, time.monotonic() + 5, threading.Event())

    def _close(self, process=None):
        process = self.process if process is None else process
        # Give the reader EOF and join it before closing its read descriptor.
        # A concurrent close/read can race with descriptor reuse or hang on Windows.
        process.native_writes.close()
        process.stdin.close()
        self.assertTrue(process.stdout.reader_started.wait(2), "the fake stdout reader did not start")
        process.stdout.reader.join(2)
        self.assertFalse(process.stdout.reader.is_alive(), "the fake stdout reader did not exit")
        process.stdout.close()
        process.native_reads.close()

    def _write_frame(self, frame):
        self.process.native_writes.write((json.dumps(frame, separators=(",", ":")) + "\n").encode())
        self.process.native_writes.flush()

    def test_control_round_trip_and_bounded_request_id(self):
        def respond():
            line = self.process.native_reads.readline()
            frame = json.loads(line)
            self.assertEqual(frame["type"], "control_request")
            self.assertEqual(frame["request"], {"subtype": "initialize"})
            self.assertTrue(0 < len(frame["request_id"]) <= 64)
            self._write_frame({"type": "control_response", "response": {
                "subtype": "success", "request_id": frame["request_id"], "response": {"models": []}}})
        threading.Thread(target=respond, daemon=True).start()
        native_write = os.write
        def fragmented_write(fd, raw):
            return native_write(fd, raw[:7])
        with mock.patch.object(protocol.os, "write", side_effect=fragmented_write):
            self.assertEqual(self.connection.call({"subtype": "initialize"}), {"models": []})

    def test_fragmented_native_frame_waits_for_the_newline(self):
        frames = []
        self.connection.on_message = frames.append
        # Split within a UTF-8 character, then hold back the line delimiter.
        for fragment in (b'{"type":"system","text":"\xe4', b'\xb8\xad"}'):
            self.process.native_writes.write(fragment)
            self.connection.pump()
            self.assertEqual(frames, [])
        self.process.native_writes.write(b'\n')
        while not frames:
            self.connection.pump()
        self.assertEqual(frames, [{"type": "system", "text": "中"}])

    def test_unknown_control_response_identity_is_rejected(self):
        self._write_frame({"type": "control_response", "response": {
            "subtype": "success", "request_id": "hey-my-buddy-999", "response": {}}})
        with self.assertRaises(ClaudeProtocolError) as caught:
            self.connection.pump()
        self.assertEqual(caught.exception.code, "invalid-protocol")

    def test_duplicate_control_response_is_rejected(self):
        def respond_twice():
            line = self.process.native_reads.readline()
            rid = json.loads(line)["request_id"]
            for _ in range(2):
                self._write_frame({"type": "control_response", "response": {
                    "subtype": "success", "request_id": rid, "response": {}}})
        threading.Thread(target=respond_twice, daemon=True).start()
        self.connection.call({"subtype": "initialize"})
        with self.assertRaises(ClaudeProtocolError) as caught:
            self.connection.pump()
        self.assertEqual(caught.exception.code, "invalid-protocol")

    def test_native_can_use_tool_request_reaches_the_controller(self):
        requests = []
        self.connection.on_request = requests.append
        self._write_frame({"type": "control_request", "request_id": "perm-1",
                           "request": {"subtype": "can_use_tool", "tool_name": "Bash",
                                       "input": {"command": "ls"}, "tool_use_id": "toolu_1"}})
        self.connection.pump()
        self.assertEqual(requests[0]["request"]["tool_name"], "Bash")
        self.connection.send({"type": "control_response", "response": {
            "subtype": "success", "request_id": "perm-1",
            "response": {"behavior": "deny", "message": "not approved here"}}})
        reply = json.loads(self.process.native_reads.readline())
        self.assertEqual(reply["response"]["response"]["behavior"], "deny")

    def test_frame_without_a_type_continues_with_bounded_shape_facts(self):
        frames = []
        self.connection.on_message = frames.append
        for value in (None, 7, {"secret": "credential-fragment"}):
            self._write_frame({"type": value, "payload": "credential-fragment"})
            self.connection.pump()
        self.assertEqual(len(frames), 3)
        facts = self.connection.observations()
        self.assertEqual(facts["status"], "incomplete")
        self.assertEqual(facts["unknownEvents"], 3)
        self.assertEqual([item["typeShape"] for item in facts["samples"]], ["NoneType", "int", "dict"])
        self.assertNotIn("credential-fragment", json.dumps(facts))
        for _ in range(20):
            self._write_frame({"payload": 1})
            self.connection.pump()
        self.assertEqual(len(self.connection.observations()["samples"]), 16)
        self.assertTrue(self.connection.observations()["samplesTruncated"])

    def test_unknown_control_response_continues_with_correlated_shape_facts(self):
        for subtype, body in (("future", {}), ("success", []), (None, None)):
            def respond():
                rid = json.loads(self.process.native_reads.readline())["request_id"]
                self._write_frame({"type": "control_response", "response": {
                    "subtype": subtype, "request_id": rid, "response": body,
                    "secret": "credential-fragment"}})
            threading.Thread(target=respond, daemon=True).start()
            self.assertIsNone(self.connection.call({"subtype": "initialize"}))
        facts = self.connection.observations()
        self.assertEqual(facts["status"], "incomplete")
        self.assertEqual(facts["unreadResponses"], 3)
        self.assertEqual([item["requestId"] for item in facts["samples"]], ["hey-my-buddy-1", "hey-my-buddy-2", "hey-my-buddy-3"])
        self.assertNotIn("credential-fragment", json.dumps(facts))

    def test_control_request_identity_continues_past_the_old_budget(self):
        self.connection.next_id = 100000
        def respond():
            frame = json.loads(self.process.native_reads.readline())
            self.assertEqual(frame["request_id"], "hey-my-buddy-100000")
            self._write_frame({"type": "control_response", "response": {
                "subtype": "success", "request_id": frame["request_id"], "response": {"models": []}}})
        threading.Thread(target=respond, daemon=True).start()
        self.assertEqual(self.connection.call({"subtype": "initialize"}), {"models": []})
        self.assertEqual(self.connection.next_id, 100001)

    def test_oversized_native_frame_still_rejects(self):
        # A separate reader sees the small test bound before reading its frame.
        process = FakeProcess()
        self.addCleanup(self._close, process)
        with mock.patch.object(protocol, "MAX_FRAME_BYTES", 64):
            connection = Connection(process, time.monotonic() + 5, threading.Event())
            process.native_writes.write(b'{"payload":"' + b'x' * 65 + b'"}\n')
            with self.assertRaises(ClaudeProtocolError) as caught:
                connection.pump()
            self.assertEqual(caught.exception.code, "invalid-protocol")

    def test_drain_ends_only_on_the_observed_end_of_stream(self):
        self._write_frame({"type": "result", "subtype": "success", "session_id": "s"})
        self.process.native_writes.close()
        self.connection.drain_until_closed(2)
        self.assertTrue(self.connection.stream_closed)
        self.connection.drain_until_closed(0.1)

    def test_drain_without_observed_eof_rejects_the_turn(self):
        self._write_frame({"type": "result", "subtype": "success", "session_id": "s"})
        with self.assertRaises(ClaudeProtocolError) as caught:
            self.connection.drain_until_closed(0.1)
        self.assertEqual(caught.exception.code, "transport-error")

    def test_eof_observed_during_interrupt_is_retained_for_the_final_drain(self):
        self.process.native_writes.close()
        with self.assertRaises(ClaudeProtocolError):
            self.connection.pump()
        self.assertTrue(self.connection.stream_closed)
        self.connection.drain_until_closed(0.1)

    def test_error_control_response_surfaces_as_rpc_error(self):
        def respond_error():
            line = self.process.native_reads.readline()
            rid = json.loads(line)["request_id"]
            self._write_frame({"type": "control_response", "response": {
                "subtype": "error", "request_id": rid, "error": "native detail"}})
        threading.Thread(target=respond_error, daemon=True).start()
        with self.assertRaises(ClaudeProtocolError) as caught:
            self.connection.call({"subtype": "initialize"})
        self.assertEqual(caught.exception.code, "native-rpc-error")
        # The native error text is not copied into the bounded reason.
        self.assertNotIn("native detail", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
