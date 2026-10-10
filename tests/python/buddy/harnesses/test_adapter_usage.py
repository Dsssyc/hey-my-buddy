"""Adapter result contract for ADR-018 items 22/23 and the retained assistant text.

Codex runs against the private App Server fixture (no model call); DSH runs against the private ACP fixture and reads its own session record. Both paths end in the same canonical result fields: ``tokenUsage``,
``quota``, ``quotaFailure`` and ``lastAssistantMessage``.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from hey_my_buddy.buddy.roles import turn_io
from hey_my_buddy.buddy.harnesses.base import ExecutionContext
from hey_my_buddy.buddy.roles.controller import worker_executor
from hey_my_buddy.errors import BoardError
from hey_my_buddy.private_dirs import context_root, native_root
from buddy.harnesses.dsh.test_dsh_role_wiring import DshRoleCase

FIXTURES = Path(__file__).parent
MOCK_CODEX = FIXTURES / "codex/fixtures/mock_codex.py"
CODEX_NATIVE = json.loads((FIXTURES / "codex/fixtures/native-usage-codex.json").read_text())

TURN_INPUT = {
    "version": 1,
    "taskId": "task",
    "attemptId": "attempt",
    "generation": 1,
    "turnId": "turn-1",
    "resumeMode": "initial",
    "previousSessionId": None,
    "context": {},
    "executionWorkspace": {},
}


class CodexUsageContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-codex-usage-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cwd = self.root / "checkout"
        self.cwd.mkdir()
        MOCK_CODEX.chmod(0o755)
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("BUDDY_") and key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        self.environment.update(BUDDY_CONSOLE_PORT="0", BUDDY_CODEX_CLI=str(MOCK_CODEX),
                                BUDDY_CODEX_FIXTURE_STATE=str(self.root / "fixture.json"),
                                BUDDY_STATE_DIR=str(self.root / "state"), BUDDY_RUNTIME_ROOT=str(self.root / "runtime"),
                                BUDDY_DEV_SOURCE="1")
        self.adapter = worker_executor("codex")

    def context(self, case: str, *, index: int = 1, previous: str | None = None) -> ExecutionContext:
        turn_input = {"version": 1, "taskId": "goal-1", "attemptId": f"attempt-{index}", "generation": index,
                      "turnId": f"turn-{index}", "resumeMode": "native-session" if previous else "initial",
                      "previousSessionId": previous, "context": {}, "executionWorkspace": {}}
        return ExecutionContext(task_id="goal-1", attempt_id=f"attempt-{index}", generation=index,
                                spec={"cwd": str(self.cwd), "task": "Write a fixture result", "timeoutSeconds": 8,
                                      "provider": "openai", "model": "fixture-model", "effort": "low"},
                                directory=self.root / f"attempt-{index}", runtime={},
                                environment={**self.environment, "BUDDY_CODEX_FIXTURE_CASE": case},
                                turn={"turnId": f"turn-{index}", "input": turn_input})

    def execute(self, context: ExecutionContext):
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(15), "Codex fixture controller did not exit")
        return self.adapter.collect(handle, context)

    def test_a_native_turn_reports_attempt_scoped_usage_and_a_quota_observation(self):
        outcome = self.execute(self.context("usage-read"))
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        expected = CODEX_NATIVE["expected"]
        token_usage = outcome.result["tokenUsage"]
        self.assertEqual(token_usage["inputTokens"], expected["inputTokens"])
        self.assertEqual(token_usage["cachedInputTokens"], expected["cachedInputTokens"])
        self.assertEqual(token_usage["outputTokens"], expected["outputTokens"])
        self.assertEqual(token_usage["reasoningOutputTokens"], expected["reasoningOutputTokens"])
        self.assertEqual(token_usage["inputBasis"], "includes-cached")
        self.assertEqual(token_usage["scope"], "attempt")
        self.assertEqual(token_usage["source"], "codex/app-server-thread-token-usage")
        # The native stream repeated its final snapshot; the replay is not new usage.
        self.assertEqual(token_usage["nativeRecords"], expected["records"])
        self.assertEqual(token_usage["completeness"], "complete")

        quota = outcome.result["quota"]
        self.assertEqual(quota["scope"]["provider"], "openai")
        self.assertEqual(quota["scope"]["nativeAccountId"], "account-fixture-0001")
        self.assertTrue(quota["ordinaryUsageAllowed"])
        self.assertEqual([window["name"] for window in quota["windows"]], ["primary", "secondary"])
        self.assertEqual(quota["windows"][0]["usedPercent"], 42.5)
        self.assertEqual(quota["windows"][1]["windowDurationMins"], 10080)
        self.assertIsNone(outcome.result["quotaFailure"])

        message = outcome.result["lastAssistantMessage"]
        self.assertEqual(message["source"], "codex/app-server-root-assistant-message")
        self.assertEqual(message["sourceId"], "final-1")
        self.assertEqual(message["phase"], "final_answer")
        self.assertIn("fixture work completed", message["text"])
        self.assertFalse(message["truncated"])

    def test_a_resumed_thread_counts_only_this_attempt(self):
        first = self.execute(self.context("usage-read"))
        self.assertEqual(first.status, "ok", first.to_report())
        second = self.execute(self.context("usage-read", index=2, previous=first.result["sessionId"]))
        self.assertEqual(second.status, "ok", second.to_report())
        expected = CODEX_NATIVE["expected"]
        # The fixture's thread totals already carry 250,000 earlier tokens on the
        # resumed turn; the delta must be exactly this attempt's two requests.
        self.assertEqual(second.result["tokenUsage"]["inputTokens"], expected["inputTokens"])
        self.assertEqual(second.result["tokenUsage"]["nativeRecords"], expected["records"])

    def test_a_quota_turn_keeps_its_native_code_and_retained_message(self):
        outcome = self.execute(self.context("quota-failure"))
        self.assertEqual(outcome.status, "failed")
        failure = outcome.result["quotaFailure"]
        self.assertEqual(failure["nativeCode"], "usageLimitExceeded")
        self.assertEqual(failure["code"], "quota-exceeded")
        self.assertEqual(failure["source"], "codex/app-server-turn-error")
        # Usage observed before the failure and the root message both survive.
        self.assertEqual(outcome.result["tokenUsage"]["inputTokens"], CODEX_NATIVE["expected"]["inputTokens"])
        self.assertIsNotNone(outcome.result["lastAssistantMessage"])
        self.assertNotIn("provider wording", json.dumps(outcome.result))

    def test_a_failed_quota_read_keeps_the_rolling_notification(self):
        outcome = self.execute(self.context("usage"))
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        quota = outcome.result["quota"]
        self.assertEqual([window["name"] for window in quota["windows"]], ["primary"])
        self.assertEqual(quota["windows"][0]["usedPercent"], 77)
        self.assertEqual(quota["source"], "codex/app-server-rate-limits")
        self.assertNotIn("ordinaryUsageAllowed", quota)
        self.assertEqual(outcome.result["tokenUsage"]["inputTokens"], CODEX_NATIVE["expected"]["inputTokens"])

    def test_an_attempt_without_native_usage_reports_unknown_not_zero(self):
        outcome = self.execute(self.context("ok"))
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertIsNone(outcome.result["tokenUsage"])
        self.assertIsNone(outcome.result["quotaFailure"])
        self.assertIsNotNone(outcome.result["quota"])
        self.assertIsNotNone(outcome.result["lastAssistantMessage"])


class DshUsageContractTests(DshRoleCase):
    """The private record reaches the real registered Worker collection path."""

    def setUp(self):
        super().setUp()
        self.sequence = 0
        self.record_mode = "usage"
        self.variant = None

    def governed_record(self, context, **kwargs):
        super().governed_record(context, **kwargs)
        selection = json.loads(self.record.read_text())
        command = selection["dsh"]["command"]
        if self.record_mode is not None:
            command += ["--session-record", self.record_mode]
        if self.variant:
            # Reuse the ACP fake and vary only its synthetic record, never the
            # driver or the shared collector being tested.
            shim = self.root / (self.variant + ".py")
            source = (
                "import json, os, sys\nfrom pathlib import Path\nimport zstandard\n"
                f"sys.path.insert(0, {str(Path(__file__).parents[2])!r})\n"
                "from buddy.harnesses.dsh.acp import fake_agent\n"
                "original = fake_agent.FakeAgent.write_session_record\n"
                "def record(self, session_id, prompt):\n"
                "    original(self, session_id, prompt)\n"
                "    path = self.sessions_dir() / (session_id + '.v3.jsonl.zstd')\n"
                "    raw = zstandard.ZstdDecompressor().decompress(path.read_bytes()).decode()\n"
                "    events = [json.loads(line) for line in raw.splitlines()]\n"
                "    for event in events:\n"
                "        data = event.get('data', {})\n"
            )
            if self.variant == "non-quota":
                source += "        if event['type'] == 'turn/end': data['reason']['error']['code'] = 'CONTEXT_WINDOW_EXCEEDED'\n"
            else:
                source += "        if 'usage' in data: data['usage'].pop('reasoningTokens', None)\n"
            source += (
                "    path.write_bytes(zstandard.ZstdCompressor().compress((''.join(json.dumps(e) + '\\n' for e in events)).encode()))\n"
                "fake_agent.FakeAgent.write_session_record = record\n"
                "raise SystemExit(fake_agent.main())\n"
            )
            shim.write_text(source)
            command[1] = str(shim)
        self.record.write_text(json.dumps(selection))

    def execute_record(self, mode="usage", *, variant=None):
        self.sequence += 1
        self.record_mode, self.variant = mode, variant
        context = self.context(index=self.sequence, attempt=f"usage-{self.sequence}")
        handle, outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        return context, handle, outcome

    def test_the_run_reads_only_its_task_private_session_record(self):
        context, handle, _outcome = self.execute_record()
        entries = [json.loads(line) for line in self.log.read_text().splitlines()]
        written = next(e for e in entries if e.get("event") == "session-record-written")
        record = Path(written["path"])
        self.assertFalse(record.is_relative_to(context_root(context, "dsh")))
        self.assertTrue(record.is_relative_to(native_root(
            Path(context.environment["BUDDY_STATE_DIR"]), "dsh", context.task_id)))
        self.assertTrue(record.is_relative_to(Path(handle.role_run_control["nativeRoot"])))
        self.assertTrue(record.name.endswith(".v3.jsonl.zstd"))

    def test_a_bound_record_becomes_the_canonical_attempt_projection(self):
        _context, _handle, outcome = self.execute_record()
        usage = outcome.result["tokenUsage"]
        self.assertEqual((usage["inputTokens"], usage["outputTokens"], usage["cachedInputTokens"]),
                         (3150, 300, 150))
        self.assertEqual(usage["source"], "dsh/session-record")
        self.assertEqual(usage["inputBasis"], "includes-cached")
        self.assertEqual(usage["scope"], "attempt")
        self.assertEqual(usage["nativeRecords"], 2)
        self.assertIsNone(outcome.result["quota"])
        self.assertIsNone(outcome.result["quotaFailure"])
        message = outcome.result["lastAssistantMessage"]
        self.assertEqual(message["text"], "the final answer")
        self.assertEqual(message["source"], "dsh/session-root-assistant-message")
        self.assertTrue(message["sourceId"].endswith("-10"))
        self.assertFalse(message["truncated"])

    def test_replaying_the_same_record_never_accumulates(self):
        context, handle, first = self.execute_record()
        second = worker_executor("dsh").collect(handle, context)
        self.assertEqual(first.result["tokenUsage"], second.result["tokenUsage"])
        self.assertEqual(second.result["tokenUsage"]["inputTokens"], 3150)

    def test_a_foreign_or_missing_record_reports_unknown_not_zero(self):
        for mode in (None, "foreign"):
            with self.subTest(mode=mode):
                _context, _handle, outcome = self.execute_record(mode)
                self.assertIsNone(outcome.result["tokenUsage"])
                self.assertIsNone(outcome.result["lastAssistantMessage"])

    def test_a_quota_failure_keeps_the_native_code_without_provider_wording(self):
        _context, _handle, outcome = self.execute_record("quota")
        failure = outcome.result["quotaFailure"]
        self.assertEqual(failure["nativeCode"], "QUOTA")
        self.assertEqual(failure["code"], "quota-exceeded")
        self.assertEqual(failure["source"], "dsh/session-turn-end")
        self.assertNotIn("provider wording", json.dumps(outcome.result))

    def test_an_unrelated_turn_failure_is_not_reported_as_a_quota_failure(self):
        _context, _handle, outcome = self.execute_record("quota", variant="non-quota")
        self.assertIsNone(outcome.result["quotaFailure"])
        self.assertIsNotNone(outcome.result["tokenUsage"])

    def test_a_partial_observation_keeps_its_own_completeness(self):
        _context, _handle, outcome = self.execute_record("missing", variant="no-reasoning")
        usage = outcome.result["tokenUsage"]
        self.assertEqual(usage["completeness"], "partial")
        self.assertNotIn("reasoningOutputTokens", usage)

    def test_an_ungoverned_context_cannot_publish_worker_usage(self):
        context = self.context()
        context.turn = None
        self.governed_record(context)
        with self.assertRaises(BoardError):
            worker_executor("dsh").start(context)
        self.assertFalse(self.log.exists(), "a native child must not start without the governed input")


if __name__ == "__main__":
    unittest.main()
