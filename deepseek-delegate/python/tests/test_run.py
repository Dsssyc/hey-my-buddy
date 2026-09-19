"""Real cross-process CLI/C-Two integration tests for the default blocking `buddy run`.

Everything runs against an isolated BUDDY_STATE_DIR and a mock dsh launched through
the real engine; no model is called anywhere. The CLI is invoked exactly as a user
invokes it (``python -m buddy.cli <method> <json>``) from an unrelated working
directory, so the tests also cover the CLI process boundary.
"""
import concurrent.futures
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from buddy.transport import call_service


MOCK_DSH = r'''#!/usr/bin/env node
const fs = require('node:fs');
const log = process.env.MOCK_DSH_LOG;
if (log) fs.appendFileSync(log, JSON.stringify({ pid: process.pid, at: new Date().toISOString() }) + "\n");
const sleep = Number(process.env.MOCK_DSH_SLEEP_MS || 0);
const text = process.env.MOCK_DSH_TEXT || 'mock dsh result';
const code = Number(process.env.MOCK_DSH_EXIT_CODE || 0);
setTimeout(() => { process.stdout.write(text + "\n"); process.exit(code); }, sleep);
'''


class CliCtwoIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-run-", dir="/tmp")
        self.root = Path(self.temp.name)
        self.cwd = self.root / "cwd"
        self.cwd.mkdir()
        self.unrelated_cwd = self.root / "elsewhere"
        self.unrelated_cwd.mkdir()
        self.settings = self.root / "settings.yaml"
        self.settings.write_text("agent-default-model:\n  model: fixture-model\n")
        self.mock = self.root / "dsh"
        self.mock.write_text(MOCK_DSH)
        self.mock.chmod(0o700)
        self.mock_log = self.root / "launches.jsonl"
        self.state = self.root / "state"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("DSH_", "BUDDY_", "MOCK_")) and k not in ["CODEX_APP_TOOLS_PIPE_PATH", "C2_RELAY_ANCHOR_ADDRESS"]}
        self.env.update(
            BUDDY_STATE_DIR=str(self.state), BUDDY_PYTHON=sys.executable, BUDDY_NODE=shutil.which("node"),
            DSH_BIN=str(self.mock), DSH_SETTINGS_FILE=str(self.settings), DSH_HOME=str(self.root / "dsh-home"),
            C2_RELAY_ANCHOR_ADDRESS="", MOCK_DSH_LOG=str(self.mock_log),
        )
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        try:
            call_service("stop", {}, self.state)
        except Exception:
            pass
        self.temp.cleanup()

    # --- CLI process helpers -------------------------------------------------
    def cli(self, method, params, timeout=120):
        process = subprocess.run(
            [sys.executable, "-m", "buddy.cli", method, json.dumps(params)],
            capture_output=True, text=True, env=self.env, cwd=str(self.unrelated_cwd), timeout=timeout,
        )
        body = json.loads(process.stdout) if process.stdout.strip() else None
        return process.returncode, body, process.stderr

    def cli_async(self, method, params):
        process = subprocess.Popen(
            [sys.executable, "-m", "buddy.cli", method, json.dumps(params)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env, cwd=str(self.unrelated_cwd),
        )
        self.processes.append(process)
        return process

    def finish(self, process, timeout=120):
        stdout, stderr = process.communicate(timeout=timeout)
        body = json.loads(stdout) if stdout.strip() else None
        return process.returncode, body, stderr

    def ok(self, method, params, timeout=120):
        code, body, stderr = self.cli(method, params, timeout)
        self.assertEqual(code, 0, f"{method} failed: {body} {stderr}")
        self.assertIsNone(body.get("error"), f"{method} returned an error: {body}")
        return body

    def params(self, request_id, **overrides):
        value = {"requestId": request_id, "task": "mock bounded task", "cwd": str(self.cwd), "workspace": False}
        value.update(overrides)
        return value

    # --- service-side helpers ------------------------------------------------
    def launches(self):
        if not self.mock_log.exists():
            return []
        return [line for line in self.mock_log.read_text().splitlines() if line.strip()]

    def wait_launches(self, count=1, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.launches()) >= count:
                return self.launches()
            time.sleep(0.05)
        raise AssertionError(f"expected {count} dsh launch(es), saw {len(self.launches())}")

    def wait_running(self, request_id, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = self.ok("list", {"limit": 100})
            for run in found["runs"]:
                if run["requestId"] == request_id:
                    return run
            time.sleep(0.1)
        raise AssertionError(f"run for {request_id} never appeared")

    # --- tests --------------------------------------------------------------
    def test_start_idempotency_and_terminal_result_truth(self):
        self.env.update(MOCK_DSH_SLEEP_MS="3000", MOCK_DSH_TEXT="idempotent dsh result")
        first = self.ok("start", self.params("idempotent", timeoutSeconds=120))
        repeated = self.ok("start", self.params("idempotent", timeoutSeconds=120))
        self.assertEqual(first["runId"], repeated["runId"], "the same requestId must recover the same durable run")
        conflict_code, conflict, _ = self.cli("start", {**self.params("idempotent", timeoutSeconds=120), "task": "different task"})
        self.assertEqual(conflict_code, 1)
        self.assertEqual(conflict["error"]["code"], "CONFLICT")
        envelope = self.ok("await", {"runId": first["runId"], "waitSeconds": 60})
        self.assertEqual(envelope["outcome"], "completed", envelope)
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["status"], "completed")
        self.assertTrue(envelope["shutdownConfirmed"])
        self.assertTrue(envelope["resultAvailable"])
        self.assertTrue(envelope["resultDelivered"])
        self.assertEqual(envelope["result"]["finalText"], "idempotent dsh result")
        self.assertTrue(envelope["result"]["processState"]["shutdownConfirmed"])
        self.assertTrue(envelope["logPaths"]["stdout"].endswith("runner.stdout.log"), envelope["logPaths"])
        self.assertIsNone(envelope["recovery"])
        # The durable result stays readable through `buddy result` afterwards.
        full = self.ok("result", {"runId": first["runId"]})
        self.assertEqual(full["result"]["finalText"], "idempotent dsh result")
        self.assertEqual(len(self.launches()), 1, "idempotent start/await must never launch dsh twice")

    def test_one_cli_run_waits_to_completion_inside_the_default_window(self):
        # A runner deadline beyond the old MCP-derived ~55 minute budget still gets a
        # covering CLI wait window, and one invocation returns the real result.
        self.env.update(MOCK_DSH_SLEEP_MS="8000", MOCK_DSH_TEXT="single call result")
        started = time.monotonic()
        envelope = self.ok("run", self.params("one-call", timeoutSeconds=300), timeout=180)
        elapsed = time.monotonic() - started
        self.assertEqual(envelope["outcome"], "completed", envelope)
        self.assertTrue(envelope["ok"])
        self.assertGreater(elapsed, 8)
        self.assertGreater(envelope["waitedSeconds"], 8)
        self.assertEqual(envelope["runnerDeadlineSeconds"], 300)
        self.assertEqual(envelope["waitSeconds"], 360)
        self.assertGreater(envelope["waitSeconds"], 60)
        self.assertEqual(envelope["maxWaitSeconds"], 86400)
        self.assertTrue(envelope["waitCoversRunnerDeadline"])
        self.assertIsNone(envelope["limitation"])
        self.assertEqual(envelope["result"]["finalText"], "single call result")
        self.assertEqual(len(self.launches()), 1)
        print(f"[evidence] one CLI run: wallClock={elapsed:.1f}s waitedSeconds={envelope['waitedSeconds']} "
              f"waitSeconds={envelope['waitSeconds']} runId={envelope['runId']}", flush=True)

    def test_explicit_short_wait_is_recoverable_without_duplicate_launch(self):
        self.env.update(MOCK_DSH_SLEEP_MS="8000", MOCK_DSH_TEXT="late dsh result")
        params = self.params("short-wait", timeoutSeconds=60, waitSeconds=1)
        code, first, stderr = self.cli("run", params, timeout=60)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(first["outcome"], "wait-timeout", first)
        self.assertFalse(first["ok"])
        self.assertTrue(first["timedOut"])
        self.assertEqual(first["status"], "running")
        self.assertIsNone(first["result"])
        self.assertEqual(first["recovery"]["requestId"], "short-wait")
        self.assertEqual(first["recovery"]["runId"], first["runId"])
        commands = " ".join(first["recovery"]["commands"])
        self.assertIn("buddy await", commands)
        self.assertIn(f'"runId":"{first["runId"]}"', commands)
        # The execution deadline is untouched by a wait that ended early.
        status = self.ok("status", {"runId": first["runId"]})
        self.assertEqual(status["timeoutSeconds"], 60)
        self.assertIn(status["status"], ("running", "completing"))
        recovered = self.ok("await", {"runId": first["runId"], "waitSeconds": 60})
        self.assertEqual(recovered["runId"], first["runId"])
        self.assertEqual(recovered["outcome"], "completed", recovered)
        self.assertEqual(recovered["result"]["finalText"], "late dsh result")
        self.assertEqual(len(self.launches()), 1)

    def test_lost_wait_client_does_not_cancel_the_owned_job(self):
        self.env.update(MOCK_DSH_SLEEP_MS="9000", MOCK_DSH_TEXT="survived disconnect")
        pending = self.cli_async("run", self.params("disconnect", timeoutSeconds=120))
        run = self.wait_running("disconnect")
        self.wait_launches(1)
        # Kill the waiting CLI process: losing the wait must only lose the wait.
        pending.send_signal(signal.SIGTERM)
        code, _body, _stderr = self.finish(pending, timeout=30)
        self.assertNotEqual(code, 0)
        still_there = self.ok("status", {"runId": run["runId"]})
        self.assertIn(still_there["status"], ("running", "completing", "completed"))
        recovered = self.ok("await", {"runId": run["runId"], "waitSeconds": 60})
        self.assertEqual(recovered["runId"], run["runId"], "recovery must reuse the same durable run")
        self.assertEqual(recovered["outcome"], "completed", recovered)
        self.assertEqual(recovered["result"]["finalText"], "survived disconnect")
        self.assertEqual(recovered["reconnects"], 0)
        self.assertEqual(len(self.launches()), 1, "recovery must never launch dsh twice")

    def test_concurrent_status_health_list_inquire_and_cancel_stay_responsive(self):
        self.env.update(MOCK_DSH_SLEEP_MS="120000", MOCK_DSH_TEXT="cancelled dsh")
        pending = self.cli_async("run", self.params("concurrent", timeoutSeconds=300))
        run = self.wait_running("concurrent")
        self.wait_launches(1)
        probes = [
            ("health", {}),
            ("status", {"runId": run["runId"]}),
            ("list", {"limit": 10}),
            ("inquire", {"runId": run["runId"]}),
        ]
        latencies = {}
        batch_started = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(probes)) as pool:
            futures = {name: pool.submit(self.cli, name, params, 30) for name, params in probes}
            for name, future in futures.items():
                code, body, stderr = future.result(timeout=30)
                latencies[name] = round(time.monotonic() - batch_started, 3)
                self.assertEqual(code, 0, f"{name} failed: {body} {stderr}")
                self.assertIsNone(body.get("error"), f"{name} returned an error: {body}")
        batch_seconds = round(time.monotonic() - batch_started, 3)
        self.assertLess(batch_seconds, 15, f"concurrent CLI probes must stay responsive: {latencies}")
        inquiry = self.ok("inquire", {"runId": run["runId"]})
        self.assertEqual(inquiry["runId"], run["runId"])
        self.assertEqual(inquiry["execution"]["status"], "running")
        # The mock dsh mounts no inquiry bridge, so the honest answer is "not observed",
        # never a fabricated one - and the run is untouched by being asked.
        self.assertFalse(inquiry["bridge"]["observed"])
        self.assertIsNone(inquiry["inquiry"])
        asked = self.ok("inquire", {"runId": run["runId"], "inquiryId": "q1", "question": "what is blocking you?"})
        self.assertFalse(asked["bridge"]["observed"])
        self.assertFalse(asked["inquiry"]["answer"]["available"])
        self.assertNotEqual(asked["inquiry"]["state"], "answered")
        self.assertEqual(asked["inquiry"]["correlation"], "inquiryId")
        self.assertEqual(asked["inquiry"]["recordedState"], "queued")
        self.assertEqual(asked["inquiry"]["questionPreview"], "what is blocking you?")
        self.assertEqual(self.ok("status", {"runId": run["runId"]})["status"], "running")
        self.assertEqual(self.ok("status", {"runId": run["runId"]})["timeoutSeconds"], 300)
        cancelled = self.ok("cancel", {"runId": run["runId"]})
        self.assertIn(cancelled["status"], ("running", "cancelling"))
        code, envelope, stderr = self.finish(pending, timeout=90)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(envelope["outcome"], "cancelled", envelope)
        self.assertFalse(envelope["ok"])
        self.assertTrue(envelope["shutdownConfirmed"])
        self.assertEqual(envelope["result"]["status"], "cancelled")
        self.assertEqual(len(self.launches()), 1)
        print(f"[evidence] concurrent CLI probes: batchSeconds={batch_seconds} latencies={latencies}", flush=True)

    def test_runner_failure_returns_a_failed_envelope(self):
        self.env.update(MOCK_DSH_SLEEP_MS="0", MOCK_DSH_TEXT="failed dsh", MOCK_DSH_EXIT_CODE="1")
        code, body, stderr = self.cli("run", self.params("failure", timeoutSeconds=60), timeout=60)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(body["outcome"], "failed", body)
        self.assertFalse(body["ok"])
        self.assertTrue(body["shutdownConfirmed"])
        self.assertEqual(body["result"]["status"], "nonzero")
        self.assertEqual(body["result"]["finalText"], "failed dsh")

    def test_runner_timeout_is_reported_as_a_terminal_timeout(self):
        self.env.update(MOCK_DSH_SLEEP_MS="60000", MOCK_DSH_TEXT="too slow")
        envelope = self.ok("run", self.params("runner-timeout", timeoutSeconds=10), timeout=90)
        self.assertEqual(envelope["outcome"], "failed", envelope)
        self.assertEqual(envelope["result"]["status"], "timeout")
        self.assertTrue(envelope["shutdownConfirmed"])
        self.assertIn("timeout after 10s", envelope["result"]["error"])


if __name__ == "__main__":
    unittest.main()
