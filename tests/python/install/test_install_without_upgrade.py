"""A same-contract install reconnects live executions without restoring the board."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import threading
import tomllib
from unittest import mock

from support import BoardTestCase, wait_for
from install import test_upgrade_host_cutover as cutover_fixture
from blackboard.service import test_execution_recovery as recovery_fixture
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.tasks.storage import prune_old_runtimes
from hey_my_buddy.cli.blocking import await_run
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import launcher, runtime, upgrade
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.protocol.contracts import CONTRACT_VERSION
from hey_my_buddy.protocol.process_identity import inspect_process_identity, kill_verified_process_group
from hey_my_buddy.protocol.transport import _call_service


class InstallWithoutUpgradeTests(BoardTestCase):
    """Each test owns a private board, installed service and synthetic runtime."""

    def private_runtime(self, generation: str, patch_number: int, *, contract_version: str | None = None) -> Path:
        original_copy = runtime._copy_assets
        contract_version = contract_version or CONTRACT_VERSION

        def copy_with_patch(source: Path, destination: Path) -> None:
            original_copy(source, destination)
            project = destination / "pyproject.toml"
            software_version = tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"]
            synthetic_version = f'{contract_version.rsplit(".", 1)[0]}.{patch_number}'
            project_text = project.read_text(encoding="utf-8")
            old_project = f'version = "{software_version}"'
            self.assertEqual(project_text.count(old_project), 1)
            project.write_text(project_text.replace(old_project, f'version = "{synthetic_version}"', 1), encoding="utf-8", newline="\n")
            lock = destination / "uv.lock"
            lock_text = lock.read_text(encoding="utf-8")
            old_package = f'name = "hey-my-buddy"\nversion = "{software_version}"'
            self.assertEqual(lock_text.count(old_package), 1)
            lock.write_text(lock_text.replace(old_package,
                                              f'name = "hey-my-buddy"\nversion = "{synthetic_version}"', 1), encoding="utf-8", newline="\n")
            contract = destination / "src/hey_my_buddy/protocol/contracts.py"
            text = contract.read_text(encoding="utf-8")
            original = f'CONTRACT_VERSION = "{CONTRACT_VERSION}"'
            self.assertIn(original, text)
            contract.write_text(text.replace(original, f'CONTRACT_VERSION = "{contract_version}"', 1), encoding="utf-8", newline="\n")

        with mock.patch.object(runtime, "_copy_assets", side_effect=copy_with_patch):
            return cutover_fixture.HostCutoverTests.private_runtime(self, generation)

    @contextmanager
    def installation(self, target: Path, *, overrides: dict | None = None):
        original_modules = launcher.entry_modules
        fixture_modules = lambda path: {**original_modules(path),
                                        "daemon": "hey_my_buddy._upgrade_fixture_daemon"}
        environment = self.child_environment({"HMB_RUNTIME_ROOT": str(target.parent),
                                              "HMB_MAX_CONCURRENT": "1", "HMB_WAIT_CAPACITY": "4",
                                              **(overrides or {})})
        environment.pop("PYTHONPATH", None)
        environment.pop("HMB_DEV_SOURCE", None)
        original_service_environment = launcher.service_environment

        def fixture_service_environment(*args, **kwargs):
            allowed = original_service_environment(*args, **kwargs)
            if overrides and "HMB_DSH_CLI" in overrides:
                allowed["HMB_DSH_CLI"] = overrides["HMB_DSH_CLI"]
            return allowed

        with mock.patch.dict(os.environ, environment, clear=True), \
             mock.patch.object(launcher, "entry_modules", side_effect=fixture_modules), \
             mock.patch.object(launcher, "service_environment", side_effect=fixture_service_environment), \
             mock.patch.object(upgrade.runtime, "materialize", return_value={"runtimeDir": str(target)}), \
             mock.patch.object(upgrade.backup, "create", side_effect=AssertionError("upgrade backup")):
            yield

    def start(self, target: Path) -> dict:
        health = upgrade.start(self.directory, target)
        self.assertEqual(health["runtimeContentId"], target.name)
        launcher.write_active_runtime(self.directory, target)
        return health

    def client(self) -> BoardClient:
        return BoardClient(self.directory, autostart=False)

    def command(self, name: str) -> tuple[str, Path, dict]:
        started = self.directory / f"{name}.started"
        release = self.directory / f"{name}.release"
        self.addCleanup(release.touch)
        script = ("import pathlib,time\n"
                  f"pathlib.Path({str(started)!r}).touch()\n"
                  f"release=pathlib.Path({str(release)!r})\n"
                  "while not release.exists(): time.sleep(.05)\n"
                  "print('finished after install', flush=True)\n")
        submitted = self.submit_execution(**{
            "requestId": name, "task": "Wait across private installation",
            "cwd": str(self.workdir(name)), "adapter": "command",
            "argv": [sys.executable, "-c", script],
        })["task"]
        self.assertTrue(wait_for(started.exists, timeout=60), submitted)
        run_id = submitted["runId"]
        running = wait_for(lambda: self.running(run_id), timeout=30)
        self.assertIsNotNone(running, self.client().get(runId=run_id))
        return run_id, release, running["selectedAttempt"]

    def running(self, run_id: str) -> dict | None:
        view = self.client().get(runId=run_id)
        attempt = view.get("selectedAttempt") or {}
        return view if attempt.get("processIdentity") and attempt.get("executionState") == "executing" else None

    def finished(self, run_id: str) -> dict | None:
        view = self.client().get(runId=run_id)
        return view if view.get("attemptState") == "finished" and view.get("shutdownConfirmed") else None

    def event_head(self) -> int:
        with closing(sqlite3.connect(self.directory / "board.sqlite3")) as connection:
            return connection.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]

    def test_two_patch_installs_preserve_execution_receipt_await_and_runtime(self):
        first = self.private_runtime("patch-one", 1)
        second = self.private_runtime("patch-two", 2)
        self.catalog_fixture()
        with self.installation(second):
            self.start(first)
            run_id, release, original = self.command("two-installs")
            identity = original["processIdentity"]
            entered = threading.Event()

            def waiting_service(method, params, state_dir):
                if method == "wait":
                    entered.set()
                return _call_service(method, params, state_dir)

            with ThreadPoolExecutor(max_workers=1) as pool:
                waiter = pool.submit(await_run, {"runId": run_id, "waitSeconds": 90},
                                     self.directory, service=waiting_service)
                try:
                    self.assertTrue(entered.wait(15))
                    first_install = upgrade.upgrade()
                    self.assertTrue(first_install["upgraded"], first_install)
                    self.assertNotIn("backup", first_install)
                    self.assertEqual(first_install["verification"]["schemaVersion"], SCHEMA_VERSION)
                    self.assertEqual(inspect_process_identity(identity), "alive")
                    self.assertTrue(first.exists(), "old runtime was pruned while its execution was alive")
                    attached = wait_for(lambda: self.running(run_id), timeout=30)
                    self.assertIsNotNone(attached, self.client().get(runId=run_id))
                    self.assertEqual(attached["selectedAttempt"]["attemptId"], original["attemptId"])
                    third = self.private_runtime("patch-three", 3)
                    with mock.patch.object(upgrade.runtime, "materialize", return_value={"runtimeDir": str(third)}):
                        second_install = upgrade.upgrade()
                    self.assertTrue(second_install["upgraded"], second_install)
                    self.assertNotIn("backup", second_install)
                    self.assertTrue(first.exists(), "runtime still serving an execution must survive a second prune")
                    self.assertEqual(inspect_process_identity(identity), "alive")
                finally:
                    release.touch()
                awaited = waiter.result(timeout=60)
            self.assertEqual(awaited["outcome"], "unavailable", awaited)
            self.assertEqual(awaited["error"]["code"], "SERVICE_STOPPING")
            self.assertEqual(awaited["recovery"]["runId"], run_id)
            # The caller explicitly resumes observation after the installation.
            recovered = await_run({"runId": run_id, "waitSeconds": 90}, self.directory)
            self.assertEqual(recovered["outcome"], "completed", recovered)
            finished = wait_for(lambda: self.finished(run_id), timeout=35)
            self.assertIsNotNone(finished, self.client().get(runId=run_id))
            self.assertEqual(finished["selectedAttempt"]["attemptId"], original["attemptId"])
            self.assertEqual(finished["selectedAttempt"]["result"]["status"], "ok")
            self.assertTrue(finished["shutdownConfirmed"])
            self.assertTrue(wait_for(lambda: inspect_process_identity(identity) == "identity-absent", timeout=15))
            with closing(sqlite3.connect(self.directory / "board.sqlite3")) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts WHERE task_id=?", (run_id,)).fetchone()[0], 1)
            store = BoardStore(self.directory)
            try:
                prune_old_runtimes(store)
            finally:
                store.db.close()
            self.assertFalse(first.exists(), "finished execution no longer pins its original runtime")

    def test_same_contract_failure_rolls_back_runtime_without_losing_post_start_event(self):
        first = self.private_runtime("rollback-one", 1)
        second = self.private_runtime("rollback-two", 2)
        self.catalog_fixture()
        with self.installation(second):
            self.start(first)
            run_id, release, before = self.command("rollback-live")
            original_verify = upgrade.verify_started
            event_head_before = self.event_head()
            injected = []

            def fail_after_target_event(state, target, health, expected, **kwargs):
                if target == second:
                    store = BoardStore(self.directory)
                    try:
                        with store.db.write() as db:
                            injected.append(store._append_event(db, "test.install-post-start",
                                                                task_id=run_id,
                                                                attempt_id=before["attemptId"]))
                    finally:
                        store.db.close()
                    raise RuntimeError("injected target verification failure")
                return original_verify(state, target, health, expected, **kwargs)

            with mock.patch.object(upgrade, "verify_started", side_effect=fail_after_target_event):
                result = upgrade.upgrade()
            self.assertFalse(result["upgraded"], result)
            self.assertEqual(result["error"]["code"], "UPGRADE_ROLLED_BACK")
            self.assertNotIn("backup", result)
            self.assertEqual(len(injected), 1)
            self.assertGreater(injected[0], event_head_before)
            with closing(sqlite3.connect(self.directory / "board.sqlite3")) as connection:
                self.assertEqual(connection.execute("SELECT kind FROM events WHERE seq=?", (injected[0],)).fetchone()[0],
                                 "test.install-post-start")
            self.assertEqual(self.client().call("health", {})["runtimeContentId"], first.name)
            attached = wait_for(lambda: self.running(run_id), timeout=30)
            self.assertIsNotNone(attached, self.client().get(runId=run_id))
            self.assertEqual(attached["selectedAttempt"]["attemptId"], before["attemptId"])
            release.touch()
            finished = wait_for(lambda: self.finished(run_id), timeout=35)
            self.assertIsNotNone(finished, self.client().get(runId=run_id))
            self.assertEqual(finished["selectedAttempt"]["attemptId"], before["attemptId"])
            self.assertEqual(finished["selectedAttempt"]["result"]["status"], "ok")

    def test_same_contract_schema_mismatch_refuses_before_mutation(self):
        first = self.private_runtime("schema-one", 1)
        second = self.private_runtime("schema-two", 2)
        self.catalog_fixture()
        with self.installation(second):
            self.start(first)
            run_id, release, before = self.command("schema-refusal")
            head = self.event_head()
            with mock.patch.object(upgrade, "SCHEMA_VERSION", SCHEMA_VERSION + 1), \
                 mock.patch.object(upgrade.runtime, "materialize", side_effect=AssertionError("materialized")):
                with self.assertRaises(BoardError) as caught:
                    upgrade.upgrade()
            self.assertEqual(caught.exception.code, "UNSUPPORTED_SCHEMA")
            self.assertEqual(self.event_head(), head)
            self.assertFalse((self.directory / "upgrade.json").exists())
            self.assertEqual(self.client().call("health", {})["runtimeContentId"], first.name)
            attached = wait_for(lambda: self.running(run_id), timeout=30)
            self.assertIsNotNone(attached, self.client().get(runId=run_id))
            self.assertEqual(attached["selectedAttempt"]["attemptId"], before["attemptId"])
            release.touch()
            self.assertIsNotNone(wait_for(lambda: self.finished(run_id), timeout=35))

    def test_changed_contract_refuses_busy_board_before_runtime_preparation(self):
        first = self.private_runtime("contract-one", 1)
        major, minor, _patch = map(int, CONTRACT_VERSION.split("."))
        target_contract = f"{major}.{minor + 1}.0"
        second = self.private_runtime("contract-two", 1, contract_version=target_contract)
        self.catalog_fixture()
        with self.installation(second):
            self.start(first)
            run_id, release, before = self.command("contract-busy")
            head = self.event_head()
            self.assertIn(f'CONTRACT_VERSION = "{target_contract}"',
                          (second / "src/hey_my_buddy/protocol/contracts.py").read_text(encoding="utf-8"))
            with mock.patch.object(upgrade, "CONTRACT_VERSION", target_contract), \
                 mock.patch.object(upgrade.runtime, "materialize", side_effect=AssertionError("materialized")):
                with self.assertRaises(BoardError) as caught:
                    upgrade.upgrade()
            self.assertEqual(caught.exception.code, "UPGRADE_NOT_IDLE")
            self.assertEqual(self.event_head(), head)
            self.assertFalse((self.directory / "upgrade.json").exists())
            self.assertEqual(self.client().call("health", {})["runtimeContentId"], first.name)
            self.assertEqual(self.running(run_id)["selectedAttempt"]["attemptId"], before["attemptId"])
            release.touch()
            self.assertIsNotNone(wait_for(lambda: self.finished(run_id), timeout=35))

    def test_interrupted_same_contract_journal_recovers_without_restoring_board(self):
        first = self.private_runtime("interrupted-one", 1)
        second = self.private_runtime("interrupted-two", 2)
        self.catalog_fixture()
        with self.installation(second):
            self.start(first)
            run_id, release, before = self.command("interrupted-live")
            head = self.event_head()
            with mock.patch.object(upgrade, "verify_started", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    upgrade.upgrade()
            journal = json.loads((self.directory / "upgrade.json").read_text(encoding="utf-8"))
            self.assertFalse(journal["contractChanged"])
            self.assertEqual(journal["before"]["schema"], SCHEMA_VERSION)
            self.assertNotIn("backup", journal)
            self.assertFalse((self.directory / "backups/current").exists())
            with mock.patch.object(upgrade, "idle_snapshot", side_effect=AssertionError("idle snapshot")), \
                 mock.patch.object(upgrade, "restore", side_effect=AssertionError("restored board")):
                recovered = upgrade.recover(self.directory, first.parent)
            self.assertFalse(recovered["upgraded"], recovered)
            self.assertTrue(recovered["recoveredJournal"])
            self.assertNotIn("backup", recovered)
            self.assertFalse((self.directory / "upgrade.json").exists())
            self.assertGreaterEqual(self.event_head(), head)
            self.assertEqual(self.client().call("health", {})["runtimeContentId"], first.name)
            attached = wait_for(lambda: self.running(run_id), timeout=30)
            self.assertIsNotNone(attached, self.client().get(runId=run_id))
            self.assertEqual(attached["selectedAttempt"]["attemptId"], before["attemptId"])
            release.touch()
            self.assertIsNotNone(wait_for(lambda: self.finished(run_id), timeout=35))

    def test_offline_harness_finishes_after_same_contract_install(self):
        first = self.private_runtime("harness-one", 1)
        second = self.private_runtime("harness-two", 2)
        script, started, release, candidate = recovery_fixture.PrivateExecutionRecoveryTests.offline_router(self)
        self.addCleanup(release.touch)
        with self.installation(second, overrides={"HMB_DSH_CLI": str(script)}):
            self.start(first)
            created = self.client().call("selection_request", {
                "requestId": "router-live-install", "task": "Choose an offline fixture buddy"})
            self.assertTrue(wait_for(started.exists, timeout=60), created)
            run_id = created["runId"]
            running = wait_for(lambda: self.running(run_id), timeout=30)
            self.assertIsNotNone(running, self.client().get(runId=run_id))
            before = running["selectedAttempt"]
            identity = before["processIdentity"]
            result = upgrade.upgrade()
            self.assertTrue(result["upgraded"], result)
            self.assertNotIn("backup", result)
            self.assertEqual(inspect_process_identity(identity), "alive")
            self.assertTrue(first.exists())
            attached = wait_for(lambda: self.running(run_id), timeout=30)
            self.assertIsNotNone(attached, self.client().get(runId=run_id))
            self.assertEqual(attached["selectedAttempt"]["attemptId"], before["attemptId"])
            release.touch()
            decision = wait_for(lambda: self._completed_decision(created["decisionId"]), timeout=45)
            self.assertIsNotNone(decision)
            self.assertEqual(decision["buddyId"], candidate)

    def _completed_decision(self, decision_id: str) -> dict | None:
        decision = self.client().call("selection_get", {"decisionId": decision_id})["decision"]
        return decision if decision["status"] == "completed" else None

    def test_frozen_execution_stays_unknown_until_identity_based_cancel(self):
        if not hasattr(signal, "SIGSTOP") or not hasattr(os, "killpg"):
            self.skipTest("frozen execution requires POSIX signals")
        first = self.private_runtime("frozen-one", 1)
        second = self.private_runtime("frozen-two", 2)
        self.catalog_fixture()
        identity = None
        release = self.directory / "frozen-install.release"
        try:
            with self.installation(second):
                self.start(first)
                run_id, release, before = self.command("frozen-install")
                identity = before["processIdentity"]
                os.kill(identity["pid"], signal.SIGSTOP)
                result = upgrade.upgrade()
                self.assertTrue(result["upgraded"], result)
                self.assertNotIn("backup", result)
                unknown = wait_for(lambda: recovery_fixture.PrivateExecutionRecoveryTests._unknown(self, run_id), timeout=35)
                self.assertIsNotNone(unknown, self.client().get(runId=run_id))
                self.assertEqual(unknown["selectedAttempt"]["attemptId"], before["attemptId"])
                self.assertEqual(inspect_process_identity(identity), "alive")
                self.assertTrue(first.exists())
                code, response = self.cli("execution-cancel", json.dumps({
                    "runId": run_id, "note": "frozen private install"}))
                self.assertEqual(code, 0, response)
                forced = wait_for(lambda: self.client().get(runId=run_id)["selectedAttempt"].get("forceRequestedAt"),
                                  timeout=35)
                self.assertIsNotNone(forced)
                finished = wait_for(lambda: self.finished(run_id), timeout=45)
                self.assertIsNotNone(finished, self.client().get(runId=run_id))
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
