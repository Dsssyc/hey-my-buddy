"""The one socket client of the existing inquiry bridges (ADR-025 step 2-C2).

Every consumer of an inquiry bridge speaks this one newline-terminated JSON
frame protocol through :func:`bridge_request`: the blackboard's inquiry and the
ZCode live binding use this module, and no harness keeps a second copy of the
client. The frame sizes, the id correlation, the transport window and the
refusal classification are the existing bridges' own and are preserved exactly:
``bridge-refused`` answers carry the bridge's own code from
:data:`BRIDGE_ERRORS` instead of folding it away.
"""
from __future__ import annotations

import json
import socket
import uuid

PROTOCOL_VERSION = 1
DEFAULT_TRANSPORT_TIMEOUT_MS = 1500
MIN_TRANSPORT_TIMEOUT_MS = 100
MAX_TRANSPORT_TIMEOUT_MS = 5000
MAX_RESPONSE_BYTES = 64 * 1024

BRIDGE_ERRORS = (
    "bad-request",
    "unauthorized",
    "frame-too-large",
    "timeout",
    "unsupported-method",
    "not-ready",
    "agent-gone",
    "agent-not-running",
    "journal-unavailable",
    "conflict",
    "too-many",
    "internal",
)


def bridge_request(credentials: dict, method: str, payload: dict, *, timeout_ms: int = DEFAULT_TRANSPORT_TIMEOUT_MS) -> dict:
    """One bounded newline-terminated JSON frame over the bridge's Unix socket."""
    socket_path = credentials.get("socketPath")
    if not isinstance(socket_path, str) or not socket_path:
        return {"ok": False, "reason": "bridge-unreachable"}
    frame = {
        "version": PROTOCOL_VERSION,
        "id": str(uuid.uuid4()),
        "token": credentials.get("token"),
        "method": method,
        **payload,
    }
    raw = json.dumps(frame, ensure_ascii=False).encode("utf-8") + b"\n"
    if len(raw) > 16 * 1024:
        return {"ok": False, "reason": "bridge-response-too-large"}
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(max(MIN_TRANSPORT_TIMEOUT_MS, min(timeout_ms, MAX_TRANSPORT_TIMEOUT_MS)) / 1000.0)
    try:
        connection.connect(socket_path)
        connection.sendall(raw)
        chunks = bytearray()
        while b"\n" not in chunks:
            block = connection.recv(4096)
            if not block:
                break
            chunks.extend(block)
            if len(chunks) > MAX_RESPONSE_BYTES:
                return {"ok": False, "reason": "bridge-response-too-large"}
    except FileNotFoundError:
        return {"ok": False, "reason": "bridge-unreachable"}
    except ConnectionRefusedError:
        return {"ok": False, "reason": "bridge-unreachable"}
    except TimeoutError:
        return {"ok": False, "reason": "bridge-timeout"}
    except OSError:
        return {"ok": False, "reason": "bridge-write-failed"}
    finally:
        connection.close()
    try:
        reply = json.loads(bytes(chunks).split(b"\n", 1)[0])
    except ValueError:
        return {"ok": False, "reason": "bridge-invalid-response"}
    if not isinstance(reply, dict) or reply.get("id") != frame["id"]:
        return {"ok": False, "reason": "bridge-mismatched-response"}
    if reply.get("ok") is not True:
        code = reply.get("error")
        return {"ok": False, "reason": "bridge-refused", "code": code if code in BRIDGE_ERRORS else "internal"}
    return {"ok": True, "value": reply.get("value")}


__all__ = [
    "BRIDGE_ERRORS", "DEFAULT_TRANSPORT_TIMEOUT_MS", "MAX_RESPONSE_BYTES", "MAX_TRANSPORT_TIMEOUT_MS",
    "MIN_TRANSPORT_TIMEOUT_MS", "PROTOCOL_VERSION", "bridge_request",
]
