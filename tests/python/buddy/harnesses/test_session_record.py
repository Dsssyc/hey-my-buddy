"""Session display failures stay separate from native execution facts."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest import mock
import os

from hey_my_buddy.buddy.harnesses.session_record import SessionRecord
from hey_my_buddy.protocol.session_content import MessageChunk, TextContent


class SessionRecordTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "attempt.jsonl"
        self.record = SessionRecord(self.path, "attempt")
        self.addCleanup(self.record.close)

    @staticmethod
    def update(text="input"):
        return MessageChunk(session_update="user_message_chunk", content=TextContent(text=text))

    def test_unencodable_text_disables_record_and_preserves_readable_prefix(self):
        self.record.append(self.update())
        before = self.path.read_bytes()
        self.record.append(self.update("unpaired \ud800"))
        self.assertEqual(self.record.failure["params"]["category"], "UnicodeEncodeError")
        self.assertEqual(self.path.read_bytes(), before)
        self.record.append(self.update("ignored"))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIsNone(self.record._fd)

    def test_notification_validation_is_inside_writer_boundary(self):
        self.record.append({})
        self.assertEqual(self.record.failure["params"]["category"], "_BoardValueError")
        self.assertFalse(self.path.exists())

    def test_reopen_trims_interrupted_line_before_appending(self):
        self.record.append(self.update("first"))
        self.record.close()
        with self.path.open("ab") as stream:
            stream.write(b'{"broken":')
        resumed = SessionRecord(self.path, "attempt")
        self.addCleanup(resumed.close)
        resumed.append(self.update("second"))
        lines = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(lines), 3)
        self.assertEqual([line["update"]["content"]["text"] for line in lines[1:]], ["first", "second"])

    def test_projector_validation_failure_disables_further_projection(self):
        def invalid_projection():
            MessageChunk(session_update="agent_message_chunk", content={"type": "text", "text": 3})

        self.record.project(invalid_projection)
        self.assertEqual(self.record.failure["params"]["category"], "_BoardValueError")
        next_projection = mock.Mock()
        self.record.project(next_projection)
        next_projection.assert_not_called()

    def test_first_error_survives_cleanup_failure_and_is_safe_to_encode(self):
        self.record.append(self.update())
        descriptor = self.record._fd
        with self.assertLogs("hey_my_buddy.buddy.harnesses.session_record", level="ERROR") as logged, \
             mock.patch("hey_my_buddy.buddy.harnesses.session_record.os.close", side_effect=KeyError("cleanup")):
            self.record.project(mock.Mock(side_effect=ValueError("\ud800" + "文" * 4000)))
        self.assertEqual(len(logged.records), 1)
        self.assertIsInstance(logged.records[0].exc_info[1], KeyError)
        # The mocked close did not close the actual descriptor.
        os.close(descriptor)
        failure = self.record.failure
        self.assertEqual(failure["params"]["category"], "ValueError")
        self.assertTrue(failure["params"]["truncated"])
        message = failure["params"]["message"]
        self.assertTrue(message.startswith("\\ud800"))
        self.assertLessEqual(len(message.encode("utf-8")), 8192)
        self.record._failed(RuntimeError("second error"))
        self.assertEqual(self.record.failure, failure)

    def test_writer_close_records_non_os_exception(self):
        self.record.append(self.update())
        descriptor = self.record._fd
        with mock.patch("hey_my_buddy.buddy.harnesses.session_record.os.close", side_effect=ValueError("close")):
            self.record.close()
        os.close(descriptor)
        self.assertEqual(self.record.failure["params"]["category"], "ValueError")
        self.assertIsNone(self.record._fd)

    def test_cleanup_error_is_recorded_once_without_replacing_first_failure(self):
        self.record.append(self.update())
        descriptor = self.record._fd
        close_error = KeyError("cleanup " + "文" * 10000)
        with self.assertLogs("hey_my_buddy.buddy.harnesses.session_record", level="ERROR") as logged, \
             mock.patch("hey_my_buddy.buddy.harnesses.session_record.os.close", side_effect=close_error):
            self.record.project(mock.Mock(side_effect=ValueError("first error")))
        os.close(descriptor)
        self.assertEqual(self.record.failure["params"], {
            "category": "ValueError", "message": "first error", "truncated": False})
        self.assertEqual(len(logged.records), 1)
        self.assertIs(logged.records[0].exc_info[1], close_error)
        self.assertIn(str(close_error), logged.output[0])
        self.record._failed(RuntimeError("later"))
        self.assertEqual(len(logged.records), 1)

    def test_cancellation_is_not_a_projection_failure(self):
        for cancellation in (KeyboardInterrupt(), SystemExit()):
            with self.subTest(cancellation=type(cancellation).__name__):
                with self.assertRaises(type(cancellation)):
                    self.record.project(mock.Mock(side_effect=cancellation))
                self.assertIsNone(self.record.failure)
