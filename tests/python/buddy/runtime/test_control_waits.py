"""Control waits never convert a slow observation into an execution failure."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from hey_my_buddy.buddy.runtime.worker import Worker, _Renewal, TERMINATION_DEADLINE
from hey_my_buddy.buddy.runtime.windows_process import ProcessCreationEvidence
from hey_my_buddy.errors import BoardError


class ControlWaitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.worker = Worker.__new__(Worker)
        self.worker.worker_id = 'fixture'
        self.worker.client = Mock()
        self.worker.client.renew.return_value = {}
        self.worker.lease_seconds = 15
        self.worker.log = Mock()
        self.worker.live = Mock()
        self.worker.spool = Mock()
        self.worker._nonce = Mock(return_value='nonce')
        self.worker.stop = Mock()
        self.worker.stop_requested = Mock(return_value=False)
        self.worker.receipt = Mock(side_effect=lambda claim, report, directory: report)
        self.claim = {'task': {'taskId': 'task', 'spec': {'adapter': 'codex'}},
                      'attempt': {'taskId': 'task', 'attemptId': 'attempt', 'generation': 1}}

    def test_unknown_health_waits_and_does_not_consume_a_failure_retry(self):
        unknown = {'status': 'unknown', 'available': False, 'revision': 1}
        ready = {'status': 'ready', 'available': True, 'revision': 2,
                 'harness': 'codex', 'command': ['fixture-codex']}
        old_failure_with_unknown_probe = {'status': 'unhealthy', 'available': False, 'revision': 2,
                                         'lastProbe': {'status': 'unknown', 'reasonCode': 'scan-timeout'}}
        self.worker.client.call.side_effect = [{'harness': unknown}, {'harness': old_failure_with_unknown_probe}, {'harness': ready}]
        self.worker._execute_selected = Mock(return_value={'status': 'ok'})
        claim = self.claim
        result = self.worker._execute_guarded(claim, claim['task'], claim['task']['spec'],
                                              claim['attempt'], self.root, {})
        self.assertEqual(result['status'], 'ok')
        self.worker.receipt.assert_not_called()
        calls = self.worker.client.call.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(not call.args[1]['retry'] for call in calls))
        self.assertEqual(len({call.args[1]['commandId'] for call in calls}), 3)
        self.worker._execute_selected.assert_called_once()

    def test_blocked_prepare_keeps_renewing_before_native_start(self):
        entered = threading.Event()
        release = threading.Event()
        renewed = threading.Event()
        ready = {'status': 'ready', 'available': True, 'revision': 1,
                 'harness': 'codex', 'command': ['fixture-codex']}

        def call(operation, params):
            if operation == 'task_get':
                return {'task': {'cancelRequested': False}}
            self.assertEqual(operation, 'harness_prepare')
            entered.set()
            self.assertTrue(release.wait(12), 'the test releases the blocked prepare')
            return {'harness': ready}

        self.worker.client.call.side_effect = call
        self.worker.client.renew.side_effect = lambda *args: (renewed.set() or {})
        def selected(claim, task, spec, attempt, directory, holder):
            self.assertTrue(holder['preflightRenewal'].is_alive(),
                            'the lease stays supervised until native handle creation')
            return {'status': 'ok'}
        self.worker._execute_selected = Mock(side_effect=selected)
        result = []
        failure = []
        thread = threading.Thread(target=lambda: self._run_guarded(result, failure))
        thread.start()
        try:
            self.assertTrue(entered.wait(2), 'prepare should enter the service call')
            self.assertTrue(renewed.wait(12), 'lease should renew while prepare is blocked')
            self.worker._execute_selected.assert_not_called()
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failure, [])
        self.assertEqual(result[0]['status'], 'ok')

    def test_cancel_between_unknown_and_next_prepare_returns_cancelled(self):
        cancelled = threading.Event()
        unknown = {'status': 'unknown', 'available': False, 'revision': 1}
        operations = []

        def call(operation, params):
            operations.append(operation)
            if operation == 'task_get':
                return {'task': {'cancelRequested': cancelled.is_set(), 'stopInitiator': 'console'}}
            self.assertEqual(operation, 'harness_prepare')
            if not cancelled.is_set():
                return {'harness': unknown}
            raise BoardError('ATTEMPT_FINISHED', 'cancel arrived before prepare')

        self.worker.client.call.side_effect = call
        self.worker.stop.wait.side_effect = lambda _: cancelled.set()
        self.worker._execute_selected = Mock()
        result = self.worker._execute_guarded(self.claim, self.claim['task'], self.claim['task']['spec'],
                                              self.claim['attempt'], self.root, {})
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(result['terminationReason'], 'console-stop')
        self.assertTrue(result['shutdownConfirmed'])
        self.worker._execute_selected.assert_not_called()
        self.assertIn('task_get', operations)

    def test_cancel_during_blocked_prepare_never_stops_an_absent_handle(self):
        entered = threading.Event()
        release = threading.Event()
        cancelled = threading.Event()
        observed = threading.Event()

        def call(operation, params):
            if operation == 'task_get':
                if cancelled.is_set():
                    observed.set()
                return {'task': {'cancelRequested': cancelled.is_set(), 'stopInitiator': 'console'}}
            self.assertEqual(operation, 'harness_prepare')
            entered.set()
            self.assertTrue(release.wait(3), 'the test releases the blocked prepare')
            raise BoardError('ATTEMPT_FINISHED', 'cancelled while preparing')

        self.worker.client.call.side_effect = call
        self.worker._execute_selected = Mock()
        result = []
        failure = []
        with patch.object(_Renewal, 'CANCEL_POLL_SECONDS', 0.01), \
                patch('hey_my_buddy.buddy.runtime.worker.role_seam.worker_stop') as native_stop:
            thread = threading.Thread(target=lambda: self._run_guarded(result, failure))
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                cancelled.set()
                self.assertTrue(observed.wait(2), 'renewal should observe the durable cancel during prepare')
            finally:
                release.set()
                thread.join(3)
            self.assertFalse(thread.is_alive())
            native_stop.assert_not_called()
        self.assertEqual(failure, [])
        self.assertEqual(result[0]['status'], 'cancelled')
        self.assertEqual(result[0]['terminationReason'], 'console-stop')
        self.worker._execute_selected.assert_not_called()

    def test_finished_attempt_without_cancel_remains_an_error(self):
        def call(operation, params):
            if operation == 'task_get':
                return {'task': {'cancelRequested': False, 'status': 'failed'}}
            raise BoardError('ATTEMPT_FINISHED', 'attempt already finished')

        self.worker.client.call.side_effect = call
        with self.assertRaises(BoardError) as caught:
            self.worker._execute_guarded(self.claim, self.claim['task'], self.claim['task']['spec'],
                                         self.claim['attempt'], self.root, {})
        self.assertEqual(caught.exception.code, 'ATTEMPT_FINISHED')
        self.worker.receipt.assert_not_called()

    def _run_guarded(self, result, failure):
        try:
            result.append(self.worker._execute_guarded(self.claim, self.claim['task'], self.claim['task']['spec'],
                                                       self.claim['attempt'], self.root, {}))
        except BaseException as error:
            failure.append(error)

    def test_transport_wait_expiry_replays_the_same_control_request(self):
        ready = {'status': 'ready', 'available': True, 'revision': 1,
                 'harness': 'codex', 'command': ['fixture-codex']}
        self.worker.client.call.side_effect = [BoardError('SERVICE_UNAVAILABLE', 'wait expired'), {'harness': ready}]
        self.worker._execute_selected = Mock(return_value={'status': 'ok'})
        claim = self.claim
        self.worker._execute_guarded(claim, claim['task'], claim['task']['spec'], claim['attempt'], self.root, {})
        first, second = self.worker.client.call.call_args_list
        self.assertEqual(first.args, second.args)
        self.worker.receipt.assert_not_called()

    def test_router_preparation_budget_uses_no_child_proof_and_deadline_receipt(self):
        self.claim['task']['spec']['adapter'] = 'decision'
        error = BoardError('router-budget-exhausted', 'frozen input used the budget')
        error.owned_popen_evidence = ProcessCreationEvidence(created=False, shutdown_confirmed=True, exit_code=None)
        def exhausted(claim, task, spec, attempt, directory, holder):
            holder['start_invoked'] = True
            raise error
        self.worker._execute_guarded = exhausted
        self.worker.state_dir = self.root
        self.worker.spool = Mock()
        self.worker.spool.read.return_value = None
        self.worker.live = Mock()
        self.worker._release_controller_endpoint = Mock()
        self.worker._cleanup_attempt_credentials = Mock()
        self.worker.log = Mock()
        result = self.worker.execute(self.claim)
        self.assertTrue(result['shutdownConfirmed'])
        self.assertEqual(result['terminationReason'], TERMINATION_DEADLINE)
        self.assertEqual(result['result']['code'], 'router-budget-exhausted')
        self.assertIs(result['result']['modelStarted'], False)
        self.assertEqual(result['result']['stopEvidence'], {
            'shutdownConfirmed': True, 'native': {'shutdownConfirmed': True}})
