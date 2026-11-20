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

    def test_foreign_trustee_diagnostics_use_only_redacted_categories(self):
        for trustee, kind in (
            ('S-1-1-0', 'everyone'),
            ('S-1-3-0', 'creator-owner'),
            ('S-1-5-11', 'authenticated-users'),
            ('S-1-5-32-545', 'builtin-users'),
            ('S-1-15-2-1', 'all-application-packages'),
            ('S-1-15-2-2', 'all-restricted-application-packages'),
            ('S-1-15-3-123', 'app-package-sid'),
            ('S-1-5-80-123', 'service-sid'),
            ('S-1-5-32-546', 'builtin-group-sid'),
            ('S-1-12-1-111-222-333-444', 'other-account-sid'),
            (FOREIGN, 'other-account-sid'),
        ):
            with self.subTest(kind=kind), self.assertRaises(BoardError) as raised:
                files.validate_acl(USER, USER, True, [((0, 16), 1, trustee)])
            error = raised.exception
            self.assertEqual(error.code, 'PRIVATE_PATH_UNSAFE')
            self.assertEqual(error.details, {'aceType': 0, 'aceFlags': 16, 'trusteeKind': kind})
            self.assertNotIn(trustee, str(error))

    def test_deny_malformed_and_foreign_owner_diagnostics_omit_account_sids(self):
        for owner, ace, details in (
            (USER, ((1, 16), 1, FOREIGN), {'aceType': 1, 'aceFlags': 16}),
            (USER, ((0, 16), 1), {'aceShape': 'malformed'}),
            (USER, ((0,), 1, FOREIGN), {'aceShape': 'malformed'}),
            (FOREIGN, ((0, 16), 1, USER), {}),
        ):
            with self.subTest(details=details), self.assertRaises(BoardError) as raised:
                files.validate_acl(owner, USER, True, [ace])
            self.assertEqual(raised.exception.details, details)
            self.assertNotIn(FOREIGN, str(raised.exception))

    def test_import_needs_no_pywin32_at_module_load(self):
        code = "import sys; sys.path.insert(0, sys.argv[1]); import hey_my_buddy.windows_files; assert 'win32security' not in sys.modules"
        result = subprocess.run([sys.executable, '-S', '-c', code, str(Path(files.__file__).parents[1])], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class StateRootACLTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary).resolve() / 'state'
        self.api = self.enterContext(mock.patch.object(files, 'WindowsFileAPI')).return_value
        self.api._call.side_effect = lambda method, *args: method(*args)
        self.reader, self.writer = FakeHandle(1), FakeHandle(2)
        self.api._open.side_effect = [self.reader, self.writer]
        self.api.info.return_value = (files.FILE_ATTRIBUTE_DIRECTORY, 0)
        self.api.close.side_effect = lambda handle: handle.Close()

        self.security = mock.Mock(TokenUser=1, SE_FILE_OBJECT=1, OWNER_SECURITY_INFORMATION=1,
                                  DACL_SECURITY_INFORMATION=4, PROTECTED_DACL_SECURITY_INFORMATION=-0x80000000,
                                  SE_DACL_PRESENT=4, ACL_REVISION=2)
        self.security.OpenProcessToken.side_effect = lambda *_: FakeHandle(3)
        self.security.GetTokenInformation.return_value = (USER, 0)
        self.security.ConvertSidToStringSid.side_effect = lambda value: value
        self.security.ConvertStringSidToSid.side_effect = lambda value: value
        self.enterContext(mock.patch.object(files, '_security', return_value=(mock.Mock(), mock.Mock(), self.security)))

    def descriptor(self, *, owner=USER, present=True, aces=()):
        descriptor = mock.Mock()
        descriptor.GetSecurityDescriptorOwner.return_value = owner
        descriptor.GetSecurityDescriptorControl.return_value = (4 if present else 0, 1)
        if aces is None:
            descriptor.GetSecurityDescriptorDacl.return_value = None
        else:
            dacl = descriptor.GetSecurityDescriptorDacl.return_value
            dacl.GetAceCount.return_value = len(aces)
            dacl.GetAce.side_effect = aces
        return descriptor

    def test_compliant_root_only_opens_for_read(self):
        self.security.GetSecurityInfo.return_value = self.descriptor(aces=[((0, 16), 1, USER)])
        files.check_state_root(self.root, create=True)
        self.assertTrue(self.root.is_dir())
        self.api._open.assert_called_once_with(self.root, 0x20080, files.OPEN_EXISTING, share_delete=True)
        self.security.SetSecurityInfo.assert_not_called()
        self.assertTrue(self.reader.closed)

    def test_foreign_owner_is_refused_without_attempting_repair(self):
        self.security.GetSecurityInfo.return_value = self.descriptor(owner=FOREIGN, aces=None)
        with self.assertRaisesRegex(BoardError, 'foreign owner') as raised:
            files.check_state_root(self.root, create=True)
        self.assertEqual(raised.exception.details, {'path': str(self.root)})
        self.api._open.assert_called_once_with(self.root, 0x20080, files.OPEN_EXISTING, share_delete=True)
        self.security.SetSecurityInfo.assert_not_called()
        self.assertTrue(self.reader.closed)

    def test_repairs_owned_root_with_unacceptable_dacl(self):
        cases = (
            ('foreign', True, [((0, 16), 1, FOREIGN)], USER, USER),
            ('null', True, None, files.ADMINISTRATORS_SID, USER),
            ('missing', False, [], files.ADMINISTRATORS_SID, files.ADMINISTRATORS_SID),
        )
        for name, present, aces, owner, user in cases:
            with self.subTest(dacl=name):
                self.api.reset_mock()
                self.security.reset_mock()
                self.reader, self.writer = FakeHandle(1), FakeHandle(2)
                self.api._open.side_effect = [self.reader, self.writer]
                self.security.GetTokenInformation.return_value = (user, 0)
                dacl = self.security.ACL.return_value

                def security_info(handle, _, information):
                    if handle is self.reader:
                        return self.descriptor(owner=owner, present=present, aces=aces)
                    self.assertTrue(self.reader.closed)
                    if information == 1:
                        self.security.SetSecurityInfo.assert_not_called()
                        return self.descriptor(owner=owner)
                    self.security.SetSecurityInfo.assert_called_once()
                    return self.descriptor(owner=owner, aces=[((0, 3), 0x1F01FF, user)])

                self.security.GetSecurityInfo.side_effect = security_info
                files.check_state_root(self.root, create=True)
                self.assertEqual(self.api._open.call_args_list, [
                    mock.call(self.root, access, files.OPEN_EXISTING, share_delete=True)
                    for access in (0x20080, 0x60080)
                ])
                self.assertEqual(self.api.info.call_args_list, [mock.call(self.reader), mock.call(self.writer)])
                self.assertEqual(self.security.GetSecurityInfo.call_args_list, [
                    mock.call(self.reader, 1, 5), mock.call(self.writer, 1, 1), mock.call(self.writer, 1, 5),
                ])
                trustees = [USER, files.SYSTEM_SID, files.ADMINISTRATORS_SID] if user == USER else [user, files.SYSTEM_SID]
                self.assertEqual(dacl.AddAccessAllowedAceEx.call_args_list, [
                    mock.call(2, 3, 0x1F01FF, trustee) for trustee in trustees
                ])
                self.security.SetSecurityInfo.assert_called_once_with(
                    self.writer, 1, 4 | -0x80000000, None, None, dacl, None)
                self.assertTrue(self.reader.closed)
                self.assertTrue(self.writer.closed)

    def test_read_only_root_check_reports_foreign_ace_without_repair(self):
        self.root.mkdir()
        self.security.GetSecurityInfo.return_value = self.descriptor(aces=[((0, 16), 1, FOREIGN)])
        with self.assertRaises(BoardError) as raised:
            files.check_state_root(self.root, create=False)
        self.assertEqual(raised.exception.code, 'PRIVATE_PATH_UNSAFE')
        self.assertEqual(raised.exception.details, {
            'path': str(self.root), 'aceType': 0, 'aceFlags': 16, 'trusteeKind': 'other-account-sid',
        })
        self.assertNotIn(FOREIGN, str(raised.exception))
        self.api._open.assert_called_once_with(self.root, 0x20080, files.OPEN_EXISTING, share_delete=True)
        self.security.SetSecurityInfo.assert_not_called()
        self.assertTrue(self.reader.closed)

    def test_reopened_junction_is_refused_before_reading_or_writing_its_acl(self):
        self.security.GetSecurityInfo.return_value = self.descriptor(aces=None)
        self.api.info.side_effect = [(files.FILE_ATTRIBUTE_DIRECTORY, 0),
                                     (files.FILE_ATTRIBUTE_DIRECTORY | files.FILE_ATTRIBUTE_REPARSE_POINT, 0)]
        with self.assertRaisesRegex(BoardError, 'real directory'):
            files.check_state_root(self.root, create=True)
        self.security.GetSecurityInfo.assert_called_once_with(self.reader, 1, 5)
        self.security.SetSecurityInfo.assert_not_called()
        self.assertTrue(self.reader.closed)
        self.assertTrue(self.writer.closed)

    def test_reopened_foreign_owner_is_refused_before_writing_its_acl(self):
        self.security.GetSecurityInfo.side_effect = [self.descriptor(aces=None), self.descriptor(owner=FOREIGN)]
        with self.assertRaisesRegex(BoardError, 'foreign owner'):
            files.check_state_root(self.root, create=True)
        self.security.SetSecurityInfo.assert_not_called()
        self.assertTrue(self.reader.closed)
        self.assertTrue(self.writer.closed)

    def test_failed_post_write_check_propagates_and_closes_handle(self):
        self.security.GetSecurityInfo.return_value = self.descriptor(aces=None)
        with self.assertRaisesRegex(BoardError, 'non-NULL DACL') as raised:
            files.check_state_root(self.root, create=True)
        self.assertEqual(raised.exception.details, {'path': str(self.root)})
        self.security.SetSecurityInfo.assert_called_once()
        self.assertTrue(self.reader.closed)
        self.assertTrue(self.writer.closed)

    def test_write_dac_denial_propagates_after_closing_read_handle(self):
        self.security.GetSecurityInfo.return_value = self.descriptor(aces=None)
        denial = PermissionError('access denied')
        self.api._open.side_effect = [self.reader, denial]
        with self.assertRaises(PermissionError) as raised:
            files.check_state_root(self.root, create=True)
        self.assertIs(raised.exception, denial)
        self.security.SetSecurityInfo.assert_not_called()
        self.assertTrue(self.reader.closed)

    def test_undecodable_callback_ace_is_reported_read_only_and_repaired_for_owned_root(self):
        self.root.mkdir()
        undecodable = self.descriptor(owner=USER, aces=[])
        undecodable.GetSecurityDescriptorDacl.return_value.GetAceCount.return_value = 1
        undecodable.GetSecurityDescriptorDacl.return_value.GetAce.side_effect = NotImplementedError('callback ACE')
        self.security.GetSecurityInfo.return_value = undecodable
        with self.assertRaisesRegex(BoardError, 'unsupported or foreign ACE') as raised:
            files.check_state_root(self.root, create=False)
        self.assertEqual(raised.exception.code, 'PRIVATE_PATH_UNSAFE')
        self.assertEqual(raised.exception.details, {'path': str(self.root), 'aceShape': 'unsupported'})
        self.security.SetSecurityInfo.assert_not_called()

        self.reader, self.writer = FakeHandle(1), FakeHandle(2)
        self.api._open.side_effect = [self.reader, self.writer]
        repaired = self.descriptor(owner=USER, aces=[((0, 3), 0x1F01FF, USER)])
        self.security.GetSecurityInfo.side_effect = [undecodable, self.descriptor(owner=USER), repaired]
        files.check_state_root(self.root, create=True)
        self.security.SetSecurityInfo.assert_called_once()
        self.assertTrue(self.reader.closed)
        self.assertTrue(self.writer.closed)

    def test_undecodable_callback_ace_with_foreign_owner_is_refused_without_repair(self):
        self.root.mkdir()
        undecodable = self.descriptor(owner=FOREIGN, aces=[])
        undecodable.GetSecurityDescriptorDacl.return_value.GetAceCount.return_value = 1
        undecodable.GetSecurityDescriptorDacl.return_value.GetAce.side_effect = NotImplementedError('callback ACE')
        self.security.GetSecurityInfo.return_value = undecodable
        with self.assertRaisesRegex(BoardError, 'foreign owner') as raised:
            files.check_state_root(self.root, create=True)
        self.assertEqual(raised.exception.details, {'path': str(self.root)})
        self.security.SetSecurityInfo.assert_not_called()
        self.assertTrue(self.reader.closed)


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

    def test_unlink_refuses_directory_reparse_and_non_disk_handles(self):
        for attributes, kind in ((files.FILE_ATTRIBUTE_DIRECTORY, files.FILE_TYPE_DISK),
                                 (files.FILE_ATTRIBUTE_REPARSE_POINT, files.FILE_TYPE_DISK),
                                 (files.FILE_ATTRIBUTE_NORMAL, 2)):
            with self.subTest(attributes=attributes, kind=kind):
                api = mock.Mock()
                api._open.return_value = 123
                api.info.return_value = (attributes, 0)
                api.file_type.return_value = kind
                with self.assertRaises(BoardError):
                    files.unlink_regular_file('leaf', api=api)
                api.kernel.SetFileInformationByHandle.assert_not_called()
                api.close.assert_called_once_with(123)

    def test_unlink_propagates_delete_denial_and_always_closes_its_handle(self):
        api = files.WindowsFileAPI()
        api.kernel = mock.Mock()
        handle = FakeHandle(321)
        self.native.CreateFile.return_value = handle
        self.native.GetFileInformationByHandleEx.return_value = {
            'FileAttributes': files.FILE_ATTRIBUTE_NORMAL, 'ReparseTag': 0}
        self.native.GetFileType.return_value = files.FILE_TYPE_DISK
        api.kernel.SetFileInformationByHandle.return_value = False
        with mock.patch.object(ctypes, 'get_last_error', return_value=32, create=True):
            with self.assertRaises(PermissionError) as raised:
                files.unlink_regular_file(r'C:\folder\leaf', api=api)
        self.assertEqual(raised.exception.winerror, 32)
        self.assertEqual(raised.exception.filename, r'C:\folder\leaf')
        self.assertEqual(raised.exception.errno, errno.EACCES)
        self.assertTrue(handle.closed)
        self.assertTrue(self.native.CreateFile.call_args.args[2] & files.FILE_SHARE_DELETE)

    def test_unlink_open_error_keeps_the_target_path(self):
        api = files.WindowsFileAPI()
        self.native.CreateFile.side_effect = FakeWinError(5)
        with self.assertRaises(PermissionError) as raised:
            files.unlink_regular_file(r'C:\folder\unreadable', api=api)
        self.assertEqual(raised.exception.filename, r'C:\folder\unreadable')
        self.assertEqual(raised.exception.errno, errno.EACCES)
        self.assertEqual(raised.exception.winerror, 5)

    def test_directory_deletion_uses_a_no_follow_handle_and_ignores_readonly(self):
        api = mock.Mock()
        api._open.return_value = 321
        api.info.return_value = (files.FILE_ATTRIBUTE_DIRECTORY | 1, 0)
        api.file_type.return_value = files.FILE_TYPE_DISK

        def remove(handle, kind, buffer, size):
            self.assertEqual((handle, kind, size), (321, 21, ctypes.sizeof(files.wintypes.DWORD)))
            self.assertEqual(ctypes.cast(buffer, ctypes.POINTER(files.wintypes.DWORD)).contents.value, 0x13)
            return True

        api.kernel.SetFileInformationByHandle.side_effect = remove
        files.remove_directory(r'C:\folder\readonly', api=api)
        api._open.assert_called_once_with(r'C:\folder\readonly', files.DELETE | files.FILE_READ_ATTRIBUTES,
                                          files.OPEN_EXISTING, share_delete=True)
        api.close.assert_called_once_with(321)

    def test_directory_deletion_refuses_files_reparse_points_and_non_disk_handles(self):
        for attributes, kind in ((files.FILE_ATTRIBUTE_NORMAL, files.FILE_TYPE_DISK),
                                 (files.FILE_ATTRIBUTE_DIRECTORY | files.FILE_ATTRIBUTE_REPARSE_POINT, files.FILE_TYPE_DISK),
                                 (files.FILE_ATTRIBUTE_DIRECTORY, 2)):
            with self.subTest(attributes=attributes, kind=kind):
                api = mock.Mock()
                api._open.return_value = 123
                api.info.return_value = (attributes, 0)
                api.file_type.return_value = kind
                with self.assertRaises(BoardError):
                    files.remove_directory('directory', api=api)
                api.kernel.SetFileInformationByHandle.assert_not_called()
                api.close.assert_called_once_with(123)

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
    def test_directory_removal_respects_held_handles_and_nonempty_contents(self):
        import win32con, win32file
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'occupied'
            root.mkdir()
            handle = win32file.CreateFile(str(native_path(root)), win32con.GENERIC_READ,
                                          win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
                                          None, win32con.OPEN_EXISTING,
                                          win32con.FILE_FLAG_BACKUP_SEMANTICS, None)
            try:
                with self.assertRaises(PermissionError) as classic:
                    native_path(root).rmdir()
                self.assertEqual(classic.exception.winerror, 32)
                with self.assertRaises(PermissionError) as caught:
                    private_dirs.remove_tree(root)
                self.assertEqual(caught.exception.winerror, 32)
                self.assertEqual(caught.exception.filename, str(root))
                self.assertTrue(root.is_dir())
            finally:
                handle.Close()
            leaf = root / 'keep'
            leaf.write_bytes(b'not an empty directory')
            with self.assertRaises(OSError) as caught:
                files.remove_directory(root)
            self.assertEqual(caught.exception.winerror, 145)
            self.assertEqual(leaf.read_bytes(), b'not an empty directory')
            self.assertTrue(private_dirs.remove_tree(root))
            self.assertFalse(root.exists())

    def test_readonly_directory_removal_covers_empty_roots_and_nested_content(self):
        import win32con, win32file
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for nested in (False, True):
                with self.subTest(nested=nested):
                    root = base / str(nested)
                    root.mkdir()
                    directories = [root]
                    if nested:
                        child = root / 'nested'
                        child.mkdir()
                        (child / 'leaf').write_bytes(b'owned private content')
                        directories.append(child)
                    for directory in directories:
                        native = str(native_path(directory))
                        attributes = win32file.GetFileAttributes(native)
                        win32file.SetFileAttributes(native, attributes | win32con.FILE_ATTRIBUTE_READONLY)
                        self.assertTrue(win32file.GetFileAttributes(native) & win32con.FILE_ATTRIBUTE_READONLY)
                    self.assertTrue(private_dirs.remove_tree(root))
                    self.assertFalse(root.exists())


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

    def test_owner_repairs_wide_inheritance_and_children_inherit_the_private_acl(self):
        import win32api, win32con, win32security as security

        token = security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            user = security.GetTokenInformation(token, security.TokenUser)[0]
        finally:
            token.Close()
        trustees = tuple(dict.fromkeys((security.ConvertSidToStringSid(user),
                                        files.SYSTEM_SID, files.ADMINISTRATORS_SID)))
        inherit = files.OBJECT_INHERIT_ACE | files.CONTAINER_INHERIT_ACE

        def acl_at(path):
            descriptor = security.GetFileSecurity(str(native_path(path)), security.DACL_SECURITY_INFORMATION)
            control, _ = descriptor.GetSecurityDescriptorControl()
            dacl = descriptor.GetSecurityDescriptorDacl()
            entries = []
            for index in range(dacl.GetAceCount()):
                header, mask, sid = dacl.GetAce(index)
                entries.append((header, mask, security.ConvertSidToStringSid(sid)))
            return control, entries

        for foreign in ('S-1-1-0', 'S-1-15-2-1', 'S-1-3-0'):
            with self.subTest(trustee=foreign), tempfile.TemporaryDirectory() as temporary, \
                    mock.patch.object(state_root, '_checked_roots', {}):
                parent = Path(temporary)
                dacl = security.ACL()
                for trustee in (*trustees, foreign):
                    dacl.AddAccessAllowedAceEx(security.ACL_REVISION, inherit, files.FILE_ALL_ACCESS,
                                              security.ConvertStringSidToSid(trustee))
                security.SetNamedSecurityInfo(str(parent), security.SE_FILE_OBJECT,
                                              security.DACL_SECURITY_INFORMATION | security.PROTECTED_DACL_SECURITY_INFORMATION,
                                              None, None, dacl, None)

                root = parent / 'state'
                self.assertEqual(state_root.check_state_root(root, create=True), root)
                control, entries = acl_at(root)
                self.assertTrue(control & security.SE_DACL_PROTECTED)
                self.assertCountEqual(entries, [((0, inherit), files.FILE_ALL_ACCESS, trustee) for trustee in trustees])

                child = root / 'child'
                child.mkdir()
                leaf = child / 'file'
                leaf.write_bytes(b'inherited')
                for path in (child, leaf):
                    child_control, child_entries = acl_at(path)
                    self.assertFalse(child_control & security.SE_DACL_PROTECTED)
                    self.assertCountEqual([entry[2] for entry in child_entries], trustees)
                    for (ace_type, flags), mask, _ in child_entries:
                        self.assertEqual(ace_type, 0)
                        self.assertTrue(flags & 0x10)  # INHERITED_ACE
                        self.assertEqual(mask, files.FILE_ALL_ACCESS)

                state_root._checked_roots.clear()
                self.assertEqual(state_root.check_state_root(root, create=False), root)
                state_root._checked_roots.clear()
                self.assertEqual(state_root.check_state_root(root, create=True), root)
                self.assertEqual(acl_at(root), (control, entries))

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
