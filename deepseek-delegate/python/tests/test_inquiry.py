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
import threading
import unittest
from pathlib import Path

from support import BoardTestCase

from buddy.errors import BoardError
from buddy.inquiry import (
    MAX_JOURNAL_BYTES,
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
                            "value": {"ready": True, "sessionId": "s-1", "agentStatus": "running", "activity": []},
                        }
                    elif request.get("method") == "ask":
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
    def _attempt_directory(self, board, client, task, cwd, argv=None):
        """Submit, claim and publish bridge credentials the way the dsh adapter does."""
        # Inquiry is the dsh adapter's capability; this fixture publishes the same
        # credentials the dsh adapter writes without spawning a runner.
        task = client.submit(requestId="inq-task", task="do", cwd=str(cwd), adapter="dsh")["task"]
        client.register_worker("w-inq", adapter="dsh", capabilities=["dsh", "inquiry"])
        claim = client.claim("w-inq", "claim-inq-1", "a" * 32)
        attempt = claim["claim"]["attempt"]
        from buddy.worker.worker import fsync_json

        directory = self.directory / "attempts" / task["runId"] / attempt["attemptId"]
        directory.mkdir(parents=True, exist_ok=True)
        bridge = FakeBridge(directory)
        fsync_json(
            directory / "inquiry.json",
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
            result = board.call("inquiry_observe", {"runId": task["runId"]})
            self.assertTrue(result["bridge"]["observed"])
            self.assertTrue(result["live"]["available"])
            self.assertEqual(result["live"]["sessionId"], "s-1")
            self.assertEqual(result["phase"], "active")
            self.assertEqual(result["inflight"] if "inflight" in result else result["execution"]["attemptState"], "starting")
            self.assertEqual(bridge.requests[-1]["method"], "observe")
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
        dsh_task = client.submit(requestId="no-bridge", task="do", cwd=str(self.workdir()))["task"]
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
        self.assertIn("no inquiry capability", command_result["bridge"]["reason"])
        self.assertIn("agentStatus", result["live"]["unavailable"])
        self.assertEqual(result["limits"]["maxQuestionBytes"], 4000)
        self.assertEqual(result["limits"]["maxInquiriesPerRun"], 32)
        self.assertEqual(result["deadline"]["estimated"], True)
        self.assertEqual(result["deadline"]["exact"], False)

    def test_bridge_request_reports_unreachable_sockets(self):
        result = bridge_request({"socketPath": str(self.workdir() / "missing.sock"), "token": "x"}, "observe", {})
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
        self.assertIn("no inquiry capability", result["bridge"]["reason"])


if __name__ == "__main__":
    unittest.main()
