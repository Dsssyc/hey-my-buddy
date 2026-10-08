"""Activity projection regressions: whitelist, binding, monotonicity, heartbeat truth.

Every test uses a private state directory and a private command child. Inherited
Buddy runtime, worker and credential variables are removed before any test
subprocess starts, as AGENTS.md requires.
"""
from __future__ import annotations

import importlib.util
import json
import os
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

from support import BoardTestCase

from hey_my_buddy.protocol import activity as activity_module
from hey_my_buddy.protocol.activity import ActivityPublisher, is_newer, normalize_activity
from hey_my_buddy.buddy.harnesses import c_two_live as ctl
from hey_my_buddy.buddy.harnesses.live import LiveCapabilities
from hey_my_buddy.buddy.harnesses.run_contract import RunIdentity
from hey_my_buddy.buddy.roles import live as role_live
from hey_my_buddy.buddy.harnesses.registry import ExecutionContext, adapter as get_adapter
from hey_my_buddy.errors import BoardError
from hey_my_buddy.buddy.runtime.worker import Worker, _Renewal

#: Inherited variables that would otherwise point a test subprocess at a production
#: runtime, a Worker identity or an agent credential instead of this private root.
SANITIZED_VARIABLES = (
    "BUDDY_STATE_DIR",
    "BUDDY_RUNTIME_ROOT",
    "BUDDY_RUNTIME",
    "BUDDY_RUNTIME_IDENTITY",
    "BUDDY_WORKER_STATE",
    "BUDDY_WORKER_ID",
    "BUDDY_TASK_ID",
    "BUDDY_ATTEMPT_ID",
    "BUDDY_AGENT_CREDENTIAL",
    "BUDDY_AGENT_CREDENTIAL_FILE",
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
)

SOURCE_ROOT = Path(__file__).resolve().parents[3] / "src"
TEST_ROOT = Path(__file__).resolve().parent


def private_environment(directory: Path, **extra: str) -> dict:
    values = {key: value for key, value in os.environ.items() if key not in SANITIZED_VARIABLES}
    values.update(
        {
            "BUDDY_STATE_DIR": str(directory),
            "BUDDY_RUNTIME_ROOT": str(directory / "runtime-root"),
            "BUDDY_DEV_SOURCE": "1",
            "VIRTUAL_ENV": "",
            "PYTHONPATH": os.pathsep.join([str(SOURCE_ROOT), str(TEST_ROOT)]),
            **extra,
        }
    )
    return values


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class ActivityValidation(BoardTestCase):
    def test_the_whitelist_accepts_the_bounded_projection(self):
        payload = {
            "phase": "tool-running",
            "observedAt": "2026-01-01T00:00:01.000Z",
            "eventSeq": 7,
            "nativeSessionId": "session-1",
            "lastNativeActivityAt": "2026-01-01T00:00:01.000Z",
            "lastToolActivityAt": "2026-01-01T00:00:00.900Z",
            "toolName": "read_file",
            "waitingReason": "waiting for the tool to return",
            "counts": {"modelTurns": 2, "toolCalls": 3},
        }
        self.assertEqual(normalize_activity(payload), payload)
        # An explicit null means "unknown" and is omitted, never invented as a zero.
        self.assertEqual(
            normalize_activity({"phase": "unknown", "observedAt": payload["observedAt"], "eventSeq": None, "counts": {"toolCalls": None}}),
            {"phase": "unknown", "observedAt": payload["observedAt"]},
        )
        # An event sequence alone is a sufficient ordering key.
        self.assertEqual(
            normalize_activity({"phase": "starting", "eventSeq": 1}),
            {"phase": "starting", "eventSeq": 1},
        )

    def test_invalid_activity_is_rejected_instead_of_stored(self):
        base = {"phase": "starting", "observedAt": "2026-01-01T00:00:00.000Z"}
        cases = {
            "unknown field": {**base, "text": "model output"},
            "unknown count": {**base, "counts": {"tokens": 10}},
            "unknown phase": {"phase": "thinking", "observedAt": base["observedAt"]},
            "missing phase": {"observedAt": base["observedAt"]},
            "missing ordering": {"phase": "starting"},
            "negative sequence": {**base, "eventSeq": -1},
            "bool sequence": {**base, "eventSeq": True},
            "negative count": {**base, "counts": {"toolCalls": -1}},
            "bool count": {**base, "counts": {"modelTurns": True}},
            "oversized tool": {**base, "toolName": "x" * 65},
            "oversized reason": {**base, "waitingReason": "x" * 257},
            "oversized session": {**base, "nativeSessionId": "x" * 257},
            "non-iso timestamp": {**base, "observedAt": "yesterday"},
            "invalid calendar date": {**base, "observedAt": "2026-02-30T00:00:00Z"},
            "invalid clock hour": {**base, "observedAt": "2026-01-01T24:00:00Z"},
            "invalid timezone minute": {**base, "observedAt": "2026-01-01T00:00:00+00:99"},
            "not an object": ["starting"],
        }
        for name, value in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(BoardError) as caught:
                    normalize_activity(value)
                self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_timestamp_recency_uses_utc_instant_without_rewriting_display_value(self):
        previous = normalize_activity({"phase": "starting", "eventSeq": 4,
                                       "observedAt": "2026-01-01T01:00:00+01:00"})
        same = normalize_activity({"phase": "starting", "eventSeq": 4,
                                   "observedAt": "2026-01-01T00:00:00Z"})
        newer = normalize_activity({"phase": "waiting-model", "eventSeq": 4,
                                    "observedAt": "2026-01-01T00:30:00Z"})
        older = normalize_activity({"phase": "waiting-model", "eventSeq": 4,
                                    "observedAt": "2026-01-01T01:00:00+01:30"})
        self.assertEqual(previous["observedAt"], "2026-01-01T01:00:00+01:00")
        self.assertFalse(is_newer(same, previous))
        self.assertTrue(is_newer(newer, previous))
        self.assertFalse(is_newer(older, previous))
        fine = normalize_activity({"phase": "waiting-model", "eventSeq": 4,
                                   "observedAt": "2026-01-01T00:00:00.0000001Z"})
        self.assertTrue(is_newer(fine, same), "fractional precision beyond microseconds still orders correctly")

    def test_callback_updates_are_throttled_and_monotone(self):
        clock = FakeClock()
        observed = []
        publisher = ActivityPublisher(lambda value: observed.append(value) or True,
                                      min_interval_seconds=2.0, clock=clock)
        first = {"phase": "starting", "observedAt": "2026-01-01T00:00:00.000Z", "eventSeq": 1}
        self.assertTrue(publisher.publish(first))
        self.assertFalse(publisher.publish(dict(first)))
        self.assertFalse(publisher.publish(
            {"phase": "starting", "observedAt": "2025-12-31T23:59:59.000Z", "eventSeq": 0}))
        clock.advance(0.5)
        second = {"phase": "waiting-model", "observedAt": "2026-01-01T00:00:00.500Z", "eventSeq": 2}
        self.assertTrue(publisher.publish(second), "phase changes bypass the throttle")
        third = {"phase": "waiting-model", "observedAt": "2026-01-01T00:00:00.700Z", "eventSeq": 3}
        self.assertFalse(publisher.publish(third))
        self.assertEqual(publisher.current(), second, "coalescing does not advance the observation")
        clock.advance(2.0)
        self.assertTrue(publisher.publish(third))
        self.assertEqual(observed, [first, second, third])


class ActivityProjection(BoardTestCase):
    """The stored projection is bounded, monotone and never faked by a heartbeat."""

    @staticmethod
    def post_activity(client, identity: dict, data: dict | None = None, *, message: str = "native activity") -> dict:
        return client.progress(
            identity["workerId"],
            identity["attemptId"],
            identity["generation"],
            identity["nonce"],
            message,
            data=data,
        )

    def claimed_attempt(self, board, *, request_id: str = "activity-1", argv=("/bin/sleep", "20")):
        client = board.client()
        cwd = self.workdir()
        task = client.submit(
            requestId=request_id,
            task="observe me",
            cwd=str(cwd),
            adapter="command",
            argv=list(argv),
            timeoutSeconds=120,
        )["task"]
        client.register_worker("w-activity", adapter="command", capabilities=["command"])
        nonce = "a" * 32
        claim = client.claim("w-activity", f"claim-{request_id}", nonce, worker_instance="instance-activity")
        return client, task, claim["claim"], nonce

    def test_progress_stores_one_monotone_idempotent_projection(self):
        board = self.board()
        client, task, claim, nonce = self.claimed_attempt(board)
        attempt = claim["attempt"]
        identity = {
            "workerId": "w-activity",
            "attemptId": attempt["attemptId"],
            "generation": attempt["generation"],
            "nonce": nonce,
        }
        current = {"phase": "starting", "observedAt": "2026-01-01T00:00:00.000Z", "eventSeq": 1}
        first = self.post_activity(client, identity, {"activity": current})
        self.assertEqual(first["activity"], "recorded")
        newer = {"phase": "tool-running", "observedAt": "2026-01-01T00:00:02.000Z", "eventSeq": 2, "toolName": "read_file"}
        self.assertEqual(self.post_activity(client, identity, {"activity": newer})["activity"], "recorded")
        view = client.get(runId=task["runId"])
        self.assertEqual(view["activity"], newer)
        # An identical replay and an older receipt are both no-ops.
        self.assertEqual(self.post_activity(client, identity, {"activity": dict(newer)})["activity"], "unchanged")
        stale = {"phase": "starting", "observedAt": "2026-01-01T00:00:00.500Z", "eventSeq": 1}
        self.assertEqual(self.post_activity(client, identity, {"activity": stale})["activity"], "unchanged")
        self.assertEqual(client.get(runId=task["runId"])["activity"], newer)
        events = [
            event for event in client.read_events(after=0)["events"] if event["kind"] == "attempt.activity"
        ]
        self.assertEqual(len(events), 2, "only advancing receipts append an event")

    def test_invalid_or_tampered_activity_is_rejected(self):
        board = self.board()
        client, task, claim, nonce = self.claimed_attempt(board, request_id="activity-2")
        attempt = claim["attempt"]
        identity = {
            "workerId": "w-activity",
            "attemptId": attempt["attemptId"],
            "generation": attempt["generation"],
            "nonce": nonce,
        }
        for name, data in {
            "unknown data key": {"activity": {"phase": "starting", "eventSeq": 1}, "text": "hello"},
            "unknown activity key": {"activity": {"phase": "starting", "eventSeq": 1, "prompt": "secret"}},
            "bad phase": {"activity": {"phase": "thinking", "eventSeq": 1}},
            "bad sequence": {"activity": {"phase": "starting", "eventSeq": -3}},
            "bad count": {"activity": {"phase": "starting", "eventSeq": 1, "counts": {"modelTurns": -1}}},
            "wrong generation": {"activity": {"phase": "starting", "eventSeq": 1}},
        }.items():
            with self.subTest(name=name):
                payload = dict(identity)
                if name == "wrong generation":
                    payload["generation"] = attempt["generation"] + 1
                with self.assertRaises(BoardError) as caught:
                    self.post_activity(client, payload, data)
                self.assertIn(caught.exception.code, ("INVALID_ARGUMENT", "STALE_GENERATION"))
        self.assertIsNone(client.get(runId=task["runId"])["activity"])
        self.assertEqual(
            [event for event in client.read_events(after=0)["events"] if event["kind"] == "attempt.activity"], []
        )

    def test_a_heartbeat_alone_never_fabricates_native_progress(self):
        board = self.board()
        client, task, claim, nonce = self.claimed_attempt(board, request_id="activity-3")
        attempt = claim["attempt"]
        for _ in range(2):
            client.renew("w-activity", attempt["attemptId"], attempt["generation"], nonce)
        client.progress(
            "w-activity", attempt["attemptId"], attempt["generation"], nonce, "the worker is still supervising"
        )
        view = client.get(runId=task["runId"])
        self.assertIsNone(view["activity"], "a renewal or prose heartbeat is not native activity")
        self.assertNotIn("activity.json", json.dumps(view, ensure_ascii=False))
        self.assertEqual(
            [event for event in client.read_events(after=0)["events"] if event["kind"] == "attempt.activity"], []
        )

    def test_activity_is_bound_to_one_generation_and_never_inherited(self):
        board = self.board()
        client, task, claim, nonce = self.claimed_attempt(board, request_id="activity-4")
        attempt = claim["attempt"]
        activity = {"phase": "finishing", "observedAt": "2026-01-01T00:00:05.000Z", "eventSeq": 5}
        self.post_activity(
            client,
            {
                "workerId": "w-activity",
                "attemptId": attempt["attemptId"],
                "generation": attempt["generation"],
                "nonce": nonce,
            },
            {"activity": activity},
        )
        client.submit_result(
            "w-activity",
            attempt["attemptId"],
            attempt["generation"],
            nonce,
            {
                "status": "failed",
                "result": {"status": "nonzero"},
                "error": "boom",
                "shutdownConfirmed": True,
                "terminationReason": "harness-error",
            },
        )
        self.assertEqual(client.get(runId=task["runId"])["status"], "failed")
        client.retry(runId=task["runId"], reason="second generation")
        client.register_worker("w-activity-2", adapter="command", capabilities=["command"])
        second = client.claim("w-activity-2", "claim-activity-4b", "b" * 32, worker_instance="instance-2")["claim"]["attempt"]
        self.assertEqual(second["generation"], 2)
        self.assertNotEqual(second["attemptId"], attempt["attemptId"])
        self.assertIsNone(
            client.get(runId=task["runId"])["activity"],
            "a replacement attempt never inherits the previous attempt's observation",
        )
        # The old projection stays readable for the attempt that produced it.
        with board.store.db.read() as connection:
            self.assertIsNotNone(board.store._activity_latest(connection, attempt["attemptId"]))


class WorkerActivityForwarding(BoardTestCase):
    """Real Worker progress over private SQLite and a real bounded live channel.

    The owned child is an explicit command fixture, not an installed harness.
    Only the role binding is supplied: its channel uses the actual C-Two client,
    strict wire DTOs and endpoint handler. The SDK connection alone is replaced
    by an in-process endpoint; this provides no cross-process/native receipt.
    """

    def live_attempt(self, *, lease_seconds: int = 15, argv=("/bin/sleep", "30"), timeout_seconds: int = 120):
        board = self.board(lease_seconds=lease_seconds)
        client = board.client()
        worker = Worker("w-forward", self.directory, client=client, lease_seconds=lease_seconds, log=lambda _m: None)
        worker.register()
        cwd = self.workdir()
        task = client.submit(
            requestId="forward-1",
            task="supervise",
            cwd=str(cwd),
            adapter="command",
            argv=list(argv),
            timeoutSeconds=timeout_seconds,
        )["task"]
        nonce = "c" * 32
        claim = client.claim("w-forward", "claim-forward-1", nonce, worker_instance=worker.instance_id)["claim"]
        attempt = claim["attempt"]
        worker.spool.ensure()
        worker.spool.write_startup(
            {
                "workerId": "w-forward",
                "instanceId": worker.instance_id,
                "nonce": nonce,
                "claimRequestId": "claim-forward-1",
                "attemptId": attempt["attemptId"],
                "taskId": task["taskId"],
                "generation": attempt["generation"],
                "createdAt": "2026-01-01T00:00:00.000Z",
            }
        )
        directory = worker.attempt_directory(task["taskId"], attempt["attemptId"])
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        context = ExecutionContext(
            task_id=task["taskId"],
            attempt_id=attempt["attemptId"],
            generation=attempt["generation"],
            spec=claim["task"]["spec"],
            directory=directory,
            runtime={},
            environment=private_environment(self.directory),
        )
        implementation = get_adapter("command")
        implementation.prepare(context)
        handle = implementation.start(context)
        self.children = getattr(self, "children", [])
        self.children.append(handle.process)
        self.addCleanup(lambda: (implementation.cancel(handle), handle.wait(15)))
        renewal = _Renewal(worker, claim, handle, implementation)
        return board, client, worker, attempt, directory, renewal

    def bounded_channel(self, attempt):
        # Reuse the existing test CRM; the fake SDK connection below reaches
        # real named handlers, without implementing a second transport.
        contract = getattr(type(self), "_test_contract", None)
        if contract is None:
            fixture = Path(__file__).resolve().parents[1] / "buddy/harnesses/fixtures/c_two_live_peer.py"
            spec = importlib.util.spec_from_file_location("activity_c_two_live_peer", fixture)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            contract = type(self)._test_contract = module.TEST_CRM
        identity = RunIdentity(task_id=attempt["taskId"], attempt_id=attempt["attemptId"],
                               generation=attempt["generation"], invocation_id="activity-invocation",
                               turn_id="activity-turn", input_sha256="f" * 64)
        endpoint = ctl.CTwoLiveEndpoint(identity, LiveCapabilities(inquiry_delivery="cooperative-checkpoint"),
                                       contract, instance_id="a" * 64, token="b" * 64)
        channel = ctl.CTwoLiveChannel(identity, contract, name="Activity Fixture",
                                     address="ipc://activity-fixture", instance_id="a" * 64, token="b" * 64)
        self.addCleanup(lambda: endpoint.close(reason="fixture-finished"))
        self.addCleanup(lambda: channel.close(reason="fixture-finished"))
        self.enterContext(mock.patch.object(ctl.cc, "connect", side_effect=lambda *a, **kw: nullcontext(endpoint)))
        self.enterContext(mock.patch.object(role_live, "handle_live_binding",
                                           return_value=(role_live.LIVE_BOUND, channel)))
        return endpoint, channel

    @staticmethod
    def activity_events(client):
        return [event for event in client.read_events(after=0)["events"] if event["kind"] == "attempt.activity"]

    def test_one_bound_channel_is_forwarded_once_and_updates_are_forwarded(self):
        board, client, worker, attempt, directory, renewal = self.live_attempt()
        endpoint, channel = self.bounded_channel(attempt)
        publisher = ActivityPublisher(endpoint.publish_activity, min_interval_seconds=0.0)
        first = {"phase": "waiting-model", "observedAt": "2026-01-01T00:00:00.000Z", "eventSeq": 1}
        self.assertTrue(publisher.publish(first))
        renewal._forward_activity()
        self.assertEqual(client.get(runId=attempt["taskId"])["activity"], first)
        renewal._forward_activity()
        self.assertEqual(len(self.activity_events(client)), 1, "a repeated live receipt is not published twice")
        self.assertEqual(
            [event for event in client.read_events(after=0)["events"] if event["kind"] == "attempt.progress"],
            [], "forwarding native activity never fabricates a prose progress event")
        second = {"phase": "tool-running", "observedAt": "2026-01-01T00:00:02.000Z", "eventSeq": 2,
                  "toolName": "write_file", "counts": {"toolCalls": 1}}
        self.assertTrue(publisher.publish(second))
        renewal._forward_activity()
        self.assertEqual(client.get(runId=attempt["taskId"])["activity"], second)
        self.assertFalse(endpoint.publish_activity(first), "the endpoint also fences older source observations")
        renewal._forward_activity()
        self.assertEqual(len(self.activity_events(client)), 2)
        self.assertIsNone(renewal.handle.process.poll(), "activity reads never stop the owned child")

    def test_unavailable_binding_is_retried_without_activity_or_heartbeat(self):
        board, client, worker, attempt, directory, renewal = self.live_attempt()
        endpoint, channel = self.bounded_channel(attempt)
        with mock.patch.object(role_live, "handle_live_binding",
                               return_value=(role_live.LIVE_UNAVAILABLE, None)) as binding:
            renewal._forward_activity()
            renewal._forward_activity()
            self.assertEqual(binding.call_count, 2, "unavailable does not become a cached stop fact")
        self.assertIsNone(client.get(runId=attempt["taskId"])["activity"])
        self.assertEqual(self.activity_events(client), [])
        self.assertIsNone(renewal.handle.process.poll())
        # A failed source read stays an unavailable fact; it invents no activity.
        endpoint.close(reason="source-unavailable")
        renewal._forward_activity()
        self.assertEqual(self.activity_events(client), [])
        self.assertEqual(
            [event for event in client.read_events(after=0)["events"] if event["kind"] == "attempt.progress"], [])
        self.assertIsNone(renewal.handle.process.poll(), "unknown/unavailable does not mean stopped")

    def test_rejected_progress_does_not_advance_the_worker_receipt(self):
        board, client, worker, attempt, directory, renewal = self.live_attempt()
        endpoint, channel = self.bounded_channel(attempt)
        payload = {"phase": "tool-running", "eventSeq": 1}
        endpoint.publish_activity(payload)
        # Invalidate only this claim capability, exercising the real progress
        # RPC and SQLite ownership fence instead of mocking progress failure.
        renewal.nonce = "d" * 32
        renewal._forward_activity()
        self.assertIsNone(renewal._activity)
        self.assertEqual(self.activity_events(client), [])
        self.assertIsNone(renewal.handle.process.poll())
        renewal.nonce = "c" * 32
        renewal._forward_activity()
        self.assertEqual(client.get(runId=attempt["taskId"])["activity"], payload)
        self.assertEqual(len(self.activity_events(client)), 1)

    def test_replaced_generation_rejects_an_old_workers_live_receipt(self):
        board, client, worker, attempt, directory, renewal = self.live_attempt()
        endpoint, channel = self.bounded_channel(attempt)
        first = {"phase": "starting", "eventSeq": 1}
        endpoint.publish_activity(first)
        renewal._forward_activity()
        self.assertEqual(client.get(runId=attempt["taskId"])["activity"], first)
        # Shutdown evidence comes from this owned fixture handle before any
        # retry is admitted; no native harness completion is claimed.
        renewal.implementation.cancel(renewal.handle)
        renewal.handle.wait(15)
        self.assertIsNotNone(renewal.handle.process.poll())
        client.submit_result("w-forward", attempt["attemptId"], attempt["generation"], "c" * 32,
                             {"status": "failed", "result": {"status": "nonzero"}, "error": "fixture-stopped",
                              "shutdownConfirmed": True, "terminationReason": "harness-error"})
        client.retry(runId=attempt["taskId"], reason="replacement generation")
        client.register_worker("w-forward-2", adapter="command", capabilities=["command"])
        replacement = client.claim("w-forward-2", "claim-forward-2", "e" * 32,
                                   worker_instance="instance-forward-2")["claim"]["attempt"]
        self.assertEqual(replacement["generation"], attempt["generation"] + 1)
        endpoint.publish_activity({"phase": "tool-running", "eventSeq": 2})
        renewal._forward_activity()
        self.assertEqual(renewal._activity, first, "a stale generation cannot advance the worker receipt")
        self.assertIsNone(client.get(runId=attempt["taskId"])["activity"])
        self.assertEqual(len(self.activity_events(client)), 1)

    def test_unextracted_command_never_reads_an_activity_sidecar(self):
        board, client, worker, attempt, directory, renewal = self.live_attempt()
        # A command has only its existing DB projection. A retired file's
        # contents carry no live contract and must not be opened at all.
        existing = {"phase": "unknown", "eventSeq": 1}
        client.progress("w-forward", attempt["attemptId"], attempt["generation"], "c" * 32,
                        data={"activity": existing})
        path = directory / "activity.json"
        path.write_text("retired activity file")
        with mock.patch.object(role_live, "handle_live_binding",
                               return_value=(role_live.LIVE_UNEXTRACTED, None)), \
                mock.patch.object(os, "open", wraps=os.open) as opened:
            renewal._forward_activity()
        activity_opens = [call for call in opened.call_args_list if Path(call.args[0]) == path]
        self.assertEqual(activity_opens, [], "Worker must never read an activity sidecar, including command fallback")
        self.assertEqual(client.get(runId=attempt["taskId"])["activity"], existing)
        self.assertEqual(len(self.activity_events(client)), 1)
        self.assertIsNone(renewal._activity)
        self.assertIsNone(renewal.handle.process.poll())


class ActivityPublisherTest(BoardTestCase):
    def test_live_publication_coalesces_without_losing_failed_or_new_phase(self):
        observed = []
        ticks = iter((0., .5, 1., 1.5, 3.))
        accept = [True, False, True, True]
        def send(value):
            if not accept.pop(0):
                return False
            observed.append(value)
            return True
        publisher = activity_module.ActivityPublisher(send, clock=lambda: next(ticks))
        self.assertTrue(publisher.publish({"phase": "starting", "eventSeq": 1}))
        self.assertFalse(publisher.publish({"phase": "starting", "eventSeq": 2}))
        self.assertFalse(publisher.publish({"phase": "tool-running", "eventSeq": 3}))
        self.assertEqual(publisher.current()["eventSeq"], 1)
        self.assertTrue(publisher.publish({"phase": "tool-running", "eventSeq": 3}))
        self.assertTrue(publisher.publish({"phase": "finishing", "eventSeq": 4}))
        self.assertEqual([v["eventSeq"] for v in observed], [1, 3, 4])

    def test_failed_callback_does_not_advance_recency_or_throttle(self):
        clock = FakeClock()
        observed = []
        outcomes = iter((True, False, RuntimeError("source unavailable"), True))
        def send(value):
            result = next(outcomes)
            if isinstance(result, Exception):
                raise result
            if result:
                observed.append(value)
            return result
        publisher = ActivityPublisher(send, min_interval_seconds=2.0, clock=clock)
        first = {"phase": "starting", "eventSeq": 1}
        second = {"phase": "starting", "eventSeq": 2}
        self.assertTrue(publisher.publish(first))
        clock.advance(2.0)
        self.assertFalse(publisher.publish(second))
        self.assertEqual(publisher.current(), first)
        clock.advance(0.1)
        with self.assertRaisesRegex(RuntimeError, "source unavailable"):
            publisher.publish(second)
        self.assertEqual(publisher.current(), first)
        clock.advance(0.1)
        self.assertTrue(publisher.publish(second), "failed sends advance neither recency nor throttle time")
        self.assertEqual(observed, [first, second])
