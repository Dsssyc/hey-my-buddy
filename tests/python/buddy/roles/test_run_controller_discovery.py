"""Discovery failure evidence crosses the existing controller receipt."""
import errno
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hey_my_buddy.buddy.roles import run_controller
from hey_my_buddy.buddy.roles import run_execution
from hey_my_buddy.buddy.harnesses.runtime_selection import bound
from hey_my_buddy.buddy.harnesses import controller
from hey_my_buddy.protocol import process_controller, process_identity
from hey_my_buddy.errors import BoardError
from buddy.harnesses.test_controller import (
    NATIVE_STOPPED, NATIVE_UNKNOWN, controller_log_close_failure, partial_log_open_failure,
)


class ControllerDiscoveryStopTests(unittest.TestCase):
    def receipt(self, stopped):
        error = ValueError('fixture native read failed')
        if stopped is not None:
            error.discovery_shutdown_confirmed = stopped
        def read(**kwargs):
            raise error
        control = {'operation': 'discover',
                   'arguments': {}, 'harness': 'zcode', 'cwd': '/fixture',
                   'privateRoot': '/fixture', 'nativeRoot': '/fixture/native', 'timeoutSeconds': 1}
        with patch('hey_my_buddy.buddy.harnesses.registry.run_seam', return_value=SimpleNamespace(run_discovery=read)):
            frame, code = run_controller.execute(control, threading.Event())
        self.assertEqual(code, 1)
        return json.loads(frame)

    def test_failed_read_with_confirmed_stop_is_published(self):
        self.assertIs(self.receipt(True)['processState']['shutdownConfirmed'], True)

    def test_failed_read_with_unconfirmed_stop_is_published(self):
        self.assertIs(self.receipt(False)['processState']['shutdownConfirmed'], False)

    def test_failed_read_without_evidence_never_implies_stop(self):
        self.assertIs(self.receipt(None)['processState']['shutdownConfirmed'], False)


class PrivateControllerDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='hey-my-buddy-controller-launch-failure-',
                                                dir=os.environ.get('HMB_CHECKS_TMPDIR', tempfile.gettempdir()))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def discover(self):
        environment = {key: value for key, value in os.environ.items()
                       if key in ('PYTHONPATH', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'WINDIR')}
        environment.update(HOME=str(self.root), USERPROFILE=str(self.root), PATH=str(self.root / 'empty-path'), HMB_DEV_SOURCE='1',
                           HMB_STATE_DIR=str(self.root / 'state'), HMB_RUNTIME_ROOT=str(self.root / 'runtime'),
                           TMPDIR=str(self.root))
        record = {'harness': 'codex', 'status': 'unavailable', 'command': [],
                  'reason': {'code': 'harness.not-found', 'params': {'operation': 'probe'}}}
        with patch.dict(os.environ, environment, clear=True), \
             patch.object(tempfile, 'tempdir', str(self.root)), \
             bound([record], environment=environment):
            return run_execution.discover_models('codex', state_dir=Path(os.environ["HMB_STATE_DIR"]))

    def test_native_metadata_wait_is_unknown_and_the_next_read_can_complete(self):
        handle = SimpleNamespace(wait=lambda seconds: 0, terminate=lambda **kwargs: None)
        unknown = SimpleNamespace(payload={'status': 'unknown', 'code': 'deadline'}, exit_code=0, stop_confirmed=True)
        catalog = {'source': 'fixture', 'providers': [], 'discoveries': [
            {'harness': 'codex', 'status': 'complete', 'accountStatus': 'confirmed'}]}
        ready = SimpleNamespace(payload={'status': 'ok', 'catalog': catalog}, exit_code=0, stop_confirmed=True)
        with patch.object(run_execution, 'launch_controller', return_value=handle), \
             patch.object(run_execution, 'collect_controller', side_effect=[unknown, ready]):
            self.assertEqual(self.discover()['discoveries'][0]['status'], 'unknown')
            self.assertEqual(self.discover(), catalog)

    def test_outer_wait_expiry_with_confirmed_stop_is_unknown(self):
        from unittest.mock import Mock
        handle = Mock()
        handle.wait.return_value = None
        collection = SimpleNamespace(payload={'status': 'error', 'code': 'user-cancel',
                                              'processState': {'shutdownConfirmed': True}},
                                     exit_code=-15, stop_confirmed=True)
        with patch.object(run_execution, 'launch_controller', return_value=handle), \
             patch.object(run_execution, 'collect_controller', return_value=collection):
            result = self.discover()
        self.assertEqual(result['discoveries'][0]['status'], 'unknown')
        handle.terminate.assert_called_once_with(grace_seconds=8)

    def test_real_metadata_refusal_remains_a_failure(self):
        handle = SimpleNamespace(wait=lambda seconds: 1, terminate=lambda **kwargs: None)
        collection = SimpleNamespace(payload={'status': 'error', 'error': 'native refused'}, exit_code=1, stop_confirmed=True)
        with patch.object(run_execution, 'launch_controller', return_value=handle), \
             patch.object(run_execution, 'collect_controller', return_value=collection), \
             self.assertRaises(BoardError) as caught:
            self.discover()
        self.assertEqual(caught.exception.code, 'HARNESS_UNAVAILABLE')

    def test_errors_after_outer_wait_expiry_are_our_termination_and_unknown(self):
        from unittest.mock import Mock
        handle = Mock()
        handle.wait.return_value = None
        collection = SimpleNamespace(payload={'status': 'error', 'code': 'native-rpc-error',
                                              'error': 'native explicitly refused the request'},
                                     exit_code=1, stop_confirmed=True)
        with patch.object(run_execution, 'launch_controller', return_value=handle), \
             patch.object(run_execution, 'collect_controller', return_value=collection):
            result = self.discover()
        self.assertEqual(result['discoveries'][0]['status'], 'unknown')
        handle.terminate.assert_called_once_with(grace_seconds=8)

    def test_emfile_before_spawn_keeps_confirmed_stop_through_role(self):
        error = OSError(errno.EMFILE, 'fixture controller logs exhausted')
        with patch.object(process_controller, 'open_logs', side_effect=error), \
             patch.object(process_controller, 'owned_popen', wraps=process_controller.owned_popen) as spawn:
            with self.assertRaises(OSError) as caught:
                self.discover()
        spawn.assert_not_called()
        self.assertIs(caught.exception, error)
        self.assertIs(error.discovery_shutdown_confirmed, True)
        self.assertEqual(list((self.root / 'state' / 'tmp').glob('hey-my-buddy-codex-catalog-*')), [])

    def test_launch_prepare_failure_keeps_no_child_stop_through_role(self):
        error = ValueError('fixture launch command preparation failed')

        def prepare():
            raise error

        def launch(**kwargs):
            kwargs['prepare'] = prepare
            return controller.launch_controller(**kwargs)

        with patch.object(run_execution, 'launch_controller', launch), \
             patch.object(process_controller, 'owned_popen', wraps=process_controller.owned_popen) as spawn:
            with self.assertRaises(ValueError) as caught:
                self.discover()
        spawn.assert_not_called()
        self.assertIs(caught.exception, error)
        self.assertIs(error.discovery_shutdown_confirmed, True)
        self.assertEqual(list((self.root / 'state' / 'tmp').glob('hey-my-buddy-codex-catalog-*')), [])

    def test_partial_log_open_failure_closes_stdout_and_keeps_role_stop(self):
        with partial_log_open_failure() as state, \
             patch.object(process_controller, 'owned_popen', wraps=process_controller.owned_popen) as spawn:
            with self.assertRaises(OSError) as caught:
                self.discover()
            spawn.assert_not_called()
            self.assertIs(caught.exception, state['error'])
            self.assertIs(caught.exception.discovery_shutdown_confirmed, True)
            self.assertFalse(caught.exception.owned_popen_evidence.created)
            with self.assertRaises(OSError) as closed:
                os.fstat(state['stdout'])
            self.assertEqual(closed.exception.errno, errno.EBADF)
            self.assertEqual(list((self.root / 'state' / 'tmp').glob('hey-my-buddy-codex-catalog-*')), [])

    def assert_created_failure(self, receipt, *, stopped):
        with controller_log_close_failure(receipt=receipt) as state:
            with self.assertRaises(OSError) as caught:
                self.discover()
            self.assertIs(caught.exception, state['error'])
            evidence = caught.exception.owned_popen_evidence
            self.assertTrue(evidence.created)
            self.assertIs(evidence.shutdown_confirmed, True)
            self.assertIsNotNone(state['process'].poll())
            self.assertEqual(state['observations'], 1)
            self.assertIs(caught.exception.discovery_shutdown_confirmed, stopped)
            self.assertEqual(len(list((self.root / 'state' / 'tmp').glob('hey-my-buddy-codex-catalog-*'))), 0 if stopped else 1)

    def test_created_fd_failure_without_native_receipt_keeps_role_unknown(self):
        self.assert_created_failure(None, stopped=False)

    def test_created_fd_failure_with_native_false_keeps_role_unknown(self):
        self.assert_created_failure(NATIVE_UNKNOWN, stopped=False)

    def test_created_fd_failure_with_both_stops_confirmed_keeps_role_stop(self):
        self.assert_created_failure(NATIVE_STOPPED, stopped=True)

    def test_created_fd_failure_with_invalid_native_receipt_keeps_role_unknown(self):
        for receipt in (b'{broken', b'[]', b'{}', b'{"processState":null}',
                        b'{"processState":[]}', b'{"processState":{"shutdownConfirmed":1}}',
                        b'{"processState":{"shutdownConfirmed":"true"}}'):
            with self.subTest(receipt=receipt):
                previous = set((self.root / 'state' / 'tmp').glob('hey-my-buddy-codex-catalog-*'))
                with controller_log_close_failure(receipt=receipt) as state:
                    with self.assertRaises(OSError) as caught:
                        self.discover()
                    self.assertIs(caught.exception, state['error'])
                    self.assertIs(caught.exception.owned_popen_evidence.shutdown_confirmed, True)
                    self.assertIs(caught.exception.discovery_shutdown_confirmed, False)
                    self.assertEqual(state['observations'], 1)
                    self.assertEqual(len(set((self.root / 'state' / 'tmp').glob('hey-my-buddy-codex-catalog-*')) - previous), 1)

    def test_created_fd_failure_with_unknown_cleanup_retains_role_directory(self):
        with controller_log_close_failure(receipt=NATIVE_STOPPED, observation_blocked=True) as state:
            with self.assertRaises(OSError) as caught:
                self.discover()
            self.assertIs(caught.exception, state['error'])
            self.assertTrue(caught.exception.owned_popen_evidence.created)
            self.assertIs(caught.exception.owned_popen_evidence.shutdown_confirmed, False)
            self.assertIs(caught.exception.discovery_shutdown_confirmed, False)
            self.assertEqual(state['observations'], 0)
            self.assertEqual(len(list((self.root / 'state' / 'tmp').glob('hey-my-buddy-codex-catalog-*'))), 1)

    def test_unregistered_controller_with_native_true_and_outer_unknown_keeps_role_unknown(self):
        error = ValueError('fixture handle registration failed')
        with controller_log_close_failure(log=None, receipt=NATIVE_STOPPED, observation_blocked=True) as state, \
             patch.object(process_identity, '_creation_time', side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                self.discover()
            self.assertIs(caught.exception.__cause__, error)
            self.assertTrue(caught.exception.owned_popen_evidence.created)
            self.assertIs(caught.exception.owned_popen_evidence.shutdown_confirmed, False)
            self.assertIs(caught.exception.discovery_shutdown_confirmed, False)
            self.assertIsNotNone(state['process'].poll())
            self.assertEqual(state['observations'], 0)
            self.assertEqual(len(list((self.root / 'state' / 'tmp').glob('hey-my-buddy-codex-catalog-*'))), 1)

    def test_simulated_windows_creation_receipt_alone_never_confirms_native_stop(self):
        from buddy.runtime.test_windows_process import FakeAPI, FakeProcess
        from hey_my_buddy.protocol import owned_process as windows_process

        for stopped in (True, False):
            with self.subTest(outer_stopped=stopped):
                process = FakeProcess()
                api = FakeAPI(process)
                api.fail_at = 'assign'
                if not stopped:
                    def refuse_kill():
                        raise OSError('fixture child cannot be killed')
                    process.kill = refuse_kill
                original_spawn = windows_process.owned_popen

                def spawn(*args, **kwargs):
                    return original_spawn(*args, windows_api=api, **kwargs)

                previous = set((self.root / 'state' / 'tmp').glob('hey-my-buddy-codex-catalog-*'))
                with patch.object(windows_process.subprocess, 'Popen', return_value=process), \
                     patch.object(process_controller, 'owned_popen', spawn):
                    with self.assertRaises((OSError, RuntimeError)) as caught:
                        self.discover()
                self.assertTrue(caught.exception.owned_popen_evidence.created)
                self.assertIs(caught.exception.owned_popen_evidence.shutdown_confirmed, stopped)
                self.assertIs(caught.exception.discovery_shutdown_confirmed, False)
                self.assertNotIn(('resume', process._handle), api.events)
                self.assertEqual(len(set((self.root / 'state' / 'tmp').glob('hey-my-buddy-codex-catalog-*')) - previous), 1)

    def test_path_without_harness_keeps_no_child_fact_through_actual_controller(self):
        with tempfile.TemporaryDirectory(prefix='hey-my-buddy-controller-no-harness-',
                                         dir=os.environ.get('HMB_CHECKS_TMPDIR', tempfile.gettempdir())) as temporary:
            root = Path(temporary)
            empty_path = root / 'empty-path'
            empty_path.mkdir()
            self.assertEqual(list(empty_path.iterdir()), [])
            environment = {key: value for key, value in os.environ.items()
                           if key in ('PYTHONPATH', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'WINDIR')}
            environment.update(HOME=str(root), USERPROFILE=str(root), PATH=str(empty_path), HMB_DEV_SOURCE='1',
                               HMB_STATE_DIR=str(root / 'state'), HMB_RUNTIME_ROOT=str(root / 'runtime'),
                               TMPDIR=str(root))
            record = {'harness': 'codex', 'status': 'unavailable', 'command': [],
                      'reason': {'code': 'harness.not-found', 'params': {'operation': 'probe'}}}
            with patch.dict(os.environ, environment, clear=True), bound([record], environment=environment):
                with self.assertRaises(BoardError) as caught:
                    run_execution.discover_models('codex', state_dir=Path(os.environ["HMB_STATE_DIR"]))
            self.assertEqual(caught.exception.code, 'HARNESS_UNAVAILABLE')
            self.assertEqual(caught.exception.message, 'The selected harness is unavailable')
            self.assertIs(caught.exception.discovery_shutdown_confirmed, True)
            self.assertEqual(list(root.glob('hey-my-buddy-codex-catalog-*')), [])
