"""The Windows private boundary's policy is portable; native checks are explicit."""
import ctypes
import errno
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from hey_my_buddy import windows_files as files, private_dirs, state_root
from hey_my_buddy.errors import BoardError
from hey_my_buddy.private_files import publish_bytes
from hey_my_buddy.paths import native_path, resolved_path, windows_path

USER = 'S-1-5-21-100-200-300-1001'
FOREIGN = 'S-1-5-21-100-200-300-1002'


class WindowsPathTests(unittest.TestCase):
    def test_drive_paths_and_existing_prefix_have_one_native_spelling(self):
        for drive in ('C', 'c', 'Z', 'z'):
            ordinary = drive + ':\\folder\\中文.json'
            extended = '\\\\?\\' + ordinary
            for value in (ordinary, extended, PureWindowsPath(ordinary)):
                with self.subTest(value=value):
                    self.assertEqual(windows_path(value), extended)
                    self.assertEqual(windows_path(value, extended=False), ordinary)

    def test_paths_without_a_local_drive_root_and_traversal_are_rejected(self):
        for path in ('leaf', 'C:leaf', '\\leaf', r'C:\folder\..\leaf',
                     r'\\?\C:\folder\..\leaf', r'\\.\NUL', r'\\?\GLOBALROOT\Device\HarddiskVolume1',
                     'C:\\folder\\nul\0suffix'):
            with self.subTest(path=path), self.assertRaises(BoardError):
                windows_path(path)


class FakeWinError(Exception):
    def __init__(self, code):
        super().__init__(f'Windows error {code}')
        self.winerror = code


class FakeHandle:
    def __init__(self, value):
        self.value = value
        self.detached = False
        self.closed = False

    def __int__(self):
        return self.value

    def Detach(self):
        self.detached = True
        return self.value

    def Close(self):
        if self.closed:
            raise AssertionError('handle closed twice')
        self.closed = True


class ACLPolicyTests(unittest.TestCase):
    def test_empty_dacl_and_all_whitelisted_ordinary_allows_are_accepted(self):
        for owner in (USER, files.ADMINISTRATORS_SID):
            files.validate_acl(owner, USER, True, [])
            for trustee in (USER, files.SYSTEM_SID, files.ADMINISTRATORS_SID, files.OWNER_RIGHTS_SID):
                for flags in (0, 3, 8, 16):  # includes inherit-only and inherited
                    files.validate_acl(owner, USER, True, [((0, flags), 0, trustee)])

    def test_missing_null_dacl_and_foreign_owner_are_rejected(self):
        for owner, present, aces in ((FOREIGN, True, []), (USER, False, []), (USER, True, None)):
            with self.subTest(owner=owner, present=present, aces=aces), self.assertRaises(BoardError):
                files.validate_acl(owner, USER, present, aces)

    def test_deny_object_conditional_unknown_and_foreign_aces_are_rejected(self):
        aces = [((1, 0), 0, USER), ((5, 0), 0, None, None, USER),
                ((9, 0), 0, USER, b'condition'), ((255, 0), 0, USER),
                ((0, 8), 0, FOREIGN)]
        for ace in aces:
            with self.subTest(ace=ace), self.assertRaises(BoardError):
                files.validate_acl(USER, USER, True, [((0, 0), 0, USER), ace])

    def test_handle_security_query_preserves_missing_and_null_dacl(self):
        security = mock.MagicMock()
        security.TokenUser = 1
        security.SE_FILE_OBJECT = 1
        security.OWNER_SECURITY_INFORMATION = 1
        security.DACL_SECURITY_INFORMATION = 4
        security.SE_DACL_PRESENT = 4
        security.ConvertSidToStringSid.side_effect = lambda value: value
        security.GetTokenInformation.return_value = (USER, 0)
        descriptor = security.GetSecurityInfo.return_value
        descriptor.GetSecurityDescriptorOwner.return_value = USER
        for control, dacl in ((0, mock.Mock()), (4, None)):
            descriptor.GetSecurityDescriptorControl.return_value = (control, 1)
            descriptor.GetSecurityDescriptorDacl.return_value = dacl
            with mock.patch.object(files, '_security', return_value=(mock.Mock(), mock.Mock(), security)):
                with self.assertRaises(BoardError):
                    files._validate_root_acl(123)
        security.GetSecurityInfo.assert_called_with(123, 1, 5)

    def test_import_needs_no_pywin32_at_module_load(self):
        code = "import sys; sys.path.insert(0, sys.argv[1]); import hey_my_buddy.windows_files; assert 'win32security' not in sys.modules"
        result = subprocess.run([sys.executable, '-S', '-c', code, str(Path(files.__file__).parents[1])], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

class HandleBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.native = mock.Mock()
        self.native.FileAttributeTagInfo = 9
        self.native.FileDispositionInfo = 4
        self.crt = mock.Mock()
        self.modules = mock.patch.dict(sys.modules, {
            'win32file': self.native,
            'pywintypes': mock.Mock(error=FakeWinError),
            'msvcrt': self.crt,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        binary = mock.patch.object(os, 'O_BINARY', 0x8000, create=True)
        noinherit = mock.patch.object(os, 'O_NOINHERIT', 0x10000, create=True)
        binary.start()
        noinherit.start()
        self.addCleanup(noinherit.stop)
        self.addCleanup(binary.stop)
        # Keep the production conversion Windows-only; supply its constructor
        # alongside the fake pywin32 modules on other test hosts.
        def win_error(code):
            category, number = ((FileNotFoundError, errno.ENOENT) if code in (2, 3) else
                                (FileExistsError, errno.EEXIST) if code in (80, 183) else
                                (PermissionError, errno.EACCES) if code in (5, 32) else
                                (OSError, errno.EIO))
            error = category(number, 'fixture')
            error.winerror = code
            return error

        converted = mock.patch.object(ctypes, 'WinError', side_effect=win_error, create=True)
        converted.start()
        self.addCleanup(converted.stop)

    def test_no_follow_open_and_private_file_operations(self):
        handle = FakeHandle(321)
        self.native.CreateFile.return_value = handle
        self.native.GetFileInformationByHandleEx.return_value = {
            'FileAttributes': files.FILE_ATTRIBUTE_REPARSE_POINT,
            'ReparseTag': files.IO_REPARSE_TAG_MOUNT_POINT,
        }
        self.native.GetFileType.return_value = files.FILE_TYPE_DISK
        with mock.patch.object(files.ctypes, 'WinDLL', create=True) as kernel:
            api = files.WindowsFileAPI()
            self.assertIs(api.kernel, files._RENAME_KERNEL)
            kernel.assert_not_called()
        self.assertIs(api._open(r'C:\folder\leaf', files.GENERIC_READ, files.OPEN_EXISTING), handle)
        self.native.CreateFile.assert_called_once_with(
            r'\\?\C:\folder\leaf', files.GENERIC_READ, files.FILE_SHARE_READ | files.FILE_SHARE_WRITE,
            None, files.OPEN_EXISTING,
            files.FILE_FLAG_OPEN_REPARSE_POINT | files.FILE_FLAG_BACKUP_SEMANTICS |
            files.FILE_ATTRIBUTE_NORMAL, None)
        api._open(r'C:\folder\leaf', files.GENERIC_READ, files.OPEN_EXISTING, share_delete=True)
        self.assertTrue(self.native.CreateFile.call_args.args[2] & files.FILE_SHARE_DELETE)
        self.assertEqual(api.info(handle), (files.FILE_ATTRIBUTE_REPARSE_POINT,
                                            files.IO_REPARSE_TAG_MOUNT_POINT))
        self.native.GetFileInformationByHandleEx.assert_called_once_with(handle, 9)
        self.assertEqual(api.file_type(handle), files.FILE_TYPE_DISK)
        api.delete(handle)
        self.native.SetFileInformationByHandle.assert_called_once_with(handle, 4, True)
        api.close(handle)
        self.assertTrue(handle.closed)

    def test_pywin32_errors_become_os_errors_with_original_categories(self):
        api = files.WindowsFileAPI()
        for code, category in ((2, FileNotFoundError), (3, FileNotFoundError),
                               (80, FileExistsError), (183, FileExistsError),
                               (5, PermissionError), (32, PermissionError),
                               (87, OSError)):
            with self.subTest(code=code):
                self.native.CreateFile.side_effect = FakeWinError(code)
                with self.assertRaises(category) as raised:
                    api._open(r'C:\folder\leaf', files.GENERIC_READ, files.OPEN_EXISTING)
                self.assertIs(raised.exception.__cause__, self.native.CreateFile.side_effect)
                self.assertEqual(raised.exception.winerror, code)
                self.assertIsInstance(raised.exception, OSError)

    def test_crt_transfer_detaches_only_after_success(self):
        api = files.WindowsFileAPI()
        handle = FakeHandle(321)
        self.native.CreateFile.return_value = handle
        self.native.GetFileInformationByHandleEx.return_value = {
            'FileAttributes': files.FILE_ATTRIBUTE_NORMAL, 'ReparseTag': 0}
        self.native.GetFileType.return_value = files.FILE_TYPE_DISK
        self.crt.open_osfhandle.return_value = 456
        self.assertEqual(files.open_regular_fd(r'C:\folder\leaf', os.O_RDONLY, api=api), 456)
        self.crt.open_osfhandle.assert_called_once_with(
            321, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
        self.assertTrue(handle.detached)
        self.assertFalse(handle.closed)

        failed = FakeHandle(654)
        self.native.CreateFile.return_value = failed
        self.crt.open_osfhandle.side_effect = OSError('transfer failed')
        with self.assertRaisesRegex(OSError, 'transfer failed'):
            files.open_regular_fd(r'C:\folder\leaf', os.O_RDONLY, api=api)
        self.assertFalse(failed.detached)
        self.assertTrue(failed.closed)

    def test_reparse_leaf_is_refused_and_closed_before_crt_transfer(self):
        api = files.WindowsFileAPI()
        handle = FakeHandle(321)
        self.native.CreateFile.return_value = handle
        self.native.GetFileInformationByHandleEx.return_value = {
            'FileAttributes': files.FILE_ATTRIBUTE_REPARSE_POINT,
            'ReparseTag': files.IO_REPARSE_TAG_MOUNT_POINT,
        }
        with self.assertRaises(BoardError):
            files.open_regular_fd(r'C:\folder\link', os.O_RDONLY, api=api)
        self.assertTrue(handle.closed)
        self.assertFalse(handle.detached)
        self.crt.open_osfhandle.assert_not_called()

    def test_rename_uses_writer_handle_and_posix_replace_flags_once(self):
        api = files.WindowsFileAPI.__new__(files.WindowsFileAPI)
        api.kernel, api.msvcrt = mock.Mock(), mock.Mock()
        api.msvcrt.get_osfhandle.return_value = 321
        target = PureWindowsPath(r'C:\facts\published-中文.json')
        expected = r'\\?\C:\facts\published-中文.json'

        def rename(handle, kind, buffer, size):
            info = files._RenameInfo.from_buffer(buffer)
            self.assertEqual((handle, kind, info.Flags, info.RootDirectory), (321, 22, 3, None))
            name = ctypes.string_at(ctypes.addressof(buffer) + files._RenameInfo.FileName.offset,
                                    info.FileNameLength).decode('utf-16-le')
            self.assertEqual(name, expected)
            self.assertEqual(size, files._RenameInfo.FileName.offset + len(expected.encode('utf-16-le')) + 2)
            self.assertEqual(buffer.raw[size - 2:size], b'\0\0')
            return True

        api.kernel.SetFileInformationByHandle.side_effect = rename
        api.rename(123, target)
        api.msvcrt.get_osfhandle.assert_called_once_with(123)
        api.kernel.SetFileInformationByHandle.assert_called_once()
        api.kernel.CloseHandle.assert_not_called()

    def test_publication_writer_requests_delete_access_and_all_sharing(self):
        api = mock.Mock()
        api._open.return_value = 123
        api.info.return_value = (files.FILE_ATTRIBUTE_NORMAL, 0)
        api.file_type.return_value = files.FILE_TYPE_DISK
        api.fd.return_value = 456
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        self.assertEqual(files.open_regular_fd(Path('new.tmp'), flags, delete=True, api=api), 456)
        api._open.assert_called_once_with(Path('new.tmp'),
            files.DELETE | files.GENERIC_WRITE, files.CREATE_NEW, share_delete=True)
        api.close.assert_not_called()

    def test_private_open_refuses_parent_traversal_and_truncation_before_opening(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path, flags in ((root / 'parent/../leaf', os.O_RDONLY),
                                (root / 'leaf', os.O_WRONLY | os.O_TRUNC),
                                (root / 'leaf', os.O_WRONLY | os.O_CREAT | os.O_TRUNC)):
                with self.subTest(path=path, flags=flags), self.assertRaisesRegex(BoardError, 'parent traversal|without traversal|Unsafe ordinary-file open'):
                    private_dirs.open_regular_fd(path, flags)

    def test_fd_ownership_transfers_once_and_failure_closes_handle(self):
        api = mock.Mock()
        api._open.return_value = 123
        api.info.return_value = (files.FILE_ATTRIBUTE_NORMAL, 0)
        api.file_type.return_value = files.FILE_TYPE_DISK
        api.fd.return_value = 456
        with mock.patch.object(files, 'WindowsFileAPI', return_value=api):
            self.assertEqual(files.open_regular_fd('leaf', os.O_RDONLY), 456)
            api.fd.assert_called_once_with(123, os.O_RDONLY)
            api.close.assert_not_called()
            api.fd.side_effect = OSError('transfer failed')
            with self.assertRaises(OSError):
                files.open_regular_fd('leaf', os.O_RDONLY)
            api.close.assert_called_once_with(123)

class LauncherTransferTests(unittest.TestCase):
    def test_windows_descriptor_duplicate_is_owned_until_transfer_finishes(self):
        from hey_my_buddy.install import launcher
        with mock.patch.object(launcher.os, 'name', 'nt'), \
                mock.patch.object(launcher.os, 'dup', return_value=123) as duplicate, \
                mock.patch.object(launcher.os, 'set_inheritable') as inherit, \
                mock.patch.object(launcher.os, 'close') as close:
            with self.assertRaisesRegex(RuntimeError, 'transfer failed'):
                with launcher._inherited_credential(17) as descriptor:
                    self.assertEqual(descriptor, 123)
                    duplicate.assert_called_once_with(17)
                    inherit.assert_called_once_with(123, True)
                    close.assert_not_called()
                    raise RuntimeError('transfer failed')
            close.assert_called_once_with(123)

    def test_windows_transfer_uses_the_exact_process_environment_and_crt_exec(self):
        from hey_my_buddy.install import launcher
        selected = {'HMB_STATE_DIR': 'private-state', 'KEEP': 'selected'}
        with mock.patch.dict(os.environ, {'DROP': 'host-setting'}, clear=True):
            with mock.patch.object(launcher.os, 'name', 'nt'), \
                    mock.patch.object(launcher.os, 'spawnv', return_value=17) as execute:
                with self.assertRaises(SystemExit) as exited:
                    launcher._exec_runtime('python', ['python', '-P', '-m', 'entry'], selected)
                self.assertEqual(exited.exception.code, 17)
                execute.assert_called_once_with(os.P_WAIT, 'python', ['python', '-P', '-m', 'entry'])
                self.assertEqual(dict(os.environ), selected)

@unittest.skipUnless(os.name == 'nt', 'native Windows ACL/handle evidence')
class NativePrivateFilesTests(unittest.TestCase):

    def test_long_resolve_checks_links_and_returns_ordinary_paths(self):
        temporary = private_dirs.make_temporary_dir(prefix='resolve-', directory=Path(tempfile.gettempdir()))
        self.addCleanup(private_dirs.remove_tree, temporary)
        root = temporary
        while len(str(root)) < 310:
            root /= 'directory-' + 'a' * 35
        self.assertEqual(private_dirs.ensure_private_dir(root), root)
        target = root / 'target.json'
        publish_bytes(target, b'content')
        link = root / 'link'
        try:
            native_path(link).symlink_to(target.name)
        except OSError as error:
            if error.winerror == 1314:
                self.skipTest('symbolic-link privilege is unavailable')
            raise
        self.assertEqual(resolved_path(link), target)
        self.assertEqual(private_dirs._absolute(native_path(root)), root)
        self.assertNotIn('\\\\?\\', str(resolved_path(root)))

    def test_private_path_accepts_different_case(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = private_dirs.ensure_private_dir(Path(temporary) / 'Mixed Case Private')
            path = root / 'Payload.json'
            publish_bytes(path, b'case-preserved-content')
            variant = Path(str(path).swapcase())
            with os.fdopen(private_dirs.open_regular_fd(variant, os.O_RDONLY), 'rb') as stream:
                self.assertEqual(stream.read(), b'case-preserved-content')
            state_root.check_state_root(Path(str(root).swapcase()))

    def test_private_path_accepts_an_available_8dot3_alias(self):
        import win32api
        with tempfile.TemporaryDirectory() as temporary:
            root = private_dirs.ensure_private_dir(Path(temporary) / 'Long Private Directory For Short Name')
            short = win32api.GetShortPathName(str(root))
            if short.casefold() == str(root).casefold():
                self.skipTest('GetShortPathNameW returned the long spelling; this volume supplies no 8.3 alias for the fixture')
            path = root / 'Payload.json'
            publish_bytes(path, b'short-name-content')
            with os.fdopen(private_dirs.open_regular_fd(Path(short) / path.name, os.O_RDONLY), 'rb') as stream:
                self.assertEqual(stream.read(), b'short-name-content')
            state_root.check_state_root(Path(short))

    def test_private_path_replaced_by_a_junction_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = private_dirs.ensure_private_dir(base / 'private')
            outside = private_dirs.ensure_private_dir(base / 'outside')
            moved = root.with_name('original-private')
            root.rename(moved)
            result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(root), str(outside)], capture_output=True)
            self.assertEqual(result.returncode, 0, repr(result.stdout) + repr(result.stderr))
            state_root._checked_roots.clear()
            try:
                with self.assertRaises(BoardError):
                    state_root.check_state_root(root)
            finally:
                root.rmdir()
                moved.rename(root)

    def test_inherited_profile_acl_is_accepted_and_foreign_ace_is_refused(self):
        import win32security as security
        with tempfile.TemporaryDirectory() as temporary:
            root = private_dirs.ensure_private_dir(Path(temporary) / 'inherited')
            state_root.check_state_root(root)
            descriptor = security.GetFileSecurity(str(root), security.DACL_SECURITY_INFORMATION)
            dacl = descriptor.GetSecurityDescriptorDacl()
            dacl.AddAccessAllowedAce(security.ACL_REVISION, 1, security.CreateWellKnownSid(security.WinWorldSid))
            security.SetNamedSecurityInfo(str(root), security.SE_FILE_OBJECT,
                                          security.DACL_SECURITY_INFORMATION, None, None, dacl, None)
            state_root._checked_roots.clear()
            with self.assertRaisesRegex(BoardError, 'foreign ACE'):
                state_root.check_state_root(root)

    def test_ordinary_creation_does_not_protect_dacl_and_descriptor_only_checks_type(self):
        import win32security as security
        with tempfile.TemporaryDirectory() as temporary:
            root = private_dirs.ensure_private_dir(Path(temporary) / 'private' / 'nested')
            descriptor = security.GetFileSecurity(str(root), security.DACL_SECURITY_INFORMATION)
            control, _ = descriptor.GetSecurityDescriptorControl()
            self.assertFalse(control & security.SE_DACL_PROTECTED)
            temporary_root = private_dirs.make_temporary_dir(prefix='inherited-', directory=root)
            temporary_descriptor = security.GetFileSecurity(str(temporary_root), security.DACL_SECURITY_INFORMATION)
            self.assertFalse(temporary_descriptor.GetSecurityDescriptorControl()[0] & security.SE_DACL_PROTECTED)
            path = root / 'credential.json'
            publish_bytes(path, b'complete')
            with os.fdopen(private_dirs.open_regular_fd(path, os.O_RDONLY), 'rb') as stream:
                private_dirs.validate_private_fd(stream.fileno())
                self.assertEqual(stream.read(), b'complete')

    def test_cold_start_refuses_junction_ancestor_without_creating_state(self):
        from hey_my_buddy.protocol import transport
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            outside = private_dirs.ensure_private_dir(base / 'outside')
            link = base / 'junction'
            result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
            self.assertEqual(result.returncode, 0, repr(result.stdout) + repr(result.stderr))
            try:
                state = state_root.get_state_dir(link / 'state')
                self.assertEqual(state, link / 'state')
                with self.assertRaisesRegex(BoardError, 'linked component'):
                    transport._cold_start_preflight(state)
                self.assertFalse((outside / 'state').exists())
            finally:
                link.rmdir()
