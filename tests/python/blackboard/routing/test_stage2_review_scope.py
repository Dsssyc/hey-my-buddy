"""Deferred review carriers are unavailable before any native process starts."""
import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from hey_my_buddy.buddy.harnesses.base import ExecutionContext, ReadOnlyStructuredRequest
from hey_my_buddy.buddy.harnesses.dsh.adapter import DshAdapter
from hey_my_buddy.buddy.harnesses.zcode.adapter import ZcodeAdapter
from hey_my_buddy.buddy.roles import run_controller, run_execution, router as router_role
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
                        router_role.prepare_router_review({}, {}, item, context)
                    with self.assertRaises(BoardError):
                        run_execution.start_review(item.name, context, request)

    def test_manually_supplied_controller_review_request_is_refused_before_launch(self):
        with patch('subprocess.Popen', side_effect=AssertionError('native process')), \
             patch('subprocess.run', side_effect=AssertionError('native process')):
            with self.assertRaises(BoardError) as caught:
                run_controller.execute({'operation': 'review', 'harness': 'zcode'}, threading.Event())
            self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')
            self.assertIsNone(importlib.util.find_spec('hey_my_buddy.buddy.harnesses.zcode.runner'))
            self.assertIsNone(importlib.util.find_spec('hey_my_buddy.buddy.harnesses.dsh.runner'))
            with self.assertRaises(BoardError) as caught:
                run_controller.execute({'operation': 'review', 'harness': 'dsh'}, threading.Event())
            self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')


if __name__ == '__main__':
    unittest.main()
