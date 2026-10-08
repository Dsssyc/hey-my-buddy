"""The registered role wiring for dsh: the shared executor over the real run.

These cases need the harness's activation to be integrated (the registry entry
and the declared cooperative-checkpoint capability): the Worker runtime selects
the registered module through :func:`worker_executor`, the governed attempt runs
in the real role-controller subprocess over the fake ACP agent, and the
collection path is the shared one — provenance and report references verified by
size and hash, the two-layer stop, and the turn record built by the role. The
tests register nothing themselves; without the registration they are expected to
fail loudly at the seam instead of silently keeping a second execution path.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hey_my_buddy.buddy.harnesses.base import Adapter, ExecutionContext
from hey_my_buddy.buddy.harnesses.registry import adapter, run_seam, worker_format
from hey_my_buddy.private_dirs import context_root
from hey_my_buddy.buddy.harnesses.run_contract import (
    decode_run_request, decode_run_result, encode_run_result as encode_run_result_str)
from hey_my_buddy.buddy.roles import turn_io
from hey_my_buddy.buddy.roles.controller import worker_executor

from buddy.harnesses.dsh.acp.support import FAKE_AGENT

CKROOT = Path(__file__).resolve().parents[5]

SPEC = {"provider": "fake", "model": "m1", "effort": "high"}


def qualified_tools(attempt_id: str) -> tuple[str, str, str]:
    """The mount's mechanical qualified names, derived exactly as the module does."""
    server = "buddy_" + hashlib.sha256(attempt_id.encode()).hexdigest()[:16]
    return (f"mcp__{server}__buddy_checkpoint",
            f"mcp__{server}__buddy_answer_inquiry",
            f"mcp__{server}__buddy_finish_turn")


class DshRoleCase(unittest.TestCase):
    """One private root; the governed attempt runs through the shared executor."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-dsh-role-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cwd = self.root / "checkout"
        self.cwd.mkdir(mode=0o700)
        self.log = self.root / "logs" / "fake-agent.log"
        self.log.parent.mkdir(mode=0o700)
        self.record = self.root / "harness-record.json"
        self.environment = {k: v for k, v in os.environ.items() if not k.startswith("BUDDY_")
                            and k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        self.environment["PYTHONPATH"] = str(CKROOT / "src") + os.pathsep + \
            str(CKROOT / "tests" / "python") + os.pathsep + os.environ.get("PYTHONPATH", "")
        # A run inherits HOME by contract; the role-controller subprocess chain
        # carries this test's private one, so the agent's forced-private-home
        # tripwire holds off the invoking shell.
        (self.root / "home").mkdir(mode=0o700)
        self.environment.update(HOME=str(self.root / "home"),
                                BUDDY_STATE_DIR=str(self.root / "state"),
                                BUDDY_RUNTIME_ROOT=str(self.root / "runtime"),
                                BUDDY_DEV_SOURCE="1", BUDDY_HARNESS_RECORD_FILE=str(self.record))

    def point_record(self, *extra: str) -> None:
        command = [sys.executable, str(FAKE_AGENT), "--log", str(self.log), *extra]
        self.record.write_text(json.dumps({"dsh": {"status": "ready", "command": command}}))

    def context(self, *, attempt="attempt-1", index=1, mode="initial", previous=None, timeout=20):
        identity = {"version": 1, "taskId": "task", "attemptId": attempt, "generation": index,
                    "turnId": f"turn-{index}", "resumeMode": mode, "previousSessionId": previous,
                    "context": {}, "executionWorkspace": {}}
        return ExecutionContext(
            task_id="task", attempt_id=attempt, generation=index,
            spec={**SPEC, "cwd": str(self.cwd), "task": "fixture governed task",
                  "timeoutSeconds": timeout},
            directory=self.root / "attempts" / attempt, runtime={}, environment=dict(self.environment),
            turn={"turnId": f"turn-{index}", "input": identity})

    def governed_record(self, context, *, permission=False, answer=False) -> None:
        checkpoint, _answer, finish = qualified_tools(context.attempt_id)
        extra = ["--prompt-mode", "governed", "--bridge-config",
                 str(context_root(context, "dsh") / "finish-bridge.json"),
                 "--finish-tool", finish, "--checkpoint-tool", checkpoint]
        if permission:
            extra.append("--governed-permission")
        if answer:
            extra += ["--wait-for-inquiry", "12", "--answer-inquiry"]
        self.point_record(*extra)

    def execute(self, context, *, answer=False, permission=False):
        self.governed_record(context, permission=permission, answer=answer)
        executor = worker_executor("dsh")
        handle = executor.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(context.timeout_seconds + 15),
                             "the role controller did not settle within its deadline")
        return handle, executor.collect(handle, context)


class DescriptionSeamTests(DshRoleCase):
    def test_the_description_has_no_legacy_execution_entries(self):
        description = adapter("dsh")
        self.assertNotIsInstance(description, Adapter)
        for entry in ("prepare", "start", "collect", "cancel", "start_no_tool_structured",
                      "start_read_only_structured", "arguments", "inquiry_paths",
                      "inquiry_credentials"):
            self.assertFalse(hasattr(description, entry), entry)
        self.assertIsNone(importlib.util.find_spec("hey_my_buddy.buddy.harnesses.dsh.runner"))
        self.assertIsNone(importlib.util.find_spec("hey_my_buddy.buddy.harnesses.dsh.catalog"))
        self.assertIsNone(importlib.util.find_spec("hey_my_buddy.buddy.harnesses.dsh.yaml_bridge"))
        self.assertIs(description.validate_turn_provenance, run_seam("dsh").validate_turn_provenance)
        module = run_seam("dsh")
        for operation in ("run", "run_discovery", "check_preparation", "prepare_services",
                          "validate_turn_provenance", "session_facts", "native_evidence",
                          "make_inquiry_bridge"):
            self.assertTrue(callable(getattr(module, operation)), operation)
        self.assertIs(worker_executor("dsh").module, module)
        self.assertIsNone(worker_format("dsh"),
                          "the governed role rules come from the shared worker services, not a format")

    def test_a_read_only_review_stays_unimplemented(self):
        description = adapter("dsh")
        eligibility = description.local_read_only_check()
        self.assertFalse(eligibility["eligible"])
        self.assertEqual(eligibility["reasonCode"], "readonly-worker-carrier-unimplemented")
        self.assertFalse(description.read_only_structured)

    def test_check_preparation_refuses_only_without_a_ready_command(self):
        """The restored availability contract, without the retired Node runner gate.

        A bound bad selection is still refused, an unbound but discoverable
        installed command is still available through the same bounded discovery
        the run seam falls back to, and a failed discovery scan refuses — all
        decided from mocked records, never a launched harness.
        """
        from hey_my_buddy.buddy.harnesses import discovery, runtime_selection
        seam = run_seam("dsh")
        description = adapter("dsh")
        ready = {"status": "ready", "command": ["/installed/dsh"]}
        with mock.patch.object(runtime_selection, "selected", return_value=ready):
            self.assertIsNone(seam.check_preparation({}, {}))
            self.assertEqual(description.available(), (True, None))
        bad = {"status": "login-required", "remedy": "log in to dsh"}
        with mock.patch.object(runtime_selection, "selected", return_value=bad):
            self.assertEqual(description.available(), (False, "log in to dsh"))
            with self.assertRaises(Exception) as caught:
                seam.check_preparation({}, {})
            self.assertEqual(caught.exception.code, "ADAPTER_UNAVAILABLE")
        detected = {"status": "ready", "available": True, "command": ["/installed/dsh"]}
        with mock.patch.object(runtime_selection, "selected", return_value=None), \
                mock.patch.object(discovery, "discover", return_value=detected):
            self.assertEqual(description.available(), (True, None))
            self.assertIsNone(seam.check_preparation({}, {}))
        failed = {"status": "missing", "available": False, "remedy": "install dsh",
                  "reasonCode": "not-found"}
        with mock.patch.object(runtime_selection, "selected", return_value=None), \
                mock.patch.object(discovery, "discover", return_value=failed):
            self.assertEqual(description.available(), (False, "install dsh"))
            with self.assertRaises(Exception) as caught:
                seam.check_preparation({}, {})
            self.assertEqual(caught.exception.code, "ADAPTER_UNAVAILABLE")


class WorkerRegisteredRunTests(DshRoleCase):
    def test_a_noncompleted_native_turn_keeps_its_reason_without_a_finish_receipt(self):
        for reason in ("max_tokens", "refusal"):
            with self.subTest(reason=reason):
                context = self.context(attempt="stop-" + reason)
                self.governed_record(context)
                record = json.loads(self.record.read_text())
                record["dsh"]["command"] += ["--stop-reason", reason]
                self.record.write_text(json.dumps(record))
                executor = worker_executor("dsh")
                handle = executor.start(context)
                self.addCleanup(lambda h=handle: h.terminate(grace_seconds=0.2) if h.group_alive() else None)
                self.assertIsNotNone(handle.wait(30))
                outcome = executor.collect(handle, context)
                self.assertEqual(outcome.status, "failed")
                self.assertEqual(outcome.result["code"], "native-" + reason.replace("_", "-"))
                self.assertTrue(outcome.shutdown_confirmed)
                self.assertNotIn("turn", outcome.result)

    def test_worker_consumes_frames_and_publishes_the_role_record(self):
        context = self.context()
        handle, outcome = self.execute(context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        request = decode_run_request(Path(handle.role_run_control["requestFile"]).read_bytes())
        result = decode_run_result(Path(handle.log_paths["stdout"]).read_bytes())
        self.assertEqual(request.identity, result.identity)
        self.assertEqual(request.identity, handle.role_run_identity)
        self.assertEqual(request.tool_scope, "write")
        self.assertNotIn("frozenAccount", request.to_payload())
        self.assertEqual(request.identity.input_sha256, turn_io.input_hash(context.turn_input))
        turn = outcome.result["turn"]
        self.assertEqual(turn["outcome"], result.value.parsed.value)
        provenance = turn["provenance"]
        self.assertEqual(provenance["adapter"], "dsh")
        self.assertEqual(provenance["nativeSessionId"], turn["sessionId"])
        self.assertTrue(provenance["receiptVerified"])
        self.assertEqual(result.native_identity.session_id, turn["sessionId"])
        native = outcome.result["nativeSession"]
        self.assertEqual(native["sessionId"], turn["sessionId"])
        self.assertEqual(native["storageOwner"], "buddy-attempt")
        self.assertTrue(native["bindingPresent"])
        self.assertTrue(native["resumable"], "the native facts carry a verified complete turn")
        self.assertIn("inquiry", outcome.result)
        self.assertTrue(outcome.result["inquiry"]["mounted"])
        self.assertEqual(outcome.result["inquiry"]["deliveryMode"], "cooperative-checkpoint")
        self.assertIn("nativeAttention", outcome.result)
        self.assertFalse(outcome.result["attentionRequired"],
                         "no native interactive request was refused in this turn")
        self.assertEqual(handle.process.args[2], "hey_my_buddy.buddy.roles.run_controller")

    def test_an_answered_host_question_flows_through_the_governed_turn(self):
        import threading
        import time as time_module
        from hey_my_buddy.buddy.harnesses.live import InquiryPayload, LiveRequest
        from hey_my_buddy.buddy.harnesses.run_contract import decode_run_request as decode_request
        from hey_my_buddy.json_codec import decode_strict_json
        context = self.context()
        self.governed_record(context, answer=True)
        executor = worker_executor("dsh")
        holder: list = []

        def attempt():
            handle = executor.start(context)
            holder.append(handle)
            self.addCleanup(lambda: handle.terminate(grace_seconds=0.2)
                            if handle.group_alive() else None)

        thread = threading.Thread(target=attempt)
        thread.start()
        root = context_root(context, "dsh")
        request_file = root / "role-run-request.json"
        deadline = time_module.monotonic() + 30
        while time_module.monotonic() < deadline and not request_file.is_file():
            time_module.sleep(0.05)
        self.assertTrue(request_file.is_file(), "the controller never published its run request")
        request = decode_request(request_file.read_bytes())
        control = decode_strict_json((root / "role-run-control.json").read_bytes())
        from hey_my_buddy.buddy.roles.live import handle_live_binding
        channel = None
        while time_module.monotonic() < deadline:
            if holder:
                _, channel = handle_live_binding(holder[0])
            if channel is not None:
                break
            time_module.sleep(0.05)
        self.assertIsNotNone(channel, "the held controller endpoint never became ready")
        live_request = LiveRequest(identity=request.identity, request_id="live-1", kind="inquiry",
                                   payload=InquiryPayload(question_id="inq-1",
                                                          question="bounded wiring question"))
        reply = None
        while time_module.monotonic() < deadline:
            reply = channel.request(live_request, timeout_ms=1000)
            if reply.status != "unavailable":
                break
            time_module.sleep(0.05)
        self.assertEqual(reply.status, "queued", (reply.status, reply.reason_code, reply.error_code))
        thread.join(timeout=60)
        handle = holder[0]
        self.assertIsNotNone(handle.wait(context.timeout_seconds + 15))
        outcome = executor.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "completed",
                         "the answered question no longer blocks the completed finish")
        result = decode_run_result(Path(handle.log_paths["stdout"]).read_bytes())
        reference = next(ref for ref in result.evidence_refs if ref.kind == "inquiry-report")
        report = json.loads(Path(reference.location).read_text())
        self.assertEqual(report["answered"], 1)
        journal = Path(control["inquiry"]["resultsPath"])
        states = [json.loads(line).get("state") for line in journal.read_text().splitlines()
                  if line.strip() and json.loads(line).get("inquiryId")]
        self.assertIn("delivered", states)
        self.assertEqual(states[-1], "answered")

    def test_a_refused_upgrade_becomes_attention_facts(self):
        context = self.context()
        handle, outcome = self.execute(context, permission=True)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "attention")
        self.assertIs(outcome.result["attentionRequired"], True)
        self.assertEqual(outcome.result["nativeAttention"]["requests"], 1)
        report = outcome.result["inquiry"]
        self.assertTrue(report["mounted"])

    def test_a_changed_provenance_reference_fails_without_losing_real_stop(self):
        context = self.context()
        handle, original = self.execute(context)
        self.assertEqual(original.status, "ok", original.to_report())
        result = decode_run_result(Path(handle.log_paths["stdout"]).read_bytes())
        reference = next(ref for ref in result.evidence_refs if ref.kind == "turn-provenance")
        value = json.loads(Path(reference.location).read_text())
        value["receiptVerified"] = False
        Path(reference.location).write_text(json.dumps(value))
        refused = worker_executor("dsh").collect(handle, context)
        self.assertEqual(refused.status, "failed")
        self.assertTrue(refused.shutdown_confirmed)
        self.assertEqual(refused.result["code"], "invalid-role-result")
        self.assertNotIn("turn", refused.result)
        self.assertNotIn("workspaceSeal", refused.result)

    def test_each_bad_report_keeps_the_other_verified_report(self):
        for kind, failed_key, kept_key in (("inquiry-report", "inquiry", "nativeAttention"),
                                           ("attention-report", "nativeAttention", "inquiry")):
            with self.subTest(kind=kind):
                context = self.context(attempt=f"attempt-{kind}", index=2 if kind == "attention-report" else 1)
                handle, original = self.execute(context, permission=True)
                self.assertEqual(original.status, "ok", original.to_report())
                result = decode_run_result(Path(handle.log_paths["stdout"]).read_bytes())
                reference = next(ref for ref in result.evidence_refs if ref.kind == kind)
                path = Path(reference.location)
                path.write_bytes(path.read_bytes() + b" ")
                refused = worker_executor("dsh").collect(handle, context)
                self.assertEqual(refused.status, "failed")
                self.assertTrue(refused.shutdown_confirmed)
                self.assertEqual(refused.result["code"], "invalid-role-result")
                self.assertIn(kept_key, refused.result)
                self.assertEqual(refused.result[kept_key], original.result[kept_key])
                self.assertNotIn(failed_key, refused.result)
                self.assertNotIn("turn", refused.result)

    def test_a_worker_result_claiming_an_unconfirmed_native_stop_cannot_publish(self):
        context = self.context()
        handle, original = self.execute(context)
        self.assertEqual(original.status, "ok", original.to_report())
        path = Path(handle.log_paths["stdout"])
        fields = decode_run_result(path.read_bytes()).to_payload()
        fields["stopEvidence"]["native"]["groupState"] = "unknown"
        path.write_text(encode_run_result_str(decode_run_result(fields)))
        refused = worker_executor("dsh").collect(handle, context)
        self.assertEqual(refused.status, "failed")
        self.assertIs(refused.shutdown_confirmed, False,
                      "the result's own native stop layer is required, never the handle alone")
        self.assertNotIn("turn", refused.result)

    def test_an_unconfirmed_controller_stops_the_turn_but_keeps_verified_reports(self):
        context = self.context()
        handle, original = self.execute(context)
        self.assertEqual(original.status, "ok", original.to_report())
        with mock.patch.object(handle, "shutdown_confirmed", return_value=False), \
                mock.patch.object(turn_io, "seal_workspace",
                                  side_effect=AssertionError("unconfirmed seal")):
            refused = worker_executor("dsh").collect(handle, context)
        self.assertEqual(refused.status, "failed")
        self.assertIs(refused.shutdown_confirmed, False)
        self.assertIs(refused.result["processState"]["shutdownConfirmed"], True)
        self.assertIn("inquiry", refused.result)
        self.assertIn("nativeAttention", refused.result)
        self.assertNotIn("turn", refused.result)
        self.assertNotIn("workspaceSeal", refused.result)


class DiscoverySeamTests(DshRoleCase):
    def test_discovery_routes_through_the_seam_without_a_prompt(self):
        from hey_my_buddy.buddy.harnesses.runtime_selection import bound, environment_for
        self.point_record("--prompt-mode", "final")
        record = {"adapter": "dsh", **json.loads(self.record.read_text())["dsh"]}
        with mock.patch.dict(os.environ, self.environment):
            with bound([record], environment={**self.environment}):
                catalog = adapter("dsh").discover_models()
        self.assertEqual(catalog["adapter"], "dsh")
        self.assertEqual(catalog["source"], "dsh-acp-session-config")
        provider = next(entry for entry in catalog["providers"] if entry["provider"] == "fake")
        self.assertIn("m1", [model["id"] for model in provider["models"]])
        prompts = [entry for entry in self.agent_log()
                   if entry.get("dir") == "in" and (entry.get("raw") or {}).get("method") == "session/prompt"]
        self.assertEqual(prompts, [], "discovery never sends a prompt")

    def agent_log(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
