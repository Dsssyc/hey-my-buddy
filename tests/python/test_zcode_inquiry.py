"""ZCode inquiry, signed replies and native-attention honesty; no model, no network.

The first half drives the controller's bridge directly (its socket protocol, journal
and preemption handling). The second half runs the real adapter, runner and MCP
bridge against the deterministic native fixture, so the confirmed ``v4/command``
guide delivery and the session-private signed answer path are exercised end to end
without a paid call.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import types
import unittest
from pathlib import Path

from test_zcode import ZcodeFixtureCase

from buddy import inquiry as inquiry_module
from buddy.adapters import turn_io
from buddy.adapters.zcode_mcp import JournalError, respond
from buddy.adapters.zcode_protocol import InquiryReplyEvidence, NativeError, verify_inquiry_reply
from buddy.adapters.zcode_runner import InquiryBridge

IDENTITY = {"taskId": "task-1", "attemptId": "attempt-1", "generation": 1, "turnId": "turn-1"}
REPLY_TOOL = "mcp__srv123__buddy_inquiry_reply"


class FakeEvidence:
    def __init__(self):
        self.turn_id = "turn-1"
        self.settled_ordinal = 0


class FakeConnection:
    """Records every native command and returns a confirmed-shaped ack."""

    def __init__(self, delivery: str = "queue"):
        self.delivery = delivery
        self.commands: list[dict] = []

    def send_text(self, envelope: dict) -> dict:
        self.commands.append(envelope)
        if envelope["type"] == "stop":
            return {"commandId": envelope["commandId"], "status": "accepted", "revisionAtDecision": 4}
        return {"commandId": envelope["commandId"], "status": "accepted", "revisionAtDecision": 4,
                "result": {"type": "inputAccepted", "delivery": self.delivery, "inputId": envelope["commandId"]}}


class BridgeHarness:
    def __init__(self, directory: Path, delivery: str = "queue"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.credentials = {
            "socketPath": str(self.directory / "inquiry.sock"),
            "resultsPath": str(self.directory / "inquiry.results.jsonl"),
            "errorPath": str(self.directory / "inquiry.error.json"),
            "token": "a" * 64,
        }
        self.bridge = InquiryBridge(self.credentials, identity=IDENTITY, reply_tool=REPLY_TOOL,
                                    journal_path=self.credentials["resultsPath"])
        self.bridge.start()
        self.bridge.activate("sess-1")
        self.connection = FakeConnection(delivery)
        self.evidence = FakeEvidence()

    def ask(self, inquiry_id: str, question: str = "status?", timeout_ms: int = 3000) -> dict:
        result: dict = {}

        def client():
            result.update(inquiry_module.bridge_request(
                self.credentials, "ask", {"inquiryId": inquiry_id, "question": question}, timeout_ms=timeout_ms))

        thread = threading.Thread(target=client, daemon=True)
        thread.start()
        deadline = time.monotonic() + timeout_ms / 1000
        while thread.is_alive() and time.monotonic() < deadline:
            self.bridge.drain(self.connection, self.evidence)
            time.sleep(0.01)
        thread.join(timeout=2)
        return result

    def records(self) -> list[dict]:
        return [json.loads(line) for line in Path(self.credentials["resultsPath"]).read_text().splitlines() if line.strip()]

    def close(self):
        self.bridge.close()


class BridgeProtocolTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-zcode-inquiry-")
        self.addCleanup(self.temporary.cleanup)
        self.harness = BridgeHarness(Path(self.temporary.name))
        self.addCleanup(self.harness.close)

    def test_observation_is_bounded_and_questions_use_guide_only(self):
        observed = inquiry_module.bridge_request(self.harness.credentials, "observe", {})
        self.assertTrue(observed["ok"], observed)
        value = observed["value"]
        self.assertTrue(value["ready"])
        self.assertEqual(value["agentStatus"], "running")
        self.assertEqual(value["limits"]["requestedDelivery"], "guide")
        self.assertFalse(value["limits"]["startsNewTurn"])
        self.assertFalse(value["limits"]["extendsDeadline"])
        self.assertIn("toolArguments", value["unavailable"])
        self.assertEqual(value["replyTool"]["name"], REPLY_TOOL)

        asked = self.harness.ask("q-1", "what is blocking you?")
        self.assertTrue(asked["ok"], asked)
        self.assertTrue(asked["value"]["accepted"])
        self.assertEqual(asked["value"]["state"], "delivered")
        self.assertEqual(asked["value"]["delivery"]["requestedDelivery"], "guide")
        self.assertEqual(asked["value"]["delivery"]["admittedDelivery"], "queue")
        self.assertEqual(asked["value"]["delivery"]["deliverySemantics"], "in-turn-steer")
        commands = self.harness.connection.commands
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["type"], "sendText")
        self.assertEqual(commands[0]["sessionId"], "sess-1")
        self.assertEqual(commands[0]["payload"]["requestedDelivery"], "guide")
        self.assertIsInstance(commands[0]["issuedAt"], int)
        self.assertIn("q-1", commands[0]["payload"]["text"])
        self.assertIn(REPLY_TOOL, commands[0]["payload"]["text"])
        self.assertNotIn("startNow", json.dumps(commands))
        record = self.harness.records()[-1]
        self.assertEqual(record["state"], "delivered")
        self.assertEqual((record["taskId"], record["attemptId"]), ("task-1", "attempt-1"))

    def test_duplicate_and_conflicting_questions_never_inject_twice(self):
        self.harness.ask("q-2", "same question")
        duplicate = self.harness.ask("q-2", "same question")
        self.assertTrue(duplicate["ok"], duplicate)
        self.assertTrue(duplicate["value"]["duplicate"])
        self.assertEqual(len(self.harness.connection.commands), 1, "a duplicate question must not be injected again")
        conflict = self.harness.ask("q-2", "a different question")
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["code"], "conflict")
        self.assertEqual(len(self.harness.connection.commands), 1)

    def test_question_for_an_ended_turn_is_refused_and_terminalized(self):
        delivered = self.harness.ask("q-3")
        self.assertTrue(delivered["ok"], delivered)
        self.harness.bridge.agent_status = "finishing"
        refused = self.harness.ask("q-4")
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["code"], "agent-gone")
        self.assertEqual(len(self.harness.connection.commands), 1, "an ended turn is never woken for a question")
        self.harness.bridge.close()
        terminal = self.harness.records()[-1]
        self.assertEqual(terminal["inquiryId"], "q-3")
        self.assertEqual(terminal["state"], "unavailable")
        self.assertIn("ended before a correlated answer", terminal["reason"])
        unreachable = inquiry_module.bridge_request(self.harness.credentials, "ask", {"inquiryId": "q-5", "question": "?"})
        self.assertEqual(unreachable["reason"], "bridge-unreachable")

    def test_a_preempted_guide_is_stopped_and_reported_as_attention(self):
        harness = BridgeHarness(Path(self.temporary.name) / "preempt", delivery="startNow")
        self.addCleanup(harness.close)
        asked = harness.ask("q-6", "are you there?")
        self.assertTrue(asked["ok"], asked)
        self.assertTrue(asked["value"]["delivery"]["preempted"])
        self.assertTrue(asked["value"]["delivery"]["remediated"])
        self.assertEqual([command["type"] for command in harness.connection.commands], ["sendText", "stop"])
        self.assertEqual(harness.bridge.attention_report()["requests"], 1)
        record = harness.records()[-1]
        self.assertTrue(record["delivery"]["preempted"])
        self.assertEqual(record["delivery"]["deliverySemantics"], "preempted-or-new-turn")
        second = harness.ask("q-7", "still there?")
        self.assertFalse(second["ok"])
        self.assertEqual(second["code"], "agent-gone")


class SignedReplyTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-zcode-reply-")
        self.addCleanup(self.temporary.cleanup)
        self.journal = Path(self.temporary.name) / "inquiry.results.jsonl"
        self.config = {"identity": IDENTITY, "inputSha256": "a" * 64, "key": "b" * 64, "journalPath": str(self.journal)}

    def delivered(self, inquiry_id="q-1", task="task-1", attempt="attempt-1"):
        self.journal.write_text(json.dumps({"inquiryId": inquiry_id, "state": "delivered", "taskId": task, "attemptId": attempt}) + "\n")

    def call(self, inquiry_id="q-1", answer="still running", config=None):
        result = respond({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": "buddy_inquiry_reply", "arguments": {"inquiryId": inquiry_id, "answer": answer}}},
                         config or self.config)
        if result["result"].get("isError"):
            return None, result["result"]["content"][0]["text"]
        return result["result"]["content"][0]["text"], None

    def test_reply_receipt_binds_identity_and_the_exact_answer(self):
        self.delivered()
        raw, error = self.call()
        self.assertIsNone(error)
        receipt = verify_inquiry_reply(raw, self.config)
        self.assertEqual(receipt["answer"], "still running")
        self.assertEqual(receipt["identity"], IDENTITY)
        for key, value in (("answer", "tampered"), ("inquiryId", "q-other"), ("inputSha256", "c" * 64)):
            with self.subTest(key=key):
                changed = json.loads(raw)
                changed[key] = value
                with self.assertRaises(NativeError):
                    verify_inquiry_reply(json.dumps(changed), self.config)
        with self.assertRaises(NativeError):
            verify_inquiry_reply(raw, {**self.config, "identity": {**IDENTITY, "attemptId": "other"}})

    def test_cross_task_unknown_and_repeated_replies_are_refused(self):
        self.delivered(task="another-task")
        self.assertIn("another task", self.call()[1])
        self.delivered(attempt="another-attempt")
        self.assertIn("another attempt", self.call()[1])
        self.assertIn("unknown inquiry id", self.call(inquiry_id="q-never-asked")[1])
        self.delivered()
        self.journal.write_text(Path(self.journal).read_text() + json.dumps({"inquiryId": "q-1", "state": "answered", "answer": "done"}) + "\n")
        self.assertIn("already has a recorded answer", self.call()[1])
        self.assertIn("nonblank answer", self.call(answer="   ")[1])
        with self.assertRaises(JournalError):
            from buddy.adapters.zcode_mcp import reply_receipt

            reply_receipt("q-1", "another", {**self.config, "journalPath": str(self.journal)})

    def test_only_a_root_tool_call_correlates_a_reply(self):
        self.delivered()
        recorded: list[tuple[dict, str]] = []
        tracker = InquiryReplyEvidence("sess-root", REPLY_TOOL, self.config,
                                       lambda receipt, call_id: recorded.append((receipt, call_id)))
        scheduled = {"method": "session/event", "params": {"sessionId": "sess-root", "turnId": "native-turn", "seq": 1, "type": "tool.updated",
                     "payload": {"kind": "scheduled", "toolName": REPLY_TOOL, "toolCallId": "reply-1"}}}
        result = {"method": "session/event", "params": {"sessionId": "sess-root", "turnId": "native-turn", "seq": 2, "type": "tool.updated",
                  "payload": {"kind": "result", "toolCallId": "reply-1", "result": {"success": True, "truncated": False,
                                                                                  "content": self.call()[0]}}}}
        tracker.observe(scheduled, 1)
        tracker.observe(result, 2)
        self.assertEqual(len(recorded), 1)
        self.assertEqual(tracker.report()["answered"], ["q-1"])
        # A relayed subagent call can never consume or answer the root question.
        child_scheduled = {"method": "session/event", "params": {"sessionId": "sess-root", "turnId": "native-turn", "seq": 3, "type": "tool.updated",
                           "payload": {"kind": "scheduled", "toolName": REPLY_TOOL, "toolCallId": "child-1", "source": "subagent"}}}
        child_result = {"method": "session/event", "params": {"sessionId": "sess-root", "turnId": "native-turn", "seq": 4, "type": "tool.updated",
                        "payload": {"kind": "result", "toolCallId": "child-1", "result": {"success": True, "truncated": False, "content": self.call()[0]}}}}
        tracker.observe(child_scheduled, 3)
        tracker.observe(child_result, 4)
        self.assertEqual(len(recorded), 1)
        # An unmatched id is refused with its own bounded reason.
        self.journal.write_text(json.dumps({"inquiryId": "q-9", "state": "delivered", "taskId": "task-1", "attemptId": "attempt-1"}) + "\n")
        unmatched = InquiryReplyEvidence("sess-root", REPLY_TOOL, self.config, lambda _receipt, _call_id: "no committed question in this attempt")
        unmatched.observe({**scheduled, "params": {**scheduled["params"], "seq": 5}}, 5)
        unmatched.observe({**result, "params": {**result["params"], "seq": 6,
                          "payload": {**result["params"]["payload"], "content": self.call("q-9")[0]}}}, 6)
        self.assertEqual(unmatched.report()["refused"], 1)


class ActivityContractTests(unittest.TestCase):
    def test_activity_document_is_metadata_only_and_uses_the_frozen_helper_api(self):
        document = turn_io.activity_document(version_identity={"taskId": "t", "attemptId": "a", "generation": 3},
                                             payload={"phase": "tool-running", "eventSeq": 7, "toolName": "Bash",
                                                      "counts": {"modelTurns": 1, "toolCalls": 2},
                                                      "prompt": "secret", "toolArguments": {"x": 1}, "reasoning": "hidden"})
        self.assertEqual(document["taskId"], "t")
        self.assertEqual(document["generation"], 3)
        self.assertEqual(document["phase"], "tool-running")
        self.assertEqual(document["counts"], {"modelTurns": 1, "toolCalls": 2})
        for forbidden in ("prompt", "toolArguments", "reasoning", "toolOutput", "credentials"):
            self.assertNotIn(forbidden, document)

        calls: list[tuple[Path, dict]] = []
        stub = types.ModuleType("buddy.activity")

        def write_activity_sidecar(directory, value):
            calls.append((Path(directory), value))
            return Path(directory) / "activity.json"

        stub.write_activity_sidecar = write_activity_sidecar
        previous = sys.modules.get("buddy.activity")
        sys.modules["buddy.activity"] = stub
        try:
            outcome = turn_io.write_activity_sidecar(Path("/tmp/attempt"), document)
        finally:
            if previous is None:
                sys.modules.pop("buddy.activity", None)
            else:
                sys.modules["buddy.activity"] = previous
        self.assertTrue(outcome["written"], outcome)
        self.assertEqual(calls[0][1], document)

    def test_missing_activity_helper_is_reported_honestly(self):
        import importlib.util

        if importlib.util.find_spec("buddy.activity") is not None:
            self.skipTest("the buddy.activity helper is installed; its own suite owns validation")
        outcome = turn_io.write_activity_sidecar(Path("/tmp/attempt"), {"version": 1})
        self.assertFalse(outcome["written"])
        self.assertIn("buddy.activity", outcome["reason"])


class ZcodeInquiryIntegrationTests(ZcodeFixtureCase):
    def credentials(self, context, timeout=15):
        """Wait until the attempt mounted its bridge AND admitted the root turn.

        Asking before admission is refused with ``not-ready`` (honest), so the test
        waits for the same readiness a real client would observe before asking.
        """
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

    def records(self, credentials):
        path = Path(credentials["resultsPath"])
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def test_live_question_is_delivered_by_guide_and_answered_by_the_signed_tool(self):
        context = self.context("inquiry", timeout=30)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        credentials = self.credentials(context)
        asked = inquiry_module.bridge_request(credentials, "ask", {"inquiryId": "q-live", "question": "what is the status?"}, timeout_ms=4000)
        self.assertTrue(asked["ok"], asked)
        self.assertTrue(asked["value"]["accepted"])
        self.assertIsNotNone(handle.wait(30), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        commands = [json.loads(line) for line in (context.directory / "native-logs" / "commands.jsonl").read_text().splitlines()]
        self.assertEqual([command["type"] for command in commands], ["sendText"])
        self.assertEqual(commands[0]["payload"]["requestedDelivery"], "guide")
        self.assertIn("q-live", commands[0]["payload"]["text"])
        methods = (context.directory / "native-logs" / "methods.jsonl").read_text().split()
        self.assertEqual(methods.count("session/send"), 1, "an inquiry must never admit another root input")
        self.assertEqual(methods.count("v4/command"), 1)
        records = self.records(credentials)
        answered = [record for record in records if record["state"] == "answered"]
        self.assertEqual(len(answered), 1, records)
        self.assertEqual(answered[0]["inquiryId"], "q-live")
        self.assertEqual(answered[0]["answer"], "fixture answer: still running")
        self.assertEqual(answered[0]["via"], "tool:buddy_inquiry_reply")
        self.assertEqual(answered[0]["toolCallId"], "reply-call-1")
        self.assertEqual((answered[0]["taskId"], answered[0]["attemptId"]), ("goal-1", "attempt-1"))
        self.assertEqual(outcome.result["inquiry"]["answered"], 1)
        self.assertEqual(outcome.result["nativeSession"]["sessionId"], outcome.result["turn"]["sessionId"])

    def test_the_question_socket_is_gone_after_a_settled_turn(self):
        context = self.context(timeout=20)
        handle = self.adapter.start(context)
        credentials = self.credentials(context)
        self.assertIsNotNone(handle.wait(20), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertFalse(Path(credentials["socketPath"]).exists(), "the bridge socket must not outlive the turn")
        late = inquiry_module.bridge_request(credentials, "ask", {"inquiryId": "q-late", "question": "still there?"})
        self.assertFalse(late["ok"])
        self.assertEqual(late["reason"], "bridge-unreachable")

    def test_a_preempted_guide_is_stopped_and_reported_as_attention(self):
        context = self.context("inquiry-preempt", timeout=30)
        handle = self.adapter.start(context)
        credentials = self.credentials(context)
        asked = inquiry_module.bridge_request(credentials, "ask", {"inquiryId": "q-preempt", "question": "status?"}, timeout_ms=4000)
        self.assertTrue(asked["ok"], asked)
        self.assertTrue(asked["value"]["delivery"]["preempted"])
        self.assertIsNotNone(handle.wait(30), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        commands = [json.loads(line) for line in (context.directory / "native-logs" / "commands.jsonl").read_text().splitlines()]
        self.assertEqual([command["type"] for command in commands], ["sendText", "stop"])
        record = [item for item in self.records(credentials) if item["inquiryId"] == "q-preempt" and item["state"] == "delivered"][-1]
        self.assertTrue(record["delivery"]["preempted"])
        self.assertGreaterEqual(outcome.result["inquiry"]["preempted"], 1)
        self.assertGreaterEqual(outcome.result["attention"]["requests"], 1)

    def test_native_interactive_requests_become_structured_attention(self):
        context = self.context("attention", timeout=30)
        _, outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        responses = json.loads((context.directory / "native-logs" / "interaction-responses.json").read_text())
        self.assertEqual(responses["permission"]["result"], {"decision": "deny", "reason": "No interactive Host is attached to this governed Buddy turn; report attention in the turn outcome instead."})
        self.assertEqual(responses["userInput"]["result"]["action"], "decline")
        self.assertEqual(responses["unknown"]["error"]["code"], -32601)
        self.assertEqual(outcome.result["attention"]["requests"], 3)
        self.assertEqual(outcome.result["attention"]["last"]["method"], "interaction/browserExecute")
        self.assertTrue(any(item["kind"] == "native-attention" for item in outcome.artifacts), outcome.artifacts)

    def test_runner_reports_the_activity_contract_without_a_helper_installed(self):
        context = self.context(timeout=20)
        _, outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        activity = outcome.result["activity"]
        self.assertEqual(activity["contract"]["module"], "buddy.activity")
        self.assertEqual(activity["contract"]["function"], "write_activity_sidecar")
        self.assertEqual(activity["contract"]["version"], 1)
        self.assertIn(activity["phase"], {"starting", "waiting-model", "streaming-model", "tool-running", "finishing"})
        if not activity["published"]:
            self.assertIn("buddy.activity", activity["reason"])
        self.assertNotIn("prompt", json.dumps(activity))


if __name__ == "__main__":
    unittest.main()
