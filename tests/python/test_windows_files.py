"""The Windows private boundary's policy is portable; native checks are explicit."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from hey_my_buddy import windows_files as files, private_dirs
from hey_my_buddy.errors import BoardError
from hey_my_buddy.private_files import publish_bytes

USER = 'S-1-5-21-100-200-300-1001'
FOREIGN = 'S-1-5-21-100-200-300-1002'

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
    def test_rename_uses_writer_handle_and_posix_replace_flags_once(self):
        api = files.WindowsFileAPI.__new__(files.WindowsFileAPI)
        api.kernel, api.msvcrt = mock.Mock(), mock.Mock()
        api.msvcrt.get_osfhandle.return_value = 321
        target = Path('published-中文.json').absolute()

        def rename(handle, kind, buffer, size):
            info = files._RenameInfo.from_buffer(buffer)
            self.assertEqual((handle, kind, info.Flags, info.RootDirectory), (321, 22, 3, None))
            name = ctypes.string_at(ctypes.addressof(buffer) + files._RenameInfo.FileName.offset,
                                    info.FileNameLength).decode('utf-16-le')
            self.assertEqual(name, str(target))
            self.assertEqual(size, files._RenameInfo.FileName.offset + len(str(target).encode('utf-16-le')) + 2)
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

    def test_directory_open_pins_against_rename(self):
        api = files.WindowsFileAPI.__new__(files.WindowsFileAPI)
        api.kernel = mock.Mock()
        api.kernel.CreateFileW.return_value = 123
        api.open_dir(Path('parent'))
        args = api.kernel.CreateFileW.call_args.args
        self.assertTrue(args[1] & files.GENERIC_READ)
        self.assertFalse(args[2] & files.FILE_SHARE_DELETE)
        self.assertEqual(args[5] & (files.FILE_FLAG_OPEN_REPARSE_POINT | files.FILE_FLAG_BACKUP_SEMANTICS),
                         files.FILE_FLAG_OPEN_REPARSE_POINT | files.FILE_FLAG_BACKUP_SEMANTICS)

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

    def test_private_path_accepts_different_case(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = private_dirs.ensure_private_dir(Path(temporary) / 'Mixed Case Private')
            path = root / 'Payload.json'
            publish_bytes(path, b'case-preserved-content')
            variant = Path(str(path).swapcase())
            with os.fdopen(private_dirs.open_regular_fd(variant, os.O_RDONLY), 'rb') as stream:
                self.assertEqual(stream.read(), b'case-preserved-content')
            files.validate_state_root(Path(str(root).swapcase()))

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
            files.validate_state_root(Path(short))

    def test_private_path_replaced_by_a_junction_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = private_dirs.ensure_private_dir(base / 'private')
            outside = private_dirs.ensure_private_dir(base / 'outside')
            moved = root.with_name('original-private')
            root.rename(moved)
            result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(root), str(outside)], capture_output=True)
            self.assertEqual(result.returncode, 0, repr(result.stdout) + repr(result.stderr))
            try:
                with self.assertRaises(BoardError):
                    files.validate_state_root(root)
            finally:
                root.rmdir()
                moved.rename(root)

    def test_inherited_profile_acl_is_accepted_and_foreign_ace_is_refused(self):
        import win32security as security
        with tempfile.TemporaryDirectory() as temporary:
            root = private_dirs.ensure_private_dir(Path(temporary) / 'inherited')
            files.validate_state_root(root)
            descriptor = security.GetFileSecurity(str(root), security.DACL_SECURITY_INFORMATION)
            dacl = descriptor.GetSecurityDescriptorDacl()
            dacl.AddAccessAllowedAce(security.ACL_REVISION, 1, security.CreateWellKnownSid(security.WinWorldSid))
            security.SetNamedSecurityInfo(str(root), security.SE_FILE_OBJECT,
                                          security.DACL_SECURITY_INFORMATION, None, None, dacl, None)
            with self.assertRaisesRegex(BoardError, 'foreign ACE'):
                files.validate_state_root(root)

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
                state = transport.get_state_dir(link / 'state')
                self.assertEqual(state, link / 'state')
                with self.assertRaisesRegex(BoardError, 'linked component'):
                    transport._cold_start_preflight(state)
                self.assertFalse((outside / 'state').exists())
            finally:
                link.rmdir()
