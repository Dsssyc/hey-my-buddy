"""Windows handle operations shared by workspace and private path policies.

Security dependencies load only when a Windows boundary is used.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import errno
import ntpath
import os
from pathlib import Path
import struct

from .errors import BoardError

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
IO_REPARSE_TAG_SYMLINK = 0xA000000C
FILE_TYPE_DISK = 1
FSCTL_GET_REPARSE_POINT = 0x000900A8
def _link_print_name(raw):
    if len(raw) < 20:
        raise OSError("invalid symbolic-link reparse data")
    tag, length, _, _, _, offset, size, _ = struct.unpack_from("<IHHHHHHI", raw)
    start = 20 + offset
    if (tag != IO_REPARSE_TAG_SYMLINK or length < 12 or length + 8 > len(raw) or
            not size or offset % 2 or size % 2 or start + size > length + 8):
        raise OSError("invalid symbolic-link print name")
    return raw[start:start + size].decode("utf-16-le")


class _AttributeTag(ctypes.Structure):
    _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]


class _Disposition(ctypes.Structure):
    _fields_ = [("DeleteFile", ctypes.c_ubyte)]


class WindowsFileAPI:
    def __init__(self):
        import msvcrt

        self.msvcrt = msvcrt
        self.kernel = kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateFileW": (wintypes.HANDLE, [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]),
            "GetFileInformationByHandleEx": (wintypes.BOOL, [wintypes.HANDLE, ctypes.c_int,
                                                               ctypes.c_void_p, wintypes.DWORD]),
            "GetFinalPathNameByHandleW": (wintypes.DWORD, [wintypes.HANDLE, wintypes.LPWSTR,
                                                            wintypes.DWORD, wintypes.DWORD]),
            "GetFileType": (wintypes.DWORD, [wintypes.HANDLE]),
            "SetFileInformationByHandle": (wintypes.BOOL, [wintypes.HANDLE, ctypes.c_int,
                                                             ctypes.c_void_p, wintypes.DWORD]),
            "DeviceIoControl": (wintypes.BOOL, [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                                                 wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                                 ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]),
            "CloseHandle": (wintypes.BOOL, [wintypes.HANDLE]),
        }
        for name, (result, arguments) in signatures.items():
            method = getattr(kernel, name)
            method.restype, method.argtypes = result, arguments

    @staticmethod
    def _error():
        code = ctypes.get_last_error()
        if code in (2, 3):
            raise FileNotFoundError(errno.ENOENT, "Windows path does not exist")
        if code in (80, 183):
            raise FileExistsError(errno.EEXIST, "Windows path already exists")
        raise ctypes.WinError(code)

    def _open(self, path, access, disposition, *, share_delete=False):
        handle = self.kernel.CreateFileW(str(path), access, FILE_SHARE_READ | FILE_SHARE_WRITE | (4 if share_delete else 0),
                                         None, disposition,
                                         FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS |
                                         FILE_ATTRIBUTE_NORMAL, None)
        if handle == ctypes.c_void_p(-1).value:
            self._error()
        return handle

    def open_dir(self, path):
        return self._open(path, GENERIC_READ | 0x00020000, OPEN_EXISTING)

    def open_leaf(self, path, *, action="read"):
        access, disposition = {
            "read": (GENERIC_READ, OPEN_EXISTING),
            "inspect": (FILE_READ_ATTRIBUTES, OPEN_EXISTING),
            "create": (GENERIC_WRITE, CREATE_NEW),
            "lock": (GENERIC_READ | GENERIC_WRITE, OPEN_ALWAYS),
            "lock-existing": (GENERIC_READ | GENERIC_WRITE, OPEN_EXISTING),
            "delete": (DELETE | FILE_READ_ATTRIBUTES, OPEN_EXISTING),
        }[action]
        return self._open(path, access, disposition)

    def info(self, handle):
        tag = _AttributeTag()
        if not self.kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(tag), ctypes.sizeof(tag)):
            self._error()
        return tag.FileAttributes, tag.ReparseTag

    def name(self, handle):
        size = self.kernel.GetFinalPathNameByHandleW(handle, None, 0, 0)
        if not size:
            self._error()
        buffer = ctypes.create_unicode_buffer(size + 1)
        used = self.kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not used or used >= len(buffer):
            self._error()
        return ntpath.basename(buffer.value.rstrip("\\"))

    def file_type(self, handle):
        return self.kernel.GetFileType(handle)

    def fd(self, handle, flags):
        # open_osfhandle transfers ownership to the returned CRT descriptor.
        return self.msvcrt.open_osfhandle(handle, flags | os.O_BINARY | os.O_NOINHERIT)

    def delete(self, handle):
        disposition = _Disposition(1)
        if not self.kernel.SetFileInformationByHandle(handle, 4, ctypes.byref(disposition),
                                                      ctypes.sizeof(disposition)):
            self._error()

    def close(self, handle):
        if not self.kernel.CloseHandle(handle):
            self._error()

    def mkdir(self, path):
        os.mkdir(path)

    def readlink(self, handle):
        # Python's Windows os.readlink returns the substitute path, which can
        # add a \\?\ prefix. Git stores the original print-name text.
        buffer = ctypes.create_string_buffer(16 * 1024)
        returned = wintypes.DWORD()
        if not self.kernel.DeviceIoControl(handle, FSCTL_GET_REPARSE_POINT, None, 0,
                                            buffer, len(buffer), ctypes.byref(returned), None):
            self._error()
        return _link_print_name(buffer.raw[:returned.value])

    def symlink(self, target, path, *, directory=False):
        os.symlink(target, path, target_is_directory=directory)

    def link(self, source, target):
        os.link(source, target)


READ_CONTROL = 0x00020000
FILE_SHARE_DELETE = 0x00000004
SYSTEM_SID = 'S-1-5-18'
ADMINISTRATORS_SID = 'S-1-5-32-544'
OWNER_RIGHTS_SID = 'S-1-3-4'


def validate_acl(owner, user, dacl_present, aces):
    """Accept only the specified owner and ordinary allow ACE shapes.

    SID strings and synthetic ACE lists make the policy testable on every OS.
    None means NULL DACL; an empty list is an existing, empty DACL.
    """
    allowed = {user, SYSTEM_SID, ADMINISTRATORS_SID, OWNER_RIGHTS_SID}
    if owner not in {user, ADMINISTRATORS_SID}:
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Private object has a foreign owner')
    if not dacl_present or aces is None:
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Private object must have a non-NULL DACL')
    for ace in aces:
        if (len(ace) != 3 or len(ace[0]) != 2 or ace[0][0] != 0
                or ace[2] not in allowed):
            raise BoardError('PRIVATE_PATH_UNSAFE', 'Private object has an unsupported or foreign ACE')


def _security():
    import win32api
    import win32con
    import win32security
    return win32api, win32con, win32security


def validate_private_handle(handle):
    api, con, security = _security()
    token = security.OpenProcessToken(api.GetCurrentProcess(), con.TOKEN_QUERY)
    try:
        user = security.GetTokenInformation(token, security.TokenUser)[0]
    finally:
        token.Close()
    descriptor = security.GetSecurityInfo(handle, security.SE_FILE_OBJECT,
        security.OWNER_SECURITY_INFORMATION | security.DACL_SECURITY_INFORMATION)
    dacl = descriptor.GetSecurityDescriptorDacl()
    control, _ = descriptor.GetSecurityDescriptorControl()
    sid = security.ConvertSidToStringSid
    aces = None if dacl is None or not control & security.SE_DACL_PRESENT else [dacl.GetAce(index) for index in range(dacl.GetAceCount())]
    if aces is not None:
        # Preserve all ACE shapes. Only ordinary allow ACEs have a trustee in slot 2.
        aces = [(ace[0], ace[1], sid(ace[2])) if len(ace) == 3 else ace for ace in aces]
    validate_acl(sid(descriptor.GetSecurityDescriptorOwner()), sid(user),
                 bool(control & security.SE_DACL_PRESENT), aces)


_owner_initialized = False


def initialize_process_owner():
    """Set an elevated process's default owner once, before creating private state."""
    global _owner_initialized
    if os.name != 'nt' or _owner_initialized:
        return
    api, con, security = _security()
    try:
        token = security.OpenProcessToken(api.GetCurrentProcess(), con.TOKEN_QUERY | con.TOKEN_ADJUST_DEFAULT)
        try:
            if security.GetTokenInformation(token, security.TokenElevation):
                user = security.GetTokenInformation(token, security.TokenUser)[0]
                security.SetTokenInformation(token, security.TokenOwner, user)
        finally:
            token.Close()
    except Exception as error:
        raise BoardError('PRIVATE_OWNER_SETUP_FAILED',
                         'Cannot set the elevated process default owner to the current user',
                         reason=str(error)) from error
    _owner_initialized = True


def _check(api, handle, path, *, directory, private=False):
    attributes, _ = api.info(handle)
    if ((path.name and api.name(handle) != path.name)
            or attributes & FILE_ATTRIBUTE_REPARSE_POINT
            or bool(attributes & FILE_ATTRIBUTE_DIRECTORY) != directory
            or api.file_type(handle) != FILE_TYPE_DISK):
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Private path has an alias, reparse point or invalid type', path=str(path))
    if private:
        validate_private_handle(handle)


@contextmanager
def parent(path, *, create=False, api=None):
    """Hold every real ancestor without delete sharing; validate the private parent.

    Existing volume/user ancestors need only be real. Each new directory and the
    final private parent are ACL-checked while their handles remain held.
    """
    api = api if api is not None else WindowsFileAPI()
    path = Path(path).absolute()
    if '..' in path.parts:
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Private path contains a parent traversal', path=str(path))
    handles = []
    try:
        for current in (*reversed(path.parent.parents), path.parent):
            created = False
            try:
                handle = api.open_dir(current)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                created = True
                handle = api.open_dir(current)
            handles.append(handle)
            _check(api, handle, current, directory=True, private=created or current == path.parent)
        yield api, path
    finally:
        close_error = None
        for handle in reversed(handles):
            try:
                api.close(handle)
            except OSError as error:
                if close_error is None:
                    close_error = error
        if close_error is not None:
            raise close_error


def open_regular_fd(path, flags, mode=0o600):
    """Open under held ancestors, then transfer the checked leaf to the CRT."""
    with parent(path) as (api, path):
        return open_held_regular_fd(api, path, flags)


def open_held_regular_fd(api, path, flags):
    """Open one leaf while the caller retains its checked parent handles."""
    if flags & os.O_TRUNC and not flags & os.O_EXCL:
        raise BoardError('PRIVATE_PATH_UNSAFE', 'Unsafe ordinary-file open', path=str(path))
    access = READ_CONTROL
    if flags & os.O_RDWR:
        access |= GENERIC_READ | GENERIC_WRITE
    elif flags & os.O_WRONLY:
        access |= GENERIC_WRITE
    else:
        access |= GENERIC_READ
    disposition = CREATE_NEW if flags & os.O_EXCL else OPEN_ALWAYS if flags & os.O_CREAT else OPEN_EXISTING
    handle = api._open(path, access, disposition, share_delete=True)
    try:
        _check(api, handle, path, directory=False, private=True)
        return api.fd(handle, flags)
    except BaseException:
        api.close(handle)
        raise


def validate_directory(path):
    with parent(Path(path) / '.private-directory-check'):
        pass


def ensure_directory(path):
    with parent(Path(path) / '.private-directory-check', create=True):
        pass
    return Path(path)
