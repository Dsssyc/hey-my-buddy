"""Strict receipt and native provenance checks independent of process fixtures."""
import errno
import json
import unittest
from unittest import mock

from buddy.adapters.base import ProcessHandle
from buddy.adapters.zcode_mcp import respond
from buddy.adapters.zcode_protocol import NativeError, RootTurnEvidence, decode_json, verify_receipt
from buddy.adapters.zcode_runner import catalog, configure_session


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.bridge = {"identity": {"taskId": "goal", "attemptId": "attempt", "generation": 1, "turnId": "logical"},
                       "inputSha256": "a" * 64, "key": "b" * 64}
        self.outcome = {"disposition": "completed", "summary": "done", "remaining": [], "decisions": [], "artifacts": [], "request": None}

    def receipt(self):
        result = respond({"id": 1, "method": "tools/call", "params": {"name": "buddy_finish_turn", "arguments": self.outcome}}, self.bridge)
        self.assertNotIn("structuredContent", result["result"])
        return result["result"]["content"][0]["text"]

    def test_bridge_receipt_is_bound_to_input_identity_and_outcome(self):
        raw = self.receipt()
        self.assertEqual(verify_receipt(raw, self.bridge)["outcome"], self.outcome)
        for key, value in (("inputSha256", "c" * 64), ("outcome", {**self.outcome, "summary": "forged"}),
                           ("identity", {**self.bridge["identity"], "generation": 2})):
            with self.subTest(key=key):
                changed = json.loads(raw)
                changed[key] = value
                with self.assertRaises(NativeError):
                    verify_receipt(json.dumps(changed), self.bridge)
        other_attempt = {**self.bridge, "identity": {**self.bridge["identity"], "attemptId": "other"}}
        with self.assertRaises(NativeError):
            verify_receipt(raw, other_attempt)

    def test_prose_duplicate_members_and_nonfinite_values_are_rejected(self):
        raw = self.receipt()
        for invalid in ("Here is the result: " + raw, "```json\n" + raw + "\n```", raw + raw):
            with self.assertRaises(NativeError):
                verify_receipt(invalid, self.bridge)
        for invalid in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'):
            with self.assertRaises(ValueError):
                decode_json(invalid)

    def test_wrong_input_and_reordered_events_cannot_establish_a_root(self):
        tracker = RootTurnEvidence("sess-root", "input-root", "finish", self.bridge)
        def event(seq, input_id):
            return {"method": "session/event", "params": {"sessionId": "sess-root", "turnId": "native-turn", "seq": seq,
                    "type": "turn.started", "payload": {"inputId": input_id}}}
        with self.assertRaises(NativeError):
            tracker.observe(event(1, "wrong-input"), 1)
        tracker = RootTurnEvidence("sess-root", "input-root", "finish", self.bridge)
        tracker.observe(event(2, "input-root"), 1)
        with self.assertRaises(NativeError):
            tracker.observe(event(1, "input-root"), 2)

    def test_root_relayed_child_finish_does_not_consume_root_receipt(self):
        tracker = RootTurnEvidence("sess-root", "input-root", "finish", self.bridge)
        tracker.observe({"method": "session/event", "params": {"sessionId": "sess-root", "turnId": "native-turn", "seq": 1,
                        "type": "turn.started", "payload": {"inputId": "input-root"}}}, 1)
        tracker.observe({"method": "session/event", "params": {"sessionId": "sess-root", "turnId": "native-turn", "seq": 2,
                        "type": "tool.updated", "payload": {"kind": "scheduled", "toolName": "finish", "toolCallId": "child", "source": "subagent"}}}, 2)
        self.assertIsNone(tracker.call_id)
        self.assertIsNone(tracker.receipt)

    def root_event(self, tracker, seq, kind, payload, **identity):
        tracker.observe({"method": "session/event", "params": {
            "sessionId": "sess-root", "turnId": "native-turn", "seq": seq,
            "type": kind, "payload": payload, **identity,
        }}, seq)

    def started_tracker(self):
        tracker = RootTurnEvidence("sess-root", "input-root", "finish", self.bridge)
        self.root_event(tracker, 1, "turn.started", {"inputId": "input-root"})
        return tracker

    def test_failed_finish_can_retry_with_one_successful_root_receipt(self):
        failures = ({"kind": "error", "error": "The required parameter request is missing"},
                    {"kind": "result", "result": {"success": False, "truncated": False, "content": "invalid outcome"}})
        for failure in failures:
            with self.subTest(kind=failure["kind"]):
                tracker = self.started_tracker()
                self.root_event(tracker, 2, "tool.updated", {"kind": "scheduled", "toolName": "finish", "toolCallId": "bad"})
                self.root_event(tracker, 3, "tool.updated", {"toolCallId": "bad", **failure})
                self.assertIsNone(tracker.receipt)
                self.root_event(tracker, 4, "tool.updated", {"kind": "scheduled", "toolName": "Bash", "toolCallId": "repair"})
                self.root_event(tracker, 5, "tool.updated", {"kind": "scheduled", "toolName": "finish", "toolCallId": "corrected"})
                self.root_event(tracker, 6, "tool.updated", {"kind": "result", "toolCallId": "corrected",
                    "result": {"success": True, "truncated": False, "content": self.receipt()}})
                self.root_event(tracker, 7, "turn.completed", {"inputId": "input-root", "resultType": "success"})
                tracker.observe({"method": "state.updated", "params": {"sessionId": "sess-root", "reason": "prompt_completed"}}, 8)
                tracker.close_ordinal = 9
                provenance = tracker.provenance()
                self.assertEqual(provenance["toolCallId"], "corrected")
                self.assertEqual(provenance["toolCallSeq"], 5)
                self.assertTrue(provenance["receiptVerified"])

    def test_failed_finish_does_not_allow_child_receipts_or_final_prose(self):
        for identity, source in (({"sessionId": "child"}, {}), ({"turnId": "other-turn"}, {}),
                                 ({}, {"source": "subagent"})):
            with self.subTest(identity=identity, source=source):
                tracker = self.started_tracker()
                self.root_event(tracker, 2, "tool.updated", {"kind": "scheduled", "toolName": "finish", "toolCallId": "bad"})
                self.root_event(tracker, 3, "tool.updated", {"kind": "error", "toolCallId": "bad"})
                self.root_event(tracker, 4, "tool.updated", {"kind": "scheduled", "toolName": "finish", "toolCallId": "child", **source}, **identity)
                self.root_event(tracker, 5, "tool.updated", {"kind": "result", "toolCallId": "child", **source,
                    "result": {"success": True, "truncated": False, "content": self.receipt()}}, **identity)
                with self.assertRaises(NativeError) as error:
                    self.root_event(tracker, 6, "turn.completed", {"inputId": "input-root", "resultType": "success", "response": self.receipt()})
                self.assertEqual(error.exception.code, "finish-tool-failed")
                self.assertIsNone(tracker.receipt)

    def test_successful_finish_cannot_be_replaced_by_a_later_error(self):
        tracker = self.started_tracker()
        self.root_event(tracker, 2, "tool.updated", {"kind": "scheduled", "toolName": "finish", "toolCallId": "accepted"})
        self.root_event(tracker, 3, "tool.updated", {"kind": "result", "toolCallId": "accepted",
            "result": {"success": True, "truncated": False, "content": self.receipt()}})
        accepted = tracker.receipt
        with self.assertRaises(NativeError):
            self.root_event(tracker, 4, "tool.updated", {"kind": "error", "toolCallId": "accepted"})
        self.assertIs(tracker.receipt, accepted)
        with self.assertRaises(NativeError):
            self.root_event(tracker, 5, "tool.updated", {"kind": "scheduled", "toolName": "finish", "toolCallId": "duplicate"})


class ConfigurationTests(unittest.TestCase):
    def test_controller_refuses_incomplete_configuration_before_native_setters(self):
        configuration = {"provider": "fixture-api", "model": "fixture-model", "effort": "low"}
        snapshot = {"session": {"sessionId": "native-default"}, "settings": {"model": {"current": {
            "providerId": "fixture-api", "modelId": "fixture-model", "options": {"reasoningLevel": "low"},
        }}}}
        for key in configuration:
            for value in (None, "", " \t", False):
                with self.subTest(key=key, value=value):
                    requested = {**configuration, key: value}
                    if value is None:
                        requested.pop(key)
                    connection = mock.Mock()
                    with self.assertRaises(NativeError) as error:
                        configure_session(connection, snapshot, requested, {"fixture-api": "api-key"})
                    self.assertEqual(error.exception.code, "invalid-configuration")
                    connection.call.assert_not_called()

    def test_catalog_preserves_each_models_real_efforts_and_omits_unconfigurable_models(self):
        def model(identifier, efforts, provider="api"):
            return {"ref": {"providerId": provider, "modelId": identifier},
                    "reasoning": {"levels": [{"value": effort} for effort in efforts]},
                    "properties": {"inputFormat": {"supportsText": True, "supportsImage": identifier == "vision"}}}
        snapshot = {"settings": {"model": {"available": [
            model("vision", ["high", "max"]), model("fast", ["low"]), model("no-reasoning", []),
            model("invalid-efforts", ["", " \t", None]), model("account-only", ["high"], "oauth"),
        ]}}}
        result = catalog(snapshot, {"api": "api-key", "oauth": "zhipu-account"}, "native-test-version")
        self.assertEqual(len(result["providers"]), 1)
        provider = result["providers"][0]
        self.assertEqual(provider["adapter"], "zcode")
        self.assertEqual(provider["packageVersion"], "native-test-version")
        models = {entry["id"]: entry for entry in provider["models"]}
        self.assertEqual(set(models), {"vision", "fast"})
        self.assertEqual(models["vision"]["efforts"], ["high", "max"])
        self.assertEqual(models["fast"]["efforts"], ["low"])
        self.assertEqual(models["vision"]["inputModalities"], ["text", "image"])
        self.assertTrue(all(entry["available"] for entry in models.values()))
        self.assertTrue(any("effort" in warning for warning in result["warnings"]))
        self.assertTrue(any("OAuth" in warning for warning in result["warnings"]))


class OwnedProcessEvidenceTests(unittest.TestCase):
    def test_failed_group_observation_cannot_confirm_shutdown(self):
        process = mock.Mock(pid=12345)
        process.poll.return_value = 0
        with mock.patch("buddy.adapters.base.os.getpgid", return_value=12345):
            handle = ProcessHandle(process, own_group=True, log_paths={})
        for error in (OSError(errno.EIO, "process observation unavailable"), PermissionError()):
            with self.subTest(error=type(error).__name__), mock.patch("buddy.adapters.base.os.killpg", side_effect=error):
                self.assertFalse(handle.shutdown_confirmed(settle_seconds=0))
        with mock.patch("buddy.adapters.base.os.killpg", side_effect=ProcessLookupError()):
            self.assertTrue(handle.shutdown_confirmed(settle_seconds=0))


if __name__ == "__main__":
    unittest.main()
