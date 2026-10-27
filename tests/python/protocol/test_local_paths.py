"""Local path admission with deterministic POSIX and Windows semantics."""
from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hey_my_buddy import paths
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import schemas


class LocalPathTests(unittest.TestCase):
    def test_cwd_admission_uses_the_platform_before_resolving_the_directory(self):
        cases = (
            (PureWindowsPath, (r"D:\work\project", "D:/work/project", r"\\server\share\project"),
             ("project", "D:project", r"\project", "/project")),
            (PurePosixPath, ("/work/project", "/work/with space", "/work/非ASCII"),
             ("project", r"D:\work\project", "D:/work/project", r"\\server\share\project")),
        )
        with tempfile.TemporaryDirectory() as temporary:
            canonical = str(Path(temporary).resolve())
            for flavor, accepted, rejected in cases:
                realpath = Mock(return_value=canonical)
                # Only the filesystem lookup is synthetic; admission uses the
                # same central rule as the other local path boundaries.
                with patch.object(paths, "Path", flavor), patch.object(
                    schemas, "os", SimpleNamespace(path=SimpleNamespace(realpath=realpath))
                ):
                    for value in accepted:
                        with self.subTest(platform=flavor.__name__, value=value):
                            self.assertEqual(schemas._canonical_cwd(value), canonical)
                            realpath.assert_called_with(value)
                    realpath.reset_mock()
                    for value in (*rejected, None, 123):
                        with self.subTest(platform=flavor.__name__, value=value), self.assertRaises(BoardError):
                            schemas._canonical_cwd(value)
                    realpath.assert_not_called()

    def test_native_cwd_still_requires_an_existing_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.assertEqual(schemas._canonical_cwd(str(directory)), str(directory.resolve()))
            file = directory / "file"
            file.touch()
            for value in (file, directory / "missing"):
                with self.subTest(value=value), self.assertRaises(BoardError) as caught:
                    schemas._canonical_cwd(str(value))
                self.assertEqual(caught.exception.message, "cwd must be an existing directory")
