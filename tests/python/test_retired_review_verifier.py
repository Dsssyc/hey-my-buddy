"""The retired paid review verifier stays retired: entries reject, nothing auto-runs.

ADR-021 item 4 removed the per-version paid native verification. These tests hold
the retirement: every former entry refuses instead of starting a probe, the
package and registry carry no certificate machinery, and no board read path
starts native or model work on its own.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

from support import BoardTestCase

import hey_my_buddy.cli.main as cli
import hey_my_buddy.protocol.transport as transport
from hey_my_buddy.buddy.harnesses.registry import adapter as get_adapter, adapters, supported_capabilities
from hey_my_buddy.console.server import CONSOLE_OPERATIONS
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.service.service import CONTROL_OPERATIONS

RETIRED_MODULES = (
    "hey_my_buddy.harness_review",
    "hey_my_buddy.review_probe",
    "hey_my_buddy.review_replay",
    "hey_my_buddy.review_evidence",
    "hey_my_buddy.sandbox_probe",
    "hey_my_buddy.buddy.harnesses.review_check",
)
RETIRED_RESOURCES = ("review-certificates.json",)


class RetiredVerifierSurfaceTests(unittest.TestCase):
    def test_the_verifier_modules_and_certificate_resource_are_not_packaged(self):
        package = importlib.util.find_spec("hey_my_buddy")
        roots = [Path(root) for root in package.submodule_search_locations]
        for name in RETIRED_MODULES:
            self.assertIsNone(importlib.util.find_spec(name), name)
        for resource in RETIRED_RESOURCES:
            self.assertFalse(any((root / resource).exists() for root in roots), resource)

    def test_the_cli_and_transport_surfaces_have_no_verify_entry(self):
        self.assertNotIn("harness-verify", cli.METHODS)
        self.assertNotIn("harness-verify", transport.METHOD_MAP)
        self.assertNotIn("harness_verify", CONTROL_OPERATIONS)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as refused:
            cli.main(["harness-verify", "{}"])
        self.assertEqual(refused.exception.code, 2)

    def test_the_registry_has_no_review_check_executor_or_capability(self):
        self.assertNotIn("review-check", adapters())
        self.assertNotIn("review-check", supported_capabilities())
        with self.assertRaises(BoardError) as refused:
            get_adapter("review-check")
        self.assertEqual(refused.exception.code, "UNSUPPORTED_ADAPTER")

    def test_the_adapter_certificate_attribute_is_gone_not_false(self):
        for instance in adapters().values():
            self.assertFalse(hasattr(instance, "read_only_structured_verified"), instance.name)


class RetiredVerifierServiceTests(BoardTestCase):
    def test_service_and_console_entries_reject_without_native_work(self):
        board = self.board()
        with mock.patch("subprocess.Popen", side_effect=AssertionError("native process")), \
             mock.patch("subprocess.run", side_effect=AssertionError("native process")):
            for params in ({"adapter": "codex"},
                           {"adapter": "codex", "profileId": "codex:openai:fixture-model:low"},
                           {"adapter": "codex", "profileId": "codex:openai:fixture-model:low",
                            "requestId": "review-1", "execute": True}):
                with self.subTest(params=params), self.assertRaises(BoardError) as refused:
                    board.call("harness_verify", params)
                self.assertEqual(refused.exception.code, "METHOD_NOT_FOUND")
            with self.assertRaises(BoardError) as refused:
                board.console.command("harness_verify", {"adapter": "codex", "execute": True})
            self.assertEqual(refused.exception.code, "METHOD_NOT_FOUND")
            self.assertNotIn("harness_verify", CONSOLE_OPERATIONS)
            with board.store.db.read() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    def test_capability_reads_expose_neither_the_executor_nor_certificates(self):
        board = self.board()
        report = board.call("capabilities", {})
        self.assertNotIn("review-check", report["adapters"])
        self.assertNotIn("reviewVerification", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
