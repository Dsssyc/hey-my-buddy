"""Test fixtures must retire their own detached daemon before deleting state."""
from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from support import (
    BoardTestCase, PYTHON_ROOT, _child_environment, _lock_held, private_state_dir,
    remove_private_test_root, request_private_service_stop, stop_private_service, wait_for, write_catalog_fixture,
)
from hey_my_buddy import locking
from hey_my_buddy.protocol.transport import _read_endpoint
import support


def assert_released(test: unittest.TestCase, handles: list[int]) -> None:
    for fd in handles:
        try:
            locking.lock(fd, blocking=False)
        except BlockingIOError:
            test.fail("A test daemon still holds its lifetime lock")
        else:
            locking.unlock(fd)


class FixtureCleanupTests(unittest.TestCase):
    def test_daemon_fixture_cooperates_on_windows_and_keeps_posix_sigterm(self):
        for platform, fail_stop in (("nt", False), ("nt", True), ("posix", False)):
            with self.subTest(platform=platform, fail_stop=fail_stop):
                fixture = BoardTestCase("runTest")
                fixture.setUp()
                process = Mock()
                process.poll.return_value = None
                events = []
                failure = AssertionError("cooperative shutdown unconfirmed")

                def cooperate(directory):
                    self.assertEqual(directory, fixture.directory)
                    events.append("stop")
                    if fail_stop:
                        raise failure

                process.terminate.side_effect = lambda: events.append("terminate")
                process.wait.side_effect = lambda **kwargs: events.append("wait")
                try:
                    support._confirmed_private_stops.add(fixture.directory.resolve())
                    with (patch.object(support, "os", wraps=os) as native,
                          patch.object(support.subprocess, "Popen", return_value=process),
                          patch("hey_my_buddy.protocol.rpc_config.configure_client"),
                          patch("hey_my_buddy.protocol.transport._read_endpoint", return_value={"token": "fixture"}),
                          patch("hey_my_buddy.protocol.transport._request", return_value={}),
                          patch.object(support, "stop_private_service", side_effect=cooperate)):
                        native.name = platform
                        if fail_stop:
                            with self.assertRaises(AssertionError) as raised:
                                with fixture.daemon():
                                    events.append("body")
                            self.assertIs(raised.exception, failure)
                            self.assertEqual(fixture._cleanup_failures, [failure])
                        else:
                            with fixture.daemon():
                                self.assertNotIn(fixture.directory.resolve(), support._confirmed_private_stops)
                                events.append("body")
                    expected = (["body", "stop", "terminate", "wait"] if fail_stop else
                                ["body", "stop", "wait"] if platform == "nt" else
                                ["body", "terminate", "wait"])
                    self.assertEqual(events, expected)
                    process.kill.assert_not_called()
                finally:
                    process.poll.return_value = 0
                    fixture._cleanup_failures.clear()
                    fixture.doCleanups()

    def test_private_tree_cleanup_keeps_posix_observation_over_the_whole_base(self):
        from hey_my_buddy.cli import checks

        with private_state_dir() as base:
            state = base / "state"
            for residue in ("process", "lock", "unknown"):
                with (self.subTest(residue=residue),
                      patch.object(support, "os", wraps=os) as native,
                      patch.object(support, "stop_private_service") as stop,
                      patch.object(support, "shutdown_private_rpc"),
                      patch.object(checks, "request_cooperative_stop", return_value=[]) as drain,
                      patch.object(checks, "held_locks", return_value={"daemon": [base / "elsewhere/board-owner.lock"]
                                                                    if residue == "lock" else []}),
                      patch.object(checks, "observed_processes", return_value=(
                          [{"pid": 123}] if residue == "process" else [],
                          ["observation incomplete"] if residue == "unknown" else [], [])) as observe,
                      patch.object(support, "remove_private_test_root") as remove):
                    native.name = "posix"
                    with self.assertRaisesRegex(AssertionError, "preserved"):
                        support.cleanup_private_tree(base, (state,))
                    stop.assert_called_once_with(state)
                    drain.assert_called_once_with(base)
                    observe.assert_called_once_with(base)
                    remove.assert_not_called()

    def test_private_tree_cleanup_uses_windows_service_evidence_without_unregistered_jobs(self):
        from hey_my_buddy.cli import checks

        with private_state_dir() as base:
            state = base / "state"
            with (patch.object(support, "os", wraps=os) as native,
                  patch.object(support, "stop_private_service") as stop,
                  patch.object(support, "shutdown_private_rpc") as shutdown,
                  patch.object(checks, "held_locks", return_value={"daemon": []}) as locks,
                  patch.object(checks, "observed_processes", side_effect=AssertionError("unregistered Job")),
                  patch.object(support, "remove_private_test_root") as remove):
                native.name = "nt"
                support.cleanup_private_tree(base, (state,))
            stop.assert_called_once_with(state)
            shutdown.assert_called_once_with()
            locks.assert_called_once_with(base)
            remove.assert_called_once_with(base)

    def test_windows_service_check_uses_lifecycle_locks(self):
        with private_state_dir() as root:
            with (patch.object(support, "os", wraps=os) as windows,
                  patch.object(support, "_lock_held", return_value=False),
                  patch("hey_my_buddy.cli.checks.observed_processes",
                        side_effect=AssertionError("Windows cannot prove shutdown by scanning open files"))):
                windows.name = "nt"
                support.assert_private_processes_gone(root)
                with patch.object(support, "_lock_held", side_effect=lambda path: path.name == "board-owner.lock"):
                    with self.assertRaisesRegex(AssertionError, "shutdown unconfirmed"):
                        support.assert_private_processes_gone(root)

    def test_windows_released_locks_do_not_replace_an_unconfirmed_drain(self):
        from hey_my_buddy.protocol.transport import ServiceError

        with private_state_dir() as root:
            with (patch.object(support, "os", wraps=os) as windows,
                  patch.object(support, "_lock_held", side_effect=[False, True, False, False, False]),
                  patch("hey_my_buddy.protocol.transport._read_endpoint", return_value={"token": "fixture"}),
                  patch("hey_my_buddy.protocol.transport._request", side_effect=ServiceError("UNAVAILABLE", "fixture lost reply"))):
                windows.name = "nt"
                with self.assertRaisesRegex(AssertionError, "stop reply unconfirmed"):
                    stop_private_service(root)

    def test_test_stop_confirmation_is_reused_while_windows_locks_are_releasing(self):
        with private_state_dir() as root:
            reply = {"stopped": True, "unresolvedAttempts": []}
            call = Mock(return_value=reply)
            self.assertIs(request_private_service_stop(root, call, drainSeconds=0), reply)
            call.assert_called_once_with("service_control", {"action": "stop", "drainSeconds": 0})
            with (patch.object(support, "os", wraps=os) as windows,
                  patch.object(support, "_lock_held", side_effect=[False, True, False, False, False]),
                  patch("hey_my_buddy.protocol.transport._request", side_effect=AssertionError("second stop request")),
                  patch.object(support.time, "sleep") as sleep):
                windows.name = "nt"
                stop_private_service(root)
            sleep.assert_called_once_with(.05)
            self.assertNotIn(root.resolve(), support._confirmed_private_stops)

    def test_unconfirmed_test_stop_cannot_be_reused_as_a_confirmation(self):
        with private_state_dir() as root:
            for reply in ({"stopped": False}, {"stopped": True, "unresolvedAttempts": ["attempt"]}):
                with self.subTest(reply=reply):
                    request_private_service_stop(root, Mock(return_value={"stopped": True}))
                    with self.assertRaisesRegex(AssertionError, "shutdown unconfirmed"):
                        request_private_service_stop(root, Mock(return_value=reply))
                    self.assertNotIn(root.resolve(), support._confirmed_private_stops)

    def test_confirmed_stop_still_requires_windows_locks_to_release(self):
        with private_state_dir() as root:
            request_private_service_stop(root, Mock(return_value={"stopped": True}))
            with (patch.object(support, "os", wraps=os) as windows,
                  patch.object(support, "_lock_held", side_effect=lambda path: path.name == "board-owner.lock"),
                  patch("hey_my_buddy.protocol.transport._request", side_effect=AssertionError("second stop request"))):
                windows.name = "nt"
                with self.assertRaisesRegex(AssertionError, "still owns state"):
                    stop_private_service(root, timeout=0)

    def test_unconfirmed_shutdown_preserves_private_state(self):
        directory = None
        try:
            with self.assertRaisesRegex(AssertionError, "shutdown unconfirmed"):
                with patch("support.stop_private_service", side_effect=AssertionError("shutdown unconfirmed")):
                    with private_state_dir() as directory:
                        (directory / "receipt.json").write_text("evidence", encoding="utf-8", newline="\n")
            self.assertEqual((directory / "receipt.json").read_text(encoding="utf-8"), "evidence")
        finally:
            if directory is not None:
                remove_private_test_root(directory)

    def test_child_environment_drops_inherited_ownership_and_credentials(self):
        inherited = {
            "HMB_STATE_DIR": "/wrong-state",
            "HMB_RUNTIME_ROOT": "/wrong-runtime",
            "HMB_RUNTIME": "/pinned-runtime",
            "HMB_RUNTIME_IDENTITY": "pinned",
            "HMB_AGENT_CREDENTIAL_FILE": "/secret-file",
            "VIRTUAL_ENV": "/wrong-venv",
            "UV_PROJECT_ENVIRONMENT": "/wrong-uv",
            "HMB_MODEL_CATALOG_FILE": "/inherited-catalog",
            "HMB_UNKNOWN_PIN": "inherited-pin",
            "ANTHROPIC_BASE_URL": "https://gateway.invalid",
            "ZCODE_UNLISTED_TEST_PIN": "inherited-config",
            "ZAI_API_KEY": "synthetic-secret",
            "ZHIPU_API_KEY": "synthetic-secret",
            "GLM_API_KEY": "synthetic-secret",
            "ANTHROPIC_API_KEY": "synthetic-secret",
            "ANTHROPIC_MODEL": "inherited-model",
            "C2_IPC_ROOT": "/inherited-ipc",
            "C2_UNKNOWN_PIN": "inherited-pin",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "CLAUDE_CODE_USE_VERTEX": "1",
            "CLAUDE_CODE_USE_FOUNDRY": "1",
            "CLAUDE_CODE_UNLISTED_PROVIDER": "inherited-provider",
            "AZURE_API_INGESTION_URL": "https://gateway.invalid",
        }
        with private_state_dir() as directory, patch.dict(os.environ, inherited):
            environment = _child_environment(directory)
            self.assertEqual(environment["HMB_STATE_DIR"], str(directory))
            self.assertEqual(environment["HMB_RUNTIME_ROOT"], str(directory / "runtime-root"))
            for key in inherited.keys() - {"HMB_STATE_DIR", "HMB_RUNTIME_ROOT"}:
                if key.startswith(("HMB_", "ANTHROPIC_", "CLAUDE_CODE_", "C2_", "ZCODE_", "ZAI_", "ZHIPU_", "GLM_")) or key in (
                        "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
                    self.assertNotIn(key, environment)
            self.assertFalse(any(key.startswith("CLAUDE_CODE_") for key in environment))

    def test_child_environment_preserves_explicit_private_paths(self):
        with private_state_dir() as directory:
            runtime_root = directory / "alternate-runtime"
            python_path = os.pathsep.join((str(PYTHON_ROOT), str(directory / "helpers")))
            environment = _child_environment(directory, {
                "HMB_STATE_DIR": "/wrong-state",
                "HMB_RUNTIME_ROOT": str(runtime_root),
                "PYTHONPATH": python_path,
            })
            self.assertEqual(environment["HMB_STATE_DIR"], str(directory))
            self.assertEqual(environment["HMB_RUNTIME_ROOT"], str(runtime_root))
            self.assertEqual(environment["PYTHONPATH"], python_path)
            with self.assertRaisesRegex(ValueError, "private state directory"):
                _child_environment(directory, {"HMB_RUNTIME_ROOT": "/wrong-runtime"})

    def test_r06_child_uses_private_state_and_explicit_catalog(self):
        """Probe a real interpreter and SQLite file, with default lookup refused."""
        with private_state_dir() as directory:
            catalog = write_catalog_fixture(directory)
            inherited = {
                "HMB_STATE_DIR": str(directory / "inherited-state"),
                "HMB_RUNTIME_ROOT": str(directory / "inherited-runtime"),
                "HMB_MODEL_CATALOG_FILE": str(directory / "inherited-catalog"),
                "HMB_RUNTIME": "inherited-runtime",
                "HMB_RUNTIME_IDENTITY": "inherited-identity",
                "HMB_AGENT_CREDENTIAL_FILE": str(directory / "inherited-credential"),
                "HMB_UNKNOWN_PIN": "inherited-pin",
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
                environment = _child_environment(directory, {"HMB_MODEL_CATALOG_FILE": str(catalog)})
            completed = subprocess.run([sys.executable, "-c", textwrap.dedent("""
                import json, os
                from contextlib import closing
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
                def reject_native(harness, **kwargs):
                    raise AssertionError('R-06: native discovery forbidden: ' + harness)
                discovery.candidate_snapshot = reject_native
                with patch.object(home, 'default_state_dir', side_effect=AssertionError('default state lookup')):
                    state = get_state_dir()
                    store = BoardStore(state)
                    with closing(store.db):
                        store.initialize()
                        ipc = configure_local_endpoint(state)
                        payload = discover(directory=state, harnesses=[])
                keys = {'VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT', 'HOME', 'USERPROFILE',
                        'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'ZCODE_DATA_BASE_DIR',
                        'DSH_HOME', 'ZCODE_BUILTIN_PROVIDER_CONFIG_FILE', 'ZCODE_PERSONAL_PROVIDER_CONFIG_FILE',
                        'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME',
                        'XDG_RUNTIME_DIR', 'APPDATA', 'LOCALAPPDATA'}
                selected = {key: value for key, value in os.environ.items()
                            if key in keys or key.startswith(('HMB_', 'ANTHROPIC_', 'C2_'))}
                result = {'environment': selected, 'state': str(state),
                          'database': str(store.db.path), 'ipc': str(ipc) if ipc is not None else None,
                          'runtime': str(runtime_root()), 'catalog': payload['source']}
                assert cc.shutdown()['completed']
                print(json.dumps(result))
            """)], env=environment, capture_output=True, text=True, timeout=30, encoding="utf-8")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            probe = json.loads(completed.stdout)
            self.assertEqual(probe["state"], str(directory))
            self.assertEqual(probe["runtime"], str(directory / "runtime-root"))
            self.assertEqual(probe["ipc"], None if os.name == "nt" else str(directory / "ipc"))
            self.assertEqual(probe["catalog"], f"file:{catalog}")
            self.assertEqual(probe["environment"]["HMB_MODEL_CATALOG_FILE"], str(catalog))
            for key in inherited.keys() - {"HMB_STATE_DIR", "HMB_RUNTIME_ROOT", "HMB_MODEL_CATALOG_FILE",
                                           "HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"}:
                if key == "DSH_HOME":
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
                        "DSH_HOME",
                        "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
                        "XDG_RUNTIME_DIR", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
                self.assertTrue(Path(probe["environment"][key]).is_relative_to(directory), key)
            self.assertEqual(list((directory / "home").iterdir()), [directory / "home/.gitconfig"])
            for path in ("inherited-state", "inherited-runtime", "inherited-ipc", "home/.local/share/hey-my-buddy/state"):
                self.assertFalse((directory / path).exists(), path)

    def test_r06_catalog_owner_passes_fixture_after_inherited_pins_are_removed(self):
        fixture = BoardTestCase("runTest")
        fixture.setUp()
        try:
            path = fixture.catalog_fixture()
            with patch.dict(os.environ, {"HMB_MODEL_CATALOG_FILE": "/wrong-catalog"}):
                self.assertNotIn("HMB_MODEL_CATALOG_FILE", _child_environment(fixture.directory))
                owned = fixture.child_environment()
                self.assertIn("HMB_MODEL_CATALOG_FILE", owned, "owner lost its explicit catalog")
                self.assertEqual(owned["HMB_MODEL_CATALOG_FILE"], str(path))
                # Both public helpers must use the owner's explicit environment.
                with patch("support.subprocess.run") as run:
                    run.return_value = subprocess.CompletedProcess([], 0, "{}", "")
                    fixture.cli("paths")
                self.assertIn("HMB_MODEL_CATALOG_FILE", run.call_args.kwargs["env"])
                self.assertEqual(run.call_args.kwargs["env"]["HMB_MODEL_CATALOG_FILE"], str(path))
                with patch("support.subprocess.Popen") as launch, patch(
                    "hey_my_buddy.protocol.transport._read_endpoint", return_value={"address": "fixture"},
                ), patch("hey_my_buddy.protocol.transport._request", return_value={}):
                    launch.return_value.poll.return_value = 0
                    with fixture.daemon():
                        pass
                self.assertIn("HMB_MODEL_CATALOG_FILE", launch.call_args.kwargs["env"])
                self.assertEqual(launch.call_args.kwargs["env"]["HMB_MODEL_CATALOG_FILE"], str(path))
                self.assertTrue(launch.call_args.args[0][-1].endswith("daemon_with_catalog.py"))
            with self.assertRaisesRegex(ValueError, "private state directory"):
                fixture.child_environment({"HMB_MODEL_CATALOG_FILE": "/wrong-catalog"})
            with self.assertRaisesRegex(ValueError, "private state directory"):
                fixture.child_environment({"HOME": "/wrong-home"})
        finally:
            fixture.doCleanups()

    def test_r05_catalog_daemon_fixture_binds_capabilities_without_native_discovery(self):
        """Exercise the existing daemon fixture without binding a socket."""
        from hey_my_buddy.blackboard.service.daemon import Daemon
        from hey_my_buddy.blackboard.service.service import call_operation

        with private_state_dir() as directory:
            catalog = write_catalog_fixture(directory)
            environment = _child_environment(directory, {"HMB_MODEL_CATALOG_FILE": str(catalog)})
            fixture = runpy.run_path(str(PYTHON_ROOT.parent / "tests/python/blackboard/service/fixtures/daemon_with_catalog.py"))
            with patch.dict(os.environ, environment, clear=True), patch(
                "hey_my_buddy.buddy.harnesses.discovery.candidate_snapshot",
                side_effect=AssertionError("R-05: native discovery forbidden"),
            ) as native:
                daemon = Daemon(directory)
                try:
                    daemon.store.initialize()
                    service = fixture["service"](daemon)
                    capabilities = json.loads(service.capabilities(json.dumps({"token": daemon.token})))
                    self.assertFalse(native.called, "fixture capabilities attempted native discovery")
                    self.assertNotIn("error", capabilities)
                    self.assertTrue(capabilities["adapters"]["dsh"]["available"])
                    refreshed = call_operation(service, "model_catalog_refresh", {"requestId": "r05-fixture"})
                    self.assertEqual(refreshed["catalog"]["source"], f"file:{catalog}")
                    payload = json.loads(catalog.read_text(encoding="utf-8"))
                    self.assertEqual(refreshed["catalog"]["providers"][0]["models"][0]["id"],
                                     payload["providers"][0]["models"][0]["id"])
                    payload["providers"][0]["models"][0]["id"] = "r05-fixture-changed-model"
                    catalog.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
                    reread = call_operation(service, "model_catalog_refresh", {"requestId": "r05-fixture-reread"})
                    self.assertEqual(reread["catalog"]["providers"][0]["models"][0]["id"],
                                     payload["providers"][0]["models"][0]["id"])
                    self.assertFalse(native.called, "fixture catalog attempted native discovery")
                finally:
                    daemon.store.db.close()
            self.assertEqual(list((directory / "home").iterdir()), [directory / "home/.gitconfig"])

    def test_cleanup_stops_cli_started_replacement_after_restart(self):
        fixture = BoardTestCase("runTest")
        fixture.setUp()
        directory = fixture.directory
        handles = []
        try:
            with patch.dict(os.environ):
                fixture.catalog_fixture()
            with fixture.daemon(env={"HMB_MAX_CONCURRENT": "1"}) as original:
                original_id = _read_endpoint(directory)["serviceId"]
                code, restarted = fixture.cli("restart", json.dumps({"drainSeconds": 0}))
                self.assertEqual(code, 0, restarted)
                self.assertTrue(restarted["restarting"])
                original.wait(timeout=20)

            # The CLI detaches this new daemon, so it is absent from children.
            code, health = fixture.cli("health", env={"HMB_MAX_CONCURRENT": "1"})
            self.assertEqual(code, 0, health)
            self.assertNotEqual(_read_endpoint(directory)["serviceId"], original_id)
            self.assertTrue(wait_for(lambda: _lock_held(directory / "control-daemon.lock")))
            for path in ("control-daemon.lock", "board-owner.lock"):
                handles.append(os.open(directory / path, os.O_RDWR))

            # Leave the directory available for a protocol cleanup if the assertion
            # fails; the lock FDs also let us inspect ownership after deletion.
            with patch("support.remove_private_test_root") as remove:
                fixture._cleanup()
                assert_released(self, handles)
                for fd in handles:
                    os.close(fd)
                handles.clear()
                remove.assert_called_once_with(directory)
            remove_private_test_root(directory)
        finally:
            for fd in handles:
                os.close(fd)
            if directory.exists():
                stop_private_service(directory)
                remove_private_test_root(directory)

    def test_private_state_context_stops_its_cli_started_service(self):
        handles = []
        directory = None
        try:
            with patch("support.remove_private_test_root") as remove:
                with private_state_dir() as directory:
                    catalog = write_catalog_fixture(directory)
                    environment = _child_environment(directory, {
                        "HMB_MAX_CONCURRENT": "1", "HMB_MODEL_CATALOG_FILE": str(catalog),
                    })
                    completed = subprocess.run(
                        [sys.executable, "-m", "hey_my_buddy.cli.main", "health"], env=environment,
                        capture_output=True, text=True, timeout=60, encoding="utf-8",
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertTrue(wait_for(lambda: _lock_held(directory / "control-daemon.lock")))
                    for path in ("control-daemon.lock", "board-owner.lock"):
                        handles.append(os.open(directory / path, os.O_RDWR))
                assert_released(self, handles)
                for fd in handles:
                    os.close(fd)
                handles.clear()
                remove.assert_called_once_with(directory)
            remove_private_test_root(directory)
        finally:
            for fd in handles:
                os.close(fd)
            if directory is not None and directory.exists():
                stop_private_service(directory)
                remove_private_test_root(directory)


if __name__ == "__main__":
    unittest.main()
