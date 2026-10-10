"""Test fixtures must retire their own detached daemon before deleting state."""
from __future__ import annotations

import fcntl
import json
import os
import runpy
import shutil
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from support import (
    BoardTestCase, PYTHON_ROOT, _child_environment, _lock_held, private_state_dir,
    stop_private_service, stop_private_workers, wait_for, write_catalog_fixture,
)
from hey_my_buddy.protocol.transport import _read_endpoint


def assert_released(test: unittest.TestCase, handles: list[int]) -> None:
    for fd in handles:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            test.fail("A test daemon or supervisor still holds its lifetime lock")
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)


class FixtureCleanupTests(unittest.TestCase):
    def test_unconfirmed_shutdown_preserves_private_state(self):
        directory = None
        try:
            with self.assertRaisesRegex(AssertionError, "shutdown unconfirmed"):
                with patch("support.stop_private_service", side_effect=AssertionError("shutdown unconfirmed")):
                    with private_state_dir() as directory:
                        (directory / "receipt.json").write_text("evidence")
            self.assertEqual((directory / "receipt.json").read_text(), "evidence")
        finally:
            if directory is not None:
                shutil.rmtree(directory)

    def test_child_environment_drops_inherited_ownership_and_credentials(self):
        inherited = {
            "BUDDY_STATE_DIR": "/wrong-state",
            "BUDDY_RUNTIME_ROOT": "/wrong-runtime",
            "BUDDY_RUNTIME": "/pinned-runtime",
            "BUDDY_RUNTIME_IDENTITY": "pinned",
            "BUDDY_WORKER_STATE": "/wrong-worker",
            "BUDDY_WORKER_ID": "wrong-worker",
            "BUDDY_AGENT_CREDENTIAL": "secret",
            "BUDDY_AGENT_CREDENTIAL_FILE": "/secret-file",
            "VIRTUAL_ENV": "/wrong-venv",
            "UV_PROJECT_ENVIRONMENT": "/wrong-uv",
            "BUDDY_MODEL_CATALOG_FILE": "/inherited-catalog",
            "BUDDY_ACCOUNT_SELECTION": "inherited-account",
            "BUDDY_UNKNOWN_PIN": "inherited-pin",
            "ANTHROPIC_BASE_URL": "https://gateway.invalid",
            "ANTHROPIC_API_KEY": "synthetic-secret",
            "ANTHROPIC_MODEL": "inherited-model",
            "C2_IPC_ROOT": "/inherited-ipc",
            "C2_UNKNOWN_PIN": "inherited-pin",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "CLAUDE_CODE_USE_VERTEX": "1",
            "CLAUDE_CODE_USE_FOUNDRY": "1",
            "AZURE_API_INGESTION_URL": "https://gateway.invalid",
        }
        with private_state_dir() as directory, patch.dict(os.environ, inherited):
            environment = _child_environment(directory)
            self.assertEqual(environment["BUDDY_STATE_DIR"], str(directory))
            self.assertEqual(environment["BUDDY_RUNTIME_ROOT"], str(directory / "runtime-root"))
            for key in inherited.keys() - {"BUDDY_STATE_DIR", "BUDDY_RUNTIME_ROOT"}:
                self.assertNotIn(key, environment)

    def test_child_environment_preserves_explicit_private_paths(self):
        with private_state_dir() as directory:
            runtime_root = directory / "alternate-runtime"
            python_path = os.pathsep.join((str(PYTHON_ROOT), str(directory / "helpers")))
            environment = _child_environment(directory, {
                "BUDDY_STATE_DIR": "/wrong-state",
                "BUDDY_RUNTIME_ROOT": str(runtime_root),
                "PYTHONPATH": python_path,
            })
            self.assertEqual(environment["BUDDY_STATE_DIR"], str(directory))
            self.assertEqual(environment["BUDDY_RUNTIME_ROOT"], str(runtime_root))
            self.assertEqual(environment["PYTHONPATH"], python_path)
            with self.assertRaisesRegex(ValueError, "private state directory"):
                _child_environment(directory, {"BUDDY_RUNTIME_ROOT": "/wrong-runtime"})

    def test_r06_child_uses_private_state_and_explicit_catalog(self):
        """Probe a real interpreter and SQLite file, with default lookup refused."""
        with private_state_dir() as directory:
            catalog = write_catalog_fixture(directory)
            inherited = {
                "BUDDY_STATE_DIR": str(directory / "inherited-state"),
                "BUDDY_RUNTIME_ROOT": str(directory / "inherited-runtime"),
                "BUDDY_MODEL_CATALOG_FILE": str(directory / "inherited-catalog"),
                "BUDDY_RUNTIME": "inherited-runtime",
                "BUDDY_RUNTIME_IDENTITY": "inherited-identity",
                "BUDDY_AGENT_CREDENTIAL": "synthetic-secret",
                "BUDDY_AGENT_CREDENTIAL_FILE": str(directory / "inherited-credential"),
                "BUDDY_WORKER_ID": "inherited-worker",
                "BUDDY_WORKER_STATE": str(directory / "inherited-worker"),
                "BUDDY_ACCOUNT_SELECTION": "inherited-account",
                "BUDDY_UNKNOWN_PIN": "inherited-pin",
                "ANTHROPIC_API_KEY": "synthetic-secret",
                "ANTHROPIC_BASE_URL": "https://gateway.invalid",
                "C2_IPC_ROOT": str(directory / "inherited-ipc"),
                "VIRTUAL_ENV": str(directory / "inherited-venv"),
                "UV_PROJECT_ENVIRONMENT": str(directory / "inherited-uv"),
                "HOME": str(directory / "inherited-home"),
                "CODEX_HOME": str(directory / "inherited-codex"),
                "CLAUDE_CONFIG_DIR": str(directory / "inherited-claude"),
                "DSH_HOME": str(directory / "inherited-dsh"),
                "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": str(directory / "inherited-builtin.json"),
                "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE": str(directory / "inherited-personal.json"),
            }
            with patch.dict(os.environ, inherited):
                environment = _child_environment(directory, {"BUDDY_MODEL_CATALOG_FILE": str(catalog)})
            completed = subprocess.run([sys.executable, "-c", textwrap.dedent("""
                import json, os
                from pathlib import Path
                from unittest.mock import patch
                import c_two as cc
                from hey_my_buddy import home
                from hey_my_buddy.protocol.transport import get_state_dir
                from hey_my_buddy.protocol.rpc_config import configure_local_endpoint
                from hey_my_buddy.blackboard.store.store import BoardStore
                from hey_my_buddy.blackboard.catalog.catalog import discover
                from hey_my_buddy.install.runtime import runtime_root
                from hey_my_buddy.buddy.harnesses import discovery
                def reject_native(adapter, **kwargs):
                    raise AssertionError('R-06: native discovery forbidden: ' + adapter)
                discovery.candidate_snapshot = reject_native
                with patch.object(home, 'default_state_dir', side_effect=AssertionError('default state lookup')):
                    state = get_state_dir()
                    store = BoardStore(state)
                    store.initialize()
                    ipc = configure_local_endpoint(state)
                    payload = discover(directory=state)
                keys = {'VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT', 'HOME', 'USERPROFILE',
                        'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'ZCODE_DATA_BASE_DIR',
                        'DSH_HOME', 'ZCODE_BUILTIN_PROVIDER_CONFIG_FILE', 'ZCODE_PERSONAL_PROVIDER_CONFIG_FILE',
                        'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME',
                        'XDG_RUNTIME_DIR', 'APPDATA', 'LOCALAPPDATA'}
                selected = {key: value for key, value in os.environ.items()
                            if key in keys or key.startswith(('BUDDY_', 'ANTHROPIC_', 'C2_'))}
                result = {'environment': selected, 'state': str(state),
                          'database': str(store.db.path), 'ipc': str(ipc),
                          'runtime': str(runtime_root()), 'catalog': payload['source']}
                assert cc.shutdown()['completed']
                print(json.dumps(result))
            """)], env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            probe = json.loads(completed.stdout)
            self.assertEqual(probe["state"], str(directory))
            self.assertEqual(probe["runtime"], str(directory / "runtime-root"))
            self.assertEqual(probe["ipc"], str(directory / "ipc"))
            self.assertEqual(probe["catalog"], f"file:{catalog}")
            self.assertEqual(probe["environment"]["BUDDY_MODEL_CATALOG_FILE"], str(catalog))
            for key in inherited.keys() - {"BUDDY_STATE_DIR", "BUDDY_RUNTIME_ROOT", "BUDDY_MODEL_CATALOG_FILE",
                                           "HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"}:
                if key in {"DSH_HOME", "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE", "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"}:
                    self.assertNotEqual(probe["environment"][key], inherited[key])
                    continue
                self.assertNotIn(key, probe["environment"])
            self.assertEqual(probe["environment"]["HOME"], str(directory / "home"))
            self.assertEqual(probe["environment"]["CODEX_HOME"], str(directory / "home/.codex"))
            self.assertEqual(probe["environment"]["CLAUDE_CONFIG_DIR"], str(directory / "home/.claude"))
            database = Path(probe["database"])
            self.assertTrue(database.is_relative_to(directory))
            self.assertEqual(database.read_bytes()[:16], b"SQLite format 3\x00")
            for key in ("HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "ZCODE_DATA_BASE_DIR",
                        "DSH_HOME", "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE", "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE",
                        "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
                        "XDG_RUNTIME_DIR", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
                self.assertTrue(Path(probe["environment"][key]).is_relative_to(directory), key)
            self.assertEqual(list((directory / "home").iterdir()), [])
            for path in ("inherited-state", "inherited-runtime", "inherited-ipc", "home/.local/share/hey-my-buddy/state"):
                self.assertFalse((directory / path).exists(), path)

    def test_r06_catalog_owner_passes_fixture_after_inherited_pins_are_removed(self):
        fixture = BoardTestCase("runTest")
        fixture.setUp()
        try:
            path = fixture.catalog_fixture()
            with patch.dict(os.environ, {"BUDDY_MODEL_CATALOG_FILE": "/wrong-catalog"}):
                self.assertNotIn("BUDDY_MODEL_CATALOG_FILE", _child_environment(fixture.directory))
                owned = fixture.child_environment()
                self.assertIn("BUDDY_MODEL_CATALOG_FILE", owned, "owner lost its explicit catalog")
                self.assertEqual(owned["BUDDY_MODEL_CATALOG_FILE"], str(path))
                # Both public helpers must use the owner's explicit environment.
                with patch("support.subprocess.run") as run:
                    run.return_value = subprocess.CompletedProcess([], 0, "{}", "")
                    fixture.cli("paths")
                self.assertIn("BUDDY_MODEL_CATALOG_FILE", run.call_args.kwargs["env"])
                self.assertEqual(run.call_args.kwargs["env"]["BUDDY_MODEL_CATALOG_FILE"], str(path))
                with patch("support.subprocess.Popen") as launch, patch(
                    "hey_my_buddy.protocol.transport._read_endpoint", return_value={"address": "fixture"},
                ), patch("hey_my_buddy.protocol.transport._request", return_value={"managedWorkerIds": []}):
                    launch.return_value.poll.return_value = 0
                    with fixture.daemon():
                        pass
                self.assertIn("BUDDY_MODEL_CATALOG_FILE", launch.call_args.kwargs["env"])
                self.assertEqual(launch.call_args.kwargs["env"]["BUDDY_MODEL_CATALOG_FILE"], str(path))
                self.assertTrue(launch.call_args.args[0][-1].endswith("daemon_with_catalog.py"))
            with self.assertRaisesRegex(ValueError, "private state directory"):
                fixture.child_environment({"BUDDY_MODEL_CATALOG_FILE": "/wrong-catalog"})
            with self.assertRaisesRegex(ValueError, "private state directory"):
                fixture.child_environment({"HOME": "/wrong-home"})
        finally:
            fixture.doCleanups()

    def test_r05_catalog_daemon_fixture_binds_capabilities_without_native_discovery(self):
        """Exercise the existing daemon fixture without binding a socket."""
        from hey_my_buddy.blackboard.service.daemon import Daemon
        from hey_my_buddy.blackboard.service.service import dispatch_local

        with private_state_dir() as directory:
            catalog = write_catalog_fixture(directory)
            environment = _child_environment(directory, {"BUDDY_MODEL_CATALOG_FILE": str(catalog)})
            fixture = runpy.run_path(str(PYTHON_ROOT.parent / "tests/python/blackboard/service/fixtures/daemon_with_catalog.py"))
            with patch.dict(os.environ, environment, clear=True), patch(
                "hey_my_buddy.buddy.harnesses.discovery.candidate_snapshot",
                side_effect=AssertionError("R-05: native discovery forbidden"),
            ) as native:
                daemon = Daemon(directory)
                daemon.store.initialize()
                service = fixture["service"](daemon)
                capabilities = json.loads(service.capabilities(json.dumps({"token": daemon.token})))
                self.assertFalse(native.called, "fixture capabilities attempted native discovery")
                self.assertNotIn("error", capabilities)
                self.assertTrue(capabilities["adapters"]["dsh"]["available"])
                refreshed = dispatch_local(service, "model_catalog_refresh", {"requestId": "r05-fixture"})
                self.assertEqual(refreshed["catalog"]["source"], f"file:{catalog}")
                payload = json.loads(catalog.read_text())
                self.assertEqual(refreshed["catalog"]["providers"][0]["models"][0]["id"],
                                 payload["providers"][0]["models"][0]["id"])
                payload["providers"][0]["models"][0]["id"] = "r05-fixture-changed-model"
                catalog.write_text(json.dumps(payload))
                reread = dispatch_local(service, "model_catalog_refresh", {"requestId": "r05-fixture-reread"})
                self.assertEqual(reread["catalog"]["providers"][0]["models"][0]["id"],
                                 payload["providers"][0]["models"][0]["id"])
                self.assertFalse(native.called, "fixture catalog attempted native discovery")
            self.assertEqual(list((directory / "home").iterdir()), [])

    def test_cleanup_stops_cli_started_replacement_after_restart(self):
        fixture = BoardTestCase("runTest")
        fixture.setUp()
        directory = fixture.directory
        handles = []
        try:
            with patch.dict(os.environ):
                fixture.catalog_fixture()
            with fixture.daemon(env={"BUDDY_MAX_CONCURRENT": "1"}) as original:
                original_id = _read_endpoint(directory)["serviceId"]
                code, restarted = fixture.cli("restart", json.dumps({"drainSeconds": 0}))
                self.assertEqual(code, 0, restarted)
                self.assertTrue(restarted["restarting"])
                original.wait(timeout=20)

            # The CLI detaches this new daemon, so it is absent from children.
            code, health = fixture.cli("health", env={"BUDDY_MAX_CONCURRENT": "1"})
            self.assertEqual(code, 0, health)
            self.assertNotEqual(_read_endpoint(directory)["serviceId"], original_id)
            self.assertTrue(wait_for(lambda: _lock_held(directory / "workers/local/supervisor.lock")))
            for path in ("control-daemon.lock", "board-owner.lock", "workers/local/supervisor.lock"):
                handles.append(os.open(directory / path, os.O_RDWR))

            # Leave the directory available for a protocol cleanup if the assertion
            # fails; the lock FDs also let us inspect ownership after deletion.
            with patch("support.shutil.rmtree") as remove:
                fixture._cleanup()
            remove.assert_called_once_with(directory)
            assert_released(self, handles)
        finally:
            if directory.exists():
                stop_private_service(directory)
                stop_private_workers(directory)
                shutil.rmtree(directory)
            for fd in handles:
                os.close(fd)

    def test_private_state_context_stops_its_cli_started_service(self):
        handles = []
        directory = None
        try:
            with patch("support.shutil.rmtree") as remove:
                with private_state_dir() as directory:
                    catalog = write_catalog_fixture(directory)
                    environment = _child_environment(directory, {
                        "BUDDY_MAX_CONCURRENT": "1", "BUDDY_MODEL_CATALOG_FILE": str(catalog),
                    })
                    completed = subprocess.run(
                        [sys.executable, "-m", "hey_my_buddy.cli.main", "health"], env=environment,
                        capture_output=True, text=True, timeout=60,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertTrue(wait_for(lambda: _lock_held(directory / "workers/local/supervisor.lock")))
                    for path in ("control-daemon.lock", "board-owner.lock", "workers/local/supervisor.lock"):
                        handles.append(os.open(directory / path, os.O_RDWR))
            remove.assert_called_once_with(directory)
            assert_released(self, handles)
        finally:
            if directory is not None and directory.exists():
                stop_private_service(directory)
                stop_private_workers(directory)
                shutil.rmtree(directory)
            for fd in handles:
                os.close(fd)


if __name__ == "__main__":
    unittest.main()
