"""Review eligibility checks local mechanisms, never paid native evidence."""
import importlib.util
import json
import unittest
from unittest.mock import patch

from buddy.adapters.base import Adapter
from buddy.adapters.codex import CodexAdapter
from buddy.adapters.claude import ClaudeAdapter
from buddy.adapters.dsh import DshAdapter
from buddy.adapters.zcode import ZcodeAdapter
from buddy.harness_health import read_health
from support import BoardTestCase


class LocalEligibilityTests(unittest.TestCase):
    def test_native_sandbox_mechanisms_do_not_require_a_version_certificate(self):
        self.assertIsNone(importlib.util.find_spec('buddy.harness_review'),
                          "the retired certificate store must not ship with the package")
        with patch('buddy.adapters.base.sys.platform', 'darwin'), \
             patch('subprocess.Popen', side_effect=AssertionError('native process')), \
             patch('subprocess.run', side_effect=AssertionError('native process')):
            for item in (CodexAdapter(), ClaudeAdapter()):
                with self.subTest(adapter=item.name):
                    result = item.local_read_only_check()
                    self.assertTrue(result['eligible'])
                    self.assertTrue(result['systemSandbox'])
                    self.assertIsNone(result['reasonCode'])

    def test_unimplemented_non_sandbox_adapters_stay_ineligible(self):
        for item in (Adapter(), ZcodeAdapter()):
            with self.subTest(adapter=item.name):
                result = item.local_read_only_check()
                self.assertFalse(result['eligible'])
                self.assertFalse(result['systemSandbox'])
        result = DshAdapter().local_read_only_check()
        self.assertTrue(result['eligible'])
        self.assertFalse(result['systemSandbox'])

    def test_a_declaration_without_a_handler_is_ineligible(self):
        class Declared(Adapter):
            read_only_structured = True
            system_sandbox_platforms = ('darwin',)
        with patch('buddy.adapters.base.sys.platform', 'darwin'):
            self.assertFalse(Declared().local_read_only_check()['eligible'])

    def test_native_sandbox_platform_and_missing_resource_are_separate_facts(self):
        with patch('buddy.adapters.base.sys.platform', 'win32'):
            result = ClaudeAdapter().local_read_only_check()
            self.assertFalse(result['eligible'])
            self.assertFalse(result['systemSandbox'])
            self.assertEqual(result['reasonCode'], 'readonly-platform-unsupported')
        with patch('buddy.adapters.base.sys.platform', 'darwin'), patch('buddy.adapters.base.Path.is_file', return_value=False):
            self.assertEqual(CodexAdapter().local_read_only_check()['reasonCode'], 'readonly-resource-missing')

    def test_non_sandbox_mechanism_cannot_allow_commands(self):
        class Restricted(DshAdapter):
            read_only_structured = True
            read_only_tool_categories = ('read', 'search')
            def start_read_only_structured(self, context, request):
                raise AssertionError('eligibility must not execute the handler')
        item = Restricted()
        self.assertTrue(item.local_read_only_check()['eligible'])
        with patch.object(item, 'read_only_tool_categories', ('read', 'execute')):
            self.assertFalse(item.local_read_only_check()['eligible'])


class HealthEligibilityTests(BoardTestCase):
    def test_health_reads_ignore_old_certificates_and_do_not_write_state(self):
        board = self.board()
        with board.store.db.write() as db:
            db.execute("INSERT INTO harness_health(adapter,revision,status,record_json) VALUES('codex',1,'ready',?) "
                       "ON CONFLICT(adapter) DO UPDATE SET revision=excluded.revision,status=excluded.status,record_json=excluded.record_json",
                       (json.dumps({'version': 'new-version', 'reviewVerification': {'verified': False}}),))
        with patch('buddy.adapters.base.sys.platform', 'darwin'), board.store.db.read() as db:
            before = db.execute('SELECT record_json FROM harness_health WHERE adapter=?', ('codex',)).fetchone()[0]
            result = read_health(db, 'codex')
            self.assertTrue(result['readOnlyStructured']['eligible'])
            self.assertTrue(result['systemSandbox'])
            self.assertNotIn('reviewVerification', result)
            self.assertEqual(db.execute('SELECT record_json FROM harness_health WHERE adapter=?', ('codex',)).fetchone()[0], before)


if __name__ == '__main__':
    unittest.main()
