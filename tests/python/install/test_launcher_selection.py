"""Matching client selection and preserved operator settings, all private."""
import os
from unittest import mock
from hey_my_buddy.install import launcher, upgrade
from hey_my_buddy.errors import BoardError
from support import BoardTestCase

class LauncherSelectionTests(BoardTestCase):
    def test_current_pointer_selects_previous_client_and_preserves_scoped_credentials(self):
        root=(self.directory/'runtime').resolve();target=root/('a'*32);target.mkdir(parents=True)
        launcher.write_active_runtime(self.directory,target,{'HMB_MAX_CONCURRENT':'7','HMB_WAIT_CAPACITY':'32'})
        clean={k:v for k,v in os.environ.items() if k not in ('HMB_RUNTIME','HMB_DEV_SOURCE')}
        with mock.patch.dict(os.environ,{**clean,'HMB_RUNTIME_ROOT':str(root),'HMB_AGENT_CREDENTIAL':'scoped-fixture'},clear=True), mock.patch('hey_my_buddy.install.runtime.is_ready',return_value=True):
            self.assertEqual(launcher.selected_runtime(self.directory),target)
            self.assertEqual(launcher.runtime_environment(target)['HMB_AGENT_CREDENTIAL'],'scoped-fixture')
        self.assertEqual(launcher.launch_defaults(self.directory)['HMB_MAX_CONCURRENT'],'7')
        self.assertEqual((self.directory/'active-runtime.json').stat().st_mode & 0o777,0o600)

    def test_upgrade_environment_preserves_capacity_over_caller_defaults(self):
        launcher.write_private(self.directory/'upgrade.json',{'environment':{'HMB_MAX_CONCURRENT':'7','HMB_WAIT_CAPACITY':'32'}})
        with mock.patch.dict(os.environ,{'HMB_MAX_CONCURRENT':'8','HMB_WAIT_CAPACITY':'64'}):
            with mock.patch.object(upgrade.runtime, 'read_ready', return_value={'sourceCommit': 'f' * 40}):
                values=upgrade._environment(self.directory,self.directory/'target')
        self.assertEqual(values['HMB_MAX_CONCURRENT'],'7')
        self.assertEqual(values['HMB_WAIT_CAPACITY'],'32')

    def test_pointer_outside_root_and_unknown_launch_settings_are_refused(self):
        root=(self.directory/'runtime').resolve();root.mkdir()
        launcher.write_active_runtime(self.directory,self.directory/'outside'/('b'*32))
        clean={k:v for k,v in os.environ.items() if k not in ('HMB_RUNTIME','HMB_DEV_SOURCE')}
        with mock.patch.dict(os.environ,{**clean,'HMB_RUNTIME_ROOT':str(root)},clear=True), mock.patch('hey_my_buddy.install.runtime.is_ready',return_value=True):
            with self.assertRaises(BoardError):launcher.selected_runtime(self.directory)
        launcher.write_private(self.directory/'launch-settings.json',{'environment':{'ARBITRARY_ENV':'1'}})
        with self.assertRaises(BoardError):launcher.launch_defaults(self.directory)


class TargetEntryModuleTests(BoardTestCase):
    def test_entry_modules_require_the_current_layout(self):
        target = self.directory / 'runtime' / ('b' * 32)
        (target / 'src' / 'hey_my_buddy').mkdir(parents=True)
        self.assertEqual(launcher.entry_modules(target),
                         {'client': 'hey_my_buddy.protocol.client',
                          'daemon': 'hey_my_buddy.blackboard.service.daemon',
                          'cli': 'hey_my_buddy.cli.main'})
        old = self.directory / 'runtime' / ('a' * 32)
        (old / 'src' / 'buddy').mkdir(parents=True)
        with self.assertRaises(BoardError) as caught:
            launcher.entry_modules(old)
        self.assertEqual(caught.exception.code, 'RUNTIME_LAYOUT_UNKNOWN')

    def test_the_launcher_execs_the_current_cli_module(self):
        target = self.directory / 'runtime' / ('f' * 32)
        (target / 'src' / 'hey_my_buddy').mkdir(parents=True)
        with mock.patch.dict(os.environ, {'HMB_STATE_DIR': str(self.directory)}, clear=True), \
                mock.patch('hey_my_buddy.install.launcher.selected_runtime', return_value=target), \
                mock.patch('hey_my_buddy.install.launcher.os.execve', side_effect=SystemExit(0)) as execute:
            with self.assertRaises(SystemExit):
                launcher.main(['ping'])
        self.assertEqual(execute.call_args.args[1],
                         [str(launcher._runtime_python(target)), '-P', '-m', 'hey_my_buddy.cli.main', 'ping'])
