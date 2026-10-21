"""The public C-Two signatures are fixed for each major/minor contract version."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import tomllib
import unittest

from hey_my_buddy.protocol.contracts import CONTRACT_VERSION


ROOT = Path(__file__).resolve().parents[3]
CONTRACT_SOURCE = ROOT / "src/hey_my_buddy/protocol/contracts.py"
SIGNATURE_FIXTURE = Path(__file__).with_name("contract_signatures.json")
RESOURCE_NAMES = ("BuddyControl", "BuddyWait", "ExecutionLive")


def contract_signature_digest(source: str) -> str:
    """Hash declared signatures, using syntax stable across supported Python versions."""
    classes = {node.name: node for node in ast.parse(source).body if isinstance(node, ast.ClassDef)}
    signatures = [
        [name, [
            [method.name, ast.unparse(method.args),
             ast.unparse(method.returns)]
            for method in classes[name].body if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]]
        for name in RESOURCE_NAMES
    ]
    canonical = json.dumps(signatures, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ContractVersionTests(unittest.TestCase):
    def test_contract_version_matches_software_major_and_minor(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        major, minor, _patch = project["project"]["version"].split(".")
        self.assertEqual(CONTRACT_VERSION, f"{major}.{minor}.0")

    def test_contract_signatures_match_the_pinned_version(self):
        fixture = json.loads(SIGNATURE_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(CONTRACT_VERSION, fixture["contractVersion"])
        self.assertEqual(contract_signature_digest(CONTRACT_SOURCE.read_text(encoding="utf-8")), fixture["sha256"])

    def test_signature_digest_changes_when_a_method_signature_changes(self):
        source = CONTRACT_SOURCE.read_text(encoding="utf-8")
        changed = source.replace(
            "def ping(self, request_json: str) -> str:",
            "def ping(self, request_json: bytes) -> str:",
            1,
        )
        self.assertNotEqual(changed, source)
        self.assertNotEqual(contract_signature_digest(changed), contract_signature_digest(source))
