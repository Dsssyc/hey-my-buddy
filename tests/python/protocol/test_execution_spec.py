"""Admission requirements for the internal execution specification."""
import tempfile
import unittest

from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import schemas


class ExecutionSpecTests(unittest.TestCase):
    def test_adapter_and_expected_seconds_are_required(self):
        with tempfile.TemporaryDirectory() as cwd:
            spec = {"requestId": "execution", "task": "fixture", "cwd": cwd,
                    "adapter": "dsh", "expectedSeconds": 60}
            for field in ("adapter", "expectedSeconds"):
                with self.subTest(field=field):
                    incomplete = dict(spec)
                    del incomplete[field]
                    with self.assertRaisesRegex(BoardError, field) as raised:
                        schemas.normalize_spec(incomplete)
                    self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

            with self.assertRaises(BoardError) as raised:
                schemas.normalize_spec({**spec, "adapter": "unknown"})
            self.assertEqual(raised.exception.code, "UNSUPPORTED_ADAPTER")

    def test_expected_seconds_requires_a_bounded_integer(self):
        for seconds in (None, True, False, "60", 60.0, 0, schemas.MAX_EXPECTED_SECONDS + 1):
            with self.subTest(seconds=seconds), self.assertRaises(BoardError) as raised:
                schemas.expected_duration({"expectedSeconds": seconds})
            self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        for seconds in (schemas.MIN_EXPECTED_SECONDS, schemas.MAX_EXPECTED_SECONDS):
            self.assertEqual(schemas.expected_duration({"expectedSeconds": seconds}), seconds)
