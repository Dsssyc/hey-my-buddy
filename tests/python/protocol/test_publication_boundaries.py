"""Caller path policies still govern the names used for fact publication."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hey_my_buddy import private_files
from hey_my_buddy.blackboard.tasks import storage, workspace
from hey_my_buddy.buddy.roles import turn_io
from hey_my_buddy.errors import BoardError


@unittest.skipIf(os.name == "nt", "These cases exercise POSIX pinned-directory and no-follow policies")
class PublicationBoundaryTests(unittest.TestCase):
    def _swap_parent_during_writer_fsync(self, parent, parked, foreign):
        fsync = os.fsync
        swapped = False

        def swap(fd):
            nonlocal swapped
            if not swapped:
                swapped = True
                parent.rename(parked)
                parent.symlink_to(foreign, target_is_directory=True)
            fsync(fd)

        return mock.patch.object(private_files.os, "fsync", side_effect=swap)

    def test_turn_publication_uses_held_parent_after_ancestor_name_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent, parked, foreign = (root / name for name in ("parent", "parked", "foreign"))
            parent.mkdir()
            foreign.mkdir()
            (foreign / "fact").write_bytes(b"foreign")
            with self._swap_parent_during_writer_fsync(parent, parked, foreign):
                turn_io._private_bytes(parent / "fact", b"complete", exclusive=True)
            self.assertEqual((parked / "fact").read_bytes(), b"complete")
            self.assertEqual((foreign / "fact").read_bytes(), b"foreign")
            self.assertEqual(list(parked.glob(".*.tmp")), [])

    def test_storage_rechecks_unpinned_publication_names_after_writer_close(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent, parked, foreign = (root / name for name in ("parent", "parked", "foreign"))
            parent.mkdir()
            foreign.mkdir()
            (parent / "fact").write_bytes(b"old")
            (foreign / "fact").write_bytes(b"foreign")
            with self._swap_parent_during_writer_fsync(parent, parked, foreign):
                with self.assertRaises(BoardError) as error:
                    storage._atomic_storage_json(parent / "fact", {"secret": "new"})
            self.assertEqual(error.exception.code, "STORAGE_UNSAFE")
            self.assertEqual((parked / "fact").read_bytes(), b"old")
            self.assertEqual((foreign / "fact").read_bytes(), b"foreign")

    def test_workspace_collision_does_not_follow_an_existing_leaf_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original, artifact = root / "original", root / "artifact"
            original.write_bytes(b"same bytes")
            artifact.symlink_to(original)
            with self.assertRaises(OSError):
                workspace._write_once(artifact, b"same bytes")
            self.assertTrue(artifact.is_symlink())
            self.assertEqual(original.read_bytes(), b"same bytes")
