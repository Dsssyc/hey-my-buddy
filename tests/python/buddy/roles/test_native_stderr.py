"""Each native Worker invocation retains its own stderr through stop summary."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from unittest import mock

from hey_my_buddy.buddy.roles import inline_run, run_execution
from hey_my_buddy.buddy.harnesses.dsh import native_run
from buddy.harnesses.dsh.test_native_run import NativeRunCase
from buddy.harnesses.zcode.test_native_run import MockNativeCase
from hey_my_buddy.buddy.harnesses.zcode import native_run as zcode_run


def assert_summary_stderr(self, module, *, harness, spec, cwd, native_root, configure):
    invocation = self.base / "worker"
    invocation.mkdir(mode=0o700)
    document = {"taskId": "task", "attemptId": "attempt", "generation": 1,
                "turnId": "turn", "resumeMode": "initial", "previousSessionId": None,
                "context": {}, "executionWorkspace": {"access": "write"}}
    input_path = invocation / "turn-input.json"
    input_path.write_text(json.dumps(document), encoding="utf-8")
    task_path = invocation / "task.txt"
    task_path.write_text("fixture task", encoding="utf-8")
    control = {"operation": "worker", "harness": harness, "privateRoot": str(invocation),
               "directory": str(self.base), "nativeRoot": str(native_root),
               "sessionRecordPath": str(self.base / "session.jsonl"),
               "inputFile": str(input_path), "taskFile": str(task_path),
               "invocationId": "first-invocation", "cwd": str(cwd),
               "spec": spec,
               "timeoutSeconds": 30}
    request, services, observer, error = run_execution.worker_request(control, module)
    self.assertIsNone(error)
    configure(services, "first invocation stderr")
    first = module.run(request, observer=observer, services=services, cancelled=lambda: False)
    self.assertEqual(first.end.status, "ok", first.end.message)
    first_ref = next(ref for ref in first.evidence_refs if ref.kind == "native-stderr")
    first_bytes = Path(first_ref.location).read_bytes()
    self.assertEqual(first_ref.sha256, hashlib.sha256(first_bytes).hexdigest())
    self.assertIn(b"first invocation stderr", first_bytes)

    def run_summary(module, summary_request, *, services, observer, cancelled):
        configure(services, "summary invocation stderr")
        return module.run(summary_request, services=services, observer=observer, cancelled=cancelled)

    handle = run_execution.InlineRunHandle(control, request.identity, grace_seconds=0)
    with mock.patch.object(inline_run, "run_harness", side_effect=run_summary):
        _, summary_request, summary, delivered, expired = inline_run._summary_run(
            control, request, first, module, self.base / "state", time.monotonic() + 30, handle)
    self.assertTrue(delivered, summary.end.message)
    self.assertFalse(expired)
    self.assertEqual(summary.end.status, "ok", summary.end.message)
    summary_ref = next(ref for ref in summary.evidence_refs if ref.kind == "native-stderr")
    self.assertNotEqual(first_ref.location, summary_ref.location)
    self.assertEqual(Path(summary_ref.location).parent, Path(summary_request.private_state.invocation_root))
    self.assertIn(b"summary invocation stderr", Path(summary_ref.location).read_bytes())
    self.assertEqual(first_bytes, Path(first_ref.location).read_bytes())
    self.assertEqual(first_ref.sha256, hashlib.sha256(Path(first_ref.location).read_bytes()).hexdigest())


class DshSummaryStderrTests(NativeRunCase):
    def test_worker_and_stop_summary_keep_distinct_stderr_evidence(self):
        def configure(services, text):
            self.micro_task_agent_args(services.mount)
            self.extra_agent_args += ["--stderr-note", text]
        assert_summary_stderr(self, native_run, harness="dsh",
                              spec={"provider": "fake", "model": "m1", "effort": "high"},
                              cwd=self.base / "cwd", native_root=self.root, configure=configure)


class ZcodeSummaryStderrTests(MockNativeCase):
    def test_worker_and_stop_summary_preserve_the_first_stderr_digest(self):
        marker = []
        original = zcode_run._spawn_app_server
        def configure(_services, text):
            marker[:] = [text]
        def spawn(**kwargs):
            owned = original(**kwargs)
            with Path(kwargs["stderr_path"]).open("ab") as stream:
                stream.write((marker[0] + "\n").encode())
            return owned
        with mock.patch.dict("os.environ", self.environment, clear=True), \
             mock.patch.object(zcode_run, "_spawn_app_server", side_effect=spawn):
            assert_summary_stderr(self, zcode_run, harness="zcode",
                                  spec={"provider": "fixture-api", "model": "fixture-model", "effort": "low"},
                                  cwd=self.cwd, native_root=self.base / "worker-native", configure=configure)
