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
from hey_my_buddy.buddy.roles import controller, inline_run, run_execution
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
               native_gone=True, usage=None):
    value = (rc.RunValue(schema_status="valid", parsed=rc.FrozenJson({"outcome": {
        "disposition": "completed", "summary": "Stopped partial work summary",
        "remaining": [], "decisions": [], "artifacts": [], "request": None}})) if status == "ok" else None)
    return rc.RunResult(identity=request.identity, harness=request.harness,
        end=rc.RunEnd(status=status, reason_code="user-cancel" if status == "cancelled" else None),
        native_identity=rc.NativeIdentity(session_id=session),
        value=value,
        usage=usage,
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
                        "invocationId": "first-invocation",
                        "executionLiveFile": str(self.root / "execution-live.json"),
                        "live": {"instanceId": "a" * 64, "token": "b" * 64},
                        "inputFile": str(self.root / "turn-input.json"),
                        "outputFile": str(self.root / "turn-output.json"), "timeoutSeconds": None}
        self.identity = request_for({**self.control, "timeoutSeconds": 60}).identity

    def _stop_handle(self):
        return run_execution.InlineRunHandle(
            {**self.control, "timeoutSeconds": 60}, self.identity, grace_seconds=0)

    def test_host_stop_signals_once_and_keeps_original_deadline(self):
        handle = self._stop_handle()
        executor = run_execution.WorkerRunExecutor(SimpleNamespace(
            name="zcode", interrupted_resume=True), SimpleNamespace())
        deadline = time.monotonic() + 300
        controller.worker_stop(executor, handle, stop_initiator="host", grace_deadline=deadline)
        controller.worker_stop(executor, handle, stop_initiator="host", grace_deadline=deadline + 300)
        self.assertEqual(handle.grace_deadline, deadline)
        self.assertTrue(handle.cancelled.is_set())
        self.assertFalse(handle.force_cancelled.is_set())

    def test_lead_stop_uses_the_same_bounded_wrap_up(self):
        handle = self._stop_handle()
        executor = run_execution.WorkerRunExecutor(SimpleNamespace(
            name="zcode", interrupted_resume=True), SimpleNamespace())
        deadline = time.monotonic() + 300
        controller.worker_stop(executor, handle, stop_initiator="lead", grace_deadline=deadline)
        self.assertEqual(handle.stop_initiator, "lead")
        self.assertTrue(handle.wrap_up_started)
        self.assertEqual(handle.grace_deadline, deadline)
        self.assertTrue(handle.cancelled.is_set())
        self.assertEqual(controller.stop_termination_reason(shutdown_confirmed=True,
                         stop_initiator="lead"), "forced-stop")

    def test_console_and_unsupported_harness_force_immediate_cancel(self):
        for initiator, resume in (("console", True), ("host", False)):
            with self.subTest(initiator=initiator, resume=resume):
                handle = self._stop_handle()
                executor = run_execution.WorkerRunExecutor(SimpleNamespace(
                    name="zcode", interrupted_resume=resume), SimpleNamespace())
                executor.cancel = mock.Mock()
                controller.worker_stop(executor, handle, stop_initiator=initiator,
                                       grace_deadline=time.monotonic() + 300)
                executor.cancel.assert_called_once_with(handle)
                self.assertEqual(handle.stop_initiator, initiator)

    def test_missing_host_deadline_forces_stop_and_console_keeps_grace_history(self):
        executor = run_execution.WorkerRunExecutor(SimpleNamespace(
            name="zcode", interrupted_resume=True), SimpleNamespace())
        executor.cancel = mock.Mock()
        handle = self._stop_handle()
        handle.grace_deadline = time.monotonic() + 300
        handle.grace_expired = True
        handle.stop_initiator = "host"
        controller.worker_stop(executor, handle, stop_initiator="console", grace_deadline=None)
        self.assertIsNotNone(handle.grace_deadline)
        self.assertIs(handle.grace_expired, True)
        self.assertEqual(handle.stop_initiator, "console")
        self.assertIs(handle.force_stop_requested, True)
        executor.cancel.assert_called_once_with(handle)
        other = self._stop_handle()
        executor.cancel.reset_mock()
        controller.worker_stop(executor, other, stop_initiator="host", grace_deadline=None)
        self.assertIsNone(other.grace_deadline)
        self.assertIs(other.force_stop_requested, True)
        executor.cancel.assert_called_once_with(other)

    def test_expired_grace_keeps_worker_recovery_time(self):
        handle = self._stop_handle()
        handle.stop_initiator = "host"
        handle.grace_expired = True
        executor = run_execution.WorkerRunExecutor(SimpleNamespace(
            name="zcode", interrupted_resume=True), SimpleNamespace())
        executor.cancel = mock.Mock()
        controller.worker_cancel(executor, handle)
        executor.cancel.assert_called_once_with(handle)
        self.assertFalse(handle.wrap_up_started)

    def test_console_force_preserves_host_grace_history(self):
        handle = self._stop_handle()
        executor = run_execution.WorkerRunExecutor(SimpleNamespace(
            name="zcode", interrupted_resume=True), SimpleNamespace())
        executor.cancel = mock.Mock()
        controller.worker_stop(executor, handle, stop_initiator="host",
                               grace_deadline=time.monotonic() + 300)
        controller.worker_stop(executor, handle, stop_initiator="console", grace_deadline=None)
        self.assertTrue(handle.cancelled.is_set())
        self.assertIsNotNone(handle.grace_deadline)
        self.assertIs(handle.force_stop_requested, True)
        executor.cancel.assert_called_once_with(handle)

    def _execute(self, *, resumable=True, native_gone=True, summary_session="native-session",
                 summary_native_gone=True, summary_status="ok", summary_raises=False,
                 force_before_summary=False, force_during_setup=False, expired=False,
                 late_summary=False, invalid_summary=False, first_usage=None, summary_usage=None):
        control = {**self.control, "timeoutSeconds": 60}
        request = request_for(control)
        deadline = time.monotonic() - 1 if expired else time.monotonic() + 300
        native_clock = time.monotonic
        late_now = [False]
        def clock():
            return deadline + 1 if late_now[0] else native_clock()
        private_json(Path(control["inputFile"]), {"taskId": "task-stop", "attemptId": "attempt-stop",
                     "generation": 1, "turnId": "turn-stop", "resumeMode": "initial"})
        handle = run_execution.InlineRunHandle(control, request.identity, grace_seconds=0)
        handle.wrap_up_started = True
        handle.grace_deadline = deadline
        handle.cancel_requested = True
        cancellation = handle.cancelled
        calls = []
        def run(_module, native_request, *, observer, services, cancelled):
            calls.append((native_request, cancelled()))
            if len(calls) == 1:
                cancellation.set()
                if force_before_summary:
                    handle.force_cancelled.set()
                return result_for(native_request, resumable=resumable, native_gone=native_gone,
                                  usage=first_usage)
            if summary_raises:
                raise OSError("native summary failed after launch")
            if late_summary:
                late_now[0] = True
            return result_for(native_request, status=summary_status, session=summary_session,
                              native_gone=summary_native_gone, usage=summary_usage)
        def prepare(candidate, _module):
            if force_during_setup and candidate["privateRoot"] != str(self.invocation):
                handle.force_cancelled.set()
            return request_for(candidate), None, lambda _fact: rc.FEEDBACK_CONTINUE, None
        module = SimpleNamespace()
        with mock.patch.object(inline_run, "run_seam", return_value=module), \
             mock.patch.object(inline_run, "adapter", return_value=SimpleNamespace(interrupted_resume=True)), \
             mock.patch.object(run_execution, "worker_request", side_effect=prepare), \
             mock.patch.object(inline_run, "_inline_live", side_effect=lambda *args: nullcontext(None)), \
             mock.patch.object(inline_run, "run_harness", side_effect=run), \
             mock.patch.object(inline_run.time, "monotonic", side_effect=clock), \
             mock.patch.object(run_execution, "_worker_result",
                               side_effect=ValueError("invalid summary") if invalid_summary else lambda *args: None):
            result, used_request, verdict, code = inline_run.execute(control, handle, self.root)
        self.last_request = used_request
        self.last_verdict = verdict
        self.last_active_control = handle.role_run_active_control
        return result, code, calls

    def _finished_handle(self, result, code, *, initiator="host", **values):
        handle = run_execution.InlineRunHandle(
            {**self.control, "timeoutSeconds": 60}, self.identity, grace_seconds=0)
        handle.result = result
        handle.request = self.last_request
        handle.verdict = self.last_verdict
        handle.role_run_active_control = self.last_active_control
        handle.role_run_active_identity = self.last_request.identity
        handle.exit_code = code
        handle.wrap_up_started = True
        handle.cancel_requested = True
        handle.stop_initiator = initiator
        for name, value in values.items():
            setattr(handle, name, value)
        handle.done.set()
        return handle

    def test_wrap_up_usage_includes_both_native_turn_increments(self):
        first = {"inputTokens": 1000, "cachedInputTokens": 400, "outputTokens": 500,
                 "reasoningOutputTokens": 200, "nativeRecords": 3, "completeness": "complete"}
        summary = {"inputTokens": 80, "cachedInputTokens": 10, "outputTokens": 221,
                   "reasoningOutputTokens": 40, "nativeRecords": 2, "completeness": "complete"}
        result, code, calls = self._execute(first_usage=first, summary_usage=summary)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.usage.value, {
            "version": 1, "inputTokens": 1080, "cachedInputTokens": 410, "outputTokens": 721,
            "reasoningOutputTokens": 240, "nativeRecords": 5, "completeness": "complete",
            "inputBasis": "includes-cached", "scope": "attempt", "source": "worker/wrap-up-native-turns"})
        self.assertEqual(run_execution._worker_facts(result)["tokenUsage"], result.usage.value)

    def test_one_unknown_native_turn_usage_keeps_known_subtotal_as_partial(self):
        known = {"inputTokens": 1000, "cachedInputTokens": 400, "outputTokens": 500,
                 "nativeRecords": 3, "completeness": "complete"}
        for first, summary in ((known, None), (None, known)):
            with self.subTest(first_known=first is not None):
                sub = ensure_private_dir(self.root / ("first-known" if first else "summary-known"))
                self.invocation = sub
                self.control = {**self.control, "privateRoot": str(sub), "inputFile": str(sub / "turn-input.json"),
                                "outputFile": str(sub / "turn-output.json")}
                result, code, _calls = self._execute(first_usage=first, summary_usage=summary)
                self.assertEqual(code, 0)
                self.assertEqual(result.usage.value["inputTokens"], 1000)
                self.assertEqual(result.usage.value["cachedInputTokens"], 400)
                self.assertEqual(result.usage.value["outputTokens"], 500)
                self.assertEqual(result.usage.value["nativeRecords"], 3)
                self.assertEqual(result.usage.value["completeness"], "partial")

    def test_partial_native_usage_stays_partial_after_adding_summary(self):
        first = {"inputTokens": 1000, "cachedInputTokens": 400, "outputTokens": 500,
                 "completeness": "partial"}
        summary = {"inputTokens": 80, "cachedInputTokens": 10, "outputTokens": 221,
                   "completeness": "complete"}
        result, _code, _calls = self._execute(first_usage=first, summary_usage=summary)
        self.assertEqual(result.usage.value["inputTokens"], 1080)
        self.assertEqual(result.usage.value["outputTokens"], 721)
        self.assertEqual(result.usage.value["completeness"], "partial")

    def test_both_unknown_native_turns_report_no_usage(self):
        result, _code, calls = self._execute()
        self.assertEqual(len(calls), 2)
        self.assertIsNone(result.usage)

    def test_partially_known_usage_counters_do_not_become_zero(self):
        first = {"inputTokens": None, "cachedInputTokens": 400, "outputTokens": 500,
                 "nativeRecords": 3, "completeness": "partial"}
        summary = {"inputTokens": 80, "cachedInputTokens": 10, "outputTokens": 221,
                   "nativeRecords": 2, "completeness": "complete"}
        result, _code, _calls = self._execute(first_usage=first, summary_usage=summary)
        self.assertIsNone(result.usage.value["inputTokens"])
        self.assertEqual(result.usage.value["cachedInputTokens"], 410)
        self.assertEqual(result.usage.value["outputTokens"], 721)
        self.assertEqual(result.usage.value["completeness"], "partial")
        self.assertNotIn("reasoningOutputTokens", result.usage.value)

    def test_unlaunched_wrap_up_does_not_count_first_usage_twice(self):
        known = {"inputTokens": 1000, "cachedInputTokens": 400, "outputTokens": 500,
                 "completeness": "partial"}
        result, _code, calls = self._execute(first_usage=known, resumable=False)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.usage.value["inputTokens"], 1000)
        self.assertEqual(result.usage.value["outputTokens"], 500)
        self.assertEqual(result.usage.value["nativeRecords"], 1)

    def test_failed_summary_without_usage_keeps_interrupted_work_usage(self):
        known = {"inputTokens": 1000, "cachedInputTokens": 400, "outputTokens": 500,
                 "completeness": "partial"}
        result, code, calls = self._execute(first_usage=known, summary_raises=True)
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.stop_evidence.native.group_state, "unknown")
        self.assertEqual(result.usage.value["outputTokens"], 500)
        self.assertEqual(result.usage.value["completeness"], "partial")

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
        self.assertTrue((self.invocation / "stop-summary").is_dir())

    def test_unknown_native_stop_or_unbound_continuation_never_resumes(self):
        for resumable, gone, expired in ((False, True, False), (True, False, False),
                                          (True, True, True)):
            with self.subTest(resumable=resumable, gone=gone, expired=expired):
                # Each execution owns fresh exclusive files.
                sub = ensure_private_dir(self.root / f"case-{resumable}-{gone}-{expired}")
                self.invocation = sub
                self.control.update(privateRoot=str(sub))
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
        self.assertTrue((self.invocation / "stop-summary").is_dir())

    def test_foreign_summary_session_is_rejected_at_collection_boundary(self):
        result, code, calls = self._execute(summary_session="foreign-session")
        self.assertEqual(len(calls), 2)
        self.assertEqual(code, 0)
        self.assertEqual(result.identity, calls[1][0].identity)
        self.assertEqual(result.native_identity.session_id, "foreign-session")
        handle = self._finished_handle(result, code)
        context = SimpleNamespace(turn_input={"resumeMode": "initial"}, spec={"harness": "zcode"},
            effective_workspace=None, turn_output_file=lambda: self.root / "turn-output.json")
        module = SimpleNamespace(session_facts=lambda root, session: {"bindingPresent": True},
                                 validate_turn_provenance=lambda record: None)
        executor = run_execution.WorkerRunExecutor(SimpleNamespace(name="zcode"), module)
        with mock.patch.object(run_execution.turn_io, "read_turn") as read_turn, \
             mock.patch.object(run_execution, "_artifacts", return_value=[]):
            outcome = executor.collect(handle, context)
        read_turn.assert_not_called()
        self.assertEqual(outcome.status, "cancelled")
        self.assertNotIn("turn", outcome.result)
        self.assertNotIn("wrapUpDelivered", outcome.result)

    def test_second_native_unknown_or_failed_result_replaces_first_stop_receipt(self):
        for case in ("unknown", "failed"):
            with self.subTest(case=case):
                sub = ensure_private_dir(self.root / case)
                self.invocation = sub
                self.control.update(privateRoot=str(sub))
                result, code, calls = self._execute(
                    summary_native_gone=case != "unknown",
                    summary_status="error" if case == "failed" else "ok")
                self.assertEqual(result.identity, calls[1][0].identity)
                self.assertEqual(result.stop_evidence.native.group_state,
                                 "unknown" if case == "unknown" else "gone")
                self.assertEqual(result.end.status, "error" if case == "failed" else "ok")
                self.assertEqual(code, 1 if case == "failed" else 0)
                handle = self._finished_handle(result, code)
                self.assertEqual(run_execution._read_run(handle), result)
                self.assertEqual(handle.role_run_active_control["privateRoot"], str(sub / "stop-summary"))
                self.assertIs(handle.verdict["wrapUpDelivered"], False)

    def test_exception_after_second_native_launch_reports_unknown_stop(self):
        result, code, calls = self._execute(summary_raises=True)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.identity, calls[1][0].identity)
        self.assertEqual(result.end.status, "unknown")
        self.assertEqual(result.stop_evidence.native.group_state, "unknown")
        self.assertEqual(code, 1)

    def test_unknown_second_native_stop_cannot_seal_or_claim_wrap_up(self):
        for summary_raises in (False, True):
            with self.subTest(summary_raises=summary_raises):
                sub = ensure_private_dir(self.root / f"collect-{summary_raises}")
                self.invocation = sub
                self.control.update(privateRoot=str(sub))
                result, code, _calls = self._execute(summary_native_gone=False,
                                                     summary_raises=summary_raises)
                handle = self._finished_handle(result, code,
                    grace_deadline=time.monotonic() - 1 if summary_raises else time.monotonic() + 300,
                    grace_expired=summary_raises)
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
                self.assertNotIn("stopReason", outcome.result)
                self.assertNotIn("turn", outcome.result)
                self.assertNotIn("workspaceSeal", outcome.result)
                seal.assert_not_called()
                artifacts.assert_not_called()

    def test_collect_keeps_valid_summary_turn_as_cancelled_partial_work(self):
        result, code, _calls = self._execute()
        self.assertEqual(code, 0)
        handle = self._finished_handle(result, code, grace_deadline=time.monotonic() + 300,
                                       grace_expired=False)
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
        self.assertIs(outcome.result["wrapUpDelivered"], True)
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
                   "executionLiveFile": str(self.root / "execution-live.json"),
                   "timeoutSeconds": None,
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
        executor = run_execution.WorkerRunExecutor(SimpleNamespace(name="codex"), codex_native_run)
        for initiator in ("host", "console"):
            with self.subTest(initiator=initiator):
                context.turn_output_file().unlink(missing_ok=True)
                run_execution._worker_result(result, control, turn_input, request.input_text,
                                             {"sessionId": "session-complete"})
                handle = run_execution.InlineRunHandle(control, identity, grace_seconds=0)
                handle.request = request
                handle.result = result
                handle.verdict = {"stopReason": None, "elapsedMs": 1,
                                  "wrapUpDelivered": False, "graceExpired": False}
                handle.stop_initiator = initiator
                handle.grace_deadline = time.monotonic() + 300 if initiator == "host" else None
                handle.force_stop_requested = True
                handle.cancel_requested = True
                handle.exit_code = 0
                handle.done.set()
                collected = executor.collect(handle, context)
                self.assertEqual(collected.status, "ok")
                self.assertTrue(collected.shutdown_confirmed)
                self.assertEqual(collected.result["turn"]["outcome"], outcome)
                self.assertNotIn("stopReason", collected.result)

    def test_console_escalation_keeps_controller_delivery_fact(self):
        result, code, _calls = self._execute()
        handle = self._finished_handle(result, code, initiator="console",
                                       force_stop_requested=True, grace_deadline=None,
                                       grace_expired=False)
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
        self.assertIs(outcome.result["wrapUpDelivered"], True)

    def test_late_collection_preserves_controller_delivery(self):
        result, _, _ = self._execute()
        handle = self._finished_handle(result, 0, grace_deadline=time.monotonic() - 1,
                                       grace_expired=True)
        context = SimpleNamespace(turn_input={"resumeMode": "initial"}, spec={"harness": "zcode"},
                                  effective_workspace=None, turn_output_file=lambda: self.root / "turn-output.json")
        module = SimpleNamespace(session_facts=lambda root, session: {"bindingPresent": True},
                                 validate_turn_provenance=lambda record: None)
        executor = run_execution.WorkerRunExecutor(SimpleNamespace(name="zcode"), module)
        with mock.patch.object(run_execution.turn_io, "read_turn", return_value=({"sessionId": "native-session"}, None)), \
             mock.patch.object(run_execution, "_artifacts", return_value=[]):
            outcome = executor.collect(handle, context)
        self.assertIs(outcome.result["wrapUpDelivered"], True)
        self.assertIs(outcome.result["graceExpired"], False)
        self.assertIn("turn", outcome.result)

    def test_late_summary_is_not_imported_as_turn(self):
        result, code, _calls = self._execute(late_summary=True)
        self.assertEqual(code, 0)
        verdict = self.last_verdict
        self.assertIs(verdict["wrapUpDelivered"], False)
        self.assertIs(verdict["graceExpired"], True)
        handle = self._finished_handle(result, code)
        context = SimpleNamespace(turn_input={"resumeMode": "initial"}, spec={"harness": "zcode"},
            effective_workspace=None, turn_output_file=lambda: self.root / "turn-output.json")
        module = SimpleNamespace(session_facts=lambda root, session: {"bindingPresent": True},
                                 validate_turn_provenance=lambda record: None)
        executor = run_execution.WorkerRunExecutor(SimpleNamespace(name="zcode"), module)
        with mock.patch.object(run_execution.turn_io, "read_turn") as read_turn, \
             mock.patch.object(run_execution, "_artifacts", return_value=[]):
            outcome = executor.collect(handle, context)
        read_turn.assert_not_called()
        self.assertNotIn("turn", outcome.result)
        self.assertEqual(outcome.status, "cancelled")

    def test_invalid_summary_is_a_confirmed_cancel_without_turn(self):
        result, code, _calls = self._execute(invalid_summary=True)
        self.assertEqual(code, 1)
        verdict = self.last_verdict
        self.assertIs(verdict["wrapUpDelivered"], False)
        self.assertIs(verdict["graceExpired"], False)
        handle = self._finished_handle(result, code)
        context = SimpleNamespace(turn_input={"resumeMode": "initial"}, spec={"harness": "zcode"},
            effective_workspace=None, turn_output_file=lambda: self.root / "turn-output.json")
        module = SimpleNamespace(session_facts=lambda root, session: {"bindingPresent": True},
                                 validate_turn_provenance=lambda record: None)
        executor = run_execution.WorkerRunExecutor(SimpleNamespace(name="zcode"), module)
        with mock.patch.object(run_execution.turn_io, "read_turn") as read_turn, \
             mock.patch.object(run_execution, "_artifacts", return_value=[]):
            outcome = executor.collect(handle, context)
        read_turn.assert_not_called()
        self.assertEqual(outcome.status, "cancelled")
        self.assertNotIn("turn", outcome.result)
        self.assertIs(outcome.result["wrapUpDelivered"], False)
