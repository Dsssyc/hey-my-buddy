"""The public C-Two contract tracks the software's major and minor version."""
from __future__ import annotations

from pathlib import Path
import tomllib
import unittest

from hey_my_buddy.protocol.contracts import CONTRACT_VERSION


ROOT = Path(__file__).resolve().parents[3]


class ContractVersionTests(unittest.TestCase):
    def test_contract_version_matches_software_major_and_minor(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())
        major, minor, _patch = project["project"]["version"].split(".")
        self.assertEqual(CONTRACT_VERSION, f"{major}.{minor}.0")
