"""DSH bounded activity observer: the real Node plugin and the runner's sidecar flag.

The observer runs inside the installed dsh process, so its contract is tested by
driving the real ``harnesses/dsh/plugins/activity.mjs`` module with the discovered
native event shapes and then reading the emitted file with the real
``hey_my_buddy.protocol.activity`` reader. The runner's private ``--activity-file`` boundary is
checked without spawning anything.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from hey_my_buddy.protocol import activity as activity_module

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "harnesses/dsh/plugins/activity.mjs"
RUNNER = ROOT / "harnesses/dsh/scripts/run.mjs"
PROMPT = "Do the bounded thing.\n"
IDENTITY = {"taskId": "task-one", "attemptId": "attempt-one", "generation": 2}

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
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


def root_session(session_id: str, cwd: Path) -> dict:
    return {"id": session_id, "header": {"version": 3, "id": session_id, "createdAt": 0, "cwd": str(cwd), "isSeeded": False}}


def user_message(text: str, seq: int = 4) -> dict:
    return {"type": "user/message", "seq": seq, "time": 0,
            "data": {"id": f"message-{seq}", "role": "user", "content": [{"type": "text", "text": text}],
                     "source": {"kind": "user"}},
            "surfaceOp": "append"}


def tool_call(seq: int, name: str) -> dict:
    return {"type": "tool/call", "seq": seq, "time": 0,
            "data": {"callId": f"call-{seq}", "name": name, "arguments": {"secretArgument": "must never be written"}}}


def tool_result(seq: int) -> dict:
    return {"type": "tool/result", "seq": seq, "time": 0,
            "data": {"message": {"content": [{"type": "text", "text": "secret tool output"}],
                                 "source": {"callId": f"call-{seq}"}}}}


@unittest.skipUnless(shutil.which("node") and PLUGIN.is_file(), "Node.js and the dsh activity observer are required")
class DshActivityObserverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-dsh-activity-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.driver = self.root / "driver.mjs"
        self.driver.write_text(DRIVER)

    def emit(self, events: list, *, config: dict | None = None, cwd: Path | None = None) -> Path:
        cwd = cwd or self.root
        activity_path = self.root / "activity.json"
        plan = {
            "config": {"activityPath": str(activity_path), **IDENTITY,
                       "promptSha256": sha256(PROMPT), "cwd": str(cwd), **(config or {})},
            "events": events,
        }
        plan_file = self.root / "plan.json"
        plan_file.write_text(json.dumps(plan))
        completed = subprocess.run([shutil.which("node"), str(self.driver), str(PLUGIN), str(plan_file)],
                                   capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return activity_path

    def test_a_bound_root_publishes_a_real_metadata_only_sidecar(self):
        session = root_session("root-1", self.root)
        path = self.emit([
            [session, user_message(PROMPT)],
            [session, tool_call(7, "X" * 400)],
            [session, tool_result(8)],
            [session, {"type": "turn/end", "seq": 9, "time": 0, "data": {}}],
        ])
        payload = activity_module.read_sidecar(path, task_id=IDENTITY["taskId"],
                                              attempt_id=IDENTITY["attemptId"], generation=IDENTITY["generation"])
        self.assertIsNotNone(payload, "the observer must write the frozen bound sidecar")
        self.assertEqual(payload["phase"], "finishing")
        self.assertEqual(payload["nativeSessionId"], "root-1")
        self.assertEqual(payload["counts"], {"modelTurns": 1, "toolCalls": 1})
        self.assertEqual(len(payload["toolName"]), 64)
        self.assertGreaterEqual(payload["eventSeq"], 9)
        raw = path.read_text()
        for forbidden in ("must never be written", "secret tool output", PROMPT, "secretArgument"):
            self.assertNotIn(forbidden, raw)
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        self.assertEqual(sorted(item.name for item in self.root.iterdir() if item.name.startswith(".activity")), [])
        # The binding is real: another attempt or generation reads nothing.
        self.assertIsNone(activity_module.read_sidecar(path, task_id=IDENTITY["taskId"],
                                                       attempt_id="other", generation=IDENTITY["generation"]))
        self.assertIsNone(activity_module.read_sidecar(path, task_id=IDENTITY["taskId"],
                                                       attempt_id=IDENTITY["attemptId"], generation=3))

    def test_nothing_is_written_until_the_exact_root_prompt_binds(self):
        other = self.root / "other-checkout"
        other.mkdir()
        unbound = self.emit([
            [root_session("other", self.root), tool_call(1, "Bash")],
            [{**root_session("child", self.root), "header": {"cwd": str(self.root), "origin": "subagent"}}, user_message(PROMPT)],
            [root_session("wrong-cwd", other), user_message(PROMPT)],
            [root_session("mismatch", self.root), user_message("a different first message")],
        ])
        self.assertFalse(unbound.exists(), "an unbound or foreign session must never produce a sidecar")

    def test_same_phase_updates_are_throttled_but_phase_changes_are_written(self):
        session = root_session("root-2", self.root)
        path = self.emit([[session, user_message(PROMPT)],
                          [session, tool_result(6)],
                          [session, tool_call(7, "Bash")],
                          [session, tool_result(2)]],
                         config={"minIntervalMs": 60000})
        payload = activity_module.read_sidecar(path, task_id=IDENTITY["taskId"],
                                              attempt_id=IDENTITY["attemptId"], generation=IDENTITY["generation"])
        self.assertEqual(payload["phase"], "streaming-model")
        # The coalesced same-phase receipt and the repeated older sequence never
        # made the durable ordinal go backwards.
        self.assertGreaterEqual(payload["eventSeq"], 7)
        self.assertEqual(payload["counts"], {"modelTurns": 1, "toolCalls": 1})

    def test_a_malformed_patch_disables_capture_instead_of_writing_a_partial_sidecar(self):
        for broken in ({"taskId": ""}, {"attemptId": ""}, {"generation": 0}, {"promptSha256": "no"}, {"cwd": ""}):
            with self.subTest(config=broken):
                path = self.root / f"sidecar-{len(json.dumps(broken))}.json"
                session = root_session("root-3", self.root)
                plan = {"config": {"activityPath": str(path), **IDENTITY, "promptSha256": sha256(PROMPT),
                                   "cwd": str(self.root), **broken},
                        "events": [[session, user_message(PROMPT)]]}
                plan_file = self.root / "broken-plan.json"
                plan_file.write_text(json.dumps(plan))
                completed = subprocess.run([shutil.which("node"), str(self.driver), str(PLUGIN), str(plan_file)],
                                           capture_output=True, text=True, timeout=30)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertFalse(path.exists(), broken)


@unittest.skipUnless(shutil.which("node") and RUNNER.is_file(), "Node.js and the dsh runner are required")
class RunnerActivityFlagTests(unittest.TestCase):
    """The private sidecar flag is preflighted before any settings or spawn work."""

    def invoke(self, *flags: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory(prefix="buddy-dsh-runner-flag-") as directory:
            cwd = Path(directory) / "checkout"
            cwd.mkdir()
            task = Path(directory) / "task.md"
            task.write_text("do the bounded thing\n")
            base = ["--cwd", str(cwd), "--task-file", str(task)]
            return subprocess.run([shutil.which("node"), str(RUNNER), *base, *flags],
                                  capture_output=True, text=True, timeout=30)

    def test_activity_file_requires_an_absolute_path(self):
        result = self.invoke("--activity-file", "relative-activity.json")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("absolute path", result.stderr)

    def test_activity_file_requires_a_governed_turn(self):
        with tempfile.TemporaryDirectory(prefix="buddy-dsh-runner-abs-") as directory:
            result = self.invoke("--activity-file", str(Path(directory) / "activity.json"))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("requires a governed turn", result.stderr)


if __name__ == "__main__":
    unittest.main()
