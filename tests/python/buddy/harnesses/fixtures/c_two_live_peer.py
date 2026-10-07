"""A real C-Two peer for the step 5-A live backend tests.

Run as a script, the peer hosts one :class:`CTwoLiveEndpoint` in its own
process and answers a one-line JSON command protocol on stdin, so the tests
drive a real registered endpoint, a real owner loop and the real clean
lifecycle. Imported (by file path) instead, it exposes ``TEST_CRM`` — the
minimal three-operation contract the peer registers and the in-process tests
connect with. No harness, model or credential is ever touched.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time

import c_two as cc

from hey_my_buddy.buddy.harnesses import c_two_live as ctl
from hey_my_buddy.buddy.harnesses.live import LiveCapabilities
from hey_my_buddy.buddy.harnesses.run_contract import RunIdentity
from hey_my_buddy.protocol import rpc_config


@cc.crm(namespace="hey.my.buddy.test.live", version="0.1.0")
class TEST_CRM:
    """The test live contract: exactly the three named operations."""

    def capabilities(self, request_json: str) -> str:
        ...

    def request(self, request_json: str) -> str:
        ...

    def observe(self, request_json: str) -> str:
        ...


class StallingLive:
    """A same-contract implementation whose every operation sleeps first.

    A real C-Two server that never answers within a transport window: the
    frames are ignored, each named call simply stalls for the configured
    seconds before returning a syntactically valid reply.
    """

    def __init__(self, seconds: float):
        self._seconds = seconds

    def capabilities(self, request_json: str) -> str:
        time.sleep(self._seconds)
        return json.dumps({"inquiryDelivery": "cooperative-checkpoint"})

    def request(self, request_json: str) -> str:
        time.sleep(self._seconds)
        return json.dumps({"status": "queued", "observed": True, "state": "queued"})

    def observe(self, request_json: str) -> str:
        time.sleep(self._seconds)
        return json.dumps({"observed": True})


def _reply(value: dict) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _write_token(path: str, token: str) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, token.encode())
    finally:
        os.close(fd)


def serve() -> int:
    # Same-user local IPC only, exactly as the daemon sanitizes its service
    # processes: no relay anchor, no inherited C-Two environment file.
    os.environ["C2_RELAY_ANCHOR_ADDRESS"] = ""
    os.environ["C2_ENV_FILE"] = ""
    endpoint: ctl.CTwoLiveEndpoint | None = None
    stalled_name: str | None = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            command = json.loads(line)
        except ValueError:
            _reply({"ok": False, "error": "malformed command"})
            continue
        op = command.get("op")
        try:
            if op == "start" and "stallSeconds" in command:
                # A stalling endpoint: the same contract registered with an
                # implementation that sleeps, so the client's bounded call
                # faces a real peer that never answers within the window.
                rpc_config.configure_server()
                rpc_config.configure_client()
                stalled_name = command.get("name") or ctl.random_person_name()
                cc.register(TEST_CRM, StallingLive(float(command["stallSeconds"])),
                            name=stalled_name,
                            concurrency=cc.ConcurrencyConfig(mode=cc.ConcurrencyMode.PARALLEL))
                descriptor = ctl.LiveEndpointDescriptor(
                    address=cc.server_address(), name=stalled_name,
                    instance_id=secrets.token_hex(32), host_pid=os.getpid())
                _write_token(command["tokenPath"], secrets.token_hex(32))
                _reply({"ok": True, "descriptor": descriptor.to_payload(),
                        "configuredRoles": list(rpc_config.configured_roles())})
            elif op == "start":
                identity = RunIdentity.from_payload(command["identity"])
                endpoint = ctl.CTwoLiveEndpoint(
                    identity, LiveCapabilities(inquiry_delivery=command.get("delivery",
                                                                            "cooperative-checkpoint")),
                    TEST_CRM, name=command.get("name"),
                    instance_id=secrets.token_hex(32), token=secrets.token_hex(32))
                descriptor = endpoint.start()
                ctl.write_ready_material(command["readyPath"], descriptor)
                # The constructing side is the one trust position that holds the
                # narrow token (the production holder passes it in memory); the
                # peer hands it to the driver through this private 0600 file.
                _write_token(command["tokenPath"], endpoint._token)
                _reply({"ok": True, "descriptor": descriptor.to_payload(),
                        "configuredRoles": list(rpc_config.configured_roles())})
            elif op == "configuredRoles":
                _reply({"ok": True, "roles": list(rpc_config.configured_roles())})
            elif op == "serverAddress":
                _reply({"ok": True, "address": cc.server_address()})
            elif op == "consume":
                request = endpoint.consume_request(float(command.get("timeout", 0.2)))
                _reply({"ok": True, "request": request.to_payload() if request else None})
            elif op == "autoSettle":
                # A simulated owner loop: consume and settle every request
                # with the given reply shape for the given seconds. This is
                # the test stand-in for the controller's own owner loop the
                # Host will wire; the endpoint itself adds no thread.
                from hey_my_buddy.buddy.harnesses.live import LiveReply
                shape = command.get("reply") or {"status": "queued", "observed": True,
                                                 "state": "queued"}

                def owner() -> None:
                    deadline = time.monotonic() + float(command.get("seconds", 30))
                    while time.monotonic() < deadline:
                        request = endpoint.consume_request(0.2)
                        if request is None:
                            continue
                        try:
                            endpoint.settle_request(request.request_id,
                                                    LiveReply.from_payload(shape))
                        except Exception:
                            pass

                threading.Thread(target=owner, name="peer-owner", daemon=True).start()
                _reply({"ok": True})
            elif op == "pendingCount":
                _reply({"ok": True, "count": len(endpoint._pending)})
            elif op == "settle":
                from hey_my_buddy.buddy.harnesses.live import LiveReply
                endpoint.settle_request(command["requestId"], LiveReply.from_payload(command["reply"]))
                _reply({"ok": True})
            elif op == "publishJournal":
                endpoint.publish_journal(command["fact"])
                _reply({"ok": True})
            elif op == "publishInquiry":
                from hey_my_buddy.buddy.harnesses.live import InquiryState
                endpoint.publish_inquiry_state(InquiryState.from_payload(command["state"]))
                _reply({"ok": True})
            elif op == "publishActivity":
                _reply({"ok": True, "changed": endpoint.publish_activity(command["payload"])})
            elif op == "publishObservation":
                endpoint.publish_observation(command["value"])
                _reply({"ok": True})
            elif op == "close":
                endpoint.close(reason=command.get("reason", "run-finished"))
                _reply({"ok": True})
            elif op == "stop":
                if endpoint is not None:
                    endpoint.stop()
                else:
                    cc.unregister(stalled_name)
                    cc.shutdown()
                _reply({"ok": True, "addressAfter": cc.server_address()})
                return 0
            else:
                _reply({"ok": False, "error": f"unknown op {op!r}"})
        except Exception as error:  # the driver sees the failure verbatim
            _reply({"ok": False, "error": f"{type(error).__name__}: {error}"})
    return 0


def main() -> int:
    extra = sys.argv[3:]
    if extra and extra[0] == "rebind":
        # A replacement-scenario peer: rebind the dead endpoint's exact
        # server_id so C-Two itself replaces the socket file at that address.
        rpc_config.configure_server()
        cc.set_server(server_id=extra[1])
        rpc_config.configure_client()
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
