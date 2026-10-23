"""Owner setup and read-only attachment share one canonical state boundary."""
import io
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy import state_root
from hey_my_buddy.blackboard.service.daemon import Daemon
from hey_my_buddy.blackboard.service import daemon
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import runtime, skill_install
from hey_my_buddy.protocol import rpc_config, transport
from support import BoardTestCase


class ReachedBoundary(RuntimeError):
    pass


class StateRootTests(BoardTestCase):
    def test_windows_root_normalizes_dotdot_without_resolving_links(self):
        target = self.directory / 'actual'
        target.mkdir()
        alias = self.directory / 'alias'
        alias.symlink_to(target, target_is_directory=True)
        windows = SimpleNamespace(name='nt', path=os.path, environ=os.environ)
        with mock.patch.object(state_root, 'os', windows):
            self.assertEqual(state_root.get_state_dir(alias / 'missing' / '..'), alias)

    def test_main_reports_an_invalid_root_without_trying_to_write_a_startup_record(self):
        root = self.directory / 'not-a-directory'
        root.write_bytes(b'retain root entry')
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {'HMB_STATE_DIR': str(root)}), \
                mock.patch.object(daemon.sys, 'stderr', stderr), \
                mock.patch.object(daemon, 'Daemon') as construct:
            self.assertEqual(daemon.main(), 2)
        construct.assert_not_called()
        self.assertIn('PRIVATE_PATH_UNSAFE', stderr.getvalue())
        self.assertEqual(root.read_bytes(), b'retain root entry')

    @unittest.skipIf(os.name == 'nt', 'POSIX owner and mode policy')
    def test_read_only_check_does_not_repair_and_owner_repairs_once(self):
        root = self.directory / 'state'
        root.mkdir(mode=0o755)
        root.chmod(0o755)
        with self.assertRaises(BoardError) as refused:
            state_root.check_state_root(root)
        self.assertEqual(refused.exception.code, 'PRIVATE_PATH_UNSAFE')
        self.assertEqual(root.stat().st_mode & 0o777, 0o755)
        with mock.patch.object(Path, 'chmod', autospec=True, side_effect=lambda path, mode: os.chmod(path, mode)) as chmod:
            self.assertEqual(state_root.check_state_root(root, create=True), root)
            self.assertEqual(state_root.check_state_root(root), root)
            self.assertEqual(state_root.check_state_root(root, create=True), root)
        chmod.assert_called_once_with(root, 0o700)
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def owner_entry(self, name, selected):
        reached = ReachedBoundary(name)
        if name == 'daemon':
            with mock.patch.object(rpc_config, 'configure_server', side_effect=reached):
                Daemon(selected).run()
        elif name == 'cold':
            with mock.patch.object(rpc_config, 'configure_local_endpoint', side_effect=reached):
                transport._cold_start_preflight(selected)
        else:
            with mock.patch.dict(os.environ, {'HMB_STATE_DIR': str(selected)}), \
                    mock.patch.object(runtime, 'runtime_root', side_effect=reached):
                skill_install.install({})

    @unittest.skipIf(os.name == 'nt', 'POSIX resolved ancestors and owner repair')
    def test_every_owner_resolves_links_and_repairs_its_real_root(self):
        for entry in ('cold', 'daemon', 'install'):
            with self.subTest(entry=entry):
                root = self.directory / (entry + '-state')
                root.mkdir(mode=0o755)
                root.chmod(0o755)
                alias = self.directory / (entry + '-alias')
                alias.symlink_to(root, target_is_directory=True)
                with self.assertRaises(ReachedBoundary):
                    self.owner_entry(entry, alias)
                self.assertEqual(root.stat().st_mode & 0o777, 0o700)
                self.assertEqual(state_root.get_state_dir(alias), root)

    @unittest.skipIf(os.name == 'nt', 'POSIX untrusted real ancestor')
    def test_every_owner_rejects_links_into_a_shared_real_parent_without_chmod(self):
        shared = self.directory / 'shared'
        shared.mkdir()
        shared.chmod(0o777)
        alias = self.directory / 'alias'
        alias.symlink_to(shared, target_is_directory=True)
        for entry in ('cold', 'daemon', 'install'):
            with self.subTest(entry=entry):
                root = shared / (entry + '-state')
                root.mkdir(mode=0o755)
                root.chmod(0o755)
                with self.assertRaises(BoardError) as refused:
                    self.owner_entry(entry, alias / root.name)
                self.assertEqual(refused.exception.code, 'PRIVATE_PATH_UNSAFE')
                self.assertEqual(root.stat().st_mode & 0o777, 0o755)

    @unittest.skipIf(os.name == 'nt', 'POSIX existing ancestor trust')
    def test_every_owner_rejects_a_missing_root_under_a_shared_parent_without_creation(self):
        shared = self.directory / 'shared-new'
        shared.mkdir()
        shared.chmod(0o777)
        for entry in ('cold', 'daemon', 'install'):
            root = shared / entry / 'state'
            with self.subTest(entry=entry), self.assertRaises(BoardError) as refused:
                self.owner_entry(entry, root)
            self.assertEqual(refused.exception.code, 'PRIVATE_PATH_UNSAFE')
        self.assertEqual(list(shared.iterdir()), [])

    def test_looping_root_is_a_private_path_error_before_daemon_creation(self):
        root = self.directory / 'loop'
        root.symlink_to(root, target_is_directory=True)
        with self.assertRaises(BoardError) as refused:
            state_root.check_state_root(root, create=True)
        self.assertEqual(refused.exception.code, 'PRIVATE_PATH_UNSAFE')
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {'HMB_STATE_DIR': str(root)}), \
                mock.patch.object(daemon.sys, 'stderr', stderr), \
                mock.patch.object(daemon, 'Daemon') as construct:
            self.assertEqual(daemon.main(), 2)
        construct.assert_not_called()
        self.assertIn('PRIVATE_PATH_UNSAFE', stderr.getvalue())

    @unittest.skipIf(os.name == 'nt', 'POSIX account ownership')
    def test_every_owner_rejects_a_foreign_root_without_chmod(self):
        original = os.stat
        for entry in ('cold', 'daemon', 'install'):
            with self.subTest(entry=entry):
                root = self.directory / (entry + '-foreign')
                root.mkdir(mode=0o755)
                root.chmod(0o755)
                def foreign(path, *args, **kwargs):
                    result = original(path, *args, **kwargs)
                    if Path(path) == root:
                        values = list(result[:10])
                        values[4] = os.getuid() + 1
                        return os.stat_result(values)
                    return result
                with mock.patch.object(state_root.os, 'stat', side_effect=foreign), \
                        mock.patch.object(Path, 'chmod', autospec=True) as chmod:
                    with self.assertRaises(BoardError) as refused:
                        self.owner_entry(entry, root)
                self.assertEqual(refused.exception.code, 'PRIVATE_PATH_UNSAFE')
                chmod.assert_not_called()
