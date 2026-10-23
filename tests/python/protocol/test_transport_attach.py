"""Focused transport tests: attaching to a healthy private service must not mutate anything.

Every fixture is an isolated local directory. Most tests patch the ping RPC to
observe filesystem operations. The IPC access tests use the existing native
probe subprocess with a controlled ping contract, never a model or daily peer.
"""
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch
from hey_my_buddy.errors import BoardError
from hey_my_buddy import private_dirs
from hey_my_buddy.protocol import transport

from hey_my_buddy.protocol.transport import (
    ServiceError,
    _attach_read_only,
    _read_endpoint,
    _request,
    _trusted_directory,
    call_service,
    ensure_service,
)

ENDPOINT = {"address": "ipc://hey-my-buddy-attach-fixture", "token": "fixture-secret", "pid": 4242}
LAUNCH_TARGET = {"python": sys.executable, "pythonPath": None,
                 "identity": "source:attach-fixture", "stable": False, "installed": False}


def snapshot(directory):
    return {path.name: (path.stat().st_mode, path.stat().st_size, path.stat().st_mtime_ns) for path in directory.iterdir()}


class MutationRecorder:
    """Records every call that could create, chmod or spawn something."""

    def __init__(self):
        self.patchers = [
            patch("hey_my_buddy.protocol.transport.Path.mkdir", autospec=True),
            patch("hey_my_buddy.protocol.transport.Path.chmod", autospec=True),
            patch("hey_my_buddy.protocol.transport.os.open", wraps=os.open),
            patch("hey_my_buddy.protocol.transport.subprocess.Popen", autospec=True),
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
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-attach-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "state"
        self.directory.mkdir(mode=0o700)
        self.directory.chmod(0o700)
        (self.directory / "ipc").mkdir(mode=0o700)
        self.endpoint_file = self.directory / "control.json"
        self.write_endpoint(ENDPOINT, 0o600)
        # The ping RPC is the only thing that may be touched: never the network.
        self.request = patch("hey_my_buddy.protocol.transport._request", return_value={"status": "ready"}).start()
        # This fixture observes bootstrap I/O, not runtime installation. Never
        # resolve an operator's runtime or create environments from a unit test.
        patch("hey_my_buddy.install.runtime.launch_target", return_value=LAUNCH_TARGET).start()
        sockets = patch("hey_my_buddy.protocol.transport.socket.socket", autospec=True).start()
        sockets.return_value.accept.return_value = (Mock(), None)
        self.addCleanup(patch.stopall)

    def write_endpoint(self, value, mode):
        self.endpoint_file.write_text(json.dumps(value), encoding="utf-8", newline="\n")
        self.endpoint_file.chmod(mode)


@unittest.skipIf(os.name == "nt", "POSIX directory ownership and account databases")
class AncestorGroupTrustTests(unittest.TestCase):
    def trusted(self, *, mode=0o775, directory_gid=2000, group_name="fixture-user", members=(), other_accounts=()):
        parents = {
            Path("/fixture"): os.stat_result((stat.S_IFDIR | mode, 1, 1, 1, 1000,
                                              directory_gid, 0, 0, 0, 0)),
            Path("/"): os.stat_result((stat.S_IFDIR | 0o755, 2, 1, 1, 0, 0, 0, 0, 0, 0)),
        }
        account = SimpleNamespace(pw_name="fixture-user", pw_uid=1000, pw_gid=2000)
        group = SimpleNamespace(gr_name=group_name, gr_mem=list(members))
        with patch.object(transport.os, "geteuid", return_value=1000), \
                patch.object(transport.os, "lstat", side_effect=lambda path: parents[Path(path)]), \
                patch("pwd.getpwuid", return_value=account), \
                patch("pwd.getpwall", return_value=[account, *other_accounts]), \
                patch("grp.getgrgid", return_value=group):
            return transport._trusted_ancestors(Path("/fixture/state"))

    def test_group_writable_ancestor_with_private_primary_group_is_trusted(self):
        for members in ((), ("fixture-user",)):
            with self.subTest(members=members):
                self.assertTrue(self.trusted(members=members))

    def test_group_writable_ancestor_with_shared_group_is_not_trusted(self):
        self.assertFalse(self.trusted(group_name="shared"))

    def test_group_writable_ancestor_with_non_primary_group_is_not_trusted(self):
        self.assertFalse(self.trusted(directory_gid=2001))

    def test_group_writable_ancestor_with_another_member_is_not_trusted(self):
        self.assertFalse(self.trusted(members=("fixture-user", "another-user")))

    def test_group_writable_ancestor_with_another_primary_member_is_not_trusted(self):
        other = SimpleNamespace(pw_uid=1001, pw_gid=2000)
        self.assertFalse(self.trusted(other_accounts=(other,)))

    def test_account_with_another_primary_group_does_not_prevent_trust(self):
        other = SimpleNamespace(pw_uid=1001, pw_gid=2001)
        self.assertTrue(self.trusted(other_accounts=(other,)))

    def test_missing_account_or_group_record_is_not_trusted(self):
        account = SimpleNamespace(pw_name="fixture-user", pw_uid=1000, pw_gid=2000)
        group = SimpleNamespace(gr_name="fixture-user", gr_mem=[])
        for missing in ("account", "group"):
            with self.subTest(missing=missing), \
                    patch.object(transport.os, "geteuid", return_value=1000), \
                    patch("pwd.getpwuid", return_value=account) as lookup_account, \
                    patch("grp.getgrgid", return_value=group) as lookup_group, \
                    patch("pwd.getpwall", return_value=[account]):
                (lookup_account if missing == "account" else lookup_group).side_effect = KeyError(1000)
                self.assertFalse(transport._private_primary_group(2000))

    def test_world_writable_ancestor_without_sticky_bit_is_not_trusted(self):
        self.assertFalse(self.trusted(mode=0o777))


class ReadOnlyAttachTests(AttachFixture):
    def test_healthy_service_still_attaches_during_upgrade(self):
        (self.directory / "upgrade.json").write_text("{}", encoding="utf-8", newline="\n")
        with MutationRecorder() as recorder:
            self.assertEqual(_attach_read_only(self.directory), ENDPOINT)
        recorder.assert_no_mutation()

    def test_healthy_private_service_attaches_without_any_filesystem_mutation(self):
        before = snapshot(self.directory)
        with MutationRecorder() as recorder:
            endpoint = ensure_service(self.directory)
        self.assertEqual(endpoint, ENDPOINT)
        self.request.assert_called_once()
        self.assertEqual(self.request.call_args.args[1], "ping")
        recorder.assert_no_mutation()
        self.assertEqual(before, snapshot(self.directory), "read-only attach changed the filesystem")

    @unittest.skipIf(os.name == "nt", "POSIX directory write modes")
    def test_attach_succeeds_when_the_directory_cannot_be_written(self):
        self.directory.chmod(0o500)
        self.addCleanup(self.directory.chmod, 0o700)
        self.assertFalse(os.access(self.directory, os.W_OK), "fixture must be non-writable to prove read-only attach")
        before = snapshot(self.directory)
        with MutationRecorder() as recorder:
            self.assertEqual(ensure_service(self.directory), ENDPOINT)
        recorder.assert_no_mutation()
        self.request.assert_called_once()
        self.assertEqual(before, snapshot(self.directory), "read-only attach changed the filesystem")
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o500)

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
        # The CLI method maps onto one named board operation.
        self.assertEqual(self.request.call_args_list[1].args[1], "service_control")


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
            if os.name != "nt":
                recorder.chmod.side_effect = lambda directory, mode: os.chmod(directory, mode)
            recorder.popen.side_effect = spawn
            with patch("hey_my_buddy.protocol.transport._read_endpoint", side_effect=endpoint_read):
                self.assertEqual(ensure_service(self.directory), ENDPOINT)
            spawned, created = recorder.popen.call_count, recorder.mkdir.call_count
        self.assertEqual(spawned, 1, "cold setup must still run when no endpoint can be trusted")
        if os.name == "nt":
            private_dirs.validate_private_dir(self.directory)
            recorder.chmod.assert_not_called()
        else:
            self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
            recorder.chmod.assert_called_once_with(self.directory.resolve(), 0o700)
        self.assertGreaterEqual(len(calls), 3, "health must be re-checked after spawning")
        self.assertEqual(requests_before_spawn, [0], "no RPC may run against an untrusted endpoint")

    @unittest.skipIf(os.name == "nt", "POSIX owner, mode and ancestor policy")
    def test_group_or_world_accessible_directory_is_not_trusted(self):
        self.directory.chmod(0o755)
        self.assert_rejected_by_validation()
        self.assert_falls_back_to_cold_start()
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)

    @unittest.skipIf(os.name == "nt", "POSIX owner, mode and ancestor policy")
    def test_group_writable_state_directory_is_not_trusted_even_with_private_group(self):
        self.directory.chmod(0o770)
        with patch.object(transport, "_private_primary_group", return_value=True) as group:
            self.assert_rejected_by_validation()
        group.assert_not_called()

    @unittest.skipIf(os.name == "nt", "POSIX directory modes")
    def test_real_group_writable_ancestor_requires_private_group(self):
        parent = Path(self.temp.name) / "group-writable"
        parent.mkdir(mode=0o770)
        parent.chmod(0o770)
        directory = parent / "state"
        directory.mkdir(mode=0o700)
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o770)
        for private in (True, False):
            with self.subTest(private=private), \
                    patch.object(transport, "_private_primary_group", return_value=private) as group:
                self.assertEqual(_trusted_directory(directory), private)
                group.assert_any_call(parent.stat().st_gid)

    @unittest.skipIf(os.name == "nt", "POSIX owner, mode and ancestor policy")
    def test_private_but_unreadable_directory_is_not_trusted(self):
        self.directory.chmod(0o000)
        self.addCleanup(self.directory.chmod, 0o700)
        self.assertIsNone(_attach_read_only(self.directory))
        self.assertIsNone(_read_endpoint(self.directory))
        self.request.assert_not_called()

    @unittest.skipIf(os.name == "nt", "POSIX owner, mode and ancestor policy")
    def test_symlinked_state_directory_is_not_trusted(self):
        link = Path(self.temp.name) / "state-link"
        link.symlink_to(self.directory)
        self.assertFalse(_trusted_directory(link))
        self.assertIsNone(_attach_read_only(link))

    @unittest.skipIf(os.name == "nt", "POSIX owner, mode and ancestor policy")
    def test_state_directory_under_a_sticky_shared_parent_is_trusted(self):
        """POSIX sticky semantics: another user cannot rename this user's entry.

        A private state directory directly under a sticky shared parent (as on a
        root-owned /tmp) is therefore unswappable and must be trusted, so a later
        attach reuses the running service instead of cold-starting again.
        """
        shared = Path(tempfile.mkdtemp(prefix="hey-my-buddy-shared-", dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp")))
        self.addCleanup(lambda: shutil.rmtree(shared, ignore_errors=True))
        shared.chmod(0o777 | stat.S_ISVTX)
        nested = shared / "state"
        nested.mkdir(mode=0o700)
        nested.chmod(0o700)
        (nested / "control.json").write_text(json.dumps(ENDPOINT), encoding="utf-8", newline="\n")
        (nested / "control.json").chmod(0o600)
        self.assertTrue(shared.stat().st_mode & stat.S_ISVTX, "fixture must be a sticky shared directory")
        self.assertTrue(nested.parent.parent.samefile(shared.parent), "fixture must sit under the real shared /tmp")
        self.assertTrue(_trusted_directory(nested))
        self.assertEqual(_read_endpoint(nested), ENDPOINT)

    @unittest.skipIf(os.name == "nt", "POSIX owner, mode and ancestor policy")
    def test_state_directory_under_a_non_sticky_shared_parent_is_not_trusted(self):
        """Without the sticky bit another user can rename the entry, so refuse it."""
        shared = Path(tempfile.mkdtemp(prefix="hey-my-buddy-shared-", dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp")))
        self.addCleanup(lambda: shutil.rmtree(shared, ignore_errors=True))
        shared.chmod(0o777)
        nested = shared / "state"
        nested.mkdir(mode=0o700)
        nested.chmod(0o700)
        (nested / "control.json").write_text(json.dumps(ENDPOINT), encoding="utf-8", newline="\n")
        (nested / "control.json").chmod(0o600)
        self.assertFalse(shared.stat().st_mode & stat.S_ISVTX, "fixture must be non-sticky")
        self.assertFalse(_trusted_directory(nested))
        self.assertIsNone(_read_endpoint(nested))

    @unittest.skipIf(os.name == "nt", "POSIX owner, mode and ancestor policy")
    def test_sticky_shared_parent_owned_by_a_foreign_user_is_not_trusted(self):
        """A foreign owner of the sticky parent could still rename this user's entry."""
        shared = Path(tempfile.mkdtemp(prefix="hey-my-buddy-shared-", dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp")))
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

        with patch("hey_my_buddy.protocol.transport.os.lstat", side_effect=foreign_shared):
            self.assertFalse(_trusted_directory(nested))
            self.assertIsNone(_read_endpoint(nested))

    def test_symlinked_endpoint_file_is_not_trusted(self):
        real = self.directory / "real-control.json"
        real.write_text(json.dumps(ENDPOINT), encoding="utf-8", newline="\n")
        real.chmod(0o600)
        self.endpoint_file.unlink()
        self.endpoint_file.symlink_to(real)
        self.assertFalse(stat.S_ISREG(self.endpoint_file.lstat().st_mode))
        self.assert_rejected_by_validation()

    @unittest.skipIf(os.name == "nt", "POSIX owner, mode and ancestor policy")
    def test_foreign_owner_is_not_trusted(self):
        def as_other_owner(result):
            fields = [result[index] for index in range(10)]
            fields[4] = os.geteuid() + 1  # st_uid
            return os.stat_result(fields)

        real_lstat, real_fstat = os.lstat, os.fstat
        with patch("hey_my_buddy.protocol.transport.os.lstat", side_effect=lambda *args, **kwargs: as_other_owner(real_lstat(*args, **kwargs))):
            self.assertFalse(_trusted_directory(self.directory))
        with patch("hey_my_buddy.protocol.transport.os.fstat", side_effect=lambda *args, **kwargs: as_other_owner(real_fstat(*args, **kwargs))):
            self.assertIsNone(_read_endpoint(self.directory))

    @unittest.skipIf(os.name == "nt", "POSIX owner, mode and ancestor policy")
    def test_unsafe_endpoint_file_mode_is_not_trusted(self):
        self.write_endpoint(ENDPOINT, 0o644)
        self.assert_rejected_by_validation()
        self.assert_falls_back_to_cold_start()

    @unittest.skipIf(os.name == "nt", "POSIX owner, mode and ancestor policy")
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

    def test_invalid_health_reply_fails_without_cold_start(self):
        self.request.side_effect = ServiceError("INVALID_RESPONSE", "malformed")
        with MutationRecorder() as recorder, patch("hey_my_buddy.protocol.transport.subprocess.Popen") as spawn:
            for attach in (_attach_read_only, ensure_service):
                with self.assertRaises(ServiceError) as raised:
                    attach(self.directory)
                self.assertEqual(raised.exception.code, "INVALID_RESPONSE")
            spawn.assert_not_called()
        recorder.assert_no_mutation()

    def test_malformed_endpoint_contents_are_never_trusted(self):
        for contents in ("not json", json.dumps([1, 2]), json.dumps({"address": "tcp://elsewhere", "token": "x"}), json.dumps({"address": "ipc://x", "token": ""})):
            with self.subTest(contents=contents):
                self.endpoint_file.write_text(contents, encoding="utf-8", newline="\n")
                self.endpoint_file.chmod(0o600)
                self.assertIsNone(_read_endpoint(self.directory))
                self.assertIsNone(_attach_read_only(self.directory))
        self.request.assert_not_called()


class ColdStartTests(unittest.TestCase):
    """Cold startup keeps its authorized setup path: create, lock, spawn, wait."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-cold-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "state"
        self.spawn = patch("hey_my_buddy.protocol.transport.subprocess.Popen", autospec=True).start()
        child = Mock()
        child.poll.return_value = None
        child.wait.return_value = 0
        self.spawn.return_value = child
        patch("hey_my_buddy.install.runtime.launch_target", return_value=LAUNCH_TARGET).start()
        sockets = patch("hey_my_buddy.protocol.transport.socket.socket", autospec=True).start()
        sockets.return_value.accept.return_value = (Mock(), None)
        self.addCleanup(patch.stopall)

    def test_cold_start_creates_private_state_directory_and_spawns_daemon(self):
        responses = [None, None, ENDPOINT]  # no attach before the spawn, then healthy

        with patch("hey_my_buddy.protocol.transport._healthy", side_effect=responses) as health:
            endpoint = ensure_service(self.directory)
        self.assertEqual(endpoint, ENDPOINT)
        self.assertEqual(health.call_count, 3, "cold start must re-check health after spawning")
        self.assertEqual(health.call_args_list, [call(self.directory.resolve())] * 3, "cold start and attach must share one trust rule with no bypass argument")
        self.assertEqual(self.spawn.call_count, 1)
        if os.name != "nt":
            self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.directory / "control-start.lock").stat().st_mode & 0o777, 0o600)
            self.assertEqual((self.directory / "control.log").stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(self.spawn.call_args.args[0][2]).name, "hey_my_buddy.blackboard.service.daemon")

    def test_orphan_upgrade_journal_without_startup_lock_refuses_without_preflight(self):
        self.directory.mkdir(mode=0o700)
        (self.directory / "upgrade.json").write_text("{}", encoding="utf-8", newline="\n")
        with patch("hey_my_buddy.protocol.transport._cold_start_preflight") as preflight:
            with self.assertRaises(ServiceError) as refused:
                ensure_service(self.directory)
        self.assertEqual(refused.exception.code, "UPGRADE_IN_PROGRESS")
        self.assertIn("rerun install", refused.exception.message)
        preflight.assert_not_called()
        self.spawn.assert_not_called()
        self.assertFalse((self.directory / "control-start.lock").exists(), "an orphan journal must not create a lock")

    def test_upgrade_journal_with_orphan_lock_refuses_control_and_wait_without_preflight(self):
        self.directory.mkdir(mode=0o700)
        (self.directory / "upgrade.json").write_text("{}", encoding="utf-8", newline="\n")
        (self.directory / "control-start.lock").touch(mode=0o600)
        with patch.object(transport, '_attach_read_only') as attach, \
             patch.object(transport, '_cold_start_preflight') as preflight:
            for resource in ("control", "wait"):
                with self.subTest(resource=resource), self.assertRaises(ServiceError) as refused:
                    ensure_service(self.directory, resource=resource)
                self.assertEqual(refused.exception.code, "UPGRADE_IN_PROGRESS")
        attach.assert_not_called()
        preflight.assert_not_called()
        self.spawn.assert_not_called()

    def test_installer_finishes_before_an_existing_client_reattaches(self):
        self.directory.mkdir(mode=0o700)
        marker = self.directory / "upgrade.json"
        marker.write_text("{}", encoding="utf-8", newline="\n")

        def installer_unlocks(_fd):
            marker.unlink()

        (self.directory / "control-start.lock").touch(mode=0o600)
        with patch.object(transport, '_attach_read_only', return_value=ENDPOINT) as attach, \
             patch.object(transport, '_cold_start_preflight') as preflight, \
             patch.object(transport.locking, 'lock', side_effect=installer_unlocks) as lock:
            self.assertEqual(ensure_service(self.directory), ENDPOINT)
        lock.assert_called_once()
        attach.assert_called_once_with(self.directory.resolve())
        preflight.assert_not_called()
        self.spawn.assert_not_called()

    def test_installer_finishes_before_wait_client_reattaches(self):
        self.directory.mkdir(mode=0o700)
        marker = self.directory / "upgrade.json"
        marker.write_text("{}", encoding="utf-8", newline="\n")
        (self.directory / "control-start.lock").touch(mode=0o600)

        def installer_unlocks(_fd):
            marker.unlink()

        with patch.object(transport, '_attach_read_only', return_value=ENDPOINT) as attach, \
             patch.object(transport, '_cold_start_preflight') as preflight, \
             patch.object(transport.locking, 'lock', side_effect=installer_unlocks):
            self.assertEqual(ensure_service(self.directory, resource="wait"), ENDPOINT)
        attach.assert_called_once_with(self.directory.resolve())
        preflight.assert_not_called()
        self.spawn.assert_not_called()

    def test_upgrade_journal_created_during_preflight_refuses_spawn_under_lock(self):
        original_preflight = transport._cold_start_preflight

        def fence_after_preflight(directory):
            original_preflight(directory)
            (directory / "upgrade.json").write_text("{}", encoding="utf-8", newline="\n")

        with patch("hey_my_buddy.protocol.transport._cold_start_preflight", side_effect=fence_after_preflight):
            with self.assertRaises(ServiceError) as refused:
                ensure_service(self.directory)
        self.assertEqual(refused.exception.code, "UPGRADE_IN_PROGRESS")
        self.assertTrue((self.directory / "control-start.lock").exists())
        self.spawn.assert_not_called()

    @unittest.skipIf(os.name == "nt", "POSIX owner, mode and ancestor policy")
    def test_existing_service_under_a_shared_sticky_parent_attaches_read_only(self):
        """Regression: a service under a root-owned sticky /tmp is reused, not re-spawned."""
        shared = Path(tempfile.mkdtemp(prefix="hey-my-buddy-shared-", dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp")))
        self.addCleanup(lambda: shutil.rmtree(shared, ignore_errors=True))
        shared.chmod(0o777 | stat.S_ISVTX)
        directory = shared / "state"
        directory.mkdir(mode=0o700)
        endpoint_file = directory / "control.json"
        endpoint_file.write_text(json.dumps(ENDPOINT), encoding="utf-8", newline="\n")
        endpoint_file.chmod(0o600)
        with patch("hey_my_buddy.protocol.transport._request", return_value={"status": "ready"}) as request:
            self.assertEqual(ensure_service(directory), ENDPOINT)
        request.assert_called_once()
        self.spawn.assert_not_called()

    def test_cold_start_fails_honestly_when_the_state_directory_cannot_be_written(self):
        # A path that already exists as a file cannot become the state directory.
        blocked = Path(self.temp.name) / "state"
        blocked.write_text("not a directory", encoding="utf-8", newline="\n")
        with self.assertRaises(BoardError) as refused:
            ensure_service(blocked)
        self.assertEqual(refused.exception.code, 'PRIVATE_PATH_UNSAFE')
        self.assertEqual(refused.exception.message, 'IPC path is not a directory')
        self.assertEqual(refused.exception.details['path'], str(blocked.resolve()))
        self.spawn.assert_not_called()
        self.assertEqual(blocked.read_text(encoding="utf-8"), "not a directory")

    def test_cold_start_reports_a_daemon_that_exits_immediately(self):
        self.spawn.return_value.poll.return_value = 1
        with patch("hey_my_buddy.protocol.transport._healthy", return_value=None):
            with self.assertRaises(ServiceError) as failure:
                ensure_service(self.directory)
        self.assertEqual(failure.exception.code, "SERVICE_START_FAILED")
        self.assertEqual(self.spawn.call_count, 1)

    def test_cold_start_uses_the_shared_budget_and_preserves_first_install_wait(self):
        for installed, clock in ((False, [100, 100.25, 100.5]), (True, [100, 399.75, 400])):
            with self.subTest(installed=installed), \
                 patch.object(transport.rpc_config, "SERVICE_STARTUP_TIMEOUT_SECONDS", 0.5), \
                 patch("hey_my_buddy.install.runtime.launch_target", return_value={**LAUNCH_TARGET, "installed": installed}), \
                 patch.object(transport, "_healthy", return_value=None), \
                 patch.object(transport.time, "monotonic", side_effect=clock), \
                 patch.object(transport.time, "sleep") as sleep:
                with self.assertRaises(ServiceError) as failure:
                    ensure_service(self.directory)
                self.assertEqual(failure.exception.code, "SERVICE_START_TIMEOUT")
                sleep.assert_called_once_with(0.05)

    def test_native_startup_timeout_reaches_the_launcher_through_the_daemon_record(self):
        import io
        from c_two._native import CoreError
        from hey_my_buddy.blackboard.service import daemon

        error = CoreError("fixture native lifecycle failure")
        error.category = "lifecycle"
        error.lifecycle_kind = "startup_timeout"
        child = self.spawn.return_value
        child.pid = os.getpid()

        def fail_startup(*_args, **_kwargs):
            with patch.dict(os.environ, {"HMB_STATE_DIR": str(self.directory)}), \
                 patch.object(daemon.Daemon, "run", side_effect=error), \
                 patch.object(daemon.signal, "signal"), \
                 patch("sys.stderr", new_callable=io.StringIO):
                child.poll.return_value = daemon.main()
            return child

        self.spawn.side_effect = fail_startup
        with patch.object(transport, "_healthy", return_value=None):
            with self.assertRaises(ServiceError) as failure:
                ensure_service(self.directory)
        self.assertEqual(failure.exception.code, "SERVICE_START_TIMEOUT")
        self.assertEqual(failure.exception.message, str(error))

    def test_startup_error_from_another_pid_is_not_attributed_to_the_new_daemon(self):
        self.directory.mkdir()
        from hey_my_buddy.blackboard.service.daemon import atomic_json
        atomic_json(self.directory / "startup-error.json", {
            "pid": 4241, "code": "SERVICE_START_TIMEOUT", "message": "earlier daemon timeout",
        })
        self.spawn.return_value.pid = 4242
        self.spawn.return_value.poll.return_value = 2
        with patch.object(transport, "_healthy", return_value=None):
            with self.assertRaises(ServiceError) as failure:
                ensure_service(self.directory)
        self.assertEqual(failure.exception.code, "SERVICE_START_FAILED")

    @unittest.skipIf(os.name == "nt", "POSIX owner, mode and ancestor policy")
    def test_cold_start_and_later_attach_share_the_same_trust_rule_under_a_shared_parent(self):
        """A /tmp-style state dir is trusted by cold start and by every later attach."""
        shared = Path(tempfile.mkdtemp(prefix="hey-my-buddy-shared-", dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp")))
        self.addCleanup(lambda: shutil.rmtree(shared, ignore_errors=True))
        shared.chmod(0o777 | stat.S_ISVTX)
        directory = shared / "state"
        directory.mkdir(mode=0o700)
        (directory / "ipc").mkdir(mode=0o700)
        endpoint_file = directory / "control.json"
        endpoint_file.write_text(json.dumps(ENDPOINT), encoding="utf-8", newline="\n")
        endpoint_file.chmod(0o600)
        self.assertEqual(_read_endpoint(directory), ENDPOINT, "cold start must trust the endpoint it just wrote")
        with patch("hey_my_buddy.protocol.transport._request", return_value={"status": "ready"}) as request:
            self.assertEqual(_attach_read_only(directory), ENDPOINT, "later attach must apply the same rule, with no cold-start exception")
        request.assert_called_once()


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(os.name == "nt", "POSIX IPC directories; Windows uses named pipes")
class RpcReadOnlySetupTests(unittest.TestCase):
    """The real RPC setup boundary must preserve missing or unsafe IPC paths."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rpc-readonly-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.endpoint_file = self.directory / "control.json"
        self.endpoint_file.write_text(json.dumps(ENDPOINT), encoding="utf-8", newline="\n")
        self.endpoint_file.chmod(0o600)

    def test_missing_ipc_does_not_create_on_transport_or_board_client_attach(self):
        from hey_my_buddy.protocol.client import BoardClient
        with patch("hey_my_buddy.protocol.transport.cc.connect") as connect:
            with MutationRecorder() as recorder:
                self.assertIsNone(_attach_read_only(self.directory))
                with self.assertRaises(ServiceError):
                    BoardClient(self.directory, autostart=False).ping()
            recorder.assert_no_mutation()
            connect.assert_not_called()
        self.assertFalse((self.directory / "ipc").exists())

    def test_read_only_state_with_private_ipc_reaches_rpc_without_mutation(self):
        from hey_my_buddy.protocol.client import BoardClient
        from support import shutdown_private_rpc

        ipc = self.directory / "ipc"
        ipc.mkdir(mode=0o700)
        before = snapshot(self.directory)
        self.directory.chmod(0o500)
        self.addCleanup(self.directory.chmod, 0o700)
        self.addCleanup(shutdown_private_rpc)
        self.assertFalse(os.access(self.directory, os.W_OK))
        proxy = Mock()
        proxy.__enter__ = Mock(return_value=proxy)
        proxy.__exit__ = Mock(return_value=False)
        proxy.ping.return_value = '{"status": "ok"}'
        # Exercise the real endpoint validation and configure_client(create=False).
        # Only the native connection is replaced; no socket bind or connect runs.
        with patch("hey_my_buddy.protocol.transport.cc.connect", return_value=proxy) as connect:
            with MutationRecorder() as recorder:
                self.assertEqual(BoardClient(self.directory, autostart=False).ping(), {"status": "ok"})
            recorder.assert_no_mutation()
        self.assertEqual(connect.call_count, 2, "health attachment and requested ping must both reach RPC")
        self.assertEqual(before, snapshot(self.directory))
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o500)
        self.assertEqual(stat.S_IMODE(ipc.stat().st_mode), 0o700)

    def test_error_reply_details_reach_the_client_unchanged(self):
        # A direct WORKSPACE_GIT_ERROR reply keeps its recorded command facts:
        # the client envelope carries them instead of only the message.
        (self.directory / "ipc").mkdir(mode=0o700)
        proxy = Mock()
        proxy.__enter__ = Mock(return_value=proxy)
        proxy.__exit__ = Mock(return_value=False)
        proxy.ping.return_value = json.dumps({"error": {
            "code": "WORKSPACE_GIT_ERROR", "message": "Git workspace operation failed",
            "details": {"operation": "read-tree", "returncode": 128, "argv": ["git", "read-tree"],
                        "reason": "fatal: cannot lock ref 'refs/hey-my-buddy/workspaces/x/input'"}}})
        with patch("hey_my_buddy.protocol.transport.cc.connect", return_value=proxy):
            with self.assertRaises(ServiceError) as raised:
                _request(ENDPOINT, "ping", {}, state_dir=self.directory)
        self.assertEqual(raised.exception.code, "WORKSPACE_GIT_ERROR")
        self.assertEqual(raised.exception.message, "Git workspace operation failed")
        self.assertEqual(raised.exception.details, {"operation": "read-tree", "returncode": 128,
                                                    "argv": ["git", "read-tree"],
                                                    "reason": "fatal: cannot lock ref 'refs/hey-my-buddy/workspaces/x/input'"})

    def test_missing_state_does_not_create_on_board_client_attach(self):
        from hey_my_buddy.protocol.client import BoardClient
        missing = self.directory / "missing"
        with MutationRecorder() as recorder:
            with self.assertRaises(ServiceError):
                BoardClient(missing, autostart=False).ping()
        recorder.assert_no_mutation()
        self.assertFalse(missing.exists())

    def native_ping_peer(self):
        """Reuse the protocol probe's native process, profile and stop handshake."""
        import c_two as cc
        from c_two import crm
        from protocol import test_rpc_config as probe_fixture
        from support import shutdown_private_rpc
        from hey_my_buddy.protocol import transport

        @crm(namespace="buddy.probe", version="1.0.0")
        class ProbeContract:
            def echo(self, payload: str) -> str: ...
            def ping(self, payload: str) -> str: ...
            def sleep_ms(self, payload: str) -> str: ...

        received = self.directory / "received.jsonl"
        received.write_text("", encoding="utf-8", newline="\n")
        script = probe_fixture.PROBE_SERVER.replace(
            "    def sleep_ms(self, payload: str) -> str:",
            "    def ping(self, payload: str) -> str:\n"
            "        return self.echo(payload)\n"
            "    def sleep_ms(self, payload: str) -> str:",
        ).replace(
            "        return payload",
            "        with (state / 'received.jsonl').open('a', encoding='utf-8', newline='\\n') as stream:\n"
            "            stream.write(payload + '\\n')\n"
            "        return payload",
        )
        self.addCleanup(shutdown_private_rpc)
        # Register ownership before waiting for readiness: a failed startup must
        # still collect this exact child, without discovering or stopping a peer.
        peer = probe_fixture.ProbeServer.__new__(probe_fixture.ProbeServer)
        self.peer_receipt = {"pid": None, "waitExit": None, "stopRequested": False}

        def stop():
            if hasattr(peer, "process"):
                self.peer_receipt["pid"] = peer.pid
                self.peer_receipt["stopRequested"] = True
                peer.close()
                self.peer_receipt["waitExit"] = peer.process.returncode
                if hasattr(peer, "address"):
                    self.assertEqual(peer.process.returncode, 0, "the native probe must confirm shutdown")
            elif hasattr(peer, "log"):
                peer.log.close()

        self.addCleanup(stop)
        with patch.object(probe_fixture, "PROBE_SERVER", script):
            probe_fixture.ProbeServer.__init__(peer, self.directory)
        self.assertEqual(Path(json.loads(peer.endpoint_path.read_text(encoding="utf-8"))["root"]), self.directory / "ipc")
        self.assertEqual(cc.__version__, "0.8.1")
        self.enterContext(patch.object(transport, "BuddyControl", ProbeContract))
        self.enterContext(patch.object(transport, "CONTROL_NAME", "hey-my-buddy-probe"))
        endpoint = {**ENDPOINT, "address": peer.address, "pid": peer.pid}
        self.endpoint_file.write_text(json.dumps(endpoint), encoding="utf-8", newline="\n")
        return endpoint, received

    def test_group_readable_ipc_reaches_native_rpc_without_chmod(self):
        endpoint, received = self.native_ping_peer()
        ipc = self.directory / "ipc"
        ipc.chmod(0o750)
        with MutationRecorder() as recorder:
            self.assertEqual(_attach_read_only(self.directory), endpoint,
                             "0750 must reach the actual SDK peer")
        recorder.assert_no_mutation()
        self.assertEqual([json.loads(line) for line in received.read_text(encoding="utf-8").splitlines()],
                         [{"token": ENDPOINT["token"]}])
        self.assertEqual(stat.S_IMODE(ipc.stat().st_mode), 0o750)

    def test_unsafe_ipc_is_not_chmodded_or_connected(self):
        """Unsafe means group/world writable; the SDK refuses before the peer receives RPC."""
        from support import shutdown_private_rpc
        from hey_my_buddy.protocol.transport import _request

        endpoint, received = self.native_ping_peer()
        ipc = self.directory / "ipc"
        for mode in (0o770, 0o777):
            with self.subTest(mode=oct(mode)):
                shutdown_private_rpc()
                ipc.chmod(mode)
                with MutationRecorder() as recorder:
                    with self.assertRaises(ServiceError) as refused:
                        _request(endpoint, "ping", {}, state_dir=self.directory)
                    self.assertIsNone(_attach_read_only(self.directory))
                recorder.assert_no_mutation()
                self.assertEqual(refused.exception.code, "SERVICE_UNAVAILABLE")
                self.assertRegex(str(refused.exception.__cause__).lower(),
                                 r"(group|world|other).*(writ|permission)|(writ|permission).*(group|world|other)",
                                 "the SDK must reject writable access, not an arbitrary transport error")
                self.assertEqual(received.read_text(encoding="utf-8"), "", "the unsafe SDK client must never reach the peer")
                self.assertEqual(stat.S_IMODE(ipc.stat().st_mode), mode)

    def test_explicit_state_reaches_rpc_setup(self):
        from hey_my_buddy.protocol.client import BoardClient
        from hey_my_buddy.protocol import transport
        proxy = Mock()
        proxy.__enter__ = Mock(return_value=proxy)
        proxy.__exit__ = Mock(return_value=False)
        proxy.ping.return_value = '{"status": "ok"}'
        with patch.object(transport.rpc_config, "configure_client") as configure, patch.object(transport.cc, "connect", return_value=proxy), patch.object(transport, "_attach_read_only", return_value=ENDPOINT):
            self.assertEqual(BoardClient(self.directory, autostart=False).ping(), {"status": "ok"})
            configure.assert_called_once_with(self.directory, create=False)
