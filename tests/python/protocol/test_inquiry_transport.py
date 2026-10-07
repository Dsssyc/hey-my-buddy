"""The one shared inquiry-bridge socket client (ADR-025 step 2-C2).

The client moved mechanically from the blackboard inquiry module to
:mod:`hey_my_buddy.protocol.inquiry_transport`, so both the board and the ZCode
live binding speak the identical frames. These tests pin the preserved wire
behavior over real local sockets: the id correlation, the transport window, the
bounded reply and the exact refusal classification — including the specific
peer code a refused answer carries, which the removed duplicate client used to
fold into one generic refusal.
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from hey_my_buddy.protocol.inquiry_transport import (
    BRIDGE_ERRORS,
    MAX_TRANSPORT_TIMEOUT_MS,
    MIN_TRANSPORT_TIMEOUT_MS,
    bridge_request,
)


class SilentServer:
    """A listener that accepts and never answers, for timeout classification."""

    def __init__(self, path: Path):
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(path))
        self.listener.listen(2)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        try:
            connection, _ = self.listener.accept()
            time.sleep(2.0)  # read nothing, answer nothing
            connection.close()
        except OSError:
            pass

    def close(self) -> None:
        self.listener.close()
        self.thread.join(timeout=2)


class TransportTests(unittest.TestCase):
    def short_socket_dir(self, prefix: str) -> Path:
        """A short AF_UNIX directory on a standard-library fixture lifecycle.

        The directory is one ``tempfile.TemporaryDirectory`` whose removal is
        registered before the sockets and serving threads each test registers
        after it, so unittest's last-registered-first cleanup order stops the
        servers first and removes the directory only afterwards. Nothing the
        module creates outlives its test.
        """
        # Keep the AF_UNIX address short: a nested workdir can exceed the
        # platform's sun_path limit before the tested condition is reached.
        temp = tempfile.TemporaryDirectory(prefix=prefix,
                                           dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(temp.cleanup)
        return Path(temp.name)

    def test_a_bridge_reply_round_trips_its_value(self):
        directory = self.short_socket_dir("buddy-transport-ok-")
        path = directory / "bridge.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(str(path))
        listener.listen(2)
        seen: list[dict] = []

        def serve() -> None:
            connection, _ = listener.accept()
            with connection:
                request = json.loads(connection.recv(64 * 1024).split(b"\n", 1)[0])
                seen.append(request)
                connection.sendall((json.dumps({"version": 1, "id": request["id"], "ok": True,
                                                "value": {"ready": True}}) + "\n").encode())

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        result = bridge_request({"socketPath": str(path), "token": "t" * 8}, "observe", {},
                                timeout_ms=1000)
        thread.join(timeout=2)
        self.assertEqual(result, {"ok": True, "value": {"ready": True}})
        self.assertEqual(seen[0]["method"], "observe")
        self.assertEqual(seen[0]["version"], 1)
        self.assertEqual(seen[0]["token"], "t" * 8)
        self.assertTrue(seen[0]["id"])

    def test_a_missing_or_refusing_socket_path_is_unreachable(self):
        missing = self.short_socket_dir("buddy-transport-missing-") / "absent.sock"
        self.assertEqual(bridge_request({"socketPath": str(missing), "token": "x"}, "observe", {})["reason"],
                         "bridge-unreachable")
        self.assertEqual(bridge_request({}, "observe", {})["reason"], "bridge-unreachable")

    def test_a_refused_answer_carries_the_bridges_own_code(self):
        directory = self.short_socket_dir("buddy-transport-refused-")
        path = directory / "bridge.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(str(path))
        listener.listen(2)

        def serve() -> None:
            connection, _ = listener.accept()
            with connection:
                request = json.loads(connection.recv(64 * 1024).split(b"\n", 1)[0])
                connection.sendall((json.dumps({"version": 1, "id": request["id"], "ok": False,
                                                "error": "not-ready"}) + "\n").encode())

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        result = bridge_request({"socketPath": str(path), "token": "x"}, "ask",
                                {"inquiryId": "i-1", "question": "hello"}, timeout_ms=1000)
        thread.join(timeout=2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "bridge-refused")
        self.assertEqual(result["code"], "not-ready")

    def test_an_unknown_refusal_code_is_not_invented_into_the_closed_set(self):
        directory = self.short_socket_dir("buddy-transport-internal-")
        path = directory / "bridge.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(str(path))
        listener.listen(2)

        def serve() -> None:
            connection, _ = listener.accept()
            with connection:
                request = json.loads(connection.recv(64 * 1024).split(b"\n", 1)[0])
                connection.sendall((json.dumps({"version": 1, "id": request["id"], "ok": False,
                                                "error": "mysterious"}) + "\n").encode())

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        result = bridge_request({"socketPath": str(path), "token": "x"}, "observe", {}, timeout_ms=1000)
        thread.join(timeout=2)
        self.assertEqual((result["reason"], result["code"]), ("bridge-refused", "internal"))
        # The closed refusal vocabulary itself is the existing one.
        self.assertEqual(BRIDGE_ERRORS,
                         ("bad-request", "unauthorized", "frame-too-large", "timeout",
                          "unsupported-method", "not-ready", "agent-gone", "agent-not-running",
                          "journal-unavailable", "conflict", "too-many", "internal"))

    def test_a_foreign_reply_id_is_mismatched_not_accepted(self):
        directory = self.short_socket_dir("buddy-transport-foreign-")
        path = directory / "bridge.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(str(path))
        listener.listen(2)

        def serve() -> None:
            # Read the request fully, then answer a foreign id, so the client's
            # own frame can never be lost to a reset before the reply arrives.
            connection, _ = listener.accept()
            with connection:
                request = json.loads(connection.recv(64 * 1024).split(b"\n", 1)[0])
                connection.sendall((json.dumps({"version": 1, "id": "not-" + request["id"][:4],
                                                "ok": True, "value": {}}) + "\n").encode())

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        result = bridge_request({"socketPath": str(path), "token": "x"}, "observe", {}, timeout_ms=1000)
        thread.join(timeout=2)
        self.assertEqual(result["reason"], "bridge-mismatched-response")

    def test_a_non_json_reply_is_invalid_and_a_silent_socket_times_out(self):
        directory = self.short_socket_dir("buddy-transport-bad-")
        path = directory / "bridge.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(str(path))
        listener.listen(2)

        def serve() -> None:
            connection, _ = listener.accept()
            with connection:
                connection.recv(64 * 1024)
                connection.sendall(b"this is not json\n")

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        result = bridge_request({"socketPath": str(path), "token": "x"}, "observe", {}, timeout_ms=1000)
        thread.join(timeout=2)
        listener.close()
        self.assertEqual(result["reason"], "bridge-invalid-response")

        silent_path = directory / "silent.sock"
        silent = SilentServer(silent_path)
        self.addCleanup(silent.close)
        started = time.monotonic()
        result = bridge_request({"socketPath": str(silent_path), "token": "x"}, "observe", {},
                                timeout_ms=200)
        self.assertEqual(result["reason"], "bridge-timeout")
        self.assertLess(time.monotonic() - started, 1.5,
                        "the caller's transport window reached the socket")
        silent.close()

    def test_an_oversized_reply_is_refused_not_truncated(self):
        directory = self.short_socket_dir("buddy-transport-big-")
        path = directory / "bridge.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(str(path))
        listener.listen(2)
        served = threading.Event()

        def serve() -> None:
            try:
                connection, _ = listener.accept()
                with connection:
                    request = json.loads(connection.recv(64 * 1024).split(b"\n", 1)[0])
                    blob = "x" * 80 * 1024
                    # The client hangs up on the oversized reply; a broken pipe
                    # here is the refusal working, not a server defect.
                    connection.sendall((json.dumps({"version": 1, "id": request["id"], "ok": True,
                                                    "value": {"blob": blob}}) + "\n").encode())
            except OSError:
                pass
            finally:
                served.set()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        result = bridge_request({"socketPath": str(path), "token": "x"}, "observe", {}, timeout_ms=1000)
        served.wait(timeout=5)
        self.assertEqual(result["reason"], "bridge-response-too-large")

    def test_the_transport_window_stays_the_existing_bounded_one(self):
        self.assertEqual((MIN_TRANSPORT_TIMEOUT_MS, MAX_TRANSPORT_TIMEOUT_MS), (100, 5000))
        # A caller's window is clamped into the same bounds the direct paths had.
        directory = self.short_socket_dir("buddy-transport-clamp-")
        result = bridge_request({"socketPath": str(directory / "absent.sock"), "token": "x"},
                                "observe", {}, timeout_ms=99_000)
        self.assertEqual(result["reason"], "bridge-unreachable")


if __name__ == "__main__":
    unittest.main()
