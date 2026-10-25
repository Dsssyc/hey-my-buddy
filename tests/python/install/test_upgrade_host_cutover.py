"""Private daemon cutover preserves Host receipts and retriable waits."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import copy
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import venv
from unittest import mock
from blackboard.tasks.test_workflow import WorkflowTestCase
from private_acl import assert_private_path
from hey_my_buddy.cli.blocking import await_run
from hey_my_buddy.cli.main import _read_control, _scrub_and_save
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.protocol.transport import _call_service
from hey_my_buddy.install import launcher, runtime, upgrade


ROOT = Path(__file__).resolve().parents[3]
DAEMON_FIXTURE = ROOT / "tests/python/blackboard/service/fixtures/daemon_with_catalog.py"


class HostCutoverTests(WorkflowTestCase):
    def private_runtime(self, generation, *, record_environment=False):
        root = self.directory / "runtime"
        source = root / ("fixture-" + generation)
        source.mkdir(parents=True)
        runtime._copy_assets(ROOT, source)
        fixture_text = DAEMON_FIXTURE.read_text(encoding="utf-8")
        bootstrap = fixture_text
        if record_environment:
            entry = "    raise SystemExit(main())"
            self.assertEqual(bootstrap.count(entry), 1, "Offline daemon fixture entry changed")
            bootstrap = bootstrap.replace(
                entry,
                "    (Path(os.environ['HMB_STATE_DIR']) / 'daemon-environment.json').write_text(\n"
                "        json.dumps({'PATH': os.environ.get('PATH'), 'VIRTUAL_ENV': os.environ.get('VIRTUAL_ENV')}), encoding='utf-8', newline='\\n')\n"
                + entry,
            )
        (source / "src/hey_my_buddy/_upgrade_fixture_daemon.py").write_text(
            bootstrap + "\n# Fixture generation: " + generation + "\n", encoding="utf-8", newline="\n"
        )
        target = root / runtime.content_id(source)
        source.rename(target)
        environment = target / "venv"
        venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(environment)
        # EnvBuilder records its creation command, including the fixture's own
        # runtime path. Installed uv environments have no such command field.
        configuration = environment / "pyvenv.cfg"
        configuration.write_text("\n".join(line for line in configuration.read_text(encoding="utf-8").splitlines()
                                           if not line.startswith("command =")) + "\n", encoding="utf-8", newline="\n")
        site = (environment / "Lib" / "site-packages" if os.name == "nt" else
                environment / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages")
        dependencies = ("c_two", "portalocker", "pydantic", "pydantic_core", "zstandard",
                        "typing_extensions", "typing_inspection", "annotated_types", "psutil")
        if os.name == "nt":
            dependencies += ("pywin32",)
        for name in dependencies:
            distribution = metadata.distribution(name.replace("_", "-"))
            # Copy distribution metadata and native libraries with the package.
            for file in distribution.files:
                # Installed command scripts live outside site-packages; this
                # fixture only runs the libraries through its private Python.
                if ".." in file.parts or "__pycache__" in file.parts:
                    continue
                destination = site / file
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(distribution.locate_file(file), destination)
        (site / "hey-my-buddy-fixture.pth").write_text(str(target / "src") + "\n", encoding="utf-8", newline="\n")
        ready = {
            "format": runtime.RUNTIME_FORMAT, "state": "READY", "contentId": target.name,
            "source": str(target), "projectRoot": str(target), "environment": str(environment),
            "python": str(runtime.runtime_python(target)), "runtimeRoot": str(root),
            "resources": {name: str(path) for name, path in runtime.resource_paths(target).items()},
            "sourceCommit": None,
        }
        (target / "READY.json").write_text(json.dumps(ready), encoding="utf-8", newline="\n")
        self.assertTrue(runtime.is_ready(target))
        return target

    def test_upgrade_started_service_excludes_installer_venv_bin_from_path(self):
        installer = self.private_runtime("installer")
        target = self.private_runtime("service", record_environment=True)
        self.catalog_fixture()
        installer_bin = str(runtime.runtime_python(installer).parent)
        markers = [str(self.directory / name) for name in ("before", "middle", "after")]
        original_entries = os.environ.get("PATH", "").split(os.pathsep)
        inherited_entries = [markers[0], installer_bin, markers[1], *original_entries,
                             installer_bin, markers[2]]
        environment = self.child_environment({"HMB_RUNTIME_ROOT": str(installer.parent),
                                              "PATH": os.pathsep.join(inherited_entries)})
        # The package launcher has execed into the installer interpreter by
        # this point, so VIRTUAL_ENV is absent even though sys.prefix is a venv.
        for key in ("PYTHONPATH", "HMB_DEV_SOURCE", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
            environment.pop(key, None)
        coordinator = (
            "import json, sys\n"
            "from pathlib import Path\n"
            "from unittest import mock\n"
            "from hey_my_buddy.install import launcher, upgrade\n"
            "state, target = map(Path, sys.argv[1:])\n"
            "assert sys.prefix != sys.base_prefix\n"
            "original = launcher.entry_modules\n"
            "fixture = lambda path: {**original(path), 'daemon': 'hey_my_buddy._upgrade_fixture_daemon'}\n"
            "with mock.patch.object(launcher, 'entry_modules', side_effect=fixture):\n"
            "    print(json.dumps(upgrade.start(state, target)))"
        )
        result = subprocess.run([str(runtime.runtime_python(installer)), "-c", coordinator,
                                 str(self.directory), str(target)], cwd=installer, env=environment,
                                capture_output=True, text=True, timeout=65, encoding="utf-8")
        log = self.directory / "upgrade-start.log"
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout +
                         (log.read_text(encoding="utf-8")[-5000:] if log.exists() else ""))
        self.assertTrue(json.loads(result.stdout)["runtimeStable"])
        recorded = json.loads((self.directory / "daemon-environment.json").read_text(encoding="utf-8"))
        self.assertIsNone(recorded["VIRTUAL_ENV"])
        service_entries = recorded["PATH"].split(os.pathsep)
        self.assertNotIn(installer_bin, service_entries)
        self.assertEqual(service_entries,
                         [entry for entry in inherited_entries if entry != installer_bin])

    def test_host_receipt_and_wait_can_retry_across_real_private_cutover(self):
        board = self.board()
        submitted = self.submit(board)
        claimed = self.claim(board)
        self.finish_turn(board, claimed, disposition='assistance')
        boundary = board.call('workflow_get', {'runId': submitted['runId']})
        self.assertEqual(boundary['state'], 'awaiting-host')
        control = self.controls[submitted['runId']]
        saved = copy.deepcopy(submitted)
        _scrub_and_save(saved, self.directory)
        control_file = Path(saved['controlFile'])
        control_bytes = control_file.read_bytes()
        request = {'runId': submitted['runId'], 'commandId': 'cutover-scope',
                   'expectedRevision': boundary['revision'], 'expectedScopeVersion': 1,
                   'writeScope': ['src'], 'note': 'Host reviewed', **control}
        receipt = board.call('workflow_scope_amend', request)
        before = board.call('workflow_get', {'runId': submitted['runId']})
        board.store.db.close()
        previous = self.private_runtime('previous')
        target = self.private_runtime('target')
        self.catalog_fixture()
        environment = self.child_environment({'HMB_RUNTIME_ROOT': str(previous.parent),
                                              'HMB_MAX_CONCURRENT': '1', 'HMB_WAIT_CAPACITY': '4'})
        environment.pop('PYTHONPATH', None)
        environment.pop('HMB_DEV_SOURCE', None)
        original_modules = launcher.entry_modules
        fixture_modules = lambda path: {**original_modules(path), 'daemon': 'hey_my_buddy._upgrade_fixture_daemon'}
        entered = threading.Event()
        release = threading.Event()

        def delayed_wait(method, params, state_dir):
            if method == 'result':
                entered.set()
                self.assertTrue(release.wait(20))
            return _call_service(method, params, state_dir)

        with mock.patch.dict(os.environ, environment, clear=True), \
             mock.patch.object(launcher, 'entry_modules', side_effect=fixture_modules), \
             mock.patch.object(upgrade.runtime, 'materialize', return_value={'runtimeDir': str(target)}):
            started = upgrade.start(self.directory, previous)
            self.assertEqual(started['runtimeContentId'], previous.name)
            launcher.write_active_runtime(self.directory, previous)
            client = BoardClient(self.directory, autostart=False)
            with ThreadPoolExecutor(max_workers=1) as pool:
                waiter = pool.submit(await_run, {'runId': submitted['runId'], 'waitSeconds': 30},
                                     self.directory, service=delayed_wait)
                try:
                    self.assertTrue(entered.wait(10))
                    original_journal = upgrade.write_journal
                    saw_fence = []
                    def on_journal(path, value):
                        original_journal(path, value)
                        if path == self.directory / 'upgrade.json' and not saw_fence:
                            saw_fence.append(value['phase'])
                            release.set()
                    with mock.patch.object(upgrade, 'write_journal', side_effect=on_journal):
                        result = upgrade.upgrade(self.directory)
                    self.assertTrue(result['upgraded'], result)
                    self.assertEqual(saw_fence, ['preflight'])
                    waited = waiter.result(timeout=20)
                    self.assertEqual(waited['outcome'], 'waiting-host')
                    self.assertIsNone(waited['error'])
                    self.assertEqual(waited['request']['requestId'], before['activeRequest']['requestId'])
                finally:
                    release.set()
            after = client.call('workflow_get', {'runId': submitted['runId']})
            self.assertEqual(after['activeRequest'], before['activeRequest'])
            self.assertEqual(after['ownerGeneration'], before['ownerGeneration'])
            self.assertEqual(after['revision'], before['revision'])
            self.assertEqual(control_file.read_bytes(), control_bytes)
            assert_private_path(self, control_file, 0o600)
            self.assertEqual(_read_control(str(control_file)), control)
            self.assertEqual(client.call('workflow_scope_amend', request), {**receipt, 'duplicate': True})
            with closing(sqlite3.connect(self.directory / 'board.sqlite3')) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM commands WHERE command_id='cutover-scope'").fetchone()[0], 1)
            decided = client.call('workflow_decide', {'runId': submitted['runId'],
                'requestId': after['activeRequest']['requestId'], 'commandId': 'post-cutover-decision',
                'expectedRevision': after['revision'], 'decision': 'approve',
                'autoContinue': False, **_read_control(str(control_file))})
            self.assertEqual(decided['runId'], submitted['runId'])
