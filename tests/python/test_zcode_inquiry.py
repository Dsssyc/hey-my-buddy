"""ZCode observation-only inquiry, native attention and the real activity sidecar.

The first half drives the controller's private bridge directly: it proves that a
question is recorded as an honest, attempt-bound refusal and that no code path can
send a native command. The second half runs the real adapter, runner and MCP bridge
against the deterministic native fixture, so the refused native interactive
requests, the attention-only finish tool and the ``buddy.activity`` sidecar are
exercised end to end without a paid call.
"""
from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

from test_zcode import ZcodeFixtureCase

from buddy import activity as activity_module
from buddy import inquiry as inquiry_module
from buddy.adapters import turn_io
from buddy.adapters.zcode_mcp import attention_requests, respond
from buddy.adapters.zcode_protocol import NATIVE_INQUIRY_UNSUPPORTED, NativeError, verify_receipt
from buddy.adapters.zcode_runner import InquiryBridge

IDENTITY = {"taskId": "task-1", "attemptId": "attempt-1", "generation": 1, "turnId": "turn-1"}


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


class BridgeRefusalTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-zcode-inquiry-")
        self.addCleanup(self.temporary.cleanup)
        self.harness = BridgeHarness(Path(self.temporary.name))
        self.addCleanup(self.harness.close)

    def test_observation_is_bounded_and_reports_the_native_limitation(self):
        observed = self.harness.request("observe")
        self.assertTrue(observed["ok"], observed)
        value = observed["value"]
        self.assertTrue(value["ready"])
        self.assertEqual(value["agentStatus"], "running")
        self.assertEqual(value["capability"], "observe")
        self.assertFalse(value["supported"])
        self.assertIsNone(value["replyTool"])
        self.assertEqual(value["limits"]["inquiry"], "unsupported")
        self.assertIsNone(value["limits"]["requestedDelivery"])
        self.assertFalse(value["limits"]["startsNewTurn"])
        self.assertFalse(value["limits"]["extendsDeadline"])
        self.assertIn("no turn-bound in-turn input", value["limitation"])
        self.assertIn("toolArguments", value["unavailable"])

    def test_a_question_is_refused_with_one_committed_identity_and_no_native_command(self):
        asked = self.harness.request("ask", inquiryId="q-1", question="what is blocking you?")
        self.assertTrue(asked["ok"], asked)
        value = asked["value"]
        self.assertFalse(value["accepted"])
        self.assertFalse(value["supported"])
        self.assertEqual(value["state"], "unavailable")
        self.assertFalse(value["duplicate"])
        self.assertNotIn("startNow", json.dumps(value))
        self.assertIn("no turn-bound in-turn input", value["reason"])
        self.assertEqual(value["delivery"]["startsNewTurn"], False)
        records = self.harness.records()
        self.assertEqual(len(records), 1, records)
        self.assertEqual(records[0]["state"], "unavailable")
        self.assertEqual(records[0]["questionSha256"], value["questionSha256"])
        self.assertEqual((records[0]["taskId"], records[0]["attemptId"]), ("task-1", "attempt-1"))

        # Repeating the identical question returns the same committed state as a
        # duplicate and appends nothing; a changed question under the same id is a
        # conflict. No retry can ever inject anything because no injection exists.
        duplicate = self.harness.request("ask", inquiryId="q-1", question="what is blocking you?")
        self.assertTrue(duplicate["ok"])
        self.assertTrue(duplicate["value"]["duplicate"])
        self.assertEqual(duplicate["value"]["questionSha256"], value["questionSha256"])
        self.assertEqual(duplicate["value"]["delivery"], value["delivery"])
        self.assertEqual(len(self.harness.records()), 1)
        conflict = self.harness.request("ask", inquiryId="q-1", question="a different question")
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["code"], "conflict")
        self.assertEqual(len(self.harness.records()), 1)

    def test_the_bridge_has_no_native_injection_path_at_all(self):
        # The proof for "a duplicate never injects again" is stronger than a
        # counter: this class holds no native connection and exposes no drain,
        # inject or reply-recording API, and the connection has no send_text.
        self.assertFalse(hasattr(self.harness.bridge, "drain"))
        self.assertFalse(hasattr(self.harness.bridge, "record_answer"))
        self.assertFalse(hasattr(self.harness.bridge, "_inject"))
        from buddy.adapters.zcode_protocol import NativeConnection

        self.assertFalse(hasattr(NativeConnection, "send_text"))
        self.assertNotIn("commandId", json.dumps(self.harness.records()))

    def test_the_journal_merges_identity_hash_and_delivery_across_records_and_restarts(self):
        self.harness.request("ask", inquiryId="q-merge", question="status?")
        committed = self.harness.bridge.entries["q-merge"]
        self.harness.bridge._journal({**self.harness.bridge._identity_fields(), "inquiryId": "q-merge",
                                      "state": "unavailable", "reason": "a later bounded record"})
        merged = self.harness.bridge.entries["q-merge"]
        self.assertEqual(merged["questionSha256"], committed["questionSha256"])
        self.assertEqual(merged["delivery"], committed["delivery"])
        self.assertEqual(merged["reason"], "a later bounded record")
        # A controller restart replays the journal and must keep the same
        # committed identity, so the same question is still a duplicate.
        restarted = InquiryBridge(self.harness.credentials, identity=IDENTITY,
                                  journal_path=self.harness.credentials["resultsPath"])
        restarted._load_journal()
        self.assertEqual(restarted.entries["q-merge"]["questionSha256"], committed["questionSha256"])
        self.assertEqual(restarted.entries["q-merge"]["delivery"], committed["delivery"])

    def test_questions_are_refused_before_the_root_turn_is_admitted(self):
        harness = BridgeHarness(Path(self.temporary.name) / "not-ready")
        harness.bridge.active = False
        self.addCleanup(harness.close)
        asked = harness.request("ask", inquiryId="q-early", question="status?")
        self.assertTrue(asked["ok"], asked)
        self.assertFalse(asked["value"]["accepted"], "an unadmitted turn still cannot receive a question")
        self.assertEqual(asked["value"]["state"], "unavailable")

    def test_attention_is_bounded_and_readable_while_the_turn_is_live(self):
        for index in range(10):
            self.harness.bridge.note_attention({"kind": "unsupported-native-request", "method": f"interaction/{index}",
                                                "at": "2026-01-01T00:00:00Z", "outcome": "refused-with-jsonrpc-error"})
        self.assertEqual(self.harness.bridge.attention_report()["requests"], 8)
        written = json.loads(self.harness.attention_path.read_text())
        self.assertEqual(written["requests"][-1]["method"], "interaction/9")
        self.assertEqual(written["attemptId"], "attempt-1")


class FinishToolTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-zcode-finish-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.attention = self.root / "attention.json"
        self.config = {"identity": IDENTITY, "inputSha256": "a" * 64, "key": "b" * 64,
                       "attentionPath": str(self.attention)}

    def call(self, name: str, arguments: dict) -> dict:
        return respond({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": name, "arguments": arguments}}, self.config)["result"]

    @staticmethod
    def outcome(disposition: str) -> dict:
        request = None if disposition == "completed" else {
            "summary": "help", "attempted": "tried", "neededWork": "decide",
            "expectedArtifacts": [], "acceptance": "decided"}
        return {"disposition": disposition, "summary": "fixture", "remaining": [], "decisions": [],
                "artifacts": [], "request": request}

    def test_only_the_finish_tool_is_exposed(self):
        listed = respond({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, self.config)
        names = [tool["name"] for tool in listed["result"]["tools"]]
        self.assertEqual(names, ["buddy_finish_turn"])
        self.assertNotIn("buddy_inquiry_reply", json.dumps(listed))

    def test_completed_is_refused_while_a_native_request_is_unresolved(self):
        self.assertEqual(attention_requests(self.config), 0)
        self.attention.write_text(json.dumps({"version": 1, "requests": [
            {"kind": "unsupported-native-request", "method": "interaction/requestPermission"}]}))
        self.assertEqual(attention_requests(self.config), 1)
        refused = self.call("buddy_finish_turn", self.outcome("completed"))
        self.assertTrue(refused["isError"], refused)
        self.assertIn("attention", refused["content"][0]["text"])
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
        self.assertTrue(invalid["isError"])


class ActivitySidecarTests(ZcodeFixtureCase):
    def test_the_runner_publishes_a_real_bound_metadata_only_sidecar(self):
        context = self.context(timeout=20)
        _, outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        activity = outcome.result["activity"]
        self.assertTrue(activity["published"], activity)
        self.assertIn(activity["phase"], activity_module.PHASES)

        path = activity_module.sidecar_path(context.directory)
        self.assertTrue(path.is_file(), "the runner did not publish activity.json")
        payload = activity_module.read_sidecar(path, task_id="goal-1", attempt_id="attempt-1", generation=1)
        self.assertIsNotNone(payload, "the emitted sidecar must satisfy the real buddy.activity reader")
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
        self.assertIsNone(activity_module.read_sidecar(path, task_id="goal-1", attempt_id="attempt-1", generation=2))

    def test_same_phase_updates_are_throttled_but_recorded_phase_changes_are_written(self):
        context = self.context(timeout=20)
        # The projection is what the controller publishes; the helper coalesces
        # same-phase receipts inside its window and always writes a phase change.
        from buddy.adapters.zcode_protocol import ActivityProjection

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
        path = context.directory / "inquiry.json"
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
        path = context.directory / "native-logs" / name
        return path.read_text() if path.exists() else ""

    def test_a_live_question_is_refused_without_touching_the_native_session(self):
        context = self.context("live", timeout=30)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        credentials = self.credentials(context)
        asked = inquiry_module.bridge_request(credentials, "ask", {"inquiryId": "q-live", "question": "what is the status?"}, timeout_ms=4000)
        self.assertTrue(asked["ok"], asked)
        self.assertFalse(asked["value"]["accepted"])
        self.assertEqual(asked["value"]["state"], "unavailable")
        duplicate = inquiry_module.bridge_request(credentials, "ask", {"inquiryId": "q-live", "question": "what is the status?"}, timeout_ms=4000)
        self.assertTrue(duplicate["value"]["duplicate"])
        (context.directory / "native-logs").mkdir(exist_ok=True)
        (context.directory / "native-logs" / "release-turn").touch()
        self.assertIsNotNone(handle.wait(30), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        # No client command was ever sent, and the refused question is bound to the
        # attempt with a single committed record.
        self.assertEqual(self.native_log(context, "commands.jsonl"), "")
        self.assertNotIn("v4/command", self.native_log(context, "methods.jsonl"))
        self.assertEqual(self.native_log(context, "methods.jsonl").split().count("session/send"), 1)
        records = [record for record in self.records(credentials) if record["inquiryId"] == "q-live"]
        self.assertEqual(len(records), 1, records)
        self.assertEqual(records[0]["state"], "unavailable")
        self.assertEqual(records[0]["questionSha256"], asked["value"]["questionSha256"])
        self.assertFalse(records[0]["delivery"]["startsNewTurn"])
        self.assertEqual(outcome.result["inquiry"]["supported"], False)
        self.assertEqual(outcome.result["inquiry"]["refused"], 1)
        self.assertIn("no turn-bound in-turn input", outcome.result["inquiry"]["limitation"])
        self.assertEqual(outcome.result["nativeAttention"]["requests"], 0)

    def test_the_question_socket_is_gone_after_a_settled_turn(self):
        context = self.context("live", timeout=30)
        handle = self.adapter.start(context)
        credentials = self.credentials(context)
        (context.directory / "native-logs").mkdir(exist_ok=True)
        (context.directory / "native-logs" / "release-turn").touch()
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
        responses = json.loads((context.directory / "native-logs" / "interaction-responses.json").read_text())
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

    def test_observation_has_a_limitation_and_no_reply_tool_in_the_prompt(self):
        context = self.context(timeout=20)
        _, outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        tree = json.loads((context.directory / "finish-bridge.json").read_text())
        self.assertIn("attentionPath", tree)
        self.assertNotIn("journalPath", tree)
        self.assertIn(NATIVE_INQUIRY_UNSUPPORTED, outcome.result["inquiry"]["limitation"])
        self.assertNotIn("buddy_inquiry_reply", (context.directory / "finish-bridge.json").read_text())


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
