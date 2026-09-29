"""Codex transport deadline boundary checks; no model network or shared state is used."""
import json
import math
import subprocess
import sys
import threading
import time
import unittest
from types import SimpleNamespace

from buddy.adapters.codex_protocol import CodexProtocolError, Connection, TurnEvidence, parse_outcome, native_checkpoint, validated_checkpoint, checkpoint_resumable
from buddy.adapters.codex_runner import execution_deadline


class StructuredOutcomeTests(unittest.TestCase):
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
        self.assertEqual(parse_outcome(json.dumps(value, ensure_ascii=False)), value["outcome"])

    def test_total_outcome_bound_still_rejects_large_reports(self):
        with self.assertRaisesRegex(ValueError, "byte bound"):
            parse_outcome(json.dumps(self.outcome("研" * 22000), ensure_ascii=False))

    def test_memory_markup_does_not_relax_the_json_contract(self):
        value = json.dumps(self.outcome("valid summary"))
        for raw in (value + "<oai-mem-citation>extra</oai-mem-citation>",
                    value.replace('"summary":', '"summary":"duplicate", "summary":'),
                    value.replace('"request": null', '"request": {}')):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_outcome(raw)


class NativeCheckpointTests(unittest.TestCase):
    def test_failed_turn_can_retain_only_its_own_completed_assistant_message(self):
        evidence = TurnEvidence("root", "turn")
        evidence.observe({"method": "turn/started", "params": {"threadId": "root", "turn": {"id": "turn"}}})
        for thread, text in (("root", "partial research"), ("child", "unrelated child report")):
            evidence.observe({"method": "item/completed", "params": {"threadId": thread, "turnId": "turn",
                "item": {"id": "item-1", "type": "agentMessage", "phase": "commentary", "text": text}}})
        evidence.observe({"method": "turn/completed", "params": {"threadId": "root", "turn": {"id": "turn", "status": "failed"}}})
        checkpoint = native_checkpoint(evidence, {"taskId": "goal", "attemptId": "attempt", "generation": 1, "turnId": "turn"})
        self.assertEqual(checkpoint["lastAssistantMessage"]["text"], "partial research")
        self.assertIsNone(evidence.final_item)

    def test_message_is_bounded_and_truncation_is_honest(self):
        document = {"taskId": "goal", "attemptId": "attempt", "generation": 1, "turnId": "turn"}
        evidence = SimpleNamespace(thread_id="thread", turn_id="native-turn", started=True,
                                   completed={"status": "completed"}, event_seq=3,
                                   final_item={"id": "final", "text": "调研\n" * 15000})
        checkpoint = native_checkpoint(evidence, document)
        self.assertTrue(checkpoint["lastAssistantMessage"]["truncated"])
        payload = {"nativeCheckpoint": checkpoint, "sessionId": "thread", "nativeTurnId": "native-turn",
                   "processState": {"shutdownConfirmed": True, "nativeExitCode": 0}}
        self.assertEqual(validated_checkpoint(payload, document), checkpoint)
        self.assertFalse(checkpoint_resumable(payload, checkpoint))
        checkpoint["bindingSaved"] = True
        self.assertTrue(checkpoint_resumable(payload, checkpoint))
        for change in ({"inputSha256": "foreign"}, {"sessionId": "foreign"}, {"nativeTurnStarted": False},
                       {"generation": True}, {"eventSeq": 1}):
            with self.subTest(change=change):
                self.assertIsNone(validated_checkpoint({**payload, "nativeCheckpoint": {**checkpoint, **change}}, document))
        self.assertIsNone(validated_checkpoint({**payload, "processState": None}, document))
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


if __name__ == "__main__":
    unittest.main()
