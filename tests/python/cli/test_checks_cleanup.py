"""The checks runner contains every suite inside one private root it can retire."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hey_my_buddy.cli import checks


def hold_lock(path: Path) -> int:
    """An open fd that owns ``path``'s flock until the caller closes it."""
    fd = os.open(path, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def release_lock(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


class ChecksEnvironmentTests(unittest.TestCase):
    def test_dsh_configuration_and_deepseek_keys_are_removed_by_prefix(self):
        from support import _child_environment

        inherited = {"DSH_HOME": "/users/fixture/dsh.v2 用户 设置",
                     "DSH_PERMISSION_MODE": "danger-full-access", "DSH_UNLISTED_FLAG": "inherited",
                     "DEEPSEEK_API_KEY": "fixture-secret", "DEEPSEEK_SEARCH_BASE_URL": "https://fixture.invalid",
                     "DEEPSEEK_UNLISTED_KEY": "inherited"}
        with patch.dict(os.environ, inherited):
            environment = checks.test_environment(Path("/repo"))
            with tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                fixture_environment = _child_environment(state)
                self.assertEqual(fixture_environment["DSH_HOME"], str(state / "home/.dsh"))
        self.assertFalse(any(key.startswith(("DSH_", "DEEPSEEK_")) for key in environment))
        self.assertEqual({key for key in fixture_environment if key.startswith(("DSH_", "DEEPSEEK_"))},
                         {"DSH_HOME"})

    def test_shell_provider_flags_are_removed_by_prefix_in_both_test_environments(self):
        from hey_my_buddy.buddy.harnesses.discovery import native_environment
        from support import _child_environment

        inherited = {"CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1",
                     "CLAUDE_CODE_UNLISTED_PROVIDER": "synthetic", "CLAUDE_CODE_EFFORT_LEVEL": "max"}
        with patch.dict(os.environ, inherited):
            environment = checks.test_environment(Path("/repo"))
            with tempfile.TemporaryDirectory() as directory:
                fixture_environment = _child_environment(Path(directory))
        for values in (environment, fixture_environment):
            self.assertFalse(any(key.startswith("CLAUDE_CODE_") for key in values))
            self.assertFalse(any(key.startswith("CLAUDE_CODE_") for key in native_environment(values)))

    def test_environment_is_private_and_unpinned(self):
        inherited = {
            "HMB_STATE_DIR": "/wrong-state",
            "HMB_RUNTIME_ROOT": "/wrong-runtime",
            "HMB_RUNTIME": "/pinned-runtime",
            "HMB_RUNTIME_IDENTITY": "pinned",
            "HMB_AGENT_CREDENTIAL_FILE": "/secret-file",
            "VIRTUAL_ENV": "/wrong-venv",
            "UV_PROJECT_ENVIRONMENT": "/wrong-uv",
            "HMB_CONSOLE_PORT": "8642",
            "HMB_UNLISTED_TEST_PIN": "inherited",
            "ANTHROPIC_UNLISTED_TEST_PIN": "inherited",
            "ZCODE_UNLISTED_TEST_PIN": "inherited",
            "ZAI_API_KEY": "synthetic-secret",
            "ZHIPU_API_KEY": "synthetic-secret",
            "GLM_API_KEY": "synthetic-secret",
            "C2_IPC_ROOT": "/public-native-domain",
            "C2_UNLISTED_TEST_PIN": "inherited",
        }
        with patch.dict(os.environ, inherited):
            environment = checks.test_environment(Path("/repo"))
        for key in checks.SANITIZED_VARIABLES:
            self.assertNotIn(key, environment)
        for key in ("HMB_UNLISTED_TEST_PIN", "ANTHROPIC_UNLISTED_TEST_PIN", "C2_IPC_ROOT", "C2_UNLISTED_TEST_PIN", "ZCODE_UNLISTED_TEST_PIN", "ZAI_API_KEY", "ZHIPU_API_KEY", "GLM_API_KEY"):
            self.assertNotIn(key, environment)
        self.assertEqual(environment["HMB_DEV_SOURCE"], "1")
        self.assertEqual(environment["HMB_CONSOLE_PORT"], "0")
        self.assertTrue(environment["HMB_CLAUDE_CLI"].endswith("claude-not-installed"))
        path_entries = environment["PYTHONPATH"].split(os.pathsep)
        self.assertEqual(path_entries[:2], [str(Path("/repo/src")), str(Path("/repo/tests/python"))])

    def test_private_root_and_child_environment_contain_every_temp_file(self):
        root = checks.create_private_root()
        try:
            self.assertTrue(root.name.startswith("hey-my-buddy-checks-"))
            self.assertEqual(stat.S_IMODE(os.stat(root).st_mode), 0o700)
            tmp = root / "tmp"
            self.assertTrue(tmp.is_dir())
            environment = checks.child_environment(Path("/repo"), root,
                                                   home_root=root / "home")
            self.assertEqual(environment["TMPDIR"], str(tmp))
            completed = subprocess.run(
                [sys.executable, "-c", "import tempfile; print(tempfile.mkdtemp(prefix='inside-'))"],
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(Path(completed.stdout.strip()).parent, tmp)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_daemon_stop_uses_explicit_state_and_ends_its_rpc_session(self):
        endpoint = {"address": "ipc://private-checks-mock", "token": "fixture"}
        state = Path("/private-test-state")
        with patch("hey_my_buddy.protocol.transport._read_endpoint", return_value=endpoint), patch("hey_my_buddy.protocol.transport._request") as request, patch("c_two.shutdown", return_value={"completed": True}) as shutdown:
            checks._request_daemon_stop(state)
        self.assertEqual(request.call_args.kwargs["state_dir"], state)
        shutdown.assert_called_once_with()



class ResidueObservationTests(unittest.TestCase):
    def test_lock_is_held_requires_an_owner(self):
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-checks-test-") as raw:
            path = Path(raw) / "lifetime.lock"
            path.write_text("")
            self.assertFalse(checks.lock_is_held(path))
            fd = hold_lock(path)
            try:
                self.assertTrue(checks.lock_is_held(path))
            finally:
                release_lock(fd)
            self.assertFalse(checks.lock_is_held(path))
            self.assertFalse(checks.lock_is_held(Path(raw) / "absent.lock"))

    def test_held_locks_report_only_buddy_lifetime_owners(self):
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-checks-test-") as raw:
            root = Path(raw)
            state = root / "hey-my-buddy-test-state"
            state.mkdir(parents=True)
            for name in ("control-daemon.lock", "board-owner.lock"):
                (state / name).write_text("")
            unrelated = state / "unrelated.lock"
            unrelated.write_text("")
            bystander = hold_lock(unrelated)
            fd = hold_lock(state / "board-owner.lock")
            try:
                residue = checks.held_locks(root)
                self.assertEqual(residue["daemon"], [state / "board-owner.lock"])
            finally:
                release_lock(fd)
                release_lock(bystander)


class PrivateRootTeardownTests(unittest.TestCase):
    def test_teardown_removes_a_clean_private_root(self):
        root = checks.create_private_root()
        state = root / "tmp" / "hey-my-buddy-test-clean"
        state.mkdir(parents=True)
        (state / "control-daemon.lock").write_text("")
        self.assertIsNone(checks.teardown_private_root(root, drain_seconds=0.5))
        self.assertFalse(root.exists())

    def test_teardown_preserves_evidence_when_a_daemon_survives(self):
        root = checks.create_private_root()
        state = root / "tmp" / "hey-my-buddy-test-daemon"
        state.mkdir(parents=True)
        (state / "control-daemon.lock").write_text("")
        fd = hold_lock(state / "control-daemon.lock")
        try:
            evidence = checks.teardown_private_root(root, drain_seconds=0.2)
            self.assertIsNotNone(evidence)
            self.assertTrue(root.exists())
            report = json.loads((root / checks.EVIDENCE_FILE_NAME).read_text())
            entry = report["held"]["daemon"][0]
            self.assertEqual(entry["path"], str(state / "control-daemon.lock"))
            self.assertEqual(entry["directory"], str(state))
            self.assertIn("control-daemon.lock", entry["directoryFiles"])
            self.assertIn("control-daemon.lock", report["stateDirectories"][str(state)]["files"])
        finally:
            release_lock(fd)
            shutil.rmtree(root, ignore_errors=True)

    def test_teardown_waits_out_a_live_process_with_unlinked_lock(self):
        # A racing cleanup unlinks the lock file while its holder still runs; a clean
        # teardown may only return after that holder observably exited.
        root = checks.create_private_root()
        execution = root / "tmp" / "hey-my-buddy-test-unlinked" / "executions" / "fixture"
        execution.mkdir(parents=True)
        lock = execution / "execution.lock"
        lock.write_text("")
        script = (
            "import fcntl, os, sys, time\n"
            "execution = sys.argv[1]\n"
            "fd = os.open(os.path.join(execution, 'execution.lock'), os.O_RDWR)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\n"
            "print('LOCKED', flush=True)\n"
            "time.sleep(0.4)\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(execution)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), "LOCKED")
            lock.unlink()
            self.assertFalse(lock.exists())
            self.assertIsNone(checks.teardown_private_root(root, drain_seconds=20))
            self.assertFalse(root.exists())
            process.wait(timeout=10)
            self.assertEqual(process.returncode, 0, "the live holder must have exited before clean")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            shutil.rmtree(root, ignore_errors=True)

    def test_teardown_reports_a_surviving_process_with_unlinked_lock(self):
        # No held lock is visible after the unlink, so only live-process observation
        # separates residue from a clean root; it must fail closed, not report clean.
        root = checks.create_private_root()
        execution = root / "tmp" / "hey-my-buddy-test-stuck" / "executions" / "fixture"
        execution.mkdir(parents=True)
        lock = execution / "execution.lock"
        lock.write_text("")
        script = (
            "import fcntl, os, sys, time\n"
            "fd = os.open(sys.argv[1], os.O_RDWR)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\n"
            "print('LOCKED', flush=True)\n"
            "time.sleep(300)\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(lock)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), "LOCKED")
            lock.unlink()
            evidence = checks.teardown_private_root(root, drain_seconds=1.0)
            self.assertIsNotNone(evidence)
            self.assertTrue(root.exists())
            report = json.loads((root / checks.EVIDENCE_FILE_NAME).read_text())
            self.assertIn(process.pid, [entry["pid"] for entry in report["processes"]])
            self.assertEqual(report["observation"]["problems"], [])
        finally:
            process.terminate()
            process.wait(timeout=10)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


class DeletedOpenFileObservationTests(unittest.TestCase):
    def test_lsof_name_channel_keeps_deleted_private_handles_only(self):
        root = Path('/private/checks-fixture')
        output = b'p123\nn/private/checks-fixture/state/control-daemon.lock (deleted)\np456\nn/private/other/state/board-owner.lock\n'
        completed = subprocess.CompletedProcess([], 0, stdout=output, stderr=b'')
        with patch('hey_my_buddy.cli.checks.shutil.which', return_value='/usr/sbin/lsof'), patch('hey_my_buddy.cli.checks.subprocess.run', return_value=completed):
            pids, error, available = checks._open_file_pids(root)
        self.assertEqual(pids, {123})
        self.assertIsNone(error)
        self.assertTrue(available)
