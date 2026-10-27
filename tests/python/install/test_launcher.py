"""The installed entry selects its active interpreter and preserves Worker authority."""
import os
from pathlib import Path
import json
import shutil
import sysconfig
import venv
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.install import launcher, runtime
from hey_my_buddy.errors import BoardError
from install.test_packaging import LAUNCHER, current_environment, launcher_argv


class LauncherTests(unittest.TestCase):
    def test_installed_launcher_uses_its_stable_hint_before_system_python(self):
        from hey_my_buddy.install.skill_install import write_runtime_hint
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = root / 'hey-my-buddy'
            shutil.copytree(LAUNCHER.parent, skill / 'scripts')
            package = skill / 'package'
            package.mkdir()
            (package / 'pyproject.toml').write_text('[project]\nname="fixture"\n', encoding="utf-8", newline="\n")
            entry = package / 'src/hey_my_buddy/install/launcher.py'
            entry.parent.mkdir(parents=True)
            entry.write_text("import json, sys\nprint(json.dumps({'used': 'stable-hint', 'python': sys.executable, 'args': sys.argv[1:]}))\nraise SystemExit(int(sys.argv[-1]))\n", encoding="utf-8", newline="\n")
            target = root / 'runtime' / ('f' * 32)
            venv.EnvBuilder(with_pip=False, symlinks=os.name != 'nt').create(target / 'venv')
            python = runtime.runtime_python(target)
            write_runtime_hint(skill, target)
            environment = current_environment(HOME=str(root), USERPROFILE=str(root), HMB_STATE_DIR=str(root / 'state'),
                                              HMB_RUNTIME_ROOT=str(root / 'runtime'), UV_BIN=str(root / 'missing-uv'))
            environment.pop('HMB_BOOTSTRAP_PYTHON', None)
            for status in (0, 37):
                with self.subTest(exit_code=status):
                    result = subprocess.run(launcher_argv('ping', str(status), launcher=skill / 'scripts/hey-my-buddy'),
                                            env=environment, capture_output=True, text=True, encoding="utf-8", timeout=240)
                    self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                    self.assertEqual(json.loads(result.stdout), {'used': 'stable-hint', 'python': str(python),
                                                                 'args': ['ping', str(status)]})

    def test_native_daily_entry_uses_active_python_with_uv_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = base / 'state'
            state.mkdir(mode=0o700)
            root = base / 'runtime'
            target = root / ('a' * 32)
            venv.EnvBuilder(with_pip=False, symlinks=os.name != 'nt').create(target / 'venv')
            python = runtime.runtime_python(target)
            entry = target / 'src/hey_my_buddy/cli/main.py'
            entry.parent.mkdir(parents=True)
            entry.write_text("import json, sys\nprint(json.dumps({'used': 'active', 'python': sys.executable, 'args': sys.argv[1:]}))\nraise SystemExit(int(sys.argv[-1]))\n", encoding="utf-8", newline="\n")
            site = Path(sysconfig.get_path('purelib', vars={'base': str(target / 'venv'), 'platbase': str(target / 'venv')}))
            (site / 'fixture.pth').write_text(str(target / 'src') + '\n', encoding="utf-8", newline="\n")
            (target / 'READY.json').write_text(json.dumps({'format': 1, 'state': 'READY', 'contentId': target.name, 'python': str(python)}), encoding="utf-8", newline="\n")
            launcher.write_active_runtime(state, target)
            environment = current_environment(HMB_STATE_DIR=str(state), HMB_RUNTIME_ROOT=str(root),
                                              HMB_BOOTSTRAP_PYTHON=sys.executable, UV_BIN=str(base / 'missing-uv'))
            for status in (0, 37):
                with self.subTest(exit_code=status):
                    completed = subprocess.run(launcher_argv('ping', str(status)), env=environment,
                                               capture_output=True, text=True, check=False, encoding="utf-8", timeout=240)
                    self.assertEqual(completed.returncode, status, completed.stdout + completed.stderr)
                    self.assertEqual(json.loads(completed.stdout), {'used': 'active', 'python': str(python),
                                                                   'args': ['ping', str(status)]})

    def test_daily_call_execs_active_python_without_uv(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            target = state / 'runtime' / ('a' * 32)
            python = runtime.runtime_python(target)
            (target / 'src' / 'hey_my_buddy').mkdir(parents=True)
            with mock.patch.dict(os.environ, {'HMB_STATE_DIR': str(state)}, clear=True), \
                    mock.patch('hey_my_buddy.install.launcher.selected_runtime', return_value=target), \
                    mock.patch('hey_my_buddy.install.launcher._exec_runtime', side_effect=SystemExit(0)) as execute:
                with self.assertRaises(SystemExit):
                    launcher.main(['ping'])
            self.assertEqual(execute.call_args.args[0], str(python))
            self.assertEqual(execute.call_args.args[1], [str(python), '-P', '-m', 'hey_my_buddy.cli.main', 'ping'])

    def test_attempt_file_alone_selects_its_board_before_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / 'board'
            state.mkdir(mode=0o700)
            credential = root / 'agent-credential.json'
            credential.write_text(json.dumps({'token': 'fixture-secret', 'stateDir': str(state)}), encoding="utf-8", newline="\n")
            credential.chmod(0o600)
            target = root / 'runtime' / ('a' * 32)
            (target / 'src' / 'hey_my_buddy').mkdir(parents=True)
            transferred = {}
            def capture_exec(_python, _arguments, environment):
                transferred.update(json.loads(os.read(int(environment['HMB_AGENT_CREDENTIAL_FD']), 65537)))
                self.assertNotIn('fixture-secret', str(environment))
                raise SystemExit(0)
            original_read = launcher.read_private
            with mock.patch.dict(os.environ, {'HMB_AGENT_CREDENTIAL_FILE': str(credential)}, clear=True), \
                    mock.patch.object(launcher, 'read_private', wraps=original_read) as read, \
                    mock.patch('hey_my_buddy.install.launcher.selected_runtime', return_value=target) as selected, \
                    mock.patch('hey_my_buddy.install.launcher._exec_runtime', side_effect=capture_exec) as execute:
                with self.assertRaises(SystemExit):
                    launcher.main(['get', '{"runId":"owned"}'])
            selected.assert_called_once_with(state)
            read.assert_called_once_with(credential)
            self.assertEqual(transferred, {'token': 'fixture-secret', 'stateDir': str(state)})
            self.assertEqual(execute.call_args.args[2]['HMB_STATE_DIR'], str(state))
            self.assertEqual(execute.call_args.args[2]['HMB_AGENT_CREDENTIAL_FILE'], str(credential))

    def test_source_launcher_passes_one_read_to_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / 'board'
            state.mkdir(mode=0o700)
            credential = root / 'agent-credential.json'
            document = {'token': 'fixture-secret', 'stateDir': str(state)}
            credential.write_text(json.dumps(document), encoding="utf-8", newline="\n")
            credential.chmod(0o600)
            original_read = launcher.read_private
            with mock.patch.dict(os.environ, {'HMB_AGENT_CREDENTIAL_FILE': str(credential)}, clear=True), \
                    mock.patch.object(launcher, 'read_private', wraps=original_read) as read, \
                    mock.patch.object(launcher, 'selected_runtime', return_value=None), \
                    mock.patch('hey_my_buddy.cli.main.main', return_value=0) as cli_main:
                self.assertEqual(launcher.main(['get', '{"runId":"owned"}']), 0)
            read.assert_called_once_with(credential)
            cli_main.assert_called_once_with(['get', '{"runId":"owned"}'], credential_document=document)

    def test_host_scrubs_internal_identity_and_endpoints_but_attempt_keeps_credential(self):
        inherited = {'HMB_STATE_DIR': '/private/state', 'HMB_RUNTIME_ROOT': '/private/runtime',
                     'HMB_DEV_SOURCE': '1', 'HMB_RUNTIME': '/stale', 'HMB_RUNTIME_IDENTITY': 'stale',
                     'HMB_PYTHON': '/stale/python', 'ANTHROPIC_BASE_URL': 'https://invalid.example',
                     'OPENAI_BASE_URL': 'https://invalid.example'}
        with mock.patch.dict(os.environ, inherited, clear=True):
            launcher.clean_host_environment()
            self.assertTrue(launcher.HOST_INTERNAL_KEYS.isdisjoint(os.environ))
            self.assertTrue(launcher.HOST_ENDPOINT_KEYS.isdisjoint(os.environ))
            self.assertEqual(os.environ['HMB_STATE_DIR'], '/private/state')
            self.assertEqual(os.environ['HMB_RUNTIME_ROOT'], '/private/runtime')
            self.assertEqual(os.environ['HMB_DEV_SOURCE'], '1')
        with mock.patch.dict(os.environ, {**inherited, 'HMB_AGENT_CREDENTIAL_FILE': '/private/attempt'}, clear=True):
            launcher.clean_host_environment()
            self.assertEqual(os.environ['HMB_AGENT_CREDENTIAL_FILE'], '/private/attempt')

    def test_missing_installed_pointer_has_repairable_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            launcher.write_private(state / 'control.json', {'runtimeIdentity': 'runtime:' + 'a' * 32})
            for project, code in (('package', 'ACTIVE_RUNTIME_MISSING'), ('source', 'DEV_SOURCE_REQUIRED')):
                with self.subTest(project=project), \
                     mock.patch.dict(os.environ, {'HMB_RUNTIME_ROOT': str(state / 'runtime')}, clear=True), \
                     mock.patch.object(runtime, 'project_root', return_value=state / project):
                    with self.assertRaises(BoardError) as caught:
                        launcher.selected_runtime(state)
                self.assertEqual(caught.exception.code, code)


if __name__ == '__main__':
    unittest.main()
