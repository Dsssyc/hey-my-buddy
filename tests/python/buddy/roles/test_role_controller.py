"""Boundary tests of the shared role controller.

The seam is exercised with a fake ``HarnessRun``, a recording native adapter
behind the Router call point, and a real
Worker attempt against the real ``command`` adapter in the worker invariants
module. Nothing here starts a native harness or a model.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from hey_my_buddy.errors import BoardError
from hey_my_buddy.buddy.harnesses.base import Adapter, ExecutionContext
from hey_my_buddy.buddy.harnesses.registry import RUN_SEAMS, CommandAdapter, DecisionAdapter, run_seam
from hey_my_buddy.buddy.harnesses.run_contract import HARNESS_NAMES
from hey_my_buddy.buddy.harnesses.run_contract import (
    FEEDBACK_CONTINUE,
    FEEDBACK_STOP,
    FrozenJson,
    PrivateStatePaths,
    RunBudget,
    RunBuddy,
    RunEnd,
    RunFeedback,
    RunIdentity,
    RunRequest,
    RunResult,
    InterruptEvidence,
    StopEvidence,
)
from hey_my_buddy.buddy.roles import controller
from hey_my_buddy.buddy.roles.router import prepare_router_fast, prepare_router_review


def observation(fact: str, sequence: int, **payload) -> dict:
    return {"fact": fact, "sequence": sequence, "payload": payload}


class FakeHarnessRun:
    supported_request_controls = ()
    prepare_run_services = None
    native_evidence = None
    run_delete_sessions = None

    """A run-module double: records the seam arguments, then drives the observer."""

    def __init__(self, facts=()):
        self.facts = list(facts)
        self.requests = []
        self.observers = []
        self.services = []
        self.cancelled = []
        self.stopped_at = None

    def run(self, request, *, observer, services, cancelled):
        self.requests.append(request)
        self.observers.append(observer)
        self.services.append(services)
        self.cancelled.append(cancelled)
        for sequence, fact in enumerate(self.facts):
            feedback = observer(fact)
            if feedback.action == "stop":
                self.stopped_at = sequence
                return self._result(request, status="cancelled", interrupt=True)
            if cancelled():
                return self._result(request, status="cancelled", interrupt=False)
        return self._result(request, status="ok")

    def run_discovery(self, **kwargs):
        return {"providers": []}


    @staticmethod
    def _result(request, *, status, interrupt=False):
        return RunResult(
            identity=request.identity, harness=request.harness,
            end=RunEnd(status=status, reason_code=None if status == "ok" else "observer-interrupt"),

            stop_evidence=StopEvidence(interrupt=InterruptEvidence(requested=interrupt or None)),
        )


def run_request(root: Path) -> RunRequest:
    return RunRequest(
        identity=RunIdentity(task_id="task-roles", attempt_id="attempt-roles", generation=0,
                             invocation_id="invocation-1"),
        harness="zcode",
        buddy=RunBuddy(provider="provider", model="model", effort="off"),
        cwd=str(root),
        session_record_path=str(root / "session.jsonl"),
        private_state=PrivateStatePaths(state_dir=str(root), invocation_root=str(root / "invocation"),
                                        native_root=str(root / "native")),
        input_text="one bounded input",
        permission="none",
        output_schema=FrozenJson({"type": "object", "additionalProperties": False}),
        budget=RunBudget(timeout_seconds=60),
    )


class RunSeamRegistryTests(unittest.TestCase):
    def test_the_fixed_map_covers_every_native_harness(self):
        self.assertEqual(set(RUN_SEAMS), set(HARNESS_NAMES))
        for name in HARNESS_NAMES:
            with self.subTest(harness=name):
                self.assertIs(run_seam(name), RUN_SEAMS[name])

    def test_non_harness_names_are_not_run_seams(self):
        for name in ("command", "decision", "external"):
            with self.subTest(name=name), self.assertRaises(KeyError):
                run_seam(name)


class RunCallPointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-roles-seam-",
                                                dir=os.environ.get("HMB_CHECKS_TMPDIR", tempfile.gettempdir()))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.request = run_request(self.root)

    def run_fake(self, module, observer, services, cancelled=lambda: False):
        return controller.run_harness(module, self.request, observer=observer, services=services,
                                      cancelled=cancelled)

    def test_the_registered_module_receives_the_role_held_seam_values(self):
        module = FakeHarnessRun()
        services = object()
        cancelled = lambda: False  # noqa: E731 - the seam passes the callable through
        result = self.run_fake(module, lambda fact: FEEDBACK_CONTINUE, services, cancelled)
        self.assertIs(module.requests[0], self.request)
        self.assertIs(module.services[0], services)
        self.assertIs(module.cancelled[0], cancelled)
        self.assertEqual(result.end.status, "ok")
        self.assertIsNone(result.stop_evidence.interrupt.requested)

    def test_the_call_point_checks_the_request_permission(self):
        module = FakeHarnessRun()
        observer = lambda fact: FEEDBACK_CONTINUE
        with mock.patch.dict(controller.PERMISSION_LEVELS, {"zcode": {}}), self.assertRaises(BoardError) as caught:
            controller.run_harness(module, self.request, observer=observer, services=None, cancelled=lambda: False)
        self.assertEqual(caught.exception.code, "UNSUPPORTED_CAPABILITY")
        self.assertEqual(module.requests, [])


    def test_cancelled_callback_reaches_the_module(self):
        module = FakeHarnessRun(facts=[observation("model-start", 0, started=True)])
        result = self.run_fake(module, lambda fact: FEEDBACK_CONTINUE, None, cancelled=lambda: True)
        self.assertEqual(result.end.status, "cancelled")

    def test_resume_checkpoint_requires_module_support(self):
        self.request = RunRequest.from_payload(run_request(self.root).to_payload() | {
            "continuation": {"mode": "native-session", "previousSessionId": "previous",
                             "checkpoint": {"nativeTurnId": "native-turn", "attemptId": "previous-attempt",
                                            "inputSha256": "a" * 64}}})
        module = FakeHarnessRun()
        with self.assertRaises(BoardError) as caught:
            self.run_fake(module, lambda fact: FEEDBACK_CONTINUE, None)
        self.assertEqual(caught.exception.code, "ROLE_RUN_UNSUPPORTED_CONTROL")
        self.assertEqual(caught.exception.details["controls"], ["resume_checkpoint"])
        self.assertEqual(module.requests, [])
        module.supported_request_controls = ("resume_checkpoint",)
        self.run_fake(module, lambda fact: FEEDBACK_CONTINUE, None)
        self.assertIs(module.requests[0], self.request)


class WorkerSeamTests(unittest.TestCase):
    def test_worker_executor_selects_the_native_and_builtin_executors(self):
        self.assertIsInstance(controller.worker_executor("command"), CommandAdapter)
        self.assertIs(controller.worker_executor("dsh").module, run_seam("dsh"))
        self.assertIsInstance(controller.worker_executor("decision"), DecisionAdapter)

    def test_native_harness_uses_the_role_executor(self):
        executor = controller.worker_executor("zcode")
        self.assertIs(executor.module, run_seam("zcode"))
        self.assertEqual(executor.name, "zcode")
        self.assertFalse(hasattr(executor.description, "start"))
        self.assertIsInstance(controller.worker_executor("command"), CommandAdapter)

    def test_prepare_worker_run_reports_availability_and_prepare_refusals(self):
        class Executor:
            name = "fake"

            def __init__(self, available, error=None):
                self._available = available
                self._error = error
                self.prepared = 0

            def available(self):
                return self._available

            def prepare(self, context):
                self.prepared += 1
                if self._error is not None:
                    raise self._error

        unavailable = Executor((False, "not installed"))
        preparation = controller.prepare_worker_run(unavailable, None)
        self.assertFalse(preparation.available)
        self.assertEqual(preparation.reason, "not installed")
        self.assertIsNone(preparation.error)
        self.assertEqual(unavailable.prepared, 0)
        refusal = BoardError("HARNESS_UNAVAILABLE", "no native binary")
        failing = Executor((True, None), error=refusal)
        preparation = controller.prepare_worker_run(failing, None)
        self.assertTrue(preparation.available)
        self.assertIs(preparation.error, refusal)
        prepared = Executor((True, None))
        preparation = controller.prepare_worker_run(prepared, None)
        self.assertTrue(preparation.available)
        self.assertIsNone(preparation.error)
        self.assertEqual(prepared.prepared, 1)


class RouterCallPointTests(unittest.TestCase):
    def test_adapter_prepares_before_start_and_preserves_full_native_budget(self):
        from types import SimpleNamespace
        from hey_my_buddy.buddy.roles import router as calls

        document = {"buddy": {"harness": "dsh"}, "routingMode": "review",
                    "budget": {"timeoutSeconds": 60, "toolCalls": 2},
                    "outputSchema": {"type": "object"}, "task": "choose", "buddies": []}
        context = self.context(decision_input=document)
        frozen = self.root / "frozen"
        frozen.mkdir()
        handle = SimpleNamespace()
        adapter = calls.DecisionAdapter()
        with mock.patch("hey_my_buddy.buddy.harnesses.registry.adapter", return_value=self.Native()), \
             mock.patch.object(calls.router_input, "prepare", return_value=(frozen, "digest")), \
             mock.patch.object(controller, "start_router_preparation", return_value=handle) as start:
            adapter.prepare(context)
            start.assert_not_called()
            self.assertIs(adapter.start(context), handle)
        preparation = start.call_args.args[0]
        self.assertEqual(preparation.request.budget, document["budget"])
        self.assertEqual(handle.router_input, (None, frozen, "digest"))

    def test_router_usage_keeps_the_native_reported_elapsed_time(self):
        from types import SimpleNamespace
        from hey_my_buddy.buddy.harnesses.base import AdapterOutcome
        from hey_my_buddy.buddy.roles import router as calls

        context = self.context(decision_input={"buddy": {}, "routingMode": "review",
                                               "budget": {"timeoutSeconds": 60}, "buddies": []})
        native = AdapterOutcome(status="failed", result={"code": "deadline", "usage": {"elapsedMs": 200}},
                                shutdown_confirmed=False)
        with mock.patch.object(calls.read_only, "collect", return_value=native):
            outcome = calls.DecisionAdapter().collect(SimpleNamespace(), context)
        self.assertEqual(outcome.result["usage"]["elapsedMs"], 200)

    def test_unknown_input_check_reuses_native_result_on_retry(self):
        from types import SimpleNamespace
        from hey_my_buddy.buddy.harnesses.base import AdapterOutcome
        from hey_my_buddy.buddy.roles import router as calls

        context = self.context(decision_input={"buddy": {}, "routingMode": "review",
                                               "budget": {"timeoutSeconds": 60},
                                               "buddies": [{"buddyId": "chosen"}]})
        handle = SimpleNamespace(router_input=(None, self.root, "digest"))
        native = AdapterOutcome(status="ok", result={"rawAnswer": "native answer", "usage": {"elapsedMs": 200}},
                                shutdown_confirmed=True)
        unknown = {"unchanged": None, "code": "router-input-unknown", "manifestSha256": None}
        verified = {"unchanged": True, "manifestSha256": None, "snapshotSha256": "digest"}
        adapter = calls.DecisionAdapter()
        with mock.patch.object(calls.read_only, "collect", return_value=native) as collect, \
             mock.patch.object(calls.router_input, "verify", side_effect=[unknown, verified]) as verify, \
             mock.patch.object(calls.router_input, "discard") as discard, \
             mock.patch.object(calls.router, "validate_answer", return_value={"buddyId": "chosen"}) as answer:
            with self.assertRaises(BoardError) as waiting:
                adapter.collect(handle, context)
            self.assertEqual(waiting.exception.code, "router-input-unknown")
            self.assertEqual(native.result["rawAnswer"], "native answer")
            answer.assert_not_called()
            discard.assert_not_called()
            result = adapter.collect(handle, context)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.result["decision"]["buddyId"], "chosen")
        self.assertEqual(result.result["usage"]["elapsedMs"], 200)
        collect.assert_called_once_with(handle)
        self.assertEqual(verify.call_count, 2)
        discard.assert_called_once_with(self.root)

    def test_failed_review_preparation_discards_its_mirror_before_start(self):
        from hey_my_buddy.buddy.roles import router as calls

        document = {"buddy": {"harness": "dsh"}, "routingMode": "review",
                    "budget": {"timeoutSeconds": 60}, "outputSchema": {"type": "object"}}
        frozen = self.root / "failed-preparation"
        frozen.mkdir()
        with mock.patch("hey_my_buddy.buddy.harnesses.registry.adapter", return_value=self.Native()), \
             mock.patch.object(calls.router_input, "prepare", return_value=(frozen, "digest")), \
             mock.patch.object(calls.router, "render_prompt", side_effect=BoardError("INVALID_ARGUMENT", "bad prompt")), \
             mock.patch.object(controller, "start_router_preparation") as start:
            with self.assertRaises(BoardError):
                calls.DecisionAdapter().prepare(self.context(decision_input=document))
        self.assertFalse(frozen.exists())
        start.assert_not_called()

    def setUp(self):
        self.module = FakeHarnessRun()
        registrations = mock.patch.dict(RUN_SEAMS, {"dsh": self.module})
        registrations.start()
        self.addCleanup(registrations.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-roles-router-",
                                                dir=os.environ.get("HMB_CHECKS_TMPDIR", tempfile.gettempdir()))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    class Native:
        """A recording stand-in for the selected carrier; no real harness."""

        name = "dsh"


    def context(self, **kwargs):
        values = dict(task_id="task-router", attempt_id="attempt-router", generation=1,
                      spec={"cwd": str(self.root), "task": "choose", "routerBudgetSeconds": 60},
                      directory=self.root / "attempt",
                      environment={"HMB_STATE_DIR": str(self.root / "state")}, runtime={},
                      session_record_path=str(self.root / "session.jsonl"))
        values.update(kwargs)
        return ExecutionContext(**values)

    def preparation(self, kind, native=None):
        document = {"routingMode": "fast" if kind == "fast" else "review", "task": "choose", "buddies": [],
                    "outputSchema": {"type": "object"}, "budget": {"timeoutSeconds": 60}}
        native = native if native is not None else self.Native()
        context = self.context()
        if kind == "fast":
            return prepare_router_fast(document, {}, native, context)
        return prepare_router_review(document, {}, native, context)

    def start(self, preparation):
        fast = isinstance(preparation, controller.FastPreparation)
        path = "hey_my_buddy.buddy.roles.run_execution." + ("start_fast" if fast else "start_review")
        with mock.patch(path, return_value="started-handle") as start, \
                mock.patch("hey_my_buddy.buddy.harnesses.registry.adapter", side_effect=AssertionError("reselected")):
            result = controller.start_router_preparation(preparation)
        self.start_call = start.call_args.args
        return result

    def test_fast_preparation_starts_the_selected_instance_without_re_resolution(self):
        native = self.Native()
        preparation = self.preparation("fast", native)
        self.assertIs(preparation.run_module, self.module)
        self.assertIsNone(preparation.context.turn)
        self.assertIsNone(preparation.context.agent_credential)
        self.assertEqual(preparation.request.cwd, str(preparation.no_tool_cwd))
        self.assertEqual(preparation.request.timeout_seconds, 60)
        self.assertEqual(self.start(preparation), "started-handle")
        module, harness, context, request = self.start_call
        self.assertIs(module, self.module)
        self.assertEqual(harness, "dsh")
        self.assertIs(context, preparation.context)
        self.assertIs(request, preparation.request)

    def test_review_preparation_keeps_the_frozen_mirror_and_the_selected_instance(self):
        from hey_my_buddy.buddy.roles import router_input

        native = self.Native()
        preparation = self.preparation("review", native)
        self.assertIs(preparation.native, native)
        manifest, root, digest = preparation.mirror
        self.assertIsNone(manifest)
        self.assertTrue(root.is_dir())
        self.assertEqual(len(digest), 64)
        self.assertTrue(router_input.verify(manifest, root, digest, deadline=time.monotonic() + 120)["unchanged"])
        self.assertEqual(self.start(preparation), "started-handle")
        harness, _context, request = self.start_call
        self.assertEqual(harness, "dsh")
        self.assertIs(request, preparation.request)

    def test_router_starts_the_prepared_native_run(self):
        preparation = self.preparation("fast")
        with mock.patch("hey_my_buddy.buddy.roles.run_execution.start_fast", return_value="run-handle") as start:
            self.assertEqual(controller.start_router_preparation(preparation), "run-handle")
        start.assert_called_once_with(run_seam("dsh"), "dsh", preparation.context, preparation.request)
        self.assertFalse(hasattr(controller.FastPreparation, "native"))
        self.assertFalse(hasattr(Adapter, "start_no_tool_structured"))
        self.assertFalse(hasattr(Adapter, "start_read_only_structured"))

    def test_router_uses_its_frozen_preparation_after_registry_change(self):
        preparation = self.preparation("fast")
        with mock.patch.dict(RUN_SEAMS, {"dsh": FakeHarnessRun()}), \
                mock.patch("hey_my_buddy.buddy.roles.run_execution.start_fast", return_value="run-handle") as start:
            self.assertEqual(controller.start_router_preparation(preparation), "run-handle")
        start.assert_called_once_with(self.module, "dsh", preparation.context, preparation.request)



if __name__ == "__main__":
    unittest.main()
