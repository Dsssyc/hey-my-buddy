"""Codex transport deadline boundary checks; no model network or shared state is used."""
import json
import math
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from hey_my_buddy.buddy.harnesses.codex.native_run import _checkpoint, execution_deadline
from hey_my_buddy.buddy.harnesses.codex.protocol import (
    CodexProtocolError,
    Connection,
    TurnEvidence,
    attempt_token_usage,
)
from hey_my_buddy.buddy.harnesses.registry import worker_format
from hey_my_buddy.buddy.harnesses.run_contract import (
    PrivateStatePaths,
    RunBudget,
    RunConfiguration,
    RunIdentity,
    RunRequest,
    RunValue,
)
from hey_my_buddy.buddy.roles.turn_io import checkpoint_resumable, input_hash, validated_checkpoint
from hey_my_buddy.errors import BoardError


class StructuredOutcomeTests(unittest.TestCase):
    """The native-schema outcome decoding the registered Worker format applies."""

    format = worker_format("codex")

    def deliver(self, raw: str):
        result = SimpleNamespace(value=RunValue(schema_status="unknown",
                                                raw=raw, correction_count=0))
        return self.format.delivery(result, {})[0]

    def outcome(self, summary):
        return {"outcome": {"disposition": "completed", "summary": summary,
                            "remaining": [], "decisions": [], "artifacts": [], "request": None}}

    def test_long_utf8_report_with_memory_citation_is_preserved(self):
        # The reported incident was valid JSON: 8,848 UTF-8 bytes in summary,
        # with its citation inside the string, not after the JSON document.
        citation = "\n<oai-mem-citation>\n<citation_entries>\nMEMORY.md:1-2|note=[context]\n</citation_entries>\n<rollout_ids>\n</rollout_ids>\n</oai-mem-citation>"
        summary = "调研结论。" * 600 + citation
        self.assertGreater(len(summary.encode()), 8000)
        value = self.outcome(summary)
        self.assertEqual(self.deliver(json.dumps(value, ensure_ascii=False)), value["outcome"])

    def test_total_outcome_bound_still_rejects_large_reports(self):
        with self.assertRaises(BoardError) as raised:
            self.deliver(json.dumps(self.outcome("研" * 22000), ensure_ascii=False))
        self.assertIn("byte bound", raised.exception.message)

    def test_memory_markup_does_not_relax_the_json_contract(self):
        value = json.dumps(self.outcome("valid summary"))
        for raw in (value + "<oai-mem-citation>extra</oai-mem-citation>",
                    value.replace('"summary":', '"summary":"duplicate", "summary":'),
                    value.replace('"request": null', '"request": {}')):
            with self.subTest(raw=raw), self.assertRaises(BoardError):
                self.deliver(raw)


class NativeCheckpointTests(unittest.TestCase):
    document = {"taskId": "goal", "attemptId": "attempt", "generation": 1, "turnId": "turn"}

    def request(self) -> RunRequest:
        return RunRequest(
            identity=RunIdentity(task_id="goal", attempt_id="attempt", generation=1,
                                 invocation_id="invocation", turn_id="turn",
                                 input_sha256=input_hash(self.document)),
            harness="codex", configuration=RunConfiguration(provider="openai", model="fixture-model", effort="low"),
            cwd="/fixture", private_state=PrivateStatePaths(invocation_root="/fixture/invocation",
                                                            native_root="/fixture/native"),
            input_text="prompt", tool_scope="write", output_schema={},
            budget=RunBudget(timeout_seconds=8))

    def test_failed_turn_can_retain_only_its_own_completed_assistant_message(self):
        evidence = TurnEvidence("root", "turn")
        evidence.observe({"method": "turn/started", "params": {"threadId": "root", "turn": {"id": "turn"}}})
        for thread, text in (("root", "partial research"), ("child", "unrelated child report")):
            evidence.observe({"method": "item/completed", "params": {"threadId": thread, "turnId": "turn",
                "item": {"id": "item-1", "type": "agentMessage", "phase": "commentary", "text": text}}})
        evidence.observe({"method": "turn/completed", "params": {"threadId": "root", "turn": {"id": "turn", "status": "failed"}}})
        checkpoint = _checkpoint(self.request(), evidence)
        self.assertEqual(checkpoint["lastAssistantMessage"]["text"], "partial research")
        self.assertIsNone(evidence.final_item)

    def test_message_is_bounded_and_truncation_is_honest(self):
        evidence = SimpleNamespace(thread_id="thread", turn_id="native-turn", started=True,
                                   completed={"status": "completed"}, event_seq=3,
                                   final_item={"id": "final", "text": "调研\n" * 15000})
        checkpoint = _checkpoint(self.request(), evidence)
        self.assertTrue(checkpoint["lastAssistantMessage"]["truncated"])
        payload = {"nativeCheckpoint": checkpoint, "sessionId": "thread", "nativeTurnId": "native-turn",
                   "processState": {"shutdownConfirmed": True, "nativeExitCode": 0}}
        self.assertEqual(validated_checkpoint(payload, self.document), checkpoint)
        self.assertFalse(checkpoint_resumable(payload, checkpoint))
        checkpoint["bindingSaved"] = True
        self.assertTrue(checkpoint_resumable(payload, checkpoint))
        for change in ({"inputSha256": "foreign"}, {"sessionId": "foreign"}, {"nativeTurnStarted": False},
                       {"generation": True}, {"eventSeq": 1}):
            with self.subTest(change=change):
                self.assertIsNone(validated_checkpoint({**payload, "nativeCheckpoint": {**checkpoint, **change}}, self.document))
        self.assertIsNone(validated_checkpoint({**payload, "processState": None}, self.document))
        self.assertFalse(checkpoint_resumable({**payload, "processState": {"nativeExitCode": False}}, checkpoint))


class UnlimitedDeadlineTests(unittest.TestCase):
    def test_zero_timeout_maps_to_an_infinite_overall_deadline_only(self):
        self.assertEqual(execution_deadline(0), math.inf)
        before = time.monotonic()
        deadline = execution_deadline(45)
        self.assertLessEqual(before + 44, deadline)
        self.assertLessEqual(deadline, time.monotonic() + 45)

    def test_infinite_deadline_keeps_each_wait_bounded_and_cancellation_honored(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE)

        def stop():
            process.kill()
            process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()

        self.addCleanup(stop)
        connection = Connection(process, math.inf, threading.Event())
        started = time.monotonic()
        connection.pump()  # no message arrives: the bounded per-pump wait must still return
        self.assertLess(time.monotonic() - started, 5.0)
        connection.cancelled.set()
        with self.assertRaises(CodexProtocolError) as error:
            connection.pump()
        self.assertEqual(error.exception.code, "user-cancel")


class NativeUsageEvidenceTests(unittest.TestCase):
    """Per-attempt usage comes from the delta the native turn advanced."""

    def breakdown(self, input_tokens, cached, output, reasoning, total):
        return {"inputTokens": input_tokens, "cachedInputTokens": cached, "cacheWriteInputTokens": 0,
                "outputTokens": output, "reasoningOutputTokens": reasoning, "totalTokens": total}

    def notify(self, evidence, last, total, *, turn="turn"):
        evidence.observe({"method": "thread/tokenUsage/updated",
                          "params": {"threadId": "root", "turnId": turn,
                                     "tokenUsage": {"last": last, "total": total}}})

    def test_a_resumed_thread_counts_only_the_delta_this_turn_advanced(self):
        evidence = TurnEvidence("root", "turn")
        prior = self.breakdown(250000, 249000, 1000, 0, 250000)
        first = self.breakdown(12000, 11000, 40, 4, 12040)
        second = self.breakdown(13000, 12000, 50, 5, 13050)
        self.notify(evidence, first, {key: prior[key] + first[key] for key in prior})
        self.notify(evidence, second, {key: prior[key] + first[key] + second[key] for key in prior})
        usage = attempt_token_usage(evidence)
        self.assertEqual(usage["inputTokens"], 25000)
        self.assertEqual(usage["cachedInputTokens"], 23000)
        self.assertEqual(usage["outputTokens"], 90)
        self.assertEqual(usage["nativeRecords"], 2)
        self.assertEqual(usage["completeness"], "partial")
        evidence.observe({"method": "turn/completed", "params": {"threadId": "root",
                          "turn": {"id": "turn", "status": "completed"}}})
        usage = attempt_token_usage(evidence)
        self.assertEqual(usage["completeness"], "complete")

    def test_a_replayed_notification_never_counts_twice(self):
        evidence = TurnEvidence("root", "turn")
        last = self.breakdown(100, 40, 20, 5, 120)
        total = self.breakdown(100, 40, 20, 5, 120)
        self.notify(evidence, last, total)
        self.notify(evidence, last, total)
        self.notify(evidence, last, total)
        usage = attempt_token_usage(evidence)
        self.assertEqual(usage["inputTokens"], 100)
        self.assertEqual(usage["nativeRecords"], 1)

    def test_an_unrelated_turn_or_thread_is_ignored(self):
        evidence = TurnEvidence("root", "turn")
        foreign = self.breakdown(100, 40, 20, 5, 120)
        self.notify(evidence, foreign, foreign, turn="another-turn")
        evidence.observe({"method": "thread/tokenUsage/updated",
                          "params": {"threadId": "other", "turnId": "turn",
                                     "tokenUsage": {"last": foreign, "total": foreign}}})
        self.assertIsNone(attempt_token_usage(evidence))

    def test_self_contradictory_native_totals_stay_unknown_instead_of_estimated(self):
        evidence = TurnEvidence("root", "turn")
        # The thread total is smaller than the request it is supposed to contain:
        # no interval is provable, so no usage may be reported at all.
        self.notify(evidence, self.breakdown(500, 400, 100, 5, 600), self.breakdown(100, 40, 20, 5, 120))
        self.assertTrue(evidence.usage_unprovable)
        self.assertIsNone(attempt_token_usage(evidence))
        # A later, internally consistent notification must not resurrect a
        # baseline that would undercount the requests already missed.
        self.notify(evidence, self.breakdown(100, 40, 20, 5, 120), self.breakdown(700, 440, 120, 5, 720))
        self.assertIsNone(attempt_token_usage(evidence))

    def test_a_decreasing_thread_total_marks_the_observation_partial(self):
        evidence = TurnEvidence("root", "turn")
        first = self.breakdown(500, 400, 100, 5, 600)
        self.notify(evidence, first, first)
        # The thread total advances overall while its input component shrinks:
        # the delta is still the latest observed total, but the observation is
        # marked partial rather than presented as complete.
        self.notify(evidence, self.breakdown(190, 70, 35, 3, 228), self.breakdown(190, 70, 35, 3, 720))
        usage = attempt_token_usage(evidence)
        self.assertTrue(evidence.usage_anomaly)
        self.assertEqual(usage["completeness"], "partial")
        self.assertEqual(usage["inputTokens"], 190)

    def test_unusable_breakdowns_are_ignored(self):
        evidence = TurnEvidence("root", "turn")
        for token_usage in ({"last": None, "total": None}, {"last": {"inputTokens": -1}, "total": {}},
                            {"last": {"inputTokens": True, "cachedInputTokens": 0, "outputTokens": 0,
                                      "reasoningOutputTokens": 0, "totalTokens": 0}, "total": {}}):
            self.notify(evidence, token_usage.get("last"), token_usage.get("total"))
        self.assertIsNone(attempt_token_usage(evidence))


if __name__ == "__main__":
    unittest.main()
