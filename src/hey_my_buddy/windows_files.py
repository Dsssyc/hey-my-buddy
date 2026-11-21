"""Windows handle operations for private paths and junctions.

Security dependencies load only when a Windows boundary is used.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import struct

from .errors import BoardError
from .paths import native_path, windows_path

FILE_READ_ATTRIBUTES = 0x0080
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
DELETE = 0x00010000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
CREATE_NEW = 1
OPEN_EXISTING = 3
OPEN_ALWAYS = 4
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
FILE_TYPE_DISK = 1
FSCTL_SET_REPARSE_POINT = 0x000900A4


class _RenameInfo(ctypes.Structure):
    _fields_ = [("Flags", ctypes.c_uint32), ("RootDirectory", wintypes.HANDLE),
                ("FileNameLength", ctypes.c_uint32), ("FileName", ctypes.c_wchar * 1)]


_RENAME_KERNEL = None
if os.name == 'nt':
    # pywin32's public setter lacks FileRenameInfoEx/POSIX_SEMANTICS.
    _RENAME_KERNEL = ctypes.WinDLL("kernel32", use_last_error=True)
    _RENAME_KERNEL.SetFileInformationByHandle.restype = wintypes.BOOL
    _RENAME_KERNEL.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]


class WindowsFileAPI:
    def __init__(self):
        import msvcrt
        import pywintypes
        import win32file

        self.msvcrt = msvcrt
        self.pywintypes = pywintypes
        self.win32file = win32file
        self.kernel = _RENAME_KERNEL

    def _call(self, method, *args):
        try:
            return method(*args)
        except self.pywintypes.error as error:
            raise self._os_error(error.winerror) from error

    @staticmethod
    def _os_error(code):
        return ctypes.WinError(code)

    def _open(self, path, access, disposition, *, share_delete=False):
        return self._call(self.win32file.CreateFile, windows_path(path), access,
                          FILE_SHARE_READ | FILE_SHARE_WRITE | (FILE_SHARE_DELETE if share_delete else 0),
                          None, disposition,
                          FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS |
                          FILE_ATTRIBUTE_NORMAL, None)

    def info(self, handle):
        tag = self._call(self.win32file.GetFileInformationByHandleEx,
                         handle, self.win32file.FileAttributeTagInfo)
        return tag["FileAttributes"], tag["ReparseTag"]

    def file_id(self, handle):
        info = self._call(self.win32file.GetFileInformationByHandle, handle)
        return info[4], info[8], info[9]

    def final_path(self, handle):
        return self._call(self.win32file.GetFinalPathNameByHandle, handle, 0)

    def long_path(self, path):
        import win32api
        return self._call(win32api.GetLongPathNameW, windows_path(path))

    def file_type(self, handle):
        return self._call(self.win32file.GetFileType, handle)

    def fd(self, handle, flags):
        # open_osfhandle transfers ownership to the returned CRT descriptor.
        descriptor = self.msvcrt.open_osfhandle(int(handle), flags | os.O_BINARY | os.O_NOINHERIT)
        handle.Detach()
        return descriptor

    def delete(self, handle):
        self._call(self.win32file.SetFileInformationByHandle,
                   handle, self.win32file.FileDispositionInfo, True)

    def rename(self, descriptor, target):
        """Replace a name using the still-open, synchronized writer handle."""
        name = windows_path(target).encode('utf-16-le')
        # The Win32 entry point consumes a terminated WCHAR path while forming
        # the native rename request. FileNameLength excludes that terminator.
        size = _RenameInfo.FileName.offset + len(name) + 2
        buffer = ctypes.create_string_buffer(max(size, ctypes.sizeof(_RenameInfo)))
        info = _RenameInfo.from_buffer(buffer)
        info.Flags = 0x00000001 | 0x00000002  # REPLACE_IF_EXISTS | POSIX_SEMANTICS
        info.FileNameLength = len(name)
        ctypes.memmove(ctypes.addressof(buffer) + _RenameInfo.FileName.offset, name, len(name))
        handle = self.msvcrt.get_osfhandle(descriptor)
        if not self.kernel.SetFileInformationByHandle(handle, 22, buffer, size):  # FileRenameInfoEx
            raise self._os_error(ctypes.get_last_error())

    def close(self, handle):
        self._call(handle.Close)

    def mkdir(self, path):
        os.mkdir(windows_path(path))

    def junction(self, target, path):
        """Store an ordinary absolute target, extending only file API paths."""
        ordinary = windows_path(target, extended=False)
        substitute = ("\\??\\" + ordinary).encode("utf-16-le")
        display = ordinary.encode("utf-16-le")
        body = substitute + b"\0\0" + display + b"\0\0"
        data = struct.pack("<IHHHHHH", IO_REPARSE_TAG_MOUNT_POINT, 8 + len(body), 0,
                           0, len(substitute), len(substitute) + 2, len(display)) + body
        self.mkdir(path)
        try:
            handle = self._open(path, GENERIC_WRITE | DELETE, OPEN_EXISTING)
        except BaseException:
            os.rmdir(windows_path(path))
            raise
        try:
            self._call(self.win32file.DeviceIoControl, handle, FSCTL_SET_REPARSE_POINT, data, None)
        except BaseException:
            # Remove only the link entry through its handle, never its target.
            self.delete(handle)
            raise
        finally:
            self.close(handle)


READ_CONTROL = 0x00020000
WRITE_DAC = 0x00040000
FILE_ALL_ACCESS = 0x001F01FF
OBJECT_INHERIT_ACE = 0x1
CONTAINER_INHERIT_ACE = 0x2
FILE_SHARE_DELETE = 0x00000004
SYSTEM_SID = 'S-1-5-18'
ADMINISTRATORS_SID = 'S-1-5-32-544'
OWNER_RIGHTS_SID = 'S-1-3-4'


class _UnsafeDacl(BoardError):
    """A DACL failure for which the owner entry point may attempt repair."""


def _trustee_kind(sid):
    known = {
        'S-1-1-0': 'everyone',
        'S-1-3-0': 'creator-owner',
        'S-1-5-11': 'authenticated-users',
        'S-1-5-32-545': 'builtin-users',
        'S-1-15-2-1': 'all-application-packages',
        'S-1-15-2-2': 'all-restricted-application-packages',
    }
    if sid in known:
        return known[sid]
    if sid.startswith('S-1-15-'):
        return 'app-package-sid'
    if sid.startswith('S-1-5-80-'):
        return 'service-sid'
    if sid.startswith('S-1-5-32-'):
        return 'builtin-group-sid'
    return 'other-account-sid'


def _validate_root_owner(owner, user):
    if owner not in {user, ADMINISTRATORS_SID}:
        raise BoardError('PRIVATE_PATH_UNSAFE', 'State root has a foreign owner')


def validate_acl(owner, user, dacl_present, aces):
    """Accept only the specified owner and ordinary allow ACE shapes.

    SID strings and synthetic ACE lists make the policy testable on every OS.
    None means NULL DACL; an empty list is an existing, empty DACL.
    """
    _validate_root_owner(owner, user)
    if not dacl_present or aces is None:
        raise _UnsafeDacl('PRIVATE_PATH_UNSAFE', 'State root must have a non-NULL DACL')
    allowed = {user, SYSTEM_SID, ADMINISTRATORS_SID, OWNER_RIGHTS_SID}
    for ace in aces:
        if len(ace) != 3 or len(ace[0]) != 2:
            details = {'aceShape': 'malformed'}
        elif ace[0][0] != 0:
            details = {'aceType': ace[0][0], 'aceFlags': ace[0][1]}
        elif ace[2] not in allowed:
            details = {'aceType': 0, 'aceFlags': ace[0][1], 'trusteeKind': _trustee_kind(ace[2])}
        else:
            continue
        raise _UnsafeDacl('PRIVATE_PATH_UNSAFE', 'State root has an unsupported or foreign ACE', **details)


def _security():
    import win32api
    import win32con
    import win32security
    return win32api, win32con, win32security


def _root_security(api, handle, *, include_dacl):
    win32api, con, security = _security()
    token = api._call(security.OpenProcessToken, win32api.GetCurrentProcess(), con.TOKEN_QUERY)
    try:
        user, _ = api._call(security.GetTokenInformation, token, security.TokenUser)
    finally:
        api.close(token)
    information = security.OWNER_SECURITY_INFORMATION
    if include_dacl:
        information |= security.DACL_SECURITY_INFORMATION
    descriptor = api._call(security.GetSecurityInfo, handle, security.SE_FILE_OBJECT, information)
    sid = security.ConvertSidToStringSid
    return security, sid(user), sid(descriptor.GetSecurityDescriptorOwner()), descriptor


def _validate_root_acl(api, handle):
    security, user, owner, descriptor = _root_security(api, handle, include_dacl=True)
    dacl = descriptor.GetSecurityDescriptorDacl()
    control, _ = descriptor.GetSecurityDescriptorControl()
    present = bool(control & security.SE_DACL_PRESENT)
    aces = None
    if present and dacl is not None:
        # Check owner before decoding ACEs so a foreign owner is never repaired even
        # when the DACL holds an ACE type pywin32 cannot decode (such as callback ACEs).
        _validate_root_owner(owner, user)
        sid = security.ConvertSidToStringSid
        aces = []
        for index in range(dacl.GetAceCount()):
            try:
                ace = dacl.GetAce(index)
            except NotImplementedError as error:
                raise _UnsafeDacl('PRIVATE_PATH_UNSAFE', 'State root has an unsupported or foreign ACE',
                                  aceShape='unsupported') from error
            # Preserve extended ACE shapes. The three-field form stores its SID in slot 2.
            aces.append((ace[0], ace[1], sid(ace[2])) if len(ace) == 3 else ace)
    validate_acl(owner, user, present, aces)


def _repair_root_dacl(api, handle):
    security, user, owner, _ = _root_security(api, handle, include_dacl=False)
    _validate_root_owner(owner, user)
    dacl = security.ACL()
    for trustee in dict.fromkeys((user, SYSTEM_SID, ADMINISTRATORS_SID)):
        api._call(dacl.AddAccessAllowedAceEx, security.ACL_REVISION,
                  OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE, FILE_ALL_ACCESS,
                  security.ConvertStringSidToSid(trustee))
    api._call(security.SetSecurityInfo, handle, security.SE_FILE_OBJECT,
              security.DACL_SECURITY_INFORMATION | security.PROTECTED_DACL_SECURITY_INFORMATION,
              None, None, dacl, None)
    _validate_root_acl(api, handle)


def _check_root_directory(api, handle):
    attributes, _ = api.info(handle)
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT or not attributes & FILE_ATTRIBUTE_DIRECTORY:
        raise BoardError('PRIVATE_PATH_UNSAFE', 'State root must be a real directory')


def _normalize_win_path(value: str) -> str:
    return value.rstrip('\\').casefold()


def _check_root_final_path(api, handle, expected_final: str) -> None:
    if _normalize_win_path(api.final_path(handle)) != _normalize_win_path(expected_final):
        raise BoardError('PRIVATE_PATH_UNSAFE', 'State root handle resolved outside the target path')


def check_state_root(path, *, create=False):
    """Check the Windows state boundary once at installation or cold start."""
    from .private_dirs import _absolute, linked
    path = _absolute(path)
    for component in (*reversed(path.parents), path):
        if linked(component):
            raise BoardError('PRIVATE_PATH_UNSAFE', 'State root contains a linked component', path=str(component))
    if create:
        native_path(path).mkdir(parents=True, exist_ok=True)
    api = WindowsFileAPI()
    try:
        access = READ_CONTROL | FILE_READ_ATTRIBUTES
        reader = api._open(path, access, OPEN_EXISTING, share_delete=True)
        writer = None
        try:
            _check_root_directory(api, reader)
            expected_final = api.long_path(path)
            _check_root_final_path(api, reader, expected_final)
            try:
                _validate_root_acl(api, reader)
                return
            except _UnsafeDacl:
                if not create:
                    raise
            reader_id = api.file_id(reader)
            writer = api._open(path, access | WRITE_DAC, OPEN_EXISTING, share_delete=True)
            try:
                _check_root_directory(api, writer)
                if api.file_id(writer) != reader_id:
                    raise BoardError('PRIVATE_PATH_UNSAFE', 'State root changed while opening for ACL repair')
                _check_root_final_path(api, writer, expected_final)
            except BaseException:
                api.close(writer)
                writer = None
                raise
        finally:
            api.close(reader)
        try:
            _repair_root_dacl(api, writer)
        finally:
            api.close(writer)
    except BoardError as error:
        error.details['path'] = str(path)
        raise


def open_regular_fd(path, flags, mode=0o600, *, delete=False, api=None):
    """Open an ordinary path with delete sharing and transfer ownership to the CRT."""
    api = api if api is not None else WindowsFileAPI()
    access = DELETE if delete else 0
    if flags & os.O_RDWR:
        access |= GENERIC_READ | GENERIC_WRITE
    elif flags & os.O_WRONLY:
        access |= GENERIC_WRITE
    else:
        access |= GENERIC_READ
    disposition = CREATE_NEW if flags & os.O_EXCL else OPEN_ALWAYS if flags & os.O_CREAT else OPEN_EXISTING
    handle = api._open(path, access, disposition, share_delete=True)
    try:
        attributes, _ = api.info(handle)
        if attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) or api.file_type(handle) != FILE_TYPE_DISK:
            raise BoardError('PRIVATE_PATH_UNSAFE', 'Path is not an ordinary file', path=str(path))
        return api.fd(handle, flags)
    except BaseException:
        api.close(handle)
        raise


def unlink_regular_file(path, *, api=None):
    """Remove one ordinary file name without changing other hardlinks' attributes."""
    _remove_entry(path, directory=False, api=api)


def remove_directory(path, *, api=None):
    """Remove an empty ordinary directory, including its read-only attribute."""
    _remove_entry(path, directory=True, api=api)


def _remove_entry(path, *, directory, api):
    api = api if api is not None else WindowsFileAPI()
    try:
        handle = api._open(path, DELETE | FILE_READ_ATTRIBUTES, OPEN_EXISTING, share_delete=True)
        try:
            attributes, _ = api.info(handle)
            if (bool(attributes & FILE_ATTRIBUTE_DIRECTORY) != directory
                    or attributes & FILE_ATTRIBUTE_REPARSE_POINT or api.file_type(handle) != FILE_TYPE_DISK):
                raise BoardError('PRIVATE_PATH_UNSAFE',
                                 'Path is not an ordinary directory' if directory else 'Path is not an ordinary file',
                                 path=str(path))
            # FileDispositionInfoEx: DELETE | POSIX_SEMANTICS | IGNORE_READONLY_ATTRIBUTE.
            flags = wintypes.DWORD(0x1 | 0x2 | 0x10)
            if not api.kernel.SetFileInformationByHandle(int(handle), 21, ctypes.byref(flags), ctypes.sizeof(flags)):
                raise api._os_error(ctypes.get_last_error())
        finally:
            api.close(handle)
    except OSError as error:
        # Handle-only Win32 failures do not carry the name being operated on.
        # Preserve the native category and error numbers, adding that known name.
        if error.filename is None:
            error.filename = os.fspath(path)
        raise
