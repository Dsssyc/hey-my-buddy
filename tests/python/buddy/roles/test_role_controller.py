"""Boundary tests of the shared role controller (ADR-025 step 1-C).

The seam is exercised with a fake ``HarnessRun`` registered in the real registry
table, a recording native adapter behind the Router call point, and a real
Worker attempt against the real ``command`` adapter in the worker invariants
module. Nothing here starts a native harness or a model.
"""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.errors import BoardError
from hey_my_buddy.buddy.harnesses.base import ExecutionContext
from hey_my_buddy.buddy.harnesses.registry import RUN_SEAMS, CommandAdapter, DecisionAdapter, DshAdapter, register_run_seam, run_seam
from hey_my_buddy.buddy.harnesses.run_contract import (
    FrozenJson,
    NetworkPolicy,
    PrivateStatePaths,
    RunBudget,
    RunConfiguration,
    RunEnd,
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
            if not observer(fact):
                self.stopped_at = sequence
                return self._result(request, status="cancelled", interrupt=True)
            if cancelled():
                return self._result(request, status="cancelled", interrupt=False)
        return self._result(request, status="ok")

    def discover(self):  # pragma: no cover - never registered for discovery
        raise AssertionError("discover is out of scope for this fake")

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
        # The capability check is honest and local: run and discover must be
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
            discover = "also not callable"

        with self.assertRaises(BoardError) as caught:
            register_run_seam("zcode", StringShaped())
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(RUN_SEAMS, {"dsh": RUN_SEAMS["dsh"]})

    def test_unextracted_names_have_no_seam(self):
        for name in ("codex", "claude", "zcode", "dsh", "command", "external", "decision"):
            self.assertIsNone(run_seam(name), name)


class RunCallPointTests(unittest.TestCase):
    def setUp(self):
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
        services = controller.WorkerTurnServices(controller.WorkerObservation())
        cancelled = lambda: False  # noqa: E731 - the seam passes the callable through
        result = self.run_fake(module, controller.WorkerObservation().observe, services, cancelled)
        self.assertIs(module.requests[0], self.request)
        self.assertIs(module.services[0], services)
        self.assertIs(module.cancelled[0], cancelled)
        self.assertEqual(result.end.status, "ok")
        self.assertIsNone(result.stop_evidence.interrupt.requested)

    def test_the_call_point_only_runs_the_registered_module_of_the_request_harness(self):
        module = FakeHarnessRun()
        register_run_seam("zcode", module)
        self.addCleanup(RUN_SEAMS.pop, "zcode")
        observer = controller.WorkerObservation().observe
        # An unregistered harness, a look-alike registered under another name,
        # and a direct call with a non-registered module are all refused here,
        # at the one call point, without a second channel existing.
        other_request = replace(self.request, harness="dsh")
        with self.assertRaises(BoardError) as caught:
            controller.run_harness(module, other_request, observer=observer, services=None, cancelled=lambda: False)
        self.assertEqual(caught.exception.code, "ROLE_RUN_UNREGISTERED")
        with self.assertRaises(BoardError) as caught:
            controller.run_harness(FakeHarnessRun(), self.request, observer=observer, services=None,
                                   cancelled=lambda: False)
        self.assertEqual(caught.exception.code, "ROLE_RUN_UNREGISTERED")
        self.assertEqual(module.requests, [])

    def test_worker_facts_never_stop_the_run_and_no_verdict_is_added(self):
        module = FakeHarnessRun(facts=[
            observation("model-start", 0, started=True),
            observation("tool-event", 1, toolCalls=1),
            observation("unknown-events", 2, total=3),
            observation("denied-interaction", 3),
        ])
        state = controller.WorkerObservation()
        result = self.run_fake(module, state.observe, controller.WorkerTurnServices(state))
        self.assertEqual(result.end.status, "ok")
        self.assertIsNone(module.stopped_at)
        self.assertEqual((state.tool_calls, state.unknown_events, state.denied_interactions), (1, 3, 1))
        self.assertIs(state.model_started, True)
        # The seam returns the run's fact package untouched: no field gained a
        # role verdict, and the unknown events stay unknown facts.
        self.assertNotIn("verdict", result.to_payload())
        self.assertEqual(result.identity, self.request.identity)

    def test_fast_facts_stop_at_the_first_tool_or_unknown_fact(self):
        module = FakeHarnessRun(facts=[
            observation("tool-event", 0, toolCalls=1),
            observation("unknown-events", 1, total=1),
            observation("correction", 2, correctionCount=0),
        ])
        state = controller.RouterObservation(mode="fast")
        result = self.run_fake(module, state.observe, None)
        self.assertEqual(module.stopped_at, 0)
        self.assertEqual(state.stopped, "tool-fact")
        self.assertEqual(state.tool_calls, 1)
        self.assertEqual(result.end.status, "cancelled")
        self.assertIs(result.stop_evidence.interrupt.requested, True)
        unknown = controller.RouterObservation(mode="fast")
        module_unknown = FakeHarnessRun(facts=[observation("unknown-events", 0, total=2)])
        self.run_fake(module_unknown, unknown.observe, None)
        self.assertEqual((unknown.stopped, unknown.unknown_events), ("unknown-events", 2))

    def test_cancelled_callback_reaches_the_module(self):
        module = FakeHarnessRun(facts=[observation("model-start", 0, started=True)])
        state = controller.WorkerObservation()
        result = self.run_fake(module, state.observe, None, cancelled=lambda: True)
        self.assertEqual(result.end.status, "cancelled")


class WorkerSeamTests(unittest.TestCase):
    def test_worker_executor_selects_only_through_the_registry(self):
        self.assertIsInstance(controller.worker_executor("command"), CommandAdapter)
        self.assertIsInstance(controller.worker_executor("dsh"), DshAdapter)
        self.assertIsInstance(controller.worker_executor("decision"), DecisionAdapter)

    def test_a_registered_seam_never_falls_through_to_the_legacy_carrier(self):
        register_run_seam("zcode", FakeHarnessRun())
        self.addCleanup(RUN_SEAMS.pop, "zcode")
        with self.assertRaises(BoardError) as caught:
            controller.worker_executor("zcode")
        self.assertEqual(caught.exception.code, "ROLE_RUN_NOT_MIGRATED")
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
        preparation = self.preparation("fast")
        register_run_seam("dsh", FakeHarnessRun())
        self.addCleanup(RUN_SEAMS.pop, "dsh")
        with self.assertRaises(BoardError) as caught:
            controller.start_router_preparation(preparation)
        self.assertEqual(caught.exception.code, "ROLE_RUN_NOT_MIGRATED")

    def test_unknown_preparations_are_refused(self):
        class Stranger:
            harness = "dsh"

        with mock.patch("hey_my_buddy.buddy.harnesses.registry.run_seam", return_value=None), \
                mock.patch("hey_my_buddy.buddy.harnesses.registry.adapter"):
            with self.assertRaises(BoardError) as caught:
                controller.start_router_preparation(Stranger())
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")


class ObservationEnvelopeTests(unittest.TestCase):
    def test_the_envelope_and_fact_set_are_closed(self):
        with self.assertRaises(BoardError):
            controller.read_observation({"fact": "tool-event", "sequence": 0})
        with self.assertRaises(BoardError):
            controller.read_observation({"fact": "tool-event", "sequence": 0, "payload": {"toolCalls": 0},
                                         "extra": 1})
        with self.assertRaises(BoardError) as caught:
            controller.read_observation(observation("made-up", 0))
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError):
            controller.read_observation(observation("tool-event", -1, toolCalls=0))
        with self.assertRaises(BoardError):
            controller.read_observation(observation("tool-event", True, toolCalls=0))
        with self.assertRaises(BoardError):
            controller.read_observation({"fact": "tool-event", "sequence": 0, "payload": None})

    def test_consumed_counts_are_declared_cumulative_and_never_read_as_known_zero(self):
        for kind, payload in (("tool-event", {}), ("tool-event", {"toolCalls": None}),
                              ("tool-event", {"toolCalls": True}), ("correction", {}),
                              ("unknown-events", {}), ("unknown-events", {"total": "3"})):
            with self.subTest(kind=kind, payload=payload):
                with self.assertRaises(BoardError) as caught:
                    controller.read_observation(observation(kind, 0, **payload))
                self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(controller._unknown_total({"total": 4}), 4)

    def test_cumulative_adoption_is_idempotent_and_monotone(self):
        self.assertEqual(controller._cumulative(4, None, "total"), 4)
        self.assertEqual(controller._cumulative(4, 4, "total"), 4)
        with self.assertRaises(BoardError) as caught:
            controller._cumulative(3, 4, "total")
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_worker_observation_ignores_unknown_events_and_counts_denials(self):
        state = controller.WorkerObservation()
        self.assertIs(state.observe(observation("unknown-events", 0, total=7)), True)
        # The same declared cumulative total again is idempotent, not additive.
        self.assertIs(state.observe(observation("unknown-events", 1, total=7)), True)
        self.assertIs(state.observe(observation("denied-interaction", 2)), True)
        self.assertIs(state.observe(observation("correction", 3, correctionCount=1)), True)
        self.assertEqual((state.unknown_events, state.denied_interactions, state.corrections), (7, 1, 1))
        self.assertIsNone(state.model_started)
        self.assertIs(state.observe(observation("model-start", 4, started=False)), True)
        self.assertIs(state.model_started, False)

    def test_router_modes_keep_their_current_differences(self):
        fast = controller.RouterObservation(mode="fast")
        self.assertIs(fast.observe(observation("tool-event", 0, toolCalls=1)), False)
        self.assertEqual((fast.stopped, fast.tool_calls), ("tool-fact", 1))
        unknown = controller.RouterObservation(mode="fast")
        self.assertIs(unknown.observe(observation("unknown-events", 0, total=5)), False)
        self.assertEqual((unknown.stopped, unknown.unknown_events), ("unknown-events", 5))
        review = controller.RouterObservation(mode="review", tool_call_limit=1)
        # Review retains unrecognized events for the fact package and never
        # stops on them; the repeated cumulative total is adopted once.
        self.assertIs(review.observe(observation("unknown-events", 0, total=5)), True)
        self.assertIs(review.observe(observation("unknown-events", 1, total=5)), True)
        self.assertEqual((review.unknown_events, review.stopped), (5, None))
        # One deduplicated call reported as start/end cumulative facts: the
        # repeated cumulative total consumes no budget twice.
        self.assertIs(review.observe(observation("tool-event", 1, toolCalls=1)), True)
        self.assertIs(review.observe(observation("tool-event", 2, toolCalls=1)), True)
        self.assertEqual((review.stopped, review.tool_calls), (None, 1))
        self.assertIs(review.observe(observation("tool-event", 3, toolCalls=2)), False)
        self.assertEqual((review.stopped, review.tool_calls), ("tool-budget", 2))
        with self.assertRaises(BoardError):
            review.observe(observation("tool-event", 4, toolCalls=1))

    def test_router_mode_and_limit_are_validated(self):
        with self.assertRaises(BoardError):
            controller.RouterObservation(mode="quick")
        with self.assertRaises(BoardError):
            controller.RouterObservation(mode="review", tool_call_limit=-1)
        with self.assertRaises(BoardError):
            controller.RouterObservation(mode="review", tool_call_limit=True)


def finish_services(denied=0) -> tuple[controller.WorkerTurnServices, controller.WorkerObservation]:
    state = controller.WorkerObservation(denied_interactions=denied)
    return controller.WorkerTurnServices(state), state


def outcome(**changes) -> dict:
    value = {"disposition": "completed", "summary": "done", "remaining": [], "decisions": [],
             "artifacts": [], "request": None}
    value.update(changes)
    return value


class WorkerTurnServicesTests(unittest.TestCase):
    def test_finish_refusals_keep_the_current_order(self):
        services, _state = finish_services()
        decision = services.evaluate_finish({"disposition": "completed"})
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "invalid-arguments")
        self.assertIn("exactly the current outcome fields", decision.detail)
        decision = services.evaluate_finish(outcome(summary=" "))
        self.assertEqual(decision.reason, "invalid-arguments")

    def test_completed_is_refused_while_attention_is_outstanding(self):
        services, _state = finish_services(denied=1)
        decision = services.evaluate_finish(outcome())
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "attention-outstanding")
        self.assertEqual(decision.pending, ())

    def test_completed_is_refused_by_name_while_host_inquiries_wait(self):
        services, _state = finish_services()
        services.ask("q-1", "first question")
        services.ask("q-2", "second question")
        decision = services.evaluate_finish(outcome())
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "inquiry-pending")
        self.assertEqual([item["inquiryId"] for item in decision.pending], ["q-1", "q-2"])
        services.withdraw("q-2")
        decision = services.evaluate_finish(outcome())
        self.assertFalse(decision.accepted)
        self.assertEqual([item["inquiryId"] for item in decision.pending], ["q-1"])
        services.record_answer("q-1", "the answer")
        self.assertTrue(services.evaluate_finish(outcome()).accepted)

    def test_assistance_and_attention_finishes_accept_with_request_fields(self):
        services, _state = finish_services(denied=2)
        request = {"summary": "blocked", "attempted": "tried", "neededWork": "access",
                   "expectedArtifacts": ["a"], "acceptance": "review"}
        self.assertTrue(services.evaluate_finish(outcome(disposition="attention", request=request)).accepted)
        self.assertTrue(services.evaluate_finish(outcome(disposition="assistance", request=request)).accepted)

    def test_answer_judgments_follow_the_journal_rules(self):
        services, _state = finish_services()
        services.ask("q-1", "question")
        for arguments, reason in (
            ({"inquiryId": "q-1"}, "invalid-arguments"),
            ({"inquiryId": "q-1", "answer": ""}, "invalid-arguments"),
            ({"inquiryId": "q-1", "answer": "x" * 4001}, "invalid-arguments"),
            ({"inquiryId": "missing", "answer": "x"}, "unknown-inquiry"),
        ):
            with self.subTest(reason=reason):
                decision = services.evaluate_answer(arguments)
                self.assertFalse(decision.accepted)
                self.assertEqual(decision.reason, reason)
        services.mark_delivered(["q-1"])
        decision = services.evaluate_answer({"inquiryId": "q-1", "answer": "the answer"})
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.inquiry_id, "q-1")
        self.assertEqual(len(decision.question_sha256), 64)
        self.assertEqual(decision.answer, "the answer")
        services.record_answer("q-1", "the answer")
        decision = services.evaluate_answer({"inquiryId": "q-1", "answer": "another answer"})
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "inquiry-state")
        with self.assertRaises(BoardError) as caught:
            services.record_answer("q-1", "another answer")
        self.assertEqual(caught.exception.code, "CONFLICT")

    def test_withdrawn_questions_can_no_longer_be_answered_or_block(self):
        services, _state = finish_services()
        services.ask("q-1", "question")
        services.withdraw("q-1")
        self.assertEqual(services.pending(), [])
        decision = services.evaluate_answer({"inquiryId": "q-1", "answer": "late"})
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "inquiry-state")

    def test_ask_bounds_and_conflicts_match_the_bridges(self):
        services, _state = finish_services()
        with self.assertRaises(BoardError):
            services.ask("q-1", "")
        with self.assertRaises(BoardError):
            services.ask("q-1", "x" * 4001)
        services.ask("q-1", "question")
        with self.assertRaises(BoardError) as caught:
            services.ask("q-1", "question again")
        self.assertEqual(caught.exception.code, "CONFLICT")
        for index in range(31):
            services.ask(f"q-{index + 2}", "question")
        with self.assertRaises(BoardError):
            services.ask("q-overflow", "one question too many")

    def test_checkpoint_batches_by_the_caller_budget(self):
        services, _state = finish_services()
        for index in range(4):
            services.ask(f"q-{index}", f"question {index} with a stable body")
        batch = services.checkpoint(budget_bytes=10_000)
        self.assertEqual(len(batch.inquiries), 4)
        self.assertEqual(batch.more_pending, 0)
        small = services.checkpoint(budget_bytes=80)
        self.assertLess(len(small.inquiries), 4)
        self.assertEqual(small.more_pending, 4 - len(small.inquiries))
        delivered = services.checkpoint(budget_bytes=80)
        self.assertEqual(len(delivered.inquiries), len(small.inquiries))
        self.assertEqual(services.checkpoint(budget_bytes=80).more_pending,
                         4 - len(small.inquiries))

    def test_pending_view_is_bounded_and_ordered(self):
        services, _state = finish_services()
        services.ask("q-1", "first")
        services.ask("q-2", "second")
        services.record_answer("q-1", "answered")
        pending = services.pending()
        self.assertEqual([item["inquiryId"] for item in pending], ["q-2"])
        self.assertEqual(pending[0]["state"], "queued")
        self.assertIn("questionSha256", pending[0])


class RouterAnswerServicesTests(unittest.TestCase):
    SCHEMA = {"type": "object", "additionalProperties": False,
              "properties": {"profileId": {"type": "string", "enum": ["a", "b"]},
                             "reason": {"type": "string", "minLength": 1}},
              "required": ["profileId", "reason"]}

    def services(self, *, mode="fast", limit=None, max_corrections=1) -> controller.RouterAnswerServices:
        return controller.RouterAnswerServices(
            schema=self.SCHEMA, max_corrections=max_corrections,
            observation=controller.RouterObservation(mode=mode, tool_call_limit=limit))

    def test_check_keeps_the_current_schema_subset_and_codes(self):
        services = self.services()
        self.assertEqual(services.check('{"profileId": "a", "reason": "fits"}'),
                         controller.AnswerCheck(True))
        shaped = services.check('{"profileId": "a"}')
        self.assertFalse(shaped.valid)
        self.assertEqual(shaped.correction, "answer-shape")
        self.assertIn("required", shaped.errors)
        json_broken = services.check("{not json")
        self.assertEqual(json_broken.correction, "answer-invalid-json")
        outside = services.check('{"profileId": "foreign", "reason": "x"}')
        self.assertFalse(outside.valid)
        self.assertIsNone(outside.correction)  # enum: outside the candidates, never retried

    def test_an_unsupported_schema_is_a_caller_bug_not_a_value_fact(self):
        services = controller.RouterAnswerServices(
            schema={"type": "object", "format": "date"},
            observation=controller.RouterObservation(mode="fast"))
        with self.assertRaises(ValueError):
            services.check("{}")

    def test_correction_budget_and_observations_feed_each_other(self):
        services = self.services(max_corrections=1)
        self.assertTrue(services.allows_correction())
        services.observation.observe(observation("correction", 0, correctionCount=1))
        self.assertFalse(services.allows_correction())
        # The same cumulative correction count again consumes no correction.
        services.observation.observe(observation("correction", 1, correctionCount=1))
        self.assertEqual(services.observation.corrections, 1)
        never = self.services(max_corrections=0)
        self.assertFalse(never.allows_correction())
        reviewed = self.services(mode="review", limit=1)
        reviewed.observation.observe(observation("tool-event", 0, toolCalls=1))
        # The deduplicated start/end pair arrives as the same cumulative total.
        reviewed.observation.observe(observation("tool-event", 1, toolCalls=1))
        self.assertEqual(reviewed.budget_facts(), {"toolCalls": 1, "toolCallLimit": 1, "corrections": 0})
        self.assertTrue(reviewed.observation.observe(observation("tool-event", 2, toolCalls=2)) is False)
        self.assertEqual(reviewed.budget_facts(), {"toolCalls": 2, "toolCallLimit": 1, "corrections": 0})

    def test_check_reads_values_without_correction_consumption(self):
        services = self.services()
        services.check('{"profileId": "a", "reason": "x"}')
        self.assertEqual(services.observation.corrections, 0)
        services.check('{"profileId": "a"}')
        self.assertEqual(services.observation.corrections, 0)


if __name__ == "__main__":
    unittest.main()
