"""Matching client selection and preserved operator settings, all private."""
import os
from pathlib import Path
from unittest import mock
from buddy import launcher, upgrade
from buddy.errors import BoardError
from support import BoardTestCase

class LauncherSelectionTests(BoardTestCase):
    def test_current_pointer_selects_previous_client_and_preserves_scoped_credentials(self):
        root=(self.directory/'runtime').resolve();target=root/('a'*32);target.mkdir(parents=True)
        launcher.write_active_runtime(self.directory,target,{'BUDDY_MAX_CONCURRENT':'7','BUDDY_WAIT_CAPACITY':'32'})
        clean={k:v for k,v in os.environ.items() if k not in ('BUDDY_RUNTIME','BUDDY_DEV_SOURCE')}
        with mock.patch.dict(os.environ,{**clean,'BUDDY_RUNTIME_ROOT':str(root),'BUDDY_AGENT_CREDENTIAL':'scoped-fixture'},clear=True), mock.patch('buddy.runtime.is_ready',return_value=True):
            self.assertEqual(launcher.selected_runtime(self.directory),target)
            self.assertEqual(launcher.runtime_environment(target)['BUDDY_AGENT_CREDENTIAL'],'scoped-fixture')
        self.assertEqual(launcher.launch_defaults(self.directory)['BUDDY_MAX_CONCURRENT'],'7')
        self.assertEqual((self.directory/'active-runtime.json').stat().st_mode & 0o777,0o600)

    def test_upgrade_environment_preserves_capacity_over_caller_defaults(self):
        launcher.write_private(self.directory/'upgrade.json',{'environment':{'BUDDY_MAX_CONCURRENT':'7','BUDDY_WAIT_CAPACITY':'32'}})
        with mock.patch.dict(os.environ,{'BUDDY_MAX_CONCURRENT':'8','BUDDY_WAIT_CAPACITY':'64'}):
            values=upgrade._environment(self.directory,self.directory/'target')
        self.assertEqual(values['BUDDY_MAX_CONCURRENT'],'7')
        self.assertEqual(values['BUDDY_WAIT_CAPACITY'],'32')

    def test_pointer_outside_root_and_unknown_launch_settings_are_refused(self):
        root=(self.directory/'runtime').resolve();root.mkdir()
        launcher.write_active_runtime(self.directory,self.directory/'outside'/('b'*32))
        clean={k:v for k,v in os.environ.items() if k not in ('BUDDY_RUNTIME','BUDDY_DEV_SOURCE')}
        with mock.patch.dict(os.environ,{**clean,'BUDDY_RUNTIME_ROOT':str(root)},clear=True), mock.patch('buddy.runtime.is_ready',return_value=True):
            with self.assertRaises(BoardError):launcher.selected_runtime(self.directory)
        launcher.write_private(self.directory/'launch-settings.json',{'environment':{'ARBITRARY_ENV':'1'}})
        with self.assertRaises(BoardError):launcher.launch_defaults(self.directory)
