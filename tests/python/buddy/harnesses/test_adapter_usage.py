"""Adapter result contract for ADR-018 items 22/23 and the retained assistant text.

Codex runs against the private App Server fixture (no model call); DSH runs against the private ACP fixture and reads its own session record. Both paths end in the same canonical result fields: ``tokenUsage``,
``quotaFailure`` and ``lastAssistantMessage``.
"""
from __future__ import annotations

import json
import os
import sys
from temporary import TemporaryDirectory
import unittest
from pathlib import Path
from buddy.harnesses.fixture_selection import mock_role_workspace, select_fixture

from hey_my_buddy.buddy.roles import turn_io
from hey_my_buddy.buddy.harnesses.base import ExecutionContext
from hey_my_buddy.buddy.roles.controller import worker_executor
from hey_my_buddy.errors import BoardError
from hey_my_buddy.private_dirs import context_root, native_root
from buddy.harnesses.dsh.test_dsh_role_wiring import DshRoleCase

FIXTURES = Path(__file__).parent
MOCK_CODEX = FIXTURES / "codex/fixtures/mock_codex.py"
CODEX_NATIVE = json.loads((FIXTURES / "codex/fixtures/native-usage-codex.json").read_text(encoding="utf-8"))

class CodexUsageContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="hey-my-buddy-codex-usage-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cwd = self.root / "checkout"
        self.cwd.mkdir()
        mock_role_workspace(self)
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("HMB_") and key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        self.environment.update(HMB_CONSOLE_PORT="0",
                                HMB_CODEX_FIXTURE_STATE=str(self.root / "fixture.json"),
                                HMB_STATE_DIR=str(self.root / "state"), HMB_RUNTIME_ROOT=str(self.root / "runtime"),
                                HMB_DEV_SOURCE="1")
        select_fixture(self.environment, self.root, "codex", MOCK_CODEX)
        self.adapter = worker_executor("codex")

    def context(self, case: str, *, index: int = 1, previous: str | None = None) -> ExecutionContext:
        turn_input = {"version": 1, "taskId": "micro-task-1", "attemptId": f"attempt-{index}", "generation": index,
                      "turnId": f"turn-{index}", "resumeMode": "native-session" if previous else "initial",
                      "previousSessionId": previous, "context": {},
                      "executionWorkspace": {"path": str(self.cwd), "access": "write"}}
        return ExecutionContext(task_id="micro-task-1", attempt_id=f"attempt-{index}", generation=index,
                                spec={"adapter": "codex", "cwd": str(self.cwd), "task": "Write a fixture result", "expectedSeconds": 8,
                                      "provider": "openai", "model": "fixture-model", "effort": "low"},
                                directory=self.root / f"attempt-{index}", runtime={},
                                environment={**self.environment, "HMB_CODEX_FIXTURE_CASE": case},
                                session_record_path=str(self.root / f"session-{index}.jsonl"),
                                turn={"turnId": f"turn-{index}", "input": turn_input})

    def execute(self, context: ExecutionContext):
        self.adapter.prepare(context)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: (handle.terminate(grace_seconds=0.2), handle.wait(15)) if not handle.finished else None)
        self.assertIsNotNone(handle.wait(15), "Codex fixture controller did not exit")
        return self.adapter.collect(handle, context)

    def test_a_native_turn_reports_attempt_scoped_usage(self):
        outcome = self.execute(self.context("usage"))
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

        self.assertNotIn("quotaFailure", outcome.result)

        message = outcome.result["lastAssistantMessage"]
        self.assertEqual(message["source"], "codex/app-server-root-assistant-message")
        self.assertEqual(message["sourceId"], "final-1")
        self.assertEqual(message["phase"], "final_answer")
        self.assertIn("fixture work completed", message["text"])
        self.assertFalse(message["truncated"])

    def test_a_resumed_thread_counts_only_this_attempt(self):
        first = self.execute(self.context("usage"))
        self.assertEqual(first.status, "ok", first.to_report())
        second = self.execute(self.context("usage", index=2, previous=first.result["sessionId"]))
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
        self.assertEqual(failure["rejectionPhase"], "during-model")
        # Usage observed before the failure and the root message both survive.
        self.assertEqual(outcome.result["tokenUsage"]["inputTokens"], CODEX_NATIVE["expected"]["inputTokens"])
        self.assertIsNotNone(outcome.result["lastAssistantMessage"])
        self.assertNotIn("provider wording", json.dumps(outcome.result))

    def test_an_attempt_without_native_usage_reports_unknown_not_zero(self):
        outcome = self.execute(self.context("ok"))
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertIsNone(outcome.result["tokenUsage"])
        self.assertNotIn("quotaFailure", outcome.result)
        self.assertIsNotNone(outcome.result["lastAssistantMessage"])


class DshUsageContractTests(DshRoleCase):
    """The private record reaches the real registered Worker collection path."""

    def setUp(self):
        super().setUp()
        self.sequence = 0
        self.record_mode = "usage"
        self.variant = None

    def micro_task_record(self, context, **kwargs):
        super().micro_task_record(context, **kwargs)
        selection = json.loads(self.record.read_text(encoding="utf-8"))
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
            shim.write_text(source, encoding="utf-8", newline="\n")
            command[1] = str(shim)
        self.record.write_text(json.dumps(selection), encoding="utf-8", newline="\n")

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
        entries = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        written = next(e for e in entries if e.get("event") == "session-record-written")
        record = Path(written["path"])
        self.assertFalse(record.is_relative_to(context_root(context, "dsh")))
        self.assertTrue(record.is_relative_to(native_root(
            Path(context.environment["HMB_STATE_DIR"]), "dsh", context.task_id)))
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
        self.assertNotIn("quotaFailure", outcome.result)
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
        self.assertEqual(failure["rejectionPhase"], "during-model")
        self.assertNotIn("provider wording", json.dumps(outcome.result))

    def test_an_unrelated_turn_failure_is_not_reported_as_a_quota_failure(self):
        _context, _handle, outcome = self.execute_record("quota", variant="non-quota")
        self.assertNotIn("quotaFailure", outcome.result)
        self.assertIsNotNone(outcome.result["tokenUsage"])

    def test_a_partial_observation_keeps_its_own_completeness(self):
        _context, _handle, outcome = self.execute_record("missing", variant="no-reasoning")
        usage = outcome.result["tokenUsage"]
        self.assertEqual(usage["completeness"], "partial")
        self.assertNotIn("reasoningOutputTokens", usage)

    def test_an_unmicro_task_context_cannot_publish_worker_usage(self):
        context = self.context()
        context.turn = None
        self.micro_task_record(context)
        with self.assertRaises(BoardError):
            worker_executor("dsh").prepare(context)
        self.assertFalse(self.log.exists(), "a native child must not start without the micro task input")


if __name__ == "__main__":
    unittest.main()
