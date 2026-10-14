"""Worker carriers and permission evidence over the offline native ACP fixture."""
import json
import os
from pathlib import Path
from unittest import mock

from hey_my_buddy.buddy.harnesses.dsh import native_run
from hey_my_buddy.buddy.harnesses.registry import adapter, require_permission
from hey_my_buddy.buddy.harnesses.run_contract import RunContinuation, decode_run_result, encode_run_result
from hey_my_buddy.buddy.roles.run_observers import worker_observer
from hey_my_buddy.errors import BoardError

from .test_native_run import NativeRunCase


class WorkerPermissionTests(NativeRunCase):
    def test_each_worker_level_launches_its_preset_and_finishes_through_the_session_tool(self):
        for level, mode in (("read", "read-only"), ("write", "workspace-write"),
                            ("full", "danger-full-access")):
            with self.subTest(level=level):
                require_permission({"harness": "dsh"}, level)
                request, bound, mount = self.worker_request(level=level, inquiry=True)
                self.micro_task_agent_args(mount)
                with mock.patch.dict(os.environ, {"DSH_PERMISSION_MODE": "inherited-mode"}):
                    result = native_run.run(request, observer=worker_observer, services=bound.services,
                                            cancelled=lambda: False)
                self.assertEqual(result.end.status, "ok", result.end.message)
                self.assertEqual(result.value.parsed.value["disposition"], "completed")
                self.assertIsNone(result.value.raw)
                self.assertEqual(result.effective_policy.tools.requested.value, {"permissionMode": mode})
                saved = decode_run_result(encode_run_result(result))
                self.assertEqual(saved.effective_policy.tools.requested.value, {"permissionMode": mode})
                self.assertEqual(native_run.native_evidence(result)["nativePolicy"], {"permissionMode": mode})
                observed = [entry for entry in self.agent_log() if entry.get("event") == "home-check"][-1]
                self.assertEqual(observed["permissionMode"], mode)
                patch = json.loads((Path(request.private_state.invocation_root) / "dsh-launch-patch.json").read_text())
                self.assertEqual([row["id"] for row in patch], ["session-persistence-jsonl", "storage-json"])
                provenance = next(ref for ref in result.evidence_refs if ref.kind == "turn-provenance")
                proof = json.loads(Path(provenance.location).read_text())
                self.assertEqual(proof["nativeTool"], mount.finish_tool)
                self.assertTrue(proof["receiptVerified"])
                inquiry = next(ref for ref in result.evidence_refs if ref.kind == "inquiry-report")
                self.assertEqual(json.loads(Path(inquiry.location).read_text())["deliveryMode"],
                                 "cooperative-checkpoint")

    def test_presets_remain_requested_facts_when_initialize_fails_before_a_session_opens(self):
        for level, mode in (("read", "read-only"), ("write", "workspace-write"),
                            ("full", "danger-full-access")):
            with self.subTest(level=level):
                request, bound, mount = self.worker_request(level=level)
                self.micro_task_agent_args(mount)
                self.extra_agent_args += ["--die-before-answer", "initialize"]
                result = native_run.run(request, observer=worker_observer, services=bound.services,
                                        cancelled=lambda: False)
                self.assertEqual(result.end.status, "error")
                self.assertEqual(result.end.reason_code, "native-disconnected")
                self.assertIsNone(result.native_identity)
                self.assertEqual(result.effective_policy.tools.requested.value, {"permissionMode": mode})
                observed = [entry for entry in self.agent_log() if entry.get("event") == "home-check"][-1]
                self.assertEqual(observed["permissionMode"], mode)

    def test_reconstructed_turn_launches_the_current_level(self):
        request, bound, mount = self.worker_request(
            level="full", continuation=RunContinuation(mode="reconstructed-new-session",
                                                       previous_session_id="previous-read-session"))
        self.micro_task_agent_args(mount)
        result = native_run.run(request, observer=worker_observer, services=bound.services,
                                cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertNotEqual(result.native_identity.session_id, "previous-read-session")
        observed = [entry for entry in self.agent_log() if entry.get("event") == "home-check"][-1]
        self.assertEqual(observed["permissionMode"], "danger-full-access")
        self.assertEqual(result.effective_policy.tools.requested.value,
                         {"permissionMode": "danger-full-access"})

    def test_worker_level_requires_the_worker_service_binding(self):
        request, bound, _ = self.worker_request(level="read")
        with self.assertRaises(BoardError) as missing_service:
            native_run.run(request, observer=worker_observer, services=None, cancelled=lambda: False)
        self.assertEqual(missing_service.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as missing_level:
            native_run.run(self.fast_request("prompt", scope="read"), observer=worker_observer,
                           services=bound.services, cancelled=lambda: False)
        self.assertEqual(missing_level.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(self.agent_log(), [])

    def test_dsh_still_has_no_router_review_mode(self):
        self.assertEqual(adapter("dsh").local_read_only_check()["reasonCode"],
                         "readonly-worker-carrier-unimplemented")

    def test_refused_operation_is_bounded_once_and_shared_by_both_evidence_paths(self):
        for inquiry in (False, True):
            with self.subTest(inquiry=inquiry):
                request, bound, mount = self.worker_request(level="read", inquiry=inquiry)
                self.micro_task_agent_args(mount, permission=True)
                path = "/outside/" + "汉字" * 300
                self.extra_agent_args += ["--permission-path", path]
                result = native_run.run(request, observer=worker_observer, services=bound.services,
                                        cancelled=lambda: False)
                self.assertEqual(result.end.status, "ok", result.end.message)
                outcome = result.value.parsed.value
                self.assertEqual(outcome["disposition"], "attention")
                self.assertEqual(outcome["request"]["attempted"], "the bounded fixture work")
                attention = json.loads(Path(mount.bridge["attentionPath"]).read_text())["requests"][0]
                ref = next(ref for ref in result.evidence_refs if ref.kind == "permission-decisions")
                decision = json.loads(Path(ref.location).read_text())["records"][0]
                operation = attention["operation"]
                self.assertEqual(operation, decision["operation"])
                self.assertTrue(operation.startswith("escalate-write: path=/outside/"))
                self.assertLessEqual(len(operation.encode("utf-8")), 512)
                self.assertLess(len(operation), len("escalate-write: path=" + path))
                self.assertEqual(decision["outcome"]["optionId"], "opt-reject")
                self.assertIn("reject_once", attention["basis"])
