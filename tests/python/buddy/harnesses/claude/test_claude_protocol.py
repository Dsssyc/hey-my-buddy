"""Claude stream-json protocol unit tests over pipes; no native process is started."""
from __future__ import annotations

import json
import os
import threading
import time
import unittest

from hey_my_buddy.buddy.harnesses.claude.protocol import (
    ClaudeProtocolError,
    Connection,
    OUTCOME_SCHEMA,
    QuotaRejected,
    TurnEvidence,
    decode_json,
    model_usage_keys,
    parse_structured_output,
    rate_limit_observation,
    result_quota_denial,
)


class FakeProcess:
    """A process-shaped object over two pipes: the controller side reads stdout and writes stdin."""

    def __init__(self):
        stdin_read, stdin_write = os.pipe()
        stdout_read, stdout_write = os.pipe()
        self.stdin = os.fdopen(stdin_write, "wb", buffering=0)
        self.stdout = os.fdopen(stdout_read, "rb", buffering=0)
        self.native_reads = os.fdopen(stdin_read, "rb", buffering=0)
        self.native_writes = os.fdopen(stdout_write, "wb", buffering=0)


class DecodeTests(unittest.TestCase):
    def test_duplicate_members_and_non_finite_numbers_are_rejected(self):
        with self.assertRaises(ValueError):
            decode_json('{"type":"result","type":"result"}')
        with self.assertRaises(ValueError):
            decode_json('{"cost":NaN}')
        with self.assertRaises(ValueError):
            decode_json('{"cost":Infinity}')
        with self.assertRaises(ValueError):
            decode_json('{"cost":1e999}')
        self.assertEqual(decode_json('{"a":1}'), {"a": 1})


class OutcomeSchemaTests(unittest.TestCase):
    def test_schema_is_a_nested_union_with_tagged_branches(self):
        branches = OUTCOME_SCHEMA["properties"]["outcome"]["anyOf"]
        self.assertEqual(len(branches), 2)
        completed, requesting = branches
        self.assertEqual(completed["properties"]["disposition"]["enum"], ["completed"])
        self.assertEqual(completed["properties"]["request"], {"type": "null"})
        self.assertEqual(requesting["properties"]["disposition"]["enum"], ["assistance", "attention"])
        self.assertEqual(sorted(requesting["properties"]["request"]["required"]),
                         ["acceptance", "attempted", "expectedArtifacts", "neededWork", "summary"])
        self.assertIn("suggestedProfileId", requesting["properties"]["request"]["properties"])

    def test_parse_structured_output_accepts_and_rejects_strictly(self):
        completed = {"disposition": "completed", "summary": "done", "remaining": [], "decisions": [],
                     "artifacts": [], "request": None}
        self.assertEqual(parse_structured_output({"outcome": completed}), completed)
        request = {"summary": "need help", "attempted": "tried", "neededWork": "more",
                   "expectedArtifacts": [], "acceptance": "verified"}
        for suggested in ("dsh-flash", None):
            assistance = {"disposition": "assistance", "summary": "stuck", "remaining": [], "decisions": [],
                          "artifacts": [], "request": {**request, "suggestedProfileId": suggested}}
            self.assertEqual(parse_structured_output({"outcome": assistance})["request"]["suggestedProfileId"],
                             suggested)
        with self.assertRaises(ValueError):
            parse_structured_output({"outcome": completed, "extra": 1})
        with self.assertRaises(ValueError):
            parse_structured_output({"outcome": {**completed, "request": {"summary": "late request"}}})
        with self.assertRaises(ValueError):
            parse_structured_output({"outcome": "not-an-object"})
        with self.assertRaises(ValueError):
            parse_structured_output("not-an-object")


class QuotaParsingTests(unittest.TestCase):
    def test_rate_limit_observation_is_bounded_and_typed(self):
        frame = {"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed_warning", "rateLimitType": "seven_day", "resetsAt": "2026-09-27T00:00:00Z",
            "utilization": {"sevenDayPctUsed": 43}}}
        rate_limit_type, observation = rate_limit_observation(frame)
        self.assertEqual(rate_limit_type, "seven_day")
        self.assertEqual(observation, {"status": "allowed_warning", "resetsAt": "2026-09-27T00:00:00Z",
                                       "utilization": {"sevenDayPctUsed": 43}})
        number = {"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "rateLimitType": "five_hour", "utilization": 12.5}}
        self.assertEqual(rate_limit_observation(number)[1]["utilization"], 12.5)
        # Utilization stays a finite numeric observation, never arbitrary JSON.
        prose = {"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "rateLimitType": "five_hour", "utilization": "about half full"}}
        self.assertNotIn("utilization", rate_limit_observation(prose)[1])
        mixed = {"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "rateLimitType": "five_hour", "utilization": {"note": "text", "pct": 10}}}
        self.assertNotIn("utilization", rate_limit_observation(mixed)[1])
        self.assertIsNone(rate_limit_observation({"type": "rate_limit_event"}))
        self.assertIsNone(rate_limit_observation(
            {"rate_limit_info": {"status": "mystery", "rateLimitType": "five_hour"}}))

    def test_result_quota_denial_uses_structured_fields_only(self):
        self.assertTrue(result_quota_denial({"api_error_status": 429}))
        self.assertTrue(result_quota_denial({"api_error_status": "429"}))
        self.assertTrue(result_quota_denial({"subtype": "error_usage_limit_reached"}))
        # Arbitrary prose in free-text fields never infers exhaustion.
        self.assertFalse(result_quota_denial({"subtype": "error_during_execution",
                                              "result": "quota exhausted according to the message"}))
        self.assertFalse(result_quota_denial({}))

    def test_model_usage_keys_form_a_bounded_observed_set(self):
        result = {"modelUsage": {"claude-opus-5": {}, "claude-haiku-4-5": {}, "zzz-other": {}}}
        self.assertEqual(model_usage_keys(result), ["claude-haiku-4-5", "claude-opus-5", "zzz-other"])
        self.assertEqual(len(model_usage_keys({"modelUsage": {str(i): {} for i in range(40)}})), 16)
        self.assertEqual(model_usage_keys({"modelUsage": {"x" * 129: {}}}), [])
        self.assertEqual(model_usage_keys({}), [])


class TurnEvidenceTests(unittest.TestCase):
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
        with self.assertRaises(QuotaRejected) as caught:
            evidence.observe({"type": "rate_limit_event", "rate_limit_info": {
                "status": "rejected", "rateLimitType": "five_hour", "resetsAt": "2026-09-26T12:00:00Z"}})
        self.assertEqual(caught.exception.rate_limit_type, "five_hour")
        self.assertEqual(caught.exception.resets_at, "2026-09-26T12:00:00Z")

    def test_rejected_rate_limit_fails_even_with_a_malformed_identity(self):
        with self.assertRaises(QuotaRejected) as caught:
            TurnEvidence("session-1", "/tmp/checkout-a").observe(
                {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}})
        self.assertEqual(caught.exception.rate_limit_type, "unknown")
        self.assertIsNone(caught.exception.resets_at)
        with self.assertRaises(QuotaRejected) as malformed:
            TurnEvidence("session-1", "/tmp/checkout-a").observe(
                {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected",
                                                                 "rateLimitType": {"bad": "type"}}})
        self.assertEqual(malformed.exception.rate_limit_type, "unknown")

    def test_a_free_text_rate_limit_identity_is_never_kept_as_a_fact(self):
        # A rate-limit identity is an identifier, never free text: whitespace,
        # control characters or credential-like fragments are dropped or
        # replaced with "unknown" instead of being published.
        for bad in ("five hour", "Bearer sk-ant-api03-fragment\n", "primary?x=1", "x" * 65):
            with self.subTest(bad=bad):
                self.assertIsNone(rate_limit_observation(
                    {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "rateLimitType": bad}}))
                with self.assertRaises(QuotaRejected) as caught:
                    TurnEvidence("session-1", "/tmp/checkout-a").observe(
                        {"type": "rate_limit_event",
                         "rate_limit_info": {"status": "rejected", "rateLimitType": bad}})
                self.assertEqual(caught.exception.rate_limit_type, "unknown")
        evidence = TurnEvidence("session-1", "/tmp/checkout-a")
        evidence.observe({"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "rateLimitType": "five_hour", "utilization": 12.5}})
        self.assertEqual(list(evidence.rate_limits), ["five_hour"])

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

    def test_background_overflow_cannot_hide_an_unsettled_task(self):
        evidence = TurnEvidence("session-1", "/tmp/checkout-a")
        for index in range(32):
            evidence.observe({"type": "system", "subtype": "task_started", "task_id": str(index)})
        with self.assertRaises(ClaudeProtocolError):
            evidence.observe({"type": "system", "subtype": "task_started", "task_id": "overflow"})


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.process = FakeProcess()
        self.addCleanup(self._close)
        self.connection = Connection(self.process, time.monotonic() + 5, threading.Event())

    def _close(self):
        for stream in (self.process.stdin, self.process.stdout, self.process.native_reads, self.process.native_writes):
            try:
                stream.close()
            except OSError:
                pass

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
        self.assertEqual(self.connection.call({"subtype": "initialize"}), {"models": []})

    def test_unknown_control_response_identity_is_rejected(self):
        self._write_frame({"type": "control_response", "response": {
            "subtype": "success", "request_id": "buddy-999", "response": {}}})
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

    def test_frame_without_a_type_is_rejected(self):
        self._write_frame({"payload": 1})
        with self.assertRaises(ClaudeProtocolError):
            self.connection.pump()

    def test_drain_ends_only_on_the_observed_end_of_stream(self):
        self._write_frame({"type": "result", "subtype": "success", "session_id": "s"})
        self.process.native_writes.close()
        self.connection.drain_until_closed(2)
        # Without the end-of-stream sentinel the bounded cap rejects the turn.
        with self.assertRaises(ClaudeProtocolError) as caught:
            self.connection.drain_until_closed(0.1)
        self.assertEqual(caught.exception.code, "transport-error")

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
