"""Worker permission facts come from the run request across native outcomes."""
from __future__ import annotations

import os
import tempfile
from contextlib import nullcontext
from pathlib import Path
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses import run_contract as rc
from hey_my_buddy.buddy.roles import inline_run, run_execution
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.private_dirs import ensure_private_dir
from hey_my_buddy.protocol.capabilities import PERMISSION_LEVELS
from buddy.harnesses.test_run_contract import full_request


class WorkerPermissionEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="worker-permission-evidence-",
                                                dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def request(self, root, harness, permission):
        fields = full_request(self.base).to_payload()
        fields.update(harness=harness, permission=permission, sessionServices=[], captureEvidence=False)
        fields["privateState"]["invocationRoot"] = str(root)
        fields["privateState"]["stateDir"] = str(self.base)
        return rc.RunRequest.from_payload(fields)

    def execute(self, root, request, *, status):
        control = {"operation": "worker", "harness": request.harness,
                   "stateDir": str(self.base),
                   "privateRoot": str(root), "executionLiveFile": str(root / "execution-live.json"),
                   "inputFile": str(root / "input.json"), "outputFile": str(root / "output.json"),
                   "timeoutSeconds": 5, "live": {"instanceId": "a" * 64, "token": "b" * 64}}
        (root / "input.json").write_text(canonical_json({
            "taskId": request.identity.task_id, "attemptId": request.identity.attempt_id,
            "generation": request.identity.generation, "turnId": request.identity.turn_id}),
            encoding="utf-8", newline="\n")
        result = rc.RunResult(identity=request.identity, harness=request.harness,
                              end=rc.RunEnd(status=status, reason_code="native-refused" if status != "ok" else None),
                              stop_evidence=rc.StopEvidence(native=rc.StopLayer(group_state="gone")))
        handle = run_execution.InlineRunHandle(control, request.identity, grace_seconds=0)
        with mock.patch.object(inline_run, "run_seam", return_value=object()), \
                mock.patch.object(run_execution, "worker_request", return_value=(request, None, None, None)), \
                mock.patch.object(inline_run, "run_harness", return_value=result) as native_run, \
                mock.patch.object(inline_run, "_inline_live", return_value=nullcontext(None)), \
                mock.patch.object(run_execution, "_worker_result", return_value={"outcome": {}}):
            actual, used_request, _verdict, code = inline_run.execute(control, handle)
        self.assertEqual(used_request, request)
        self.assertEqual(native_run.call_args.args[1], request)
        return actual, code

    def test_native_outcomes_keep_permission_and_stop_facts_without_a_permission_file(self):
        for harness, levels in PERMISSION_LEVELS.items():
            for permission in ("read", "write", "full"):
                for status in ("ok", "error", "unknown"):
                    with self.subTest(harness=harness, permission=permission, status=status):
                        root = ensure_private_dir(self.base / f"{harness}-{permission}-{status}")
                        request = self.request(root, harness, permission)
                        result, code = self.execute(root, request, status=status)
                        self.assertEqual(code, 0 if status == "ok" else 1)
                        self.assertEqual(result.stop_evidence.native.group_state, "gone")
                        self.assertFalse((root / "worker-permission.json").exists())
                        self.assertFalse(any(ref.kind == "worker-permission" for ref in result.evidence_refs))
                        facts = run_execution._worker_facts(result, request)
                        self.assertEqual(facts["nativeEvidence"]["permission"], {
                            "level": permission, "enforcement": levels[permission]})
                        self.assertEqual(facts["processState"]["shutdownConfirmed"], True)
                        self.assertEqual(facts["status"], status)


if __name__ == "__main__":
    unittest.main()
