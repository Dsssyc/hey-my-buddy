"""Cold start must prove local authority before creating a daemon."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.protocol import transport


class LocalLaunchTests(unittest.TestCase):
    def test_local_socket_denial_refuses_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'state'
            with mock.patch('hey_my_buddy.protocol.transport.socket.socket') as socket_factory, \
                    mock.patch('hey_my_buddy.protocol.transport.subprocess.Popen') as spawn:
                socket_factory.return_value.bind.side_effect = PermissionError('sandbox')
                with self.assertRaises(transport.ServiceError) as caught:
                    transport.ensure_service(directory)
            self.assertEqual(caught.exception.code, 'LAUNCH_ACCESS_DENIED')
            self.assertEqual(caught.exception.details['io_operation'], 'bind')
            spawn.assert_not_called()

    def test_unreachable_recorded_service_does_not_spawn_another(self):
        from hey_my_buddy import locking
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'control.json').write_text('{}', encoding="utf-8", newline="\n")
            owner = os.open(directory / 'board-owner.lock', os.O_CREAT | os.O_RDWR, 0o600)
            locking.lock(owner)
            try:
                with mock.patch('hey_my_buddy.protocol.transport._attach_read_only', return_value=None), \
                        mock.patch('hey_my_buddy.protocol.transport.subprocess.Popen') as spawn:
                    with self.assertRaises(transport.ServiceError) as caught:
                        transport.ensure_service(directory)
            finally:
                os.close(owner)
            self.assertEqual(caught.exception.code, 'SERVICE_UNAVAILABLE')
            self.assertIn('lifetime locks', caught.exception.message)
            spawn.assert_not_called()

    def test_stale_endpoint_is_reclaimed_only_when_owner_locks_are_free(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            endpoint = directory / 'control.json'
            endpoint.write_text('{}', encoding="utf-8", newline="\n")
            transport._retire_stale_endpoint(directory)
            self.assertFalse(endpoint.exists())


if __name__ == '__main__':
    unittest.main()
