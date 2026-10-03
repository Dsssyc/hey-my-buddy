"""Deferred review carriers are unavailable before any native process starts."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from hey_my_buddy.buddy.harnesses.base import ExecutionContext, ReadOnlyStructuredRequest
from hey_my_buddy.buddy.harnesses.dsh.adapter import DshAdapter
from hey_my_buddy.buddy.harnesses.zcode.adapter import ZcodeAdapter
from hey_my_buddy.buddy.harnesses.dsh import runner as dsh_runner
from hey_my_buddy.buddy.roles import structured_call as read_only
from hey_my_buddy.buddy.harnesses.zcode import runner as zcode_runner
from hey_my_buddy.errors import BoardError


class DeferredReviewTests(unittest.TestCase):
    def test_removed_modules_and_bridge_do_not_ship(self):
        for module in ('dsh_read_only', 'zcode_read_only', 'zcode_static_contract'):
            self.assertIsNone(importlib.util.find_spec('hey_my_buddy.buddy.harnesses.' + module))
        root = Path(__file__).resolve().parents[4]
        self.assertFalse((root / 'harnesses/dsh/plugins/read-only-structured.mjs').exists())

    def test_capabilities_and_all_entrypoints_refuse_without_native_start(self):
        with tempfile.TemporaryDirectory(prefix='deferred-review-') as directory:
            root = Path(directory)
            context = ExecutionContext(task_id='micro-task', attempt_id='attempt', generation=1,
                spec={'cwd': str(root)}, directory=root / 'attempt', environment={}, runtime={})
            request = ReadOnlyStructuredRequest(str(root), 'read', {'type': 'object'},
                                               {'timeoutSeconds': 60, 'toolCalls': 8})
            with patch('subprocess.Popen', side_effect=AssertionError('native process')), \
                 patch('subprocess.run', side_effect=AssertionError('native process')):
                for item in (DshAdapter(), ZcodeAdapter()):
                    self.assertFalse(item.read_only_structured)
                    self.assertFalse(item.read_only_structured_resume)
                    self.assertTrue(item.no_tool_structured)
                    self.assertEqual(item.local_read_only_check()['reasonCode'], 'readonly-worker-carrier-unimplemented')
                    with self.assertRaises(BoardError):
                        item.start_read_only_structured(context, request)
                    with self.assertRaises(BoardError):
                        read_only.start(item.name, context, request)

    def test_manually_supplied_controller_review_request_is_refused_before_launch(self):
        with patch('subprocess.Popen', side_effect=AssertionError('native process')), \
             patch('subprocess.run', side_effect=AssertionError('native process')):
            result, code = zcode_runner.run({'readOnlyRequest': {}}, threading.Event())
            self.assertEqual(code, 1)
            self.assertEqual(result['code'], 'readonly-worker-carrier-unimplemented')
            self.assertFalse(result['modelStarted'])
            self.assertTrue(result['processState']['shutdownConfirmed'])
            with tempfile.TemporaryDirectory(prefix='deferred-controller-') as directory:
                control = Path(directory) / 'control.json'
                control.write_text(json.dumps({'readOnlyRequest': {}}))
                output = io.StringIO()
                with patch('sys.argv', ['dsh_runner', '--control', str(control)]), \
                     patch('sys.stdout', output), patch.object(dsh_runner.signal, 'signal'):
                    self.assertEqual(dsh_runner.main(), 1)
                result = json.loads(output.getvalue())
                self.assertEqual(result['code'], 'readonly-worker-carrier-unimplemented')
                self.assertFalse(result['modelStarted'])
                self.assertTrue(result['processState']['shutdownConfirmed'])


if __name__ == '__main__':
    unittest.main()
