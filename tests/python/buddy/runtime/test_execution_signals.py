"""Early stops enter the real execution entry and retain the attempt stop intent."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import unittest
from pathlib import Path

from support import BoardTestCase, wait_for

from hey_my_buddy.buddy.runtime.execution import STARTUP, STOP
from hey_my_buddy.buddy.runtime.receipts import receipt_directory
from hey_my_buddy.buddy.runtime.worker import fsync_json
from hey_my_buddy.protocol.client import BoardClient


class EarlyExecutionSignalTests(BoardTestCase):
    def test_stop_during_private_startup_read_keeps_intent_and_starts_no_native_child(self):
        for stage in ("before-read", "after-read"):
            with self.subTest(stage=stage):
                client = BoardClient(self.directory, autostart=False)
                work = self.workdir(f"early-{stage}")
                native_started = work / "native-started"
                script = ("from pathlib import Path\n"
                          f"Path({str(native_started)!r}).write_text('started')\n")
                with self.daemon():
                    task = client.submit(
                        requestId=f"early-{stage}", task="stop during private startup",
                        cwd=str(work), adapter="command", argv=[sys.executable, "-c", script],
                        expectedSeconds=60, requiredCapabilities=["early-signal-fixture"],
                    )["task"]
                    worker = f"early-worker-{stage}"
                    nonce = "n" * 64
                    client.register_worker(worker, adapter="command",
                                           capabilities=["command", "early-signal-fixture"])
                    claim = client.claim(worker, f"claim-{stage}", nonce,
                                         task_id=task["taskId"], worker_instance=f"instance-{stage}")["claim"]
                    self.assertIsNotNone(claim)
                    attempt = claim["attempt"]
                    directory = receipt_directory(self.directory, "command", task["taskId"], attempt["attemptId"])
                    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                    startup = directory / STARTUP
                    fsync_json(startup, {"claim": claim, "stateDir": str(self.directory),
                                         "workerId": worker, "nonce": nonce, "leaseSeconds": 120})
                    marker, release = work / "paused", work / "release"
                    submitted_report = work / "submitted-report.json"
                    wrapper = work / "entry.py"
                    wrapper.write_text(
                        "import json, runpy, time\n"
                        "from pathlib import Path\n"
                        "from hey_my_buddy.buddy.roles import turn_io\n"
                        "from hey_my_buddy import json_codec\n"
                        "from hey_my_buddy.protocol import client\n"
                        f"startup = Path({str(startup)!r})\n"
                        f"marker = Path({str(marker)!r})\n"
                        f"release = Path({str(release)!r})\n"
                        f"submitted_report = Path({str(submitted_report)!r})\n"
                        "class RecordingClient(client.BoardClient):\n"
                        "    def submit_result(self, worker_id, attempt_id, generation, nonce, report):\n"
                        "        submitted_report.write_text(json.dumps(report))\n"
                        "        return super().submit_result(worker_id, attempt_id, generation, nonce, report)\n"
                        "client.BoardClient = RecordingClient\n"
                        "def pause():\n"
                        "    marker.write_text('ready')\n"
                        "    while not release.exists(): time.sleep(.01)\n"
                        "if " + repr(stage) + " == 'before-read':\n"
                        "    original = turn_io.guard_private_path\n"
                        "    def guarded(path, *args, **kwargs):\n"
                        "        if Path(path) == startup: pause()\n"
                        "        return original(path, *args, **kwargs)\n"
                        "    turn_io.guard_private_path = guarded\n"
                        "else:\n"
                        "    original = json_codec.decode_strict_json\n"
                        "    def decoded(data, *args, **kwargs):\n"
                        "        pause()\n"
                        "        return original(data, *args, **kwargs)\n"
                        "    json_codec.decode_strict_json = decoded\n"
                        "runpy.run_module('hey_my_buddy.buddy.runtime.execution', run_name='__main__')\n"
                    )
                    stderr = directory / "entry.stderr.log"
                    with stderr.open("wb") as error_output:
                        process = subprocess.Popen(
                            [sys.executable, str(wrapper), "--startup", str(startup)],
                            cwd=directory, env=self.child_environment(),
                            stdout=subprocess.DEVNULL, stderr=error_output, start_new_session=True,
                        )
                        self.addCleanup(lambda p=process: p.kill() if p.poll() is None else None)
                        self.addCleanup(lambda path=release: path.touch(exist_ok=True))
                        self.assertTrue(wait_for(marker.exists, 20), stderr.read_text(errors="replace"))
                        fsync_json(directory / STOP, {
                            "binding": {key: attempt[key] for key in ("taskId", "attemptId", "generation")},
                            "initiator": "console", "graceDeadline": None, "force": True,
                        })
                        os.kill(process.pid, signal.SIGTERM)
                        release.touch()
                        self.assertEqual(process.wait(timeout=30), 0,
                                         stderr.read_text(errors="replace"))
                    report = json.loads(submitted_report.read_text())
                    self.assertEqual(report["terminationReason"], "console-stop", report)
                    self.assertIs(report["shutdownConfirmed"], True)
                    delivery = json.loads((directory / "result-delivery.json").read_text())
                    self.assertEqual(delivery["attemptId"], attempt["attemptId"])
                    self.assertFalse(native_started.exists())


if __name__ == "__main__":
    unittest.main()
