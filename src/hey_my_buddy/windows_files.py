"""Windows handle operations shared by workspace and private path policies.

Security dependencies load only when a Windows boundary is used.
"""
from __future__ import annotations

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


class _RenameInfo(ctypes.Structure):
    _fields_ = [("Flags", ctypes.c_uint32), ("RootDirectory", wintypes.HANDLE),
                ("FileNameLength", ctypes.c_uint32), ("FileName", ctypes.c_wchar * 1)]


class WindowsFileAPI:
    def __init__(self):
        import msvcrt
        import pywintypes
        import win32file

        self.msvcrt = msvcrt
        self.pywintypes = pywintypes
        self.win32file = win32file
        self.kernel = None

    def _call(self, method, *args):
        try:
            return method(*args)
        except self.pywintypes.error as error:
            raise self._os_error(error.winerror) from error

    @staticmethod
    def _os_error(code):
        if code in (2, 3):
            return FileNotFoundError(errno.ENOENT, "Windows path does not exist")
        if code in (80, 183):
            return FileExistsError(errno.EEXIST, "Windows path already exists")
        return ctypes.WinError(code)

    def _open(self, path, access, disposition, *, share_delete=False):
        return self._call(self.win32file.CreateFile, str(path), access,
                          FILE_SHARE_READ | FILE_SHARE_WRITE | (FILE_SHARE_DELETE if share_delete else 0),
                          None, disposition,
                          FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS |
                          FILE_ATTRIBUTE_NORMAL, None)

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
        tag = self._call(self.win32file.GetFileInformationByHandleEx,
                         handle, self.win32file.FileAttributeTagInfo)
        return tag["FileAttributes"], tag["ReparseTag"]

    def final_path(self, handle):
        return self._call(self.win32file.GetFinalPathNameByHandle, handle, 0)

    def name(self, handle):
        return ntpath.basename(self.final_path(handle).rstrip("\\"))

    def long_path(self, path):
        return self._call(self.win32file.GetLongPathName, str(path))

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
        name = str(Path(target).absolute()).encode('utf-16-le')
        # The Win32 entry point consumes a terminated WCHAR path while forming
        # the native rename request. FileNameLength excludes that terminator.
        size = _RenameInfo.FileName.offset + len(name) + 2
        buffer = ctypes.create_string_buffer(max(size, ctypes.sizeof(_RenameInfo)))
        info = _RenameInfo.from_buffer(buffer)
        info.Flags = 0x00000001 | 0x00000002  # REPLACE_IF_EXISTS | POSIX_SEMANTICS
        info.FileNameLength = len(name)
        ctypes.memmove(ctypes.addressof(buffer) + _RenameInfo.FileName.offset, name, len(name))
        if self.kernel is None:
            # pywin32's public setter lacks FileRenameInfoEx/POSIX_SEMANTICS.
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            method = self.kernel.SetFileInformationByHandle
            method.restype = wintypes.BOOL
            method.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        handle = self.msvcrt.get_osfhandle(descriptor)
        if not self.kernel.SetFileInformationByHandle(handle, 22, buffer, size):  # FileRenameInfoEx
            raise self._os_error(ctypes.get_last_error())

    def close(self, handle):
        self._call(handle.Close)

    def mkdir(self, path):
        os.mkdir(path)

    def readlink(self, handle):
        # Python's Windows os.readlink returns the substitute path, which can
        # add a \\?\ prefix. Git stores the original print-name text.
        raw = self._call(self.win32file.DeviceIoControl,
                         handle, FSCTL_GET_REPARSE_POINT, None, 16 * 1024)
        return _link_print_name(raw)

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
        raise BoardError('PRIVATE_PATH_UNSAFE', 'State root has a foreign owner')
    if not dacl_present or aces is None:
        raise BoardError('PRIVATE_PATH_UNSAFE', 'State root must have a non-NULL DACL')
    for ace in aces:
        if (len(ace) != 3 or len(ace[0]) != 2 or ace[0][0] != 0
                or ace[2] not in allowed):
            raise BoardError('PRIVATE_PATH_UNSAFE', 'State root has an unsupported or foreign ACE')


def _security():
    import win32api
    import win32con
    import win32security
    return win32api, win32con, win32security


def _validate_root_acl(handle):
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


def check_state_root(path, *, create=False):
    """Check the Windows state boundary once at installation or cold start."""
    from .private_dirs import _absolute, linked
    path = _absolute(path)
    for component in (*reversed(path.parents), path):
        if linked(component):
            raise BoardError('PRIVATE_PATH_UNSAFE', 'State root contains a linked component', path=str(component))
    if create:
        path.mkdir(parents=True, exist_ok=True)
    api = WindowsFileAPI()
    handle = api._open(path, READ_CONTROL | FILE_READ_ATTRIBUTES, OPEN_EXISTING, share_delete=True)
    try:
        attributes, _ = api.info(handle)
        if attributes & FILE_ATTRIBUTE_REPARSE_POINT or not attributes & FILE_ATTRIBUTE_DIRECTORY:
            raise BoardError('PRIVATE_PATH_UNSAFE', 'State root must be a real directory', path=str(path))
        _validate_root_acl(handle)
    except BoardError as error:
        error.details['path'] = str(path)
        raise
    finally:
        api.close(handle)


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
