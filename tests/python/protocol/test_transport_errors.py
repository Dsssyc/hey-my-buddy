"""Client translation of structured C-Two failures and cold-start probes."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from c_two.error import ContractMismatch, CoreError

from hey_my_buddy import locking
from hey_my_buddy.protocol import transport

CLEAR_SHUTDOWN = {'completed': True, 'runtime_barrier_error': None, 'route_close_error': None,
                  'ipc_client_close_error': None, 'relay_errors': [], 'route_outcomes': []}


def core_failure(kind: str | None) -> CoreError:
    failure = CoreError('transport', 'native connection failed')
    failure.transport_kind = 'ipc'
    failure.io_kind = kind
    failure.raw_os_error = errno.EACCES if kind == 'permission_denied' else errno.ENOENT
    failure.io_operation = 'connect'
    return failure


class StructuredRpcFailureTests(unittest.TestCase):
    def test_namespace_and_protocol_failures_retain_native_diagnostics(self):
        for category, fields in (
                ('lifecycle', {'endpoint_unverified_reason': 'unsupported-namespace-format'}),
                ('protocol_version_mismatch', {'local_protocol_version': 14, 'peer_protocol_version': 15})):
            with self.subTest(category=category):
                error = CoreError(category, 'native endpoint refused')
                for key, value in fields.items():
                    setattr(error, key, value)
                failure = transport.local_rpc_error(error)
                self.assertEqual(failure.details, {'category': category, **fields})

    def test_connection_and_operation_map_only_the_retryable_local_causes(self):
        endpoint = {'address': 'ipc://fixture', 'token': 'fixture'}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for kind, code in (('permission_denied', 'LAUNCH_ACCESS_DENIED'),
                               ('not_found', 'SERVICE_UNAVAILABLE'),
                               ('connection_refused', 'SERVICE_UNAVAILABLE'),
                               ('other', 'C_TWO_TRANSPORT')):
                with self.subTest(kind=kind):
                    failure = core_failure(kind)
                    with patch.object(transport.rpc_config, 'configure_client'), \
                         patch.object(transport.cc, 'connect', side_effect=failure), \
                         self.assertRaises(transport.ServiceError) as caught:
                        with transport._connection(endpoint, 'control', directory):
                            pass
                    self.assertEqual(caught.exception.code, code)
                    self.assertEqual(caught.exception.details['io_kind'], kind)
                    self.assertEqual(caught.exception.details['raw_os_error'], failure.raw_os_error)
                    self.assertEqual(caught.exception.details['io_operation'], 'connect')

                    service = Mock()
                    service.ping.side_effect = failure
                    with self.assertRaises(transport.ServiceError) as caught:
                        transport._invoke(service, endpoint, 'ping', {})
                    self.assertEqual(caught.exception.code, code)
                    self.assertEqual(caught.exception.details['io_kind'], kind)

    def test_only_absent_listener_enters_cold_start(self):
        endpoint = {'address': 'ipc://fixture', 'token': 'fixture'}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for kind, expected in (('permission_denied', 'LAUNCH_ACCESS_DENIED'),
                                   ('other', 'C_TWO_TRANSPORT')):
                with self.subTest(kind=kind), \
                     patch.object(transport, '_read_endpoint', return_value=endpoint), \
                     patch.object(transport.private_dirs, 'linked', return_value=True), \
                     patch.object(transport.rpc_config, 'configure_client'), \
                     patch.object(transport.cc, 'connect', side_effect=core_failure(kind)), \
                     patch.object(transport, '_cold_start_preflight') as preflight:
                    with self.assertRaises(transport.ServiceError) as caught:
                        transport._ensure_service(directory)
                    self.assertEqual(caught.exception.code, expected)
                    preflight.assert_not_called()
            for kind in ('not_found', 'connection_refused'):
                with self.subTest(kind=kind), \
                     patch.object(transport, '_read_endpoint', return_value=endpoint), \
                     patch.object(transport.private_dirs, 'linked', return_value=True), \
                     patch.object(transport.rpc_config, 'configure_client'), \
                     patch.object(transport.cc, 'connect', side_effect=core_failure(kind)), \
                     patch.object(transport, '_cold_start_preflight', side_effect=RuntimeError('cold start reached')) as preflight:
                    with self.assertRaisesRegex(RuntimeError, 'cold start reached'):
                        transport._ensure_service(directory)
                    preflight.assert_called_once_with(directory)

    def test_identity_refusal_without_os_error_never_starts_or_reports_stopped(self):
        # C-Two rejects a Windows peer's identity before sending bytes.
        # That permission denial has no OS errno and is not an absent listener.
        failure = core_failure('permission_denied')
        failure.transport_phase = 'pre_dispatch'
        failure.raw_os_error = None
        endpoint = {'address': 'ipc://fixture', 'token': 'fixture'}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for method in ('health', 'stop', 'restart'):
                with self.subTest(method=method), \
                     patch.object(transport, '_read_endpoint', return_value=endpoint), \
                     patch.object(transport.private_dirs, 'linked', return_value=True), \
                     patch.object(transport.rpc_config, 'configure_client'), \
                     patch.object(transport.cc, 'connect', side_effect=failure), \
                     patch.object(transport, '_cold_start_preflight') as preflight, \
                     patch.object(transport.subprocess, 'Popen') as spawn:
                    with self.assertRaises(transport.ServiceError) as caught:
                        transport._call_service(method, {}, directory)
                    self.assertEqual(caught.exception.code, 'LAUNCH_ACCESS_DENIED')
                    self.assertEqual(caught.exception.details, {
                        'category': 'transport', 'transport_kind': 'ipc',
                        'transport_phase': 'pre_dispatch', 'io_kind': 'permission_denied',
                        'io_operation': 'connect',
                    })
                    preflight.assert_not_called()
                    spawn.assert_not_called()

    def test_non_c_two_errors_propagate_from_both_boundaries(self):
        endpoint = {'address': 'ipc://fixture', 'token': 'fixture'}
        failure = ValueError('programming failure')
        service = Mock()
        service.ping.side_effect = failure
        with self.assertRaises(ValueError) as caught:
            transport._invoke(service, endpoint, 'ping', {})
        self.assertIs(caught.exception, failure)
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(transport.rpc_config, 'configure_client'), \
             patch.object(transport.cc, 'connect', side_effect=failure):
            with self.assertRaises(ValueError) as caught:
                with transport._connection(endpoint, 'control', Path(temporary)):
                    pass
        self.assertIs(caught.exception, failure)

    def test_c_two_contract_failure_keeps_its_stable_code_and_details(self):
        endpoint = {'address': 'ipc://fixture', 'token': 'fixture'}
        service = Mock()
        service.ping.side_effect = ContractMismatch('wrong contract', details={'expected': 'v1'})
        with self.assertRaises(transport.ServiceError) as caught:
            transport._invoke(service, endpoint, 'ping', {})
        self.assertEqual(caught.exception.code, 'C_TWO_CONTRACT_MISMATCH')
        self.assertEqual(caught.exception.details, {'expected': 'v1'})


class PreflightFailureTests(unittest.TestCase):
    def test_permission_failure_preserves_operation_and_errno(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'state'
            denial = PermissionError(errno.EACCES, 'denied')
            with patch.object(transport.private_dirs, 'open_regular_fd', side_effect=denial), \
                 self.assertRaises(transport.ServiceError) as caught:
                transport._cold_start_preflight(directory)
            self.assertEqual(caught.exception.code, 'LAUNCH_ACCESS_DENIED')
            self.assertEqual(caught.exception.details,
                             {'raw_os_error': errno.EACCES, 'io_operation': 'state_write'})

    def test_other_os_failure_preserves_its_cause(self):
        with tempfile.TemporaryDirectory() as temporary:
            failure = OSError(errno.ENOSPC, 'disk full')
            with patch.object(transport.private_dirs, 'open_regular_fd', side_effect=failure), \
                 self.assertRaises(transport.ServiceError) as caught:
                transport._cold_start_preflight(Path(temporary) / 'state')
            self.assertEqual(caught.exception.code, 'LAUNCH_IO_ERROR')
            self.assertEqual(caught.exception.details,
                             {'raw_os_error': errno.ENOSPC, 'io_operation': 'state_write'})
            self.assertIs(caught.exception.__cause__, failure)

    @unittest.skipIf(os.name == 'nt', 'POSIX independent native namespace')
    def test_replaced_temp_path_does_not_redirect_socket_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            socket_directory = root / 'socket'
            socket_directory.mkdir()
            relocated = root / 'relocated'
            unrelated = root / 'unrelated'
            unrelated.mkdir()
            sentinel = unrelated / 's'
            sentinel.write_text('keep', encoding='utf-8')
            runtime = Mock()
            runtime.shutdown.return_value = CLEAR_SHUTDOWN

            def replace_then_fail():
                socket_directory.rename(relocated)
                socket_directory.symlink_to(unrelated, target_is_directory=True)
                raise FileNotFoundError(errno.ENOENT, 'path replaced')

            runtime.start.side_effect = replace_then_fail
            with patch.object(transport.tempfile, 'mkdtemp', return_value=str(socket_directory)), \
                 patch.object(transport.cc, 'Runtime', return_value=runtime), \
                 self.assertRaises(transport.ServiceError) as caught:
                transport._cold_start_preflight(root / 'state')
            self.assertEqual(caught.exception.code, 'LAUNCH_IO_ERROR')
            self.assertEqual(caught.exception.details['io_operation'], 'cleanup')
            self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep')
            self.assertFalse((relocated / 's').exists())
            self.assertTrue(relocated.is_dir())
            self.assertTrue(socket_directory.is_symlink())

    @unittest.skipIf(os.name == 'nt', 'POSIX independent native namespace')
    def test_incomplete_probe_shutdown_keeps_its_namespace(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Mock()
            runtime.shutdown.return_value = {**CLEAR_SHUTDOWN, 'completed': False}
            with patch.object(transport.cc, 'Runtime', return_value=runtime), \
                 self.assertRaises(transport.ServiceError) as caught:
                transport._cold_start_preflight(Path(temporary))
            self.assertEqual(caught.exception.code, 'LAUNCH_IO_ERROR')
            self.assertEqual(caught.exception.details['io_operation'], 'shutdown')
            self.assertTrue(Path(caught.exception.details['path']).is_dir())

    @unittest.skipIf(os.name == 'nt', 'POSIX independent native namespace')
    def test_completed_probe_shutdown_retires_namespace_even_with_errors(self):
        for field, value in (
                ('runtime_barrier_error', 'barrier refused'),
                ('route_close_error', 'route refused'), ('ipc_client_close_error', 'client refused'),
                ('relay_errors', ['relay refused']),
                ('route_outcomes', [{'active_drained': False, 'close_error': None}]),
                ('route_outcomes', [{'active_drained': True, 'close_error': 'close refused'}])):
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as temporary:
                runtime = Mock()
                runtime.shutdown.return_value = {**CLEAR_SHUTDOWN, field: value}
                with patch.object(transport.cc, 'Runtime', return_value=runtime):
                    transport._cold_start_preflight(Path(temporary))
                self.assertFalse(list(Path(temporary).glob('.launch-ipc-*')))

    @unittest.skipIf(os.name == 'nt', 'POSIX independent runtime bind')
    def test_start_failure_retains_its_cause_and_completed_shutdown_errors(self):
        error = core_failure('permission_denied')
        error.io_operation = 'bind'
        outcome = {**CLEAR_SHUTDOWN, 'runtime_barrier_error': 'same bind refusal'}
        runtime = Mock()
        runtime.start.side_effect = error
        runtime.shutdown.return_value = outcome
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(transport.cc, 'Runtime', return_value=runtime), \
                 self.assertRaises(transport.ServiceError) as caught:
                transport._cold_start_preflight(Path(temporary))
            self.assertEqual(caught.exception.code, 'LAUNCH_ACCESS_DENIED')
            self.assertEqual(caught.exception.details['io_operation'], 'bind')
            self.assertEqual(caught.exception.details['raw_os_error'], errno.EACCES)
            self.assertEqual(caught.exception.details['shutdownOutcome'], outcome)
            self.assertIs(caught.exception.__cause__, error)
            self.assertFalse(list(Path(temporary).glob('.launch-ipc-*')))

    @unittest.skipIf(os.name == 'nt', 'POSIX independent runtime namespace')
    def test_public_runtime_probe_cleans_coordination_files_and_leaves_default_domain(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = transport.cc.local_endpoint_context()
            transport._probe_local_ipc(root)
            after = transport.cc.local_endpoint_context()
            self.assertEqual((after.platform, after.namespace_id, after.root),
                             (before.platform, before.namespace_id, before.root))
            self.assertEqual(list(root.iterdir()), [])

    @unittest.skipIf(os.name == 'nt', 'POSIX independent runtime bind')
    def test_public_runtime_bind_denial_reaches_preflight_without_retry(self):
        runtime = Mock()
        runtime.start.side_effect = core_failure('permission_denied')
        runtime.shutdown.return_value = CLEAR_SHUTDOWN
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(transport.cc, 'Runtime', return_value=runtime), \
             self.assertRaises(transport.ServiceError) as caught:
            transport._cold_start_preflight(Path(temporary))
        self.assertEqual(caught.exception.code, 'LAUNCH_ACCESS_DENIED')
        runtime.start.assert_called_once_with()
        runtime.set_server.assert_called_once_with(startup_timeout=transport.rpc_config.SERVICE_STARTUP_TIMEOUT_SECONDS)
        runtime.shutdown.assert_called_once_with(timeout=transport.rpc_config.SERVICE_STARTUP_TIMEOUT_SECONDS)


class EndpointRetirementTests(unittest.TestCase):
    def test_both_lifetime_locks_are_held_when_control_is_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            endpoint = directory / 'control.json'
            endpoint.write_text('{}', encoding='utf-8')
            acquired = []
            original_lock = locking.lock
            original_unlink = Path.unlink

            def observe_lock(fd, *args, **kwargs):
                original_lock(fd, *args, **kwargs)
                acquired.append(fd)

            def observe_unlink(path, *args, **kwargs):
                if path == endpoint:
                    self.assertEqual(len(acquired), 2)
                    for fd in acquired:
                        os.fstat(fd)
                return original_unlink(path, *args, **kwargs)

            with patch.object(transport.locking, 'lock', side_effect=observe_lock), \
                 patch.object(transport.Path, 'unlink', side_effect=observe_unlink, autospec=True):
                transport._retire_stale_endpoint(directory)
            self.assertFalse(endpoint.exists())


if __name__ == '__main__':
    unittest.main()
