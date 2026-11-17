"""Harness presence, shared-link publication and independent rollback under private homes."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import skill_install, upgrade
from hey_my_buddy.paths import native_path


class HarnessSkillLinkTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.target = self.home / '.agents/skills/hey-my-buddy'
        self.target.mkdir(parents=True)
        (self.target / 'sentinel').write_bytes(b'shared skill')
        self.enterContext(mock.patch.dict(os.environ, {
            'HOME': str(self.home), 'USERPROFILE': str(self.home),
            'HMB_AGENT_SKILLS_DIR': str(self.target.parent), 'HMB_CLAUDE_SKILLS_DIR': ''}))

    def enable(self, *directories):
        for directory in directories:
            (self.home / directory).mkdir(parents=True, exist_ok=True)

    def test_presence_controls_publication_without_creating_absent_harness_homes(self):
        cases = (((), set()), (('.gemini',), set()), (('.claude',), {'claude'}),
                 (('.gemini/antigravity',), {'antigravity'}),
                 (('.gemini/antigravity-cli',), {'antigravity'}),
                 (('.gemini/antigravity', '.gemini/antigravity-cli'), {'antigravity'}),
                 (('.claude', '.gemini/antigravity'), {'claude', 'antigravity'}))
        for index, (directories, expected) in enumerate(cases):
            with self.subTest(directories=directories):
                home = self.root / f'case-{index}'
                home.mkdir()
                for directory in directories:
                    (home / directory).mkdir(parents=True)
                with mock.patch.dict(os.environ, {'HOME': str(home), 'USERPROFILE': str(home)}):
                    facts = skill_install._link_facts()
                    skill_install._check_harness_links(self.target)
                    reports = skill_install._link_harnesses(self.target, facts)
                    for name, report in reports.items():
                        if name in expected:
                            self.assertEqual(report['status'], 'linked')
                            self.assertEqual(Path(report['path']).resolve(), self.target)
                            self.assertTrue(facts[name]['created'])
                        else:
                            self.assertEqual(report['status'], 'skipped')
                            self.assertIn('directory does not exist', report['reason'])
                            self.assertFalse(Path(report['path']).parent.exists())
                    reused = skill_install._link_harnesses(self.target, skill_install._link_facts())
                    for name in expected:
                        self.assertEqual(reused[name]['status'], 'already-linked')
                    self.assertEqual((home / '.claude').exists(), '.claude' in directories)
                    self.assertEqual((home / '.gemini').exists(), any(d.startswith('.gemini') for d in directories))

    def test_claude_override_changes_link_location_but_not_presence_rule(self):
        override = self.root / 'custom/skills'
        with mock.patch.dict(os.environ, {'HMB_CLAUDE_SKILLS_DIR': str(override)}):
            reports = skill_install._link_harnesses(self.target, skill_install._link_facts())
            self.assertEqual(reports['claude']['status'], 'skipped')
            self.assertFalse(override.exists())
            self.enable('.claude')
            reports = skill_install._link_harnesses(self.target, skill_install._link_facts())
            self.assertEqual(Path(reports['claude']['path']), override / skill_install.SKILL)
            self.assertEqual((override / skill_install.SKILL).resolve(), self.target)

    def test_conflicts_are_reported_and_rejected_for_each_harness(self):
        self.enable('.claude', '.gemini/antigravity')
        for name, (link, _) in skill_install.harness_link_locations().items():
            with self.subTest(harness=name):
                link.mkdir(parents=True)
                keep = link / 'keep'
                keep.write_bytes(b'user skill')
                self.assertEqual(skill_install._harness_link_report(self.target)[name]['status'], 'conflict')
                with self.assertRaises(BoardError) as caught:
                    skill_install._check_harness_links(self.target)
                self.assertEqual(caught.exception.code, 'SKILL_TARGET_CONFLICT')
                self.assertEqual(keep.read_bytes(), b'user skill')
                keep.unlink()
                link.rmdir()

    def test_rollback_retains_preexisting_links_and_removes_new_links_for_either_harness(self):
        self.enable('.claude', '.gemini/antigravity')
        for retained in ('claude', 'antigravity'):
            with self.subTest(retained=retained):
                locations = skill_install.harness_link_locations()
                skill_install._link_skill(locations[retained][0], self.target)
                facts = skill_install._link_facts()
                skill_install._link_harnesses(self.target, facts)
                self.assertFalse(facts[retained]['created'])
                skill_install._restore_links(facts, self.target)
                for name, (link, _) in locations.items():
                    self.assertEqual(skill_install._is_link(link), name == retained)
                self.assertEqual((self.target / 'sentinel').read_bytes(), b'shared skill')
                skill_install._remove_link(locations[retained][0])

    def test_failed_second_link_can_rollback_the_first_without_touching_shared_content(self):
        self.enable('.claude', '.gemini/antigravity')
        facts = skill_install._link_facts()
        create = skill_install._create_link
        def fail_second(link, target):
            if link == Path(facts['antigravity']['path']):
                raise OSError('injected second-link failure')
            create(link, target)
        with mock.patch.object(skill_install, '_create_link', side_effect=fail_second):
            with self.assertRaises(BoardError) as caught:
                skill_install._link_harnesses(self.target, facts)
        self.assertEqual(caught.exception.code, 'SKILL_LINK_FAILED')
        skill_install._restore_links(facts, self.target)
        self.assertTrue(all(not skill_install._is_link(Path(f['path'])) for f in facts.values()))
        self.assertEqual((self.target / 'sentinel').read_bytes(), b'shared skill')

    def test_recovery_removes_links_created_before_created_fact_was_persisted(self):
        self.enable('.claude', '.gemini/antigravity-cli')
        facts = skill_install._link_facts()
        before_publication = copy.deepcopy(facts)
        skill_install._link_harnesses(self.target, facts)
        upgrade._restore_skill({'skillHadPrevious': False, 'harnessLinks': before_publication},
                               (self.target, self.root / 'stage', self.root / 'previous'))
        self.assertFalse(self.target.exists())
        for fact in facts.values():
            self.assertFalse(skill_install._is_link(Path(fact['path'])))

    def test_recovery_validates_every_recorded_link_before_deleting(self):
        self.enable('.claude', '.gemini/antigravity')
        suffix = 'a' * 32
        stage = self.target.with_name('.hey-my-buddy-upgrade-stage-' + suffix)
        previous = self.target.with_name('.hey-my-buddy-upgrade-previous-' + suffix)
        journal = {'skillTarget': str(self.target), 'skillStage': str(stage),
                   'skillPrevious': str(previous), 'harnessLinks': skill_install._link_facts()}
        self.assertEqual(upgrade._skill_paths(journal), (self.target, stage, previous))
        for name in journal['harnessLinks']:
            invalid = copy.deepcopy(journal)
            invalid['harnessLinks'][name]['path'] = str(self.root / 'unrelated')
            with self.subTest(harness=name), self.assertRaises(BoardError) as caught:
                upgrade._skill_paths(invalid)
            self.assertEqual(caught.exception.code, 'UPGRADE_RECOVERY_REQUIRED')
        self.assertEqual(native_path(self.target / 'sentinel').read_bytes(), b'shared skill')
