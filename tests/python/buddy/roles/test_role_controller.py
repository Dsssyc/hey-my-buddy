"""Boundary tests of the shared role controller (ADR-025 step 1-C).

The seam is exercised with a fake ``HarnessRun`` registered in the real registry
table, a recording native adapter behind the Router call point, and a real
Worker attempt against the real ``command`` adapter in the worker invariants
module. Nothing here starts a native harness or a model.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.errors import BoardError
from hey_my_buddy.buddy.harnesses.base import ExecutionContext
from hey_my_buddy.buddy.harnesses.registry import RUN_SEAMS, CommandAdapter, DecisionAdapter, DshAdapter, register_run_seam, run_seam
from hey_my_buddy.buddy.harnesses.run_contract import (
    decode_run_request,
    FEEDBACK_CONTINUE,
    FEEDBACK_STOP,
    FrozenJson,
    NetworkPolicy,
    PrivateStatePaths,
    RunBudget,
    RunConfiguration,
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
            unknown_events=None,
            stop_evidence=StopEvidence(interrupt=InterruptEvidence(requested=interrupt or None)),
        )


def run_request(root: Path) -> RunRequest:
    return RunRequest(
        identity=RunIdentity(task_id="task-roles", attempt_id="attempt-roles", generation=0,
                             invocation_id="invocation-1"),
        harness="zcode",
        configuration=RunConfiguration(provider="provider", model="model", effort="off"),
        cwd=str(root),
        private_state=PrivateStatePaths(invocation_root=str(root / "invocation"),
                                        native_root=str(root / "native")),
        input_text="one bounded input",
        tool_scope="none",
        network=NetworkPolicy(requested=False),
        output_schema=FrozenJson({"type": "object", "additionalProperties": False}),
        budget=RunBudget(timeout_seconds=60, max_output_bytes=4096),
    )


class RunSeamRegistrationTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(RUN_SEAMS, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_registered_harness_run_is_the_only_seam_entry(self):
        module = FakeHarnessRun()
        register_run_seam("zcode", module)
        self.addCleanup(RUN_SEAMS.pop, "zcode")
        self.assertIs(run_seam("zcode"), module)
        self.assertIn("zcode", RUN_SEAMS)

    def test_registration_refuses_non_harness_names_duplicates_and_foreign_objects(self):
        with self.assertRaises(BoardError) as caught:
            register_run_seam("command", FakeHarnessRun())
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as caught:
            register_run_seam("external", FakeHarnessRun())
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        register_run_seam("dsh", FakeHarnessRun())
        self.addCleanup(RUN_SEAMS.pop, "dsh")
        with self.assertRaises(BoardError) as caught:
            register_run_seam("dsh", FakeHarnessRun())
        self.assertEqual(caught.exception.code, "CONFLICT")
        # The capability check is honest and local: run must be
        # callable, and nothing about the registration proves more than that.
        # A command adapter, a bare object and a string-shaped stand-in with
        # the right attribute names are all refused.
        with self.assertRaises(BoardError) as caught:
            register_run_seam("claude", CommandAdapter())
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as caught:
            register_run_seam("codex", object())
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

        class StringShaped:
            run = "not callable"

        with self.assertRaises(BoardError) as caught:
            register_run_seam("zcode", StringShaped())
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(RUN_SEAMS, {"dsh": RUN_SEAMS["dsh"]})

    def test_unextracted_names_have_no_seam(self):
        for name in ("codex", "claude", "zcode", "dsh", "command", "external", "decision"):
            self.assertIsNone(run_seam(name), name)


class RunCallPointTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(RUN_SEAMS, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-roles-seam-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.request = run_request(self.root)

    def run_fake(self, module, observer, services, cancelled=lambda: False):
        RUN_SEAMS.pop("zcode", None)
        register_run_seam("zcode", module)
        self.addCleanup(RUN_SEAMS.pop, "zcode", None)
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

    def test_the_call_point_only_runs_the_registered_module_of_the_request_harness(self):
        module = FakeHarnessRun()
        register_run_seam("zcode", module)
        self.addCleanup(RUN_SEAMS.pop, "zcode")
        observer = lambda fact: FEEDBACK_CONTINUE
        # An unregistered harness, a look-alike registered under another name,
        # and a direct call with a non-registered module are all refused here,
        # at the one call point, without a second channel existing.
        other_request = decode_run_request(self.request.to_payload() | {"harness": "dsh"})
        with self.assertRaises(BoardError) as caught:
            controller.run_harness(module, other_request, observer=observer, services=None, cancelled=lambda: False)
        self.assertEqual(caught.exception.code, "ROLE_RUN_UNREGISTERED")
        with self.assertRaises(BoardError) as caught:
            controller.run_harness(FakeHarnessRun(), self.request, observer=observer, services=None,
                                   cancelled=lambda: False)
        self.assertEqual(caught.exception.code, "ROLE_RUN_UNREGISTERED")
        self.assertEqual(module.requests, [])


    def test_cancelled_callback_reaches_the_module(self):
        module = FakeHarnessRun(facts=[observation("model-start", 0, started=True)])
        result = self.run_fake(module, lambda fact: FEEDBACK_CONTINUE, None, cancelled=lambda: True)
        self.assertEqual(result.end.status, "cancelled")


class WorkerSeamTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(RUN_SEAMS, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_worker_executor_selects_only_through_the_registry(self):
        self.assertIsInstance(controller.worker_executor("command"), CommandAdapter)
        self.assertIsInstance(controller.worker_executor("dsh"), DshAdapter)
        self.assertIsInstance(controller.worker_executor("decision"), DecisionAdapter)

    def test_a_registered_seam_never_falls_through_to_the_legacy_carrier(self):
        register_run_seam("zcode", FakeHarnessRun())
        self.addCleanup(RUN_SEAMS.pop, "zcode")
        executor = controller.worker_executor("zcode")
        self.assertIs(executor.module, run_seam("zcode"))
        self.assertEqual(executor.name, "zcode")
        self.assertFalse(hasattr(executor.description, "start"))
        # Unregistered harnesses and command keep their legacy carrier.
        self.assertIsInstance(controller.worker_executor("dsh"), DshAdapter)
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
        refusal = BoardError("ADAPTER_UNAVAILABLE", "no native binary")
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
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-roles-router-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    class Native:
        """A recording stand-in for the selected carrier; no real harness."""

        name = "dsh"
        no_tool_structured = True

        def __init__(self):
            self.calls = []

        def local_read_only_check(self):
            return {"eligible": True, "reasonCode": None, "reason": None,
                    "systemSandbox": False, "sameAttemptContinuation": False}

        def start_no_tool_structured(self, context, request):
            self.calls.append(("fast", context, request))
            return "started-handle"

        def start_read_only_structured(self, context, request):
            self.calls.append(("review", context, request))
            return "started-handle"

    def context(self, **kwargs):
        values = dict(task_id="task-router", attempt_id="attempt-router", generation=1,
                      spec={"cwd": str(self.root), "task": "choose", "timeoutSeconds": 60},
                      directory=self.root / "attempt",
                      environment={"BUDDY_STATE_DIR": str(self.root / "state")}, runtime={})
        values.update(kwargs)
        return ExecutionContext(**values)

    def preparation(self, kind, native=None):
        document = {"routingMode": "fast" if kind == "fast" else "review", "task": "choose", "profiles": [],
                    "outputSchema": {"type": "object"}, "budget": {"timeoutSeconds": 60}}
        native = native if native is not None else self.Native()
        context = self.context()
        if kind == "fast":
            return prepare_router_fast(document, {}, native, context)
        return prepare_router_review(document, {}, native, context)

    def start(self, preparation):
        with mock.patch("hey_my_buddy.buddy.harnesses.registry.run_seam", return_value=None):
            return controller.start_router_preparation(preparation)

    def test_fast_preparation_starts_the_selected_instance_without_re_resolution(self):
        native = self.Native()
        preparation = self.preparation("fast", native)
        self.assertIs(preparation.native, native)
        self.assertIsNone(preparation.context.turn)
        self.assertIsNone(preparation.context.agent_credential)
        self.assertEqual(preparation.request.cwd, str(preparation.no_tool_cwd))
        self.assertEqual(preparation.request.timeout_seconds, 60)
        self.assertEqual(self.start(preparation), "started-handle")
        mode, context, request = native.calls[0]
        self.assertEqual(mode, "fast")
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
        self.assertTrue(router_input.verify(manifest, root, digest)["unchanged"])
        self.assertEqual(self.start(preparation), "started-handle")
        mode, _context, request = native.calls[0]
        self.assertEqual(mode, "review")
        self.assertIs(request, preparation.request)

    def test_a_registered_seam_refuses_the_legacy_router_entry(self):
        register_run_seam("dsh", FakeHarnessRun())
        self.addCleanup(RUN_SEAMS.pop, "dsh")
        preparation = self.preparation("fast")
        with mock.patch("hey_my_buddy.buddy.roles.run_execution.start_fast", return_value="run-handle") as start:
            self.assertEqual(controller.start_router_preparation(preparation), "run-handle")
        start.assert_called_once_with(run_seam("dsh"), "dsh", preparation.context, preparation.request)
        self.assertEqual(preparation.native.calls, [])

    def test_unknown_preparations_are_refused(self):
        class Stranger:
            harness = "dsh"

        with mock.patch("hey_my_buddy.buddy.harnesses.registry.run_seam", return_value=None), \
                mock.patch("hey_my_buddy.buddy.harnesses.registry.adapter"):
            with self.assertRaises(BoardError) as caught:
                controller.start_router_preparation(Stranger())
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")


if __name__ == "__main__":
    unittest.main()
