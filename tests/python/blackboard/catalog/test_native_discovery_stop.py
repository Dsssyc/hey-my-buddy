"""Native discovery retains actual process creation and shutdown evidence."""
from hey_my_buddy.blackboard.service.live_registry import LiveRegistry
import errno
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hey_my_buddy.blackboard.catalog import catalog
from hey_my_buddy.blackboard.service.service import BoardService
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.buddy.harnesses.runtime_selection import bound
from hey_my_buddy.buddy.harnesses.codex import native_run
from hey_my_buddy.buddy.harnesses import controller, discovery, version
from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.protocol.owned_process import owned_popen
from hey_my_buddy.buddy.roles import run_execution
from hey_my_buddy.protocol import process_controller
from hey_my_buddy.errors import BoardError
from support import BoardTestCase
from buddy.harnesses.test_controller import (
    NATIVE_STOPPED, NATIVE_UNKNOWN, controller_log_close_failure, partial_log_open_failure,
)


class NativeDiscoveryFixture:
    def setUp(self):
        super().setUp()
        self.board = BoardStore(self.directory / 'native-stop')
        self.board.initialize()
        self.live_registry = LiveRegistry()
        self.service = BoardService(self.board, live_registry=self.live_registry, token='host', control={},
                                    on_stop=lambda p: {}, on_restart=lambda p: {}, automatic_discovery=False)
        self.addCleanup(self.service.harnesses.close)
        self.record = self.service.harnesses.get('codex')
        self.payload = {'source': 'fixture-native', 'providers': [], 'warnings': [],
                        'discoveries': [{'harness': 'codex', 'status': 'complete', 'accountStatus': 'confirmed'}]}


class CatalogNativeStopTests(NativeDiscoveryFixture, BoardTestCase):
    def invoke(self, value):
        def read():
            if isinstance(value, Exception):
                raise value
            return value() if callable(value) else value
        instance = SimpleNamespace(model_discovery=True, discovery_available=lambda: (True, None), discover_models=read)
        with patch('hey_my_buddy.buddy.harnesses.registry.adapters', return_value={'codex': instance}), \
             patch.object(catalog, '_override', return_value=None), bound([self.record]):
            return catalog.discover(directory=self.board.directory, harnesses=[self.record])

    def test_foreign_harness_return_after_stop_is_rejected(self):
        payload = {**self.payload, 'providers': [{'harness': 'zcode', 'provider': 'fixture',
                                                'displayName': 'Fixture', 'models': []}],
                   'discoveries': [{'harness': 'zcode', 'status': 'complete', 'accountStatus': 'confirmed'}]}
        self.assertEqual(catalog.canonical_payload(payload)['providers'][0]['harness'], 'zcode')
        result = self.invoke(payload)
        self.assertEqual(result['discoveries'][0]['reason'],
                         {'code': 'catalog.discovery-failed', 'params': {'harness': 'codex', 'errorCode': 'CATALOG_INVALID'}})

    def test_codex_directory_creation_failure_preserves_stop_evidence(self):
        error = OSError('fixture catalog directory refused')
        with patch.object(run_execution.tempfile, 'mkdtemp', side_effect=error):
            with self.assertRaises(OSError) as caught:
                self.invoke(lambda: run_execution.discover_models('codex'))
        self.assertIs(caught.exception, error)
        self.assertIs(caught.exception.discovery_shutdown_confirmed, True)

    def controller_discovery(self):
        # The actual private controller may only encounter an unavailable synthetic harness.
        self.record.update(status='unavailable', command=[], reasonCode='HARNESS_UNAVAILABLE',
                           remedy='fixture has no native harness')
        with patch.dict(os.environ, {'HOME': str(self.directory), 'CODEX_HOME': str(self.directory / '.codex'),
                                     'HMB_CODEX_CLI': ''}):
            return self.invoke(lambda: run_execution.discover_models('codex'))

    def test_controller_log_emfile_without_spawn_preserves_stop_evidence(self):
        error = OSError(errno.EMFILE, 'fixture controller logs exhausted')
        with patch.object(process_controller, 'open_logs', side_effect=error), \
             patch.object(process_controller, 'owned_popen', wraps=process_controller.owned_popen) as spawn:
            with self.assertRaises(OSError) as caught:
                self.controller_discovery()
        spawn.assert_not_called()
        self.assertIs(caught.exception, error)
        self.assertIs(error.discovery_shutdown_confirmed, True)

    def test_controller_preparation_failure_preserves_stop_evidence(self):
        error = ValueError('fixture controller environment failed')
        with patch('hey_my_buddy.buddy.harnesses.runtime_selection.controller_environment', side_effect=error), \
             patch.object(process_controller, 'owned_popen', wraps=process_controller.owned_popen) as spawn:
            with self.assertRaises(ValueError) as caught:
                self.controller_discovery()
        spawn.assert_not_called()
        self.assertIs(caught.exception, error)
        self.assertIs(error.discovery_shutdown_confirmed, True)

    def test_controller_partial_log_open_failure_closes_fd_and_preserves_stop_evidence(self):
        with partial_log_open_failure() as state, \
             patch.object(process_controller, 'owned_popen', wraps=process_controller.owned_popen) as spawn:
            with self.assertRaises(OSError) as caught:
                self.controller_discovery()
            spawn.assert_not_called()
            self.assertIs(caught.exception, state['error'])
            self.assertFalse(caught.exception.owned_popen_evidence.created)
            self.assertIs(caught.exception.discovery_shutdown_confirmed, True)
            with self.assertRaises(OSError) as closed:
                os.fstat(state['stdout'])
            self.assertEqual(closed.exception.errno, errno.EBADF)

    def test_controller_created_fd_failure_without_native_receipt_preserves_unconfirmed_stop(self):
        with controller_log_close_failure() as state:
            with self.assertRaises(OSError) as caught:
                self.controller_discovery()
            self.assertIs(caught.exception, state['error'])
            self.assertIs(caught.exception.discovery_shutdown_confirmed, False)
            self.assertIs(caught.exception.owned_popen_evidence.shutdown_confirmed, True)
            self.assertTrue(caught.exception.owned_popen_evidence.created)
            self.assertIsNotNone(state['process'].poll())
            self.assertEqual(state['observations'], 1)

    def test_controller_native_false_after_outer_stop_keeps_native_child_alive(self):
        native = owned_popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                             cwd=str(self.directory), env=dict(os.environ), start_new_session=True)
        handle = ProcessHandle(native, own_group=True, log_paths={})
        try:
            with controller_log_close_failure(receipt=NATIVE_UNKNOWN) as state:
                with self.assertRaises(OSError) as caught:
                    self.controller_discovery()
                self.assertIs(caught.exception, state['error'])
                self.assertTrue(caught.exception.owned_popen_evidence.created)
                self.assertIs(caught.exception.owned_popen_evidence.shutdown_confirmed, True)
                self.assertIsNotNone(state['process'].poll())
                self.assertIsNone(native.poll())
                self.assertTrue(handle.group_alive())
                self.assertIs(caught.exception.discovery_shutdown_confirmed, False)
                self.assertEqual(state['observations'], 1)
        finally:
            handle.terminate(grace_seconds=0.2)
            self.assertTrue(handle.shutdown_confirmed())

    def test_controller_invalid_native_receipt_after_outer_stop_preserves_unconfirmed_stop(self):
        with controller_log_close_failure(receipt=b'{"processState":null}') as state:
            with self.assertRaises(OSError) as caught:
                self.controller_discovery()
            self.assertIs(caught.exception, state['error'])
            self.assertIs(caught.exception.owned_popen_evidence.shutdown_confirmed, True)
            self.assertIs(caught.exception.discovery_shutdown_confirmed, False)

    def test_controller_native_true_and_outer_stop_preserve_stop_evidence(self):
        with controller_log_close_failure(receipt=NATIVE_STOPPED) as state:
            with self.assertRaises(OSError) as caught:
                self.controller_discovery()
            self.assertIs(caught.exception, state['error'])
            self.assertIs(caught.exception.owned_popen_evidence.shutdown_confirmed, True)
            self.assertIs(caught.exception.discovery_shutdown_confirmed, True)
            self.assertEqual(state['observations'], 1)

    def test_controller_native_false_after_normal_exit_preserves_unconfirmed_stop(self):
        receipt = json.dumps({'status': 'ok', 'catalog': self.payload,
                              'processState': {'shutdownConfirmed': False}}).encode()
        with controller_log_close_failure(log=None, receipt=receipt, exit_after_receipt=True) as state:
            result = self.controller_discovery()
            self.assertEqual(result['discoveries'][0]['reason'],
                             {'code': 'catalog.discovery-failed', 'params': {'harness': 'codex', 'errorCode': 'HARNESS_DISCOVERY_STOP_UNCONFIRMED'}})
            self.assertEqual(result['discoveries'][0]['status'], 'unknown')
            self.assertEqual(state['process'].poll(), 0)

    def test_controller_native_true_after_normal_exit_preserves_stop_evidence(self):
        receipt = json.dumps({'status': 'ok', 'catalog': self.payload,
                              'processState': {'shutdownConfirmed': True}}).encode()
        with controller_log_close_failure(log=None, receipt=receipt, exit_after_receipt=True) as state:
            result = self.controller_discovery()
            self.assertEqual(result['source'], self.payload['source'])
            self.assertEqual(state['process'].poll(), 0)
            self.assertEqual(state['observations'], 1)

    def test_controller_created_fd_failure_with_unknown_cleanup_preserves_unconfirmed_stop(self):
        with controller_log_close_failure(receipt=NATIVE_STOPPED, observation_blocked=True) as state:
            with self.assertRaises(OSError) as caught:
                self.controller_discovery()
            self.assertIs(caught.exception, state['error'])
            self.assertIs(caught.exception.discovery_shutdown_confirmed, False)
            self.assertTrue(caught.exception.owned_popen_evidence.created)
            self.assertIs(caught.exception.owned_popen_evidence.shutdown_confirmed, False)
            self.assertEqual(state['observations'], 1)

    def native_discovery(self):
        return native_run.run_discovery(cwd=str(self.directory), invocation_root=self.directory / 'discovery',
                                        native_root=self.directory / 'native', timeout_seconds=2,
                                        cancelled=lambda: False)

    def private_version_command(self):
        fixture = Path(__file__).resolve().parents[2] / 'buddy/harnesses/codex/fixtures/mock_codex.py'
        script = self.directory / 'diagnostic.py'
        script.write_text("import os, sys\nif '--version' in sys.argv:\n"
                          "    print('fixture diagnostic refused')\n    sys.exit(7)\n"
                          "os.execv(sys.executable, [sys.executable, " + repr(str(fixture)) +
                          ", *sys.argv[1:]])\n", encoding="utf-8", newline="\n")
        self.record.update(status='ready', command=[sys.executable, str(script)])

    def test_codex_version_failure_with_actual_stop_continues_catalog_and_preserves_stop_evidence(self):
        self.private_version_command()
        receipts = []

        def probe_version(*args, **kwargs):
            result = version.probe_version(*args, **kwargs)
            self.assertIsNone(result)
            receipts.append(dict(kwargs['owned'][0]))
            return result

        def read():
            payload = self.native_discovery()
            self.assertEqual(payload['harnessVersion'], 'unknown')
            return payload

        with patch.dict(os.environ, {'HOME': str(self.directory), 'CODEX_HOME': str(self.directory / '.codex'),
                                     'HMB_CODEX_CLI': ''}), \
             patch.object(native_run, 'probe_version', side_effect=probe_version):
            result = self.invoke(read)
        self.assertEqual(receipts, [{'created': True, 'shutdown': True, 'exit_code': 7}])
        self.assertEqual(result['source'], 'codex-native-app-server')
        self.assertEqual(result['harnessVersion'], 'codex:unknown')
        self.assertEqual(result['discoveries'], [{'harness': 'codex', 'status': 'complete',
                                                  'accountStatus': 'confirmed', 'reason': None, 'content': None}])
        self.assertEqual(result['providers'][0]['harness'], 'codex')
        self.assertEqual(result['providers'][0]['provider'], 'openai')
        self.assertEqual([model['id'] for model in result['providers'][0]['models']], ['fixture-model'])

    def test_codex_version_unknown_stop_preserves_unconfirmed_stop(self):
        self.private_version_command()
        reader = discovery._probe_posix

        def observed(process, stop_at, deadline):
            reason, output, stopped = reader(process, stop_at, deadline)
            self.assertTrue(stopped, 'the private version process must actually be reaped')
            self.assertEqual(reason, 'probe-failed')
            self.assertEqual(process.returncode, 7)
            return reason, output, False

        with patch.dict(os.environ, {'HOME': str(self.directory), 'CODEX_HOME': str(self.directory / '.codex'),
                                     'HMB_CODEX_CLI': ''}), \
             patch.object(discovery, '_probe_posix', side_effect=observed) as observation, \
             patch.object(native_run, 'probe_version', wraps=version.probe_version) as shared, \
             patch.object(native_run, 'owned_popen', wraps=native_run.owned_popen) as main:
            with self.assertRaises(version.VersionShutdownError) as caught:
                self.invoke(self.native_discovery)
        observation.assert_called_once()
        shared.assert_called_once()
        main.assert_not_called()
        self.assertEqual(shared.call_args.kwargs['owned'],
                         [{'created': True, 'shutdown': False, 'exit_code': 7}])
        self.assertEqual(caught.exception.code, 'native-shutdown-failed')
        self.assertIs(caught.exception.discovery_shutdown_confirmed, False)
