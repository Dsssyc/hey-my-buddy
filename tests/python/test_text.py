"""UTF-8 output bytes and truncation at complete Unicode character boundaries."""
import io
import sys
import unittest
from unittest import mock

from hey_my_buddy.text import configure_utf8_output, truncate_utf8


class Utf8TextTests(unittest.TestCase):
    def test_output_uses_utf8_and_lf_for_both_byte_backed_streams(self):
        buffers = [io.BytesIO(), io.BytesIO()]
        streams = [io.TextIOWrapper(buffer, encoding="ascii", newline="\r\n") for buffer in buffers]
        for stream in streams:
            self.addCleanup(stream.close)
        with mock.patch.object(sys, "stdout", streams[0]), mock.patch.object(sys, "stderr", streams[1]):
            configure_utf8_output()
            for stream in streams:
                stream.write("中文😀\n")
                stream.flush()
        self.assertEqual([buffer.getvalue() for buffer in buffers], ["中文😀\n".encode()] * 2)

    def test_in_memory_text_streams_are_left_usable(self):
        output, error = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", output), mock.patch.object(sys, "stderr", error):
            configure_utf8_output()
            print("中文")
            print("错误", file=sys.stderr)
        self.assertEqual(output.getvalue(), "中文\n")
        self.assertEqual(error.getvalue(), "错误\n")

    def test_truncation_preserves_complete_characters_at_every_byte_boundary(self):
        expected = ["", "A", "A", "A", "A中", "A中", "A中", "A中", "A中😀", "A中😀B"]
        for limit, value in enumerate(expected):
            with self.subTest(limit=limit):
                self.assertEqual(truncate_utf8("A中😀B", limit), value)
        self.assertEqual(truncate_utf8("", 10), "")
        self.assertEqual(truncate_utf8("中文", 10), "中文")

    def test_unencodable_text_keeps_the_callers_error_policy(self):
        with self.assertRaises(UnicodeEncodeError):
            truncate_utf8("A\ud800中", 10)
        self.assertEqual(truncate_utf8("A\ud800中", 4, errors="replace"), "A?")


if __name__ == "__main__":
    unittest.main()
