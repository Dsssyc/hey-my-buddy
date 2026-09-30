"""The shared package carries all platform launcher entrypoints."""
from pathlib import Path
import tempfile
import unittest
import json
import os
import shutil
from unittest import mock

from buddy import skill_package, skill_install
from buddy.errors import BoardError


ROOT = Path(__file__).resolve().parents[2]


class SkillPackageTests(unittest.TestCase):
    def test_packaged_skill_carries_windows_and_posix_entrypoints(self):
        with tempfile.TemporaryDirectory() as temporary:
            skill = Path(temporary) / 'buddy'
            skill_package.assemble(ROOT, skill)
            for name in ('buddy', 'buddy.cmd', 'buddy.ps1'):
                self.assertTrue((skill / 'scripts' / name).is_file(), name)
            self.assertIn('active-runtime.json', (skill / 'package/src/buddy/launcher.py').read_text())

    def test_same_marker_does_not_hide_a_damaged_launcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target = Path(temporary) / 'source/buddy', Path(temporary) / 'installed/buddy'
            skill_package.assemble(ROOT, source)
            target.parent.mkdir()
            shutil.copytree(source, target)
            marker = skill_install._marker(source)
            self.assertTrue(skill_install._matches(source, target, marker))
            (target / 'scripts/buddy').write_text('damaged')
            self.assertFalse(skill_install._matches(source, target, marker))
            placement, previous = skill_install._place(source, target, marker)
            self.assertEqual(placement, 'updated')
            self.assertTrue(skill_install._matches(source, target, marker))
            self.assertEqual((previous / 'scripts/buddy').read_text(), 'damaged')

    def _upgrade_pair(self, root: Path) -> tuple[Path, Path]:
        """An assembled source and an identical copy at the agent skills home."""
        source, target = root / 'source/buddy', root / 'agents/buddy'
        skill_package.assemble(ROOT, source)
        target.parent.mkdir()
        shutil.copytree(source, target)
        return source, target

    def _upgrade_state(self, root: Path) -> Path:
        state = root / 'state'
        state.mkdir()
        (state / 'active-runtime.json').write_text('{}')
        (state / 'control.json').write_text('{}')
        return state

    def _upgrade_environment(self, root: Path, state: Path) -> dict:
        return {'BUDDY_STATE_DIR': str(state), 'BUDDY_AGENT_SKILLS_DIR': str(root / 'agents'),
                'BUDDY_CLAUDE_SKILLS_DIR': str(root / 'claude'), 'CODEX_HOME': str(root / 'codex'),
                'BUDDY_RUNTIME_ROOT': str(root / 'runtime'), 'HOME': str(root / 'home')}

    def test_same_marker_with_changed_content_reports_an_update(self):
        with tempfile.TemporaryDirectory(prefix='buddy-skill-content-') as temporary:
            root = Path(temporary)
            source, target = self._upgrade_pair(root)
            state = self._upgrade_state(root)
            # The version, contract and sourceCommit all stay the same; only the
            # bytes change, which is exactly the rebuild this defect covered.
            (target / 'SKILL.md').write_text((target / 'SKILL.md').read_text() + '\n<!-- rebuilt -->\n')
            with mock.patch.dict(os.environ, self._upgrade_environment(root, state), clear=True), \
                 mock.patch('buddy.skill_install.packaged_skill', return_value=source), \
                 mock.patch('buddy.skill_install._check_claude_link'), \
                 mock.patch('buddy.skill_install._link_claude', return_value={'status': 'already-linked'}), \
                 mock.patch('buddy.skill_install.write_runtime_hint'), \
                 mock.patch('buddy.upgrade.upgrade', return_value={'upgraded': True}) as upgrade:
                result = skill_install.install({})
            upgrade.assert_called_once()
            self.assertEqual(result['skill']['placement'], 'updated')
            self.assertEqual(result['service']['action'], 'upgrade')

    def test_same_marker_with_identical_content_stays_already_current(self):
        with tempfile.TemporaryDirectory(prefix='buddy-skill-current-') as temporary:
            root = Path(temporary)
            source, target = self._upgrade_pair(root)
            state = self._upgrade_state(root)
            with mock.patch.dict(os.environ, self._upgrade_environment(root, state), clear=True), \
                 mock.patch('buddy.skill_install.packaged_skill', return_value=source), \
                 mock.patch('buddy.launcher.selected_runtime', return_value=None), \
                 mock.patch('buddy.skill_install._check_claude_link'), \
                 mock.patch('buddy.skill_install._link_claude', return_value={'status': 'already-linked'}), \
                 mock.patch('buddy.skill_install.write_runtime_hint'), \
                 mock.patch('buddy.upgrade.upgrade', return_value={'upgraded': True}) as upgrade:
                result = skill_install.install({})
            # The service may still switch generations, but the skill itself is
            # reported by its actual content, not by the version marker alone.
            upgrade.assert_called_once()
            self.assertEqual(result['skill']['placement'], 'already-current')
            self.assertTrue(skill_install._matches(source, target, skill_install._marker(source)))

    def test_intact_install_does_not_restart_or_materialize(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state, home = root / 'state', root / 'skills'
            state.mkdir()
            (state / 'active-runtime.json').write_text('{}')
            source = root / 'source'
            active = root / 'runtime' / ('a' * 32)
            with mock.patch.dict(os.environ, {**self._upgrade_environment(root, state), 'BUDDY_AGENT_SKILLS_DIR': str(home)}, clear=True), \
                 mock.patch('buddy.skill_install.packaged_skill', return_value=source), \
                 mock.patch('buddy.skill_install._marker', return_value={'version': 'fixture', 'contract': 'fixture'}), \
                 mock.patch('buddy.skill_install._matches', return_value=True), \
                 mock.patch('buddy.skill_install._check_claude_link'), \
                 mock.patch('buddy.skill_install._link_claude', return_value={'status': 'already-linked'}), \
                 mock.patch('buddy.skill_install.write_runtime_hint'), \
                 mock.patch('buddy.launcher.selected_runtime', return_value=active), \
                 mock.patch('buddy.runtime.runtime_dir', return_value=active), \
                 mock.patch('buddy.runtime.content_id', return_value=active.name), \
                 mock.patch('buddy.runtime.materialize') as materialize, \
                 mock.patch('buddy.upgrade.upgrade') as upgrade:
                result = skill_install.install({})
            self.assertEqual(result['skill']['placement'], 'already-current')
            self.assertEqual(result['service']['action'], 'none')
            materialize.assert_not_called()
            upgrade.assert_not_called()

    def test_interrupted_install_recovers_before_busy_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / 'state'
            state.mkdir()
            (state / 'board.sqlite3').touch()
            journal = state / 'upgrade.json'
            journal.write_text('{}')
            def recover(_params):
                journal.unlink()
            with mock.patch.dict(os.environ, {**self._upgrade_environment(root, state), 'BUDDY_AGENT_SKILLS_DIR': str(root / 'skills')}, clear=True), \
                 mock.patch('buddy.upgrade.upgrade', side_effect=recover) as recovery, \
                 mock.patch('buddy.upgrade.idle_snapshot', side_effect=BoardError('UPGRADE_NOT_IDLE', 'raced work')), \
                 mock.patch('buddy.skill_install._place') as place:
                with self.assertRaises(BoardError) as caught:
                    skill_install.install({})
            self.assertEqual(caught.exception.code, 'UPGRADE_NOT_IDLE')
            recovery.assert_called_once_with({})
            place.assert_not_called()


if __name__ == '__main__':
    unittest.main()
