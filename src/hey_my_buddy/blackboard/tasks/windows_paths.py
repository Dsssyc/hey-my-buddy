"""Windows workspace file operations with pinned, non-reparse ancestors.

Every operation retains a handle to each ancestor without FILE_SHARE_DELETE.
CreateFileW opens reparse points themselves; a returned handle is checked before
its contents or path may be used. The API is injectable for POSIX contract tests.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path

from ... import private_files
from ...windows_files import (WindowsFileAPI, FILE_ATTRIBUTE_REPARSE_POINT,
                              FILE_ATTRIBUTE_DIRECTORY, FILE_TYPE_DISK, IO_REPARSE_TAG_SYMLINK)
from ...paths import is_absolute_path
from ...errors import BoardError

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
    if not is_absolute_path(path) or path.name in ("", ".", ".."):
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


def _write_held(api, path, data):
    handle, _ = _open_checked(api, path, action="create")
    try:
        descriptor = api.fd(handle, os.O_WRONLY)
    except BaseException:
        api.close(handle)
        raise
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)


def write_new(path, data, *, api=None, create_parents=False):
    # The Git mode remains in the index; Windows chmod cannot set its execute bit.
    with parent(path, create=create_parents, api=api) as (api, path):
        _write_held(api, path, data)


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


def lock_fd(path, *, create=True, api=None):
    with parent(path, api=api) as (api, path):
        handle, _ = _open_checked(api, path, action="lock" if create else "lock-existing")
        try:
            return api.fd(handle, os.O_RDWR)
        except BaseException:
            api.close(handle)
            raise


class _WorkspacePublishOperations:
    """Use the workspace's held Windows handles for shared byte publication."""

    def __init__(self, api):
        self.api = api

    def open(self, path):
        handle, _ = _open_checked(self.api, path, action="create")
        try:
            return self.api.fd(handle, os.O_WRONLY)
        except BaseException:
            self.api.close(handle)
            raise

    def read(self, path):
        contents = read(path, api=self.api)
        if contents is None:
            raise FileNotFoundError(path)
        return contents

    def link(self, source, target):
        self.api.link(source, target)

    def unlink(self, path):
        remove(path, missing_ok=True, api=self.api)


@contextmanager
def artifact_publication(path):
    """Translate immutable workspace conflicts at one shared boundary."""
    try:
        yield
    except FileExistsError as error:
        raise BoardError("WORKSPACE_CONFLICT", "An immutable workspace artifact already has different contents",
                         path=str(path)) from error


def write_once(path, data, *, api=None):
    """Publish an immutable artifact under the held Windows workspace parent."""
    with parent(path, api=api) as (api, path), artifact_publication(path):
        private_files.publish_bytes(path, data, immutable=True,
                                    operations=_WorkspacePublishOperations(api))
