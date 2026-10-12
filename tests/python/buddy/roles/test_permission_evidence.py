"""The controller's declared Worker permission follows one bounded native run."""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from pathlib import Path
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses import run_contract as rc
from hey_my_buddy.buddy.roles import run_controller, run_execution
from hey_my_buddy.errors import BoardError
from hey_my_buddy.json_codec import canonical_json, decode_strict_json
from hey_my_buddy.private_dirs import ensure_private_dir
from buddy.harnesses.test_run_contract import full_request


class WorkerPermissionEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="worker-permission-evidence-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(temporary.cleanup)
        self.root = ensure_private_dir(Path(temporary.name) / "inv")

    def request(self, *, permission=True):
        fields = full_request(self.root.parent).to_payload()
        fields.update(harness="codex", toolScope="write" if permission else "none", sessionServices=[],
                      captureEvidence=False)
        fields["privateState"]["invocationRoot"] = str(self.root)
        if permission:
            fields["workerPermission"] = {"level": "read", "toolScope": "write",
                                          "enforcement": "convention"}
        return rc.decode_run_request(fields)

    def execute(self, request, *, status="ok", operation="worker"):
        control = {"operation": operation, "harness": "codex",
                   "privateRoot": str(self.root), "requestFile": str(self.root / "request.json"),
                   "verdictFile": str(self.root / "verdict.json")}
        result = rc.RunResult(identity=request.identity, harness=request.harness,
                              end=rc.RunEnd(status=status, reason_code="native-refused" if status != "ok" else None),
                              stop_evidence=rc.StopEvidence(native=rc.StopLayer(group_state="gone")))
        with mock.patch.object(run_controller, "run_seam", return_value=object()), \
                mock.patch.object(run_controller.run_execution,
                                  "worker_request" if operation == "worker" else "fast_request",
                                  return_value=(request, None, None, None)), \
                mock.patch.object(run_controller, "run_harness", return_value=result) as native_run, \
                mock.patch("hey_my_buddy.buddy.harnesses.registry.worker_format", return_value=None):
            frame, code = run_controller.execute(control, threading.Event(), self.root.parent)
        self.assertEqual(rc.decode_run_request((self.root / "request.json").read_bytes()), request)
        self.assertEqual(native_run.call_args.args[1], request)
        return rc.decode_run_result(frame), code

    def test_success_and_native_failure_retain_the_declaration_and_stop(self):
        for status in ("ok", "error"):
            with self.subTest(status=status):
                # Each controller invocation gets its own private root.
                if status == "error":
                    self.root = ensure_private_dir(self.root.parent / "next-inv")
                request = self.request()
                result, code = self.execute(request, status=status)
                self.assertEqual(code, 0 if status == "ok" else 1)
                self.assertEqual(result.stop_evidence.native.group_state, "gone")
                self.assertEqual(len(result.evidence_refs), 1)
                ref = result.evidence_refs[0]
                self.assertEqual(ref.kind, "worker-permission")
                raw = Path(ref.location).read_bytes()
                self.assertEqual(ref.size_bytes, len(raw))
                self.assertEqual(ref.sha256, hashlib.sha256(raw).hexdigest())
                self.assertEqual(run_execution._worker_facts(result)["nativeEvidence"]["workerPermission"], {
                    "source": "harness-permission-declaration",
                    "binding": request.identity.to_payload(),
                    "permission": request.worker_permission.to_payload()})

    def test_changed_content_or_foreign_binding_is_refused(self):
        request = self.request()
        result, _ = self.execute(request)
        path = Path(result.evidence_refs[0].location)
        declaration = decode_strict_json(path.read_bytes())
        declaration["binding"]["invocationId"] = "foreign-invocation"
        raw = canonical_json(declaration).encode()
        path.write_bytes(raw)
        with self.assertRaises(BoardError):
            run_execution._worker_facts(result)
        ref = result.evidence_refs[0].model_copy(update={
            "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        rebound = result.model_copy(update={"evidence_refs": (ref,)})
        with self.assertRaises(BoardError):
            run_execution._worker_facts(rebound)
        self.assertEqual(rebound.stop_evidence.native.group_state, "gone")

    def test_run_without_worker_permission_writes_no_declaration(self):
        request = self.request(permission=False)
        result, _ = self.execute(request, operation="fast")
        self.assertFalse((self.root / "worker-permission.json").exists())
        self.assertEqual(result.evidence_refs, ())


if __name__ == "__main__":
    unittest.main()
