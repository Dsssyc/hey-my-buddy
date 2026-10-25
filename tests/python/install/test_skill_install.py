"""The shared package carries all platform launcher entrypoints."""
from contextlib import chdir
from pathlib import Path
import tempfile
import unittest
import json
import os
import shutil
from unittest import mock

from hey_my_buddy.install import skill_package, skill_install
from hey_my_buddy.errors import BoardError
from hey_my_buddy.paths import native_path
from hey_my_buddy.private_dirs import ensure_private_dir


ROOT = Path(__file__).resolve().parents[3]


class ClaudeLinkTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary).resolve()
        self.target = self.root / 'agents/hey-my-buddy'
        self.target.mkdir(parents=True)
        (self.target / 'sentinel').write_bytes(b'canonical skill')
        self.home = self.root / 'claude'
        self.link = self.home / skill_install.SKILL
        self.enterContext(mock.patch.object(skill_install, 'claude_skills_home', return_value=self.home))

    def test_link_is_verified_reused_reported_and_removed_without_entering_its_target(self):
        self.assertEqual(skill_install._link_claude(self.target)['status'], 'linked')
        self.assertTrue(self.link.is_junction() if os.name == 'nt' else self.link.is_symlink())
        self.assertEqual(self.link.resolve(), self.target.resolve())
        skill_install._check_claude_link(self.target)
        self.assertEqual(skill_install._claude_status(self.target), 'linked')
        with mock.patch.object(skill_install, '_create_claude_link') as create:
            self.assertEqual(skill_install._link_claude(self.target)['status'], 'already-linked')
        create.assert_not_called()
        skill_install._remove_claude_link(self.link)
        self.assertFalse(self.link.exists() or self.link.is_junction() or self.link.is_symlink())
        self.assertEqual((self.target / 'sentinel').read_bytes(), b'canonical skill')

    def test_a_link_to_another_skill_is_a_conflict_even_when_its_target_is_missing(self):
        foreign = self.root / 'foreign'
        foreign.mkdir()
        self.home.mkdir()
        skill_install._create_claude_link(self.link, foreign)
        foreign.rmdir()
        self.assertEqual(skill_install._claude_status(self.target), 'conflict')
        for operation in (skill_install._check_claude_link, skill_install._link_claude):
            with self.assertRaises(BoardError) as failure:
                operation(self.target)
            self.assertEqual(failure.exception.code, 'SKILL_TARGET_CONFLICT')
            self.assertIn(str(self.link), failure.exception.message)
        skill_install._remove_claude_link(self.link)
        self.assertEqual((self.target / 'sentinel').read_bytes(), b'canonical skill')

    def test_the_canonical_skill_cannot_be_a_link(self):
        self.home.mkdir()
        skill_install._create_claude_link(self.link, self.target)
        self.assertFalse(skill_install._matches(self.target, self.link, {}))
        with self.assertRaises(BoardError) as failure:
            skill_install._place(self.target, self.link, {})
        self.assertEqual(failure.exception.code, 'SKILL_TARGET_CONFLICT')

    def test_upgrade_rollback_removes_a_new_link_after_its_target_has_gone(self):
        from hey_my_buddy.install import upgrade
        skill_install._link_claude(self.target)
        staged, previous = self.root / 'stage', self.root / 'previous'
        upgrade._restore_skill({'skillHadPrevious': False, 'claudeLinkWasMissing': True,
                                'claudeLink': str(self.link)}, (self.target, staged, previous))
        self.assertFalse(self.link.exists() or self.link.is_junction() or self.link.is_symlink())


class SkillPackageTests(unittest.TestCase):
    def test_build_normalizes_a_relative_destination_before_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            cwd = root / 'working'
            cwd.mkdir()
            def assemble(_source, destination):
                destination.mkdir()
            with chdir(cwd), \
                 mock.patch.object(skill_package, 'assemble', side_effect=assemble):
                result = skill_package.build(ROOT, Path('..') / 'dist' / 'hey-my-buddy')
            self.assertEqual(result, root / 'dist' / 'hey-my-buddy')
            self.assertTrue(result.is_dir())
            self.assertNotIn('..', result.parts)

    def test_packaged_skill_carries_windows_and_posix_entrypoints(self):
        with tempfile.TemporaryDirectory() as temporary:
            skill = Path(temporary) / 'hey-my-buddy'
            skill_package.assemble(ROOT, skill)
            for name in ('hey-my-buddy', 'hey-my-buddy.cmd', 'hey-my-buddy.ps1'):
                self.assertTrue((skill / 'scripts' / name).is_file(), name)
            self.assertIn('active-runtime.json', (skill / 'package/src/hey_my_buddy/install/launcher.py').read_text(encoding="utf-8"))

    def test_same_marker_does_not_hide_a_damaged_launcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target = Path(temporary) / 'source/hey-my-buddy', Path(temporary) / 'installed/hey-my-buddy'
            skill_package.assemble(ROOT, source)
            target.parent.mkdir()
            shutil.copytree(source, target)
            marker = skill_install._marker(source)
            self.assertTrue(skill_install._matches(source, target, marker))
            (target / 'scripts/hey-my-buddy').write_text('damaged', encoding="utf-8", newline="\n")
            self.assertFalse(skill_install._matches(source, target, marker))
            placement, previous = skill_install._place(source, target, marker)
            self.assertEqual(placement, 'updated')
            self.assertTrue(skill_install._matches(source, target, marker))
            self.assertEqual((previous / 'scripts/hey-my-buddy').read_text(encoding="utf-8"), 'damaged')

    def test_deep_skill_install_and_upgrade_restore_keep_ordinary_paths(self):
        from hey_my_buddy.install import upgrade
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            home = root.joinpath(*(f'depth-{index}-' + 'x' * 38 for index in range(5)))
            target = home / skill_install.SKILL
            self.assertGreater(len(str(target)), 260)
            native_path(home).mkdir(parents=True)
            try:
                source = root / 'source'
                native_path(source).mkdir()
                native_path(source / 'skill.json').write_text(
                    json.dumps({'name': skill_install.SKILL, 'version': 'new'}), encoding='utf-8')
                native_path(source / 'SKILL.md').write_text('new', encoding='utf-8')
                native_path(target).mkdir()
                native_path(target / 'skill.json').write_text(
                    json.dumps({'name': skill_install.SKILL, 'version': 'old'}), encoding='utf-8')
                native_path(target / 'SKILL.md').write_text('old', encoding='utf-8')

                placement, previous = skill_install._place(source, target, skill_install._marker(source))
                self.assertEqual(placement, 'updated')
                self.assertEqual(native_path(target / 'SKILL.md').read_text(encoding='utf-8'), 'new')
                self.assertEqual(native_path(previous / 'SKILL.md').read_text(encoding='utf-8'), 'old')
                self.assertFalse(str(previous).startswith('\\\\?\\'))
                shutil.rmtree(native_path(previous))

                suffix = 'a' * 32
                staged = home / ('.hey-my-buddy-upgrade-stage-' + suffix)
                prior = home / ('.hey-my-buddy-upgrade-previous-' + suffix)
                native_path(staged).mkdir()
                native_path(staged / 'SKILL.md').write_text('future', encoding='utf-8')
                claude_home = root / 'claude'
                link = claude_home / skill_install.SKILL
                journal = {'skillHadPrevious': True, 'claudeLinkWasMissing': True, 'claudeLink': str(link)}
                with mock.patch.dict(os.environ, {'HMB_CLAUDE_SKILLS_DIR': str(claude_home)}):
                    upgrade._publish_skill(journal, (target, staged, prior))
                    self.assertEqual(native_path(target / 'SKILL.md').read_text(encoding='utf-8'), 'future')
                    self.assertEqual(native_path(prior / 'SKILL.md').read_text(encoding='utf-8'), 'new')
                    self.assertEqual(journal['createdClaudeLink'], str(link))
                    upgrade._restore_skill(journal, (target, staged, prior))
                self.assertEqual(native_path(target / 'SKILL.md').read_text(encoding='utf-8'), 'new')
                self.assertFalse(native_path(prior).exists())
                self.assertFalse(native_path(link).exists())
                self.assertFalse(skill_install._is_link(link))
            finally:
                shutil.rmtree(native_path(home))

    def _upgrade_pair(self, root: Path) -> tuple[Path, Path]:
        """An assembled source and an identical copy at the agent skills home."""
        source, target = root / 'source/hey-my-buddy', root / 'agents/hey-my-buddy'
        skill_package.assemble(ROOT, source)
        target.parent.mkdir()
        shutil.copytree(source, target)
        return source, target

    def _upgrade_state(self, root: Path) -> Path:
        state = root / 'state'
        ensure_private_dir(state)
        (state / 'active-runtime.json').write_text('{}', encoding="utf-8", newline="\n")
        (state / 'control.json').write_text('{}', encoding="utf-8", newline="\n")
        return state

    def _upgrade_environment(self, root: Path, state: Path) -> dict:
        return {'HMB_STATE_DIR': str(state), 'HMB_AGENT_SKILLS_DIR': str(root / 'agents'),
                'HMB_CLAUDE_SKILLS_DIR': str(root / 'claude'), 'CODEX_HOME': str(root / 'codex'),
                'HMB_RUNTIME_ROOT': str(root / 'runtime'), 'HOME': str(root / 'home'),
                'USERPROFILE': str(root / 'home'), 'LOCALAPPDATA': str(root / 'home/AppData/Local')}

    def test_same_marker_with_changed_content_reports_an_update(self):
        with tempfile.TemporaryDirectory(prefix='hey-my-buddy-skill-content-') as temporary:
            root = Path(temporary)
            source, target = self._upgrade_pair(root)
            state = self._upgrade_state(root)
            # The version, contract and sourceCommit all stay the same; only the
            # bytes change, which is exactly the rebuild this defect covered.
            (target / 'SKILL.md').write_text((target / 'SKILL.md').read_text(encoding="utf-8") + '\n<!-- rebuilt -->\n', encoding="utf-8", newline="\n")
            with mock.patch.dict(os.environ, self._upgrade_environment(root, state), clear=True), \
                 mock.patch('hey_my_buddy.install.skill_install.packaged_skill', return_value=source), \
                 mock.patch('hey_my_buddy.install.skill_install._check_claude_link'), \
                 mock.patch('hey_my_buddy.install.skill_install._link_claude', return_value={'status': 'already-linked'}), \
                 mock.patch('hey_my_buddy.install.skill_install.write_runtime_hint'), \
                 mock.patch('hey_my_buddy.install.upgrade.upgrade', return_value={'upgraded': True}) as upgrade:
                result = skill_install.install({})
            upgrade.assert_called_once()
            self.assertEqual(result['skill']['placement'], 'updated')
            self.assertEqual(result['service']['action'], 'upgrade')

    def test_same_marker_with_identical_content_stays_already_current(self):
        with tempfile.TemporaryDirectory(prefix='hey-my-buddy-skill-current-') as temporary:
            root = Path(temporary)
            source, target = self._upgrade_pair(root)
            state = self._upgrade_state(root)
            with mock.patch.dict(os.environ, self._upgrade_environment(root, state), clear=True), \
                 mock.patch('hey_my_buddy.install.skill_install.packaged_skill', return_value=source), \
                 mock.patch('hey_my_buddy.install.launcher.selected_runtime', return_value=None), \
                 mock.patch('hey_my_buddy.install.skill_install._check_claude_link'), \
                 mock.patch('hey_my_buddy.install.skill_install._link_claude', return_value={'status': 'already-linked'}), \
                 mock.patch('hey_my_buddy.install.skill_install.write_runtime_hint'), \
                 mock.patch('hey_my_buddy.install.upgrade.upgrade', return_value={'upgraded': True}) as upgrade:
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
            ensure_private_dir(state)
            (state / 'active-runtime.json').write_text('{}', encoding="utf-8", newline="\n")
            source = root / 'source'
            active = root / 'runtime' / ('a' * 32)
            with mock.patch.dict(os.environ, {**self._upgrade_environment(root, state), 'HMB_AGENT_SKILLS_DIR': str(home)}, clear=True), \
                 mock.patch('hey_my_buddy.install.skill_install.packaged_skill', return_value=source), \
                 mock.patch('hey_my_buddy.install.skill_install._marker', return_value={'version': 'fixture', 'contract': 'fixture'}), \
                 mock.patch('hey_my_buddy.install.skill_install._matches', return_value=True), \
                 mock.patch('hey_my_buddy.install.skill_install._check_claude_link'), \
                 mock.patch('hey_my_buddy.install.skill_install._link_claude', return_value={'status': 'already-linked'}), \
                 mock.patch('hey_my_buddy.install.skill_install.write_runtime_hint'), \
                 mock.patch('hey_my_buddy.install.launcher.selected_runtime', return_value=active), \
                 mock.patch('hey_my_buddy.install.runtime.runtime_dir', return_value=active), \
                 mock.patch('hey_my_buddy.install.runtime.materialize') as materialize, \
                 mock.patch('hey_my_buddy.install.upgrade.upgrade') as upgrade:
                result = skill_install.install({})
            self.assertEqual(result['skill']['placement'], 'already-current')
            self.assertEqual(result['service']['action'], 'none')
            materialize.assert_not_called()
            upgrade.assert_not_called()

    def test_interrupted_install_recovers_before_attempting_the_next_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = self._upgrade_pair(root)
            (target / 'SKILL.md').write_text('changed generation', encoding='utf-8')
            state = self._upgrade_state(root)
            journal = state / 'upgrade.json'
            journal.write_text('{}', encoding='utf-8')
            calls = []

            recovered = {'upgraded': False, 'recoveredJournal': True, 'rollback': {'workPreserved': True}}

            def cutover(selected_state, **kwargs):
                self.assertEqual(selected_state, state)
                calls.append(kwargs)
                if not kwargs:
                    journal.unlink()
                    return recovered
                self.assertFalse(journal.exists())
                raise BoardError('UPGRADE_NOT_IDLE', 'new work was admitted')

            with mock.patch.dict(os.environ, self._upgrade_environment(root, state), clear=True), \
                 mock.patch.object(skill_install, 'packaged_skill', return_value=source), \
                 mock.patch('hey_my_buddy.install.upgrade.upgrade', side_effect=cutover), \
                 mock.patch.object(skill_install, '_place') as place:
                with self.assertRaises(BoardError) as caught:
                    skill_install.install({})
            self.assertEqual(caught.exception.code, 'UPGRADE_NOT_IDLE')
            self.assertEqual(caught.exception.details['recovery'], recovered)
            self.assertEqual(calls, [{}, {'skill_source': source, 'skill_target': target}])
            place.assert_not_called()

    def test_install_preserves_recovery_result_and_passes_one_checked_state_root(self):
        recovered = {'upgraded': True, 'recoveredJournal': True, 'skillCleanupError': 'old skill cleanup denied',
                     'runtimePruning': {'complete': False, 'error': 'STORAGE_BUSY'}}
        for intact in (True, False):
            with self.subTest(intact=intact), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, target = self._upgrade_pair(root)
                if not intact:
                    (target / 'SKILL.md').write_text('changed generation', encoding='utf-8')
                state = self._upgrade_state(root)
                journal = state / 'upgrade.json'
                journal.write_text('{}', encoding='utf-8')
                active = root / 'runtime' / ('a' * 32)

                def cutover(selected_state, **kwargs):
                    self.assertEqual(selected_state, state)
                    if not kwargs:
                        journal.unlink()
                        return recovered
                    return {'upgraded': True}

                with mock.patch.dict(os.environ, self._upgrade_environment(root, state), clear=True), \
                     mock.patch.object(skill_install, 'get_state_dir', return_value=state) as select_state, \
                     mock.patch.object(skill_install, 'check_state_root', wraps=skill_install.check_state_root) as check_state, \
                     mock.patch.object(skill_install, 'packaged_skill', return_value=source), \
                     mock.patch.object(skill_install, 'write_runtime_hint'), \
                     mock.patch.object(skill_install, '_link_claude', return_value={'status': 'already-linked'}), \
                     mock.patch('hey_my_buddy.install.runtime.runtime_dir', return_value=active), \
                     mock.patch('hey_my_buddy.install.launcher.selected_runtime', return_value=active), \
                     mock.patch('hey_my_buddy.install.upgrade.upgrade', side_effect=cutover) as upgrade:
                    result = skill_install.install({})
                self.assertEqual(result['recovery'], recovered)
                self.assertEqual(result['service']['action'], 'none' if intact else 'upgrade')
                select_state.assert_called_once_with()
                check_state.assert_called_once_with(state, create=True)
                self.assertEqual(upgrade.call_args_list, [mock.call(state)] if intact else
                                 [mock.call(state), mock.call(state, skill_source=source, skill_target=target)])

    def test_install_rejects_a_linked_lock_before_changing_the_skill(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / 'agents'
            home.mkdir()
            foreign = root / 'foreign'
            foreign.write_bytes(b'untouched')
            (home / '.hey-my-buddy-install.lock').symlink_to(foreign)
            with mock.patch.dict(os.environ, self._upgrade_environment(root, root / 'state'), clear=True), \
                 mock.patch.object(skill_install, 'packaged_skill') as source:
                with self.assertRaises(BoardError) as failure:
                    skill_install.install({})
            self.assertEqual(failure.exception.code, 'PRIVATE_PATH_UNSAFE')
            self.assertEqual(foreign.read_bytes(), b'untouched')
            source.assert_not_called()

    def test_paths_distinguishes_missing_from_unreadable_runtime_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / 'state'
            state.mkdir()
            environment = self._upgrade_environment(root, state)
            with mock.patch.dict(os.environ, environment, clear=True):
                self.assertIsNone(skill_install.paths({})['runtime']['active'])
                pointer = state / 'active-runtime.json'
                pointer.write_text('{', encoding='utf-8')
                with self.assertRaises(ValueError):
                    skill_install.paths({})
                pointer.unlink()
                foreign = root / 'foreign'
                foreign.write_text('{}', encoding='utf-8')
                pointer.symlink_to(foreign)
                with self.assertRaises(BoardError) as failure:
                    skill_install.paths({})
            self.assertEqual(failure.exception.code, 'PRIVATE_PATH_UNSAFE')



if __name__ == '__main__':
    unittest.main()
