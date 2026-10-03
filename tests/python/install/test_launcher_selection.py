"""Matching client selection and preserved operator settings, all private."""
import os
from pathlib import Path
from unittest import mock
from hey_my_buddy.install import launcher, upgrade
from hey_my_buddy.errors import BoardError
from support import BoardTestCase

class LauncherSelectionTests(BoardTestCase):
    def test_current_pointer_selects_previous_client_and_preserves_scoped_credentials(self):
        root=(self.directory/'runtime').resolve();target=root/('a'*32);target.mkdir(parents=True)
        launcher.write_active_runtime(self.directory,target,{'BUDDY_MAX_CONCURRENT':'7','BUDDY_WAIT_CAPACITY':'32'})
        clean={k:v for k,v in os.environ.items() if k not in ('BUDDY_RUNTIME','BUDDY_DEV_SOURCE')}
        with mock.patch.dict(os.environ,{**clean,'BUDDY_RUNTIME_ROOT':str(root),'BUDDY_AGENT_CREDENTIAL':'scoped-fixture'},clear=True), mock.patch('hey_my_buddy.install.runtime.is_ready',return_value=True):
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
        with mock.patch.dict(os.environ,{**clean,'BUDDY_RUNTIME_ROOT':str(root)},clear=True), mock.patch('hey_my_buddy.install.runtime.is_ready',return_value=True):
            with self.assertRaises(BoardError):launcher.selected_runtime(self.directory)
        launcher.write_private(self.directory/'launch-settings.json',{'environment':{'ARBITRARY_ENV':'1'}})
        with self.assertRaises(BoardError):launcher.launch_defaults(self.directory)


class TargetEntryModuleTests(BoardTestCase):
    """A coordinator addresses a target runtime with that runtime's own modules.

    The persistent fixture here is a minimal old-generation package layout built
    with the standard library only: no Git history, no external tools, no extra
    interpreter and no network. The one-off reproduction against the real fixed
    old source (2bdb497) lives in the acceptance record's tmp material, not in
    the check suite.
    """

    def _minimal_old_layout_target(self) -> Path:
        """A private minimal old-generation package layout with explicit markers."""
        target = self.directory / 'runtime' / ('c' * 32)
        source = target / 'src' / 'buddy'
        source.mkdir(parents=True)
        (source / '__init__.py').write_text('"""Old-generation package layout fixture."""\n')
        (source / 'client.py').write_text('CLIENT_MARKER = "old-layout-fixture-client"\n')
        (source / 'daemon.py').write_text('DAEMON_MARKER = "old-layout-fixture-daemon"\n')
        (source / 'cli.py').write_text('CLI_MARKER = "old-layout-fixture-cli"\n')
        return target

    def test_entry_modules_follow_the_target_layout_not_the_coordinator(self):
        old = self.directory / 'runtime' / ('a' * 32)
        (old / 'src' / 'buddy').mkdir(parents=True)
        self.assertEqual(launcher.entry_modules(old),
                         {'client': 'buddy.client', 'daemon': 'buddy.daemon', 'cli': 'buddy.cli'})
        new = self.directory / 'runtime' / ('b' * 32)
        (new / 'src' / 'hey_my_buddy').mkdir(parents=True)
        self.assertEqual(launcher.entry_modules(new),
                         {'client': 'hey_my_buddy.protocol.client',
                          'daemon': 'hey_my_buddy.blackboard.service.daemon',
                          'cli': 'hey_my_buddy.cli.main'})
        unknown = self.directory / 'runtime' / ('d' * 32)
        unknown.mkdir()
        with self.assertRaises(BoardError) as caught:
            launcher.entry_modules(unknown)
        self.assertEqual(caught.exception.code, 'RUNTIME_LAYOUT_UNKNOWN')

    def test_derived_modules_resolve_in_an_old_layout_targets_own_namespace(self):
        import subprocess
        import sys
        target = self._minimal_old_layout_target()
        modules = launcher.entry_modules(target)
        # One isolated subprocess of the current interpreter: -S skips every
        # site-packages (including this checkout's venv), -I ignores inherited
        # python variables and the working directory, and the program inserts
        # only the fixture's src, so exactly the target's own namespace can
        # resolve. A new-layout name resolving here would be a masking defect.
        program = (
            'import importlib.util, sys\n'
            'sys.path.insert(0, sys.argv[1])\n'
            'client = importlib.import_module(sys.argv[2])\n'
            'print("client", client.CLIENT_MARKER)\n'
            'print("daemon", importlib.util.find_spec(sys.argv[3]) is not None)\n'
            'print("cli", importlib.util.find_spec(sys.argv[4]) is not None)\n'
            'try:\n'
            '    import hey_my_buddy\n'
            'except ModuleNotFoundError:\n'
            '    print("new-package", "absent")\n'
            'else:\n'
            '    print("new-package", "present")\n'
        )
        completed = subprocess.run(
            [sys.executable, '-I', '-S', '-c', program, str(target / 'src'),
             modules['client'], modules['daemon'], modules['cli']],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.splitlines(),
                         ['client old-layout-fixture-client', 'daemon True', 'cli True',
                          'new-package absent'])

    def test_the_coordinator_builds_target_layout_invocations(self):
        target = self.directory / 'runtime' / ('e' * 32)
        (target / 'src' / 'buddy').mkdir(parents=True)
        with mock.patch('hey_my_buddy.install.upgrade.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '{}'
            upgrade.command(self.directory, target, 'health')
        argv = run.call_args.args[0]
        self.assertEqual(argv[1], '-c')
        self.assertIn('from buddy.client import BoardClient', argv[2])
        self.assertNotIn('hey_my_buddy', argv[2])
        with mock.patch('hey_my_buddy.install.upgrade.subprocess.Popen') as popen:
            popen.return_value.poll.return_value = 0
            with self.assertRaises(BoardError):
                upgrade.start(self.directory, target)
        self.assertEqual(popen.call_args.args[0][1:3], ['-m', 'buddy.daemon'])

    def test_the_launcher_execs_an_old_targets_own_cli_module(self):
        target = self.directory / 'runtime' / ('f' * 32)
        (target / 'src' / 'buddy').mkdir(parents=True)
        with mock.patch.dict(os.environ, {'BUDDY_STATE_DIR': str(self.directory)}, clear=True), \
                mock.patch('hey_my_buddy.install.launcher.selected_runtime', return_value=target), \
                mock.patch('hey_my_buddy.install.launcher.os.execve', side_effect=SystemExit(0)) as execute:
            with self.assertRaises(SystemExit):
                launcher.main(['ping'])
        # The interpreter path follows the launcher's own platform rule; this
        # change owns only the exec'd -m module argument.
        self.assertEqual(execute.call_args.args[1],
                         [str(launcher._runtime_python(target)), '-P', '-m', 'buddy.cli', 'ping'])
