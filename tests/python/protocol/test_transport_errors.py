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


def core_failure(kind: str | None) -> CoreError:
    failure = CoreError('transport', 'native connection failed')
    failure.transport_kind = 'ipc'
    failure.io_kind = kind
    failure.raw_os_error = errno.EACCES if kind == 'permission_denied' else errno.ENOENT
    failure.io_operation = 'connect'
    return failure


class StructuredRpcFailureTests(unittest.TestCase):
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

    @unittest.skipIf(os.name == 'nt', 'POSIX AF_UNIX cleanup')
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
            listener = Mock()
            connector = Mock()
            listener.bind.side_effect = lambda address: Path(address).touch()

            def replace_then_fail(_address):
                socket_directory.rename(relocated)
                socket_directory.symlink_to(unrelated, target_is_directory=True)
                raise FileNotFoundError(errno.ENOENT, 'path replaced')

            connector.connect.side_effect = replace_then_fail
            with patch.object(transport.tempfile, 'mkdtemp', return_value=str(socket_directory)), \
                 patch.object(transport.socket, 'socket', side_effect=[listener, connector]), \
                 self.assertRaises(transport.ServiceError) as caught:
                transport._cold_start_preflight(root / 'state')
            self.assertEqual(caught.exception.code, 'LAUNCH_IO_ERROR')
            self.assertEqual(caught.exception.details,
                             {'raw_os_error': errno.ENOENT, 'io_operation': 'connect'})
            self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep')
            self.assertFalse((relocated / 's').exists())
            self.assertTrue(relocated.is_dir())
            self.assertTrue(socket_directory.is_symlink())


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
