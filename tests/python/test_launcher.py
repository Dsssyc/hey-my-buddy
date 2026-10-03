"""The installed entry selects its active interpreter and preserves Worker authority."""
import os
from pathlib import Path
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.install import launcher, runtime
from hey_my_buddy.errors import BoardError


class LauncherTests(unittest.TestCase):
    def test_installed_launcher_uses_its_stable_hint_before_system_python(self):
        import shutil
        from hey_my_buddy.install.skill_install import write_runtime_hint
        source = Path(__file__).resolve().parents[2] / 'skills/buddy/scripts/buddy'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = root / 'buddy'
            (skill / 'scripts').mkdir(parents=True)
            (skill / 'package').mkdir()
            (skill / 'package/pyproject.toml').write_text('[project]\nname="fixture"\n')
            shutil.copy2(source, skill / 'scripts/buddy')
            target = root / 'runtime' / ('f' * 32)
            python = runtime.runtime_python(target)
            python.parent.mkdir(parents=True)
            python.write_text('#!/bin/sh\nprintf \'{"used":"stable-hint"}\\n\'\n')
            python.chmod(0o700)
            write_runtime_hint(skill, target)
            trap = root / 'bin'
            trap.mkdir()
            (trap / 'python3').write_text('#!/bin/sh\nexit 77\n')
            (trap / 'python3').chmod(0o700)
            env = {'PATH': str(trap) + ':/usr/bin:/bin', 'HOME': str(root),
                   'BUDDY_STATE_DIR': str(root / 'state'), 'BUDDY_RUNTIME_ROOT': str(root / 'runtime'), 'UV_BIN': str(root / 'missing-uv')}
            result = subprocess.run(['/bin/sh', str(skill / 'scripts/buddy'), 'ping'], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'used': 'stable-hint'})

    def test_shell_daily_entry_uses_active_python_with_uv_unavailable(self):
        script = Path(__file__).resolve().parents[2] / 'skills/buddy/scripts/buddy'
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = base / 'state'
            state.mkdir(mode=0o700)
            root = base / 'runtime'
            target = root / ('a' * 32)
            python = target / 'venv/bin/python'
            python.parent.mkdir(parents=True)
            python.write_text('#!/bin/sh\nprintf \'{"used":"active"}\\n\'\n')
            python.chmod(0o700)
            (target / 'src' / 'hey_my_buddy').mkdir(parents=True)
            (target / 'READY.json').write_text(json.dumps({'format': 1, 'state': 'READY', 'contentId': target.name}))
            launcher.write_active_runtime(state, target)
            bootstrap = base / 'bootstrap'
            bootstrap.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -S "$@"\n')
            bootstrap.chmod(0o700)
            environment = {key: value for key, value in os.environ.items() if not key.startswith('BUDDY_')}
            environment.update(BUDDY_STATE_DIR=str(state), BUDDY_RUNTIME_ROOT=str(root),
                               BUDDY_BOOTSTRAP_PYTHON=str(bootstrap), UV_BIN=str(base / 'missing-uv'))
            completed = subprocess.run(['/bin/sh', str(script), 'ping'], env=environment,
                                       capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(json.loads(completed.stdout), {'used': 'active'})

    def test_daily_call_execs_active_python_without_uv(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            target = state / 'runtime' / ('a' * 32)
            python = target / 'venv/bin/python'
            (target / 'src' / 'hey_my_buddy').mkdir(parents=True)
            with mock.patch.dict(os.environ, {'BUDDY_STATE_DIR': str(state)}, clear=True), \
                    mock.patch('hey_my_buddy.install.launcher.selected_runtime', return_value=target), \
                    mock.patch('hey_my_buddy.install.launcher.os.execve', side_effect=SystemExit(0)) as execute:
                with self.assertRaises(SystemExit):
                    launcher.main(['ping'])
            self.assertEqual(execute.call_args.args[0], str(python))
            self.assertEqual(execute.call_args.args[1], [str(python), '-P', '-m', 'hey_my_buddy.cli.main', 'ping'])

    def test_host_scrubs_internal_identity_and_endpoints_but_worker_keeps_credential(self):
        inherited = {'BUDDY_STATE_DIR': '/private/state', 'BUDDY_RUNTIME_ROOT': '/private/runtime',
                     'BUDDY_DEV_SOURCE': '1', 'BUDDY_RUNTIME': '/stale', 'BUDDY_RUNTIME_IDENTITY': 'stale',
                     'BUDDY_PYTHON': '/stale/python', 'BUDDY_WORKER_STATE': '/stale/worker',
                     'BUDDY_WORKER_ID': 'stale-worker', 'ANTHROPIC_BASE_URL': 'https://invalid.example',
                     'OPENAI_BASE_URL': 'https://invalid.example'}
        with mock.patch.dict(os.environ, inherited, clear=True):
            launcher.clean_host_environment()
            self.assertTrue(launcher.HOST_INTERNAL_KEYS.isdisjoint(os.environ))
            self.assertTrue(launcher.HOST_ENDPOINT_KEYS.isdisjoint(os.environ))
            self.assertEqual(os.environ['BUDDY_STATE_DIR'], '/private/state')
            self.assertEqual(os.environ['BUDDY_RUNTIME_ROOT'], '/private/runtime')
            self.assertEqual(os.environ['BUDDY_DEV_SOURCE'], '1')
        with mock.patch.dict(os.environ, {**inherited, 'BUDDY_AGENT_CREDENTIAL_FILE': '/private/attempt'}, clear=True):
            launcher.clean_host_environment()
            self.assertEqual(os.environ['BUDDY_WORKER_ID'], 'stale-worker')
            self.assertEqual(os.environ['BUDDY_AGENT_CREDENTIAL_FILE'], '/private/attempt')

    def test_missing_installed_pointer_has_repairable_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(os.environ, {'BUDDY_RUNTIME_ROOT': str(Path(temporary) / 'runtime')}, clear=True), \
                    mock.patch.object(runtime, 'project_root', return_value=Path(temporary) / 'package'):
                with self.assertRaises(BoardError) as caught:
                    launcher.selected_runtime(Path(temporary))
            self.assertEqual(caught.exception.code, 'ACTIVE_RUNTIME_MISSING')


if __name__ == '__main__':
    unittest.main()
