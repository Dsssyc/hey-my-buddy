"""ADR-031 M1: one private execution process owns one claimed attempt.

These tests use a real private C-Two service and an offline DSH fixture.  A
capability that only the test Worker declares keeps the daemon's managed Worker
from taking the claims before the test launches their execution processes.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import BoardTestCase, wait_for

from hey_my_buddy.buddy.runtime.execution import Execution, launch_execution, stop_execution
from hey_my_buddy.buddy.runtime.receipts import deliver_receipt, write_receipt
from hey_my_buddy.buddy.runtime.worker import Worker
from hey_my_buddy.buddy.harnesses.base import Adapter
from hey_my_buddy.private_dirs import ensure_private_dir
from hey_my_buddy.errors import BoardError
from hey_my_buddy.private_dirs import attempt_root
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.protocol.process_identity import inspect_process_identity, read_native_process_journal


DSH_FIXTURE = Path(__file__).resolve().parents[1] / "harnesses/dsh/fixtures/workflow_agent.py"
PRIVATE_CAPABILITY = "execution-process-fixture"
BUDDY = {"harness": "dsh", "provider": "deepseek-official", "model": "deepseek-flash", "effort": "off"}


def silent(_message: str) -> None:
    pass


class ExecutionProcessTests(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client = BoardClient(self.directory, autostart=False)
        self.environment = {"HMB_MAX_CONCURRENT": "1"}
        self.work = self.workdir()

    def register_and_claim(self, task: dict, *, worker_id: str, adapter: str, nonce: str, instance: str) -> dict:
        self.client.register_worker(worker_id, adapter=adapter, capabilities=[adapter, PRIVATE_CAPABILITY])
        reply = self.client.claim(worker_id, f"claim-{task['taskId']}", nonce,
                                  task_id=task["taskId"], worker_instance=instance)
        self.assertIsNotNone(reply["claim"], reply)
        return reply["claim"]

    def command_claim(self, argv: list[str], *, request_id: str, worker_id: str = "execution-test",
                      nonce: str = "a" * 64, instance: str = "execution-test-instance") -> dict:
        task = self.client.submit(
            requestId=request_id, task="private execution process", cwd=str(self.work),
            adapter="command", argv=argv, expectedSeconds=120,
            requiredCapabilities=[PRIVATE_CAPABILITY],
        )["task"]
        return self.register_and_claim(task, worker_id=worker_id, adapter="command", nonce=nonce, instance=instance)

    def launch(self, claim: dict, *, worker_id: str = "execution-test", nonce: str = "a" * 64):
        # Match the check runner's private child boundary even when the caller is
        # itself a Buddy: the fixture harness must never see a person's HOME.
        with mock.patch.dict(os.environ, self.child_environment(self.environment), clear=True):
            handle = launch_execution(claim, self.directory, worker_id, nonce)
        self.children = getattr(self, "children", [])
        self.children.append(handle.process)
        return handle

    def attempt_directory(self, claim: dict) -> Path:
        return self.directory / "attempts" / claim["task"]["taskId"] / claim["attempt"]["attemptId"]

    def completed(self, claim: dict) -> dict | None:
        view = self.client.get(taskId=claim["task"]["taskId"])
        return view if view["status"] == "completed" else None

    def test_command_completes_reports_and_exits(self):
        marker = self.work / "command-completed.txt"
        with self.daemon(env=self.environment):
            claim = self.command_claim([sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('done')"],
                                       request_id="execution-command")
            handle = self.launch(claim)
            handle.wait(timeout=45)
            self.assertEqual(handle.process.returncode, 0)
            self.assertEqual(marker.read_text(), "done")
            view = wait_for(lambda: self.completed(claim), 15)
            self.assertIsNotNone(view)
            result = self.client.result(taskId=claim["task"]["taskId"])
            self.assertEqual(result["attemptId"], claim["attempt"]["attemptId"])
            self.assertEqual(result["resultMeta"]["terminationReason"], "completed", result)
            directory = self.attempt_directory(claim)
            self.assertFalse((directory / "result-receipt.json").exists())
            self.assertTrue((directory / "result-delivery.json").is_file())

    def test_private_stop_signal_stops_the_native_command_and_delivers_evidence(self):
        started = self.work / "stop-started"
        script = ("from pathlib import Path\nimport time\n"
                  f"Path({str(started)!r}).write_text('ready')\n"
                  "while True: time.sleep(.05)\n")
        with self.daemon(env=self.environment):
            claim = self.command_claim([sys.executable, "-c", script], request_id="execution-private-stop")
            handle = self.launch(claim)
            self.assertTrue(wait_for(started.exists, 20))
            self.client.cancel(taskId=claim["task"]["taskId"], reason="private stop test")
            stop_execution(handle, initiator="unknown")
            handle.wait(timeout=30)
            self.assertEqual(handle.process.returncode, 0)
            result = self.client.result(taskId=claim["task"]["taskId"])
            self.assertEqual(result["resultMeta"]["terminationReason"], "unknown-stop")
            self.assertIs(result["resultMeta"]["shutdownConfirmed"], True)
            self.assertFalse((self.attempt_directory(claim) / "execution-start.json").exists())

    def test_offline_dsh_harness_completes_through_execution_process(self):
        self.catalog_fixture()
        self.environment["HMB_DSH_CLI"] = str(DSH_FIXTURE)
        repository = self.directory / "fixture-repository"
        repository.mkdir(mode=0o700)
        for arguments in (("init", "-q", "-b", "main"),
                          ("config", "user.email", "buddy@example.invalid"),
                          ("config", "user.name", "Buddy Test")):
            completed = subprocess.run(["git", *arguments], cwd=repository, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
        (repository / "tracked.txt").write_text("base\n")
        for arguments in (("add", "."), ("commit", "-q", "-m", "base")):
            completed = subprocess.run(["git", *arguments], cwd=repository, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
        with self.daemon(env=self.environment):
            submitted = self.client.workflow_submit(
                requestId="execution-dsh-fixture", hostId="private-host", task="complete an offline fixture turn",
                cwd=str(repository), executionWorkspace={"kind": "existing", "access": "write"},
                requiredCapabilities=[PRIVATE_CAPABILITY], **BUDDY,
            )
            task = self.client.get(runId=submitted["runId"])
            claim = self.register_and_claim(task, worker_id="execution-dsh", adapter="dsh",
                                            nonce="e" * 64, instance="execution-dsh-instance")
            handle = self.launch(claim, worker_id="execution-dsh", nonce="e" * 64)
            handle.wait(timeout=90)
            self.assertEqual(handle.process.returncode, 0)
            result = self.client.result(runId=submitted["runId"])
            self.assertEqual(result["attemptId"], claim["attempt"]["attemptId"])
            error_log = Path(result["resultMeta"]["logPaths"]["stderr"])
            diagnostic = error_log.read_text(errors="replace")[-4000:] if error_log.exists() else "no runner stderr"
            execution_log = self.attempt_directory(claim) / "execution.stdout.log"
            if execution_log.exists():
                diagnostic += "\nexecution: " + execution_log.read_text(errors="replace")[-4000:]
            native_logs = list(self.directory.rglob("native-stderr.log"))
            for log in native_logs:
                diagnostic += f"\n{log.name}: " + log.read_text(errors="replace")[-4000:]
            self.assertEqual(result["resultMeta"]["terminationReason"], "completed", diagnostic)
            self.assertTrue((result.get("result") or {}).get("nativeSession", {}).get("captured"))
            self.assertEqual((repository / "mock-output.txt").read_text().splitlines(),
                             [f"output for turn {claim['turn']['turnId']}"])

    def test_harness_finishes_during_outage_and_flushes_activity_before_result(self):
        self.catalog_fixture()
        started, release = self.work / "harness-started", self.work / "harness-release"
        wrapper = self.work / "offline-harness.py"
        wrapper.write_text(
            "import importlib.util\nfrom pathlib import Path\nimport time\n"
            f"spec=importlib.util.spec_from_file_location('offline_fixture', {str(DSH_FIXTURE)!r})\n"
            "module=importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n"
            "original=module.WorkflowAgent.prepare_workspace\n"
            "def paused(self, message, turn_input):\n"
            "    original(self, message, turn_input)\n"
            f"    Path({str(started)!r}).write_text('ready')\n"
            f"    while not Path({str(release)!r}).exists(): time.sleep(.05)\n"
            "module.WorkflowAgent.prepare_workspace=paused\n"
            "raise SystemExit(module.main())\n")
        environment = {**self.environment, "HMB_DSH_CLI": str(wrapper)}
        repository = self.directory / "outage-repository"
        repository.mkdir(mode=0o700)
        for arguments in (("init", "-q", "-b", "main"),
                          ("config", "user.email", "buddy@example.invalid"),
                          ("config", "user.name", "Buddy Test")):
            subprocess.run(["git", *arguments], cwd=repository, check=True, capture_output=True)
        (repository / "tracked.txt").write_text("base\n")
        for arguments in (("add", "."), ("commit", "-q", "-m", "base")):
            subprocess.run(["git", *arguments], cwd=repository, check=True, capture_output=True)
        with self.daemon(env=environment) as first:
            submitted = self.client.workflow_submit(
                requestId="execution-harness-outage", hostId="private-host", task="offline outage turn",
                cwd=str(repository), executionWorkspace={"kind": "existing", "access": "write"},
                requiredCapabilities=[PRIVATE_CAPABILITY], **BUDDY)
            task = self.client.get(runId=submitted["runId"])
            claim = self.register_and_claim(task, worker_id="execution-harness-outage", adapter="dsh",
                                            nonce="f" * 64, instance="harness-outage-instance")
            with mock.patch.dict(os.environ, environment):
                handle = self.launch(claim, worker_id="execution-harness-outage", nonce="f" * 64)
            try:
                self.assertTrue(wait_for(started.exists, 30), "native harness did not reach its barrier")
                first.kill()
                first.wait(timeout=15)
            finally:
                release.write_text("go")
        directory = self.attempt_directory(claim)
        self.assertTrue(wait_for((directory / "result-receipt.json").exists, 35))
        self.assertIsNone(handle.process.poll())
        receipt = json.loads((directory / "result-receipt.json").read_text())
        self.assertEqual(receipt["report"]["status"], "ok", receipt)
        with self.daemon(env=environment):
            handle.wait(timeout=45)
            self.assertEqual(handle.process.returncode, 0)
            events = self.client.call("events_read", {"after": 0, "limit": 200})["events"]
            relevant = [event for event in events if event.get("attemptId") == claim["attempt"]["attemptId"]]
            activities = [event for event in relevant if event["kind"] == "attempt.activity"]
            completed = [event for event in relevant if event["kind"] == "task.completed"]
            self.assertTrue(activities, relevant)
            self.assertTrue(completed, relevant)
            self.assertLess(activities[-1]["seq"], completed[-1]["seq"])

    def test_service_downtime_keeps_result_until_restart(self):
        started = self.work / "started"
        release = self.work / "release"
        runs = self.work / "runs"
        script = ("from pathlib import Path\nimport time\n"
                  f"started=Path({str(started)!r}); release=Path({str(release)!r}); runs=Path({str(runs)!r})\n"
                  "runs.write_text(runs.read_text()+'run\\n' if runs.exists() else 'run\\n')\n"
                  "started.write_text('ready')\n"
                  "while not release.exists(): time.sleep(.05)\n"
                  "print('finished after restart')\n")
        with self.daemon(env=self.environment) as first:
            claim = self.command_claim([sys.executable, "-c", script], request_id="execution-restart")
            handle = self.launch(claim)
            self.assertTrue(wait_for(started.exists, 20), "the command did not begin")
            first.kill()
            first.wait(timeout=15)
        release.write_text("go")
        receipt_path = self.attempt_directory(claim) / "result-receipt.json"
        self.assertTrue(wait_for(receipt_path.exists, 35), "execution must retain a durable result during outage")
        self.assertIsNone(handle.process.poll(), "execution must wait to deliver its result")
        with self.daemon(env=self.environment):
            handle.wait(timeout=45)
            self.assertEqual(handle.process.returncode, 0)
            self.assertIsNotNone(wait_for(lambda: self.completed(claim), 20))
            self.assertFalse(receipt_path.exists())
            self.assertEqual(runs.read_text().splitlines(), ["run"])
            result = self.client.result(taskId=claim["task"]["taskId"])
            self.assertEqual(result["attemptId"], claim["attempt"]["attemptId"])

    def test_latest_activity_is_flushed_before_retried_result(self):
        claim = {"task": {"taskId": "task-activity"},
                 "attempt": {"taskId": "task-activity", "attemptId": "attempt-activity", "generation": 1}}
        calls = []

        class IntermittentClient:
            online = False

            def progress(self, *_args, **kwargs):
                calls.append(("activity", self.online, kwargs["data"]))
                if not self.online:
                    raise BoardError("SERVICE_UNAVAILABLE", "offline fixture")

            def submit_result(self, *_args, **_kwargs):
                calls.append(("result", self.online, None))
                if not self.online:
                    self.online = True
                    raise BoardError("SERVICE_UNAVAILABLE", "offline fixture")
                return {"taskState": "completed"}

        client = IntermittentClient()
        execution = Execution(claim, self.directory, "execution-activity", "a" * 64, client=client)
        directory = execution.directory
        directory.mkdir(mode=0o700, parents=True)
        activity = {"phase": "finishing", "eventSeq": 7}
        execution.hint(activity=activity)
        receipt = {"workerId": "execution-activity", "attemptId": "attempt-activity",
                   "taskId": "task-activity", "generation": 1, "nonce": "a" * 64,
                   "commandId": "result:attempt-activity", "report": {"status": "ok", "shutdownConfirmed": True}}
        write_receipt(directory, receipt)
        self.assertTrue(deliver_receipt(client, receipt, directory, flush_hints=execution.flush_hints))
        self.assertEqual(calls, [("activity", False, {"activity": activity}),
                                 ("result", False, None),
                                 ("activity", True, {"activity": activity}),
                                 ("result", True, None)])

    def test_validation_refusal_ends_delivery_without_retry(self):
        client = mock.Mock()
        client.submit_result.side_effect = [BoardError("INVALID_ARGUMENT", "invalid report"), {}]
        directory = self.work / "refused-receipt"
        receipt = {"workerId": "worker", "attemptId": "attempt", "taskId": "task", "generation": 1,
                   "nonce": "n" * 64, "report": {"status": "invalid", "shutdownConfirmed": False}, "commandId": "result:attempt"}
        write_receipt(directory, receipt)
        self.assertFalse(deliver_receipt(client, receipt, directory))
        self.assertEqual(client.submit_result.call_count, 1)
        self.assertFalse((directory / "result-receipt.json").exists())
        self.assertFalse(json.loads((directory / "result-delivery.json").read_text())["accepted"])

    def test_live_observation_error_cannot_disable_native_stop(self):
        claim = {"task": {"taskId": "task-live-error"},
                 "attempt": {"taskId": "task-live-error", "attemptId": "attempt-live-error", "generation": 1}}
        execution = Execution(claim, self.directory, "worker", "n" * 64, client=mock.Mock())
        observed, stopped = threading.Event(), threading.Event()
        handle = mock.Mock()
        def unavailable(*_args, **_kwargs):
            observed.set()
            raise OSError("live endpoint unavailable")
        with mock.patch("hey_my_buddy.buddy.roles.live.handle_live_binding", side_effect=unavailable), \
             mock.patch("hey_my_buddy.buddy.runtime.execution.role_seam.worker_stop",
                        side_effect=lambda *_args, **_kwargs: stopped.set()):
            execution.control.attach(handle, mock.Mock())
            execution.control.start()
            try:
                self.assertTrue(observed.wait(2))
                execution.request_stop("console")
                self.assertTrue(stopped.wait(2), "live observation failure disabled stop handling")
            finally:
                execution.control.stop()
                execution.control.join(3)
        self.assertFalse(execution.control.is_alive())

    def test_summary_endpoint_rebind_retries_after_a_failed_withdrawal(self):
        from hey_my_buddy.buddy.runtime.worker import _Renewal
        from hey_my_buddy.protocol.run_identity import RunIdentity
        original = RunIdentity(task_id="task", attempt_id="attempt", generation=1, invocation_id="first")
        summary = original.model_copy(update={"invocation_id": "summary"})
        handle = SimpleNamespace(role_run_identity=original, role_live_channel=mock.Mock())
        worker = SimpleNamespace(state_dir=self.directory, live=mock.Mock(), _nonce=lambda: "n" * 64)
        worker.live.bind.side_effect = [False, True]
        claim = {"attempt": {"attemptId": "attempt", "generation": 1}}
        renewal = _Renewal(worker, claim, handle, None)
        def binding(held, **_kwargs):
            held.role_run_identity = summary
            if held.role_live_channel is None:
                held.role_live_channel = mock.Mock()
            return "bound", held.role_live_channel
        with mock.patch("hey_my_buddy.buddy.roles.live.handle_live_binding", side_effect=binding):
            renewal._refresh_live_binding()
            self.assertTrue(renewal._live_rebind)
            renewal._refresh_live_binding()
        self.assertEqual(worker.live.unbind.call_count, 2)
        self.assertEqual(worker.live.bind.call_count, 2)
        self.assertFalse(renewal._live_rebind)
        self.assertEqual(renewal._live_identity, summary)

    def test_preparation_refusal_removes_materialized_attempt_credential(self):
        credential = []
        class RefusingAdapter(Adapter):
            def prepare(self, context):
                path = context.credential_file()
                ensure_private_dir(path.parent)
                path.write_text("private bearer")
                path.chmod(0o600)
                credential.append(path)
                raise BoardError("WORKSPACE_CHANGED", "workspace changed after credential staging")
        claim = {"task": {"taskId": "task-refused-prepare", "spec": {"adapter": "command"}},
                 "attempt": {"taskId": "task-refused-prepare", "attemptId": "attempt-refused-prepare", "generation": 1}}
        execution = Execution(claim, self.directory, "worker", "n" * 64, client=mock.Mock())
        with mock.patch("hey_my_buddy.buddy.runtime.execution.role_seam.worker_executor", return_value=RefusingAdapter()):
            receipt = execution.execute()
        self.assertEqual(receipt["report"]["status"], "failed")
        self.assertIs(receipt["report"]["shutdownConfirmed"], True)
        self.assertEqual(len(credential), 1)
        self.assertFalse(credential[0].exists(), "a finished preparation left its bearer credential on disk")

    def test_refused_result_ends_process_and_records_refusal(self):
        with self.daemon(env=self.environment):
            claim = self.command_claim([sys.executable, "-c", "print('finished')"],
                                       request_id="execution-refusal")
            directory = self.attempt_directory(claim)
            handle = self.launch(claim, nonce="b" * 64)
            handle.wait(timeout=45)
            self.assertIsNotNone(handle.process.poll(), "a refused result must end retrying")
            self.assertFalse((directory / "result-receipt.json").exists())
            delivery = json.loads((directory / "result-delivery.json").read_text())
            self.assertIs(delivery["accepted"], False)
            self.assertEqual(self.client.get(taskId=claim["task"]["taskId"])["status"], "running")

    def test_worker_delivers_durable_receipt_after_execution_crash(self):
        started = self.work / "crash-started"
        release = self.work / "crash-release"
        script = ("from pathlib import Path\nimport time\n"
                  f"Path({str(started)!r}).write_text('ready')\n"
                  f"while not Path({str(release)!r}).exists(): time.sleep(.05)\n"
                  "print('finished')\n")
        worker = Worker("execution-fallback", self.directory, client=self.client, log=silent)
        nonce = "c" * 64
        with self.daemon(env=self.environment) as first:
            claim = self.command_claim([sys.executable, "-c", script], request_id="execution-fallback-receipt",
                                       worker_id=worker.worker_id, nonce=nonce, instance=worker.instance_id)
            handle = self.launch(claim, worker_id=worker.worker_id, nonce=nonce)
            self.assertTrue(wait_for(started.exists, 20))
            first.kill()
            first.wait(timeout=15)
        release.write_text("go")
        receipt_path = self.attempt_directory(claim) / "result-receipt.json"
        self.assertTrue(wait_for(receipt_path.exists, 35))
        handle.process.kill()
        handle.wait(timeout=15)
        worker.spool.write_startup({"workerId": worker.worker_id, "instanceId": worker.instance_id,
                                    "nonce": nonce, "claimRequestId": f"claim-{claim['task']['taskId']}",
                                    "attemptId": claim["attempt"]["attemptId"],
                                    "taskId": claim["task"]["taskId"],
                                    "generation": claim["attempt"]["generation"]})
        with self.daemon(env=self.environment):
            receipt = worker.execute(claim)
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt["report"]["status"], "ok")
            self.assertIsNotNone(wait_for(lambda: self.completed(claim), 20))
            self.assertFalse(receipt_path.exists())

    def test_worker_reports_crash_without_receipt(self):
        started = self.work / "no-receipt-started"
        release = self.work / "no-receipt-release"
        self.addCleanup(lambda: release.write_text("go"))
        script = ("from pathlib import Path\nimport time\n"
                  f"Path({str(started)!r}).write_text('ready')\n"
                  f"while not Path({str(release)!r}).exists(): time.sleep(.05)\n")
        worker = Worker("execution-no-receipt", self.directory, client=self.client, log=silent)
        nonce = "d" * 64
        with self.daemon(env=self.environment):
            claim = self.command_claim([sys.executable, "-c", script], request_id="execution-fallback-missing",
                                       worker_id=worker.worker_id, nonce=nonce, instance=worker.instance_id)
            handle = self.launch(claim, worker_id=worker.worker_id, nonce=nonce)
            self.assertTrue(wait_for(started.exists, 20))
            handle.process.kill()
            handle.wait(timeout=15)
            self.assertFalse((self.attempt_directory(claim) / "result-receipt.json").exists())
            worker.spool.write_startup({"workerId": worker.worker_id, "instanceId": worker.instance_id,
                                        "nonce": nonce, "claimRequestId": f"claim-{claim['task']['taskId']}",
                                        "attemptId": claim["attempt"]["attemptId"],
                                        "taskId": claim["task"]["taskId"],
                                        "generation": claim["attempt"]["generation"]})
            receipt = worker._settle_execution(claim, handle)
            self.assertEqual(receipt["report"]["status"], "failed")
            self.assertIs(receipt["report"]["shutdownConfirmed"], False)
            view = self.client.get(taskId=claim["task"]["taskId"])
            self.assertEqual(view["status"], "failed")
            private = attempt_root(self.directory, "command", claim["task"]["taskId"],
                                   claim["attempt"]["attemptId"])
            journals = list(private.glob("native-process-journal-*.json"))
            self.assertEqual(len(journals), 1)
            identity = read_native_process_journal(journals[0], {
                "taskId": claim["task"]["taskId"], "attemptId": claim["attempt"]["attemptId"],
                "generation": claim["attempt"]["generation"]})["identities"][0]
            release.write_text("go")
            self.assertTrue(wait_for(lambda: inspect_process_identity(identity) == "identity-absent", 15),
                            "the independently owned command must exit before the private test root is removed")


if __name__ == "__main__":
    unittest.main()
