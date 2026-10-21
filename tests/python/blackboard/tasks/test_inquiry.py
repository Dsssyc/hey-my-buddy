"""Board inquiry facts over a real Store/Actor/turn and registered C-Two seam.

The narrow JournalOwner replaces the native owner and execution endpoint transport,
not inquiry, actor validation, wire codecs, endpoint admission or projections.
It commits a private journal before settling requests and publishes through the
real DTO/journal layer. It is not an end-to-end or native receipt verification.
The native InquiryBridge lifecycle remains a separate C1 boundary.
"""
from __future__ import annotations
from admission import admit_execution
from protocol.fixtures import native_turn

from contextlib import nullcontext
import json
import os
import subprocess
import threading
import unittest
from unittest import mock
from pathlib import Path

from support import FIXTURE_CATALOG, BoardTestCase

from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.tasks.inquiry import (
    MAX_JOURNAL_BYTES,
    observe,
    read_journal,
)

from hey_my_buddy.buddy.harnesses import c_two_live as ctl
from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveChannel, CTwoLiveEndpoint
from hey_my_buddy.buddy.harnesses.inquiry_bridge import read_inquiry_journal
from hey_my_buddy.buddy.harnesses.live import (
    LiveCapabilities, LiveJournal, LiveObservation, LiveReply, LiveSnapshot, _journal_states,
)
from hey_my_buddy.buddy.roles.turn_io import input_hash
from hey_my_buddy.protocol.run_identity import RunIdentity
from buddy.harnesses.live_contract import HarnessRunLive


class ExecutionChannel:
    """Service-owned endpoint returns payloads at the execution boundary."""
    def __init__(self, native):
        self.native = native
        self.identity = native.identity

    def request(self, request, **kwargs):
        return self.native.request(request, **kwargs).to_payload()

    def observe(self, **kwargs):
        return self.native.observe(**kwargs).to_payload()

    def close(self, *, reason):
        self.native.close(reason=reason)


class OwnerConnection:
    """Fictional SDK peer dispatching the three named RPCs to the real owner."""

    def __init__(self, owner):
        self.owner = owner

    def _call(self, operation, text):
        self.owner.requests.append({"operation": operation, **json.loads(text)})
        if self.owner.disconnected:
            raise ConnectionError("the fixture execution endpoint is unavailable")
        # The owner publishes its source facts before the endpoint reads them;
        # the endpoint itself never opens a journal or drives a native process.
        self.owner.publish()
        if operation == "capabilities":
            return self.owner.endpoint.capabilities(text)
        if operation == "request":
            return self.owner.endpoint.request(text)
        return self.owner.endpoint.observe(text)

    def capabilities(self, text):
        return self._call("capabilities", text)

    def request(self, text):
        return self._call("request", text)

    def observe(self, text):
        return self._call("observe", text)


class JournalOwner:
    """A model-free owner fixture, with real durable writes and endpoint handoff.

    The production C1 reader checks source version/task/attempt/generation/turn.
    Signed native receipts, lifecycle and execution transport remain outside
    this fixture; the endpoint checks the complete transport RunIdentity.
    """

    def __init__(self, directory, identity):
        self.directory = directory
        self.journal_path = str(directory / "inquiry.results.jsonl")
        self.identity = identity
        self.token = "a" * 64
        self.requests = []
        self.refusal = None
        self.disconnected = False
        self.observation = {
            "ready": True, "observedAt": "2026-10-07T00:00:00.000Z", "sessionId": "s-1",
            "agentStatus": "running", "deliveryMode": "cooperative-checkpoint",
            "inbox": {"pending": 0, "delivered": 0, "answered": 0, "discarded": 0, "refused": 0},
            "activity": [{"at": "2026-10-07T00:00:00.000Z", "kind": "tool_call", "toolName": "bash"}],
        }
        capabilities = LiveCapabilities(inquiry_delivery="cooperative-checkpoint")
        self.endpoint = CTwoLiveEndpoint(identity, capabilities, HarnessRunLive,
                                         token=self.token, state_dir=Path(os.environ["HMB_STATE_DIR"]))
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.errors = []
        self.thread = threading.Thread(target=self._consume, name="inquiry-test-owner", daemon=True)
        self.thread.start()

    @property
    def bound(self):
        return {"version": 1, "taskId": self.identity.task_id, "attemptId": self.identity.attempt_id,
                "generation": self.identity.generation, "turnId": self.identity.turn_id}

    @property
    def entries(self):
        return read_journal(self.journal_path)["entries"]

    def _journal_fact(self):
        return read_inquiry_journal(self.identity, self.journal_path)

    def publish(self):
        with self.lock:
            source = self._journal_fact()
            self.endpoint.publish_journal(LiveJournal.from_payload(
                {key: source[key] for key in ("available", "reason", "entries", "rejections")}))
            # Pure production projection retains answer provenance through
            # _record_facts; only the endpoint owns sequencing and pagination.
            for state in _journal_states(source["records"]):
                self.endpoint.publish_inquiry_state(state)
            try:
                observation = LiveObservation.from_payload({
                    **{key: value for key, value in self.observation.items() if key != "activity"},
                    "recentActivity": self.observation.get("activity", []),
                })
            except BoardError:
                # A source/strict DTO failure clears the stale observation.
                # It supplies no answer, shutdown or look-alike metadata.
                self.endpoint.publish_snapshot(LiveSnapshot(
                    observed=False, reason="observation-unavailable"))
            else:
                self.endpoint.publish_observation(observation)

    def append(self, record):
        with self.lock:
            with open(self.journal_path, "a") as stream:
                stream.write(json.dumps({**self.bound, **record}) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            self.publish()

    def _consume(self):
        while not self.stopping.is_set():
            request = self.endpoint.consume_request(0.05)
            if request is None:
                continue
            try:
                if self.refusal:
                    reply = LiveReply(status="unavailable", reason_code=self.refusal, error_code=self.refusal)
                else:
                    question_id = request.payload.question_id
                    if question_id not in self.entries:
                        self.append({"inquiryId": question_id, "question": request.payload.question,
                                     "state": "queued"})
                    reply = LiveReply(status="queued", observed=True, state="queued")
                self.endpoint.settle_request(request.request_id, reply)
            except OSError:
                self.endpoint.settle_request(request.request_id, LiveReply(
                    status="unavailable", reason_code="journal-unavailable", error_code="journal-unavailable"))
            except Exception as error:
                self.errors.append(error)
                self.endpoint.settle_request(request.request_id, LiveReply(
                    status="unavailable", reason_code="internal", error_code="internal"))

    def activate(self, session_id):
        self.observation = {**self.observation, "sessionId": session_id}
        self.publish()

    def note_event(self, message, phase):
        self.observation = {**self.observation, "agentStatus": phase,
                            "activity": [{"at": "2026-10-07T00:00:00.000Z", "kind": "tool_call", "toolName": message["params"]["payload"]["toolName"]}]}
        self.publish()

    def deliver_inquiries(self, receipt, tool_call_id):
        # Fixture source facts only; verification of native signed receipts is C1.
        for entry in receipt["inquiries"]:
            self.append({**entry, "state": "delivered", "via": "tool:hey_my_buddy_checkpoint",
                         "toolCallId": tool_call_id})

    def record_answer(self, receipt, tool_call_id):
        self.append({"inquiryId": receipt["inquiryId"], "state": "answered",
                     "answer": {"text": receipt["answer"], "via": "tool:hey_my_buddy_answer_inquiry",
                                "toolCallId": tool_call_id}})

    def withdraw(self, question_id):
        self.append({"inquiryId": question_id, "state": "discarded",
                     "reason": "withdrawn by the asking side; it no longer blocks turn completion"})

    def settle(self):
        for question_id, entry in self.entries.items():
            if entry["state"] == "queued":
                self.append({"inquiryId": question_id, "state": "unavailable",
                             "reason": "the owned turn ended", "limitation": "cooperative fixture limitation"})

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=2)
        self.endpoint.close(reason="fixture cleanup")
        if self.thread.is_alive() or self.errors:
            raise AssertionError(f"owner cleanup failed: {self.errors!r}")


def attach_owner(case, board, client, task, adapter):
    nonce = "a" * 32
    claim = admit_execution(client, nonce)["claim"]
    turn, attempt = claim["turn"], claim["attempt"]
    case._claim = claim
    case.assertIsNotNone(turn)
    with board.store.db.read() as db:
        row = db.execute("SELECT input_json, input_sha256 FROM workflow_turns WHERE turn_id=?", (turn["turnId"],)).fetchone()
        case.assertIsNone(row["input_sha256"], "the running receipt-time digest is allowed to remain NULL")
        case.assertEqual(json.loads(row["input_json"]), turn["input"])
    identity = RunIdentity(task_id=task["runId"], attempt_id=attempt["attemptId"],
                           generation=attempt["generation"], invocation_id="inquiry-fixture-invocation",
                           turn_id=turn["turnId"], input_sha256=turn["inputSha256"])
    case.assertEqual(identity.input_sha256, input_hash(turn["input"]))
    directory = case.directory / "attempts" / task["runId"] / attempt["attemptId"]
    directory.mkdir(parents=True, exist_ok=True)
    owner = JournalOwner(directory, identity)
    case.addCleanup(owner.close)
    peer = OwnerConnection(owner)
    def connect(*args, timeout, **kwargs):
        case.assertGreaterEqual(timeout, 0)
        return nullcontext(peer)
    def with_call_options(connection, *, timeout):
        case.assertIs(connection, peer)
        case.assertGreaterEqual(timeout, 0)
        return connection
    case.enterContext(mock.patch.object(ctl.cc, "connect", side_effect=connect))
    case.enterContext(mock.patch.object(ctl.cc, "with_call_options", side_effect=with_call_options))
    channel = ExecutionChannel(CTwoLiveChannel(
        identity, HarnessRunLive, name='inquiry fixture', address='fixture://execution-live',
        instance_id=owner.endpoint.instance_id, token=owner.token, state_dir=Path(os.environ["HMB_STATE_DIR"])))
    board.live_registry.bind(attempt['attemptId'], channel)
    case._turn_id = identity.turn_id
    channel = board.live_registry.channel_for(board.store.task_get({"runId": task["runId"]})["task"])
    case.assertEqual(channel.native.capabilities().inquiry_delivery, "cooperative-checkpoint")
    case.assertEqual(channel.identity, identity)
    return task, attempt, owner


class TestInquiry(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.catalog_fixture()

    def _submit(self, client, cwd, *, request_id, harness="dsh", provider="deepseek-official",
                model="deepseek-flash", effort="off"):
        environment = {**os.environ, "GIT_CONFIG_SYSTEM": "/dev/null"}
        for arguments in (("init", "-q"), ("-c", "user.name=Buddy Test", "-c", "user.email=buddy@example.invalid",
                                           "commit", "--allow-empty", "-qm", "inquiry fixture")):
            completed = subprocess.run(["git", *arguments], cwd=cwd, env=environment, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
        submitted = client.workflow_submit(
            requestId=request_id, hostId="inquiry-test-host", task="do", cwd=str(cwd), harness=harness,
            provider=provider, model=model, effort=effort,
            executionWorkspace={"kind": "existing", "access": "read"},
        )
        self.assertEqual(submitted["task"]["lineage"]["kind"], "micro-task")
        self._submission_control = submitted["control"]
        return client.get(runId=submitted["runId"])

    def _submit_dsh(self, client, cwd, *, request_id):
        return self._submit(client, cwd, request_id=request_id)

    def _attempt_directory(self, board, client):
        task = self._submit(client, self.workdir(), request_id="inq-task")
        return attach_owner(self, board, client, task, "dsh")

    def _bound_fields(self, task, attempt):
        return {"version": 1, "taskId": task["runId"], "attemptId": attempt["attemptId"],
                "generation": attempt["generation"], "turnId": self._turn_id, "sessionId": "s-1"}

    def test_observation_uses_the_real_bridge_protocol(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._attempt_directory(board, client)
        try:
            result = board.call("inquiry_observe", {"runId": task["runId"]})
            self.assertTrue(result["bridge"]["observed"])
            self.assertTrue(result["live"]["available"])
            self.assertEqual(result["live"]["sessionId"], "s-1")
            self.assertEqual(result["live"]["deliveryMode"], "cooperative-checkpoint")
            self.assertEqual(result["live"]["activity"][0]["toolName"], "bash")
            self.assertEqual(result["phase"], "active")
            self.assertEqual(result["inflight"] if "inflight" in result else result["execution"]["attemptState"], "starting")
            self.assertEqual({request["operation"] for request in bridge.requests}, {"capabilities", "observe"})
            self.assertEqual(bridge.requests[-1]["identity"], bridge.identity.to_payload())
            self.assertEqual(bridge.requests[-1]["instanceId"], bridge.endpoint.instance_id)
        finally:
            bridge.close()

    def test_observation_does_not_publish_native_tool_arguments(self):
        board = self.board()
        task, _attempt, bridge = self._attempt_directory(board, board.client())
        try:
            # A peer that smuggles native tool arguments inside its activity
            # metadata loses its whole observation: the channel's strict live
            # model refuses the look-alike value instead of sanitizing it, and
            # nothing private is ever published.
            bridge.observation = {"activity": [{"at": "2026-10-07T00:00:00.000Z", "kind": "tool_call",
                                                "toolName": "bash",
                                                "argumentPreview": "private-provider-key",
                                                "nested": {"prompt": "private-prompt"}}]}
            result = board.call("inquiry_observe", {"runId": task["runId"]})
            self.assertFalse(result["bridge"]["observed"])
            self.assertEqual(result["bridge"]["reason"], "observation-unavailable")
            self.assertFalse(result["live"]["available"])
            self.assertNotIn("private-provider-key", json.dumps(result))
            self.assertNotIn("private-prompt", json.dumps(result))
            self.assertNotIn(bridge.token, json.dumps(result))
            self.assertNotIn("a" * 32, json.dumps(result))
            self.assertNotIn("fixture://execution-live", json.dumps(result))
        finally:
            bridge.close()

    def test_a_question_is_correlated_and_a_wrong_token_is_refused(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._attempt_directory(board, client)
        try:
            result = board.call(
                "inquiry_observe",
                {"runId": task["runId"], "inquiryId": "q-live", "question": "what is blocking you?"},
            )
            # Cooperative-checkpoint delivery: the ask itself only ever queues;
            # delivery and answers arrive through the journal.
            self.assertEqual(result["inquiry"]["state"], "queued")
            self.assertEqual(result["inquiry"]["correlation"], "inquiryId")
            request = next(item for item in bridge.requests if item["operation"] == "request")
            self.assertEqual(request["payload"]["questionId"], "q-live")
            self.assertEqual(bridge.entries["q-live"]["state"], "queued")
            # A bridge that rejects the token is reported, never silently treated as an answer.
            bridge.endpoint._token = "b" * 64
            second = board.call(
                "inquiry_observe",
                {"runId": task["runId"], "inquiryId": "q-two", "question": "and now?"},
            )
            self.assertEqual(second["bridge"]["reason"], "token-mismatch")
            self.assertIsNone(second["bridge"]["error"])
            self.assertNotIn("q-two", bridge.entries)
            self.assertEqual(client.get_message("q-two", runId=task["runId"])["state"], "queued",
                             "an unauthorized ask is a retryable refusal, never a terminal answer")
        finally:
            bridge.close()

    def test_the_real_bridge_journal_shape_is_imported_with_reply_tool_evidence(self):
        """Nested source answer facts keep their checkpoint tool evidence."""
        board = self.board()
        client = board.client()
        task, attempt, bridge = self._attempt_directory(board, client)
        bound = self._bound_fields(task, attempt)
        journal = Path(bridge.directory) / "inquiry.results.jsonl"
        journal.write_text(
            json.dumps({**bound, "inquiryId": "q-real", "state": "queued",
                        "question": "status?", "questionSha256": "f" * 64,
                        "delivery": {"requestedDelivery": None, "admittedDelivery": "cooperative-checkpoint",
                                     "startsNewTurn": False, "extendsDeadline": False, "supported": True}})
            + "\n"
            + json.dumps({**bound, "inquiryId": "q-real", "state": "delivered",
                          "deliveredAt": "2026-10-07T05:00:00.000Z", "via": "tool:hey_my_buddy_checkpoint",
                          "toolCallId": "call-41"})
            + "\n"
            + json.dumps({**bound, "inquiryId": "q-real", "state": "answered",
                          "answeredAt": "2026-10-07T05:00:04.000Z",
                          "answer": {"text": "the tests are still running", "bytes": 27,
                                     "via": "tool:hey_my_buddy_answer_inquiry", "toolCallId": "call-42",
                                     "at": "2026-10-07T05:00:04.000Z", "truncated": False}})
            + "\n"
        )
        try:
            board.call("inquiry_observe", {"runId": task["runId"], "inquiryId": "q-real", "question": "status?"})
            message = client.get_message("q-real", runId=task["runId"])
            self.assertEqual(message["state"], "answered")
            self.assertEqual(message["answer"]["text"], "the tests are still running")
            self.assertEqual(message["answer"]["via"], "tool:hey_my_buddy_answer_inquiry")
            self.assertEqual(message["answer"]["toolCallId"], "call-42")
            self.assertEqual(message["answer"]["at"], "2026-10-07T05:00:04.000Z")
            self.assertEqual(message["answer"]["source"], "bridge-journal")
            # The shared bridge's delivery record is journal transport evidence
            # whose keys are not the message store's bounded injection-delivery
            # keys; the correlation that survives publicly is the reply-tool
            # evidence asserted above (the retired Node bridge's messageId
            # correlation went with its channel).
            # Re-importing the same journal is idempotent.
            board.call("inquiry_observe", {"runId": task["runId"], "inquiryId": "q-real", "question": "status?"})
            self.assertEqual(client.get_message("q-real", runId=task["runId"])["answer"]["text"], "the tests are still running")
        finally:
            bridge.close()

    def test_an_answered_journal_entry_without_text_never_becomes_answered(self):
        board = self.board()
        client = board.client()
        task, attempt, bridge = self._attempt_directory(board, client)
        bound = self._bound_fields(task, attempt)
        journal = Path(bridge.directory) / "inquiry.results.jsonl"
        journal.write_text(
            json.dumps({**bound, "inquiryId": "q-empty", "state": "delivered",
                        "via": "tool:hey_my_buddy_checkpoint", "toolCallId": "call-1"})
            + "\n"
            + json.dumps({**bound, "inquiryId": "q-empty", "state": "answered",
                          "via": "tool:hey_my_buddy_answer_inquiry"})
            + "\n"
        )
        try:
            board.call("inquiry_observe", {"runId": task["runId"], "inquiryId": "q-empty", "question": "status?"})
            message = client.get_message("q-empty", runId=task["runId"])
            self.assertEqual(message["state"], "delivered", "answered without usable text must not be recorded")
            self.assertIsNone(message["answer"])
            self.assertEqual(message["reason"], {"code": "inquiry-answer-empty", "params": {}})
        finally:
            bridge.close()

    def test_the_journal_is_idempotent_transport_evidence(self):
        board = self.board()
        client = board.client()
        task, attempt, bridge = self._attempt_directory(board, client)
        bound = self._bound_fields(task, attempt)
        journal = Path(bridge.directory) / "inquiry.results.jsonl"
        journal.write_text(
            json.dumps({**bound, "inquiryId": "q-journal", "state": "delivered"})
            + "\n"
            + json.dumps({**bound, "inquiryId": "q-journal", "state": "answered",
                          "answer": {"text": "from the journal"}})
            + "\n"
            + "{torn line\n"
        )
        try:
            board.call("inquiry_observe", {"runId": task["runId"], "inquiryId": "q-journal", "question": "hello?"})
            message = client.get_message("q-journal", runId=task["runId"])
            self.assertEqual(message["answer"]["text"], "from the journal")
            self.assertEqual(message["answer"]["source"], "bridge-journal")
            second = board.call(
                "inquiry_observe", {"runId": task["runId"], "inquiryId": "q-journal", "question": "hello?"}
            )
            self.assertTrue(second["inquiry"]["duplicate"])
            self.assertEqual(second["journal"]["entries"], 1)
        finally:
            bridge.close()

    def test_journal_reading_is_bounded_and_never_throws(self):
        directory = self.workdir("journal")
        self.assertEqual(read_journal(None)["reason"], "no-journal-path")
        self.assertEqual(read_journal(str(directory / "absent.jsonl"))["reason"], "journal-not-written")
        big = directory / "big.jsonl"
        big.write_bytes(b"x" * (MAX_JOURNAL_BYTES + 1))
        self.assertEqual(read_journal(str(big))["reason"], "journal-exceeds-limit")

    def test_an_attempt_without_a_bridge_reports_the_reason(self):
        board = self.board()
        client = board.client()
        dsh_task = self._submit_dsh(client, self.workdir(), request_id="no-bridge")
        result = board.call("inquiry_observe", {"runId": dsh_task["runId"]})
        self.assertFalse(result["bridge"]["enabled"])
        self.assertEqual(result["bridge"]["reason"], "channel-unbound")
        command_task = board.submit_execution(
            requestId="no-bridge-command",
            task="do",
            cwd=str(self.workdir("other")),
            adapter="command",
            argv=["/bin/true"],
        )["task"]
        command_result = board.call("inquiry_observe", {"runId": command_task["runId"]})
        self.assertIsNone(command_result["bridge"]["reason"])
        self.assertIn("agentStatus", result["live"]["unavailable"])
        self.assertEqual(result["limits"]["maxQuestionBytes"], 4000)
        self.assertEqual(result["limits"]["maxInquiriesPerRun"], 32)
        self.assertEqual(result["expectedSeconds"], 1800)
        self.assertNotIn("deadline", result)

    def test_command_expected_duration_is_visible_without_an_execution_deadline(self):
        board = self.board()
        task = board.submit_execution(
            requestId="expected-inquiry", task="a long task", cwd=str(self.workdir()),
            adapter="command", argv=["/bin/true"], expectedSeconds=7,
        )["task"]
        result = board.call("inquiry_observe", {"runId": task["runId"]})
        self.assertEqual(result["expectedSeconds"], 7)
        self.assertNotIn("deadline", result)

    def test_execution_endpoint_loss_is_unavailable_not_shutdown(self):
        board = self.board()
        client = board.client()
        task, _attempt, owner = self._attempt_directory(board, client)
        owner.disconnected = True
        result = board.call("inquiry_observe", {"runId": task["runId"],
                            "inquiryId": "q-lost", "question": "status?"})
        self.assertEqual(result["bridge"]["reason"], "transport-unreachable")
        self.assertFalse(result["bridge"]["observed"])
        self.assertEqual(result["status"], "running")
        self.assertFalse(result["execution"]["shutdownConfirmed"])
        self.assertIsNone(client.get_message("q-lost", runId=task["runId"])["answer"])
        self.assertEqual(client.get_message("q-lost", runId=task["runId"])["state"], "queued")

    def test_an_unsupported_adapter_answers_with_a_capability_error(self):
        board = self.board()
        client = board.client()
        task = board.submit_execution(
            requestId="ext-inq", task="do", cwd=str(self.workdir()), adapter="command", argv=["/bin/true"]
        )["task"]
        result = board.call("inquiry_observe", {"runId": task["runId"]})
        self.assertFalse(result["bridge"]["enabled"])
        self.assertIsNone(result["bridge"]["reason"])

    def test_inquiry_capability_comes_from_the_adapter_registry(self):
        from hey_my_buddy.blackboard.tasks.inquiry import inquiry_capable

        from hey_my_buddy.blackboard.tasks.inquiry import observe_capable

        # DSH and ZCode both deliver questions at their session's cooperative
        # tool checkpoints. Neither capability is inferred by probing a native CLI.
        self.assertTrue(inquiry_capable("dsh"))
        self.assertTrue(observe_capable("dsh"))
        self.assertTrue(inquiry_capable("zcode"))
        self.assertTrue(observe_capable("zcode"))
        for name in ("codex", "claude"):
            self.assertTrue(inquiry_capable(name))
            self.assertTrue(observe_capable(name))
        for name in ("command", "external", "not-an-adapter"):
            with self.subTest(adapter=name):
                self.assertFalse(inquiry_capable(name))
                self.assertFalse(observe_capable(name))

    def test_journal_failure_refuses_the_question_without_ending_execution(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._attempt_directory(board, client)
        try:
            # A directory at the journal path produces a real read/open failure;
            # the owner cannot commit, so it must never acknowledge queued.
            Path(bridge.journal_path).mkdir()
            result = board.call("inquiry_observe", {
                "runId": task["runId"], "inquiryId": "q-journal-failed", "question": "Status?",
            })
            self.assertEqual(result["bridge"]["error"], "journal-unavailable")
            message = client.get_message("q-journal-failed", runId=task["runId"])
            self.assertEqual(message["state"], "unavailable")
            self.assertEqual(message["reason"], {"code": "bridge-refused", "params": {"errorCode": "journal-unavailable"}})
            self.assertEqual(client.get(runId=task["runId"])["state"], "running")
        finally:
            bridge.close()

    def test_a_question_refused_after_the_turn_ended_is_recorded_unavailable(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._attempt_directory(board, client)
        # An explicit owner refusal reports its real code. This does not
        # supply process shutdown evidence or open a replacement model turn.
        bridge.refusal = "agent-gone"
        try:
            result = board.call(
                "inquiry_observe",
                {"runId": task["runId"], "inquiryId": "q-ended", "question": "still running?"},
            )
            self.assertEqual(result["bridge"]["error"], "agent-gone")
            message = client.get_message("q-ended", runId=task["runId"])
            self.assertEqual(message["state"], "unavailable")
            self.assertEqual(message["reason"], {"code": "bridge-refused", "params": {"errorCode": "agent-gone"}})
            self.assertIsNone(message["answer"])
        finally:
            bridge.close()

    def test_a_journal_record_bound_to_another_task_can_never_answer(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._attempt_directory(board, client)
        journal = Path(bridge.directory) / "inquiry.results.jsonl"
        journal.write_text(json.dumps({
            "version": 1, "taskId": "another-task", "attemptId": "another-attempt", "generation": 0,
            "turnId": "other-turn", "inquiryId": "q-foreign", "state": "answered",
            "answer": {"text": "answer that belongs to a different run"},
            "via": "tool:hey_my_buddy_answer_inquiry",
        }) + "\n")
        try:
            result = board.call(
                "inquiry_observe",
                {"runId": task["runId"], "inquiryId": "q-foreign", "question": "who are you?"},
            )
            self.assertEqual(result["bridge"]["journalRejected"], "journal-task-mismatch")
            message = client.get_message("q-foreign", runId=task["runId"])
            self.assertIsNone(message["answer"], "a foreign journal record must never be imported as an answer")
        finally:
            bridge.close()

    def test_question_replay_and_conflict_never_requeue_the_owner(self):
        board = self.board()
        client = board.client()
        task, _attempt, owner = self._attempt_directory(board, client)
        params = {"runId": task["runId"], "inquiryId": "q-replay", "question": "status?"}
        board.call("inquiry_observe", params)
        first = Path(owner.journal_path).read_bytes()
        second = board.call("inquiry_observe", params)
        self.assertTrue(second["inquiry"]["duplicate"])
        self.assertEqual(Path(owner.journal_path).read_bytes(), first)
        with self.assertRaises(BoardError) as error:
            board.call("inquiry_observe", {**params, "question": "changed question"})
        self.assertEqual(error.exception.code, "CONFLICT")
        self.assertEqual(Path(owner.journal_path).read_bytes(), first)
        self.assertEqual(client.get_message("q-replay", runId=task["runId"])["state"], "queued")
        self.assertEqual(board.call("inquiry_observe", {"runId": task["runId"]})["execution"]["attemptId"],
                         owner.identity.attempt_id)

    def test_execution_endpoint_rejects_foreign_identity_and_token_before_owner_commit(self):
        from types import SimpleNamespace
        from hey_my_buddy.buddy.runtime.execution import _ExecutionLive

        board = self.board()
        client = board.client()
        task, attempt, owner = self._attempt_directory(board, client)
        live = _ExecutionLive(SimpleNamespace(claim={
            "task": {"taskId": task["runId"]}, "attempt": attempt,
            "turn": {"turnId": owner.identity.turn_id},
        }))
        frame = {"identity": live.identity.to_payload(), "instanceId": live.instance_id,
                 "token": live.token}
        self.assertEqual(live._auth(json.dumps(frame), set()), frame)
        for key, value in (("taskId", "foreign-task"), ("attemptId", "foreign-attempt"),
                           ("generation", live.identity.generation + 1),
                           ("invocationId", "foreign-invocation"), ("turnId", "foreign-turn")):
            with self.subTest(field=key):
                bad = {**frame, "identity": {**frame["identity"], key: value}}
                with self.assertRaises(BoardError) as caught:
                    live._auth(json.dumps(bad), set())
                self.assertEqual(caught.exception.code, "LIVE_UNAVAILABLE")
        for field, value in (("instanceId", "b" * 64), ("token", "b" * 64)):
            with self.subTest(field=field):
                with self.assertRaises(BoardError) as caught:
                    live._auth(json.dumps({**frame, field: value}), set())
                self.assertEqual(caught.exception.code, "LIVE_UNAVAILABLE")
        self.assertEqual(owner.entries, {})
        self.assertEqual(client.get(runId=task["runId"])["state"], "running")

    def test_wait_for_an_unpublished_id_does_not_invent_an_answer_or_new_turn(self):
        board = self.board()
        task, _attempt, owner = self._attempt_directory(board, board.client())
        owner.refusal = "timeout"
        result = board.call("inquiry_observe", {"runId": task["runId"], "inquiryId": "q-wait",
                            "question": "status?", "waitMs": 100})
        self.assertEqual(result["inquiry"]["state"], "queued")
        self.assertFalse(result["inquiry"]["answer"]["available"])
        point_queries = [item for item in owner.requests if item.get("inquiryId") == "q-wait"]
        self.assertTrue(point_queries)
        self.assertEqual(owner.entries, {})
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM attempts WHERE task_id=?", (task["runId"],)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM workflow_turns WHERE run_id=?", (task["runId"],)).fetchone()[0], 1)

    def test_question_refused_after_root_end_cannot_remain_queued(self):
        board = self.board()
        task, _attempt, owner = self._attempt_directory(board, board.client())
        owner.refusal = "agent-not-running"
        result = board.call("inquiry_observe", {"runId": task["runId"], "inquiryId": "q-ended",
                                               "question": "Did the root end?"})
        self.assertEqual(result["inquiry"]["state"], "unavailable")
        self.assertEqual(owner.entries, {})
        self.assertEqual(board.client().get(runId=task["runId"])["state"], "running")

    def test_unreadable_and_oversized_live_journals_publish_only_their_availability(self):
        board = self.board()
        task, _attempt, owner = self._attempt_directory(board, board.client())
        journal = Path(owner.journal_path)
        journal.mkdir()
        unreadable = board.call("inquiry_observe", {"runId": task["runId"]})
        self.assertEqual(unreadable["journal"], {"available": False, "reason": "journal-unreadable", "entries": 0})
        # Keep the original failure material; select a new source path instead
        # of deleting or overwriting the previous experiment object.
        owner.journal_path = str(owner.directory / "oversized.results.jsonl")
        Path(owner.journal_path).write_bytes(b"x" * (MAX_JOURNAL_BYTES + 1))
        oversized = board.call("inquiry_observe", {"runId": task["runId"]})
        self.assertEqual(oversized["journal"], {"available": False, "reason": "journal-exceeds-limit", "entries": 0})
        self.assertEqual(oversized["status"], "running")
        self.assertFalse(oversized["execution"]["shutdownConfirmed"])

    def test_terminal_history_reads_only_this_attempt_without_a_live_call_or_new_turn(self):
        self._assert_terminal_history(concluded=False)

    def test_concluded_history_reads_saved_answers_without_a_live_call(self):
        self._assert_terminal_history(concluded=True)

    def _assert_terminal_history(self, *, concluded):
        board = self.board()
        client = board.client()
        task, attempt, owner = self._attempt_directory(board, client)
        owner.append({"inquiryId": "q-terminal", "state": "answered",
                      "answer": {"text": "durable history", "via": "tool:hey_my_buddy_answer_inquiry",
                                 "toolCallId": "terminal-answer"}})
        owner.append({"inquiryId": "q-foreign-terminal", "taskId": "foreign-task", "state": "answered",
                      "answer": {"text": "foreign history"}})
        owner.append({"inquiryId": "q-foreign-attempt", "attemptId": "foreign-attempt", "state": "answered",
                      "answer": {"text": "foreign attempt history"}})
        owner.close()  # the fixture's only owned execution thread has really stopped
        client.submit_result(attempt["attemptId"], attempt["generation"], "a" * 32, {"status": "ok", "result": {"finalText": "fixture owner stopped"},
                              "shutdownConfirmed": True, "exitCode": 0})
        if concluded:
            closed = board.call("workflow_conclude", {"runId": task["runId"],
                "note": "Stopped inquiry fixture", **self._submission_control})
            self.assertEqual(closed["state"], "concluded")
        before = len(owner.requests)
        result = board.call("inquiry_observe", {"runId": task["runId"], "inquiryId": "q-terminal",
                            "question": "what was the last answer?", "waitMs": 100})
        self.assertEqual(result["phase"], "terminal")
        self.assertIsNone(result["bridge"]["reason"])
        self.assertEqual(result["inquiry"]["state"], "answered")
        self.assertEqual(result["inquiry"]["answer"]["text"], "durable history")
        self.assertEqual(result["inquiry"]["answer"]["source"], "bridge-journal")
        self.assertEqual(result["inquiry"]["answer"]["toolCallId"], "terminal-answer")
        for question_id, label in (("q-foreign-terminal", "task"), ("q-foreign-attempt", "attempt")):
            with self.subTest(source=label):
                rejected = board.call("inquiry_observe", {"runId": task["runId"],
                                      "inquiryId": question_id, "question": "historical probe"})
                self.assertEqual(rejected["bridge"]["journalRejected"], f"journal-{label}-mismatch")
                self.assertFalse(rejected["inquiry"]["answer"]["available"])
        self.assertEqual(len(owner.requests), before)
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM attempts WHERE task_id=?", (task["runId"],)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM workflow_turns WHERE run_id=?", (task["runId"],)).fetchone()[0], 1)

    def test_run_observation_imports_all_existing_live_questions_only(self):
        board = self.board()
        client = board.client()
        task, _attempt, owner = self._attempt_directory(board, client)
        for question_id, state in (("q-delivered", "delivered"), ("q-answered", "answered"),
                                   ("q-foreign", "answered")):
            board.store._message_post(task["runId"], question_id, "status?")
            if question_id == "q-foreign":
                # The endpoint retains this prior projection; the latest source
                # rejection must fence it for both run-only and targeted reads.
                owner.append({"inquiryId": question_id, "state": "answered",
                              "answer": {"text": "stale answer"}})
            owner.append({"inquiryId": question_id, "state": state,
                          **({"taskId": "another-task"} if question_id == "q-foreign" else {}),
                          **({"answer": {"text": "native answer"}} if state == "answered" else {})})
        owner.append({"inquiryId": "q-orphan", "state": "answered", "answer": {"text": "orphan"}})
        result = board.call("inquiry_observe", {"runId": task["runId"]})
        self.assertTrue(result["bridge"]["observed"])
        self.assertEqual(result["journal"]["rejections"]["q-orphan"], "journal-question-unknown")
        self.assertEqual(client.get_message("q-delivered", runId=task["runId"])["state"], "delivered")
        self.assertEqual(client.get_message("q-answered", runId=task["runId"])["answer"]["text"], "native answer")
        self.assertEqual(client.get_message("q-foreign", runId=task["runId"])["state"], "queued")
        rejected = board.call("inquiry_observe", {"runId": task["runId"],
                              "inquiryId": "q-foreign", "question": "status?"})
        self.assertEqual(rejected["bridge"]["journalRejected"], "journal-task-mismatch")
        self.assertNotIn("journalImported", rejected["bridge"])
        self.assertEqual(client.get_message("q-foreign", runId=task["runId"])["state"], "queued")
        with self.assertRaises(BoardError) as error:
            client.get_message("q-orphan", runId=task["runId"])
        self.assertEqual(error.exception.code, "NOT_FOUND")

    def test_finished_attempt_at_host_boundary_imports_unavailable_history(self):
        for repeat_id in (False, True):
            with self.subTest(repeat_id=repeat_id):
                board = self.board()
                client = board.client()
                task = self._submit(client, self.workdir(f"ended-{repeat_id}"), request_id=f"inq-ended-{repeat_id}")
                task, attempt, owner = attach_owner(self, board, client, task, "dsh")
                board.store._message_post(task["runId"], "q-ended", "status?")
                reason = "the native root turn ended before this inquiry was answered: " + "x" * 245
                owner.append({"inquiryId": "q-ended", "state": "unavailable", "reason": reason,
                              "limitation": "generic bridge delivery description"})
                owner.close()
                outcome = {"disposition": "attention", "summary": "Host review needed", "remaining": ["review"],
                           "artifacts": [], "decisions": [], "request": {
                               "summary": "review", "attempted": "bounded work", "neededWork": ["review"],
                               "expectedArtifacts": ["reviewed work"], "acceptance": "Host accepts"}}
                record = native_turn.claim_record(self._claim, outcome=outcome)
                client.submit_result(attempt["attemptId"], attempt["generation"], "a" * 32,
                                     {"status": "ok", "result": {"turn": record},
                                      "shutdownConfirmed": True, "exitCode": 0})
                view = client.get(runId=task["runId"])
                self.assertEqual(view["state"], "queued")
                self.assertEqual(view["selectedAttempt"]["executionState"], "finished")
                self.assertEqual(board.call("workflow_get", {"runId": task["runId"]})["state"], "awaiting-host")
                before = len(owner.requests)
                result = board.call("inquiry_observe", {"runId": task["runId"],
                    **({"inquiryId": "q-ended", "question": "status?"} if repeat_id else {})})
                self.assertTrue(result["journal"]["available"])
                message = client.get_message("q-ended", runId=task["runId"])
                self.assertEqual(message["state"], "unavailable")
                self.assertEqual(message["reason"], {"code": "inquiry-root-turn-ended", "params": {}})
                self.assertEqual(len(owner.requests), before)

    def test_an_observation_source_loss_removes_stale_public_facts_without_stopping(self):
        board = self.board()
        task, _attempt, owner = self._attempt_directory(board, board.client())
        initial = board.call("inquiry_observe", {"runId": task["runId"]})
        self.assertTrue(initial["live"]["available"])
        owner.observation = {}
        lost = board.call("inquiry_observe", {"runId": task["runId"]})
        self.assertFalse(lost["bridge"]["observed"])
        self.assertEqual(lost["bridge"]["reason"], "observation-unavailable")
        self.assertFalse(lost["live"]["available"])
        self.assertNotIn("sessionId", lost["live"])
        self.assertEqual(lost["status"], "running")
        self.assertFalse(lost["execution"]["shutdownConfirmed"])
        self.assertEqual(lost["execution"]["attemptId"], initial["execution"]["attemptId"])

    def test_the_last_foreign_source_record_rejects_every_journal_binding_component(self):
        board = self.board()
        client = board.client()
        task, _attempt, owner = self._attempt_directory(board, client)
        # Neither the owner nor the endpoint has published the earlier legal
        # answer: the final source record must reject it before publication.
        changed = (("version", 2, "journal-attempt-unbound"),
                   ("taskId", "foreign-task", "journal-task-mismatch"),
                   ("attemptId", "foreign-attempt", "journal-attempt-mismatch"),
                   ("generation", owner.identity.generation + 1, "journal-attempt-unbound"),
                   ("turnId", "foreign-turn", "journal-attempt-unbound"))
        rows = []
        for index, (key, value, _reason) in enumerate(changed):
            legal = {**owner.bound, "inquiryId": f"q-source-{index}", "state": "answered",
                     "answer": {"text": "earlier legal answer"}}
            rows.extend((legal, {**legal, key: value, "answer": {"text": "foreign answer"}}))
        Path(owner.journal_path).write_text("".join(json.dumps(row) + "\n" for row in rows) + "{torn line\n")
        for index, (_key, _value, reason) in enumerate(changed):
            with self.subTest(source=_key):
                question_id = f"q-source-{index}"
                result = board.call("inquiry_observe", {"runId": task["runId"],
                                    "inquiryId": question_id, "question": "source probe"})
                self.assertTrue(result["bridge"]["observed"])
                self.assertIsNone(result["bridge"]["reason"])
                self.assertIsNone(result["bridge"]["error"])
                self.assertIn(reason, result["bridge"]["journalRejected"])
                self.assertEqual(result["journal"], {"available": True, "reason": None, "entries": 5,
                    "rejections": {f"q-source-{i}": item[2] for i, item in enumerate(changed)}})
                message = client.get_message(question_id, runId=task["runId"])
                self.assertEqual(message["state"], "queued")
                self.assertIsNone(message["answer"])
                self.assertEqual(result["status"], "running")
                self.assertFalse(result["execution"]["shutdownConfirmed"])

    def test_message_reasons_are_internal_and_answers_clear_them(self):
        from hey_my_buddy.blackboard.tasks.inquiry import _apply_journal, _mark_unavailable
        board = self.board()
        client = board.client()
        inactive = board.submit_execution(requestId="inactive-question", task="fixture",
            cwd=str(self.workdir("inactive")), adapter="command", argv=["true"])["task"]
        posted = board.store._message_post(inactive["runId"], "q-inactive", "status?")["message"]
        self.assertEqual(posted["reason"], {"code": "inquiry-no-live-agent", "params": {}})
        client.cancel(runId=inactive["runId"])
        task, _attempt, _owner = self._attempt_directory(board, client)
        board.store._message_post(task["runId"], "q-late", "status?")
        _mark_unavailable(board.store, task["runId"], "q-late", "agent-gone")
        message = client.get_message("q-late", runId=task["runId"])
        self.assertEqual(message["reason"], {"code": "bridge-refused", "params": {"errorCode": "agent-gone"}})
        self.assertNotIn("note", message)
        _apply_journal(board.store, task["runId"], "q-late", {"state": "answered", "answer": {"text": "late answer"}})
        message = client.get_message("q-late", runId=task["runId"])
        self.assertEqual(message["state"], "answered")
        self.assertIsNone(message["reason"])
        self.assertNotIn("note", message)

    def test_message_reason_checks_reject_invalid_pairs_params_and_states(self):
        import sqlite3
        board = self.board()
        client = board.client()
        task, _attempt, _owner = self._attempt_directory(board, client)
        board.store._message_post(task["runId"], "q-check", "status?")
        with board.store.db.read() as connection:
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(messages)')}
        self.assertFalse({'reason', 'note'} & columns)
        self.assertTrue({'reason_code', 'reason_params_json'} <= columns)
        for code, params, state in ((None, None, "unavailable"),
                                    ("inquiry-answer-empty", None, "delivered"),
                                    (None, "{}", "delivered"),
                                    ("inquiry-answer-empty", "[]", "delivered"),
                                    ("inquiry-answer-empty", "{}", "queued"),
                                    ("inquiry-answer-empty", "{}", "claimed"),
                                    ("inquiry-answer-empty", "{}", "discarded"),
                                    ("inquiry-answer-empty", "{}", "answered")):
            with self.subTest(code=code, params=params, state=state), self.assertRaises(sqlite3.IntegrityError):
                with board.store.db.write() as connection:
                    connection.execute("UPDATE messages SET reason_code=?,reason_params_json=?,state=? WHERE inquiry_id='q-check'",
                                       (code, params, state))

    def test_unknown_journal_questions_are_rejected_and_other_errors_propagate(self):
        from hey_my_buddy.blackboard.tasks.inquiry import _apply_journal, _apply_bridge_answer, _mark_unavailable
        board = self.board()
        client = board.client()
        task, _attempt, _owner = self._attempt_directory(board, client)
        self.assertEqual(_apply_journal(board.store, task["runId"], "q-orphan", {"state": "delivered"}), "journal-question-unknown")
        board.store._message_post(task["runId"], "q-owned", "status?")
        for apply in (_apply_journal, _apply_bridge_answer):
            with mock.patch.object(board.store, "_message_update", side_effect=BoardError("CONFLICT", "test failure")), self.assertRaises(BoardError):
                apply(board.store, task["runId"], "q-owned", {"state": "delivered"})
        with mock.patch.object(board.store, "_message_update", side_effect=BoardError("CONFLICT", "test failure")), self.assertRaises(BoardError):
            _mark_unavailable(board.store, task["runId"], "q-owned", "agent-gone")

    def test_observe_has_no_explanatory_note_or_unavailable_answer_sentence(self):
        board = self.board()
        client = board.client()
        task, _attempt, _owner = self._attempt_directory(board, client)
        result = board.call("inquiry_observe", {"runId": task["runId"], "inquiryId": "q-shape", "question": "status?"})
        self.assertNotIn("note", result)
        self.assertNotIn("reason", result["live"])
        self.assertNotIn("limitation", result["live"])
        self.assertEqual(result["inquiry"]["answer"], {"available": False})
        self.assertNotIn("note", result["inquiry"])

    def test_closed_wait_answer_has_root_turn_reason(self):
        from hey_my_buddy.blackboard.tasks.inquiry import _apply_bridge_answer, _live_answer_value
        from hey_my_buddy.buddy.harnesses.live import InquiryState
        board = self.board()
        client = board.client()
        task, _attempt, _owner = self._attempt_directory(board, client)
        board.store._message_post(task["runId"], "q-closed", "status?")
        frame = InquiryState(question_id="q-closed", status="unavailable")
        _apply_bridge_answer(board.store, task["runId"], "q-closed", _live_answer_value(frame))
        message = client.get_message("q-closed", runId=task["runId"])
        self.assertEqual(message["state"], "unavailable")
        self.assertEqual(message["reason"], {"code": "inquiry-root-turn-ended", "params": {}})

    def test_terminal_observe_reports_invalid_records_and_imports_valid_answers(self):
        board = self.board()
        client = board.client()
        task, attempt, owner = self._attempt_directory(board, client)
        for question_id in ("q-state", "q-size", "q-valid"):
            board.store._message_post(task["runId"], question_id, "status?")
        owner.close()
        client.submit_result(attempt["attemptId"], attempt["generation"], "a" * 32,
                             {"status": "ok", "result": {"finalText": "fixture ended"},
                              "shutdownConfirmed": True, "exitCode": 0})
        bound = self._bound_fields(task, attempt)
        records = [
            {**bound, "inquiryId": "q-state", "state": "not-a-state"},
            {**bound, "inquiryId": "q-size", "state": "answered", "answer": {"text": "错" * 1400}},
            {**bound, "inquiryId": "q-valid", "state": "answered", "answer": {"text": "valid answer"}},
        ]
        journal = Path(owner.journal_path)
        journal.write_text("".join(json.dumps(record) + "\n" for record in records))
        evidence = journal.read_bytes()
        before = len(owner.requests)
        for selector in ({}, {"inquiryId": "q-state", "question": "status?"}, {}):
            observed = board.call("inquiry_observe", {"runId": task["runId"], **selector})
            self.assertEqual(observed["journal"]["rejections"], {
                "q-state": "journal-record-invalid", "q-size": "journal-record-invalid"})
            self.assertEqual(observed["journal"]["entries"], 3)
            if selector:
                self.assertEqual(observed["bridge"]["journalRejected"], "journal-record-invalid")
            self.assertEqual(client.get_message("q-valid", runId=task["runId"])["answer"]["text"], "valid answer")
            for question_id in ("q-state", "q-size"):
                self.assertIsNone(client.get_message(question_id, runId=task["runId"])["answer"])
        self.assertEqual(journal.read_bytes(), evidence)
        self.assertEqual(len(owner.requests), before)

    def test_observe_preserves_delivery_mode_and_separates_live_diagnostics(self):
        board = self.board()
        task, _attempt, owner = self._attempt_directory(board, board.client())
        for mode in ("realtime", "cooperative-checkpoint", "unsupported"):
            for ready in (True, False):
                with self.subTest(mode=mode, ready=ready):
                    diagnostic = "journal-unavailable: the inquiry record could not be appended durably"
                    owner.observation.update(ready=ready, error=diagnostic, deliveryMode=mode)
                    result = board.call("inquiry_observe", {"runId": task["runId"]})
                    self.assertEqual(result["live"]["available"], ready)
                    self.assertEqual(result["live"]["deliveryMode"], mode)
                    self.assertEqual(result["live"]["error"], diagnostic)
                    self.assertNotIn("reason", result["live"])

    def test_only_nested_journal_answers_supply_answer_provenance(self):
        from hey_my_buddy.blackboard.tasks.inquiry import normalize_journal_answer
        self.assertIsNone(normalize_journal_answer({"answer": "retired string answer", "answerBytes": 21}))
        answer = normalize_journal_answer({"answer": {"text": "nested"}, "via": "retired", "answeredAt": "retired", "toolCallId": "retired"})
        self.assertIsNone(answer["via"])
        self.assertIsNone(answer["at"])
        self.assertIsNone(answer["toolCallId"])


def zcode_catalog() -> dict:
    import copy

    payload = copy.deepcopy(FIXTURE_CATALOG)
    source = payload["providers"][0]
    payload["providers"] = [{**source, "harness": "zcode", "provider": "fixture-zcode",
                             "models": [{**source["models"][0], "id": "fixture-glm", "efforts": ["low", "high"]}]}]
    return payload


class ZcodeLiveChannelTests(BoardTestCase):
    """ZCode capability facts with a narrow owner, not a native harness run."""

    def setUp(self):
        super().setUp()
        self.catalog_fixture(zcode_catalog())

    def _submit(self, client, cwd, *, request_id, harness="zcode"):
        environment = {**os.environ, "GIT_CONFIG_SYSTEM": "/dev/null"}
        for arguments in (("init", "-q"), ("-c", "user.name=Buddy Test", "-c", "user.email=buddy@example.invalid",
                                           "commit", "--allow-empty", "-qm", "zcode live fixture")):
            completed = subprocess.run(["git", *arguments], cwd=cwd, env=environment, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
        submitted = client.workflow_submit(
            requestId=request_id, hostId="zcode-live-test-host", task="do", cwd=str(cwd), harness=harness,
            provider="fixture-zcode", model="fixture-glm", effort="low",
            executionWorkspace={"kind": "existing", "access": "read"},
        )
        self.assertEqual(submitted["task"]["lineage"]["kind"], "micro-task")
        return client.get(runId=submitted["runId"])

    def _live_attempt(self, board, client):
        task = self._submit(client, self.workdir(), request_id="zcode-live")
        return attach_owner(self, board, client, task, "zcode")

    def test_observation_reads_the_real_bridge_through_the_channel(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._live_attempt(board, client)
        bridge.activate("sess-live")
        bridge.note_event({"method": "session/event",
                           "params": {"type": "tool.updated", "payload": {"toolName": "read"}}}, "running")
        result = board.call("inquiry_observe", {"runId": task["runId"]})
        self.assertTrue(result["bridge"]["observed"])
        self.assertTrue(result["live"]["available"])
        self.assertEqual(result["live"]["sessionId"], "sess-live")
        self.assertEqual(result["live"]["deliveryMode"], "cooperative-checkpoint")
        self.assertEqual(result["live"]["inbox"], {"pending": 0, "delivered": 0, "answered": 0,
                                                   "discarded": 0, "refused": 0})
        self.assertEqual(result["live"]["activity"][0]["toolName"], "read")

    def test_a_question_is_asked_through_the_channel_and_answered_from_the_journal(self):
        import hashlib as hashlib_module

        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._live_attempt(board, client)
        bridge.activate("sess-live")
        question = "what is blocking you?"
        result = board.call("inquiry_observe", {
            "runId": task["runId"], "inquiryId": "q-live", "question": question, "waitMs": 3000,
        })
        # Owner admission commits queued, never delivered. The point query
        # reads the published queued state; it proves no native answer fact.
        # Signed native root-checkpoint verification stays outside this fixture.
        self.assertEqual(result["inquiry"]["state"], "queued")
        self.assertIn("q-live", bridge.entries)
        # The fixture writes checkpoint and reply-tool source evidence; the
        # business projection preserves it without verifying a native receipt.
        digest = hashlib_module.sha256(question.encode()).hexdigest()
        bridge.deliver_inquiries({"inquiries": [{"inquiryId": "q-live", "questionSha256": digest}]},
                                 "call-checkpoint")
        bridge.record_answer({"inquiryId": "q-live", "questionSha256": digest,
                              "answer": "the checkpoint tool"}, "call-answer")
        # A later ask replays the committed inquiry and imports its source
        # evidence from the owner-published durable journal projection.
        replayed = board.call("inquiry_observe", {
            "runId": task["runId"], "inquiryId": "q-live", "question": question,
        })
        self.assertEqual(replayed["journal"]["entries"], 1)
        message = client.get_message("q-live", runId=task["runId"])
        self.assertEqual(message["state"], "answered")
        self.assertEqual(message["answer"]["text"], "the checkpoint tool")
        self.assertEqual(message["answer"]["source"], "bridge-journal")
        self.assertEqual(message["answer"]["toolCallId"], "call-answer")

    def test_an_unpublished_point_query_is_observed_but_keeps_the_board_question_pending(self):
        board = self.board()
        client = board.client()
        task, _attempt, owner = self._live_attempt(board, client)
        board.store._message_post(task["runId"], "q-early", "anyone there?")
        channel = board.live_registry.channel_for(board.store.task_get({"runId": task["runId"]})["task"])
        snapshot = channel.native.observe(inquiry_id="q-early", timeout_ms=1500)
        self.assertTrue(snapshot.observed)
        self.assertEqual(snapshot.inquiries, ())
        self.assertEqual(client.get_message("q-early", runId=task["runId"])["state"], "queued")
        self.assertNotIn("q-early", owner.entries)
        self.assertFalse(board.call("inquiry_observe", {"runId": task["runId"]})["execution"]["shutdownConfirmed"])

    def test_an_answered_journal_entry_without_text_never_becomes_answered_here_either(self):
        board = self.board()
        client = board.client()
        task, attempt, bridge = self._live_attempt(board, client)
        # A published journal state without usable answer text must degrade
        # to delivered with a reason, never become a half-answered question.
        journal = Path(bridge.journal_path)
        journal.write_text(json.dumps({
            "version": 1, "taskId": task["runId"], "attemptId": attempt["attemptId"],
            "generation": attempt["generation"], "turnId": bridge.identity.turn_id,
            "inquiryId": "q-empty", "state": "answered", "via": "tool:hey_my_buddy_answer_inquiry",
        }) + "\n")
        board.call("inquiry_observe", {
            "runId": task["runId"], "inquiryId": "q-empty", "question": "status?",
        })
        message = client.get_message("q-empty", runId=task["runId"])
        self.assertEqual(message["state"], "delivered", "answered without usable text must not be recorded")
        self.assertIsNone(message["answer"])
        self.assertEqual(message["reason"], {"code": "inquiry-answer-empty", "params": {}})

    def test_an_absent_registration_never_falls_back_to_private_files(self):
        board = self.board()
        client = board.client()
        task, _attempt, owner = self._live_attempt(board, client)
        channel = board.live_registry.channel_for(board.store.task_get({"runId": task["runId"]})["task"])
        board.live_registry.unbind(owner.identity.attempt_id, channel)
        # A live source has an answer, but the service must not read it without
        # the holder's binding. There are no native request/credential files.
        owner.append({"inquiryId": "q-unbound", "state": "answered", "answer": {"text": "private answer"}})
        observed = board.call("inquiry_observe", {"runId": task["runId"],
                              "inquiryId": "q-unbound", "question": "anyone there?"})
        self.assertFalse(observed["bridge"]["observed"])
        self.assertEqual(observed["journal"], {"available": False, "reason": "channel-unbound", "entries": 0})
        self.assertFalse(observed["live"]["available"])
        self.assertEqual(observed["status"], "running")
        self.assertEqual(client.get_message("q-unbound", runId=task["runId"])["state"], "queued")
        self.assertIsNone(client.get_message("q-unbound", runId=task["runId"])["answer"])

    def test_execution_channel_binding_is_scoped_to_the_selected_attempt(self):
        board = self.board()
        client = board.client()
        task, _attempt, owner = self._live_attempt(board, client)
        view = board.store.task_get({"runId": task["runId"]})["task"]
        original = board.live_registry.channel_for(view)
        board.live_registry.unbind("foreign-attempt", original)
        self.assertIs(board.live_registry.channel_for(view), original)
        self.assertTrue(board.call("inquiry_observe", {"runId": task["runId"]})["bridge"]["observed"])

    def test_a_foreign_record_for_the_asked_id_surfaces_as_the_public_rejection(self):
        board = self.board()
        client = board.client()
        task, attempt, bridge = self._live_attempt(board, client)
        # The source record is foreign; the public rejection must survive
        # typed projection and never import the foreign answer.
        journal = Path(bridge.journal_path)
        journal.write_text(json.dumps({
            "version": 1, "taskId": "another-task", "attemptId": "another-attempt", "generation": 0,
            "turnId": "other", "inquiryId": "q-live", "state": "answered",
            "answer": {"text": "answer that belongs to a different run"},
        }) + "\n")
        result = board.call("inquiry_observe", {
            "runId": task["runId"], "inquiryId": "q-live", "question": "who are you?",
        })
        self.assertIsNone(result["bridge"]["error"])
        self.assertEqual(result["bridge"]["journalRejected"],
                         "journal-task-mismatch")
        self.assertEqual(result["journal"], {"available": True, "reason": None, "entries": 1,
                         "rejections": {"q-live": "journal-task-mismatch"}},
                         "the reader's own count includes the rejected record")
        message = client.get_message("q-live", runId=task["runId"])
        self.assertEqual(message["state"], "queued")
        self.assertIsNone(message["answer"], "a foreign journal record must never be imported as an answer")

    def test_a_journal_not_written_reports_the_direct_reader_reason(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._live_attempt(board, client)
        bridge.activate("sess-live")
        self.assertFalse(Path(bridge.journal_path).exists())
        result = board.call("inquiry_observe", {"runId": task["runId"]})
        self.assertTrue(result["bridge"]["observed"])
        self.assertEqual(result["journal"], {"available": False, "reason": "journal-not-written", "entries": 0})

    def test_the_channel_projection_consumes_every_page(self):
        board = self.board()
        client = board.client()
        task, attempt, bridge = self._live_attempt(board, client)
        bridge.activate("sess-live")
        # Thirty-two legitimate, complete, bound answers: more than one frame
        # holds, so the projection must be consumed to the end of its
        # pagination before anything is imported.
        rows = []
        for index in range(32):
            rows.append({"version": 1, "taskId": task["runId"], "attemptId": attempt["attemptId"],
                         "generation": attempt["generation"], "turnId": bridge.identity.turn_id,
                         "inquiryId": f"q{index}", "state": "answered",
                         "answer": {"text": "x" * 4000, "bytes": 4000,
                                    "via": "tool:hey_my_buddy_answer_inquiry", "toolCallId": f"call-{index}",
                                    "at": "2026-10-06T00:00:00Z", "truncated": False}})
        journal = Path(bridge.journal_path)
        journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
        board.store._message_post(task["runId"], "q0", "first")
        board.store._message_post(task["runId"], "q31", "last")
        result = board.call("inquiry_observe", {"runId": task["runId"]})
        self.assertEqual(client.get_message("q0", runId=task["runId"])["state"], "answered")
        self.assertEqual(result["journal"], {"available": True, "reason": None, "entries": 32,
            "rejections": {f"q{i}": "journal-question-unknown" for i in range(1, 31)}})
        message = client.get_message("q31", runId=task["runId"])
        self.assertEqual(message["state"], "answered")
        self.assertEqual(message["answer"]["text"], "x" * 4000)
        self.assertEqual(message["answer"]["toolCallId"], "call-31")
        self.assertEqual(message["answer"]["source"], "bridge-journal")
        pages = [item for item in bridge.requests if item["operation"] == "observe"
                 and item.get("fields") == ["inquiries"]]
        self.assertGreater(len(pages), 1)
        self.assertTrue(any(item.get("afterSeq") is not None for item in pages))

    def test_a_withdrawn_question_replays_as_an_observed_discarded_state(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._live_attempt(board, client)
        bridge.activate("sess-live")
        params = {"runId": task["runId"], "inquiryId": "q-discard", "question": "probe"}
        board.call("inquiry_observe", params)
        bridge.withdraw("q-discard")
        replayed = board.call("inquiry_observe", params)
        # The replay of a withdrawn question is an observed success carrying
        # the committed state and the native withdrawal reason — the transport
        # observed the bridge, and the reason is not lost.
        self.assertTrue(replayed["bridge"]["observed"])
        self.assertIsNone(replayed["bridge"]["reason"])
        self.assertIsNone(replayed["bridge"]["error"])
        message = client.get_message("q-discard", runId=task["runId"])
        self.assertEqual(message["state"], "discarded")
        self.assertIsNone(message["reason"])

    def test_a_settled_question_keeps_the_specific_reason(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._live_attempt(board, client)
        bridge.activate("sess-live")
        params = {"runId": task["runId"], "inquiryId": "q-close", "question": "probe"}
        board.call("inquiry_observe", params)
        bridge.settle()
        result = board.call("inquiry_observe", params)
        message = client.get_message("q-close", runId=task["runId"])
        self.assertEqual(message["state"], "unavailable")
        # The specific lifecycle reason survives the projection instead of
        # the generic explanation of how this bridge delivers inquiries.
        self.assertEqual(message["reason"], {"code": "inquiry-root-turn-ended", "params": {}})

    def test_an_empty_existing_journal_is_available_not_exceeds(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._live_attempt(board, client)
        bridge.activate("sess-live")
        Path(bridge.journal_path).write_bytes(b"")
        result = board.call("inquiry_observe", {"runId": task["runId"]})
        self.assertTrue(result["bridge"]["observed"])
        self.assertEqual(result["journal"], {"available": True, "reason": None, "entries": 0},
                         "an empty readable journal is a real journal, not an over-limit one")

    def test_the_latest_legal_record_supersedes_an_older_foreign_rejection(self):
        board = self.board()
        client = board.client()
        task, attempt, bridge = self._live_attempt(board, client)
        # The Host's superseded-foreign-record case over the real board: the
        # question's first journal line is foreign, its second fully bound with
        # an owned answer. The final effective record decides — the answer
        # imports with the journal's own source and the stale rejection is gone.
        journal = Path(bridge.journal_path)
        foreign = {"version": 1, "taskId": "other-task", "attemptId": "other-attempt",
                   "generation": 0, "turnId": "other-turn", "inquiryId": "q-last",
                   "state": "answered", "answer": {"text": "foreign"}}
        valid = {"version": 1, "taskId": task["runId"], "attemptId": attempt["attemptId"],
                 "generation": attempt["generation"], "turnId": bridge.identity.turn_id,
                 "inquiryId": "q-last", "state": "answered",
                 "answer": {"text": "owned answer", "bytes": 12,
                            "via": "tool:hey_my_buddy_answer_inquiry"}}
        journal.write_text(json.dumps(foreign) + "\n" + json.dumps(valid) + "\n")
        result = board.call("inquiry_observe", {
            "runId": task["runId"], "inquiryId": "q-last", "question": "probe",
        })
        self.assertNotIn("journalRejected", result["bridge"])
        self.assertEqual(result["journal"], {"available": True, "reason": None, "entries": 1})
        message = client.get_message("q-last", runId=task["runId"])
        self.assertEqual(message["state"], "answered")
        self.assertEqual(message["answer"]["text"], "owned answer")
        self.assertEqual(message["answer"]["source"], "bridge-journal")

    @mock.patch("hey_my_buddy.blackboard.tasks.inquiry.adapter_capabilities", return_value=frozenset({"zcode", "observe"}))
    def test_an_observe_only_adapter_refuses_a_question_without_asking_the_bridge(self, _capabilities):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._live_attempt(board, client)
        bridge.activate("sess-live")
        result = board.call(
            "inquiry_observe",
            {"runId": task["runId"], "inquiryId": "q-observe", "question": "what is the status?"},
        )
        self.assertTrue(result["bridge"]["canObserve"])
        self.assertFalse(result["bridge"]["canAsk"])
        self.assertIsNone(result["bridge"]["reason"])
        message = client.get_message("q-observe", runId=task["runId"])
        self.assertEqual(message["state"], "unavailable")
        self.assertEqual(message["reason"], {"code": "inquiry-observe-only", "params": {}})
        self.assertIsNone(message["answer"])
        self.assertEqual(bridge.entries, {}, "an observe-only adapter must never ask the bridge")
        observed = board.call("inquiry_observe", {"runId": task["runId"]})
        self.assertTrue(observed["bridge"]["observed"], "observe stays available for an observe-only adapter")


if __name__ == "__main__":
    unittest.main()
