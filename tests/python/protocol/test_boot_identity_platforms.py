"""Stable boot identity APIs on platforms not available to this test machine."""
from __future__ import annotations

import ctypes
import errno
import io
import os
import subprocess
import sys
import time
import unittest
import uuid
from unittest.mock import Mock, patch

from hey_my_buddy.protocol import process_identity, process_start


BOOT = '00000000-0000-4000-8000-000000000001'


class LegacyMacBSDInfo(ctypes.Structure):
    # The previous product reader, retained only as an independent equality
    # oracle for identities already recorded by installed executions.
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32), ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32), ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32), ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32), ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32), ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16), ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32), ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32), ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32), ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64), ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def legacy_macos_start(pid):
    method = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True).proc_pidinfo
    method.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    method.restype = ctypes.c_int
    info = LegacyMacBSDInfo()
    count = method(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
    if count != ctypes.sizeof(info) or info.pbi_pid != pid:
        raise OSError(ctypes.get_errno(), 'legacy process creation observation failed')
    return info.pbi_start_tvsec * 1_000_000 + info.pbi_start_tvusec


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
            kernel.WaitForSingleObject.reset_mock()
            self.assertEqual(process_start.process_start(73, include_exited=True),
                             0x0123456789ABCDEF)
            kernel.WaitForSingleObject.assert_not_called()
        self.assertEqual(kernel.CloseHandle.call_count, 3)

    def test_macos_sysctl_distinguishes_absence_from_unreadable_or_malformed_identity(self):
        def read(_mib, _count, output, length, _new, _new_length):
            info = output._obj
            info.pid, info.status = 73, 2
            info.start_tvsec, info.start_tvusec = 1_700_000_000, 123_457
            return 0

        library = Mock()
        library.sysctl.side_effect = read
        with patch.object(ctypes, 'CDLL', return_value=library):
            self.assertEqual(process_start._macos_process_start(73, include_exited=False),
                             1_700_000_000_123_457)
            for error in (errno.EPERM, errno.EACCES, errno.ENOMEM, errno.ESRCH):
                def failed(*_args):
                    ctypes.set_errno(error)
                    return -1
                library.sysctl.side_effect = failed
                with self.subTest(error=error):
                    if error == errno.ESRCH:
                        self.assertIsNone(process_start._macos_process_start(73, include_exited=True))
                    else:
                        with self.assertRaises(OSError) as caught:
                            process_start._macos_process_start(73, include_exited=True)
                        self.assertEqual(caught.exception.errno, error)
            for field, value in (('length', 0), ('length', 647), ('length', 649),
                                 ('pid', 74), ('status', 0), ('start_tvsec', 0),
                                 ('start_tvusec', -1), ('start_tvusec', 1_000_000)):
                def malformed(mib, count, output, length, new, new_length):
                    read(mib, count, output, length, new, new_length)
                    if field == 'length':
                        length._obj.value = value
                    else:
                        setattr(output._obj, field, value)
                    return 0
                library.sysctl.side_effect = malformed
                with self.subTest(field=field, value=value):
                    if field == 'length' and value == 0:
                        self.assertIsNone(process_start._macos_process_start(73, include_exited=True))
                    else:
                        with self.assertRaises(OSError):
                            process_start._macos_process_start(73, include_exited=True)

    def test_creation_token_is_stable_across_wall_clock_shift(self):
        pid = os.getpid()
        baseline = process_start.process_start(pid, include_exited=False)
        self.assertIsInstance(baseline, int)
        for shifted in (time.time() + 86_400, time.time() - 86_400):
            with self.subTest(shifted=shifted), patch.object(time, 'time', return_value=shifted):
                self.assertEqual(process_start.process_start(pid, include_exited=False), baseline)

    @unittest.skipUnless(sys.platform == 'darwin', 'native sysctl/libproc equality')
    def test_macos_creation_tokens_equal_previous_reader_for_live_processes(self):
        children = []
        try:
            for _ in range(3):
                children.append(subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']))
            for pid in (os.getpid(), os.getppid(), *(child.pid for child in children)):
                with self.subTest(pid=pid):
                    previous = legacy_macos_start(pid)
                    self.assertGreater(previous, 0)
                    self.assertEqual(process_start.process_start(pid, include_exited=False), previous)
                    self.assertEqual(process_start.process_start(pid, include_exited=True), previous)
        finally:
            for child in children:
                child.kill()
                child.wait(timeout=5)

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

    def test_windows_has_no_boot_identifier_to_read(self):
        # A Windows identity records no boot value, so the reader refuses the
        # platform instead of returning an unused number.
        with patch.object(process_identity.sys, 'platform', 'win32'):
            with self.assertRaises(OSError):
                process_identity.boot_id()
