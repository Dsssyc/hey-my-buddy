"""Frozen tree refusals retain their cause and remove partially written input."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.buddy.roles import router_input
from hey_my_buddy.errors import BoardError


class FrozenTreeEntryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("GIT_")}
        self.environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "--quiet")
        self.object_id = self.git("hash-object", "-w", "--stdin", input=b"frozen bytes").strip()
        self.tree = self.git("mktree", input=b"100644 blob " + self.object_id + b"\tfirst.txt\n").strip().decode()
        self.manifest = {"checkoutRoot": str(self.checkout), "inputTree": self.tree}
        self.enterContext(mock.patch.object(router_input.workspace, "verify"))

    def git(self, *arguments, input=None):
        return subprocess.run(["git", "-C", str(self.checkout), *arguments],
                              input=input, env=self.environment, capture_output=True,
                              check=True).stdout

    def assert_refused_after_materializing_a_file(self, entry, message):
        directory = self.root / "review"
        directory.mkdir()
        frozen = directory / "frozen-input"
        first = b"100644 blob " + self.object_id + b"\tfirst.txt\0"
        listing = subprocess.CompletedProcess([], 0, stdout=first + entry + b"\0")
        discard = router_input.discard

        def check_partial_input(root):
            self.assertEqual(root, frozen)
            self.assertEqual((root / "first.txt").read_bytes(), b"frozen bytes")
            discard(root)

        with mock.patch.object(router_input.subprocess, "run", return_value=listing), \
                mock.patch.object(router_input, "discard", side_effect=check_partial_input) as cleanup:
            with self.assertRaises(BoardError) as refused:
                router_input.prepare(self.manifest, directory)
        self.assertEqual(refused.exception.code, "router-input-unavailable")
        self.assertEqual(refused.exception.message, message)
        cleanup.assert_called_once_with(frozen)
        self.assertEqual(list(directory.iterdir()), [])
        self.assertFalse((self.root / "escaped.txt").exists())

    def test_absolute_and_parent_paths_are_refused_as_unsafe_and_cleaned_up(self):
        message = "A frozen tree path is unsafe: absolute paths, parent traversal and Git metadata are forbidden"
        for path in (str(self.root / "escaped.txt"), "../../escaped.txt"):
            with self.subTest(path=path):
                self.assert_refused_after_materializing_a_file(
                    b"100644 blob " + self.object_id + b"\t" + os.fsencode(path), message)
                (self.root / "review").rmdir()

    def test_git_metadata_is_refused_as_unsafe_and_cleaned_up(self):
        self.assert_refused_after_materializing_a_file(
            b"100644 blob " + self.object_id + b"\tnested/.git/config",
            "A frozen tree path is unsafe: absolute paths, parent traversal and Git metadata are forbidden")

    def test_submodule_is_refused_as_unsupported_and_cleaned_up(self):
        self.assert_refused_after_materializing_a_file(
            b"160000 commit " + b"a" * 40 + b"\tsubmodule",
            "Frozen Router input with submodules is not supported")


if __name__ == "__main__":
    unittest.main()
