"""Review eligibility checks local mechanisms, never paid native evidence."""
import importlib.util
import json
import unittest
from unittest.mock import patch

from hey_my_buddy.buddy.harnesses.base import Adapter
from hey_my_buddy.buddy.harnesses.codex.adapter import CodexAdapter
from hey_my_buddy.buddy.harnesses.claude.adapter import ClaudeAdapter
from hey_my_buddy.buddy.harnesses.dsh.adapter import DshAdapter
from hey_my_buddy.buddy.harnesses.zcode.adapter import ZcodeAdapter
from hey_my_buddy.buddy.harnesses.registry import RUN_SEAMS
from hey_my_buddy.blackboard.service.harness_health import read_health
from support import BoardTestCase


class LocalEligibilityTests(unittest.TestCase):
    def test_native_sandbox_mechanisms_do_not_require_a_version_certificate(self):
        self.assertIsNone(importlib.util.find_spec('hey_my_buddy.harness_review'),
                          "the retired certificate store must not ship with the package")
        with patch('hey_my_buddy.buddy.harnesses.base.sys.platform', 'darwin'), \
             patch('subprocess.Popen', side_effect=AssertionError('native process')), \
             patch('subprocess.run', side_effect=AssertionError('native process')):
            for item in (CodexAdapter(), ClaudeAdapter()):
                with self.subTest(harness=item.name):
                    result = item.local_read_only_check()
                    self.assertTrue(result['eligible'])
                    self.assertTrue(result['systemSandbox'])
                    self.assertIsNone(result['reason'])

    def test_unimplemented_non_sandbox_adapters_stay_ineligible(self):
        with patch('subprocess.Popen', side_effect=AssertionError('native process')), \
             patch('subprocess.run', side_effect=AssertionError('native process')):
            for item in (Adapter(), DshAdapter(), ZcodeAdapter()):
                with self.subTest(harness=item.name):
                    result = item.local_read_only_check()
                    self.assertFalse(result['eligible'])
                    self.assertFalse(result['systemSandbox'])
                    if item.name in ('dsh', 'zcode'):
                        self.assertEqual(result['reason'], {'code': 'harness.readonly-worker-carrier-unimplemented',
                                                            'params': {'harness': item.name}})

    def test_a_declaration_without_a_handler_is_ineligible(self):
        class Declared(Adapter):
            read_only_structured = True
            system_sandbox_platforms = ('darwin',)
        with patch('hey_my_buddy.buddy.harnesses.base.sys.platform', 'darwin'):
            self.assertFalse(Declared().local_read_only_check()['eligible'])

    def test_native_sandbox_platform_and_missing_resource_are_separate_facts(self):
        with patch('hey_my_buddy.buddy.harnesses.base.sys.platform', 'win32'):
            result = ClaudeAdapter().local_read_only_check()
            self.assertFalse(result['eligible'])
            self.assertFalse(result['systemSandbox'])
            self.assertEqual(result['reason'], {'code': 'harness.readonly-platform-unsupported', 'params': {}})
        with patch('hey_my_buddy.buddy.harnesses.base.sys.platform', 'darwin'), patch.dict(RUN_SEAMS, {'codex': None}):
            self.assertEqual(CodexAdapter().local_read_only_check()['reason'],
                             {'code': 'harness.readonly-resource-missing', 'params': {}})

    def test_non_sandbox_mechanism_cannot_allow_commands(self):
        class Restricted(Adapter):
            name = "dsh"
            read_only_structured = True
            read_only_tool_categories = ('read', 'search')
        item = Restricted()
        self.assertTrue(item.local_read_only_check()['eligible'])
        with patch.object(item, 'read_only_tool_categories', ('read', 'execute')):
            self.assertFalse(item.local_read_only_check()['eligible'])


class HealthEligibilityTests(BoardTestCase):
    def test_deferred_review_health_is_unavailable_without_launching_or_writing(self):
        board = self.board()
        with board.store.db.read() as db, patch('subprocess.Popen', side_effect=AssertionError('native process')), \
             patch('subprocess.run', side_effect=AssertionError('native process')):
            before = db.total_changes
            for harness in ('dsh', 'zcode'):
                result = read_health(db, harness)
                capability = result['readOnlyStructured']
                self.assertFalse(capability['eligible'])
                self.assertFalse(capability['systemSandbox'])
                self.assertEqual(capability['reason'], {'code': 'harness.readonly-worker-carrier-unimplemented',
                                                        'params': {'harness': harness}})
            self.assertEqual(db.total_changes, before)

    def test_health_reads_ignore_old_certificates_and_do_not_write_state(self):
        board = self.board()
        with board.store.db.write() as db:
            db.execute("INSERT INTO harness_health(harness,revision,status,record_json) VALUES('codex',1,'ready',?) "
                       "ON CONFLICT(harness) DO UPDATE SET revision=excluded.revision,status=excluded.status,record_json=excluded.record_json",
                       (json.dumps({'version': 'new-version', 'reviewVerification': {'verified': False}, 'reason': None}),))
        with patch('hey_my_buddy.buddy.harnesses.base.sys.platform', 'darwin'), board.store.db.read() as db:
            before = db.execute('SELECT record_json FROM harness_health WHERE harness=?', ('codex',)).fetchone()[0]
            result = read_health(db, 'codex')
            self.assertTrue(result['readOnlyStructured']['eligible'])
            self.assertTrue(result['systemSandbox'])
            self.assertNotIn('reviewVerification', result)
            self.assertEqual(db.execute('SELECT record_json FROM harness_health WHERE harness=?', ('codex',)).fetchone()[0], before)


if __name__ == '__main__':
    unittest.main()
