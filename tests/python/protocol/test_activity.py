"""Activity projection regressions: whitelist, binding, monotonicity, heartbeat truth.

Every test uses a private state directory and a private command child. Inherited
Buddy runtime, worker and credential variables are removed before any test
subprocess starts, as AGENTS.md requires.
"""
from __future__ import annotations
from admission import admit_execution

import json
import os
import time
import threading
from pathlib import Path
from unittest import mock

from support import BoardTestCase, wait_for

from hey_my_buddy.protocol import activity as activity_module
from hey_my_buddy.protocol.activity import ActivityPublisher, is_newer, normalize_activity
from hey_my_buddy.buddy.harnesses import c_two_live as ctl
from hey_my_buddy.buddy.harnesses.live import LiveCapabilities
from hey_my_buddy.buddy.harnesses.run_contract import RunIdentity
from hey_my_buddy.buddy.harnesses.registry import ExecutionContext, adapter as get_adapter
from hey_my_buddy.errors import BoardError
from hey_my_buddy.buddy.runtime.execution import Execution

#: Inherited variables that would otherwise point a test subprocess at a production
#: runtime, a Worker identity or an agent credential instead of this private root.
SANITIZED_VARIABLES = (
    "HMB_STATE_DIR",
    "HMB_RUNTIME_ROOT",
    "HMB_RUNTIME",
    "HMB_RUNTIME_IDENTITY",
    "HMB_WORKER_STATE",
    "HMB_WORKER_ID",
    "HMB_TASK_ID",
    "HMB_ATTEMPT_ID",
    "HMB_AGENT_CREDENTIAL_FILE",
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
)

SOURCE_ROOT = Path(__file__).resolve().parents[3] / "src"
TEST_ROOT = Path(__file__).resolve().parent


def private_environment(directory: Path, **extra: str) -> dict:
    values = {key: value for key, value in os.environ.items() if key not in SANITIZED_VARIABLES}
    values.update(
        {
            "HMB_STATE_DIR": str(directory),
            "HMB_RUNTIME_ROOT": str(directory / "runtime-root"),
            "HMB_DEV_SOURCE": "1",
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
    def test_native_tool_names_fit_the_activity_contract(self):
        for raw in (None, "", "   ", "tool\0name"):
            with self.subTest(raw=raw):
                self.assertIsNone(activity_module.native_tool_name(raw))
        name = "mcp__" + "s" * 60 + "__tool"
        projected = activity_module.native_tool_name("  " + name + "  ")
        self.assertEqual(projected, name[:activity_module.MAX_TOOL_NAME])
        self.assertEqual(normalize_activity({"phase": "tool-running", "eventSeq": 1,
                                             "toolName": projected})["toolName"], projected)

    def test_the_whitelist_accepts_the_bounded_projection(self):
        payload = {
            "phase": "tool-running",
            "observedAt": "2026-01-01T00:00:01.000Z",
            "eventSeq": 7,
            "nativeSessionId": "session-1",
            "lastNativeActivityAt": "2026-01-01T00:00:01.000Z",
            "lastToolActivityAt": "2026-01-01T00:00:00.900Z",
            "toolName": "read_file",
            "waitingReason": {"code": "native-authority-required", "params": {"kind": "permission"}},
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
        return client.progress(identity["attemptId"], identity["generation"], identity["nonce"], message, data=data)

    def claimed_attempt(self, board, *, request_id: str = "activity-1", argv=("/bin/sleep", "20")):
        client = board.client()
        cwd = self.workdir()
        task = board.submit_execution(
            requestId=request_id,
            task="observe me",
            cwd=str(cwd),
            adapter="command",
            argv=list(argv),
            expectedSeconds=120,
        )["task"]
        nonce = "a" * 32
        claim = admit_execution(client, nonce)
        return client, task, claim["claim"], nonce

    def test_progress_stores_one_monotone_idempotent_projection(self):
        board = self.board()
        client, task, claim, nonce = self.claimed_attempt(board)
        attempt = claim["attempt"]
        identity = {'attemptId': attempt['attemptId'], 'generation': attempt['generation'], 'nonce': nonce}
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
        self.assertEqual(len(events), 1, "advancing receipts replace this attempt's previous activity")
        self.assertEqual(events[0]["payload"], {})
        self.assertEqual(events[0]["taskId"], task["runId"])
        self.assertEqual(events[0]["attemptId"], attempt["attemptId"])

    def test_activity_waiting_reason_is_checked_at_service_receipt(self):
        board = self.board()
        client, task, claim, nonce = self.claimed_attempt(board)
        attempt = claim['attempt']
        identity = {'attemptId': attempt['attemptId'], 'generation': attempt['generation'], 'nonce': nonce}
        waiting = {'phase': 'waiting-host', 'eventSeq': 1,
                   'waitingReason': {'code': 'native-authority-required',
                                     'params': {'kind': 'permission'}}}
        self.assertEqual(self.post_activity(client, identity, {'activity': waiting})['activity'], 'recorded')
        self.assertEqual(client.get(runId=task['runId'])['activity']['waitingReason'], waiting['waitingReason'])
        for reason in ('native permission requested', {'code': 'unregistered-wait', 'params': {}},
                       {'code': 'native-authority-required', 'params': {'unexpected': 'permission'}},
                       {'code': 'native-authority-required', 'params': {}},
                       {'code': 'router-input-unknown', 'params': {}},
                       {'code': 'native-authority-required', 'params': {'kind': 'permission.requested'}},
                       {'code': 'native-authority-required', 'params': {'kind': 'tool'}},
                       {'code': 'native-authority-required', 'params': {'kind': []}}):
            invalid = {'phase': 'waiting-host', 'eventSeq': 2, 'waitingReason': reason}
            with self.subTest(reason=reason), self.assertRaises(BoardError) as error:
                self.post_activity(client, identity, {'activity': invalid})
            self.assertEqual(error.exception.code, 'INVALID_ARGUMENT')
        self.assertEqual(client.get(runId=task['runId'])['activity'], waiting)

    def test_replacement_preserves_forward_cursors_and_rolls_back_with_the_projection(self):
        board = self.board()
        client, task, claim, nonce = self.claimed_attempt(board)
        attempt = claim["attempt"]
        first = {"phase": "waiting-model", "eventSeq": 1}
        second = {"phase": "tool-running", "eventSeq": 2, "toolName": "read_file"}
        client.progress(attempt["attemptId"], attempt["generation"], nonce, data={"activity": first})
        page = client.read_events(after=0)
        original = page["events"][-1]
        old_cursor = page["cursor"]
        self.assertEqual(original["kind"], "attempt.activity")

        append = board.store._append_event
        def fail_after_append(*args, **kwargs):
            append(*args, **kwargs)
            raise BoardError("FIXTURE_FAILURE", "Roll back the replacement")
        with mock.patch.object(board.store, "_append_event", side_effect=fail_after_append):
            with self.assertRaises(BoardError):
                client.progress(attempt["attemptId"], attempt["generation"], nonce, data={"activity": second})
        self.assertEqual(client.get(runId=task["runId"])["activity"], first)
        self.assertEqual(client.read_events(after=0)["events"][-1], original)
        self.assertEqual(board.store.head(), old_cursor)

        client.progress(attempt["attemptId"], attempt["generation"], nonce, data={"activity": second})
        following = board.store.events_wait({"after": old_cursor, "runId": task["runId"], "timeoutMs": 0})
        self.assertEqual(len(following["events"]), 1)
        self.assertGreater(following["cursor"], old_cursor)
        self.assertEqual(following["head"], board.store.head())
        self.assertEqual(following["events"][0]["payload"], {})
        self.assertEqual(following["events"][0]["taskId"], task["runId"])
        self.assertEqual(following["events"][0]["attemptId"], attempt["attemptId"])
        self.assertFalse(following["timedOut"])
        all_events = client.read_events(after=0)["events"]
        self.assertNotIn(old_cursor, [event["seq"] for event in all_events])
        self.assertEqual(client.get(runId=task["runId"])["activity"], second)

    def test_replacement_deletes_use_the_existing_task_index(self):
        board = self.board()
        client, task, claim, nonce = self.claimed_attempt(board)
        attempt = claim["attempt"]
        statements = []
        with board.store.db.read() as connection:
            connection.set_trace_callback(statements.append)
        try:
            client.progress(attempt["attemptId"], attempt["generation"], nonce,
                            data={"activity": {"phase": "waiting-model", "eventSeq": 1}})
            with board.store.db.write() as connection:
                for kind in ("harness.checked", "harness.probe_unknown", "harness.account_observed"):
                    board.store._append_event(connection, kind, payload={"harness": "codex"})
        finally:
            with board.store.db.read() as connection:
                connection.set_trace_callback(None)
        deletes = [statement for statement in statements if statement.startswith("DELETE FROM events")]
        self.assertEqual(len(deletes), 4)
        with board.store.db.read() as connection:
            for statement in deletes:
                with self.subTest(sql=statement):
                    plan = [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + statement)]
                    self.assertTrue(any("USING INDEX events_task_idx" in detail for detail in plan), plan)

    def test_invalid_or_tampered_activity_is_rejected(self):
        board = self.board()
        client, task, claim, nonce = self.claimed_attempt(board, request_id="activity-2")
        attempt = claim["attempt"]
        identity = {'attemptId': attempt['attemptId'], 'generation': attempt['generation'], 'nonce': nonce}
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
            client.progress(attempt["attemptId"], attempt["generation"], nonce)
        client.progress(attempt["attemptId"], attempt["generation"], nonce, "the worker is still supervising")
        view = client.get(runId=task["runId"])
        self.assertIsNone(view["activity"], "a prose heartbeat is not native activity")
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
            {'attemptId': attempt['attemptId'], 'generation': attempt['generation'], 'nonce': nonce},
            {"activity": activity},
        )
        client.submit_result(attempt["attemptId"], attempt["generation"], nonce, {
                "status": "failed",
                "result": {"status": "nonzero"},
                "error": "boom",
                "shutdownConfirmed": True,
                "terminationReason": "harness-error",
            })
        self.assertEqual(client.get(runId=task["runId"])["state"], "failed")
        client.retry(runId=task["runId"], note="second generation")
        second = admit_execution(client, "b" * 32)["claim"]["attempt"]
        self.assertEqual(second["generation"], 2)
        self.assertNotEqual(second["attemptId"], attempt["attemptId"])
        self.assertIsNone(
            client.get(runId=task["runId"])["activity"],
            "a replacement attempt never inherits the previous attempt's observation",
        )
        # The old projection stays readable for the attempt that produced it.
        with board.store.db.read() as connection:
            self.assertIsNotNone(board.store._activity_latest(connection, attempt["attemptId"]))
        for event_seq in (1, 2):
            client.progress(second["attemptId"], second["generation"], "b" * 32,
                            data={"activity": {"phase": "waiting-model", "eventSeq": event_seq}})
        events = [event for event in client.read_events(after=0)["events"] if event["kind"] == "attempt.activity"]
        self.assertEqual({event["attemptId"] for event in events}, {attempt["attemptId"], second["attemptId"]})
        self.assertEqual(len(events), 2, "replacement belongs to one attempt, including across retries")


class ExecutionActivityForwarding(BoardTestCase):
    """Execution reads its own endpoint locally and forwards to authoritative state."""

    def live_attempt(self, *, lease_seconds: int = 15, argv=("/bin/sleep", "30"), expected_seconds: int = 120):
        board = self.board(lease_seconds=lease_seconds)
        client = board.client()
        cwd = self.workdir()
        task = board.submit_execution(
            requestId="forward-1",
            task="supervise",
            cwd=str(cwd),
            adapter="command",
            argv=list(argv),
            expectedSeconds=expected_seconds,
        )["task"]
        nonce = "c" * 32
        claim = admit_execution(client, nonce)["claim"]
        attempt = claim["attempt"]
        execution = Execution(claim, self.directory, nonce, client=client)
        directory = execution.directory
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        context = ExecutionContext(
            task_id=task["runId"],
            attempt_id=attempt["attemptId"],
            generation=attempt["generation"],
            spec=claim["task"]["spec"],
            directory=directory,
            runtime={},
            environment=private_environment(self.directory),
            session_record_path=None,
        )
        implementation = get_adapter("command")
        implementation.prepare(context)
        handle = implementation.start(context)
        self.children = getattr(self, "children", [])
        self.children.append(handle.process)
        self.addCleanup(lambda: (implementation.cancel(handle), handle.wait(15)))
        control = execution.control
        control.attach(handle, implementation)
        self.addCleanup(lambda: (control.stop(), control.join(2)) if control.ident is not None else None)
        return board, client, execution, attempt, directory, control

    def local_endpoint(self, attempt, control):
        identity = RunIdentity(task_id=attempt["taskId"], attempt_id=attempt["attemptId"],
                               generation=attempt["generation"], invocation_id="activity-invocation",
                               turn_id="activity-turn", input_sha256="f" * 64)
        endpoint = ctl.CTwoLiveEndpoint(identity, LiveCapabilities(inquiry_delivery="cooperative-checkpoint"))
        control.handle.live_endpoint = endpoint
        return endpoint

    @staticmethod
    def activity_events(client):
        return [event for event in client.read_events(after=0)["events"] if event["kind"] == "attempt.activity"]

    def test_one_bound_channel_is_forwarded_once_and_updates_are_forwarded(self):
        board, client, execution, attempt, directory, control = self.live_attempt()
        endpoint = self.local_endpoint(attempt, control)
        publisher = ActivityPublisher(endpoint.publish_activity, min_interval_seconds=0.0)
        first = {"phase": "waiting-model", "observedAt": "2026-01-01T00:00:00.000Z", "eventSeq": 1}
        control.start()
        self.assertTrue(publisher.publish(first))
        self.assertTrue(wait_for(lambda: client.get(runId=attempt["taskId"])["activity"] == first, 3))
        time.sleep(0.4)
        self.assertEqual(len(self.activity_events(client)), 1, "a repeated live receipt is not published twice")
        self.assertEqual(
            [event for event in client.read_events(after=0)["events"] if event["kind"] == "attempt.progress"],
            [], "native activity never fabricates a prose progress event")
        second = {"phase": "tool-running", "observedAt": "2026-01-01T00:00:02.000Z", "eventSeq": 2,
                  "toolName": "write_file", "counts": {"toolCalls": 1}}
        self.assertTrue(publisher.publish(second))
        self.assertTrue(wait_for(lambda: client.get(runId=attempt["taskId"])["activity"] == second, 3))
        self.assertFalse(endpoint.publish_activity(first), "the endpoint fences older observations")
        self.assertEqual(len(self.activity_events(client)), 1)
        self.assertEqual(self.activity_events(client)[0]["payload"], {})
        self.assertEqual(self.activity_events(client)[0]["attemptId"], attempt["attemptId"])
        self.assertIsNone(control.handle.process.poll(), "activity reads never stop the owned child")

    def test_unavailable_binding_is_retried_without_activity_or_heartbeat(self):
        board, client, execution, attempt, directory, control = self.live_attempt()
        endpoint = self.local_endpoint(attempt, control)
        with mock.patch.object(endpoint, "current_activity", side_effect=BoardError("SERVICE_UNAVAILABLE", "fixture endpoint")) as observation:
            control.start()
            self.assertTrue(wait_for(lambda: observation.call_count >= 2, 3),
                            "an unavailable observation is retried without inventing progress")
        self.assertIsNone(client.get(runId=attempt["taskId"])["activity"])
        self.assertEqual(self.activity_events(client), [])
        self.assertIsNone(control.handle.process.poll())
        endpoint.close(reason="source-unavailable")
        time.sleep(0.4)
        self.assertEqual(self.activity_events(client), [])
        self.assertEqual(
            [event for event in client.read_events(after=0)["events"] if event["kind"] == "attempt.progress"], [])
        self.assertIsNone(control.handle.process.poll(), "unknown availability does not mean stopped")

    def test_offline_progress_remains_pending_until_delivery_succeeds(self):
        board, client, execution, attempt, directory, control = self.live_attempt()
        endpoint = self.local_endpoint(attempt, control)
        payload = {"phase": "tool-running", "eventSeq": 1}
        endpoint.publish_activity(payload)
        original = client.progress
        online = threading.Event()

        def progress(*args, **kwargs):
            if not online.is_set():
                raise BoardError("SERVICE_UNAVAILABLE", "private service is offline")
            return original(*args, **kwargs)

        with mock.patch.object(client, "progress", side_effect=progress):
            control.start()
            self.assertTrue(wait_for(lambda: execution._hints.get("activity") == payload, 3))
            self.assertEqual(self.activity_events(client), [])
            self.assertIsNone(control.handle.process.poll())
            online.set()
            self.assertTrue(wait_for(lambda: client.get(runId=attempt["taskId"])["activity"] == payload, 3))
            self.assertEqual(len(self.activity_events(client)), 1)

    def test_invalid_nonce_is_an_explicit_fatal_control_failure(self):
        board, client, execution, attempt, directory, control = self.live_attempt()
        endpoint = self.local_endpoint(attempt, control)
        payload = {"phase": "tool-running", "eventSeq": 1}
        endpoint.publish_activity(payload)
        execution.nonce = "d" * 32
        control.start()
        self.assertTrue(wait_for(lambda: control.fatal is not None, 3))
        with self.assertRaises(BoardError) as refused:
            control.raise_fatal()
        self.assertEqual(refused.exception.code, "UNAUTHORIZED")
        self.assertEqual(self.activity_events(client), [])

    def test_replaced_generation_rejects_an_old_executions_live_receipt(self):
        board, client, execution, attempt, directory, control = self.live_attempt()
        endpoint = self.local_endpoint(attempt, control)
        first = {"phase": "starting", "eventSeq": 1}
        control.start()
        endpoint.publish_activity(first)
        self.assertTrue(wait_for(lambda: client.get(runId=attempt["taskId"])["activity"] == first, 3))
        control.implementation.cancel(control.handle)
        control.handle.wait(15)
        self.assertIsNotNone(control.handle.process.poll())
        client.submit_result(attempt["attemptId"], attempt["generation"], "c" * 32, {"status": "failed", "result": {"status": "nonzero"}, "error": "fixture-stopped",
                              "shutdownConfirmed": True, "terminationReason": "harness-error"})
        client.retry(runId=attempt["taskId"], note="replacement generation")
        replacement = admit_execution(client, "e" * 32)["claim"]["attempt"]
        self.assertEqual(replacement["generation"], attempt["generation"] + 1)
        second = {"phase": "tool-running", "eventSeq": 2}
        endpoint.publish_activity(second)
        self.assertTrue(wait_for(lambda: execution._hints.get("activity") == second, 3))
        self.assertIsNone(client.get(runId=attempt["taskId"])["activity"])
        self.assertEqual(len(self.activity_events(client)), 1)

    def test_unextracted_command_never_reads_an_activity_sidecar(self):
        board, client, execution, attempt, directory, control = self.live_attempt()
        existing = {"phase": "unknown", "eventSeq": 1}
        client.progress(attempt["attemptId"], attempt["generation"], "c" * 32, data={"activity": existing})
        path = directory / "activity.json"
        path.write_text("retired activity file", encoding="utf-8", newline="\n")
        with mock.patch.object(os, "open", wraps=os.open) as opened:
            control.start()
            time.sleep(0.4)
            control.stop()
            control.join(2)
        activity_opens = [call for call in opened.call_args_list if Path(call.args[0]) == path]
        self.assertEqual(activity_opens, [], "an unextracted command has no activity file fallback")
        self.assertEqual(client.get(runId=attempt["taskId"])["activity"], existing)
        self.assertEqual(len(self.activity_events(client)), 1)
        self.assertIsNone(control._activity)
        self.assertIsNone(control.handle.process.poll())


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
