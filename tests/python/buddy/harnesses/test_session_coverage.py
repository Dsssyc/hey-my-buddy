"""Declaration completeness; projection tests alone cannot prove native coverage."""
from pathlib import Path
import unittest

from hey_my_buddy.buddy.harnesses.registry import SESSION_COVERAGE, session_coverage
from hey_my_buddy.protocol.harness_names import HARNESS_NAMES
from hey_my_buddy.protocol.reasons import REGISTERED_REASON_PARAMETERS
from hey_my_buddy.protocol.session_coverage import (
    SESSION_CONTENT_KINDS, Evidenced, Unavailable, Unverified,
)


class SessionCoverageTests(unittest.TestCase):
    def test_every_harness_declares_every_kind_with_traceable_evidence_or_explicit_absence(self):
        self.assertEqual(set(SESSION_COVERAGE), set(HARNESS_NAMES))
        repository = Path(__file__).resolve().parents[4]
        for harness in HARNESS_NAMES:
            coverage = session_coverage(harness)
            self.assertEqual(set(coverage), set(SESSION_CONTENT_KINDS), harness)
            for kind, declaration in coverage.items():
                with self.subTest(harness=harness, kind=kind):
                    self.assertIsInstance(declaration, (Evidenced, Unavailable, Unverified))
                    if isinstance(declaration, Evidenced):
                        self.assertTrue(declaration.version.strip(), "evidence needs a harness form and version")
                        self.assertTrue(declaration.source.strip(), "evidence needs its native source")
                        for reference in declaration.source.split(";"):
                            file, separator, heading = reference.strip().partition("#")
                            self.assertTrue(file.startswith(("tests/", "docs/")),
                                            "evidence needs a repository source reference")
                            path = repository / file
                            self.assertTrue(path.is_file(), f"missing evidence file: {file}")
                            if path.suffix == ".md":
                                self.assertTrue(separator and heading, "document evidence needs a section title")
                            if separator:
                                titles = [line.lstrip("#").strip() for line in
                                          path.read_text(encoding="utf-8").splitlines() if line.startswith("#")]
                                self.assertIn(heading, titles, f"missing evidence section: {reference}")
                    elif isinstance(declaration, Unavailable):
                        self.assertEqual(set(declaration.reason), {"code", "params"})
                        self.assertIn(declaration.reason["code"], REGISTERED_REASON_PARAMETERS)
                        self.assertEqual(set(declaration.reason["params"]), set(REGISTERED_REASON_PARAMETERS[declaration.reason["code"]]))
