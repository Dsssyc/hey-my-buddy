"""Owned Node execution engine behind private C-Two RPC resources."""
from __future__ import annotations

from concurrent.futures import Future
import fcntl
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import uuid

import c_two as cc

from .contracts import BuddyControl
from .transport import CONTROL_NAME, MAX_MESSAGE_BYTES, ServiceError, encode_message, get_state_dir


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class LegacySocketGuard:
    """Occupy the previous endpoint so an old binary cannot own the same jobs."""
    def __init__(self, directory: Path):
        self.path = directory / "service.sock"
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.listener.bind(str(self.path))
        except OSError as cause:
            self.listener.close()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(1)
                try:
                    probe.connect(str(self.path))
                except (ConnectionRefusedError, FileNotFoundError):
                    raise ServiceError("STALE_LEGACY_SOCKET", "Existing service.sock was not removed; inspect the prior service before cleanup") from cause
                else:
                    raise ServiceError("LEGACY_SERVICE_RUNNING", "Stop the existing Node service before starting the C-Two control service") from cause
        self.path.chmod(0o600)
        stat = self.path.stat()
        self.identity = (stat.st_dev, stat.st_ino)
        self.listener.listen(16)
        self.listener.settimeout(0.2)
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._serve, name="buddy-legacy-migration-guard", daemon=True)
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
                connection.settimeout(0.2)
                reply = {"error": {"code": "MIGRATED", "message": "Use uv-managed Buddy"}}
                try:
                    # This endpoint only rejects old clients; it never dispatches work.
                    raw = connection.recv(8192)
                    try:
                        request = json.loads(raw.split(b"\n", 1)[0])
                        if isinstance(request, dict) and "id" in request:
                            reply["id"] = request["id"]
                    except (ValueError, UnicodeDecodeError):
                        pass
                except (OSError, socket.timeout):
                    pass
                try:
                    connection.sendall((json.dumps(reply) + "\n").encode())
                except OSError:
                    pass

    def close(self):
        self.stopping.set()
        self.listener.close()
        self.thread.join(timeout=1)
        try:
            stat = self.path.stat()
            if (stat.st_dev, stat.st_ino) == self.identity:
                self.path.unlink()
        except FileNotFoundError:
            pass


class Engine:
    """Owned Node execution engine.

    The engine's stdout carries request/response lines keyed by ``id``. Any line
    with an ``event`` field is an unsolicited engine notification; since the
    MCP-only completion delivery was removed there is no consumer, so such lines
    are ignored rather than mistaken for a response. Runs are read through
    ``wait``/``status``/``result`` on the same endpoint.
    """

    def __init__(self, directory: Path, on_closed):
        node = os.environ.get("BUDDY_NODE") or shutil.which("node")
        if not node:
            raise ServiceError("NODE_NOT_FOUND", "Set BUDDY_NODE to a Node.js executable")
        path = os.environ.get("BUDDY_ENGINE_PATH") or str(Path(__file__).resolve().parents[2] / "service/engine.mjs")
        self.lock = threading.Lock()
        self.pending: dict[str, Future] = {}
        self.closed = False
        self.on_closed = on_closed
        env = {**os.environ, "BUDDY_STATE_DIR": str(directory), "BUDDY_PYTHON": sys.executable}
        self.process = subprocess.Popen([node, path], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None, text=True, bufsize=1)
        self.reader = threading.Thread(target=self._read, name="buddy-engine-reader", daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(MAX_MESSAGE_BYTES + 1)
                if not line:
                    break
                if len(line.encode()) > MAX_MESSAGE_BYTES or not line.endswith("\n"):
                    raise ServiceError("ENGINE_PROTOCOL", "Oversized engine response")
                message = json.loads(line)
                if "event" in message:
                    continue
                with self.lock:
                    future = self.pending.pop(str(message.get("id")), None)
                if future:
                    if "error" in message:
                        future.set_exception(ServiceError(message["error"]["code"], message["error"]["message"]))
                    else:
                        future.set_result(message["result"])
        except Exception as exc:
            self.failure = str(exc)
        finally:
            with self.lock:
                self.closed = True
                pending, self.pending = self.pending, {}
            for future in pending.values():
                future.set_exception(ServiceError("ENGINE_CLOSED", "Execution engine disconnected; request was not replayed"))
            self.on_closed()

    def request(self, method: str, params: dict) -> dict:
        request_id = uuid.uuid4().hex
        future = Future()
        line = encode_message({"id": request_id, "method": method, "params": params}) + "\n"
        with self.lock:
            if self.closed:
                raise ServiceError("ENGINE_CLOSED", "Execution engine is unavailable")
            self.pending[request_id] = future
            try:
                self.process.stdin.write(line)
                self.process.stdin.flush()
            except (OSError, ValueError) as exc:
                self.pending.pop(request_id, None)
                raise ServiceError("ENGINE_CLOSED", "Execution engine disconnected") from exc
        return future.result()

    def shutdown(self):
        with self.lock:
            if self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()
        # The owned engine gracefully stops only its owned jobs before exiting.
        self.process.wait()
        self.reader.join()


class Control:
    """Serialized, token-authenticated facade over the owned Node execution engine.

    Every run/result fact lives in the engine's durable records; this class holds no
    completion or notification state. The previous MCP-only completion inbox,
    notification binding and ``notification_ack`` machinery were removed with the
    MCP server, so historical ``notifications/*.json`` files are never read,
    mutated or replayed by this service.
    """

    def __init__(self, directory: Path, token: str, stopping: threading.Event):
        self.directory, self.token, self.stopping = directory, token, stopping
        self.engine = Engine(directory, stopping.set)

    def dispatch(self, request_json: str) -> str:
        try:
            if not isinstance(request_json, str) or len(request_json.encode()) > MAX_MESSAGE_BYTES:
                raise ServiceError("MESSAGE_TOO_LARGE", "Invalid or oversized request")
            request = json.loads(request_json)
            if not isinstance(request, dict) or not isinstance(request.get("token"), str) or not hmac.compare_digest(request["token"], self.token):
                raise ServiceError("UNAUTHORIZED", "Invalid service token")
            method, params = request.get("method"), request.get("params", {})
            if not isinstance(params, dict) or method not in ("start", "status", "wait", "result", "list", "cancel", "acknowledge", "dashboard", "health", "stop", "inquire"):
                raise ServiceError("INVALID_ARGUMENT", "Invalid control request")
            result = self.engine.request(method, dict(params))
            if method == "stop":
                self.stopping.set()
            return encode_message({"result": result})
        except ServiceError as exc:
            return json.dumps({"error": {"code": exc.code, "message": str(exc)}})
        except (ValueError, TypeError) as exc:
            return json.dumps({"error": {"code": "INVALID_ARGUMENT", "message": str(exc)}})
        except Exception:
            return json.dumps({"error": {"code": "INTERNAL_ERROR", "message": "Service operation failed"}})


def main():
    directory = get_state_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    lock_fd = os.open(directory / "control-daemon.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_fd)
        return
    stopping = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_args: stopping.set())
    token = secrets.token_urlsafe(32)
    control = None
    legacy_guard = None
    endpoint_path = directory / "control.json"
    try:
        legacy_guard = LegacySocketGuard(directory)
        control = Control(directory, token, stopping)
        cc.register(BuddyControl, control, name=CONTROL_NAME, concurrency=cc.ConcurrencyConfig(mode=cc.ConcurrencyMode.PARALLEL))
        atomic_json(endpoint_path, {"address": cc.server_address(), "token": token, "pid": os.getpid()})
        stopping.wait()
    finally:
        if control:
            control.engine.shutdown()
        cc.shutdown()
        if legacy_guard:
            legacy_guard.close()
        try:
            if json.loads(endpoint_path.read_text()).get("token") == token:
                endpoint_path.unlink()
        except (OSError, ValueError):
            pass
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


if __name__ == "__main__":
    main()
