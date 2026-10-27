"""Generated receipt bytes are stable across the host's text conventions."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from hey_my_buddy.protocol.execution import fsync_json, receipt_reference, RECEIPT
from hey_my_buddy.private_files import publish_bytes


class TextEncodingTests(unittest.TestCase):
    def test_receipt_keeps_existing_json_bytes_with_non_ascii_and_line_endings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / RECEIPT
            fsync_json(path, {"task": "中文\r\nline\n", "done": True})
            expected = b'{"task": "\xe4\xb8\xad\xe6\x96\x87\\r\\nline\\n", "done": true}'
            self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(receipt_reference(path.parent)["sha256"],
                             hashlib.sha256(expected).hexdigest())

    def test_private_artifact_preserves_original_crlf_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint"
            original = b'\xe4\xb8\xad\xe6\x96\x87\r\nsecond\r\n'
            publish_bytes(path, original, immutable=True)
            self.assertEqual(path.read_bytes(), original)

    def test_internal_protocols_write_utf8_even_with_ascii_standard_streams(self):
        import io
        import sys
        from unittest import mock
        from hey_my_buddy.buddy.roles import run_controller, session_mcp

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "configuration.json"
            path.write_bytes(b'{}')
            for module, flag, frame in (
                (run_controller, "--control", '{"task":"中文"}'),
                (session_mcp, "--config", '{"id":"中文","jsonrpc":"2.0","result":{}}'),
            ):
                with self.subTest(module=module.__name__):
                    captured = io.BytesIO()
                    output = io.TextIOWrapper(captured, encoding="ascii", newline="\r\n")
                    incoming = io.BytesIO('{"jsonrpc":"2.0","method":"ping","id":"中文"}\n'.encode("utf-8"))
                    source = io.TextIOWrapper(incoming, encoding="ascii")
                    with (mock.patch.object(sys, "argv", [module.__name__, flag, str(path)]),
                          mock.patch.object(sys, "stdin", source),
                          mock.patch.object(sys, "stdout", output),
                          mock.patch.object(run_controller, "execute", return_value=(frame, 0)),
                          # This test owns in-memory streams; the real EOF
                          # listener is covered by the controller process tests.
                          mock.patch.object(run_controller, "_read_stop")):
                        self.assertEqual(module.main(), 0)
                    output.flush()
                    self.assertEqual(captured.getvalue(), (frame + "\n").encode("utf-8"))
                    output.detach()

    def test_cli_json_is_utf8_when_redirected_to_a_local_encoded_pipe(self):
        import io
        import json
        import sys
        from unittest import mock
        from hey_my_buddy.cli import main as cli

        captured = io.BytesIO()
        output = io.TextIOWrapper(captured, encoding="ascii", newline="\r\n")
        with mock.patch.object(sys, "stdout", output), self.assertRaises(SystemExit):
            cli.main(["不存在的命令"])
        output.flush()
        raw = captured.getvalue()
        self.assertIn("不存在的命令", json.loads(raw)["error"]["message"])
        self.assertTrue(raw.endswith(b"\n"))
        self.assertNotIn(b"\r\n", raw)
        output.detach()
