"""Private old-runtime rehearsal for the product-name cutover; no native model call."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
from unittest import mock

from hey_my_buddy.install import skill_install, upgrade
from support import BoardTestCase

ROOT = Path(__file__).resolve().parents[3]
BASE = '4b7f494670efe7a3fe053062f16cce9312a5e042'


class ReleasedRuntimeNamesTests(BoardTestCase):
    def test_0341_service_and_skill_upgrade_through_their_own_names(self):
        source = self.directory / 'package'
        source.mkdir()
        archive = subprocess.run(['git', '-C', str(ROOT), 'archive', BASE],
                                 capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as stock:
            stock.extractall(source, filter='data')
        (source / 'src/hey_my_buddy/build-info.json').write_text(json.dumps({'sourceCommit': BASE}))
        state = self.directory / 'state'
        runtimes = self.directory / 'runtimes'
        agents = self.directory / 'agents'
        claude = self.directory / 'claude'
        old_skill = agents / 'buddy'
        old_link = claude / 'buddy'
        # Only this historical subprocess receives the released generation's names.
        old_environment = {('BUDDY_' + key.removeprefix('HMB_') if key.startswith('HMB_') else key): value
                           for key, value in os.environ.items()
                           if key not in {'PYTHONPATH', 'VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT'}}
        old_environment.update(BUDDY_STATE_DIR=str(state), BUDDY_RUNTIME_ROOT=str(runtimes),
                               BUDDY_DEV_SOURCE='', BUDDY_CONSOLE_PORT='0',
                               BUDDY_AGENT_SKILLS_DIR=str(agents), BUDDY_CLAUDE_SKILLS_DIR=str(claude))
        launcher = source / 'skills/buddy/scripts/buddy'
        self.addCleanup(self.stop_private, state, runtimes)
        installed = subprocess.run([str(launcher), 'install'], env=old_environment,
                                   capture_output=True, text=True, timeout=180)
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        old = subprocess.run([str(old_skill / 'scripts/buddy'), 'health'], env=old_environment,
                             capture_output=True, text=True, timeout=180)
        self.assertEqual(old.returncode, 0, old.stdout + old.stderr)
        health = json.loads(old.stdout)
        self.assertTrue(health['runtimeStable'])
        previous = runtimes / health['runtimeContentId']
        upgrade.detach(state, previous)
        self.assertFalse((state / 'control.json').exists())
        environment = {'HMB_STATE_DIR': str(state), 'HMB_RUNTIME_ROOT': str(runtimes),
                       'HMB_AGENT_SKILLS_DIR': str(agents), 'HMB_CLAUDE_SKILLS_DIR': str(claude)}
        with mock.patch.dict(os.environ, environment):
            installation = skill_install.install({})
            result = installation['service']['result']
            self.assertTrue(result['upgraded'], result)
            target = runtimes / result['verification']['runtimeContentId']
            self.assertEqual(upgrade.command(state, target, 'health')['runtimeContentId'], target.name)
        self.assertEqual(result['previousRuntime'], previous.name)
        self.assertEqual(result['legacySkillCleanup']['agent']['status'], 'removed')
        self.assertEqual(result['legacySkillCleanup']['claude']['status'], 'removed')
        self.assertFalse(old_skill.exists())
        self.assertFalse(old_link.is_symlink())
        self.assertEqual(json.loads((agents / 'hey-my-buddy/skill.json').read_text())['name'], 'hey-my-buddy')
        self.assertTrue((claude / 'hey-my-buddy').is_symlink())

    def stop_private(self, state: Path, runtimes: Path):
        pointer = state / 'active-runtime.json'
        endpoint = state / 'control.json'
        if pointer.exists():
            target = Path(json.loads(pointer.read_text())['runtimeDir'])
        elif endpoint.exists():
            target = runtimes / json.loads(endpoint.read_text())['runtimeIdentity'].removeprefix('runtime:')
        else:
            return
        upgrade.detach(state, target)
