"""Adapter result contract for ADR-018 items 22/23 and the retained assistant text.

Codex runs against the private App Server fixture (no model call); DSH's observer
document is supplied directly, because the plugin only runs inside a real headless
dsh child. Both paths end in the same canonical result fields: ``tokenUsage``,
``quota``, ``quotaFailure`` and ``lastAssistantMessage``.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from buddy.adapters import turn_io
from buddy.adapters.base import ExecutionContext, ProcessHandle
from buddy.adapters.codex import CodexAdapter
from buddy.adapters.dsh import DshAdapter, native_usage_sidecar_path

FIXTURES = Path(__file__).parent / "fixtures"
MOCK_CODEX = FIXTURES / "mock_codex.py"
DSH_NATIVE = json.loads((FIXTURES / "native-usage-dsh.json").read_text())
CODEX_NATIVE = json.loads((FIXTURES / "native-usage-codex.json").read_text())

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


def dsh_sidecar_document(task_id: str = "task", attempt_id: str = "attempt", generation: int = 1,
                         *, failure: dict | None = None, message: bool = True) -> dict:
    """The document ``harnesses/dsh/plugins/usage.mjs`` writes for the DSH fixture."""
    events = [event["data"]["usage"] for event in DSH_NATIVE["events"] if event["type"] == "assistant/message"]
    total = sum(event["totalTokens"] for event in events)
    prompt = sum(event["inputTokens"] for event in events)
    output = sum(event["outputTokens"] for event in events)
    document = {
        "version": 1,
        "taskId": task_id,
        "attemptId": attempt_id,
        "generation": generation,
        "updatedAt": "2026-01-01T00:00:00Z",
        "nativeUsage": {
            "source": "dsh/session-assistant-usage",
            "sessionId": "session-fixture-0001",
            "tokenUsage": {
                "source": "dsh/session-assistant-usage",
                "inputBasis": "excludes-cached",
                "inputTokens": prompt,
                "outputTokens": output,
                "cachedInputTokens": total - prompt - output,
                "reasoningOutputTokens": sum(event["reasoningTokens"] for event in events),
                "totalTokens": total,
                "records": len(events),
                "completeness": "complete",
            },
        },
    }
    if message:
        text = DSH_NATIVE["expected"]["lastAssistantText"]
        raw = text.encode()
        document["nativeUsage"]["lastAssistantMessage"] = {
            "text": text, "sourceId": "assistant-fixture-2", "sourceBytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), "truncated": False,
        }
    if failure is not None:
        document["nativeUsage"]["failure"] = failure
    return document


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
        self.adapter = CodexAdapter()

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


class DshUsageContractTests(unittest.TestCase):
    """The DSH observer's document is imported, bound and normalized by the adapter."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-dsh-usage-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.attempt = self.root / "attempt"
        self.attempt.mkdir()
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()

    def context(self, *, workspace: bool = False) -> ExecutionContext:
        return ExecutionContext(
            task_id="task", attempt_id="attempt", generation=1,
            spec={"cwd": str(self.checkout), "task": "x", "timeoutSeconds": 30, "workspace": workspace},
            directory=self.attempt, runtime={}, environment={"BUDDY_STATE_DIR": str(self.root / "state")},
            turn={"turnId": "turn-1", "input": dict(TURN_INPUT)},
        )

    def collect(self, context: ExecutionContext | None = None):
        context = context or self.context()
        stdout = self.attempt / "stdout.log"
        stdout.write_text(json.dumps({
            "status": "ok",
            "processState": {"shutdownConfirmed": True},
            "logPaths": {"stdout": str(stdout), "capture": None},
            "nativeStorage": {"scope": "task-private-sessions", "sessionRootPrivate": True,
                              "credentialsStore": "harness-user-store",
                              "nativeAppVisibility": "not-listed-in-native-app",
                              "resumeMode": "reconstructed-new-session"},
        }) + "\n")
        process = subprocess.Popen([sys.executable, "-c", ""])
        process.wait()
        handle = ProcessHandle(process, own_group=False, log_paths={"stdout": str(stdout)})
        return DshAdapter().collect(handle, context)

    def write_sidecar(self, document: dict) -> None:
        native_usage_sidecar_path(self.context()).write_text(json.dumps(document))

    def test_the_runner_receives_the_attempt_private_sidecar_path(self):
        context = self.context()
        arguments = DshAdapter().arguments(context, {"socketPath": "/tmp/inquiry.sock", "token": "t",
                                                     "resultsPath": "/tmp/results.jsonl", "errorPath": "/tmp/results.error.json"})
        self.assertIn("--usage-file", arguments)
        self.assertEqual(arguments[arguments.index("--usage-file") + 1], str(native_usage_sidecar_path(context)))
        self.assertEqual(native_usage_sidecar_path(context).name, "native-usage.json")

    def test_a_bound_sidecar_becomes_the_canonical_attempt_projection(self):
        self.write_sidecar(dsh_sidecar_document())
        outcome = self.collect()
        expected = DSH_NATIVE["expected"]
        token_usage = outcome.result["tokenUsage"]
        self.assertEqual(token_usage["inputTokens"], expected["unifiedInputTokens"])
        self.assertEqual(token_usage["cachedInputTokens"], expected["derivedCachedInputTokens"])
        self.assertEqual(token_usage["outputTokens"], expected["outputTokens"])
        self.assertEqual(token_usage["source"], "dsh/session-assistant-usage")
        self.assertEqual(token_usage["scope"], "attempt")
        self.assertEqual(token_usage["nativeRecords"], expected["records"])
        self.assertEqual(outcome.result["quota"], None)
        self.assertEqual(outcome.result["quotaFailure"], None)
        message = outcome.result["lastAssistantMessage"]
        self.assertEqual(message["text"], expected["lastAssistantText"])
        self.assertEqual(message["source"], "dsh/session-root-assistant-message")
        self.assertEqual(message["sourceId"], "assistant-fixture-2")
        self.assertFalse(message["truncated"])
        self.assertTrue(outcome.result["nativeUsage"]["sidecarWritten"])

    def test_replaying_the_same_record_never_accumulates(self):
        self.write_sidecar(dsh_sidecar_document())
        first = self.collect().result["tokenUsage"]
        second = self.collect().result["tokenUsage"]
        self.assertEqual(first, second)
        self.assertEqual(second["inputTokens"], DSH_NATIVE["expected"]["unifiedInputTokens"])

    def test_a_foreign_or_missing_sidecar_reports_unknown_not_zero(self):
        self.write_sidecar(dsh_sidecar_document(attempt_id="another-attempt"))
        outcome = self.collect()
        self.assertIsNone(outcome.result["tokenUsage"])
        self.assertIsNone(outcome.result["lastAssistantMessage"])

    def test_a_quota_failure_keeps_the_native_code_without_provider_wording(self):
        self.write_sidecar(dsh_sidecar_document(
            failure={"code": DSH_NATIVE["quotaFailure"]["expectedCode"],
                     "message": "provider wording that must never be retained"}))
        outcome = self.collect()
        failure = outcome.result["quotaFailure"]
        self.assertEqual(failure["nativeCode"], "QUOTA")
        self.assertEqual(failure["code"], "quota-exceeded")
        self.assertEqual(failure["source"], "dsh/session-turn-end")
        self.assertNotIn("provider wording", json.dumps(outcome.result))

    def test_an_unrelated_turn_failure_is_not_reported_as_a_quota_failure(self):
        self.write_sidecar(dsh_sidecar_document(failure={"code": "CONTEXT_WINDOW_EXCEEDED", "kind": "error"}))
        outcome = self.collect()
        self.assertIsNone(outcome.result["quotaFailure"])
        self.assertIsNotNone(outcome.result["tokenUsage"])

    def test_a_partial_observation_keeps_its_own_completeness(self):
        document = dsh_sidecar_document()
        usage = document["nativeUsage"]["tokenUsage"]
        usage["completeness"] = "partial"
        usage.pop("reasoningOutputTokens")
        self.write_sidecar(document)
        token_usage = self.collect().result["tokenUsage"]
        self.assertEqual(token_usage["completeness"], "partial")
        self.assertNotIn("reasoningOutputTokens", token_usage)

    def test_an_ungoverned_run_never_imports_a_sidecar(self):
        context = self.context()
        context.turn = None
        self.write_sidecar(dsh_sidecar_document())
        outcome = self.collect(context)
        self.assertIsNone(outcome.result["tokenUsage"])
        self.assertIsNone(outcome.result["lastAssistantMessage"])


if __name__ == "__main__":
    unittest.main()
