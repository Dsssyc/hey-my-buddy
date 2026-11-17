"""Maintenance files inspect links at the boundary of each operation."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import maintenance


class MaintenancePathTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="maintenance-paths-")
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        self.run = self.state / "maintenance" / hashlib.sha256(b"run").hexdigest()

    def test_publish_and_read_refuse_linked_content_directory(self):
        outside = self.state / "outside"
        outside.mkdir()
        root = self.run.parent
        root.symlink_to(outside, target_is_directory=True)
        reference = {"sha256": "0" * 64}
        try:
            with self.assertRaises(BoardError) as raised:
                maintenance.publish(self.state, "run", {"value": 1})
            self.assertEqual(raised.exception.code, "PRIVATE_PATH_UNSAFE")
            with self.assertRaises(BoardError) as raised:
                maintenance.read(self.state, "run", reference)
            self.assertEqual(raised.exception.code, "PRIVATE_PATH_UNSAFE")
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            root.unlink()

        root.mkdir()
        self.run.symlink_to(outside, target_is_directory=True)
        try:
            with self.assertRaises(BoardError) as raised:
                maintenance.publish(self.state, "run", {"value": 1})
            self.assertEqual(raised.exception.code, "PRIVATE_PATH_UNSAFE")
            with self.assertRaises(BoardError) as raised:
                maintenance.read(self.state, "run", reference)
            self.assertEqual(raised.exception.code, "PRIVATE_PATH_UNSAFE")
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            self.run.unlink()

    def test_discard_checks_only_the_item_it_removes(self):
        material = self.state / "private-material"
        material.mkdir()
        self.run.parent.symlink_to(material, target_is_directory=True)
        self.run.mkdir()
        reference = {"sha256": "0" * 64}
        file = self.run / (reference["sha256"] + ".json")
        file.write_text("owned material", encoding="utf-8")
        maintenance.discard_reference(self.state, "run", reference)
        self.assertFalse(file.exists())
        maintenance.discard(self.state, "run")
        self.assertFalse(self.run.exists())
        self.assertTrue(self.run.parent.is_symlink())
        self.assertEqual(list(material.iterdir()), [])

    def test_discard_refuses_linked_target_without_touching_destination(self):
        self.run.mkdir(parents=True)
        outside = self.state / "outside.json"
        outside.write_text("retained", encoding="utf-8")
        reference = {"sha256": "0" * 64}
        file = self.run / (reference["sha256"] + ".json")
        file.symlink_to(outside)
        with self.assertRaises(BoardError) as raised:
            maintenance.discard_reference(self.state, "run", reference)
        self.assertEqual(raised.exception.code, "PRIVATE_PATH_UNSAFE")
        self.assertTrue(file.is_symlink())
        self.assertEqual(outside.read_text(encoding="utf-8"), "retained")


if __name__ == "__main__":
    unittest.main()
