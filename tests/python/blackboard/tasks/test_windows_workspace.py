"""Windows workspace records and native Git link behavior."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.errors import BoardError


class WindowsWorkspacePrivateRecordsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-win-workspace-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_windows_relative_paths_cannot_address_a_drive_root_or_stream(self):
        with patch.object(workspace, "_WINDOWS", True):
            for path in ("C:/outside", "C:outside", "\\outside", "directory\\..\\outside", "file:stream"):
                with self.subTest(path=path), self.assertRaises(BoardError):
                    workspace._relative(path)
            self.assertEqual(workspace._relative("directory/file.txt"), "directory/file.txt")

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


@unittest.skipUnless(os.name == "nt", "native Windows Git symbolic links")
class NativeWindowsGitTests(unittest.TestCase):
    def test_git_checks_out_directory_link_from_index(self):
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-win-git-") as temporary:
            root = Path(temporary).resolve()
            repository = root / "repo"
            repository.mkdir()

            def git(*args, data=None):
                result = subprocess.run(["git", *args], cwd=repository, input=data,
                                        capture_output=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                return result.stdout

            git("init", "-q")
            git("config", "core.symlinks", "true")
            git("config", "user.name", "Windows Git Test")
            git("config", "user.email", "windows-git@example.invalid")
            target = repository / "directory" / "nested"
            target.mkdir(parents=True)
            (target / "file").write_bytes(b"content")
            probe = repository / "symlink-probe"
            try:
                probe.symlink_to(Path("directory/nested"), target_is_directory=True)
            except OSError as error:
                if getattr(error, "winerror", None) == 1314:
                    self.skipTest("Windows account cannot create symbolic links")
                raise
            probe.unlink()
            git("add", "directory/nested/file")
            link_text = b"directory/nested"
            oid = git("hash-object", "-w", "--stdin", data=link_text).strip()
            git("update-index", "--index-info", data=b"120000 " + oid + b"\tlink\n")
            git("commit", "-qm", "directory link")

            # Git must create a directory link itself; no workspace code chooses its type.
            result = subprocess.run(["git", "checkout-index", "--force", "--", "link"],
                                    cwd=repository, capture_output=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            link = repository / "link"
            self.assertTrue(link.is_symlink())
            self.assertEqual((link / "file").read_bytes(), b"content")
            self.assertEqual(git("show", "HEAD:link"), link_text)
            self.assertEqual(git("ls-files", "-s", "--", "link").split()[0], b"120000")


if __name__ == "__main__":
    unittest.main()
