"""Synthetic Host stop and same-session summary across the role boundary."""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses import run_contract as rc
from hey_my_buddy.buddy.harnesses.base import ExecutionContext
from hey_my_buddy.buddy.harnesses.codex import native_run as codex_native_run
from hey_my_buddy.buddy.harnesses.registry import worker_format
from hey_my_buddy.buddy.roles import controller, run_controller, run_execution
from hey_my_buddy.buddy.roles.turn_io import input_hash, private_json
from hey_my_buddy.private_dirs import ensure_private_dir


def request_for(control):
    root = Path(control["privateRoot"])
    return rc.RunRequest(
        identity=rc.RunIdentity(task_id="task-stop", attempt_id="attempt-stop", generation=1,
                                invocation_id=control["invocationId"], turn_id="turn-stop",
                                input_sha256="a" * 64),
        harness="zcode", buddy=rc.RunBuddy(provider="provider", model="model", effort="off"),
        cwd=control["cwd"], private_state=rc.PrivateStatePaths(
            invocation_root=str(root), native_root=control["nativeRoot"]),
        input_text="worker prompt", tool_scope="write", output_schema=rc.FrozenJson({"type": "object"}),
        budget=rc.RunBudget(timeout_seconds=control["timeoutSeconds"]),
    )


def result_for(request, *, status="cancelled", session="native-session", resumable=True,
               native_gone=True):
    value = (rc.RunValue(schema_status="valid", parsed=rc.FrozenJson({"outcome": {
        "disposition": "completed", "summary": "Stopped partial work summary",
        "remaining": [], "decisions": [], "artifacts": [], "request": None}})) if status == "ok" else None)
    return rc.RunResult(identity=request.identity, harness=request.harness,
        end=rc.RunEnd(status=status, reason_code="user-cancel" if status == "cancelled" else None),
        native_identity=rc.NativeIdentity(session_id=session),
        value=value,
        continuation=rc.ContinuationFacts(resumable=resumable),
        stop_evidence=rc.StopEvidence(native=rc.StopLayer(group_state="gone" if native_gone else "unknown")))


class RoleStopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hmb-role-stop-",
                                                 dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.invocation = ensure_private_dir(self.root / "invocation")
        self.control = {"privateRoot": str(self.invocation), "nativeRoot": str(self.root / "native"),
                        "cwd": str(self.root), "harness": "zcode", "operation": "worker",
                        "invocationId": "first-invocation", "requestFile": str(self.invocation / "request.json"),
                        "verdictFile": str(self.invocation / "verdict.json"),
                        "outputFile": str(self.root / "turn-output.json"), "timeoutSeconds": None}
        self.identity = request_for({**self.control, "timeoutSeconds": 60}).identity

    def test_host_stop_signals_once_and_keeps_original_deadline(self):
        handle = SimpleNamespace(role_run_control=self.control, role_run_identity=self.identity,
                                 cancel_requested=False, signal_group=mock.Mock())
        executor = SimpleNamespace(description=SimpleNamespace(native_resume=True), cancel=mock.Mock())
        deadline = time.monotonic() + 300
        controller.worker_stop(executor, handle, stop_initiator="host", grace_deadline=deadline)
        controller.worker_stop(executor, handle, stop_initiator="host", grace_deadline=deadline + 300)
        self.assertEqual(handle.grace_deadline, deadline)
        self.assertEqual(handle.signal_group.call_count, 1)
        value = run_controller.decode_strict_json((self.invocation / run_execution._STOP_CONTROL).read_bytes())
        self.assertEqual(value["binding"], self.identity.to_payload())
        self.assertEqual(value["graceDeadline"], deadline)
        executor.cancel.assert_not_called()

    def test_console_and_unsupported_harness_force_immediate_cancel(self):
        for initiator, resume in (("console", True), ("host", False)):
            with self.subTest(initiator=initiator, resume=resume):
                handle = SimpleNamespace()
                executor = SimpleNamespace(description=SimpleNamespace(native_resume=resume), cancel=mock.Mock())
                controller.worker_stop(executor, handle, stop_initiator=initiator,
                                       grace_deadline=time.monotonic() + 300)
                executor.cancel.assert_called_once_with(handle, grace_seconds=0)
                self.assertEqual(handle.stop_initiator, initiator)

    def test_missing_host_deadline_forces_stop_and_console_clears_grace(self):
        executor = SimpleNamespace(description=SimpleNamespace(native_resume=True), cancel=mock.Mock())
        handle = SimpleNamespace(grace_deadline=time.monotonic() + 300,
                                 grace_expired=True, stop_initiator="host")
        controller.worker_stop(executor, handle, stop_initiator="console", grace_deadline=None)
        self.assertIsNone(handle.grace_deadline)
        self.assertIs(handle.grace_expired, False)
        self.assertEqual(handle.stop_initiator, "console")
        self.assertIs(handle.force_stop_requested, True)
        executor.cancel.assert_called_once_with(handle, grace_seconds=0)
        other = SimpleNamespace()
        executor.cancel.reset_mock()
        controller.worker_stop(executor, other, stop_initiator="host", grace_deadline=None)
        self.assertIsNone(other.grace_deadline)
        self.assertIs(other.force_stop_requested, True)
        executor.cancel.assert_called_once_with(other, grace_seconds=0)

    def test_expired_grace_allows_only_controller_reaping_before_outer_kill(self):
        handle = SimpleNamespace(stop_initiator="host", grace_expired=True,
                                 role_run_control=self.control, role_run_identity=self.identity)
        executor = SimpleNamespace(description=SimpleNamespace(native_resume=True), cancel=mock.Mock())
        controller.worker_cancel(executor, handle, grace_seconds=0)
        executor.cancel.assert_called_once_with(handle, grace_seconds=5.0)
        value = run_controller.decode_strict_json((self.invocation / run_execution._STOP_CONTROL).read_bytes())
        self.assertEqual(value, {"kind": "force-stop", "stopInitiator": "host",
                                 "binding": self.identity.to_payload()})

    def test_registered_role_force_overwrites_host_stop_and_reaps_before_outer_kill(self):
        handle = SimpleNamespace(role_run_control=self.control, role_run_identity=self.identity,
                                 cancel_requested=False, signal_group=mock.Mock())
        executor = SimpleNamespace(description=SimpleNamespace(native_resume=True), cancel=mock.Mock())
        controller.worker_stop(executor, handle, stop_initiator="host",
                               grace_deadline=time.monotonic() + 300)
        controller.worker_stop(executor, handle, stop_initiator="console", grace_deadline=None)
        value = run_controller.decode_strict_json((self.invocation / run_execution._STOP_CONTROL).read_bytes())
        self.assertEqual(value, {"kind": "force-stop", "stopInitiator": "console",
                                 "binding": self.identity.to_payload()})
        self.assertIsNone(handle.grace_deadline)
        self.assertIs(handle.force_stop_requested, True)
        executor.cancel.assert_called_once_with(handle, grace_seconds=5.0)

    def _execute(self, *, resumable=True, native_gone=True, summary_session="native-session",
                 summary_native_gone=True, summary_status="ok", summary_raises=False,
                 force_before_summary=False, force_during_setup=False, expired=False):
        control = {**self.control, "timeoutSeconds": 60}
        request = request_for(control)
        deadline = time.monotonic() - 1 if expired else time.monotonic() + 300
        private_json(self.invocation / run_execution._STOP_CONTROL,
                     {"kind": "host-graceful-stop", "stopInitiator": "host",
                      "graceDeadline": deadline, "binding": request.identity.to_payload()}, exclusive=True)
        cancellation = threading.Event()
        calls = []
        def run(_module, native_request, *, observer, services, cancelled):
            calls.append((native_request, cancelled()))
            if len(calls) == 1:
                cancellation.set()
                if force_before_summary:
                    private_json(self.invocation / run_execution._STOP_CONTROL,
                                 {"kind": "force-stop", "stopInitiator": "console",
                                  "binding": request.identity.to_payload()})
                return result_for(native_request, resumable=resumable, native_gone=native_gone)
            if summary_raises:
                raise OSError("native summary failed after launch")
            return result_for(native_request, status=summary_status, session=summary_session,
                              native_gone=summary_native_gone)
        def prepare(candidate, _module):
            if force_during_setup and candidate["privateRoot"] != str(self.invocation):
                private_json(self.invocation / run_execution._STOP_CONTROL,
                             {"kind": "force-stop", "stopInitiator": "console",
                              "binding": request.identity.to_payload()})
            return request_for(candidate), None, lambda _fact: rc.FEEDBACK_CONTINUE, None
        module = SimpleNamespace()
        with mock.patch.object(run_controller, "run_seam", return_value=module), \
             mock.patch.object(run_controller, "adapter", return_value=SimpleNamespace(native_resume=True)), \
             mock.patch.object(run_controller.run_execution, "worker_request", side_effect=prepare), \
             mock.patch.object(run_controller, "_controller_live", side_effect=lambda *args: nullcontext(None)), \
             mock.patch.object(run_controller, "run_harness", side_effect=run), \
             mock.patch.object(run_controller.run_execution, "worker_delivery", return_value=({}, {}, {})):
            frame, code = run_controller.execute(control, cancellation, self.root)
        return rc.decode_run_result(frame), code, calls

    def test_interrupted_run_resumes_same_session_with_new_invocation_and_fresh_cancel_check(self):
        result, code, calls = self._execute()
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        first, second = (entry[0] for entry in calls)
        self.assertEqual(second.continuation.mode, "native-session")
        self.assertEqual(second.continuation.previous_session_id, "native-session")
        self.assertEqual(second.identity.task_id, first.identity.task_id)
        self.assertEqual(second.identity.attempt_id, first.identity.attempt_id)
        self.assertEqual(second.identity.turn_id, first.identity.turn_id)
        self.assertNotEqual(second.identity.invocation_id, first.identity.invocation_id)
        self.assertEqual(second.private_state.native_root, first.private_state.native_root)
        self.assertNotEqual(second.private_state.invocation_root, first.private_state.invocation_root)
        self.assertFalse(calls[1][1], "the first interrupt must not cancel the summary run")
        self.assertEqual(result.identity, second.identity)
        self.assertTrue((self.invocation / run_execution._STOP_SUMMARY).is_file())

    def test_unknown_native_stop_or_unbound_continuation_never_resumes(self):
        for resumable, gone, expired in ((False, True, False), (True, False, False),
                                          (True, True, True)):
            with self.subTest(resumable=resumable, gone=gone, expired=expired):
                # Each execution owns fresh exclusive files.
                sub = ensure_private_dir(self.root / f"case-{resumable}-{gone}-{expired}")
                self.invocation = sub
                self.control.update(privateRoot=str(sub), requestFile=str(sub / "request.json"),
                                    verdictFile=str(sub / "verdict.json"))
                result, code, calls = self._execute(resumable=resumable, native_gone=gone, expired=expired)
                self.assertEqual(len(calls), 1)
                self.assertEqual(code, 1)
                self.assertEqual(result.end.status, "cancelled")

    def test_console_escalation_before_summary_never_starts_second_native(self):
        result, code, calls = self._execute(force_before_summary=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.identity, calls[0][0].identity)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")
        self.assertEqual(code, 1)
        self.assertFalse((self.invocation / "stop-summary").exists())

    def test_force_during_summary_setup_beats_a_late_signal_snapshot(self):
        result, code, calls = self._execute(force_during_setup=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.identity, calls[0][0].identity)
        self.assertEqual(code, 1)
        self.assertTrue((self.invocation / run_execution._STOP_SUMMARY).is_file())

    def test_foreign_summary_session_is_not_accepted_as_graceful(self):
        result, code, calls = self._execute(summary_session="foreign-session")
        self.assertEqual(len(calls), 2)
        self.assertEqual(code, 0)
        self.assertEqual(result.identity, calls[1][0].identity)
        self.assertEqual(result.native_identity.session_id, "foreign-session")
        verdict = run_controller.decode_strict_json(
            (self.invocation / "stop-summary" / run_execution._VERDICT).read_bytes())
        self.assertIs(verdict["gracefulStop"], False)

    def test_second_native_unknown_or_failed_result_replaces_first_stop_receipt(self):
        for case in ("unknown", "failed"):
            with self.subTest(case=case):
                sub = ensure_private_dir(self.root / case)
                self.invocation = sub
                self.control.update(privateRoot=str(sub), requestFile=str(sub / "request.json"),
                                    verdictFile=str(sub / "verdict.json"))
                result, code, calls = self._execute(
                    summary_native_gone=case != "unknown",
                    summary_status="error" if case == "failed" else "ok")
                self.assertEqual(result.identity, calls[1][0].identity)
                self.assertEqual(result.stop_evidence.native.group_state,
                                 "unknown" if case == "unknown" else "gone")
                self.assertEqual(result.end.status, "error" if case == "failed" else "ok")
                self.assertEqual(code, 1 if case == "failed" else 0)
                stdout = sub / "controller.stdout"
                stdout.write_text(rc.encode_run_result(result) + "\n")
                handle = SimpleNamespace(role_run_control={**self.control, "timeoutSeconds": 60},
                                         role_run_identity=self.identity, stop_initiator="host")
                self.assertEqual(run_execution._read_run(handle, stdout), result)
                self.assertEqual(handle.role_run_active_control["privateRoot"], str(sub / "stop-summary"))
                verdict = run_controller.decode_strict_json(
                    (sub / "stop-summary" / run_execution._VERDICT).read_bytes())
                self.assertIs(verdict["gracefulStop"], False)

    def test_exception_after_second_native_launch_reports_unknown_stop(self):
        result, code, calls = self._execute(summary_raises=True)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.identity, calls[1][0].identity)
        self.assertEqual(result.end.status, "unknown")
        self.assertEqual(result.stop_evidence.native.group_state, "unknown")
        self.assertEqual(code, 1)

    def test_unknown_second_native_stop_cannot_seal_or_claim_graceful(self):
        for summary_raises in (False, True):
            with self.subTest(summary_raises=summary_raises):
                sub = ensure_private_dir(self.root / f"collect-{summary_raises}")
                self.invocation = sub
                self.control.update(privateRoot=str(sub), requestFile=str(sub / "request.json"),
                                    verdictFile=str(sub / "verdict.json"))
                result, code, _calls = self._execute(summary_native_gone=False,
                                                     summary_raises=summary_raises)
                stdout = sub / "controller.stdout"
                stdout.write_text(rc.encode_run_result(result) + "\n")
                handle = SimpleNamespace(
                    role_run_control={**self.control, "timeoutSeconds": 60},
                    role_run_identity=self.identity, stop_initiator="host",
                    grace_deadline=time.monotonic() - 1 if summary_raises else time.monotonic() + 300,
                    cancel_requested=True, grace_expired=summary_raises,
                    process=SimpleNamespace(returncode=code), log_paths={"stdout": str(stdout)},
                    shutdown_confirmed=lambda: True)
                context = SimpleNamespace(turn_input={"resumeMode": "initial"}, spec={"harness": "zcode"},
                    effective_workspace=None, turn_output_file=lambda: self.root / "turn-output.json")
                module = SimpleNamespace(session_facts=lambda root, session: {"bindingPresent": False},
                                         validate_turn_provenance=lambda record: None)
                executor = run_execution.WorkerRunExecutor(SimpleNamespace(name="zcode"), module)
                with mock.patch.object(run_execution.turn_io, "seal_workspace") as seal, \
                     mock.patch.object(run_execution, "_artifacts") as artifacts:
                    outcome = executor.collect(handle, context)
                self.assertEqual(outcome.status, "failed")
                self.assertFalse(outcome.shutdown_confirmed)
                self.assertIsNone(outcome.result["stopReason"])
                self.assertNotIn("turn", outcome.result)
                self.assertNotIn("workspaceSeal", outcome.result)
                seal.assert_not_called()
                artifacts.assert_not_called()

    def test_collect_keeps_valid_summary_turn_as_cancelled_partial_work(self):
        result, code, _calls = self._execute()
        self.assertEqual(code, 0)
        stdout = self.root / "controller.stdout"
        stdout.write_text(rc.encode_run_result(result) + "\n")
        handle = SimpleNamespace(
            role_run_control={**self.control, "timeoutSeconds": 60},
            role_run_identity=self.identity, stop_initiator="host", grace_deadline=time.monotonic() + 300,
            cancel_requested=True, grace_expired=False,
            process=SimpleNamespace(returncode=0), log_paths={"stdout": str(stdout)},
            shutdown_confirmed=lambda: True)
        turn = {"outcome": {"disposition": "assistance", "summary": "partial work"},
                "sessionId": "native-session"}
        context = SimpleNamespace(turn_input={"resumeMode": "initial"}, spec={"harness": "zcode"},
                                  effective_workspace=None, turn_output_file=lambda: self.root / "turn-output.json")
        module = SimpleNamespace(session_facts=lambda root, session: {"bindingPresent": True,
                                                                       "sessionId": session},
                                 validate_turn_provenance=lambda record: None)
        executor = run_execution.WorkerRunExecutor(SimpleNamespace(name="zcode"), module)
        with mock.patch.object(run_execution, "_worker_result"), \
             mock.patch.object(run_execution.turn_io, "read_turn", return_value=(turn, None)), \
             mock.patch.object(run_execution.turn_io, "seal_workspace") as seal, \
             mock.patch.object(run_execution, "_artifacts", return_value=[]):
            outcome = executor.collect(handle, context)
        self.assertEqual(outcome.status, "cancelled")
        self.assertEqual(outcome.result["stopReason"], "graceful-stop")
        self.assertEqual(outcome.result["turn"], turn)
        seal.assert_not_called()

    def test_cancel_arriving_after_first_native_completion_keeps_valid_turn_completed(self):
        turn_input = {"taskId": "task-stop", "attemptId": "attempt-stop", "generation": 1,
                      "turnId": "turn-stop", "resumeMode": "initial", "previousSessionId": None}
        context = ExecutionContext(task_id="task-stop", attempt_id="attempt-stop", generation=1,
            spec={"harness": "codex", "cwd": str(self.root)}, directory=self.root,
            runtime={}, environment={"HMB_STATE_DIR": str(self.root)},
            turn={"turnId": "turn-stop", "input": turn_input})
        control = {"operation": "worker", "harness": "codex", "privateRoot": str(self.invocation),
                   "nativeRoot": str(ensure_private_dir(self.root / "native")),
                   "requestFile": str(self.invocation / "request.json"),
                   "verdictFile": str(self.invocation / "verdict.json"),
                   "outputFile": str(context.turn_output_file())}
        identity = rc.RunIdentity(task_id="task-stop", attempt_id="attempt-stop", generation=1,
                                  invocation_id="first-invocation", turn_id="turn-stop",
                                  input_sha256=input_hash(turn_input))
        request = rc.RunRequest(identity=identity, harness="codex",
            buddy=rc.RunBuddy(provider="provider", model="model", effort="off"),
            cwd=str(self.root), private_state=rc.PrivateStatePaths(
                invocation_root=str(self.invocation), native_root=control["nativeRoot"]),
            input_text="original Worker prompt", tool_scope="write",
            output_schema=rc.FrozenJson(worker_format("codex").schema),
            budget=rc.RunBudget(timeout_seconds=None))
        Path(control["requestFile"]).write_text(rc.encode_run_request(request))
        private_json(Path(control["verdictFile"]),
                     {"stopReason": None, "elapsedMs": 1, "gracefulStop": False})
        facts = {"harness": "codex", "nativeThreadId": "session-complete",
                 "nativeTurnId": "native-turn-complete", "finalMessageCompleted": True,
                 "nativeTurnStarted": True, "nativeTurnCompleted": True,
                 "finalItemId": "item-complete", "eventSeq": 2}
        evidence_path = self.invocation / "native-turn-facts.json"
        private_json(evidence_path, facts)
        raw = evidence_path.read_bytes()
        outcome = {"disposition": "completed", "summary": "The first run finished",
                   "remaining": [], "decisions": [], "artifacts": [], "request": None}
        result = rc.RunResult(identity=identity, harness="codex",
            end=rc.RunEnd(status="ok", native_exit_code=0),
            native_identity=rc.NativeIdentity(session_id="session-complete", turn_id="native-turn-complete"),
            value=rc.RunValue(schema_status="valid", parsed=rc.FrozenJson({"outcome": outcome})),
            continuation=rc.ContinuationFacts(resumable=True),
            stop_evidence=rc.StopEvidence(native=rc.StopLayer(group_state="gone")),
            evidence_refs=(rc.EvidenceRef(kind="native-turn-facts", location=str(evidence_path),
                                          size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()),))
        stdout = self.root / "first-complete.stdout"
        stdout.write_text(rc.encode_run_result(result) + "\n")
        executor = run_execution.WorkerRunExecutor(SimpleNamespace(name="codex"), codex_native_run)
        for initiator in ("host", "console"):
            with self.subTest(initiator=initiator):
                context.turn_output_file().unlink(missing_ok=True)
                handle = SimpleNamespace(role_run_control=control, role_run_identity=identity,
                    stop_initiator=initiator, grace_deadline=time.monotonic() + 300 if initiator == "host" else None,
                    grace_expired=False, force_stop_requested=True, cancel_requested=True,
                    process=SimpleNamespace(returncode=0), log_paths={"stdout": str(stdout)},
                    shutdown_confirmed=lambda: True)
                collected = executor.collect(handle, context)
                self.assertEqual(collected.status, "ok")
                self.assertTrue(collected.shutdown_confirmed)
                self.assertEqual(collected.result["turn"]["outcome"], outcome)
                self.assertNotIn("stopReason", collected.result)

    def test_console_escalation_reads_second_receipt_without_graceful_label(self):
        result, code, _calls = self._execute()
        stdout = self.root / "console-controller.stdout"
        stdout.write_text(rc.encode_run_result(result) + "\n")
        handle = SimpleNamespace(
            role_run_control={**self.control, "timeoutSeconds": 60},
            role_run_identity=self.identity, stop_initiator="console", graceful_stop_started=True,
            force_stop_requested=True, grace_deadline=None, grace_expired=False,
            cancel_requested=True, process=SimpleNamespace(returncode=code),
            log_paths={"stdout": str(stdout)}, shutdown_confirmed=lambda: True)
        context = SimpleNamespace(turn_input={"resumeMode": "initial"}, spec={"harness": "zcode"},
                                  effective_workspace=None, turn_output_file=lambda: self.root / "turn-output.json")
        module = SimpleNamespace(session_facts=lambda root, session: {"bindingPresent": True},
                                 validate_turn_provenance=lambda record: None)
        executor = run_execution.WorkerRunExecutor(SimpleNamespace(name="zcode"), module)
        with mock.patch.object(run_execution, "_worker_result"), \
             mock.patch.object(run_execution.turn_io, "read_turn", return_value=(None, None)), \
             mock.patch.object(run_execution, "_artifacts", return_value=[]):
            outcome = executor.collect(handle, context)
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertEqual(outcome.result["stopReason"], "console-stop")

    def test_expired_grace_overrides_even_a_valid_summary(self):
        result, _, _ = self._execute()
        stdout = self.root / "controller.stdout"
        stdout.write_text(rc.encode_run_result(result) + "\n")
        handle = SimpleNamespace(
            role_run_control={**self.control, "timeoutSeconds": 60},
            role_run_identity=self.identity, stop_initiator="host", grace_deadline=time.monotonic() - 1,
            cancel_requested=True, grace_expired=True,
            process=SimpleNamespace(returncode=0), log_paths={"stdout": str(stdout)},
            shutdown_confirmed=lambda: True)
        context = SimpleNamespace(turn_input={"resumeMode": "initial"}, spec={"harness": "zcode"},
                                  effective_workspace=None, turn_output_file=lambda: self.root / "turn-output.json")
        module = SimpleNamespace(session_facts=lambda root, session: {"bindingPresent": True},
                                 validate_turn_provenance=lambda record: None)
        executor = run_execution.WorkerRunExecutor(SimpleNamespace(name="zcode"), module)
        with mock.patch.object(run_execution, "_worker_result"), \
             mock.patch.object(run_execution.turn_io, "read_turn", return_value=({"sessionId": "native-session"}, None)), \
             mock.patch.object(run_execution, "_artifacts", return_value=[]):
            outcome = executor.collect(handle, context)
        self.assertEqual(outcome.result["stopReason"], "grace-expired")
