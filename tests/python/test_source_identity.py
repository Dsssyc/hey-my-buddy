"""An extracted source archive must not adopt its enclosing repository's commit."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from buddy.skill_package import source_commit


class SourceIdentityTests(unittest.TestCase):
    def test_archive_identity_precedes_any_enclosing_git_repository(self):
        with tempfile.TemporaryDirectory(prefix="buddy-source-identity-") as raw:
            root = Path(raw)
            (root / "src/buddy").mkdir(parents=True)
            (root / "src/buddy/build-info.json").write_text(json.dumps({"sourceCommit": "a" * 40}))
            with patch("buddy.skill_package.subprocess.check_output", return_value="b" * 40) as git:
                self.assertEqual(source_commit(root), "a" * 40)
                git.assert_not_called()

    def test_unknown_archive_identity_stays_unknown(self):
        with tempfile.TemporaryDirectory(prefix="buddy-source-identity-") as raw:
            root = Path(raw)
            (root / "src/buddy").mkdir(parents=True)
            (root / "src/buddy/build-info.json").write_text('{"sourceCommit":null}')
            with patch("buddy.skill_package.subprocess.check_output", return_value="b" * 40):
                self.assertIsNone(source_commit(root))
