"""Real private daemon cutover, durable Host recovery and upgrade-spanning await.

Native discovery and automatic pool spawning are offline fixture boundaries.
The runtime trees, interpreters, RPC, Git, owner locks, backup and cutover are real.
No dependency is installed: private environments copy the runner's dependencies.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import copy
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import venv
from unittest import mock

from blackboard.tasks.test_workflow_real import CONFIGURATION, RealWorkspaceTestCase
from protocol.fixtures import native_turn
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.store.db import sha256_text
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.cli.blocking import await_run
from hey_my_buddy.cli.main import _read_control, _scrub_and_save
from hey_my_buddy.install import launcher, runtime, upgrade
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.protocol.transport import ServiceError, _call_service


ROOT = Path(__file__).resolve().parents[3]
DAEMON_FIXTURE = ROOT / "tests/python/blackboard/service/fixtures/daemon_with_catalog.py"


class HostCutoverTests(RealWorkspaceTestCase):
    def private_runtime(self, generation):
        root = self.directory / "runtime"
        source = root / ("fixture-" + generation)
        source.mkdir(parents=True)
        runtime._copy_assets(ROOT, source)
        # Preserve the existing native-catalog fixture. Keep fixture Worker
        # results caller-owned so neither a model nor an automatic child runs.
        fixture_text = DAEMON_FIXTURE.read_text()
        import_line = "from hey_my_buddy.blackboard.service.daemon import Daemon, main"
        self.assertEqual(fixture_text.count(import_line), 1, "Offline pool fixture import changed")
        bootstrap = fixture_text.replace(
            import_line,
            "from hey_my_buddy.blackboard.service.daemon import Daemon, WorkerPool, main\n"
            "WorkerPool.start = lambda pool: []\n"
            "WorkerPool.reconcile = lambda pool, store: pool.report()",
        )
        (source / "src/hey_my_buddy/_upgrade_fixture_daemon.py").write_text(
            bootstrap + "\n# Fixture generation: " + generation + "\n"
        )
        target = root / runtime.content_id(source)
        source.rename(target)
        environment = target / "venv"
        venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
        # EnvBuilder records its creation command, including the fixture's own
        # runtime path. Installed uv environments have no such command field.
        configuration = environment / "pyvenv.cfg"
        configuration.write_text("\n".join(line for line in configuration.read_text().splitlines()
                                           if not line.startswith("command =")) + "\n")
        site = environment / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        for name in ("c_two", "fastdb4py", "numpy", "portalocker", "pydantic", "pydantic_core", "zstandard",
                     "typing_extensions", "typing_inspection", "annotated_types"):
            module = importlib.import_module(name)
            path = Path(module.__file__)
            if path.name == "__init__.py":
                shutil.copytree(path.parent, site / name, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(path, site / path.name)
            distribution = metadata.distribution(name.replace("_", "-"))
            metadata_dir = next(file.parts[0] for file in distribution.files if file.parts[0].endswith(".dist-info"))
            shutil.copytree(distribution.locate_file(metadata_dir), site / metadata_dir)
        (site / "buddy-fixture.pth").write_text(str(target / "src") + "\n")
        ready = {
            "format": runtime.RUNTIME_FORMAT, "state": "READY", "contentId": target.name,
            "source": str(target), "projectRoot": str(target), "environment": str(environment),
            "python": str(runtime.runtime_python(target)), "runtimeRoot": str(root),
            "resources": {name: str(path) for name, path in runtime.resource_paths(target).items()},
            "sourceCommit": None,
        }
        (target / "READY.json").write_text(json.dumps(ready))
        self.assertTrue(runtime.is_ready(target))
        return target

    def snapshot(self):
        with closing(sqlite3.connect(self.directory / "board.sqlite3")) as connection:
            return backup.database_snapshot(connection)

    def finish(self, client, claim, *, nonce, session):
        identity = claim["claim"]["attempt"]
        manifest = claim["claim"]["turn"]["input"]["executionWorkspace"]
        seal = workspace.seal(self.directory, manifest, identity["taskId"], identity["attemptId"])
        record = native_turn.claim_record(claim["claim"], session_id=session, prompt_sha256=sha256_text("fixture prompt"),
            outcome={"disposition": "assistance", "summary": "Host decision required", "remaining": ["continue"],
                     "decisions": [], "artifacts": [], "request": {"summary": "review", "attempted": "fixture turn",
                     "neededWork": "decide", "expectedArtifacts": ["decision"], "acceptance": "reviewed"}})
        return client.call("worker_result", {
            "workerId": "fixture-worker", "attemptId": identity["attemptId"], "generation": identity["generation"],
            "nonce": nonce, "status": "ok", "shutdownConfirmed": True, "exitCode": 0,
            "result": {"status": "ok", "processState": {"shutdownConfirmed": True}, "turn": record,
                       "workspaceSeal": seal, "turnResultPath": str(self.directory / "fixture-turn.json")},
        })

    def submit_params(self, request_id):
        return {**CONFIGURATION, "requestId": request_id, "submissionToken": "t" * 32, "hostId": "host-1",
                "task": "private upgrade goal", "cwd": str(self.repo),
                "executionWorkspace": {"kind": "worktree", "access": "write"}}

    def test_same_schema_upgrade_preserves_host_boundary_receipts_and_retriable_wait(self):
        previous = self.private_runtime("previous")
        target = self.private_runtime("target")
        environment = self.child_environment({"BUDDY_RUNTIME_ROOT": str(previous.parent),
                                               "BUDDY_MAX_CONCURRENT": "1", "BUDDY_WAIT_CAPACITY": "4"})
        environment.pop("PYTHONPATH", None)
        environment.pop("BUDDY_DEV_SOURCE", None)
        entry_modules = launcher.entry_modules
        def fixture_modules(path):
            return {**entry_modules(path), "daemon": "hey_my_buddy._upgrade_fixture_daemon"}

        # The real coordinator launches both generations from their own
        # interpreters. Only preparation is replaced by already-built fixtures.
        with mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch.object(launcher, "entry_modules", side_effect=fixture_modules), \
                mock.patch.object(upgrade.runtime, "materialize", return_value={"runtimeDir": str(target)}):
            try:
                started = upgrade.start(self.directory, previous)
            except Exception:
                self.fail((self.directory / "upgrade-start.log").read_text()[-5000:])
            self.assertTrue(started["runtimeStable"], upgrade.command(self.directory, previous, "runtime"))
            self.assertEqual(started["runtimeContentId"], previous.name)
            launcher.write_active_runtime(self.directory, previous)
            client = BoardClient(self.directory, autostart=False)
            root = client.call("workflow_submit", self.submit_params("boundary"))
            control = dict(root["control"])
            saved = copy.deepcopy(root)
            _scrub_and_save(saved, self.directory)
            control_file = Path(saved["controlFile"])
            control_bytes = control_file.read_bytes()
            client.call("worker_register", {"workerId": "fixture-worker", "capabilities": ["dsh"]})
            claim = client.call("worker_claim", {"workerId": "fixture-worker", "runId": root["runId"],
                                                 "claimRequestId": "claim-root", "nonce": "n" * 16})
            self.assertIsNotNone(claim["claim"])
            wait_entered = threading.Event()
            wait_replied = threading.Event()
            release_wait = threading.Event()

            def wait_service(method, params, state_dir):
                if method in ("wait", "watch"):
                    wait_entered.set()
                    response = _call_service(method, params, state_dir)
                    wait_replied.set()
                    # Delay delivery of the real server reply until fencing:
                    # await already occupied a server wait slot before cutover.
                    if not release_wait.wait(15):
                        raise AssertionError("Upgrade did not release the private wait")
                    return response
                return _call_service(method, params, state_dir)

            with ThreadPoolExecutor(max_workers=1) as pool:
                waiter = pool.submit(await_run, {"runId": root["runId"], "waitSeconds": 30},
                                     self.directory, service=wait_service)
                finished_root = False
                try:
                    self.assertTrue(wait_entered.wait(10), "await did not enter its existing-run wait")
                    deadline = time.monotonic() + 10
                    while client.call("wait_capacity", {}, resource="wait")["admitted"] != 1:
                        self.assertLess(time.monotonic(), deadline, "Server did not admit the private wait")
                        time.sleep(.05)
                    self.finish(client, claim, nonce="n" * 16, session="root-session")
                    finished_root = True
                    self.assertTrue(wait_replied.wait(10), "Server wait did not observe the finished turn")
                    # A second stopped goal supplies the commandId retry without
                    # consuming the first goal's still-open Host boundary.
                    other = client.call("workflow_submit", self.submit_params("cancel-after-upgrade"))
                    claimed = client.call("worker_claim", {"workerId": "fixture-worker", "runId": other["runId"],
                        "claimRequestId": "claim-other", "nonce": "m" * 16})
                    self.finish(client, claimed, nonce="m" * 16, session="other-session")
                    before = client.call("workflow_get", {"runId": root["runId"]})
                    lost_command = {"runId": root["runId"], "commandId": "lost-scope-reply",
                        "expectedRevision": before["revision"], "expectedScopeVersion": 1,
                        "writeScope": ["src"], "reason": "private authorized scope", **control}
                    first_receipt = client.call("workflow_scope_amend", lost_command)
                    before = client.call("workflow_get", {"runId": root["runId"]})
                    with closing(sqlite3.connect(self.directory / "board.sqlite3")) as db:
                        reservations = db.execute("SELECT * FROM workspace_reservations ORDER BY checkout_id").fetchall()
                    checkout = Path(root["workspace"]["path"])
                    checkout_head = self.git_at(checkout, "rev-parse", "HEAD")
                    cancel = {"runId": other["runId"], "commandId": "fenced-cancel", "reason": "private fixture",
                              **other["control"]}
                    submit = self.submit_params("fenced-submission")
                    write_journal = upgrade.write_journal
                    observed = []

                    def on_journal(path, value):
                        write_journal(path, value)
                        if path == self.directory / "upgrade.json" and not observed:
                            observed.append(value["phase"])
                            fenced_before = self.snapshot()
                            for operation, params in (("workflow_cancel", cancel), ("workflow_submit", submit)):
                                with self.assertRaises(ServiceError) as caught:
                                    client.call(operation, params)
                                self.assertEqual(caught.exception.code, "UPGRADE_IN_PROGRESS")
                                self.assertIn("retry", str(caught.exception))
                                self.assertEqual(self.snapshot(), fenced_before)
                            release_wait.set()
                            waited = waiter.result(timeout=10)
                            self.assertEqual(waited["outcome"], "unavailable")
                            self.assertEqual(waited["error"]["code"], "UPGRADE_IN_PROGRESS")
                            self.assertEqual(waited["recovery"]["runId"], root["runId"])
                            self.assertIn("await", waited["recovery"]["action"])

                    with mock.patch.object(upgrade, "write_journal", side_effect=on_journal):
                        result = upgrade.upgrade({})
                    self.assertTrue(result["upgraded"], str(result) + (self.directory / "upgrade-start.log").read_text()[-5000:])
                    self.assertEqual(observed, ["preflight"])
                    self.assertTrue(result["verification"]["retainedDataFingerprints"])
                    self.assertEqual(client.health()["runtimeContentId"], target.name)
                    self.assertFalse((self.directory / "upgrade.json").exists())
                    after = client.call("workflow_get", {"runId": root["runId"]})
                    self.assertEqual(after["state"], "awaiting-host")
                    self.assertEqual(after["activeRequest"], before["activeRequest"])
                    self.assertEqual(after["revision"], before["revision"])
                    self.assertEqual(after["ownerGeneration"], before["ownerGeneration"])
                    self.assertEqual(control_file.read_bytes(), control_bytes)
                    self.assertEqual(control_file.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(_read_control(str(control_file)), control)
                    self.assertTrue(checkout.is_dir())
                    self.assertEqual(self.git_at(checkout, "rev-parse", "HEAD"), checkout_head)
                    with closing(sqlite3.connect(self.directory / "board.sqlite3")) as db:
                        self.assertEqual(db.execute("SELECT * FROM workspace_reservations ORDER BY checkout_id").fetchall(), reservations)
                    replay = client.call("workflow_scope_amend", lost_command)
                    self.assertEqual(replay, {**first_receipt, "duplicate": True})
                    with closing(sqlite3.connect(self.directory / "board.sqlite3")) as db:
                        self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_scope_versions WHERE run_id=?",
                                                    (root["runId"],)).fetchone()[0], 2)
                        self.assertEqual(db.execute("SELECT COUNT(*) FROM commands WHERE command_id=?",
                                                    (lost_command["commandId"],)).fetchone()[0], 1)
                        self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE task_id=? AND kind='workflow.scope_amended'",
                                                    (root["runId"],)).fetchone()[0], 1)
                    cancelled = client.call("workflow_cancel", cancel)
                    self.assertFalse(cancelled["duplicate"])
                    self.assertEqual(client.call("workflow_cancel", cancel), {**cancelled, "duplicate": True})
                    submitted = client.call("workflow_submit", submit)
                    recovered = client.call("workflow_submit", submit)
                    self.assertEqual(recovered["runId"], submitted["runId"])
                    self.assertEqual(recovered["control"], submitted["control"])
                    with closing(sqlite3.connect(self.directory / "board.sqlite3")) as db:
                        self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks WHERE request_id=?",
                                                    (submit["requestId"],)).fetchone()[0], 1)
                    # Present the saved control file's exact capability after the
                    # cutover; decide settles the request, continue queues one turn.
                    decided = client.call("workflow_decide", {"runId": root["runId"],
                        "requestId": after["activeRequest"]["requestId"], "commandId": "post-upgrade-decision",
                        "expectedRevision": after["revision"], "decision": "approve", "autoContinue": False,
                        **_read_control(str(control_file))})
                    continued = client.call("workflow_continue", {"runId": root["runId"],
                        "commandId": "post-upgrade-continuation", "expectedRevision": decided["revision"],
                        "input": "continue after private cutover", "helperPolicy": "keep", **control})
                    self.assertEqual(continued["state"], "executing")
                    with closing(sqlite3.connect(self.directory / "board.sqlite3")) as db:
                        self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_continuations WHERE run_id=? AND state='queued'",
                                                    (root["runId"],)).fetchone()[0], 1)
                finally:
                    if not finished_root:
                        self.finish(client, claim, nonce="n" * 16, session="root-session")
                    release_wait.set()

    def git_at(self, checkout, *args):
        from blackboard.tasks.test_workflow_real import GIT_ENV
        result = subprocess.run(["git", *args], cwd=checkout, env={**os.environ, **GIT_ENV},
                                capture_output=True, text=True, check=True)
        return result.stdout.strip()
