"""Activity projection regressions: whitelist, binding, monotonicity, heartbeat truth.

Every test uses a private state directory and a private command child. Inherited
Buddy runtime, worker and credential variables are removed before any test
subprocess starts, as AGENTS.md requires.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from support import BoardTestCase

from buddy import activity as activity_module
from buddy.activity import ActivitySidecar, normalize_activity, read_sidecar, validate_sidecar
from buddy.adapters import ExecutionContext, adapter as get_adapter
from buddy.errors import BoardError
from buddy.worker.worker import Worker, _Renewal

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

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
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
            "not an object": ["starting"],
        }
        for name, value in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(BoardError) as caught:
                    normalize_activity(value)
                self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_sidecar_binding_is_enforced_and_rejections_are_quiet(self):
        directory = self.workdir("attempt")
        sidecar = ActivitySidecar(
            directory,
            task_id="task-1",
            attempt_id="attempt-1",
            generation=1,
            min_interval_seconds=0.0,
        )
        written = sidecar.publish({"phase": "waiting-model", "observedAt": "2026-01-01T00:00:00.000Z", "eventSeq": 1})
        self.assertEqual(written, directory / "activity.json")
        document = json.loads(written.read_text())
        self.assertEqual(document["version"], activity_module.ACTIVITY_VERSION)
        self.assertEqual((document["taskId"], document["attemptId"], document["generation"]), ("task-1", "attempt-1", 1))
        # The path is never part of the payload, so a public response cannot leak it.
        self.assertNotIn("path", document["activity"])

        self.assertEqual(
            read_sidecar(written, task_id="task-1", attempt_id="attempt-1", generation=1),
            {"phase": "waiting-model", "observedAt": "2026-01-01T00:00:00.000Z", "eventSeq": 1},
        )
        for name, kwargs in {
            "other task": {"task_id": "task-2", "attempt_id": "attempt-1", "generation": 1},
            "other attempt": {"task_id": "task-1", "attempt_id": "attempt-2", "generation": 1},
            "other generation": {"task_id": "task-1", "attempt_id": "attempt-1", "generation": 2},
        }.items():
            with self.subTest(name=name):
                self.assertIsNone(read_sidecar(written, **kwargs))
                with self.assertRaises(BoardError):
                    validate_sidecar(document, **kwargs)

        foreign = {**document, "version": activity_module.ACTIVITY_VERSION + 1}
        self.assertIsNone(read_sidecar(_write(directory / "other.json", foreign), task_id="task-1", attempt_id="attempt-1", generation=1))
        self.assertIsNone(read_sidecar(directory / "missing.json", task_id="task-1", attempt_id="attempt-1", generation=1))
        broken = directory / "broken.json"
        broken.write_text("{not json")
        self.assertIsNone(read_sidecar(broken, task_id="task-1", attempt_id="attempt-1", generation=1))

    def test_sidecar_updates_are_atomic_throttled_and_monotone(self):
        directory = self.workdir("sidecar")
        clock = FakeClock()
        sidecar = ActivitySidecar(
            directory, task_id="t", attempt_id="a", generation=1, min_interval_seconds=2.0, clock=clock
        )
        first = {"phase": "starting", "observedAt": "2026-01-01T00:00:00.000Z", "eventSeq": 1}
        self.assertIsNotNone(sidecar.publish(first))
        path = directory / "activity.json"
        stamp = path.read_text()
        # Identical and older receipts never rewrite the file.
        self.assertIsNone(sidecar.publish(dict(first)))
        self.assertIsNone(
            sidecar.publish({"phase": "starting", "observedAt": "2025-12-31T23:59:59.000Z", "eventSeq": 0})
        )
        clock.advance(0.5)
        # A phase change is published immediately; a same-phase update waits for the window.
        self.assertIsNotNone(
            sidecar.publish({"phase": "waiting-model", "observedAt": "2026-01-01T00:00:00.500Z", "eventSeq": 2})
        )
        self.assertIsNone(
            sidecar.publish({"phase": "waiting-model", "observedAt": "2026-01-01T00:00:00.700Z", "eventSeq": 3})
        )
        clock.advance(2.0)
        self.assertIsNotNone(
            sidecar.publish({"phase": "waiting-model", "observedAt": "2026-01-01T00:00:02.700Z", "eventSeq": 3})
        )
        self.assertNotEqual(path.read_text(), stamp)
        self.assertEqual(list(directory.glob("*.tmp")), [], "an atomic replace leaves no temporary file")
        self.assertEqual(list(directory.glob(".*.tmp")), [])


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value))
    return path


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
    """The Worker forwards a bound sidecar once, and ignores anything unbound."""

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

    def test_one_bound_sidecar_is_forwarded_once_and_updates_are_forwarded(self):
        board, client, worker, attempt, directory, renewal = self.live_attempt()
        clock = FakeClock()
        sidecar = ActivitySidecar(
            directory,
            task_id=attempt["taskId"],
            attempt_id=attempt["attemptId"],
            generation=attempt["generation"],
            min_interval_seconds=0.0,
            clock=clock,
        )
        first = {"phase": "waiting-model", "observedAt": "2026-01-01T00:00:00.000Z", "eventSeq": 1}
        sidecar.publish(first)
        renewal._forward_activity()
        self.assertEqual(client.get(runId=attempt["taskId"])["activity"], first)
        renewal._forward_activity()
        events = [event for event in client.read_events(after=0)["events"] if event["kind"] == "attempt.activity"]
        self.assertEqual(len(events), 1, "a repeated sidecar is not published twice")
        self.assertEqual(
            [event for event in client.read_events(after=0)["events"] if event["kind"] == "attempt.progress"],
            [],
            "forwarding native activity never fabricates a prose progress event",
        )
        second = {
            "phase": "tool-running",
            "observedAt": "2026-01-01T00:00:02.000Z",
            "eventSeq": 2,
            "toolName": "write_file",
            "counts": {"toolCalls": 1},
        }
        sidecar.publish(second)
        renewal._forward_activity()
        self.assertEqual(client.get(runId=attempt["taskId"])["activity"], second)

    def test_an_unbound_or_malformed_sidecar_is_never_forwarded(self):
        board, client, worker, attempt, directory, renewal = self.live_attempt()
        path = directory / "activity.json"
        valid = {
            "version": activity_module.ACTIVITY_VERSION,
            "taskId": attempt["taskId"],
            "attemptId": attempt["attemptId"],
            "generation": attempt["generation"],
            "activity": {"phase": "tool-running", "observedAt": "2026-01-01T00:00:00.000Z", "eventSeq": 1},
        }
        _write(path, {**valid, "generation": attempt["generation"] + 1})
        renewal._forward_activity()
        _write(path, {**valid, "attemptId": "someone-else"})
        renewal._forward_activity()
        _write(path, {**valid, "activity": {**valid["activity"], "reasoning": "hidden"}})
        renewal._forward_activity()
        path.write_text("{broken")
        renewal._forward_activity()
        self.assertIsNone(client.get(runId=attempt["taskId"])["activity"])
        self.assertEqual(
            [event for event in client.read_events(after=0)["events"] if event["kind"] == "attempt.activity"], []
        )
        # A correctly bound sidecar is still accepted afterwards.
        _write(path, valid)
        renewal._forward_activity()
        self.assertEqual(client.get(runId=attempt["taskId"])["activity"], valid["activity"])
