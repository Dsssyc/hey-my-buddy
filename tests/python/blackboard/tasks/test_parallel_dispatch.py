"""Direct service admission shares one total ceiling and releases finished slots."""
from __future__ import annotations

import sys
import time
import os
import signal
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from itertools import islice

import c_two as cc
import psutil
from pathlib import Path

from support import BoardTestCase, request_private_service_stop, wait_for
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.protocol import rpc_config, transport
from hey_my_buddy.protocol.contracts import BuddyControl, CONTROL_NAME
from hey_my_buddy.protocol.process_identity import inspect_process_identity
from hey_my_buddy.private_dirs import attempt_root


class DirectAdmissionTests(BoardTestCase):
    def submit(self, board, name: str, *, cwd: Path | None = None, exclusive=None):
        params = {"requestId": name, "task": "offline business work", "cwd": str(cwd or self.workdir(name)),
                  "adapter": "command", "argv": ["/bin/true"]}
        if exclusive is not None:
            params["exclusiveResources"] = exclusive
        return board.submit_execution(**params)["task"]

    def test_total_capacity_and_workspace_resource_admission(self):
        board = self.board(max_concurrent=2)
        first = self.submit(board, "first")
        second = self.submit(board, "second")
        third = self.submit(board, "third")
        one = board.store.admit_execution("n" * 32, task_id=first["runId"])["claim"]
        two = board.store.admit_execution("m" * 32, task_id=second["runId"])["claim"]
        self.assertEqual({one["task"]["taskId"], two["task"]["taskId"]},
                         {first["runId"], second["runId"]})
        self.assertEqual(board.store.capacity_report()["totalActive"], 2)
        refused = board.store.admit_execution("o" * 32, task_id=third["runId"])
        self.assertIsNone(refused["claim"])
        self.assertEqual(refused["reason"], "capacity")
        board.store.release_execution(one["attempt"]["attemptId"], reason="execution-process-not-created")
        self.assertEqual(board.store.admit_execution("p" * 32, task_id=third["runId"])["claim"]["task"]["taskId"],
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
        command = board.submit_execution(**{
            "requestId": "command-free", "task": "offline command",
            "cwd": str(self.workdir("command-free")), "adapter": "command", "argv": ["/bin/true"],
        })["task"]
        claim = board.store.admit_execution("p" * 32)["claim"]
        self.assertEqual(claim["task"]["taskId"], command["runId"])

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
        command = board.submit_execution(**{"requestId": "deep-other", "task": "offline", "adapter": "command",
                                                  "argv": ["/bin/true"], "cwd": str(self.workdir("deep-other"))})["task"]
        self.assertIsNotNone(board.store.admit_execution("c" * 32, task_id=command["runId"])["claim"])
        third = board.submit_execution(**{"requestId": "deep-third", "task": "offline", "adapter": "command",
                                                "argv": ["/bin/true"], "cwd": str(self.workdir("deep-third"))})["task"]
        blocked_total = board.store.admit_execution("d" * 32, task_id=third["runId"])
        self.assertEqual(blocked_total["reason"], "capacity")
        capacity = board.store.capacity_report()
        self.assertEqual(capacity["totalActive"], 2)
        self.assertEqual(next(row for row in capacity["models"] if all(row[key] == value
                         for key, value in deep.items()))["active"], 1)


class PrivateDaemonDispatchTests(BoardTestCase):
    def execution_diagnostics(self, runs: dict[str, Path]) -> str:
        """Read bounded failure evidence without changing the private board."""
        def capture(read):
            try:
                return read()
            except Exception as error:
                return {"diagnosticError": f"{type(error).__name__}: {error}"[:2048]}

        def file_tail(path):
            try:
                with path.open("rb") as stream:
                    stat = os.fstat(stream.fileno())
                    stream.seek(max(0, stat.st_size - 4096))
                    return {"exists": True, "mtimeNs": stat.st_mtime_ns, "size": stat.st_size,
                            "tail": stream.read(4096).decode("utf-8", errors="replace")}
            except FileNotFoundError:
                return {"exists": False}

        def native_journals(directory):
            journals = {path.name: capture(lambda: file_tail(path))
                        for path in islice(directory.glob("native-process-journal-*.json"), 4)}
            return {"exists": bool(journals), "files": journals}

        def read_rpc(operation, params):
            rpc_config.configure_client(self.directory, create=False)
            endpoint = transport._read_endpoint(self.directory)
            if endpoint is None:
                raise RuntimeError("Private service endpoint is unavailable")
            with cc.connect(BuddyControl, name=CONTROL_NAME, address=endpoint["address"], timeout=2) as peer:
                with cc.with_call_options(peer, timeout=2) as bounded:
                    return transport._invoke(bounded, endpoint, operation, params)

        client = BoardClient(self.directory, call=read_rpc, autostart=False)

        def task_get(run_id):
            view = client.get(runId=run_id)
            # Keep the mechanical state, excluding command text and result output.
            facts = {key: view.get(key) for key in (
                "runId", "state", "attemptState", "queueReason", "selectedAttemptId",
                "activeAttemptId", "shutdownConfirmed", "cancelRequested", "terminationReason")}
            attempt = view.get("selectedAttempt")
            facts["selectedAttempt"] = ({key: attempt.get(key) for key in (
                "attemptId", "generation", "executionState", "ownership", "processIdentity",
                "startedAt", "updatedAt", "finishedAt", "shutdownConfirmed", "cancelRequestedAt",
                "stopInitiator", "graceDeadline", "forceRequestedAt", "forceReason", "exitCode", "signal")}
                                        if attempt is not None else None)
            if attempt is not None:
                error = attempt.get("error")
                facts["selectedAttempt"]["error"] = error[:2048] if error is not None else None
                facts["selectedAttempt"]["issues"] = [
                    {**issue, "message": issue["message"][:2048]} for issue in attempt["issues"][:32]]
            return facts

        details = {"capturedAt": datetime.now(timezone.utc).isoformat(),
                   "limits": {"events": 32, "issues": 32, "attemptsPerRun": 4,
                              "journalsPerAttempt": 4, "fileTailBytes": 4096,
                              "rpcConnectSeconds": 2, "rpcCallSeconds": 2},
                   "runs": {run_id: {"taskGet": capture(lambda: task_get(run_id)),
                                     "started": capture(lambda: file_tail(started)), "attempts": {}}
                            for run_id, started in runs.items()}}
        bindings = {run_id: set() for run_id in runs}
        for run_id, facts in details["runs"].items():
            attempt = facts["taskGet"].get("selectedAttempt")
            if attempt is not None:
                bindings[run_id].add(attempt["attemptId"])

        def database_facts():
            with closing(sqlite3.connect((self.directory / "board.sqlite3").as_uri() + "?mode=ro",
                                         uri=True, timeout=.2)) as connection:
                connection.row_factory = sqlite3.Row

                def rows(sql, params=()):
                    return [dict(row) for row in connection.execute(sql, params)]

                details["serviceIssues"] = capture(lambda: rows(
                    "SELECT stage,code,substr(message,1,2048) AS message,observed_at"
                    " FROM service_issues ORDER BY stage LIMIT 32"))
                details["recentEvents"] = capture(lambda: rows(
                    "SELECT seq,task_id,attempt_id,kind,substr(payload_json,1,2048) AS payload_json,created_at"
                    " FROM events ORDER BY seq DESC LIMIT 32"))
                for run_id, facts in details["runs"].items():
                    attempts = capture(lambda: rows(
                        "SELECT attempt_id,generation,execution_state,ownership,identity_confirmed_at,"
                        " started_at,updated_at,finished_at FROM attempts WHERE task_id=?"
                        " ORDER BY generation DESC LIMIT 4", (run_id,)))
                    facts["storedAttempts"] = attempts
                    if isinstance(attempts, list):
                        bindings[run_id].update(row["attempt_id"] for row in attempts)
                    facts["executionIssues"] = capture(lambda: rows(
                        "SELECT e.attempt_id,e.stage,e.code,substr(e.message,1,2048) AS message,e.observed_at"
                        " FROM execution_issues e JOIN attempts a ON a.attempt_id=e.attempt_id"
                        " WHERE a.task_id=? ORDER BY a.generation DESC,e.stage LIMIT 32", (run_id,)))

        database_error = capture(database_facts)
        if database_error is not None:
            details["database"] = database_error
        for run_id, attempt_ids in bindings.items():
            for attempt_id in sorted(attempt_ids)[:4]:
                evidence = self.directory / "attempts" / run_id / attempt_id
                private = attempt_root(self.directory, "command", run_id, attempt_id)
                facts = {name: capture(lambda: file_tail(evidence / name)) for name in (
                    "spawn.intent", "spawn.marker", "execution.stdout.log", "execution.stderr.log")}
                facts["nativeJournals"] = capture(lambda: native_journals(private))
                details["runs"][run_id]["attempts"][attempt_id] = facts
        details["daemonLog"] = capture(lambda: file_tail(self.directory / "test-daemon.log"))
        return json.dumps(details, ensure_ascii=False, indent=2)

    def wait_for_execution(self, predicate, runs: dict[str, Path], *, timeout: float):
        value = wait_for(predicate, timeout=timeout)
        if not value:
            try:
                evidence = self.execution_diagnostics(runs)
            except Exception as error:
                evidence = f"Diagnostic collection failed: {type(error).__name__}: {error}"[:2048]
            self.fail(f"Execution wait timed out after {timeout}s\n{evidence}")
        return value

    @staticmethod
    def native_stopped(pid: int) -> bool:
        try:
            return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return True

    def gated(self, name: str):
        started = self.directory / f"{name}.started"
        pid_path = self.directory / f"{name}.pid"
        release = self.directory / f"{name}.release"
        script = ("import os,pathlib,time\n"
                  f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()), encoding='utf-8', newline='\\n')\n"
                  f"pathlib.Path({str(started)!r}).touch()\n"
                  f"release=pathlib.Path({str(release)!r})\n"
                  "while not release.exists(): time.sleep(.05)\n")
        task = self.submit_execution(requestId=name, task="hold one execution", cwd=str(self.workdir(name)),
                             adapter="command", argv=[sys.executable, "-c", script],
                             expectedSeconds=120)["task"]
        return task["runId"], started, release

    def test_command_processes_use_free_slots_then_complete(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "2"}):
            client = BoardClient(self.directory, autostart=False)
            first, first_started, first_release = self.gated("first")
            second, second_started, second_release = self.gated("second")
            runs = {first: first_started, second: second_started}
            third_release = self.directory / "third.release"
            try:
                # R2 (2026-10-25): 27 starts took 0.6–3.3s under load; 60s detects a stall.
                self.wait_for_execution(lambda: first_started.exists() and second_started.exists(), runs, timeout=60)
                self.assertEqual(client.health()["capacity"]["totalActive"], 2)
                third, third_started, third_release = self.gated("third")
                runs[third] = third_started
                self.assertEqual(client.get(runId=third)["queueReason"], "capacity")
                first_release.touch()
                self.wait_for_execution(third_started.exists, runs, timeout=60)
                second_release.touch()
                third_release.touch()
                for run_id in (first, second, third):
                    self.wait_for_execution(lambda: client.get(runId=run_id)["state"] == "completed",
                                            runs, timeout=60)
                self.assertEqual(client.health()["capacity"]["totalActive"], 0)
            finally:
                first_release.touch()
                second_release.touch()
                third_release.touch()

    def test_stop_reaches_the_execution_and_confirms_shutdown(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            client = BoardClient(self.directory, autostart=False)
            run_id, started, release = self.gated("stop-running")
            try:
                self.wait_for_execution(started.exists, {run_id: started}, timeout=60)
                client.cancel(runId=run_id)
                self.assertTrue(wait_for(lambda: client.get(runId=run_id)["state"] == "cancelled",
                                         timeout=60), client.get(runId=run_id))
                self.assertTrue(client.get(runId=run_id)["shutdownConfirmed"])
            finally:
                release.touch()

    def test_zero_drain_service_stop_reaches_connected_execution(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}) as first:
            client = BoardClient(self.directory, autostart=False)
            run_id, started, release = self.gated("zero-drain")
            self.wait_for_execution(started.exists, {run_id: started}, timeout=45)
            self.assertIsNotNone(wait_for(lambda: (client.get(runId=run_id).get("selectedAttempt") or {}).get(
                "processIdentity"), timeout=20))
            attempt_id = client.get(runId=run_id)["selectedAttemptId"]
            try:
                reply = {}

                def stop(operation, params):
                    reply.update(client.call(operation, params))
                    return reply

                # Zero drain initiates cancellation but cannot confirm the still
                # executing attempt. The shared entry must reject that reply.
                with self.assertRaisesRegex(AssertionError, "shutdown unconfirmed"):
                    request_private_service_stop(self.directory, stop, drainSeconds=0)
                self.assertEqual(reply["cancelRequested"], 1)
                self.assertFalse(reply["stopped"])
                self.assertTrue(reply["unresolvedAttempts"])
                first.wait(timeout=20)
                self.assertTrue(wait_for(lambda: self.native_stopped(int((self.directory / "zero-drain.pid").read_text(encoding="utf-8"))),
                                         timeout=12), "zero-drain stop never reached the running command")
            finally:
                release.touch()
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            client = BoardClient(self.directory, autostart=False)
            self.assertTrue(wait_for(lambda: client.get(runId=run_id)["attemptState"] == "finished",
                                     timeout=35), client.get(runId=run_id))
            view = client.get(runId=run_id)
            self.assertEqual(view["selectedAttemptId"], attempt_id)
            self.assertEqual(view["state"], "cancelled")
            self.assertTrue(view["shutdownConfirmed"])
            self.assertEqual(view["stopInitiator"], "service")

    def test_restart_retains_execution_receipt_delivery(self):
        release = self.directory / "restart-running.release"
        try:
            with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}) as first:
                client = BoardClient(self.directory, autostart=False)
                run_id, started, release = self.gated("restart-running")
                self.wait_for_execution(started.exists, {run_id: started}, timeout=60)
                attempt_id = client.get(runId=run_id)["selectedAttemptId"]
                reply = client.call("service_control", {"action": "restart"})
                self.assertTrue(reply["restarting"])
                first.wait(timeout=30)
            with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
                client = BoardClient(self.directory, autostart=False)
                self.assertEqual(client.get(runId=run_id)["selectedAttemptId"], attempt_id)
                release.touch()
                self.wait_for_execution(lambda: client.get(runId=run_id)["state"] == "completed",
                                        {run_id: started}, timeout=60)
        finally:
            release.touch()

    def test_execution_death_does_not_report_stopped_until_native_work_ends(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            client = BoardClient(self.directory, autostart=False)
            run_id, started, release = self.gated("death-running")
            try:
                self.wait_for_execution(started.exists, {run_id: started}, timeout=60)
                def published():
                    return (client.get(runId=run_id).get("selectedAttempt") or {}).get("processIdentity")
                identity = wait_for(published, timeout=30)
                self.assertIsNotNone(identity)
                os.kill(identity["pid"], signal.SIGKILL)
                self.assertTrue(wait_for(lambda: client.get(runId=run_id)["attemptState"] == "finished",
                                         timeout=60), client.get(runId=run_id))
                view = client.get(runId=run_id)
                self.assertTrue(view["shutdownConfirmed"])
                native_pid = int((self.directory / "death-running.pid").read_text(encoding="utf-8"))
                self.assertTrue(wait_for(lambda: self.native_stopped(native_pid), timeout=30))
            finally:
                release.touch()

    def test_frozen_command_is_forced_after_the_stop_grace(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            client = BoardClient(self.directory, autostart=False)
            started = self.directory / "frozen.started"
            pid_path = self.directory / "frozen.pid"
            script = ("import os,pathlib,signal,time\n"
                      "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                      f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()), encoding='utf-8', newline='\\n')\n"
                      f"pathlib.Path({str(started)!r}).touch()\n"
                      "while True: time.sleep(1)\n")
            run_id = self.submit_execution(requestId="frozen", task="ignore stop", cwd=str(self.workdir("frozen")),
                                   adapter="command", argv=[sys.executable, "-c", script],
                                   expectedSeconds=120)["task"]["runId"]
            self.wait_for_execution(started.exists, {run_id: started}, timeout=45)
            execution_pid = client.get(runId=run_id)["selectedAttempt"]["processIdentity"]["pid"]
            client.cancel(runId=run_id)
            self.assertTrue(wait_for(lambda: client.get(runId=run_id)["attemptState"] == "finished",
                                     timeout=25), client.get(runId=run_id))
            view = client.get(runId=run_id)
            self.assertEqual(view["state"], "cancelled")
            self.assertTrue(view["shutdownConfirmed"])
            self.assertTrue(wait_for(lambda: self.native_stopped(int(pid_path.read_text(encoding="utf-8"))), timeout=10))
            self.assertTrue(wait_for(lambda: self.native_stopped(execution_pid), timeout=10))

    def test_stopped_execution_is_killed_from_the_recorded_force_time(self):
        if not hasattr(signal, "SIGSTOP"):
            self.skipTest("SIGSTOP is unavailable")
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            client = BoardClient(self.directory, autostart=False)
            run_id, started, release = self.gated("freeze-execution")
            self.wait_for_execution(started.exists, {run_id: started}, timeout=45)
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
                self.assertTrue(wait_for(lambda: client.get(runId=run_id)["attemptState"] == "finished",
                                         timeout=60), client.get(runId=run_id))
                view = client.get(runId=run_id)
                self.assertEqual(view["state"], "cancelled")
                self.assertTrue(view["shutdownConfirmed"])
                elapsed = (datetime.now(timezone.utc) - recorded).total_seconds()
                self.assertGreaterEqual(elapsed, 7)
                native_pid = int((self.directory / 'freeze-execution.pid').read_text(encoding="utf-8"))
                self.assertTrue(wait_for(lambda: self.native_stopped(native_pid), timeout=30))
            finally:
                release.touch()
                if inspect_process_identity(identity) == "alive":
                    os.kill(identity["pid"], signal.SIGCONT)
