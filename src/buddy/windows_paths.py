"""Windows workspace file operations with pinned, non-reparse ancestors.

Every operation retains a handle to each ancestor without FILE_SHARE_DELETE.
CreateFileW opens reparse points themselves; a returned handle is checked before
its contents or path may be used. The API is injectable for POSIX contract tests.
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
import uuid

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
_FORBIDDEN = set('<>:"/\\|?*')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"{stem}{digit}" for stem in ("COM", "LPT")
                                                  for digit in "123456789¹²³"}


def validate_git_path(value):
    """Refuse names that Win32 would reinterpret or alias."""
    if value == ".":
        return
    for part in value.split("/"):
        stem = part.split(".", 1)[0]
        if (not part or part in (".", "..") or part[-1] in " ." or stem.endswith(" ") or
                any(char in _FORBIDDEN or ord(char) < 32 for char in part) or
                stem.upper() in _RESERVED):
            raise BoardError("WORKSPACE_UNSUPPORTED", "A Git path is not portable to Windows", path=value)


def validate_unique(paths):
    folded = {}
    for path in paths:
        parts = path.split("/")
        for count in range(1, len(parts) + 1):
            prefix = "/".join(parts[:count])
            key = prefix.casefold()
            if key in folded and folded[key] != prefix:
                raise BoardError("WORKSPACE_UNSUPPORTED", "Git paths collide on Windows",
                                 paths=[folded[key], prefix])
            folded[key] = prefix


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

    def _open(self, path, access, disposition):
        handle = self.kernel.CreateFileW(str(path), access, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                         None, disposition,
                                         FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS |
                                         FILE_ATTRIBUTE_NORMAL, None)
        if handle == ctypes.c_void_p(-1).value:
            self._error()
        return handle

    def open_dir(self, path):
        return self._open(path, FILE_READ_ATTRIBUTES, OPEN_EXISTING)

    def open_leaf(self, path, *, action="read"):
        access, disposition = {
            "read": (GENERIC_READ, OPEN_EXISTING),
            "inspect": (FILE_READ_ATTRIBUTES, OPEN_EXISTING),
            "create": (GENERIC_WRITE, CREATE_NEW),
            "lock": (GENERIC_READ | GENERIC_WRITE, OPEN_ALWAYS),
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


def _checked(api, handle, name, *, directory=False, allow_symlink=False):
    attributes, tag = api.info(handle)
    if name is not None and api.name(handle) != name:
        raise BoardError("WORKSPACE_UNSUPPORTED", "A Windows short-name or case alias is not a workspace path", path=name)
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        if allow_symlink and tag == IO_REPARSE_TAG_SYMLINK:
            return "symlink"
        raise BoardError("WORKSPACE_UNSUPPORTED", "A workspace path contains a reparse point", path=name)
    if directory:
        if not attributes & FILE_ATTRIBUTE_DIRECTORY:
            raise BoardError("WORKSPACE_UNSUPPORTED", "A workspace parent is not a directory", path=name)
        return "directory"
    if attributes & FILE_ATTRIBUTE_DIRECTORY or api.file_type(handle) != FILE_TYPE_DISK:
        raise BoardError("WORKSPACE_UNSUPPORTED", "Only regular files and symlinks are supported", path=name)
    return "file"


@contextmanager
def parent(path, *, create=False, api=None):
    """Pin the volume root and each parent until the leaf operation finishes."""
    api = api if api is not None else WindowsFileAPI()
    path = Path(path)
    if not path.is_absolute() or path.name in ("", ".", ".."):
        raise BoardError("INVALID_WORKSPACE", "A Windows workspace file needs an absolute path")
    parent_path = path.parent
    ancestors = list(reversed(parent_path.parents)) + [parent_path]
    handles = []
    try:
        for current in ancestors:
            if create and current != Path(current.anchor):
                try:
                    api.mkdir(current)
                except FileExistsError:
                    pass
            handle = api.open_dir(current)
            handles.append(handle)
            _checked(api, handle, current.name or None, directory=True)
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


def _open_checked(api, path, *, action="read", allow_symlink=False):
    handle = api.open_leaf(path, action=action)
    try:
        kind = _checked(api, handle, path.name, allow_symlink=allow_symlink)
        return handle, kind
    except BaseException:
        api.close(handle)
        raise


def read(path, *, allow_symlink=False, missing_ok=True, api=None):
    """Read a held regular file, or the text of an explicitly allowed Git link."""
    with parent(path, api=api) as (api, path):
        try:
            handle, kind = _open_checked(api, path, allow_symlink=allow_symlink)
        except FileNotFoundError:
            if missing_ok:
                return None
            raise
        if kind == "symlink":
            try:
                return "120000", os.fsencode(api.readlink(handle))
            finally:
                api.close(handle)
        try:
            descriptor = api.fd(handle, os.O_RDONLY)
        except BaseException:
            api.close(handle)
            raise
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            data = stream.read()
            after = os.fstat(stream.fileno())
        keys = ("st_ino", "st_dev", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")
        if any(getattr(before, key) != getattr(after, key) for key in keys):
            raise BoardError("WORKSPACE_CHANGED", "A file changed during snapshot capture", path=str(path))
        return ("100755" if before.st_mode & 0o111 else "100644", data) if allow_symlink else data


def _write_held(api, path, data, *, sync=False):
    handle, _ = _open_checked(api, path, action="create")
    try:
        descriptor = api.fd(handle, os.O_WRONLY)
    except BaseException:
        api.close(handle)
        raise
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        if sync:
            stream.flush()
            os.fsync(stream.fileno())


def write_new(path, data, *, api=None, create_parents=False, sync=False):
    # The Git mode remains in the index; Windows chmod cannot set its execute bit.
    with parent(path, create=create_parents, api=api) as (api, path):
        _write_held(api, path, data, sync=sync)


def _remove_held(api, path, *, missing_ok=False):
    try:
        handle, _ = _open_checked(api, path, action="delete", allow_symlink=True)
    except FileNotFoundError:
        if missing_ok:
            return
        raise
    try:
        api.delete(handle)
    finally:
        api.close(handle)


def remove(path, *, missing_ok=False, api=None, create_parents=False):
    with parent(path, create=create_parents, api=api) as (api, path):
        _remove_held(api, path, missing_ok=missing_ok)


def restore(path, data, *, symlink_target=None, api=None):
    """Replace one recovery leaf while retaining every parent handle."""
    with parent(path, create=True, api=api) as (api, path):
        _remove_held(api, path, missing_ok=True)
        if symlink_target is not None:
            api.symlink(symlink_target, path, directory=(path.parent / symlink_target).is_dir())
        elif data is not None:
            _write_held(api, path, data)


def symlink(path, target, *, api=None):
    with parent(path, create=True, api=api) as (api, path):
        # Windows needs this type bit at creation; the Git entry remains link text.
        directory = (path.parent / target).is_dir()
        api.symlink(target, path, directory=directory)


def lock_fd(path, *, api=None):
    with parent(path, api=api) as (api, path):
        handle, _ = _open_checked(api, path, action="lock")
        try:
            return api.fd(handle, os.O_RDWR)
        except BaseException:
            api.close(handle)
            raise


def write_once(path, data, *, api=None):
    """Publish a flushed private file by compare-and-create hard link."""
    with parent(path, api=api) as (api, path):
        existing = read(path, api=api)
        if existing is not None:
            if existing != data:
                raise BoardError("WORKSPACE_CONFLICT", "An immutable workspace artifact already has different contents", path=str(path))
            return
        temporary = path.with_name(".pending-" + uuid.uuid4().hex)
        try:
            write_new(temporary, data, api=api, sync=True)
            try:
                api.link(temporary, path)
            except FileExistsError:
                if read(path, api=api) != data:
                    raise BoardError("WORKSPACE_CONFLICT", "An immutable workspace artifact collided", path=str(path))
            from .backup import sync_dir
            sync_dir(path.parent)
        finally:
            remove(temporary, missing_ok=True, api=api)
