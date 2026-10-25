"""Windows workspace file operations with pinned, non-reparse ancestors.

Every operation retains a handle to each ancestor without FILE_SHARE_DELETE.
CreateFileW opens reparse points themselves; a returned handle is checked before
its contents or path may be used. The API is injectable for POSIX contract tests.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path, PureWindowsPath

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


def symlink_types(root, entries, targets):
    """Prove link types from the complete Git tree, without following disk paths."""
    directories = {""}
    for path in entries:
        parts = path.split("/")
        directories.update("/".join(parts[:count]) for count in range(1, len(parts)))
    names = {path.casefold(): path for path in (*entries, *directories)}
    root = PureWindowsPath(root)
    result = {}
    for link, target in targets.items():
        def refuse(reason):
            raise BoardError("WORKSPACE_UNSUPPORTED",
                             f"Cannot create Windows symlink {link}: {reason}; "
                             "use a target inside the Git tree and retry workspace preparation",
                             path=link, target=target, reason=reason)

        def resolve(parent, text, active):
            if not text or "\0" in text:
                refuse("the target is empty or contains a NUL character")
            anchored = PureWindowsPath(text)
            if anchored.anchor:
                if anchored.drive.casefold() != root.drive.casefold() or not anchored.root:
                    refuse("the target is outside the repository or depends on a drive's current directory")
                try:
                    text = anchored.relative_to(root).as_posix()
                except ValueError:
                    refuse("the absolute target points outside the repository")
                parent = []
            resolved = list(parent)
            for part in text.replace("\\", "/").split("/"):
                if "/".join(resolved) not in directories:
                    refuse("the target traverses a file")
                if part in ("", "."):
                    continue
                if part == "..":
                    if not resolved:
                        refuse("the target points outside the repository")
                    resolved.pop()
                    continue
                candidate = names.get("/".join([*resolved, part]).casefold())
                if candidate is None:
                    refuse(f"the target is absent from the Git tree at {'/'.join([*resolved, part])}")
                if candidate in targets:
                    if candidate in active:
                        refuse("the target contains a symlink cycle")
                    resolved = resolve(resolved, targets[candidate], active | {candidate})
                elif candidate in entries or candidate in directories:
                    resolved = candidate.split("/")
            return resolved

        resolved = resolve(link.split("/")[:-1], target, {link})
        result[link] = "/".join(resolved) in directories
    return result


def _checked(api, handle, name, *, directory=False, allow_symlink=False, long_name=False):
    attributes, tag = api.info(handle)
    if name is not None:
        expected = Path(api.long_path(name)).name if long_name else name
        observed = api.name(handle)
        different = observed.casefold() != expected.casefold() if long_name else observed != expected
        if different:
            raise BoardError("WORKSPACE_UNSUPPORTED", "A Windows short-name or case alias is not a workspace path",
                             path=str(name))
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
def parent(path, *, create=False, api=None, repository=None):
    """Pin the volume root and each parent until the leaf operation finishes."""
    api = api if api is not None else WindowsFileAPI()
    path = Path(path)
    if not is_absolute_path(path) or path.name in ("", ".", ".."):
        raise BoardError("INVALID_WORKSPACE", "A Windows workspace file needs an absolute path")
    parent_path = path.parent
    repository = Path(repository).absolute() if repository is not None else None
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
            outside_git = (repository is not None and bool(current.name)
                           and (current == repository or current in repository.parents))
            _checked(api, handle, current if outside_git else current.name or None,
                     directory=True, long_name=outside_git)
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


def read(path, *, allow_symlink=False, missing_ok=True, api=None, repository=None):
    """Read a held regular file, or the text of an explicitly allowed Git link."""
    with parent(path, api=api, repository=repository) as (api, path):
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


def write_new(path, data, *, api=None, create_parents=False, repository=None):
    # The Git mode remains in the index; Windows chmod cannot set its execute bit.
    with parent(path, create=create_parents, api=api, repository=repository) as (api, path):
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


def remove(path, *, missing_ok=False, api=None, create_parents=False, repository=None):
    with parent(path, create=create_parents, api=api, repository=repository) as (api, path):
        _remove_held(api, path, missing_ok=missing_ok)


def _symlink_held(api, path, target, directory):
    try:
        api.symlink(target, path, directory=directory)
    except OSError as error:
        raise BoardError("WORKSPACE_UNSUPPORTED",
                         f"Cannot create Windows symlink {path}: {error}; use an account permitted to create "
                         "symlinks or replace this repository link and retry workspace preparation",
                         path=str(path), target=target, reason=str(error)) from error


def restore(path, data, *, link=None, api=None, repository=None):
    """Replace one recovery leaf while retaining every parent handle."""
    with parent(path, create=True, api=api, repository=repository) as (api, path):
        _remove_held(api, path, missing_ok=True)
        if link is not None:
            _symlink_held(api, path, *link)
        elif data is not None:
            _write_held(api, path, data)


def symlink(path, target, *, directory, api=None, repository=None):
    with parent(path, create=True, api=api, repository=repository) as (api, path):
        _symlink_held(api, path, target, directory)


def lock_fd(path, *, create=True, api=None, repository=None):
    with parent(path, api=api, repository=repository) as (api, path):
        handle, _ = _open_checked(api, path, action="lock" if create else "lock-existing")
        try:
            return api.fd(handle, os.O_RDWR)
        except BaseException:
            api.close(handle)
            raise


@contextmanager
def artifact_publication(path):
    """Translate immutable workspace conflicts at one shared boundary."""
    try:
        yield
    except FileExistsError as error:
        raise BoardError("WORKSPACE_CONFLICT", "An immutable workspace artifact already has different contents",
                         path=str(path)) from error


def write_once(path, data, *, api=None, repository=None):
    """Publish an immutable artifact under the held Windows workspace parent."""
    with parent(path, api=api, repository=repository) as (api, path), artifact_publication(path):
        # Workspace names retain their case/short-name policy at this boundary.
        try:
            handle, _ = _open_checked(api, path)
        except FileNotFoundError:
            pass
        else:
            api.close(handle)
        private_files.publish_bytes(path, data, immutable=True)
