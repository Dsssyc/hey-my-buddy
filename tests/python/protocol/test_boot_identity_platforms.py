"""Stable boot identity APIs on platforms not available to this test machine."""
from __future__ import annotations

import ctypes
import io
import unittest
import uuid
from unittest.mock import Mock, patch

from hey_my_buddy.protocol import process_identity


BOOT = '00000000-0000-4000-8000-000000000001'


class StableBootIdentityTests(unittest.TestCase):
    def test_linux_reads_kernel_boot_uuid(self):
        source = Mock(return_value=io.StringIO(BOOT + '\n'))
        with patch.object(process_identity.sys, 'platform', 'linux'), patch('builtins.open', source):
            self.assertEqual(process_identity.boot_id(), 'linux:' + BOOT)
        source.assert_called_once_with('/proc/sys/kernel/random/boot_id', encoding='ascii')

    def test_macos_reads_boot_session_uuid_with_sized_sysctl(self):
        raw = BOOT.upper().encode() + b'\0'
        def sysctl(name, output, size, _new, _length):
            self.assertEqual(name, b'kern.bootsessionuuid')
            if output is None:
                size._obj.value = len(raw)
            else:
                ctypes.memmove(output, raw, len(raw))
            return 0
        library = Mock()
        library.sysctlbyname = Mock(side_effect=sysctl)
        with patch.object(process_identity.sys, 'platform', 'darwin'), patch.object(ctypes, 'CDLL', return_value=library):
            self.assertEqual(process_identity.boot_id(), 'macos:' + BOOT)
        self.assertEqual(library.sysctlbyname.call_count, 2)

    def test_windows_reads_boot_environment_guid_and_rejects_api_failure(self):
        def query(kind, output, size, _returned):
            self.assertEqual(kind, 0x5A)
            self.assertEqual(size, 32)
            ctypes.memmove(output, uuid.UUID(BOOT).bytes_le + bytes(16), 32)
            return 0
        library = Mock()
        library.NtQuerySystemInformation = Mock(side_effect=query)
        with patch.object(process_identity.sys, 'platform', 'win32'), \
             patch.object(ctypes, 'WinDLL', return_value=library, create=True):
            self.assertEqual(process_identity.boot_id(), 'windows:' + BOOT)
            library.NtQuerySystemInformation.side_effect = None
            library.NtQuerySystemInformation.return_value = -1
            with self.assertRaises(OSError):
                process_identity.boot_id()
