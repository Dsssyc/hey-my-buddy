"""Direct service admission shares one total ceiling and releases finished slots."""
from __future__ import annotations

import sys
import time
import os
import signal
from datetime import datetime, timezone
import psutil
from pathlib import Path

from support import BoardTestCase, wait_for
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.protocol.process_identity import inspect_process_identity


class DirectAdmissionTests(BoardTestCase):
    def submit(self, board, name: str, *, cwd: Path | None = None, exclusive=None):
        params = {"requestId": name, "task": "offline business work", "cwd": str(cwd or self.workdir(name)),
                  "adapter": "command", "argv": ["/bin/true"]}
        if exclusive is not None:
            params["exclusiveResources"] = exclusive
        return board.call("task_submit", params)["task"]

    def test_total_capacity_and_workspace_resource_admission(self):
        board = self.board(max_concurrent=2)
        first = self.submit(board, "first")
        second = self.submit(board, "second")
        third = self.submit(board, "third")
        one = board.store.admit_execution("n" * 32, task_id=first["runId"])["claim"]
        two = board.store.admit_execution("m" * 32, task_id=second["runId"])["claim"]
        self.assertEqual({one["task"]["runId"], two["task"]["runId"]},
                         {first["runId"], second["runId"]})
        self.assertEqual(board.store.capacity_report()["totalActive"], 2)
        refused = board.store.admit_execution("o" * 32, task_id=third["runId"])
        self.assertIsNone(refused["claim"])
        self.assertEqual(refused["reason"], "capacity")
        board.store.release_execution(one["attempt"]["attemptId"])
        self.assertEqual(board.store.admit_execution("p" * 32, task_id=third["runId"])["claim"]["task"]["runId"],
                         third["runId"])

    def test_exclusive_resource_prevents_overlap_with_a_free_machine_slot(self):
        board = self.board(max_concurrent=2)
        first = self.submit(board, "resource-one", exclusive=["gpu:0"])
        second = self.submit(board, "resource-two", exclusive=["gpu:0"])
        board.store.admit_execution("n" * 32, task_id=first["runId"])
        refused = board.store.admit_execution("m" * 32, task_id=second["runId"])
        self.assertIsNone(refused["claim"])
        self.assertEqual(refused["reason"], "exclusive-resource")


class ModelServiceAdmissionTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        from protocol.fixtures import mock_readonly
        mock_readonly.install(self)

    def test_full_model_service_does_not_hide_command_work(self):
        board = self.board(max_concurrent=3)
        board.call("model_catalog_refresh", {"requestId": "catalog-seed"})
        model = {"harness": "dsh", "provider": "deepseek-official", "model": "deepseek-flash"}
        buddy_id = "dsh:deepseek-official:deepseek-flash:off"
        board.console_call("user_policy_publish", {
            "commandId": "seed-policy",
            "buddySettings": [{"buddyId": buddy_id, "enabled": True},
                              {"buddyId": "dsh:deepseek-official:deepseek-v4-pro:off", "enabled": True}],
            "configuration": {"defaultRoutingMode": "review", "routerBuddyIds": [buddy_id]},
            "modelServiceConcurrency": [{**model, "limit": 1}],
        })
        first = board.call("selection_request", {"requestId": "decision-one", "task": "pick"})
        board.store.admit_execution("n" * 32, task_id=first["runId"])
        second = board.call("selection_request", {"requestId": "decision-two", "task": "pick again"})
        refused = board.store.admit_execution("m" * 32, task_id=second["runId"])
        self.assertIsNone(refused["claim"])
        self.assertEqual(refused["reason"], "model-capacity")
        command = board.call("task_submit", {
            "requestId": "command-free", "task": "offline command",
            "cwd": str(self.workdir("command-free")), "adapter": "command", "argv": ["/bin/true"],
        })["task"]
        claim = board.store.admit_execution("p" * 32)["claim"]
        self.assertEqual(claim["task"]["runId"], command["runId"])

    def test_uncertain_deep_model_attempt_holds_model_and_machine_capacity(self):
        board = self.board(max_concurrent=2)
        board.call("model_catalog_refresh", {"requestId": "catalog-deep"})
        deep = {"harness": "dsh", "provider": "deepseek-official", "model": "deepseek-v4-pro"}
        board.console_call("user_policy_publish", {
            "commandId": "deep-limit",
            "buddySettings": [
                {"buddyId": "dsh:deepseek-official:deepseek-v4-pro:off", "enabled": True},
                {"buddyId": "dsh:deepseek-official:deepseek-flash:off", "enabled": True},
            ],
            "configuration": {"defaultRoutingMode": "review", "routerBuddyIds": [
                "dsh:deepseek-official:deepseek-v4-pro:off"]},
            "modelServiceConcurrency": [{**deep, "limit": 1}],
        })
        first = board.call("selection_request", {"requestId": "deep-first", "task": "choose"})
        claimed = board.store.admit_execution("a" * 32, task_id=first["runId"])["claim"]
        attempt_id = claimed["attempt"]["attemptId"]
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET execution_state='uncertain', ownership='uncertain' "
                               "WHERE attempt_id=?", (attempt_id,))
            connection.execute("UPDATE resource_claims SET state='retained' WHERE attempt_id=?", (attempt_id,))
        second = board.call("selection_request", {"requestId": "deep-second", "task": "choose again"})
        blocked = board.store.admit_execution("b" * 32, task_id=second["runId"])
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "model-capacity")
        self.assertEqual(board.call("task_get", {"runId": second["runId"]})["task"]["queueReason"],
                         "model-capacity")
        command = board.call("task_submit", {"requestId": "deep-other", "task": "offline", "adapter": "command",
                                                  "argv": ["/bin/true"], "cwd": str(self.workdir("deep-other"))})["task"]
        self.assertIsNotNone(board.store.admit_execution("c" * 32, task_id=command["runId"])["claim"])
        third = board.call("task_submit", {"requestId": "deep-third", "task": "offline", "adapter": "command",
                                                "argv": ["/bin/true"], "cwd": str(self.workdir("deep-third"))})["task"]
        blocked_total = board.store.admit_execution("d" * 32, task_id=third["runId"])
        self.assertEqual(blocked_total["reason"], "capacity")
        capacity = board.store.capacity_report()
        self.assertEqual(capacity["totalActive"], 2)
        self.assertEqual(next(row for row in capacity["models"] if all(row[key] == value
                         for key, value in deep.items()))["active"], 1)


class PrivateDaemonDispatchTests(BoardTestCase):
    @staticmethod
    def native_stopped(pid: int) -> bool:
        try:
            return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return True

    def gated(self, client: BoardClient, name: str):
        started = self.directory / f"{name}.started"
        pid_path = self.directory / f"{name}.pid"
        release = self.directory / f"{name}.release"
        script = ("import os,pathlib,time\n"
                  f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()))\n"
                  f"pathlib.Path({str(started)!r}).touch()\n"
                  f"release=pathlib.Path({str(release)!r})\n"
                  "while not release.exists(): time.sleep(.05)\n")
        task = client.submit(requestId=name, task="hold one execution", cwd=str(self.workdir(name)),
                             adapter="command", argv=[sys.executable, "-c", script],
                             expectedSeconds=120)["task"]
        return task["runId"], started, release

    def test_command_processes_use_free_slots_then_complete(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "2"}):
            client = BoardClient(self.directory, autostart=False)
            first, first_started, first_release = self.gated(client, "first")
            second, second_started, second_release = self.gated(client, "second")
            self.assertTrue(wait_for(lambda: first_started.exists() and second_started.exists(), timeout=45),
                            (self.directory / "test-daemon.log").read_text()[-3000:])
            self.assertEqual(client.health()["capacity"]["totalActive"], 2)
            third, third_started, third_release = self.gated(client, "third")
            self.assertEqual(client.get(runId=third)["queueReason"], "capacity")
            first_release.touch()
            self.assertTrue(wait_for(lambda: third_started.exists(), timeout=45),
                            (self.directory / "test-daemon.log").read_text()[-3000:])
            second_release.touch()
            third_release.touch()
            for run_id in (first, second, third):
                self.assertTrue(wait_for(lambda: client.get(runId=run_id)["status"] == "completed", timeout=45),
                                (run_id, client.get(runId=run_id)))
            self.assertEqual(client.health()["capacity"]["totalActive"], 0)

    def test_stop_reaches_the_execution_and_confirms_shutdown(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            client = BoardClient(self.directory, autostart=False)
            run_id, started, _release = self.gated(client, "stop-running")
            self.assertTrue(wait_for(started.exists, timeout=45))
            client.cancel(runId=run_id)
            self.assertTrue(wait_for(lambda: client.get(runId=run_id)["status"] == "cancelled",
                                     timeout=35), client.get(runId=run_id))
            self.assertTrue(client.get(runId=run_id)["shutdownConfirmed"])

    def test_zero_drain_service_stop_reaches_connected_execution(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}) as first:
            client = BoardClient(self.directory, autostart=False)
            run_id, started, release = self.gated(client, "zero-drain")
            self.assertTrue(wait_for(started.exists, timeout=45))
            self.assertIsNotNone(wait_for(lambda: (client.get(runId=run_id).get("selectedAttempt") or {}).get(
                "processIdentity"), timeout=20))
            attempt_id = client.get(runId=run_id)["selectedAttemptId"]
            try:
                reply = client.call("service_control", {"action": "stop", "drainSeconds": 0})
                self.assertEqual(reply["cancelRequested"], 1)
                first.wait(timeout=20)
                self.assertTrue(wait_for(lambda: self.native_stopped(int((self.directory / "zero-drain.pid").read_text())),
                                         timeout=12), "zero-drain stop never reached the running command")
            finally:
                release.touch()
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            client = BoardClient(self.directory, autostart=False)
            self.assertTrue(wait_for(lambda: client.get(runId=run_id)["attemptState"] == "finished",
                                     timeout=35), client.get(runId=run_id))
            view = client.get(runId=run_id)
            self.assertEqual(view["selectedAttemptId"], attempt_id)
            self.assertEqual(view["status"], "cancelled")
            self.assertTrue(view["shutdownConfirmed"])
            self.assertEqual(view["stopInitiator"], "service")

    def test_restart_retains_execution_receipt_delivery(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}) as first:
            client = BoardClient(self.directory, autostart=False)
            run_id, started, release = self.gated(client, "restart-running")
            self.assertTrue(wait_for(started.exists, timeout=45))
            attempt_id = client.get(runId=run_id)["selectedAttemptId"]
            reply = client.call("service_control", {"action": "restart"})
            self.assertTrue(reply["restarting"])
            first.wait(timeout=15)
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            client = BoardClient(self.directory, autostart=False)
            self.assertEqual(client.get(runId=run_id)["selectedAttemptId"], attempt_id)
            release.touch()
            self.assertTrue(wait_for(lambda: client.get(runId=run_id)["status"] == "completed",
                                     timeout=45), client.get(runId=run_id))

    def test_execution_death_does_not_report_stopped_until_native_work_ends(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            client = BoardClient(self.directory, autostart=False)
            run_id, started, _release = self.gated(client, "death-running")
            self.assertTrue(wait_for(started.exists, timeout=45))
            def published():
                return (client.get(runId=run_id).get("selectedAttempt") or {}).get("processIdentity")
            identity = wait_for(published, timeout=20)
            self.assertIsNotNone(identity)
            os.kill(identity["pid"], signal.SIGKILL)
            self.assertTrue(wait_for(lambda: client.get(runId=run_id)["attemptState"] == "finished",
                                     timeout=35), client.get(runId=run_id))
            view = client.get(runId=run_id)
            self.assertTrue(view["shutdownConfirmed"])
            native_pid = int((self.directory / "death-running.pid").read_text())
            self.assertTrue(wait_for(lambda: self.native_stopped(native_pid), timeout=10))

    def test_frozen_command_is_forced_after_the_stop_grace(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            client = BoardClient(self.directory, autostart=False)
            started = self.directory / "frozen.started"
            pid_path = self.directory / "frozen.pid"
            script = ("import os,pathlib,signal,time\n"
                      "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                      f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()))\n"
                      f"pathlib.Path({str(started)!r}).touch()\n"
                      "while True: time.sleep(1)\n")
            run_id = client.submit(requestId="frozen", task="ignore stop", cwd=str(self.workdir("frozen")),
                                   adapter="command", argv=[sys.executable, "-c", script],
                                   expectedSeconds=120)["task"]["runId"]
            self.assertTrue(wait_for(started.exists, timeout=45))
            execution_pid = client.get(runId=run_id)["selectedAttempt"]["processIdentity"]["pid"]
            client.cancel(runId=run_id)
            self.assertTrue(wait_for(lambda: client.get(runId=run_id)["attemptState"] == "finished",
                                     timeout=25), client.get(runId=run_id))
            view = client.get(runId=run_id)
            self.assertEqual(view["status"], "cancelled")
            self.assertTrue(view["shutdownConfirmed"])
            self.assertTrue(wait_for(lambda: self.native_stopped(int(pid_path.read_text())), timeout=10))
            self.assertTrue(wait_for(lambda: self.native_stopped(execution_pid), timeout=10))

    def test_stopped_execution_is_killed_from_the_recorded_force_time(self):
        if not hasattr(signal, "SIGSTOP"):
            self.skipTest("SIGSTOP is unavailable")
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            client = BoardClient(self.directory, autostart=False)
            run_id, started, release = self.gated(client, "freeze-execution")
            self.assertTrue(wait_for(started.exists, timeout=45))
            identity = wait_for(lambda: (client.get(runId=run_id).get("selectedAttempt") or {}).get(
                "processIdentity"), timeout=20)
            self.assertIsNotNone(identity)
            try:
                os.kill(identity["pid"], signal.SIGSTOP)
                client.cancel(runId=run_id)
                force_at = wait_for(lambda: (client.get(runId=run_id).get("selectedAttempt") or {}).get(
                    "forceRequestedAt"), timeout=15)
                self.assertIsNotNone(force_at)
                recorded = datetime.fromisoformat(force_at.replace("Z", "+00:00"))
                self.assertLess((datetime.now(timezone.utc) - recorded).total_seconds(), 5)
                self.assertNotEqual(client.get(runId=run_id)["attemptState"], "finished")
                self.assertTrue(wait_for(lambda: client.get(runId=run_id)["attemptState"] == "finished",
                                         timeout=20), client.get(runId=run_id))
                view = client.get(runId=run_id)
                self.assertEqual(view["status"], "cancelled")
                self.assertTrue(view["shutdownConfirmed"])
                elapsed = (datetime.now(timezone.utc) - recorded).total_seconds()
                self.assertGreaterEqual(elapsed, 7)
                self.assertLess(elapsed, 12, 'forced execution outlived its persisted external grace')
                native_pid = int((self.directory / 'freeze-execution.pid').read_text())
                self.assertTrue(wait_for(lambda: self.native_stopped(native_pid), timeout=3))
            finally:
                release.touch()
                if inspect_process_identity(identity) == "alive":
                    os.kill(identity["pid"], signal.SIGCONT)
