"""Focused transport tests: attaching to a healthy private service must not mutate anything.

Every fixture is an isolated local directory. Nothing here contacts a real C-Two
endpoint, the network, or a native App pipe: the health RPC is patched at the
``_request`` boundary so the tests observe exactly which filesystem operations
the attach path performs.
"""
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from buddy.transport import (
    ServiceError,
    _attach_read_only,
    _read_endpoint,
    _trusted_directory,
    call_service,
    ensure_service,
)

ENDPOINT = {"address": "ipc://buddy-attach-fixture", "token": "fixture-secret", "pid": 4242}


def snapshot(directory):
    return {path.name: (path.stat().st_mode, path.stat().st_size, path.stat().st_mtime_ns) for path in directory.iterdir()}


class MutationRecorder:
    """Records every call that could create, chmod or spawn something."""

    def __init__(self):
        self.patchers = [
            patch("buddy.transport.Path.mkdir", autospec=True),
            patch("buddy.transport.Path.chmod", autospec=True),
            patch("buddy.transport.os.open", wraps=os.open),
            patch("buddy.transport.subprocess.Popen", autospec=True),
        ]
        self.mkdir = self.chmod = self.os_open = self.popen = None

    def __enter__(self):
        self.mkdir, self.chmod, self.os_open, self.popen = (item.start() for item in self.patchers)
        return self

    def __exit__(self, *_):
        for item in reversed(self.patchers):
            item.stop()

    def assert_no_mutation(self):
        self.mkdir.assert_not_called()
        self.chmod.assert_not_called()
        self.popen.assert_not_called()
        mutating = [call for call in self.os_open.call_args_list if call.args[1] & (os.O_CREAT | os.O_WRONLY | os.O_RDWR | os.O_APPEND)]
        assert mutating == [], f"attach opened a file for writing: {mutating}"


class AttachFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-attach-")
        self.directory = Path(self.temp.name) / "state"
        self.directory.mkdir(mode=0o700)
        self.directory.chmod(0o700)
        self.endpoint_file = self.directory / "control.json"
        self.write_endpoint(ENDPOINT, 0o600)
        # The health RPC is the only thing that may be touched: never the network.
        self.request = patch("buddy.transport._request", return_value={"status": "ready"}).start()
        self.addCleanup(patch.stopall)

    def write_endpoint(self, value, mode):
        self.endpoint_file.write_text(json.dumps(value))
        self.endpoint_file.chmod(mode)


class ReadOnlyAttachTests(AttachFixture):
    def test_healthy_private_service_attaches_without_any_filesystem_mutation(self):
        before = snapshot(self.directory)
        with MutationRecorder() as recorder:
            endpoint = ensure_service(self.directory)
        self.assertEqual(endpoint, ENDPOINT)
        self.request.assert_called_once()
        recorder.assert_no_mutation()
        self.assertEqual(before, snapshot(self.directory), "read-only attach changed the filesystem")

    def test_attach_succeeds_when_the_directory_cannot_be_written(self):
        self.directory.chmod(0o500)
        self.addCleanup(self.directory.chmod, 0o700)
        self.assertFalse(os.access(self.directory, os.W_OK), "fixture must be non-writable to prove read-only attach")
        endpoint = ensure_service(self.directory)
        self.assertEqual(endpoint, ENDPOINT)
        self.request.assert_called_once()

    def test_call_service_health_uses_read_only_attach_for_existing_service(self):
        before = snapshot(self.directory)
        with MutationRecorder() as recorder:
            self.assertEqual(call_service("health", state_dir=self.directory), {"status": "ready"})
        recorder.assert_no_mutation()
        self.assertEqual(before, snapshot(self.directory))

    def test_call_service_stop_of_a_healthy_service_is_read_only(self):
        self.request.side_effect = [{"status": "ready"}, {"status": "stopping"}]
        with MutationRecorder() as recorder:
            self.assertEqual(call_service("stop", state_dir=self.directory), {"status": "stopping"})
        recorder.assert_no_mutation()
        self.assertEqual(self.request.call_args_list[1].args[1], "stop")


class TrustBoundaryTests(AttachFixture):
    """An insecure or malformed endpoint must never be trusted without chmod."""

    def assert_rejected_by_validation(self):
        with MutationRecorder() as recorder:
            self.assertIsNone(_attach_read_only(self.directory))
            self.assertIsNone(_read_endpoint(self.directory))
        self.request.assert_not_called()
        recorder.assert_no_mutation()
        return recorder

    def assert_falls_back_to_cold_start(self):
        """An untrusted endpoint must fall through to the authorized cold setup."""
        calls = []
        requests_before_spawn = []

        def endpoint_read(directory):
            calls.append(directory)
            # Untrusted for the pre-lock and under-lock attempts, then the
            # daemon's own freshly written endpoint, exactly as on a cold start.
            return None if len(calls) <= 2 else ENDPOINT

        def spawn(*_args, **_kwargs):
            requests_before_spawn.append(self.request.call_count)
            child = Mock()
            child.poll.return_value = 1
            child.wait.return_value = 1
            return child

        with MutationRecorder() as recorder:
            recorder.popen.side_effect = spawn
            with patch("buddy.transport._read_endpoint", side_effect=endpoint_read):
                self.assertEqual(ensure_service(self.directory), ENDPOINT)
            spawned, created = recorder.popen.call_count, recorder.mkdir.call_count
        self.assertEqual(spawned, 1, "cold setup must still run when no endpoint can be trusted")
        self.assertEqual(created, 1, "cold setup must create the state directory")
        self.assertGreaterEqual(len(calls), 3, "health must be re-checked after spawning")
        self.assertEqual(requests_before_spawn, [0], "no RPC may run against an untrusted endpoint")

    def test_group_or_world_accessible_directory_is_not_trusted(self):
        self.directory.chmod(0o755)
        self.assert_rejected_by_validation()
        self.assert_falls_back_to_cold_start()

    def test_private_but_unreadable_directory_is_not_trusted(self):
        self.directory.chmod(0o000)
        self.addCleanup(self.directory.chmod, 0o700)
        self.assertIsNone(_attach_read_only(self.directory))
        self.assertIsNone(_read_endpoint(self.directory))
        self.request.assert_not_called()

    def test_symlinked_state_directory_is_not_trusted(self):
        link = Path(self.temp.name) / "state-link"
        link.symlink_to(self.directory)
        self.assertFalse(_trusted_directory(link))
        self.assertIsNone(_attach_read_only(link))

    def test_state_directory_under_a_sticky_shared_parent_is_trusted(self):
        """POSIX sticky semantics: another user cannot rename this user's entry.

        A private state directory directly under a sticky shared parent (as on a
        root-owned /tmp) is therefore unswappable and must be trusted, so a later
        attach reuses the running service instead of cold-starting again.
        """
        shared = Path(tempfile.mkdtemp(prefix="buddy-shared-", dir="/tmp"))
        self.addCleanup(lambda: shutil.rmtree(shared, ignore_errors=True))
        shared.chmod(0o777 | stat.S_ISVTX)
        nested = shared / "state"
        nested.mkdir(mode=0o700)
        nested.chmod(0o700)
        (nested / "control.json").write_text(json.dumps(ENDPOINT))
        (nested / "control.json").chmod(0o600)
        self.assertTrue(shared.stat().st_mode & stat.S_ISVTX, "fixture must be a sticky shared directory")
        self.assertTrue(nested.parent.parent.samefile(shared.parent), "fixture must sit under the real shared /tmp")
        self.assertTrue(_trusted_directory(nested))
        self.assertEqual(_read_endpoint(nested), ENDPOINT)

    def test_state_directory_under_a_non_sticky_shared_parent_is_not_trusted(self):
        """Without the sticky bit another user can rename the entry, so refuse it."""
        shared = Path(tempfile.mkdtemp(prefix="buddy-shared-", dir="/tmp"))
        self.addCleanup(lambda: shutil.rmtree(shared, ignore_errors=True))
        shared.chmod(0o777)
        nested = shared / "state"
        nested.mkdir(mode=0o700)
        nested.chmod(0o700)
        (nested / "control.json").write_text(json.dumps(ENDPOINT))
        (nested / "control.json").chmod(0o600)
        self.assertFalse(shared.stat().st_mode & stat.S_ISVTX, "fixture must be non-sticky")
        self.assertFalse(_trusted_directory(nested))
        self.assertIsNone(_read_endpoint(nested))

    def test_sticky_shared_parent_owned_by_a_foreign_user_is_not_trusted(self):
        """A foreign owner of the sticky parent could still rename this user's entry."""
        shared = Path(tempfile.mkdtemp(prefix="buddy-shared-", dir="/tmp"))
        self.addCleanup(lambda: shutil.rmtree(shared, ignore_errors=True))
        shared.chmod(0o777 | stat.S_ISVTX)
        nested = shared / "state"
        nested.mkdir(mode=0o700)
        nested.chmod(0o700)
        real_lstat = os.lstat

        def foreign_shared(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if Path(path) == shared:
                fields = [info[index] for index in range(10)]
                fields[4] = os.geteuid() + 1  # st_uid
                return os.stat_result(fields)
            return info

        with patch("buddy.transport.os.lstat", side_effect=foreign_shared):
            self.assertFalse(_trusted_directory(nested))
            self.assertIsNone(_read_endpoint(nested))

    def test_symlinked_endpoint_file_is_not_trusted(self):
        real = self.directory / "real-control.json"
        real.write_text(json.dumps(ENDPOINT))
        real.chmod(0o600)
        self.endpoint_file.unlink()
        self.endpoint_file.symlink_to(real)
        self.assertFalse(stat.S_ISREG(self.endpoint_file.lstat().st_mode))
        self.assert_rejected_by_validation()

    def test_foreign_owner_is_not_trusted(self):
        def as_other_owner(result):
            fields = [result[index] for index in range(10)]
            fields[4] = os.geteuid() + 1  # st_uid
            return os.stat_result(fields)

        real_lstat, real_fstat = os.lstat, os.fstat
        with patch("buddy.transport.os.lstat", side_effect=lambda *args, **kwargs: as_other_owner(real_lstat(*args, **kwargs))):
            self.assertFalse(_trusted_directory(self.directory))
        with patch("buddy.transport.os.fstat", side_effect=lambda *args, **kwargs: as_other_owner(real_fstat(*args, **kwargs))):
            self.assertIsNone(_read_endpoint(self.directory))

    def test_unsafe_endpoint_file_mode_is_not_trusted(self):
        self.write_endpoint(ENDPOINT, 0o644)
        self.assert_rejected_by_validation()
        self.assert_falls_back_to_cold_start()

    def test_endpoint_outside_a_private_directory_is_not_trusted(self):
        self.directory.chmod(0o750)
        self.assert_rejected_by_validation()

    def test_unhealthy_endpoint_is_not_attached_read_only(self):
        self.request.side_effect = ServiceError("SERVICE_UNAVAILABLE", "no service")
        self.assertIsNone(_attach_read_only(self.directory))
        self.request.assert_called_once()
        self.request.reset_mock()
        self.request.side_effect = None
        self.request.return_value = {"status": "ready"}
        self.assert_falls_back_to_cold_start()

    def test_unresolvable_health_reply_is_not_attached(self):
        self.request.side_effect = ServiceError("INVALID_RESPONSE", "malformed")
        self.assertIsNone(_attach_read_only(self.directory))

    def test_malformed_endpoint_contents_are_never_trusted(self):
        for contents in ("not json", json.dumps([1, 2]), json.dumps({"address": "tcp://elsewhere", "token": "x"}), json.dumps({"address": "ipc://x", "token": ""})):
            with self.subTest(contents=contents):
                self.endpoint_file.write_text(contents)
                self.endpoint_file.chmod(0o600)
                self.assertIsNone(_read_endpoint(self.directory))
                self.assertIsNone(_attach_read_only(self.directory))
        self.request.assert_not_called()


class ColdStartTests(unittest.TestCase):
    """Cold startup keeps its authorized setup path: create, lock, spawn, wait."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-cold-")
        self.directory = Path(self.temp.name) / "state"
        self.spawn = patch("buddy.transport.subprocess.Popen", autospec=True).start()
        child = Mock()
        child.poll.return_value = None
        child.wait.return_value = 0
        self.spawn.return_value = child
        self.addCleanup(patch.stopall)

    def test_cold_start_creates_private_state_directory_and_spawns_daemon(self):
        responses = [None, None, ENDPOINT]  # no attach before the spawn, then healthy

        with patch("buddy.transport._healthy", side_effect=responses) as health:
            endpoint = ensure_service(self.directory)
        self.assertEqual(endpoint, ENDPOINT)
        self.assertEqual(health.call_count, 3, "cold start must re-check health after spawning")
        self.assertEqual(health.call_args_list, [call(self.directory.resolve())] * 3, "cold start and attach must share one trust rule with no bypass argument")
        self.assertEqual(self.spawn.call_count, 1)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.directory / "control-start.lock").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.directory / "control.log").stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(self.spawn.call_args.args[0][2]).name, "buddy.daemon")

    def test_existing_service_under_a_shared_sticky_parent_attaches_read_only(self):
        """Regression: a service under a root-owned sticky /tmp is reused, not re-spawned."""
        shared = Path(tempfile.mkdtemp(prefix="buddy-shared-", dir="/tmp"))
        self.addCleanup(lambda: shutil.rmtree(shared, ignore_errors=True))
        shared.chmod(0o777 | stat.S_ISVTX)
        directory = shared / "state"
        directory.mkdir(mode=0o700)
        endpoint_file = directory / "control.json"
        endpoint_file.write_text(json.dumps(ENDPOINT))
        endpoint_file.chmod(0o600)
        with patch("buddy.transport._request", return_value={"status": "ready"}) as request:
            self.assertEqual(ensure_service(directory), ENDPOINT)
        request.assert_called_once()
        self.spawn.assert_not_called()

    def test_cold_start_fails_honestly_when_the_state_directory_cannot_be_written(self):
        # A path that already exists as a file cannot become the state directory.
        blocked = Path(self.temp.name) / "state"
        blocked.write_text("not a directory")
        with self.assertRaises(OSError):
            ensure_service(blocked)
        self.spawn.assert_not_called()
        self.assertEqual(blocked.read_text(), "not a directory")

    def test_cold_start_reports_a_daemon_that_exits_immediately(self):
        self.spawn.return_value.poll.return_value = 1
        with patch("buddy.transport._healthy", return_value=None):
            with self.assertRaises(ServiceError) as failure:
                ensure_service(self.directory)
        self.assertEqual(failure.exception.code, "SERVICE_START_FAILED")
        self.assertEqual(self.spawn.call_count, 1)

    def test_cold_start_and_later_attach_share_the_same_trust_rule_under_a_shared_parent(self):
        """A /tmp-style state dir is trusted by cold start and by every later attach."""
        shared = Path(tempfile.mkdtemp(prefix="buddy-shared-", dir="/tmp"))
        self.addCleanup(lambda: shutil.rmtree(shared, ignore_errors=True))
        shared.chmod(0o777 | stat.S_ISVTX)
        directory = shared / "state"
        directory.mkdir(mode=0o700)
        endpoint_file = directory / "control.json"
        endpoint_file.write_text(json.dumps(ENDPOINT))
        endpoint_file.chmod(0o600)
        self.assertEqual(_read_endpoint(directory), ENDPOINT, "cold start must trust the endpoint it just wrote")
        with patch("buddy.transport._request", return_value={"status": "ready"}) as request:
            self.assertEqual(_attach_read_only(directory), ENDPOINT, "later attach must apply the same rule, with no cold-start exception")
        request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
