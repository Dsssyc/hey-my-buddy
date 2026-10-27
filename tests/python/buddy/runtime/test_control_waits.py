"""Execution preflight and local control keep one attempt's stop intent intact."""
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from hey_my_buddy.buddy.runtime.execution import Execution, _ExecutionControl
from hey_my_buddy.errors import BoardError
from hey_my_buddy.paths import native_path


class ControlWaitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.claim = {'task': {'taskId': 'task', 'spec': {'adapter': 'codex'}},
                      'attempt': {'taskId': 'task', 'attemptId': 'attempt', 'generation': 1}}
        self.client = Mock()
        self.execution = Execution(self.claim, self.root, 'nonce', client=self.client)
        self.directory = self.execution.directory
        native_path(self.directory).mkdir(parents=True)
        self.execution.receipt = Mock(side_effect=lambda claim, report, directory: report)
        self.execution._execute_selected = Mock(return_value={'status': 'ok'})
        self.backoff = self.enterContext(patch.object(self.execution.control, 'wait'))

    def guarded(self):
        return self.execution._execute_guarded(self.claim, self.claim['task'],
                                               self.claim['task']['spec'], self.claim['attempt'],
                                               self.directory, {})

    def test_unknown_health_waits_without_using_the_failure_retry(self):
        unknown = {'status': 'unknown', 'revision': 1}
        ready = {'status': 'ready', 'revision': 2,
                 'harness': 'codex', 'command': ['fixture-codex']}
        old_failure_with_unknown_probe = {'status': 'unhealthy', 'revision': 2,
                                          'lastProbe': {'status': 'unknown', 'reasonCode': 'scan-timeout'}}
        self.client.call.side_effect = [{'harness': unknown},
                                        {'harness': old_failure_with_unknown_probe}, {'harness': ready}]
        self.assertEqual(self.guarded()['status'], 'ok')
        calls = self.client.call.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(not call.args[1]['retry'] for call in calls))
        self.assertEqual(len({call.args[1]['commandId'] for call in calls}), 3)
        self.assertEqual([call.args[0] for call in self.backoff.call_args_list], [15, 30])
        self.execution._execute_selected.assert_called_once()

    def test_transport_wait_expiry_replays_the_same_control_request(self):
        ready = {'status': 'ready', 'revision': 1,
                 'harness': 'codex', 'command': ['fixture-codex']}
        self.client.call.side_effect = [BoardError('SERVICE_UNAVAILABLE', 'wait expired'),
                                        {'harness': ready}]
        self.guarded()
        first, second = self.client.call.call_args_list
        self.assertEqual(first.args, second.args)

    def test_stop_between_unknown_health_and_next_prepare_is_cancelled(self):
        self.client.call.return_value = {'harness': {'status': 'unknown',
                                                      'revision': 1}}
        self.backoff.side_effect = lambda seconds: self.execution.request_stop('console')
        result = self.guarded()
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(result['terminationReason'], 'console-stop')
        self.assertTrue(result['shutdownConfirmed'])
        self.execution._execute_selected.assert_not_called()

    def test_stop_during_blocked_prepare_never_starts_a_handle(self):
        entered = threading.Event()
        release = threading.Event()
        result = []
        errors = []

        def prepare(operation, params):
            self.assertEqual(operation, 'harness_prepare')
            entered.set()
            self.assertTrue(release.wait(3))
            return {'harness': {'status': 'ready', 'revision': 1,
                                'harness': 'codex', 'command': ['fixture-codex']}}

        self.client.call.side_effect = prepare
        def guarded():
            try:
                result.append(self.guarded())
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=guarded)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.execution.request_stop('console')
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result[0]['status'], 'cancelled')
        self.assertEqual(result[0]['terminationReason'], 'console-stop')
        self.execution._execute_selected.assert_not_called()

    def test_finished_attempt_without_stop_remains_an_error(self):
        self.client.call.side_effect = BoardError('ATTEMPT_FINISHED', 'already finished')
        with self.assertRaises(BoardError) as caught:
            self.guarded()
        self.assertEqual(caught.exception.code, 'ATTEMPT_FINISHED')
        self.execution.receipt.assert_not_called()

    def test_pending_stop_reaches_new_handle_once(self):
        control = _ExecutionControl(self.execution)
        self.execution.control = control
        self.execution.flush_hints = Mock()
        control.request_stop('console', None)
        handle = SimpleNamespace(cancel_requested=False)
        stopped = threading.Event()

        def stop(_implementation, owned, *, stop_initiator, grace_deadline):
            self.assertEqual((stop_initiator, grace_deadline), ('console', None))
            owned.cancel_requested = True
            stopped.set()

        with patch('hey_my_buddy.buddy.runtime.execution.role_seam.worker_stop', side_effect=stop) as native_stop:
            control.start()
            try:
                control.attach(handle, object())
                self.assertTrue(stopped.wait(2))
                self.assertTrue(handle.cancel_requested)
                self.assertEqual(native_stop.call_count, 1)
            finally:
                control.stop()
                control.join(2)
        self.assertFalse(control.is_alive())
