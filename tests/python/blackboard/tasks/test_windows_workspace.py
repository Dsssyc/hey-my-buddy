"""Injectable Windows path boundary checks on the host filesystem."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.blackboard.tasks import windows_paths, workspace
from hey_my_buddy.errors import BoardError
from hey_my_buddy.windows_files import WindowsFileAPI, _link_print_name
from hey_my_buddy import private_dirs
from hey_my_buddy.paths import native_path


class FakeHandle:
    def __init__(self, path, fd=None):
        self.path, self.fd = Path(path), fd


class FakeWindowsAPI:
    def __init__(self):
        self.native = WindowsFileAPI() if os.name == "nt" else None
        self.events = []
        self.open = []
        self.reparse = set()
        self.fail_on = None
        self.aliases = {}

    def _track(self, path, fd=None):
        if self.fail_on == Path(path):
            if fd is not None:
                self._close_resource(fd)
            raise OSError("injected open failure")
        handle = FakeHandle(path, fd)
        self.open.append(handle)
        self.events.append(("open", str(path)))
        return handle

    def _close_resource(self, resource):
        if self.native is not None:
            self.native.close(resource)
        else:
            os.close(resource)

    def open_dir(self, path):
        fd = (self.native.open_dir(path) if self.native is not None else
              os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        return self._track(path, fd)

    def open_leaf(self, path, *, action="read"):
        if self.native is not None:
            fd = self.native.open_leaf(path, action=action)
        elif action == "create":
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        elif action in ("lock", "lock-existing") and not Path(path).is_symlink():
            fd = os.open(path, os.O_RDWR | (os.O_CREAT if action == "lock" else 0), 0o600)
        elif action == "read" and not Path(path).is_symlink():
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        else:
            os.lstat(path)
            fd = None
        return self._track(path, fd)

    def info(self, handle):
        if self.native is not None:
            attributes, tag = self.native.info(handle.fd)
        else:
            mode = os.lstat(handle.path).st_mode
            attributes = windows_paths.FILE_ATTRIBUTE_DIRECTORY if stat.S_ISDIR(mode) else 0
            if stat.S_ISLNK(mode):
                attributes |= windows_paths.FILE_ATTRIBUTE_REPARSE_POINT
                tag = windows_paths.IO_REPARSE_TAG_SYMLINK
            else:
                tag = 0
        if handle.path in self.reparse:
            attributes |= windows_paths.FILE_ATTRIBUTE_REPARSE_POINT
            tag = 0xA0000003
        return attributes, tag

    def name(self, handle):
        return self.aliases.get(handle.path, handle.path.name)

    def long_path(self, path):
        path = Path(path)
        return path.with_name(self.aliases[path]) if path in self.aliases else path

    def file_type(self, handle):
        return (self.native.file_type(handle.fd) if self.native is not None else
                windows_paths.FILE_TYPE_DISK)

    def fd(self, handle, _flags):
        result = (self.native.fd(handle.fd, _flags) if self.native is not None else
                  handle.fd)
        handle.fd = None
        self.open.remove(handle)
        return result

    def delete(self, handle):
        if self.native is not None:
            self.native.delete(handle.fd)
        else:
            os.unlink(handle.path)
        self.events.append(("delete", str(handle.path)))

    def close(self, handle):
        self.events.append(("close", str(handle.path)))
        if handle.fd is not None:
            self._close_resource(handle.fd)
        self.open.remove(handle)

    def mkdir(self, path):
        os.mkdir(path)

    def readlink(self, handle):
        return (self.native.readlink(handle.fd) if self.native is not None else
                os.readlink(handle.path))

    def symlink(self, target, path, *, directory=False):
        os.symlink(target, path, target_is_directory=directory)


class WindowsWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-win-paths-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.api = FakeWindowsAPI()

    def test_parent_handles_cover_all_ancestors_until_leaf_closes(self):
        target = self.root / "one" / "two" / "item"
        with windows_paths.parent(target, create=True, api=self.api):
            self.assertTrue(self.api.open)
            self.assertEqual(self.api.open[-1].path, target.parent)
            self.assertIn(self.root, [handle.path for handle in self.api.open])
        self.assertEqual(self.api.open, [])
        self.assertEqual(self.api.events[-1], ("close", str(Path(self.root.anchor))))

    @unittest.skipUnless(os.name == 'nt', 'native Windows directory symbolic links')
    def test_long_directory_symlinks_preserve_type_with_parent_components(self):
        root = self.root / 'long-links'
        self.addCleanup(private_dirs.remove_tree, root)
        deep = root
        while len(str(deep)) < 310:
            deep /= 'directory-' + 'a' * 35
        private_dirs.ensure_private_dir(deep / 'links')
        private_dirs.ensure_private_dir(deep / 'target')
        for operation in ('restore', 'symlink'):
            with self.subTest(operation=operation):
                path = deep / 'links' / operation
                target = '../target'
                try:
                    if operation == 'restore':
                        windows_paths.restore(path, None, symlink_target=target)
                    else:
                        windows_paths.symlink(path, target)
                except OSError as error:
                    if error.winerror == 1314:
                        self.skipTest('symbolic-link privilege is unavailable')
                    raise
                metadata = native_path(path).lstat()
                self.assertTrue(metadata.st_file_attributes & windows_paths.FILE_ATTRIBUTE_REPARSE_POINT)
                self.assertTrue(metadata.st_file_attributes & windows_paths.FILE_ATTRIBUTE_DIRECTORY)

    def test_reparse_parent_is_rejected_and_all_handles_close(self):
        middle = self.root / "junction"
        middle.mkdir()
        self.api.reparse.add(middle)
        with self.assertRaises(BoardError) as error:
            windows_paths.write_new(middle / "child", b"wrong", api=self.api)
        self.assertEqual(error.exception.code, "WORKSPACE_UNSUPPORTED")
        self.assertFalse((middle / "child").exists())
        self.assertEqual(self.api.open, [])

    def test_failed_parent_open_closes_earlier_handles(self):
        middle = self.root / "middle"
        middle.mkdir()
        self.api.fail_on = middle
        with self.assertRaises(OSError):
            windows_paths.read(middle / "file", api=self.api)
        self.assertEqual(self.api.open, [])

    def test_leaf_read_preserves_link_text_and_rejects_other_reparse(self):
        file = self.root / "source"
        file.write_bytes(b"content")
        link = self.root / "link"
        link.symlink_to("source")
        self.assertEqual(windows_paths.read(file, api=self.api), b"content")
        self.assertEqual(windows_paths.read(link, allow_symlink=True, api=self.api),
                         ("120000", b"source"))
        with mock.patch.object(workspace, "_WINDOWS", True), \
             mock.patch.object(windows_paths, "WindowsFileAPI", return_value=self.api):
            self.assertEqual(workspace._file(self.root, "link"), ("120000", b"source"))
            self.assertEqual(workspace._file(self.root, "source", mode_hint="100755"),
                             ("100755", b"content"))
        with self.assertRaises(BoardError):
            windows_paths.read(link, api=self.api)
        self.api.reparse.add(file)
        with self.assertRaises(BoardError):
            windows_paths.read(file, api=self.api)
        self.assertEqual(self.api.open, [])

    def test_existing_short_name_alias_is_rejected(self):
        file = self.root / "LONGFI~1"
        file.write_bytes(b"content")
        self.api.aliases[file] = "LongFilename"
        with self.assertRaises(BoardError) as error:
            windows_paths.read(file, api=self.api)
        self.assertEqual(error.exception.code, "WORKSPACE_UNSUPPORTED")
        self.assertEqual(self.api.open, [])

    def test_outer_short_names_and_case_aliases_keep_git_paths_exact(self):
        outer = self.root / "LONGFI~1"
        folder = outer / "REPOSI~1" / "folder"
        folder.mkdir(parents=True)
        item = folder / "item"
        item.write_bytes(b"content")
        self.api.aliases[self.root] = self.root.name.upper()
        self.api.aliases[outer] = "LongFolder"
        self.api.aliases[folder.parent] = "Repository"
        with self.assertRaises(BoardError):
            windows_paths.read(item, api=self.api)
        self.assertEqual(windows_paths.read(item, api=self.api, repository=folder.parent), b"content")
        created = folder / "new"
        windows_paths.write_new(created, b"fresh", api=self.api, repository=folder.parent)
        self.assertEqual(created.read_bytes(), b"fresh")
        self.api.aliases[folder] = "Folder"
        with self.assertRaises(BoardError) as error:
            windows_paths.read(item, api=self.api, repository=folder.parent)
        self.assertEqual(error.exception.code, "WORKSPACE_UNSUPPORTED")
        self.assertEqual(self.api.open, [])

    def test_create_new_delete_and_restore_use_exact_leaf(self):
        target = self.root / "nested" / "target"
        windows_paths.write_new(target, b"first", create_parents=True, api=self.api)
        with self.assertRaises(FileExistsError):
            windows_paths.write_new(target, b"second", api=self.api)
        self.assertEqual(target.read_bytes(), b"first")
        windows_paths.remove(target, api=self.api)
        self.assertFalse(target.exists())
        windows_paths.write_new(target, b"old", api=self.api)
        with mock.patch.object(workspace, "_WINDOWS", True), \
             mock.patch.object(windows_paths, "WindowsFileAPI", return_value=self.api), \
             mock.patch.object(workspace, "_git", return_value=b"restored"):
            workspace._write_path(self.root, "nested/target", {"mode": "100644", "oid": "abc"})
        self.assertEqual(target.read_bytes(), b"restored")
        deleted = max(index for index, event in enumerate(self.api.events)
                      if event == ("delete", str(target)))
        recreated = self.api.events.index(("open", str(target)), deleted)
        self.assertNotIn(("close", str(target.parent)), self.api.events[deleted:recreated])
        with mock.patch.object(workspace, "_WINDOWS", True), \
             mock.patch.object(windows_paths, "WindowsFileAPI", return_value=self.api), \
             mock.patch.object(workspace, "_git", return_value=b"target"):
            workspace._write_path(self.root, "nested/link", {"mode": "120000", "oid": "def"})
            self.assertEqual(workspace._file(self.root, "nested/link"), ("120000", b"target"))
        self.assertEqual(self.api.open, [])

    def test_immutable_record_rejects_symlink_and_keeps_first_contents(self):
        record = self.root / "record.json"
        windows_paths.write_once(record, b"first", api=self.api)
        windows_paths.write_once(record, b"first", api=self.api)
        with self.assertRaises(BoardError) as error:
            windows_paths.write_once(record, b"different", api=self.api)
        self.assertEqual(error.exception.code, "WORKSPACE_CONFLICT")
        record.unlink()
        record.symlink_to("elsewhere")
        with self.assertRaises(BoardError):
            windows_paths.write_once(record, b"first", api=self.api)
        self.assertFalse((self.root / "elsewhere").exists())
        self.assertEqual(self.api.open, [])

    def test_private_lock_leaf_cannot_follow_a_link(self):
        with workspace._lock(self.root):
            pass
        (self.root / ".lock").unlink()
        (self.root / ".lock").symlink_to("elsewhere")
        with self.assertRaises(BoardError):
            with workspace._lock(self.root):
                pass
        self.assertFalse((self.root / "elsewhere").exists())

    def test_existing_lock_does_not_create_a_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            with workspace._lock(self.root, create=False):
                pass
        self.assertFalse((self.root / ".lock").exists())
        with workspace._lock(self.root):
            pass
        with workspace._lock(self.root, create=False):
            pass

    def test_private_records_allow_a_linked_ancestor(self):
        directory = self.root / "records"
        directory.mkdir()
        linked = self.root / "linked-records"
        linked.symlink_to(directory, target_is_directory=True)
        workspace._mkdir(linked)
        record = linked / "manifest.json"
        workspace._write_once(record, b"immutable")
        self.assertEqual(workspace._read(record), b"immutable")
        workspace._write_once(record, b"immutable")
        with self.assertRaises(BoardError) as error:
            workspace._write_once(record, b"different")
        self.assertEqual(error.exception.code, "WORKSPACE_CONFLICT")
        with workspace._lock(linked):
            self.assertTrue((directory / ".lock").is_file())

    def test_windows_names_and_case_aliases_are_rejected(self):
        invalid = ["folder\\escape", "x:stream", "CON.txt", "COM¹.log", "NUL .txt", "item.",
                   "item ", "bad?name", "bad\x01name", "a//b", "a/./b"]
        with mock.patch.object(workspace, "_WINDOWS", True):
            for value in invalid:
                with self.subTest(value=value), self.assertRaises(BoardError):
                    workspace._relative(value)
        with self.assertRaises(BoardError):
            windows_paths.validate_unique(["File.txt", "file.TXT"])
        with self.assertRaises(BoardError):
            windows_paths.validate_unique(["Folder/a", "folder/b"])

    def test_link_reparse_data_uses_print_name_not_substitute(self):
        substitute = "\\??\\C:\\elsewhere".encode("utf-16-le")
        print_name = "../target".encode("utf-16-le")
        raw = (struct.pack("<IHHHHHHI", windows_paths.IO_REPARSE_TAG_SYMLINK,
                           12 + len(substitute) + len(print_name), 0, 0, len(substitute),
                           len(substitute), len(print_name), 1) + substitute + print_name)
        self.assertEqual(_link_print_name(raw), "../target")
        with self.assertRaises(OSError):
            _link_print_name(raw[:12])


if __name__ == "__main__":
    unittest.main()
