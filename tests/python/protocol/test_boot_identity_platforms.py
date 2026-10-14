"""Stable boot identity APIs on platforms not available to this test machine."""
from __future__ import annotations

import ctypes
import io
import os
import sys
import time
import unittest
import uuid
from unittest.mock import Mock, patch

from hey_my_buddy.protocol import process_identity, process_start


BOOT = '00000000-0000-4000-8000-000000000001'


class StableBootIdentityTests(unittest.TestCase):
    def test_linux_reads_raw_start_ticks_after_parenthesized_comm(self):
        fields = ['S'] + ['0'] * 18 + ['123456789']
        stat = b'73 (name with ) bracket \xff) ' + ' '.join(fields).encode('ascii')
        with patch.object(process_start.sys, 'platform', 'linux'), \
             patch('builtins.open', return_value=io.BytesIO(stat)) as opened:
            self.assertEqual(process_start.process_start(73, include_exited=False), 123456789)
        opened.assert_called_once_with('/proc/73/stat', 'rb')

    def test_windows_reads_raw_filetime_and_closes_handle(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 77
        kernel.WaitForSingleObject.return_value = 0x102

        def get_times(_handle, created, _exited, _kernel, _user):
            created._obj.low = 0x89ABCDEF
            created._obj.high = 0x01234567
            return 1

        kernel.GetProcessTimes.side_effect = get_times
        with patch.object(process_start.sys, 'platform', 'win32'), \
             patch.object(ctypes, 'WinDLL', return_value=kernel, create=True):
            self.assertEqual(process_start.process_start(73, include_exited=False),
                             0x0123456789ABCDEF)
            kernel.WaitForSingleObject.return_value = 0
            self.assertIsNone(process_start.process_start(73, include_exited=False))
        self.assertEqual(kernel.CloseHandle.call_count, 2)

    def test_creation_token_is_stable_across_wall_clock_shift(self):
        pid = os.getpid()
        baseline = process_start.process_start(pid, include_exited=False)
        self.assertIsInstance(baseline, int)
        for shifted in (time.time() + 86_400, time.time() - 86_400):
            with self.subTest(shifted=shifted), patch.object(time, 'time', return_value=shifted):
                self.assertEqual(process_start.process_start(pid, include_exited=False), baseline)

    @unittest.skipUnless(sys.platform == 'darwin', 'native libproc observation')
    def test_macos_native_creation_token_is_exact_and_live(self):
        pid = os.getpid()
        raw = process_start._macos_process_start(pid, include_exited=False)
        self.assertIsInstance(raw, int)
        self.assertGreater(raw, 0)
        self.assertEqual(raw, process_identity._creation_time(pid))
        self.assertEqual(raw, process_identity._started_at(pid))

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
