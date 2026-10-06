"""Inquiry behaviour over the real service: bridge client, journal import and bounds.

The previous version of this file ran the Node job manager and a mock dsh. That
manager is gone; the bridge socket still belongs to the Node dsh plugin, so this
file tests the Python side of it directly: the wire protocol client, the JSONL
journal importer, the recorded message facts and the honest answers for adapters
that have no inquiry capability at all.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import unittest
from unittest import mock
from pathlib import Path

from support import FIXTURE_CATALOG, BoardTestCase

from hey_my_buddy.errors import BoardError
from hey_my_buddy.private_dirs import attempt_root, ensure_private_dir
from hey_my_buddy.blackboard.tasks.inquiry import (
    MAX_JOURNAL_BYTES,
    NOTE,
    bridge_request,
    observe,
    read_journal,
)


class FakeBridge:
    """A minimal stand-in for the Node plugin's Unix-socket protocol."""

    def __init__(self, directory: Path, *, token: str = "a" * 64):
        # The real adapter picks a short socket path for the same platform reason:
        # a long sun_path overflows the macOS/Linux Unix-socket limit.
        import tempfile
        import uuid

        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        short = Path(tempfile.gettempdir()) / f"hbi-{uuid.uuid4().hex[:8]}"
        short.mkdir(mode=0o700, exist_ok=True)
        self.path = short / "inquiry.sock"
        self.token = token
        self.requests: list[dict] = []
        self.answer: dict | None = None
        self.refusal: str | None = None
        self.activity = []
        self.delivery_mode = None
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.path))
        self.listener.listen(4)
        self.listener.settimeout(0.2)
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.stopping.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(2)
                try:
                    raw = connection.recv(64 * 1024)
                    request = json.loads(raw.split(b"\n", 1)[0])
                    self.requests.append(request)
                    if request.get("token") != self.token:
                        reply = {"version": 1, "id": request.get("id"), "ok": False, "error": "unauthorized"}
                    elif request.get("method") == "observe":
                        reply = {
                            "version": 1,
                            "id": request["id"],
                            "ok": True,
                            "value": {"ready": True, "sessionId": "s-1", "agentStatus": "running",
                                      "activity": self.activity, "deliveryMode": self.delivery_mode},
                        }
                    elif request.get("method") == "ask":
                        if self.refusal:
                            reply = {"version": 1, "id": request["id"], "ok": False, "error": self.refusal}
                        else:
                            reply = {"version": 1, "id": request["id"], "ok": True, "value": {"state": "delivered"}}
                    else:
                        reply = {
                            "version": 1,
                            "id": request["id"],
                            "ok": True,
                            "value": {"answer": self.answer} if self.answer else {},
                        }
                except (OSError, ValueError):
                    reply = {"version": 1, "id": None, "ok": False, "error": "bad-request"}
                try:
                    connection.sendall((json.dumps(reply) + "\n").encode())
                except OSError:
                    pass

    def close(self):
        self.stopping.set()
        self.listener.close()
        self.thread.join(timeout=2)
        self.path.unlink(missing_ok=True)
        try:
            self.path.parent.rmdir()
        except OSError:
            pass


class TestInquiry(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.catalog_fixture()

    def _submit(self, client, cwd, *, request_id, adapter="dsh", provider="deepseek-official",
                model="deepseek-flash", effort="off"):
        environment = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        for arguments in (("init", "-q"), ("-c", "user.name=Buddy Test", "-c", "user.email=buddy@example.invalid",
                                           "commit", "--allow-empty", "-qm", "inquiry fixture")):
            completed = subprocess.run(["git", *arguments], cwd=cwd, env=environment, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
        submitted = client.workflow_submit(
            requestId=request_id, hostId="inquiry-test-host", task="do", cwd=str(cwd), adapter=adapter,
            provider=provider, model=model, effort=effort,
            executionWorkspace={"kind": "existing", "access": "read"},
        )
        self.assertTrue(submitted["governed"])
        return client.get(runId=submitted["runId"])

    def _submit_dsh(self, client, cwd, *, request_id):
        return self._submit(client, cwd, request_id=request_id)

    def _attempt_directory(self, board, client, task, cwd, argv=None, *, adapter="dsh",
                           capabilities=("dsh", "inquiry")):
        """Submit, claim and publish bridge credentials the way a coding adapter does."""
        # This fixture publishes the same credentials the adapter writes without
        # spawning a runner; the declared capabilities decide observe vs inquire.
        task = self._submit(client, cwd, request_id="inq-task", adapter=adapter,
                            provider="deepseek-official" if adapter == "dsh" else "fixture-zcode",
                            model="deepseek-flash" if adapter == "dsh" else "fixture-glm",
                            effort="off" if adapter == "dsh" else "low")
        client.register_worker("w-inq", adapter=adapter, capabilities=list(capabilities))
        claim = client.claim("w-inq", "claim-inq-1", "a" * 32)
        self.assertIsNotNone(claim["claim"]["turn"])
        attempt = claim["claim"]["attempt"]
        from hey_my_buddy.buddy.runtime.worker import fsync_json

        directory = self.directory / "attempts" / task["runId"] / attempt["attemptId"]
        directory.mkdir(parents=True, exist_ok=True)
        private = ensure_private_dir(attempt_root(self.directory, adapter, task["runId"], attempt["attemptId"]))
        bridge = FakeBridge(directory)
        fsync_json(
            private / "inquiry.json",
            {
                "socketPath": str(bridge.path),
                "resultsPath": str(directory / "inquiry.results.jsonl"),
                "errorPath": str(directory / "inquiry.sock.error.json"),
                "token": bridge.token,
            },
        )
        return task, attempt, bridge

    def test_observation_uses_the_real_bridge_protocol(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._attempt_directory(board, client, None, self.workdir())
        try:
            bridge.delivery_mode = "cooperative-checkpoint"
            result = board.call("inquiry_observe", {"runId": task["runId"]})
            self.assertTrue(result["bridge"]["observed"])
            self.assertTrue(result["live"]["available"])
            self.assertEqual(result["live"]["sessionId"], "s-1")
            self.assertEqual(result["live"]["deliveryMode"], "cooperative-checkpoint")
            self.assertEqual(result["phase"], "active")
            self.assertEqual(result["inflight"] if "inflight" in result else result["execution"]["attemptState"], "starting")
            self.assertEqual(bridge.requests[-1]["method"], "observe")
        finally:
            bridge.close()

    def test_observation_does_not_publish_native_tool_arguments(self):
        board = self.board()
        task, _attempt, bridge = self._attempt_directory(board, board.client(), None, self.workdir())
        try:
            bridge.activity = [{"phase": "started", "tool": "bash", "at": 100, "seq": 1,
                                "argumentPreview": "private-provider-key", "nested": {"prompt": "private-prompt"}}]
            result = board.call("inquiry_observe", {"runId": task["runId"]})
            self.assertEqual(result["live"]["activity"][0]["tool"], "bash")
            self.assertNotIn("private-provider-key", json.dumps(result))
            self.assertNotIn("private-prompt", json.dumps(result))
        finally:
            bridge.close()

    def test_a_question_is_correlated_and_a_wrong_token_is_refused(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._attempt_directory(board, client, None, self.workdir())
        try:
            result = board.call(
                "inquiry_observe",
                {"runId": task["runId"], "inquiryId": "q-live", "question": "what is blocking you?"},
            )
            self.assertEqual(result["inquiry"]["state"], "delivered")
            self.assertEqual(result["inquiry"]["correlation"], "inquiryId")
            self.assertEqual(bridge.requests[-1]["method"], "ask")
            self.assertEqual(bridge.requests[-1]["inquiryId"], "q-live")
            # A bridge that rejects the token is reported, never silently treated as an answer.
            bridge.token = "b" * 64
            second = board.call(
                "inquiry_observe",
                {"runId": task["runId"], "inquiryId": "q-two", "question": "and now?"},
            )
            self.assertEqual(second["bridge"]["error"], "unauthorized")
        finally:
            bridge.close()

    def test_the_real_bridge_journal_shape_is_imported_with_reply_tool_evidence(self):
        """The Node bridge writes a string answer with sibling evidence fields."""
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._attempt_directory(board, client, None, self.workdir())
        journal = Path(bridge.directory) / "inquiry.results.jsonl"
        journal.write_text(
            json.dumps(
                {
                    "inquiryId": "q-real",
                    "state": "delivered",
                    "messageId": "m-1",
                    "deliveredAt": "2026-09-19T05:00:00.000Z",
                }
            )
            + "\n"
            + json.dumps(
                {
                    "inquiryId": "q-real",
                    "state": "answered",
                    "answeredAt": "2026-09-19T05:00:04.000Z",
                    "via": "tool:buddy_inquiry_reply",
                    "toolCallId": "call-42",
                    "messageId": "m-1",
                    "answer": "the tests are still running",
                    "answerBytes": 27,
                    "truncated": False,
                }
            )
            + "\n"
        )
        try:
            board.call("inquiry_observe", {"runId": task["runId"], "inquiryId": "q-real", "question": "status?"})
            message = client.get_message("q-real", runId=task["runId"])
            self.assertEqual(message["state"], "answered")
            self.assertEqual(message["answer"]["text"], "the tests are still running")
            self.assertEqual(message["answer"]["via"], "tool:buddy_inquiry_reply")
            self.assertEqual(message["answer"]["toolCallId"], "call-42")
            self.assertEqual(message["answer"]["at"], "2026-09-19T05:00:04.000Z")
            self.assertEqual(message["answer"]["source"], "bridge-journal")
            self.assertEqual(message["delivery"]["messageId"], "m-1")
            # Re-importing the same journal is idempotent.
            board.call("inquiry_observe", {"runId": task["runId"], "inquiryId": "q-real", "question": "status?"})
            self.assertEqual(client.get_message("q-real", runId=task["runId"])["answer"]["text"], "the tests are still running")
        finally:
            bridge.close()

    def test_an_answered_journal_entry_without_text_never_becomes_answered(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._attempt_directory(board, client, None, self.workdir())
        journal = Path(bridge.directory) / "inquiry.results.jsonl"
        journal.write_text(
            json.dumps({"inquiryId": "q-empty", "state": "delivered"})
            + "\n"
            + json.dumps({"inquiryId": "q-empty", "state": "answered", "via": "tool:buddy_inquiry_reply"})
            + "\n"
        )
        try:
            board.call("inquiry_observe", {"runId": task["runId"], "inquiryId": "q-empty", "question": "status?"})
            message = client.get_message("q-empty", runId=task["runId"])
            self.assertEqual(message["state"], "delivered", "answered without usable text must not be recorded")
            self.assertIsNone(message["answer"])
            self.assertIn("without usable answer text", message["reason"])
        finally:
            bridge.close()

    def test_the_journal_is_idempotent_transport_evidence(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._attempt_directory(board, client, None, self.workdir())
        journal = Path(bridge.directory) / "inquiry.results.jsonl"
        journal.write_text(
            json.dumps({"inquiryId": "q-journal", "state": "delivered"})
            + "\n"
            + json.dumps({"inquiryId": "q-journal", "state": "answered", "answer": {"text": "from the journal"}})
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
        self.assertIn("no inquiry bridge credentials", result["bridge"]["reason"])
        command_task = client.submit(
            requestId="no-bridge-command",
            task="do",
            cwd=str(self.workdir("other")),
            adapter="command",
            argv=["/bin/true"],
        )["task"]
        command_result = board.call("inquiry_observe", {"runId": command_task["runId"]})
        self.assertIn("no observation or inquiry capability", command_result["bridge"]["reason"])
        self.assertIn("agentStatus", result["live"]["unavailable"])
        self.assertEqual(result["limits"]["maxQuestionBytes"], 4000)
        self.assertEqual(result["limits"]["maxInquiriesPerRun"], 32)
        self.assertEqual(result["deadline"]["estimated"], True)
        self.assertEqual(result["deadline"]["exact"], False)

    def test_explicit_unlimited_execution_never_reports_an_expired_deadline(self):
        board = self.board()
        task = board.client().submit(
            requestId="unlimited-inquiry", task="a long task", cwd=str(self.workdir()),
            adapter="command", argv=["/bin/true"], timeoutSeconds=0,
        )["task"]
        result = board.call("inquiry_observe", {"runId": task["runId"]})
        self.assertEqual(result["deadline"], {
            "available": False, "unlimited": True, "reason": "this execution has no deadline",
        })

    def test_bridge_request_reports_unreachable_sockets(self):
        import tempfile
        import uuid

        # Keep the AF_UNIX address short even inside hey_my_buddy.cli.checks' private TMPDIR.
        # A nested workdir can exceed macOS's socket limit before ENOENT is tested.
        missing = Path(tempfile.gettempdir()) / f"hbi-{uuid.uuid4().hex[:8]}"
        self.assertFalse(missing.exists())
        result = bridge_request({"socketPath": str(missing), "token": "x"}, "observe", {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "bridge-unreachable")

    def test_an_unsupported_adapter_answers_with_a_capability_error(self):
        board = self.board()
        client = board.client()
        task = client.submit(
            requestId="ext-inq", task="do", cwd=str(self.workdir()), adapter="external"
        )["task"]
        result = board.call("inquiry_observe", {"runId": task["runId"]})
        self.assertFalse(result["bridge"]["enabled"])
        self.assertIn("no observation or inquiry capability", result["bridge"]["reason"])

    def test_inquiry_capability_comes_from_the_adapter_registry(self):
        from hey_my_buddy.blackboard.tasks.inquiry import inquiry_capable

        from hey_my_buddy.blackboard.tasks.inquiry import observe_capable

        # DSH supports live injection; ZCode delivers at cooperative tool
        # checkpoints. Neither capability is inferred by probing a native CLI.
        self.assertTrue(inquiry_capable("dsh"))
        self.assertTrue(observe_capable("dsh"))
        self.assertTrue(inquiry_capable("zcode"))
        self.assertTrue(observe_capable("zcode"))
        for name in ("command", "external", "not-an-adapter"):
            with self.subTest(adapter=name):
                self.assertFalse(inquiry_capable(name))
                self.assertFalse(observe_capable(name))

    def test_journal_failure_refuses_the_question_without_ending_execution(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._attempt_directory(board, client, None, self.workdir())
        try:
            bridge.refusal = "journal-unavailable"
            result = board.call("inquiry_observe", {
                "runId": task["runId"], "inquiryId": "q-journal-failed", "question": "Status?",
            })
            self.assertEqual(result["bridge"]["error"], "journal-unavailable")
            message = client.get_message("q-journal-failed", runId=task["runId"])
            self.assertEqual(message["state"], "unavailable")
            self.assertIn("journal-unavailable", message["reason"])
            self.assertEqual(client.get(runId=task["runId"])["state"], "running")
        finally:
            bridge.close()

    def test_a_question_refused_after_the_turn_ended_is_recorded_unavailable(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._attempt_directory(board, client, None, self.workdir())
        bridge.refusal = "agent-gone"
        try:
            result = board.call(
                "inquiry_observe",
                {"runId": task["runId"], "inquiryId": "q-ended", "question": "still running?"},
            )
            self.assertEqual(result["bridge"]["error"], "agent-gone")
            message = client.get_message("q-ended", runId=task["runId"])
            self.assertEqual(message["state"], "unavailable")
            self.assertIn("agent-gone", message["reason"])
            self.assertIsNone(message["answer"])
        finally:
            bridge.close()

    def test_a_journal_record_bound_to_another_task_can_never_answer(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._attempt_directory(board, client, None, self.workdir())
        journal = Path(bridge.directory) / "inquiry.results.jsonl"
        journal.write_text(json.dumps({
            "inquiryId": "q-foreign", "state": "answered", "taskId": "another-task", "attemptId": "another-attempt",
            "answer": "answer that belongs to a different run", "via": "tool:buddy_inquiry_reply",
        }) + "\n")
        try:
            result = board.call(
                "inquiry_observe",
                {"runId": task["runId"], "inquiryId": "q-foreign", "question": "who are you?"},
            )
            self.assertIn("another", result["bridge"]["journalRejected"])
            message = client.get_message("q-foreign", runId=task["runId"])
            self.assertIsNone(message["answer"], "a foreign journal record must never be imported as an answer")
        finally:
            bridge.close()


def zcode_catalog() -> dict:
    import copy

    payload = copy.deepcopy(FIXTURE_CATALOG)
    source = payload["providers"][0]
    payload["providers"] = [{**source, "adapter": "zcode", "provider": "fixture-zcode",
                             "models": [{**source["models"][0], "id": "fixture-glm", "efforts": ["low", "high"]}]}]
    return payload


class ZcodeLiveChannelTests(BoardTestCase):
    """The board's zcode path through the registered live channel (step 2-C2).

    The real Python bridge serves a real socket and the board's real
    ``inquiry_observe`` runs against a real store. The channel binds the stored
    public run request's complete identity against the run's own control
    binding and governed turn input; an extracted harness whose stored request
    is missing, unreadable or foreign stays unbound with no direct fallback —
    the old direct-bridge behavior was the defect this round corrects.
    """

    def setUp(self):
        super().setUp()
        self.catalog_fixture(zcode_catalog())

    def _submit(self, client, cwd, *, request_id, adapter="zcode"):
        environment = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        for arguments in (("init", "-q"), ("-c", "user.name=Buddy Test", "-c", "user.email=buddy@example.invalid",
                                           "commit", "--allow-empty", "-qm", "zcode live fixture")):
            completed = subprocess.run(["git", *arguments], cwd=cwd, env=environment, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
        submitted = client.workflow_submit(
            requestId=request_id, hostId="zcode-live-test-host", task="do", cwd=str(cwd), adapter=adapter,
            provider="fixture-zcode", model="fixture-glm", effort="low",
            executionWorkspace={"kind": "existing", "access": "read"},
        )
        self.assertTrue(submitted["governed"])
        return client.get(runId=submitted["runId"])

    def _live_attempt(self, board, client, *, with_request=True, invocation_id="invocation-live"):
        """Submit a zcode task, claim it, and serve the real bridge it owns.

        The stored materials mirror exactly what the accepted C1 launch wrote:
        the public ``role-run-request.json``, the private ``role-run-control.json``
        naming the invocation and the governed turn input, and that turn input
        file — the relationship the channel's full identity check reads.
        """
        import secrets as secrets_module
        import tempfile
        import uuid

        from hey_my_buddy.buddy.harnesses.run_contract import (
            FrozenJson,
            PrivateStatePaths,
            RunBudget,
            RunConfiguration,
            RunIdentity,
            RunRequest,
            encode_run_request,
        )
        from hey_my_buddy.buddy.harnesses.zcode.native_run import make_inquiry_bridge
        from hey_my_buddy.buddy.roles.turn_io import input_hash
        from hey_my_buddy.buddy.runtime.worker import fsync_json

        task = self._submit(client, self.workdir(), request_id="zcode-live")
        client.register_worker("w-zcode-live", adapter="zcode", capabilities=("zcode", "inquiry"))
        claim = client.claim("w-zcode-live", "claim-zcode-live-1", "a" * 32)
        self.assertIsNotNone(claim["claim"]["turn"])
        attempt = claim["claim"]["attempt"]
        directory = self.directory / "attempts" / task["runId"] / attempt["attemptId"]
        directory.mkdir(parents=True, exist_ok=True)
        private = ensure_private_dir(attempt_root(self.directory, "zcode", task["runId"],
                                                  attempt["attemptId"]))
        # The real adapter moves the socket itself to a short temp directory:
        # the attempt root's path overflows a Unix socket's sun_path.
        short = Path(tempfile.gettempdir()) / f"zbl-{uuid.uuid4().hex[:8]}"
        short.mkdir(mode=0o700, exist_ok=True)
        bridge_identity = {"taskId": task["runId"], "attemptId": attempt["attemptId"],
                           "generation": attempt["generation"], "turnId": "turn-fixture-live"}
        credentials = {
            "socketPath": str(short / "inquiry.sock"),
            "resultsPath": str(directory / "inquiry.results.jsonl"),
            "errorPath": str(directory / "inquiry.sock.error.json"),
            "token": secrets_module.token_hex(32),
        }
        bridge = make_inquiry_bridge({"socketPath": credentials["socketPath"], "token": credentials["token"]},
                               identity=bridge_identity,
                               journal_path=credentials["resultsPath"])
        bridge.start()
        self.addCleanup(bridge.close)
        fsync_json(private / "inquiry.json", credentials)
        turn_input = {"taskId": task["runId"], "attemptId": attempt["attemptId"],
                      "generation": attempt["generation"], "turnId": "turn-fixture-live",
                      "resumeMode": "initial", "previousSessionId": None,
                      "context": {}}
        identity = RunIdentity(task_id=task["runId"], attempt_id=attempt["attemptId"],
                               generation=attempt["generation"], invocation_id=invocation_id,
                               turn_id="turn-fixture-live", input_sha256=input_hash(turn_input))
        fsync_json(directory / "turn-input.json", turn_input)
        control = {"operation": "worker", "harness": "zcode", "invocationId": identity.invocation_id,
                   "inputFile": str(directory / "turn-input.json"), "privateRoot": str(private),
                   "directory": str(directory), "inquiry": credentials,
                   "spec": {"provider": "fixture-zcode", "model": "fixture-glm", "effort": "low"},
                   "cwd": str(self.workdir()), "timeoutSeconds": 600}
        fsync_json(private / "role-run-control.json", control)
        if with_request:
            request = RunRequest(
                identity=identity, harness="zcode",
                configuration=RunConfiguration(provider="fixture-zcode", model="fixture-glm", effort="low"),
                cwd=str(self.workdir()),
                private_state=PrivateStatePaths(invocation_root=str(private), native_root=str(private / "native")),
                input_text="the governed turn input", tool_scope="write",
                output_schema=FrozenJson({"type": "object"}),
                budget=RunBudget(timeout_seconds=600))
            fsync_json(private / "role-run-request.json", json.loads(encode_run_request(request)))
        return task, attempt, bridge

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
        # Before the root checkpoint the question is queued, never delivered;
        # the wait's single native answer query found no answer yet. (The
        # bridge's own delivery record is journal transport evidence; the board
        # message store keeps only its bounded injection-delivery keys.)
        self.assertEqual(result["inquiry"]["state"], "queued")
        self.assertIn("q-live", bridge.entries)
        # The verified root answer arrives through the driver-authority methods
        # that write the very journal records.
        digest = hashlib_module.sha256(question.encode()).hexdigest()
        bridge.deliver_inquiries({"inquiries": [{"inquiryId": "q-live", "questionSha256": digest}]},
                                 "call-checkpoint")
        bridge.record_answer({"inquiryId": "q-live", "questionSha256": digest,
                              "answer": "the checkpoint tool"}, "call-answer")
        # A later ask of the same question replays the committed inquiry and
        # imports the answer from the durable journal through the channel's
        # bound projection, with the journal's own source.
        replayed = board.call("inquiry_observe", {
            "runId": task["runId"], "inquiryId": "q-live", "question": question,
        })
        self.assertEqual(replayed["journal"]["entries"], 1)
        message = client.get_message("q-live", runId=task["runId"])
        self.assertEqual(message["state"], "answered")
        self.assertEqual(message["answer"]["text"], "the checkpoint tool")
        self.assertEqual(message["answer"]["source"], "bridge-journal")
        self.assertEqual(message["answer"]["toolCallId"], "call-answer")

    def test_a_not_ready_refusal_stays_pending_and_carries_the_specific_code(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._live_attempt(board, client)
        # The bridge is mounted but the native turn is not admitted yet.
        result = board.call("inquiry_observe", {
            "runId": task["runId"], "inquiryId": "q-early", "question": "anyone there?",
        })
        self.assertEqual(result["bridge"]["reason"], "bridge-refused")
        self.assertEqual(result["bridge"]["error"], "not-ready")
        message = client.get_message("q-early", runId=task["runId"])
        self.assertEqual(message["state"], "queued", "a retryable refusal never terminalizes a question")

    def test_an_answered_journal_entry_without_text_never_becomes_answered_here_either(self):
        board = self.board()
        client = board.client()
        task, attempt, bridge = self._live_attempt(board, client)
        # The native turn is not admitted, so the board's ask is refused
        # without journaling and the journal keeps only this record: an
        # answered entry with no usable answer text, projected through the
        # channel, must degrade to delivered with its reason — never a
        # half-answered question.
        journal = Path(bridge.journal_path)
        journal.write_text(json.dumps({
            "version": 1, "taskId": task["runId"], "attemptId": attempt["attemptId"],
            "generation": attempt["generation"], "turnId": "turn-fixture-live",
            "inquiryId": "q-empty", "state": "answered", "via": "tool:buddy_answer_inquiry",
        }) + "\n")
        board.call("inquiry_observe", {
            "runId": task["runId"], "inquiryId": "q-empty", "question": "status?",
        })
        message = client.get_message("q-empty", runId=task["runId"])
        self.assertEqual(message["state"], "delivered", "answered without usable text must not be recorded")
        self.assertIsNone(message["answer"])
        self.assertIn("without usable answer text", message["reason"])

    def test_a_tampered_request_never_bridges_around_its_channel(self):
        board = self.board()
        client = board.client()
        task, attempt, bridge = self._live_attempt(board, client)
        bridge.activate("sess-live")
        # Tamper the stored request after the fixture established the owned
        # bridge: its invocation no longer matches the run's own control
        # binding, so no channel binds — and the extracted live path has no
        # direct fallback. Nothing reaches the native bridge.
        request_file = attempt_root(self.directory, "zcode", task["runId"], attempt["attemptId"]) / "role-run-request.json"
        request = json.loads(request_file.read_text())
        request["identity"]["invocationId"] = "foreign-invocation"
        request_file.write_text(json.dumps(request))
        observed = board.call("inquiry_observe", {"runId": task["runId"]})
        self.assertFalse(observed["bridge"]["observed"])
        self.assertIn("unbound", observed["bridge"]["reason"])
        self.assertFalse(observed["live"]["available"])
        self.assertEqual(observed["journal"], {"available": False, "reason": "channel-unbound", "entries": 0})
        asked = board.call("inquiry_observe", {
            "runId": task["runId"], "inquiryId": "q-tampered", "question": "anyone there?",
        })
        self.assertFalse(asked["bridge"]["observed"])
        self.assertNotIn("q-tampered", bridge.entries,
                         "a foreign request identity never reaches the native bridge")
        message = client.get_message("q-tampered", runId=task["runId"])
        self.assertEqual(message["state"], "queued")

    def test_a_task_id_mismatch_never_binds_either(self):
        board = self.board()
        client = board.client()
        task, attempt, bridge = self._live_attempt(board, client)
        bridge.activate("sess-live")
        request_file = attempt_root(self.directory, "zcode", task["runId"], attempt["attemptId"]) / "role-run-request.json"
        request = json.loads(request_file.read_text())
        request["identity"]["taskId"] = "foreign-task"
        request_file.write_text(json.dumps(request))
        observed = board.call("inquiry_observe", {"runId": task["runId"]})
        self.assertFalse(observed["bridge"]["observed"])
        self.assertIn("unbound", observed["bridge"]["reason"])

    def test_a_foreign_record_for_the_asked_id_surfaces_as_the_public_rejection(self):
        board = self.board()
        client = board.client()
        task, attempt, bridge = self._live_attempt(board, client)
        # The native turn is not admitted, so the board's ask is refused
        # without journaling; the journal keeps only the foreign record, bound
        # to the very id this run is asking about.
        journal = Path(bridge.journal_path)
        journal.write_text(json.dumps({
            "version": 1, "taskId": "another-task", "attemptId": "another-attempt", "generation": 0,
            "turnId": "other", "inquiryId": "q-live", "state": "answered",
            "answer": {"text": "answer that belongs to a different run"},
        }) + "\n")
        result = board.call("inquiry_observe", {
            "runId": task["runId"], "inquiryId": "q-live", "question": "who are you?",
        })
        self.assertEqual(result["bridge"]["error"], "not-ready")
        self.assertEqual(result["bridge"]["journalRejected"],
                         "the journal record belongs to another task")
        self.assertEqual(result["journal"], {"available": True, "reason": None, "entries": 1},
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
                         "generation": attempt["generation"], "turnId": "turn-fixture-live",
                         "inquiryId": f"q{index}", "state": "answered",
                         "answer": {"text": "x" * 4000, "bytes": 4000,
                                    "via": "tool:buddy_answer_inquiry", "toolCallId": f"call-{index}",
                                    "at": "2026-10-06T00:00:00Z", "truncated": False}})
        journal = Path(bridge.journal_path)
        journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
        result = board.call("inquiry_observe", {
            "runId": task["runId"], "inquiryId": "q31", "question": "status of the last one?",
        })
        self.assertEqual(result["journal"], {"available": True, "reason": None, "entries": 32})
        message = client.get_message("q31", runId=task["runId"])
        self.assertEqual(message["state"], "answered")
        self.assertEqual(message["answer"]["text"], "x" * 4000)
        self.assertEqual(message["answer"]["toolCallId"], "call-31")
        self.assertEqual(message["answer"]["source"], "bridge-journal")

    def test_a_withdrawn_question_replays_as_an_observed_discarded_state(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._live_attempt(board, client)
        bridge.activate("sess-live")
        params = {"runId": task["runId"], "inquiryId": "q-discard", "question": "probe"}
        board.call("inquiry_observe", params)
        discarded = bridge.handle({"version": 1, "id": "withdraw-1", "token": bridge.token,
                                   "method": "discard", "inquiryId": "q-discard"})
        self.assertTrue(discarded["ok"], discarded)
        replayed = board.call("inquiry_observe", params)
        # The replay of a withdrawn question is an observed success carrying
        # the committed state and the native withdrawal reason — the transport
        # observed the bridge, and the reason is not lost.
        self.assertTrue(replayed["bridge"]["observed"])
        self.assertIsNone(replayed["bridge"]["reason"])
        self.assertIsNone(replayed["bridge"]["error"])
        message = client.get_message("q-discard", runId=task["runId"])
        self.assertEqual(message["state"], "discarded")
        self.assertEqual(message["reason"], "withdrawn by the asking side; it no longer blocks turn completion")

    def test_a_settled_question_keeps_the_limitation_preferred_reason(self):
        board = self.board()
        client = board.client()
        task, _attempt, bridge = self._live_attempt(board, client)
        bridge.activate("sess-live")
        params = {"runId": task["runId"], "inquiryId": "q-close", "question": "probe"}
        board.call("inquiry_observe", params)
        bridge.close()
        result = board.call("inquiry_observe", params)
        message = client.get_message("q-close", runId=task["runId"])
        self.assertEqual(message["state"], "unavailable")
        # The direct reader preferred the record's limitation over its reason;
        # the channel's projection keeps that selection verbatim, bounded by
        # the message store's own reason length exactly as before.
        from hey_my_buddy.buddy.harnesses.zcode.protocol import COOPERATIVE_INQUIRY_NOTE

        self.assertEqual(message["reason"], COOPERATIVE_INQUIRY_NOTE[:200])

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
                 "generation": attempt["generation"], "turnId": "turn-fixture-live",
                 "inquiryId": "q-last", "state": "answered",
                 "answer": {"text": "owned answer", "bytes": 12,
                            "via": "tool:buddy_answer_inquiry"}}
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
        self.assertIn("no correlated inquiry capability", result["bridge"]["reason"])
        message = client.get_message("q-observe", runId=task["runId"])
        self.assertEqual(message["state"], "unavailable")
        self.assertIn("no correlated inquiry capability", message["reason"])
        self.assertIsNone(message["answer"])
        self.assertEqual(bridge.entries, {}, "an observe-only adapter must never ask the bridge")
        observed = board.call("inquiry_observe", {"runId": task["runId"]})
        self.assertTrue(observed["bridge"]["observed"], "observe stays available for an observe-only adapter")


if __name__ == "__main__":
    unittest.main()
