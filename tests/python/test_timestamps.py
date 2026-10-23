"""The native timestamp retains automatic microseconds and its existing suffix."""
from datetime import datetime, timezone
import unittest
from unittest import mock

from hey_my_buddy import timestamps


class NativeTimestampTests(unittest.TestCase):
    def test_fractional_precision_and_zero_fraction_are_preserved(self):
        for microsecond, suffix in ((0, "Z"), (1, ".000001Z"), (123000, ".123000Z"),
                                    (123456, ".123456Z")):
            with self.subTest(microsecond=microsecond):
                instant = datetime(2026, 10, 24, 1, 2, 3, microsecond, tzinfo=timezone.utc)
                with mock.patch.object(timestamps, "datetime") as clock:
                    clock.now.return_value = instant
                    self.assertEqual(timestamps.utc_now(), "2026-10-24T01:02:03" + suffix)


if __name__ == "__main__":
    unittest.main()
