"""The checks runner contains every suite inside one private root it can retire."""
from __future__ import annotations

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
from unittest.mock import Mock, patch

from hey_my_buddy import locking, process_observation as observation
from hey_my_buddy.cli import checks
from hey_my_buddy.paths import native_path
from hey_my_buddy.blackboard.tasks import storage
from process_observation_fakes import native_references, proc_references, process, processes


def hold_lock(path: Path) -> int:
    """An open fd that owns ``path``'s lock until the caller closes it."""
    fd = os.open(native_path(path), os.O_RDWR)
    locking.lock(fd)
    return fd


def release_lock(fd: int) -> None:
    locking.unlock(fd)
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
            if os.name == "nt":
                checks.state_root.check_state_root(root)
            else:
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
                timeout=60, encoding="utf-8",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(Path(completed.stdout.strip()).parent, tmp)
        finally:
            shutil.rmtree(root)

    def test_windows_temp_and_tmp_override_inherited_paths(self):
        with tempfile.TemporaryDirectory(prefix="checks-temp-") as raw:
            root = Path(raw)
            with (patch.object(checks, "os", wraps=os) as windows,
                  patch.dict(os.environ, {"TEMP": str(root / "outside-temp"), "TMP": str(root / "outside-tmp")})):
                windows.name = "nt"
                environment = checks.child_environment(Path("/repo"), root / "child", home_root=root / "home")
            self.assertEqual(environment["TEMP"], str(root / "child/tmp"))
            self.assertEqual(environment["TMP"], str(root / "child/tmp"))
            self.assertEqual(environment["TMPDIR"], str(root / "child/tmp"))

    def test_daemon_stop_uses_explicit_state_and_ends_its_rpc_session(self):
        endpoint = {"address": "ipc://private-checks-mock", "token": "fixture"}
        state = Path("/private-test-state")
        with patch("hey_my_buddy.protocol.transport._read_endpoint", return_value=endpoint), patch("hey_my_buddy.protocol.transport._request", return_value={"stopped": True, "unresolvedAttempts": []}) as request, patch("c_two.shutdown", return_value={"completed": True}) as shutdown:
            checks._request_daemon_stop(state)
        self.assertEqual(request.call_args.kwargs["state_dir"], state)
        shutdown.assert_called_once_with()



class ResidueObservationTests(unittest.TestCase):
    def test_directory_with_a_lifetime_lock_name_is_not_silently_dropped(self):
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-checks-test-") as raw:
            root = Path(raw)
            (root / "board-owner.lock").mkdir()
            with self.assertRaises(OSError):
                checks.held_locks(root)

    def test_long_lock_scans_do_not_enter_outside_links(self):
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-checks-test-") as raw:
            container = Path(raw)
            root = container / "private"
            state = root
            while len(str(state)) < 310:
                state /= "directory-" + "a" * 35
            native_path(state).mkdir(parents=True)
            for name in ("board-owner.lock", "control-daemon.lock"):
                native_path(state / name).touch()
            outside = container / "outside"
            outside.mkdir()
            (outside / "control-daemon.lock").touch()
            link = root / "outside-link"
            if os.name == "nt":
                result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                        capture_output=True)
                self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
                self.assertTrue(link.is_junction())
            else:
                link.symlink_to(outside, target_is_directory=True)
            inside_fd = hold_lock(state / "board-owner.lock")
            outside_fd = hold_lock(outside / "control-daemon.lock")
            try:
                self.assertEqual(checks.held_locks(root), {"daemon": [state / "board-owner.lock"]})
                with (patch.object(checks, "os", wraps=os) as windows,
                      patch.object(checks, "_windows_owned_processes", return_value=([], [], [])),
                      patch.object(checks, "held_locks", return_value={"daemon": []}),
                      patch.object(checks, "_request_daemon_stop") as stop):
                    windows.name = "nt"
                    self.assertEqual(checks.request_cooperative_stop(root, timeout=0),
                                     [f"private service stop reply unconfirmed: {state}"])
                    stop.assert_not_called()
            finally:
                release_lock(inside_fd)
                release_lock(outside_fd)
                checks.remove_private_test_root(root)

    def test_lock_is_held_requires_an_owner(self):
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-checks-test-") as raw:
            path = Path(raw) / "lifetime.lock"
            path.write_text("", encoding="utf-8", newline="\n")
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
                (state / name).write_text("", encoding="utf-8", newline="\n")
            unrelated = state / "unrelated.lock"
            unrelated.write_text("", encoding="utf-8", newline="\n")
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
        if os.name != "nt":
            (state / "control-daemon.lock").write_text("", encoding="utf-8", newline="\n")
        self.assertIsNone(checks.teardown_private_root(root, drain_seconds=0.5))
        self.assertFalse(root.exists())

    def test_teardown_preserves_evidence_when_a_daemon_survives(self):
        root = checks.create_private_root()
        state = root / "tmp" / "hey-my-buddy-test-daemon"
        state.mkdir(parents=True)
        (state / "control-daemon.lock").write_text("", encoding="utf-8", newline="\n")
        fd = hold_lock(state / "control-daemon.lock")
        try:
            evidence = checks.teardown_private_root(root, drain_seconds=0.2)
            self.assertIsNotNone(evidence)
            self.assertTrue(root.exists())
            report = json.loads((root / checks.EVIDENCE_FILE_NAME).read_text(encoding="utf-8"))
            entry = report["held"]["daemon"][0]
            self.assertEqual(entry["path"], str(state / "control-daemon.lock"))
            self.assertEqual(entry["directory"], str(state))
            self.assertIn("control-daemon.lock", entry["directoryFiles"])
            self.assertIn("control-daemon.lock", report["stateDirectories"][str(state)]["files"])
        finally:
            release_lock(fd)
            shutil.rmtree(root)

    @unittest.skipIf(os.name == "nt", "POSIX observation of an unlinked lock")
    def test_teardown_waits_out_a_live_process_with_unlinked_lock(self):
        # A racing cleanup unlinks the lock file while its holder still runs; a clean
        # teardown may only return after that holder observably exited.
        root = checks.create_private_root()
        execution = root / "tmp" / "hey-my-buddy-test-unlinked" / "executions" / "fixture"
        execution.mkdir(parents=True)
        lock = execution / "execution.lock"
        lock.write_text("", encoding="utf-8", newline="\n")
        script = (
            "import os, sys, time\n"
            "from hey_my_buddy import locking, process_observation as observation\n"
            "execution = sys.argv[1]\n"
            "fd = os.open(os.path.join(execution, 'execution.lock'), os.O_RDWR)\n"
            "locking.lock(fd)\n"
            "print('LOCKED', flush=True)\n"
            "time.sleep(0.4)\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(execution)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
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
            if root.exists():
                shutil.rmtree(root)

    @unittest.skipIf(os.name == "nt", "POSIX observation of an unlinked lock")
    def test_teardown_reports_a_surviving_process_with_unlinked_lock(self):
        # No held lock is visible after the unlink, so only live-process observation
        # separates residue from a clean root; it must fail closed, not report clean.
        root = checks.create_private_root()
        execution = root / "tmp" / "hey-my-buddy-test-stuck" / "executions" / "fixture"
        execution.mkdir(parents=True)
        lock = execution / "execution.lock"
        lock.write_text("", encoding="utf-8", newline="\n")
        script = (
            "import os, sys, time\n"
            "from hey_my_buddy import locking, process_observation as observation\n"
            "fd = os.open(sys.argv[1], os.O_RDWR)\n"
            "locking.lock(fd)\n"
            "print('LOCKED', flush=True)\n"
            "time.sleep(300)\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(lock)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), "LOCKED")
            lock.unlink()
            evidence = checks.teardown_private_root(root, drain_seconds=1.0)
            self.assertIsNotNone(evidence)
            self.assertTrue(root.exists())
            report = json.loads((root / checks.EVIDENCE_FILE_NAME).read_text(encoding="utf-8"))
            self.assertIn(process.pid, [entry["pid"] for entry in report["processes"]])
            self.assertEqual(report["observation"]["problems"], [])
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            if root.exists():
                shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()


class WindowsOwnedCleanupTests(unittest.TestCase):
    def setUp(self):
        self.container = self.enterContext(tempfile.TemporaryDirectory(prefix="checks-owned-"))
        self.root = Path(self.container).resolve()
        self.windows = self.enterContext(patch.object(checks, "os", wraps=os))
        self.windows.name = "nt"
        self.enterContext(patch.dict(checks._windows_suite_children, clear=True))
        self.enterContext(patch.dict(checks._windows_spawn_problems, clear=True))
        # The owned path must not silently fall back to a whole-machine scan.
        self.enterContext(patch.object(checks, "_observation_snapshot", side_effect=AssertionError("unowned scan")))

    def child(self, *, active=0, finished=True):
        child = Mock()
        child.identity.return_value = {"pid": 123, "startedAt": 134000000000000001,
                                       "jobName": "Local\\HeyMyBuddy-test"}
        child.finished = finished
        child.job.handle = object()
        child.job.active.return_value = active
        child.job.api.process_identities.return_value = [{"pid": 456, "startedAt": 134000000000000003}]
        checks._windows_suite_children[self.root] = [child]
        return child

    def test_exited_root_does_not_prove_its_descendants_stopped(self):
        child = self.child(active=2)
        processes, problems, _ = checks.observed_processes(self.root)
        self.assertEqual(problems, [])
        self.assertEqual(processes[0]["startedAt"], 134000000000000001)
        self.assertEqual(processes[0]["activeProcesses"], 2)
        self.assertFalse(processes[0]["rootRunning"])
        self.assertEqual(processes[0]["members"], [{"pid": 456, "startedAt": 134000000000000003}])
        child.job.api.process_identities.assert_called_once_with(child.job.handle)
        child.job.active.return_value = 0
        self.assertEqual(checks.observed_processes(self.root), ([], [], []))

    def test_unreadable_member_identity_keeps_the_job_evidence_and_root(self):
        child = self.child(active=2)
        child.job.api.process_identities.side_effect = OSError("member creation time denied")
        evidence = checks.teardown_private_root(self.root, drain_seconds=0)
        self.assertTrue(self.root.exists())
        self.assertEqual(evidence["processes"][0]["activeProcesses"], 2)
        self.assertEqual(evidence["processes"][0]["members"], [])
        self.assertIn("member creation time denied", evidence["observation"]["problems"][0])
        child.terminate.assert_not_called()
        child.job.terminate.assert_not_called()

    def test_empty_job_does_not_replace_the_creating_process_handle(self):
        self.child(active=0, finished=False)
        processes, problems, _ = checks.observed_processes(self.root)
        self.assertTrue(processes[0]["rootRunning"])
        self.assertEqual(problems, [])
        self.assertEqual(processes[0]["members"], [])
        checks._windows_suite_children[self.root][0].job.api.process_identities.assert_not_called()

    def test_closed_or_unqueryable_job_is_unknown_even_if_its_name_is_gone(self):
        for closed in (False, True):
            with self.subTest(closed=closed):
                child = self.child()
                if closed:
                    child.job.handle = None
                else:
                    child.job.active.side_effect = OSError("query denied")
                processes, problems, _ = checks.observed_processes(self.root)
                self.assertEqual(processes, [])
                self.assertTrue(problems)
                if closed:
                    child.job.active.assert_not_called()

    def test_unknown_ownership_preserves_the_root_and_evidence(self):
        evidence = checks.teardown_private_root(self.root, drain_seconds=0)
        self.assertIsNotNone(evidence)
        self.assertIn("handles unavailable", evidence["observation"]["problems"][0])
        self.assertTrue((self.root / checks.EVIDENCE_FILE_NAME).is_file())
        self.windows.kill.assert_not_called()

    def test_stop_reply_must_confirm_draining_all_attempts(self):
        for reply in ({"stopped": False}, {"stopped": True, "unresolvedAttempts": ["attempt"]}):
            with (self.subTest(reply=reply),
                  patch("hey_my_buddy.protocol.transport._read_endpoint", return_value={"token": "fixture"}),
                  patch("hey_my_buddy.protocol.transport._request", return_value=reply),
                  patch("c_two.shutdown", return_value={"completed": True})):
                self.assertFalse(checks._request_daemon_stop(self.root))

    def test_released_locks_do_not_replace_a_lost_stop_reply(self):
        checks._windows_suite_children[self.root] = []
        path = self.root / "control-daemon.lock"
        with (patch.object(checks, "held_locks", side_effect=[{"daemon": [path]}, {"daemon": []}]),
              patch.object(checks, "_request_daemon_stop", return_value=False)):
            problems = checks.request_cooperative_stop(self.root, timeout=1)
        self.assertIn("stop reply unconfirmed", problems[0])

    def test_unconfirmed_stop_requests_each_state_only_once_and_continues_draining(self):
        checks._windows_suite_children[self.root] = []
        first = self.root / "first/control-daemon.lock"
        second = self.root / "second/board-owner.lock"
        with (patch.object(checks, "held_locks", side_effect=[{"daemon": [first, second]},
                                                            {"daemon": [first, second]}, {"daemon": []}]),
              patch.object(checks, "_request_daemon_stop", side_effect=[False, True]) as request):
            problems = checks.request_cooperative_stop(self.root, timeout=2)
        self.assertEqual([args.args[0] for args in request.call_args_list], [first.parent, second.parent])
        self.assertEqual(problems, [f"private service stop reply unconfirmed: {first.parent}"])

    def test_teardown_reports_one_observation_problem_and_releases_handles_once(self):
        child = self.child()
        child.job.active.side_effect = OSError("query denied")
        with patch.object(checks, "_release_suite_handles", wraps=checks._release_suite_handles) as release:
            evidence = checks._safe_teardown(self.root)
        self.assertEqual(evidence["observation"]["problems"], ["owned suite observation failed: query denied"])
        release.assert_called_once_with(self.root)
        child.job.close.assert_called_once_with()

    def test_parent_keeps_service_state_a_suite_left_without_a_stop_reply(self):
        checks._windows_suite_children[self.root] = []
        (self.root / "control-daemon.lock").touch()
        with patch.object(checks, "lock_is_held", return_value=False):
            evidence = checks.teardown_private_root(self.root, drain_seconds=0)
        self.assertTrue(self.root.exists())
        self.assertEqual(evidence["held"], {})
        self.assertIn("stop reply unconfirmed", evidence["observation"]["problems"][0])


@unittest.skipUnless(os.name == "nt", "Windows owned Job descendants")
class NativeWindowsOwnedCleanupTests(unittest.TestCase):
    def test_suite_job_allows_breakaway_without_kill_on_close(self):
        api = checks._SuiteWindowsAPI()
        import uuid

        job = api.create_job(f"Local\\HeyMyBuddy-{uuid.uuid4()}")
        try:
            limits = api._call(api.job.QueryInformationJobObject, job, api.job.JobObjectExtendedLimitInformation)
            flags = limits["BasicLimitInformation"]["LimitFlags"]
            self.assertTrue(flags & api.job.JOB_OBJECT_LIMIT_BREAKAWAY_OK)
            self.assertFalse(flags & api.job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        finally:
            api.close(job)

    def test_venv_descendant_survives_root_exit_until_held_job_becomes_empty(self):
        import threading

        root = checks.create_private_root()
        release = root / "release"
        recorded = root / "descendant.json"
        descendant = ("import json,os,sys,time; from pathlib import Path; "
                      "from hey_my_buddy.protocol.process_start import process_start; "
                      "release=Path(sys.argv[1]); "
                      f"Path({str(recorded)!r}).write_text(json.dumps({{'pid':os.getpid(),"
                      "'startedAt':process_start(os.getpid(),include_exited=True)}),encoding='utf-8'); "
                      "exec('while not release.exists(): time.sleep(0.02)')")
        parent = ("import subprocess,sys; "
                  f"subprocess.Popen([sys.executable,'-c',{descendant!r},{str(release)!r}])")
        outcome = checks.run_suite_child(
            Path(__file__).resolve().parents[3], root, "p000", "descendant",
            [[sys.executable, "-c", parent]], set(), threading.Lock(), threading.Event(),
        )
        try:
            self.assertEqual(outcome.returncode, 0, outcome.stderr)
            self.assertTrue(wait_until(lambda: recorded.exists()))
            expected = json.loads(recorded.read_text(encoding="utf-8"))
            processes, problems, _ = checks.observed_processes(root)
            self.assertEqual(problems, [])
            self.assertTrue(processes[0]["activeProcesses"])
            self.assertFalse(processes[0]["rootRunning"])
            self.assertIn(expected, processes[0]["members"])
            self.assertTrue(root.exists())
        finally:
            release.touch()
            self.assertIsNone(checks.teardown_private_root(root, drain_seconds=15))
        self.assertFalse(root.exists())


class ProcessObservationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(
            prefix='process-observation-'))).resolve()
        self.enterContext(patch.object(observation.psutil, 'WINDOWS', False))
        self.enterContext(patch.object(observation.psutil, 'LINUX', False))
        self.enterContext(patch.object(observation.psutil, 'MACOS', True))
        self.enterContext(patch.object(observation.os, 'geteuid', return_value=0, create=True))

    def test_different_effective_uid_excludes_denied_argv_and_cwd(self):
        included = process(123, ['python'], uid=1, effective_uid=0)
        excluded = process(456, ['login'], uid=0, effective_uid=1)
        excluded.cmdline.side_effect = observation.psutil.AccessDenied(456)
        excluded.cwd.side_effect = observation.psutil.AccessDenied(456)
        excluded.status.side_effect = observation.psutil.AccessDenied(456)
        with processes(included, excluded), native_references() as native:
            self.assertEqual(checks._ps_argv_table(), [(123, ['python'])])
            self.assertTrue(storage.process_inventory(Path('/private/checks-fixture'))[2])
            _, error, available = checks._open_file_pids(Path('/private/checks-fixture'))
        self.assertIsNone(error)
        self.assertTrue(available)
        excluded.cmdline.assert_not_called()
        excluded.cwd.assert_not_called()
        excluded.status.assert_not_called()
        command = native.call_args.args[0]
        self.assertEqual(command[command.index('-p') + 1], '123')
        self.assertNotIn('-u', command)  # The shared effective-UID selection is final.

    def test_same_effective_uid_denied_argv_and_cwd_are_unknown(self):
        included = process(123, uid=1, effective_uid=0)
        included.cmdline.side_effect = observation.psutil.AccessDenied(123)
        included.cwd.side_effect = observation.psutil.AccessDenied(123)
        with processes(included), native_references():
            self.assertIsNone(checks._ps_argv_table())
            self.assertFalse(storage.process_inventory(Path('/private/checks-fixture'))[2])
            _, error, available = checks._open_file_pids(Path('/private/checks-fixture'))
        self.assertIsNotNone(error)
        self.assertTrue(available)

    def test_failed_argv_reads_use_current_starting_or_ending_state(self):
        for failure in (observation.psutil.AccessDenied(123), SystemError('proc_cmdline failed')):
            for state in ('idle', 'zombie', 'gone', 'running', 'denied'):
                with self.subTest(failure=type(failure).__name__, state=state):
                    target = process(123, uid=1, effective_uid=0)
                    target.cmdline.side_effect = failure
                    if state == 'gone':
                        target.status.side_effect = observation.psutil.NoSuchProcess(123)
                    elif state == 'denied':
                        target.status.side_effect = observation.psutil.AccessDenied(123)
                    else:
                        target.status.return_value = state
                    with processes(target):
                        table = checks._ps_argv_table()
                    self.assertEqual(table, [] if state in ('idle', 'zombie', 'gone') else None)
                    target.status.assert_called_once_with()

    def test_successful_reads_keep_positive_results_without_reading_status(self):
        root = Path('/private/checks-fixture')
        for state in (observation.psutil.STATUS_IDLE, observation.psutil.AccessDenied(123)):
            with self.subTest(state=state):
                target = process(123, ['python', str(root / 'state')], cwd=str(root))
                if isinstance(state, Exception):
                    target.status.side_effect = state
                else:
                    target.status.return_value = state
                with processes(target), native_references():
                    self.assertEqual(checks._ps_argv_table(), [(123, ['python', str(root / 'state')])])
                    pids, error, available = checks._open_file_pids(root)
                self.assertEqual(pids, {123})
                self.assertIsNone(error)
                self.assertTrue(available)
                target.status.assert_not_called()

    def test_unreadable_uids_are_unknown_and_cannot_exclude_a_process(self):
        included = process(123, effective_uid=0)
        included.uids.side_effect = observation.psutil.AccessDenied(123)
        with processes(included), native_references() as native:
            self.assertIsNone(checks._ps_argv_table())
            self.assertFalse(storage.process_inventory(Path('/private/checks-fixture'))[2])
            _, error, available = checks._open_file_pids(Path('/private/checks-fixture'))
        self.assertIsNotNone(error)
        self.assertTrue(available)
        native.assert_not_called()

    def test_effective_uid_is_read_once_per_pid_snapshot(self):
        included = process(123)
        with processes(included), native_references():
            _, error, _ = checks._open_file_pids(Path('/private/checks-fixture'))
        self.assertIsNone(error)
        included.uids.assert_called_once_with()

    def test_native_argv_tokens_keep_spaces_and_chinese_without_matching_text(self):
        root = Path('/private/检查 fixture with spaces')
        target = process(123, ['/python 路径/python', str(root / 'state')])
        text = process(456, ['python', '-c', f'print("{root}/state")'])
        neighbor = process(789, ['python', str(root) + '-other/state'])
        with processes(target, text, neighbor), native_references():
            findings, problems, _ = checks._observation_snapshot(root)
        self.assertEqual(set(findings), {123})
        self.assertEqual(findings[123]['executable'], 'python')
        self.assertEqual(problems, [])

    def test_unreadable_process_argv_is_unknown(self):
        target = process(123)
        target.cmdline.side_effect = observation.psutil.AccessDenied(123)
        with processes(target):
            self.assertIsNone(checks._ps_argv_table())

    def test_incomplete_enumeration_keeps_positive_findings_and_unknown_status(self):
        root = Path('/private/checks-fixture')
        target = process(123, ['python', str(root / 'state')])
        denied = process(456)
        denied.cmdline.side_effect = observation.psutil.AccessDenied(456)
        with processes(target, denied), native_references():
            findings, problems, _ = checks._observation_snapshot(root)
        self.assertEqual(set(findings), {123})
        self.assertIn('psutil process enumeration incomplete', problems[0])

    def test_process_exiting_between_snapshot_and_argv_is_ended(self):
        for error in (observation.psutil.NoSuchProcess(123), observation.psutil.ZombieProcess(123)):
            with self.subTest(error=type(error).__name__):
                target = process(123, ['python', '/private/checks-fixture/state'])
                target.cmdline.side_effect = error
                with processes(target):
                    self.assertEqual(checks._ps_argv_table(), [])

    def test_ended_or_zombie_process_releases_observed_paths(self):
        root = Path('/private/checks-fixture')
        for phase in ('cwd', 'memory_maps'):
            for error in (observation.psutil.NoSuchProcess(123), observation.psutil.ZombieProcess(123)):
                with self.subTest(phase=phase, error=type(error).__name__):
                    target = process(123, cwd=str(root), mappings=[root / 'library.so'])
                    getattr(target, phase).side_effect = error
                    with (processes(target), proc_references(),
                          patch.object(observation.psutil, 'LINUX', True),
                          patch.object(observation.psutil, 'MACOS', False)):
                        pids, error, available = checks._open_file_pids(root)
                    self.assertEqual(pids, set())
                    self.assertIsNone(error)
                    self.assertTrue(available)

    def test_lsof_exit_one_missing_ended_process_is_complete(self):
        root = Path('/private/checks-fixture')
        for state in ('gone', 'zombie', 'idle'):
            with self.subTest(state=state):
                survivor = process(123)
                ended = process(456, cwd=str(root))
                if state == 'gone':
                    ended.status.side_effect = observation.psutil.NoSuchProcess(456)
                else:
                    ended.status.return_value = state
                with processes(survivor, ended), native_references('p123\n', returncode=1):
                    pids, error, available = checks._open_file_pids(root)
                self.assertEqual(pids, set())
                self.assertIsNone(error)
                self.assertTrue(available)

    def test_lsof_missing_live_process_is_unknown(self):
        root = Path('/private/checks-fixture')
        for returncode in (0, 1):
            with self.subTest(returncode=returncode):
                with processes(process(123)), native_references('', returncode=returncode):
                    _, error, available = checks._open_file_pids(root)
                self.assertIsNotNone(error)
                self.assertTrue(available)

    def test_process_becoming_zombie_after_snapshot_explains_missing_lsof_result(self):
        root = Path('/private/checks-fixture')
        target = process(456, cwd=str(root))
        def cwd_before_exit():
            target.status.return_value = observation.psutil.STATUS_ZOMBIE
            return str(root)
        target.cwd.side_effect = cwd_before_exit
        with processes(target), native_references('', returncode=1) as native:
            pids, error, available = checks._open_file_pids(root)
        self.assertEqual(pids, set())
        self.assertIsNone(error)
        self.assertTrue(available)
        command = native.call_args.args[0]
        self.assertEqual(command[command.index('-p') + 1], '456')

    def test_other_lsof_failures_remain_unknown_even_when_a_process_ends(self):
        for returncode, stderr in ((2, ''), (0, 'denied'), (1, 'denied')):
            with self.subTest(returncode=returncode, stderr=stderr):
                ended = process(123)
                ended.status.side_effect = observation.psutil.NoSuchProcess(123)
                with (processes(ended),
                      native_references('', returncode=returncode, stderr=stderr)):
                    _, error, available = checks._open_file_pids(Path('/private/checks-fixture'))
                self.assertIsNotNone(error)
                self.assertTrue(available)

    def test_linux_missing_fd_directory_means_ended_but_denied_is_unknown(self):
        root = Path('/private/checks-fixture')
        for failure in (FileNotFoundError('fd directory gone'), PermissionError('fd denied')):
            with self.subTest(failure=type(failure).__name__):
                with (processes(process(123, cwd=str(root))),
                      patch.object(observation.psutil, 'LINUX', True),
                      patch.object(observation.psutil, 'MACOS', False),
                      patch.object(Path, 'iterdir', side_effect=failure)):
                    pids, error, available = checks._open_file_pids(root)
                self.assertEqual(error is None, isinstance(failure, FileNotFoundError))
                if error is None:
                    self.assertEqual(pids, set())
                self.assertTrue(available)

    def test_cwd_and_linux_mappings_find_processes_without_an_argv_reference(self):
        root = Path('/private/检查 fixture')
        cwd = process(123, cwd=str(root))
        mapped = process(456, mappings=[root / 'library.so (deleted)'])
        bystander = process(789, cwd=str(root) + '-other')
        with (processes(cwd, mapped, bystander), proc_references(),
              patch.object(observation.psutil, 'LINUX', True),
              patch.object(observation.psutil, 'MACOS', False)):
            pids, error, available = checks._open_file_pids(root)
        self.assertEqual(pids, {123, 456})
        self.assertIsNone(error)
        self.assertTrue(available)
        cwd.open_files.assert_not_called()

    def test_macos_keeps_native_mappings_and_uses_psutil_only_for_cwd(self):
        root = self.root
        target = process(123)
        target.memory_maps.side_effect = AssertionError('macOS mappings are not in psutil')
        with processes(target), native_references(f'p123\nn{root / "library.dylib"}\n') as command:
            pids, error, available = checks._open_file_pids(root)
        self.assertEqual(pids, {123})
        self.assertIsNone(error)
        self.assertTrue(available)
        self.assertIn('^cwd', command.call_args.args[0])
        target.memory_maps.assert_not_called()
        target.open_files.assert_not_called()

    def test_native_name_channel_keeps_deleted_private_handles_only(self):
        root = self.root / 'observed'
        output = (f'p123\nn{root / "state" / "control-daemon.lock"} (deleted)\n'
                  f'p456\nn{self.root / "other" / "state" / "board-owner.lock"}\n')
        with processes(process(123), process(456)), native_references(output):
            pids, error, available = checks._open_file_pids(root)
        self.assertEqual(pids, {123})
        self.assertIsNone(error)
        self.assertTrue(available)

    def test_linux_fd_channel_preserves_deleted_files_and_denied_reads_are_unknown(self):
        root = self.root
        for failure in (None, PermissionError('fd denied')):
            with self.subTest(failure=failure):
                with (processes(process(123)),
                      patch.object(observation.psutil, 'LINUX', True),
                      patch.object(observation.psutil, 'MACOS', False),
                      patch.object(observation.shutil, 'which', side_effect=AssertionError('Linux called lsof')),
                      proc_references(failure or f'{root / "unlinked"} (deleted)')):
                    pids, error, available = checks._open_file_pids(root)
                self.assertTrue(available)
                if failure:
                    self.assertIsNotNone(error)
                else:
                    self.assertEqual(pids, {123})
                    self.assertIsNone(error)

    def test_linux_descriptor_closed_before_read_does_not_make_observation_unknown(self):
        root = self.root
        with (processes(process(123)),
              patch.object(observation.psutil, 'LINUX', True),
              patch.object(observation.psutil, 'MACOS', False),
              patch.object(observation.shutil, 'which', side_effect=AssertionError('Linux called lsof')),
              proc_references(FileNotFoundError('descriptor closed'), str(root / 'open-file'))):
            pids, error, available = checks._open_file_pids(root)
        self.assertEqual(pids, {123})
        self.assertIsNone(error)
        self.assertTrue(available)

    def test_incomplete_paths_cannot_report_a_clear_root(self):
        for attribute in ('cwd', 'memory_maps', 'uids', 'status'):
            with self.subTest(attribute=attribute):
                target = process(123)
                getattr(target, attribute).side_effect = observation.psutil.AccessDenied(123)
                linux = attribute != 'status'
                with (processes(target),
                      patch.object(observation.psutil, 'LINUX', linux),
                      patch.object(observation.psutil, 'MACOS', not linux),
                      proc_references() if linux else native_references('')):
                    _, error, available = checks._open_file_pids(Path('/private/checks-fixture'))
                self.assertIsNotNone(error)
                self.assertTrue(available)

    def test_failed_path_reads_drop_references_of_starting_or_ending_processes(self):
        root = Path('/private/checks-fixture')
        for channel in ('cwd', 'memory_maps', 'proc-fd'):
            for state in ('idle', 'zombie', 'gone'):
                with self.subTest(channel=channel, state=state):
                    target = process(123, cwd=str(root), mappings=[root / 'library'])
                    if state == 'gone':
                        target.status.side_effect = observation.psutil.NoSuchProcess(123)
                    else:
                        target.status.return_value = state
                    if channel != 'proc-fd':
                        getattr(target, channel).side_effect = observation.psutil.AccessDenied(123)
                    with (processes(target), patch.object(observation.psutil, 'LINUX', True),
                          patch.object(observation.psutil, 'MACOS', False),
                          proc_references(PermissionError('fd denied')) if channel == 'proc-fd' else proc_references()):
                        pids, error, available = checks._open_file_pids(root)
                    self.assertEqual(pids, set())
                    self.assertTrue(available)
                    self.assertIsNone(error)

    def test_system_error_from_path_reads_is_not_caught(self):
        for attribute in ('cwd', 'memory_maps'):
            with self.subTest(attribute=attribute):
                target = process(123)
                getattr(target, attribute).side_effect = SystemError('path read failed')
                with (processes(target), proc_references(),
                      patch.object(observation.psutil, 'LINUX', True),
                      patch.object(observation.psutil, 'MACOS', False)):
                    with self.assertRaises(SystemError):
                        checks._open_file_pids(Path('/checks-fixture'))

    def test_system_error_from_status_is_not_caught(self):
        target = process(123)
        target.cmdline.side_effect = observation.psutil.AccessDenied(123)
        target.status.side_effect = SystemError('status read failed')
        with processes(target):
            with self.assertRaises(SystemError):
                checks._ps_argv_table()

    def test_windows_cwd_is_positive_evidence_without_enumerating_maps_or_handles(self):
        root = self.root
        for cwd in (str(root), str(root.parent)):
            with self.subTest(cwd=cwd):
                target = process(123, cwd=cwd, files=[root / 'file'], mappings=[root / 'library'])
                target.open_files.side_effect = AssertionError('Windows enumerated handles')
                target.memory_maps.side_effect = AssertionError('Windows enumerated mappings')
                with (processes(target), patch.object(observation.psutil, 'WINDOWS', True),
                      patch.object(observation.psutil, 'MACOS', False),
                      patch.object(observation.subprocess, 'run', side_effect=AssertionError('native POSIX channel called'))):
                    pids, error, available = checks._open_file_pids(root)
                self.assertEqual(pids, {123} if cwd == str(root) else set())
                self.assertIn('Windows open-file observation is incomplete', error)
                self.assertTrue(available)
                target.open_files.assert_not_called()
                target.memory_maps.assert_not_called()

    def test_windows_username_scope_is_shared_by_argv_storage_and_paths(self):
        included = process(123, ['python'], cwd=str(self.root), username='account')
        excluded = process(456, ['login'], username='other-account')
        excluded.cmdline.side_effect = observation.psutil.AccessDenied(456)
        excluded.cwd.side_effect = observation.psutil.AccessDenied(456)
        with (processes(included, excluded, current_username='account'),
              patch.object(observation.psutil, 'WINDOWS', True),
              patch.object(observation.psutil, 'MACOS', False)):
            self.assertEqual(checks._ps_argv_table(), [(123, ['python'])])
            _, commands, known = storage.process_inventory(self.root)
            pids, error, available = checks._open_file_pids(self.root)
        self.assertTrue(known)
        self.assertEqual(commands, ['python'])
        self.assertEqual(pids, {123})
        self.assertIn('Windows open-file observation is incomplete', error)
        self.assertNotIn('process enumeration incomplete', error)
        self.assertTrue(available)
        excluded.cmdline.assert_not_called()
        excluded.cwd.assert_not_called()
        included.uids.assert_not_called()
        excluded.uids.assert_not_called()

    def test_windows_unreadable_username_is_unknown_and_cannot_exclude_a_process(self):
        target = process(123)
        target.username.side_effect = observation.psutil.AccessDenied(123)
        with (processes(target), patch.object(observation.psutil, 'WINDOWS', True),
              patch.object(observation.psutil, 'MACOS', False)):
            self.assertIsNone(checks._ps_argv_table())
            self.assertFalse(storage.process_inventory(self.root)[2])
            _, error, available = checks._open_file_pids(self.root)
        self.assertIn('psutil process enumeration incomplete', error)
        self.assertIn('Windows open-file observation is incomplete', error)
        self.assertTrue(available)
        target.cmdline.assert_not_called()
        target.cwd.assert_not_called()

    def test_windows_unreadable_current_username_keeps_scope_unknown(self):
        target = process(123)
        with (processes(target) as current, patch.object(observation.psutil, 'WINDOWS', True),
              patch.object(observation.psutil, 'MACOS', False)):
            current.username.side_effect = observation.psutil.AccessDenied(0)
            self.assertIsNone(checks._ps_argv_table())
            _, error, _ = checks._open_file_pids(self.root)
        self.assertIn('psutil process enumeration incomplete', error)
        target.cmdline.assert_not_called()
        target.cwd.assert_not_called()

    def test_windows_service_inventory_still_observes_its_lock_files(self):
        foreign = self.root / 'foreign'
        service = process(123, ['python', '-m', 'hey_my_buddy.blackboard.service.daemon'],
                          files=[foreign / 'board-owner.lock'])
        with (processes(service), patch.object(observation.psutil, 'WINDOWS', True),
              patch.object(observation.psutil, 'MACOS', False)):
            orphans, _, known = storage.process_inventory(self.root)
        self.assertTrue(known)
        self.assertEqual([(row['pid'], row['stateDir']) for row in orphans], [(123, str(foreign))])
        service.open_files.assert_called_once_with()
