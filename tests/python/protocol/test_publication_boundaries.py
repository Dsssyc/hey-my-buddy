"""Immutable workspace publication never follows a colliding leaf link."""
from pathlib import Path
import tempfile
import unittest

from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.tasks import workspace


class PublicationBoundaryTests(unittest.TestCase):
    def test_workspace_collision_does_not_follow_an_existing_leaf_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original, artifact = root / "original", root / "artifact"
            original.write_bytes(b"same bytes")
            artifact.symlink_to(original)
            with self.assertRaises(BoardError):
                workspace._write_once(artifact, b"same bytes")
            self.assertTrue(artifact.is_symlink())
            self.assertEqual(original.read_bytes(), b"same bytes")
