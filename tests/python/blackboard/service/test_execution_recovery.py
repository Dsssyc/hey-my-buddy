"""A replacement private service reconnects executions using durable identity."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from fake_executable import make_fake_executable
from support import BoardTestCase, wait_for
from blackboard.routing.fixtures.router_tool_receipt import claim_tool_receipt
from blackboard.routing.test_router_dispatch import RouterDispatchTestCase
from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.blackboard.service.execution_scheduler import ExecutionScheduler, _RecoveredExecution
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.store.db import canonical_json
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.protocol.execution import fsync_json
from hey_my_buddy.protocol.process_identity import inspect_process_identity, kill_verified_process_group
from hey_my_buddy.protocol.run_identity import RunIdentity
from hey_my_buddy.protocol.transport import _read_endpoint, _request
from hey_my_buddy.private_dirs import attempt_root


class PrivateExecutionRecoveryTests(BoardTestCase):
    """Only explicit command children run; each case owns its daemon and state."""

    def client(self):
        return BoardClient(self.directory, autostart=False)

    def command(self, name: str):
        started = self.directory / f"{name}.started"
        release = self.directory / f"{name}.release"
        script = ("import pathlib,time\n"
                  f"pathlib.Path({str(started)!r}).touch()\n"
                  f"release=pathlib.Path({str(release)!r})\n"
                  "while not release.exists(): time.sleep(.05)\n"
                  "print('released after service restart', flush=True)\n")
        submitted = self.submit_execution(**{
            "requestId": name, "task": "Wait behind a private gate",
            "cwd": str(self.workdir(name)), "adapter": "command",
            "argv": [sys.executable, "-c", script],
        })["task"]
        run_id = submitted["runId"]
        self.assertTrue(wait_for(started.exists, timeout=60),
                        f"command did not start: {submitted}")
        view = wait_for(lambda: self._running(run_id), timeout=30)
        self.assertIsNotNone(view, self.client().get(runId=run_id))
        return run_id, release, view["selectedAttempt"]

    def _running(self, run_id: str):
        view = self.client().get(runId=run_id)
        attempt = view.get("selectedAttempt") or {}
        if attempt.get("processIdentity") and attempt.get("executionState") == "executing":
            return view
        return None

    def restart(self, daemon):
        endpoint = _read_endpoint(self.directory)
        self.assertIsNotNone(endpoint)
        reply = _request(endpoint, "service_control", {"action": "restart", 'note': "private recovery test"},
                         state_dir=self.directory)
        self.assertTrue(reply["restarting"], reply)
        self.assertTrue(reply["executionsPreserved"], reply)
        self.assertEqual(daemon.wait(timeout=25), 0)

    def execution_dir(self, run_id, attempt_id):
        return attempt_root(self.directory, "command", run_id, attempt_id)

    def assert_retained(self, run_id, attempt_id):
        view = self.client().get(runId=run_id)
        self.assertEqual(view["selectedAttempt"]["attemptId"], attempt_id)
        self.assertFalse(view["selectedAttempt"]["shutdownConfirmed"])
        store = BoardStore(self.directory)
        try:
            self.assertEqual(store.capacity_report()["totalActive"], 1)
            self.assertTrue(store.resource_claims())
        finally:
            store.db.close()
        return view

    def test_live_command_reconnects_and_cancel_settles(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}) as daemon:
            run_id, release, before = self.command("reconnect-cancel")
            identity = before["processIdentity"]
            self.restart(daemon)
        try:
            with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
                attached = wait_for(lambda: self._running(run_id), timeout=30)
                self.assertIsNotNone(attached, self.client().get(runId=run_id))
                attempt = attached["selectedAttempt"]
                self.assertEqual(attempt["attemptId"], before["attemptId"])
                self.assertEqual(attempt["ownership"], "owned")
                self.assertEqual(attempt["processIdentity"], identity)
                self.assert_retained(run_id, before["attemptId"])
                code, response = self.cli("execution-cancel", json.dumps({
                    "runId": run_id, "note": "private test"}))
                self.assertEqual(code, 0, response)
                finished = wait_for(lambda: self._finished(run_id), timeout=35)
                self.assertIsNotNone(finished, self.client().get(runId=run_id))
                self.assertEqual(finished["state"], "cancelled")
                self.assertTrue(finished["shutdownConfirmed"])
                self.assertEqual(finished["terminationReason"], "unknown-stop")
        finally:
            release.touch()

    def test_normal_startup_has_no_unknown_state_connection_issue_or_reconnect_event(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            run_id, release, before = self.command("normal-startup")
            try:
                self.assertIsNotNone(wait_for(lambda: self._live_bridge(run_id), timeout=30))
                running = self.client().get(runId=run_id)
                self.assertEqual(running["selectedAttempt"]["executionState"], "executing")
                self.assertEqual(running["selectedAttempt"]["ownership"], "owned")
                self.assertEqual(running["selectedAttempt"]["issues"], [])
                store = BoardStore(self.directory)
                try:
                    with store.db.read() as db:
                        events = db.execute("SELECT kind FROM events WHERE attempt_id=? AND kind IN"
                            " ('attempt.uncertain','execution.unknown','execution.connected',"
                            " 'execution.issue','execution.issue_resolved')", (before["attemptId"],)).fetchall()
                    self.assertEqual(events, [])
                finally:
                    store.db.close()
                release.touch()
                finished = wait_for(lambda: self._finished(run_id), timeout=35)
                self.assertIsNotNone(finished, self.client().get(runId=run_id))
                self.assertEqual(finished["state"], "completed")
                self.assertEqual(finished["selectedAttempt"]["issues"], [])
            finally:
                release.touch()

    def test_live_command_completes_after_replacement_service(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}) as daemon:
            run_id, release, before = self.command("reconnect-complete")
            self.restart(daemon)
        try:
            with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
                attached = wait_for(lambda: self._running(run_id), timeout=30)
                self.assertIsNotNone(attached, self.client().get(runId=run_id))
                self.assertEqual(attached["selectedAttempt"]["attemptId"], before["attemptId"])
                release.touch()
                finished = wait_for(lambda: self._finished(run_id), timeout=35)
                self.assertIsNotNone(finished, self.client().get(runId=run_id))
                self.assertEqual(finished["state"], "completed")
                self.assertTrue(finished["shutdownConfirmed"])
                self.assertEqual(finished["selectedAttempt"]["attemptId"], before["attemptId"])
                self.assertEqual(finished["selectedAttempt"]["result"]["status"], "ok")
        finally:
            release.touch()

    def _finished(self, run_id):
        view = self.client().get(runId=run_id)
        return view if view.get("attemptState") == "finished" and view["shutdownConfirmed"] else None

    def test_execution_dead_during_restart_is_settled(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}) as daemon:
            run_id, release, before = self.command("dead-during-restart")
            identity = before["processIdentity"]
            self.restart(daemon)
        try:
            self.assertEqual(inspect_process_identity(identity), "alive")
            os.killpg(identity["processGroupId"], signal.SIGKILL)
            with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
                finished = wait_for(lambda: self._finished(run_id), timeout=35)
                self.assertIsNotNone(finished, self.client().get(runId=run_id))
                self.assertEqual(finished["selectedAttempt"]["attemptId"], before["attemptId"])
                self.assertEqual(finished["state"], "failed")
                self.assertEqual(finished["terminationReason"], "interrupted")
        finally:
            release.touch()

    @unittest.skipUnless(hasattr(signal, "SIGSTOP") and hasattr(os, "killpg"),
                         "frozen execution requires POSIX process signals")
    def test_frozen_execution_is_unknown_then_forced_after_durable_stop_window(self):
        identity = None
        release = self.directory / "frozen-execution.release"
        try:
            with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}) as daemon:
                run_id, release, before = self.command("frozen-execution")
                identity = before["processIdentity"]
                self.assertEqual(inspect_process_identity(identity), "alive")
                os.kill(identity["pid"], signal.SIGSTOP)
                # A crashed service cannot wait for the frozen endpoint's long call.
                daemon.kill()
                self.assertIsNotNone(daemon.wait(timeout=10))
            with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
                unknown = wait_for(lambda: self._unknown(run_id), timeout=35)
                self.assertIsNotNone(unknown, self.client().get(runId=run_id))
                self.assertEqual(unknown["selectedAttempt"]["attemptId"], before["attemptId"])
                self.assert_retained(run_id, before["attemptId"])
                code, response = self.cli("execution-cancel", json.dumps({
                    "runId": run_id, "note": "frozen private execution"}))
                self.assertEqual(code, 0, response)
                forced = wait_for(lambda: self.client().get(runId=run_id)["selectedAttempt"].get("forceRequestedAt"),
                                  timeout=35)
                self.assertIsNotNone(forced)
                finished = wait_for(lambda: self._finished(run_id), timeout=45)
                self.assertIsNotNone(finished, self.client().get(runId=run_id))
                attempt = finished["selectedAttempt"]
                self.assertEqual(attempt["forceRequestedAt"], forced)
                elapsed = (datetime.fromisoformat(attempt["finishedAt"].replace("Z", "+00:00")) -
                           datetime.fromisoformat(forced.replace("Z", "+00:00"))).total_seconds()
                self.assertGreaterEqual(elapsed, 7.5)
                self.assertTrue(finished["shutdownConfirmed"])
                self.assertEqual(inspect_process_identity(identity), "identity-absent")
        finally:
            release.touch()
            if identity is not None and inspect_process_identity(identity) == "alive":
                os.kill(identity["pid"], signal.SIGCONT)
                wait_for(lambda: inspect_process_identity(identity) == "identity-absent", timeout=5)
                kill_verified_process_group(identity)
            for journal in self.directory.glob("harnesses/command/micro-tasks/*/attempts/*/native-process-journal-*.json"):
                for native in json.loads(journal.read_text(encoding="utf-8"))["identities"]:
                    kill_verified_process_group(native)

    def _unknown(self, run_id):
        view = self.client().get(runId=run_id)
        attempt = view.get("selectedAttempt") or {}
        if (attempt.get("executionState") == "uncertain"
                and attempt.get("ownership") == "uncertain"
                and any(issue["stage"] == "connection" and issue["code"] == "EXECUTION_UNKNOWN"
                        for issue in attempt.get("issues", []))):
            return view
        return None

    def test_bad_private_endpoint_keeps_claim_until_descriptor_is_restored(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}) as daemon:
            run_id, release, before = self.command("bad-endpoint")
            endpoint = self.execution_dir(run_id, before["attemptId"]) / "execution-live.json"
            original = endpoint.read_bytes()
            corrupted = json.loads(original)
            corrupted["token"] = "0" * 64 if corrupted["token"] != "0" * 64 else "1" * 64
            endpoint.write_text(json.dumps(corrupted), encoding="utf-8", newline="\n")
            os.chmod(endpoint, 0o600)
            self.restart(daemon)
        try:
            with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}) as daemon:
                issue = wait_for(lambda: self._connection_issue(run_id), timeout=30)
                self.assertIsNotNone(issue, self.client().get(runId=run_id))
                self.assertEqual(issue["code"], "EXECUTION_UNKNOWN")
                self.assert_retained(run_id, before["attemptId"])
                endpoint.write_bytes(original)
                os.chmod(endpoint, 0o600)
                recovered = wait_for(lambda: self._running(run_id), timeout=30)
                self.assertIsNotNone(recovered, self.client().get(runId=run_id))
                self.assertEqual(recovered["selectedAttempt"]["ownership"], "owned")
                endpoint.write_bytes(b"{broken json")
                os.chmod(endpoint, 0o600)
                self.restart(daemon)
            with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
                malformed = wait_for(lambda: self._connection_issue(run_id), timeout=30)
                self.assertIsNotNone(malformed, self.client().get(runId=run_id))
                self.assertEqual(malformed["code"], "EXECUTION_UNKNOWN")
                self.assert_retained(run_id, before["attemptId"])
                endpoint.write_bytes(original)
                os.chmod(endpoint, 0o600)
                reattached = wait_for(lambda: self._running(run_id), timeout=30)
                self.assertIsNotNone(reattached, self.client().get(runId=run_id))
                release.touch()
                self.assertIsNotNone(wait_for(lambda: self._finished(run_id), timeout=35))
        finally:
            if endpoint.exists():
                endpoint.write_bytes(original)
                os.chmod(endpoint, 0o600)
            release.touch()

    def _connection_issue(self, run_id):
        view = self.client().get(runId=run_id)
        for issue in (view.get("selectedAttempt") or {}).get("issues", []):
            if issue["stage"] == "connection":
                return issue
        return None

    def offline_router(self):
        """An ACP fixture waits on a private gate before returning one legal answer."""
        fixture = Path(__file__).resolve().parents[2] / "buddy/harnesses/dsh/fixtures/workflow_agent.py"
        gate = self.directory / "router.release"
        started = self.directory / "router.started"
        candidate = "dsh:deepseek-official:deepseek-v4-pro:max"
        script = self.directory / "fixture-router.py"
        script.write_text(
            "import importlib.util,json,os,sys,time\n"
            "from pathlib import Path\n"
            f"spec=importlib.util.spec_from_file_location('fixture_agent', {str(fixture)!r})\n"
            "module=importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "class GatedAgent(module.WorkflowAgent):\n"
            "    def run_micro_task_prompt(self, message):\n"
            "        if self.turn_input(message):\n"
            f"            root=Path({str(self.directory)!r})\n"
            "            (root/'worker.started').touch()\n"
            "            while not (root/'worker.release').exists(): time.sleep(.05)\n"
            "            return super().run_micro_task_prompt(message)\n"
            f"        root=Path({str(self.directory)!r})\n"
            "        (root/'router.started').touch()\n"
            "        while not (root/'router.release').exists(): time.sleep(.05)\n"
            f"        self.final_answers=[json.dumps({{'buddyId':{candidate!r},'note':'offline fixture choice','evidence':[]}})]\n"
            "        return module.fake_agent.FakeAgent.run_final_prompt(self,message)\n"
            "module.WorkflowAgent=GatedAgent\n"
            f"sys.argv[1:1] = ['--fixture-root', {str(self.directory)!r}]\n"
            "raise SystemExit(module.main())\n", encoding="utf-8", newline="\n"
        )
        entry = make_fake_executable(self.directory / "harness-bin", "dsh", source=script)
        self.catalog_fixture()
        store = BoardStore(self.directory)
        try:
            store.initialize()
            first = "dsh:deepseek-official:deepseek-flash:max"
            with store.db.write() as db:
                for buddy_id, model in ((first, "deepseek-flash"), (candidate, "deepseek-v4-pro")):
                    db.execute("INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,"
                               "available,enabled,capabilities_json) VALUES(?,?,?,?,?,?,1,1,?)",
                               (buddy_id, buddy_id, "dsh", "deepseek-official", model, "max",
                                canonical_json([])))
                settings = {**router.configuration(db), "routerBuddyIds": [first],
                            "defaultRoutingMode": "fast"}
                router._write_settings(db, settings)
        finally:
            store.db.close()
        return entry, started, gate, candidate

    def test_router_result_after_restart_uses_frozen_decision_and_budget(self):
        script, started, release, candidate = self.offline_router()
        environment = {"HMB_MAX_CONCURRENT": "1", "HMB_DSH_CLI": str(script)}
        with self.daemon(env=environment) as daemon:
            created = self.client().call("selection_request", {
                "requestId": "router-reconnect", "task": "Choose an offline fixture buddy"})
            self.assertTrue(wait_for(started.exists, timeout=60), created)
            self.assertFalse((self.directory / "sessions").exists(), "ungrouped selections do not record sessions")
            run_id = created["runId"]
            before = self.client().get(runId=run_id)["selectedAttempt"]
            self.assertIsNotNone(before["processIdentity"])
            budget = attempt_root(self.directory, "dsh", run_id,
                                  before["attemptId"]) / "execution-budget.json"
            self.assertTrue(wait_for(budget.exists, timeout=15))
            original_budget = json.loads(budget.read_text(encoding="utf-8"))
            self.restart(daemon)
        try:
            with self.daemon(env=environment):
                attached = wait_for(lambda: self._running(run_id), timeout=30)
                self.assertIsNotNone(attached, self.client().get(runId=run_id))
                self.assertEqual(attached["selectedAttempt"]["attemptId"], before["attemptId"])
                self.assertEqual(json.loads(budget.read_text(encoding="utf-8")), original_budget)
                self.assertNotEqual(self.client().call("selection_get", {
                    "decisionId": created["decisionId"]})["decision"]["status"], "completed")
                release.touch()
                decision = wait_for(lambda: self._completed_decision(created["decisionId"]), timeout=45)
                self.assertIsNotNone(decision, self.client().call("selection_get", {
                    "decisionId": created["decisionId"]}))
                self.assertEqual(decision["buddyId"], candidate)
                self.assertEqual(self.client().get(runId=run_id)["selectedAttempt"]["attemptId"],
                                 before["attemptId"])
        finally:
            release.touch()

    @unittest.skipUnless(hasattr(signal, "SIGSTOP") and hasattr(os, "killpg"),
                         "forced Router recovery requires POSIX process signals")
    def test_reconnected_router_budget_forces_frozen_execution_after_eight_seconds(self):
        script, started, release, _candidate = self.offline_router()
        environment = {"HMB_MAX_CONCURRENT": "1", "HMB_DSH_CLI": str(script)}
        identity = None
        try:
            with self.daemon(env=environment) as daemon:
                created = self.client().call("selection_request", {
                    "requestId": "router-deadline", "task": "Choose behind a frozen gate"})
                self.assertTrue(wait_for(started.exists, timeout=60), created)
                self.assertFalse((self.directory / "sessions").exists(), "ungrouped selections do not record sessions")
                run_id = created["runId"]
                attempt = self.client().get(runId=run_id)["selectedAttempt"]
                identity = attempt["processIdentity"]
                budget = attempt_root(self.directory, "dsh", run_id,
                                      attempt["attemptId"]) / "execution-budget.json"
                self.assertTrue(wait_for(budget.exists, timeout=15))
                self.restart(daemon)
            with self.daemon(env=environment):
                attached = wait_for(lambda: self._running(run_id), timeout=30)
                self.assertIsNotNone(attached, self.client().get(runId=run_id))
                self.assertEqual(attached["selectedAttempt"]["ownership"], "owned")
                self.assertEqual(inspect_process_identity(identity), "alive")
                os.kill(identity["pid"], signal.SIGSTOP)
                material = json.loads(budget.read_text(encoding="utf-8"))
                material["deadline"] = time.monotonic() - 1
                fsync_json(budget, material)
                # Publish as the execution does: an authenticated progress hint
                # wakes the observer even when waitingReason is unchanged.
                startup = json.loads((budget.parent / "execution-start.json").read_bytes())
                self.client().progress(attempt["attemptId"], attempt["generation"], startup["nonce"],
                                       data={"waitingReason": None})
                forced = wait_for(lambda: self.client().get(runId=run_id)["selectedAttempt"].get("forceRequestedAt"),
                                  timeout=35)
                self.assertIsNotNone(forced)
                final = wait_for(lambda: self._finished(run_id), timeout=45)
                self.assertIsNotNone(final, self.client().get(runId=run_id))
                selected = final["selectedAttempt"]
                self.assertEqual(selected["forceReason"], "deadline")
                self.assertEqual(selected["attemptId"], attempt["attemptId"])
                elapsed = (datetime.fromisoformat(selected["finishedAt"].replace("Z", "+00:00")) -
                           datetime.fromisoformat(forced.replace("Z", "+00:00"))).total_seconds()
                self.assertGreaterEqual(elapsed, 7.5)
                self.assertTrue(selected["shutdownConfirmed"])
                decision = self.client().call("selection_get", {
                    "decisionId": created["decisionId"], "includeAudit": True})["decision"]
                self.assertEqual(decision["status"], "failed")
                self.assertIsNone(decision["output"])
        finally:
            release.touch()
            if identity is not None and inspect_process_identity(identity) == "alive":
                os.kill(identity["pid"], signal.SIGCONT)
                wait_for(lambda: inspect_process_identity(identity) == "identity-absent", timeout=5)
                kill_verified_process_group(identity)
            for journal in self.directory.glob("harnesses/dsh/micro-tasks/*/attempts/*/native-process-journal-*.json"):
                for native in json.loads(journal.read_text(encoding="utf-8"))["identities"]:
                    kill_verified_process_group(native)

    def _completed_decision(self, decision_id):
        decision = self.client().call("selection_get", {"decisionId": decision_id})["decision"]
        return decision if decision["status"] == "completed" else None

    @unittest.skipUnless(hasattr(signal, "SIGSTOP") and hasattr(os, "killpg"),
                         "forced wrap-up recovery requires POSIX process signals")
    def test_host_wrap_up_deadline_survives_restart_and_forces_at_expiry(self):
        script, _router_started, router_release, _candidate = self.offline_router()
        worker_started = self.directory / "worker.started"
        worker_release = self.directory / "worker.release"
        environment = {"HMB_MAX_CONCURRENT": "1", "HMB_DSH_CLI": str(script)}
        repo = self.workdir("worker-repo")
        for command in (("init", "-q", "-b", "main"),
                        ("config", "user.email", "buddy@example.invalid"),
                        ("config", "user.name", "Buddy Test")):
            subprocess.run(["git", *command], cwd=repo, check=True, capture_output=True)
        (repo / "tracked.txt").write_text("private fixture\n", encoding="utf-8", newline="\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=repo,
                       check=True, capture_output=True)
        identity = None
        try:
            with self.daemon(env=environment) as daemon:
                submitted = self.client().call("workflow_submit", {
                    "requestId": "worker-wrap-up", "hostId": "host-1",
                    "task": "Complete a gated offline fixture turn", "cwd": str(repo),
                    "submissionToken": "t" * 32, "executionWorkspace": {"kind": "existing", "access": "write"},
                    "harness": "dsh", "provider": "deepseek-official", "model": "deepseek-flash",
                    "effort": "max"})
                run_id = submitted["runId"]
                self.assertTrue(wait_for(worker_started.exists, timeout=60), submitted)
                before_view = wait_for(lambda: self._running(run_id), timeout=30)
                self.assertIsNotNone(before_view, self.client().get(runId=run_id))
                before = before_view["selectedAttempt"]
                identity = before["processIdentity"]
                self.restart(daemon)
            with self.daemon(env=environment):
                attached = wait_for(lambda: self._running(run_id), timeout=30)
                self.assertIsNotNone(attached, self.client().get(runId=run_id))
                self.assertEqual(attached["selectedAttempt"]["attemptId"], before["attemptId"])
                bridge = wait_for(lambda: self._live_bridge(run_id), timeout=20)
                self.assertIsNotNone(bridge)
                self.assertEqual(inspect_process_identity(identity), "alive")
                os.kill(identity["pid"], signal.SIGSTOP)
                self.client().call("workflow_cancel", {"runId": run_id, **submitted["control"]})
                stopped = self.client().get(runId=run_id)["selectedAttempt"]
                self.assertEqual(stopped["stopInitiator"], "host")
                self.assertIsNotNone(stopped["graceDeadline"], stopped)
                store = BoardStore(self.directory)
                try:
                    with store.db.write() as db:
                        db.execute("UPDATE attempts SET grace_deadline=? WHERE attempt_id=?",
                                   ("2000-01-01T00:00:00.000Z", before["attemptId"]))
                finally:
                    store.db.close()
                forced = wait_for(lambda: self.client().get(runId=run_id)["selectedAttempt"].get("forceRequestedAt"),
                                  timeout=35)
                self.assertIsNotNone(forced)
                terminal = wait_for(lambda: self._finished(run_id), timeout=45)
                self.assertIsNotNone(terminal, self.client().get(runId=run_id))
                self.assertEqual(terminal["state"], "cancelled")
                self.assertTrue(terminal["shutdownConfirmed"])
                self.assertEqual(terminal["selectedAttempt"]["forceReason"], "grace-expired")
                elapsed = (datetime.fromisoformat(terminal["selectedAttempt"]["finishedAt"].replace("Z", "+00:00")) -
                           datetime.fromisoformat(forced.replace("Z", "+00:00"))).total_seconds()
                self.assertGreaterEqual(elapsed, 7.5)
        finally:
            worker_release.touch()
            router_release.touch()
            if identity is not None and inspect_process_identity(identity) == "alive":
                os.kill(identity["pid"], signal.SIGCONT)
                wait_for(lambda: inspect_process_identity(identity) == "identity-absent", timeout=5)
                kill_verified_process_group(identity)
            for journal in self.directory.glob("harnesses/dsh/micro-tasks/*/attempts/*/native-process-journal-*.json"):
                for native in json.loads(journal.read_text(encoding="utf-8"))["identities"]:
                    kill_verified_process_group(native)

    def _live_bridge(self, run_id):
        observed = self.client().call("inquiry_observe", {"runId": run_id, "timeoutMs": 1000})
        return observed["bridge"] if observed["bridge"]["enabled"] else None


class ReconnectedRouterContractTests(RouterDispatchTestCase):
    """A decision's frozen input and deadline survive observer reconstruction."""

    def claimed_decision(self):
        request = self.request()
        claim = self.claim("router")["claim"]
        self.assertIsNotNone(claim)
        claim["nonce"] = "n" * 32 + "router"
        return request, claim

    def recovered_handle(self, claim):
        task = claim["task"]
        attempt = claim["attempt"]
        binding = {"taskId": task["taskId"], "attemptId": attempt["attemptId"],
                   "generation": attempt["generation"]}
        directory = attempt_root(self.directory, claim["decisionInput"]["buddy"]["harness"],
                                 binding["taskId"], binding["attemptId"])
        directory.mkdir(parents=True, exist_ok=True)
        identity = RunIdentity(task_id=binding["taskId"], attempt_id=binding["attemptId"],
                               generation=binding["generation"], invocation_id="execution",
                               turn_id=(claim.get("turn") or {}).get("turnId"))
        return _RecoveredExecution(directory, binding, identity)

    def test_reconstructed_router_observer_honors_original_budget(self):
        _request, claim = self.claimed_decision()
        handle = self.recovered_handle(claim)
        handle.budget_path.write_text(json.dumps({"binding": handle.execution_binding,
                                                  "deadline": time.monotonic() - 1}), encoding="utf-8", newline="\n")
        scheduler = ExecutionScheduler(self.board_.service, live_registry=self.board_.live_registry)
        client = Mock()
        view = self.client.get(runId=claim["task"]["taskId"])
        sent, forced_at, _deadline = scheduler._apply_stop(view, claim, handle, client, None, None)
        self.assertEqual(sent, ("deadline", None, True))
        self.assertIsNotNone(forced_at)
        client.stop.assert_called_once_with(initiator="deadline", grace_deadline=None, force=True)
        stored = self.client.get(runId=claim["task"]["taskId"])["selectedAttempt"]
        self.assertEqual(stored["forceReason"], "deadline")
        self.assertIsNotNone(stored["forceRequestedAt"])

    def test_interrupted_router_waits_for_authenticated_result_then_accepts_frozen_decision(self):
        request, claim = self.claimed_decision()
        actor = {"attemptId": claim["attempt"]["attemptId"],
                 "generation": claim["attempt"]["generation"], "nonce": claim["nonce"]}
        identity = {"bootId": "macos:00000000-0000-0000-0000-000000000001", "pid": 123,
                    "startedAt": 123250000, "processGroupId": 123, "jobName": None}
        self.board_.call("execution_progress", {**actor, "processIdentity": identity})
        with patch("hey_my_buddy.protocol.process_identity.inspect_process_identity", return_value="alive"):
            self.board_.store.reconcile_startup()
        pending = self.decision_row(request["decisionId"])
        self.assertNotEqual(pending["status"], "completed")
        self.assertEqual(self.client.get(runId=claim["task"]["taskId"])["attemptState"], "uncertain")
        self.board_.store.execution_connection(actor["attemptId"], connected=True)
        output = {**claim_tool_receipt(claim, 0, state_dir=self.directory), "status": "ok", "operation": "select",
                  "usage": {"elapsedMs": 100, "toolCalls": 0, "bytesRead": None},
                  "stopEvidence": {"shutdownConfirmed": True, "native": {"shutdownConfirmed": True}},
                  "inputVerification": {"unchanged": True, "snapshotSha256": "private-fixture-copy",
                                        "manifestSha256": (claim["decisionInput"].get("executionWorkspace") or {}).get("manifestSha256")},
                  "decision": {"buddyId": "dsh:fixture:bravo:max", "note": "The frozen legal choice",
                               "evidence": []}}
        settled = self.board_.call("execution_result", {**actor, "status": "ok",
                                                         "shutdownConfirmed": True, "result": output})
        self.assertEqual(settled["decision"]["outcome"], "answered")
        final = self.decision_row(request["decisionId"])
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["buddyId"], "dsh:fixture:bravo:max")


class ReconnectedWrapUpTests(WorkflowTestCase):
    def test_reconstructed_observer_delivers_the_original_host_wrap_up_deadline(self):
        board = self.board()
        self.register(board)
        run = self.submit(board)
        claimed = self.claim(board)
        claim = claimed["claim"]
        attempt = claim["attempt"]
        board.call("workflow_cancel", {"runId": run["runId"], **self.controls[run["runId"]]})
        durable = board.client().get(runId=run["runId"])["selectedAttempt"]
        self.assertEqual(durable["stopInitiator"], "host")
        self.assertIsNotNone(durable["graceDeadline"])

        binding = {"taskId": claim["task"]["taskId"], "attemptId": attempt["attemptId"],
                   "generation": attempt["generation"]}
        location = attempt_root(self.directory, "dsh", binding["taskId"], binding["attemptId"])
        location.mkdir(parents=True, exist_ok=True)
        handle = _RecoveredExecution(location, binding, RunIdentity(
            task_id=binding["taskId"], attempt_id=binding["attemptId"],
            generation=binding["generation"], invocation_id="execution",
            turn_id=claim["turn"]["turnId"]))
        scheduler = ExecutionScheduler(board.service, live_registry=board.live_registry)
        for channel in (Mock(), Mock()):
            view = board.client().get(runId=run["runId"])
            sent, forced_at, _deadline = scheduler._apply_stop(view, claim, handle, channel, None, None)
            self.assertEqual(sent, ("host", durable["graceDeadline"], False))
            self.assertIsNone(forced_at)
            call = channel.stop.call_args.kwargs
            self.assertEqual(call["initiator"], "host")
            self.assertFalse(call["force"])
            self.assertGreater(call["grace_deadline"], time.monotonic())
        self.assertEqual(board.client().get(runId=run["runId"])["selectedAttempt"]["graceDeadline"],
                         durable["graceDeadline"])
