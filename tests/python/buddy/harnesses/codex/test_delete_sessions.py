"""Session deletion through the real controller, using only a private fake CLI."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hey_my_buddy import private_dirs
from hey_my_buddy.buddy.harnesses.base import Adapter
from hey_my_buddy.buddy.harnesses.codex.adapter import CodexAdapter
from hey_my_buddy.buddy.harnesses.codex import native_run
from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError
from hey_my_buddy.buddy.roles.run_execution import delete_sessions
from hey_my_buddy.errors import BoardError

FIXTURE = Path(__file__).parent / "fixtures/delete_sessions_codex.py"


class DeleteSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ.get("HMB_CHECKS_TMPDIR"))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "codex-home").mkdir()
        self.trace = self.root / "trace.jsonl"
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("HMB_") and key not in
                       ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "OPENAI_API_KEY", "CODEX_API_KEY")}
        environment.update(HOME=str(self.root), CODEX_HOME=str(self.root / "codex-home"),
                           HMB_DEV_SOURCE="1", HMB_STATE_DIR=str(self.root / "state"),
                           HMB_RUNTIME_ROOT=str(self.root / "runtime"), HMB_CODEX_CLI=str(FIXTURE),
                           HMB_CODEX_FIXTURE_STATE=str(self.trace))
        private = patch.dict(os.environ, environment, clear=True)
        private.start()
        self.addCleanup(private.stop)

    def native_delete(self, session_ids):
        return native_run.run_delete_sessions(
            session_ids=session_ids, cwd=str(self.root), invocation_root=self.root / "invocation",
            native_root=self.root / "native", timeout_seconds=5, cancelled=lambda: False)

    def requests(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()]

    def test_adapter_uses_controller_and_deletes_each_exact_identity_once(self):
        record = private_dirs.session_record_path(self.root / 'state', 'run:owned-task', 'owned-attempt')
        private_dirs.ensure_private_dir(record.parent)
        record.write_bytes(b'private display record\n')
        self.assertEqual(CodexAdapter().delete_sessions(["owned", "gone", "owned", "gone"]),
                         {"owned": "deleted", "gone": "already-gone"})
        self.assertEqual(record.read_bytes(), b'private display record\n')
        frames = self.requests()
        self.assertEqual([item["method"] for item in frames],
                         ["initialize", "initialized", "thread/delete", "thread/delete"])
        self.assertEqual([item["params"]["threadId"] for item in frames[2:]], ["owned", "gone"])
        self.assertEqual(list((self.root / "codex-home").iterdir()), [])

    def test_invalid_id_and_other_native_errors_fail_whole_controller_operation(self):
        cases = [("invalid", -32600, "invalid thread id: invalid"),
                 ("denied", -32000, "permission denied"),
                 ("same-prefix-other-code", -32000, "no rollout found for thread id x"),
                 ("long-error", -32000, "native-reason-" * 60 + "-end")]
        for session_id, code, message in cases:
            with self.subTest(session_id=session_id), self.assertRaises(BoardError) as caught:
                CodexAdapter().delete_sessions(["owned", session_id, "must-not-delete"])
            self.assertEqual(caught.exception.details["nativeError"], {"code": code, "message": message})
            self.assertTrue(str(caught.exception).startswith("Codex rejected thread/delete: "))
            self.assertLessEqual(len(str(caught.exception).encode("utf-8")), 512)
            if len(message.encode("utf-8")) < 400:
                self.assertIn(message, str(caught.exception))
        self.assertNotIn("must-not-delete", [frame.get("params", {}).get("threadId") for frame in self.requests()])

    def test_malformed_native_replies_fail_at_protocol_boundary(self):
        for session_id in ("malformed-error", "missing-message", "invalid-result", "missing-result"):
            with self.subTest(session_id=session_id), self.assertRaises(CodexProtocolError) as caught:
                self.native_delete([session_id])
            self.assertEqual(caught.exception.code, "invalid-protocol")
            self.assertTrue(caught.exception.discovery_shutdown_confirmed)

    def test_additional_native_result_fields_do_not_block_deletion(self):
        self.assertEqual(CodexAdapter().delete_sessions(["extra-result"]), {"extra-result": "deleted"})

    def test_unsupported_harness_fails_without_an_empty_default(self):
        with self.assertRaises(BoardError) as caught:
            Adapter().delete_sessions(["owned"])
        self.assertEqual(caught.exception.code, "UNSUPPORTED")
        with self.assertRaises(BoardError) as caught:
            delete_sessions("claude", ["owned"])
        self.assertEqual(caught.exception.code, "UNSUPPORTED")
        self.assertFalse(self.trace.exists())

    def test_native_stop_failure_is_not_success_after_delete(self):
        halt = native_run._halt_owned_group
        def unconfirmed(*args):
            _stopped, signalled = halt(*args)
            return False, signalled
        with patch.object(native_run, "_halt_owned_group", side_effect=unconfirmed), self.assertRaises(CodexProtocolError) as caught:
            self.native_delete(["owned"])
        self.assertEqual(caught.exception.code, "native-shutdown-failed")
        self.assertFalse(caught.exception.discovery_shutdown_confirmed)
