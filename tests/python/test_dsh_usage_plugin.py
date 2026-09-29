"""DSH native usage observer: the real Node plugin, read by the real Python reader.

The observer runs inside the installed dsh process, so its contract is tested by
driving the real ``harnesses/dsh/plugins/activity.mjs`` module with the discovered
native event shapes and then reading the emitted file with the real
``buddy.activity`` reader. This suite does the same for
``harnesses/dsh/plugins/usage.mjs`` and ``buddy.usage``: the desensitized native
DSH record in ``fixtures/native-usage-dsh.json`` is projected by the production
plugin, then normalized by the production adapter-side reader.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from buddy import usage

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "harnesses/dsh/plugins/usage.mjs"
FIXTURE = json.loads((Path(__file__).parent / "fixtures/native-usage-dsh.json").read_text())

DRIVER = textwrap.dedent(
    """
    import { readFileSync } from 'node:fs';
    const { apply } = await import(process.argv[2]);
    const plan = JSON.parse(readFileSync(process.argv[3], 'utf8'));
    const listeners = [];
    const ctx = { on(event, listener) { if (event === 'session/event') listeners.push(listener); } };
    apply(ctx, plan.config);
    for (const [session, event] of plan.events) {
      for (const listener of listeners) listener(session, event);
    }
    """
)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@unittest.skipUnless(shutil.which("node") and PLUGIN.is_file(), "Node.js and the usage plugin are required")
class DshUsagePluginTests(unittest.TestCase):
    def test_replayed_native_events_do_not_double_count_usage(self):
        self.mount([*FIXTURE["events"], *FIXTURE["events"]])
        native = self.read()["nativeUsage"]["tokenUsage"]
        self.assertEqual(native["records"], FIXTURE["expected"]["records"])
        self.assertEqual(native["inputTokens"], FIXTURE["expected"]["inputTokens"])

    def test_escaped_assistant_text_cannot_overflow_the_usage_sidecar(self):
        import copy
        events = copy.deepcopy(FIXTURE["events"])
        for event in events:
            if event["type"] == "assistant/message":
                event["data"]["message"]["content"] = [{"type": "text", "text": '"\\' * 40000}]
        self.mount(events)
        message = self.read()["nativeUsage"]["lastAssistantMessage"]
        self.assertTrue(message["truncated"])
        self.assertLessEqual(len(json.dumps(message["text"]).encode()), usage.MAX_ASSISTANT_MESSAGE_BYTES)
        self.assertEqual(message["sourceBytes"], 80000)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-dsh-usage-plugin-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.driver = self.root / "driver.mjs"
        self.driver.write_text(DRIVER)
        self.usage_path = self.root / "native-usage.json"
        self.session = {"id": FIXTURE["session"]["id"], "header": dict(FIXTURE["session"])}

    def mount(self, events: list[tuple[dict, dict]], *, config: dict | None = None) -> Path:
        plan = {
            "config": config or {"usagePath": str(self.usage_path), "taskId": "task-one", "attemptId": "attempt-one",
                                 "generation": 1, "promptSha256": sha256(FIXTURE["prompt"]),
                                 "cwd": FIXTURE["session"]["cwd"]},
            "events": [[self.session, event] for event in events],
        }
        plan_path = self.root / "plan.json"
        plan_path.write_text(json.dumps(plan))
        environment = {key: value for key, value in os.environ.items() if not key.startswith("BUDDY_")}
        completed = subprocess.run([shutil.which("node"), str(self.driver), str(PLUGIN), str(plan_path)],
                                   capture_output=True, text=True, timeout=60, env=environment, cwd=str(ROOT))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return self.usage_path

    def read(self) -> dict:
        document = usage.read_sidecar(self.usage_path, task_id="task-one", attempt_id="attempt-one", generation=1)
        self.assertIsNotNone(document, self.usage_path.read_text()[:400])
        return document

    def test_the_real_plugin_projects_the_native_record_into_the_python_contract(self):
        self.mount(FIXTURE["events"])
        native = self.read()["nativeUsage"]
        token_usage = usage.normalize_token_usage({
            "source": native["tokenUsage"]["source"], "inputBasis": native["tokenUsage"]["inputBasis"],
            "inputTokens": native["tokenUsage"]["inputTokens"],
            "cachedInputTokens": native["tokenUsage"]["cachedInputTokens"],
            "outputTokens": native["tokenUsage"]["outputTokens"],
            "reasoningOutputTokens": native["tokenUsage"].get("reasoningOutputTokens"),
            "nativeRecords": native["tokenUsage"]["records"],
            "completeness": native["tokenUsage"]["completeness"],
        })
        expected = FIXTURE["expected"]
        self.assertEqual(token_usage["inputTokens"], expected["unifiedInputTokens"])
        self.assertEqual(token_usage["cachedInputTokens"], expected["derivedCachedInputTokens"])
        self.assertEqual(token_usage["outputTokens"], expected["outputTokens"])
        self.assertEqual(token_usage["scope"], "attempt")
        self.assertEqual(token_usage["completeness"], "complete")
        message = usage.normalize_last_assistant_message(native["lastAssistantMessage"],
                                                        source="dsh/session-root-assistant-message")
        self.assertEqual(message["text"], expected["lastAssistantText"])
        self.assertEqual(message["source"], "dsh/session-root-assistant-message")
        self.assertFalse(message["truncated"])

    def test_the_real_plugin_classifies_a_quota_failure_without_provider_wording(self):
        self.mount([*FIXTURE["events"], FIXTURE["quotaFailure"]["event"]])
        native = self.read()["nativeUsage"]
        self.assertEqual(native["failure"]["code"], FIXTURE["quotaFailure"]["expectedCode"])
        self.assertEqual(native["tokenUsage"]["completeness"], "partial")
        failure = usage.normalize_quota_failure({"nativeCode": native["failure"]["code"],
                                                 "source": "dsh/session-turn-end"})
        self.assertEqual(failure["code"], "quota-exceeded")
        self.assertNotIn("provider wording", self.usage_path.read_text())

    def test_a_record_without_native_usage_stays_unknown_instead_of_zero(self):
        events = [event for event in FIXTURE["events"] if event["type"] != "assistant/message"]
        events.append({"type": "assistant/message", "seq": 20, "data": {
            "turn": 1, "step": 3,
            "message": {"id": "assistant-fixture-3", "role": "assistant",
                        "content": [{"type": "text", "text": "No usage was reported for this step.\n"}],
                        "source": {"kind": "model", "provider": "deepseek-official", "model": "deepseek-flash"}}}})
        self.mount(events)
        native = self.read()["nativeUsage"]
        self.assertNotIn("inputTokens", native["tokenUsage"])
        self.assertNotIn("outputTokens", native["tokenUsage"])
        token_usage = usage.normalize_token_usage({"source": native["tokenUsage"]["source"],
                                                   "inputBasis": native["tokenUsage"]["inputBasis"],
                                                   "nativeRecords": native["tokenUsage"]["records"],
                                                   "completeness": native["tokenUsage"]["completeness"]})
        self.assertIsNone(token_usage)
        self.assertEqual(native["tokenUsage"]["completeness"], "partial")

    def test_a_foreign_session_never_writes_a_sidecar(self):
        self.mount(FIXTURE["events"], config={"usagePath": str(self.usage_path), "taskId": "task-one",
                                              "attemptId": "attempt-one", "generation": 1,
                                              "promptSha256": sha256("a different prompt"),
                                              "cwd": FIXTURE["session"]["cwd"]})
        self.assertFalse(self.usage_path.exists())

    def test_a_generation_mismatch_is_refused_by_the_python_binding(self):
        self.mount(FIXTURE["events"])
        self.assertIsNotNone(usage.read_sidecar(self.usage_path, task_id="task-one",
                                               attempt_id="attempt-one", generation=1))
        self.assertIsNone(usage.read_sidecar(self.usage_path, task_id="task-one",
                                             attempt_id="attempt-one", generation=2))


if __name__ == "__main__":
    unittest.main()
