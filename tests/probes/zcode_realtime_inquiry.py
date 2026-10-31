#!/usr/bin/env python3
"""Inspect installed ZCode's inquiry commands without a model or daily state.

All native storage and configuration live below the checkout's ignored tmp/.
The macOS sandbox denies outbound network. No session/send or startNow input
is admitted; the only valid sendText explicitly requests queue. Exit 2 means
queue admission/readback remains unresolved, rather than a passing gate.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.buddy.harnesses.native_support import halt_owned_session
from hey_my_buddy.buddy.harnesses.version import probe_version
from hey_my_buddy.buddy.harnesses.zcode.protocol import NativeConnection
from hey_my_buddy.protocol.owned_process import owned_popen


class ProbeConnection(NativeConnection):
    """Keep native validation errors rather than the public redacted summary."""

    def call(self, method: str, params: dict) -> dict:
        request_id = self.next_id
        self.next_id += 1
        self.send({"id": request_id, "method": method, "params": params})
        while request_id not in self.responses:
            self.pump()
        response = self.responses.pop(request_id)
        captures.append({"method": method, "params": params, "response": response})
        return response


captures: list[dict] = []


def main() -> int:
    cli = Path("/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs")
    builtin = cli.parent.parent / "config/provider/zcode-builtin.json"
    root = ROOT / "tmp" / "zcode-realtime-inquiry" / uuid.uuid4().hex
    root.mkdir(parents=True, mode=0o700)
    for name in ("home", "work", "storage", "logs"):
        (root / name).mkdir()
    personal = root / "provider.json"
    personal.write_text(json.dumps({"schemaVersion": 1, "config": {
        "providerOrder": ["private-probe"], "providerConfigRules": {"providerRules": [{
            "providerId": "private-probe", "templateId": "zai-standard-api",
            "providerName": "Private offline probe", "enabled": True, "config": {
                "group": "standard-personal", "access": {"type": "api-key", "apiKey": "not-a-secret"},
                "api": {"type": "openai-chat-completions", "baseUrl": "http://127.0.0.1:9"},
                "personalModelIds": ["GLM-5.3-Flash"]}}]},
        "modelConfigRules": {"providerModelRules": [], "manualProviderModelRules": []},
        "defaultModelSelection": {"providerId": "private-probe", "modelId": "GLM-5.3-Flash",
                                  "options": {"reasoningLevel": "low"}}}}))
    (root / "work" / "t").mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(root / "home"),
           "TMPDIR": "t", "LANG": "en_US.UTF-8",
           "ZCODE_DATA_BASE_DIR": str(root / "home"),
           "ZCODE_STORAGE_DIR": str(root / "storage"),
           "ZCODE_SESSION_DB_PATH": str(root / "sessions.sqlite"),
           "ZCODE_LOG_DIR": str(root / "logs"), "ZCODE_LOG_CONSOLE": "0",
           "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": str(builtin),
           "ZCODE_BUILTIN_PROVIDER_BUNDLED_CONFIG_FILE": str(builtin),
           "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE": str(personal)}
    command = [shutil.which("sandbox-exec"), "-p",
               "(version 1) (allow default) (deny network-outbound)",
               shutil.which("node"), str(cli)]
    session_id = None
    summary = {"version": probe_version(command, env, cwd=str(root / "work"),
        deadline=time.monotonic() + 5, owned=[]), "preconditionsConfirmed": False}
    with (root / "stderr.log").open("wb") as stderr:
        process = owned_popen([*command, "app-server", "--cwd", str(root / "work")],
                              cwd=root / "work", env=env, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=stderr,
                              start_new_session=True, close_fds=True)
        handle = ProcessHandle(process, stop_channel="stdin", log_paths={})
        connection = ProbeConnection(process, time.monotonic() + 45, threading.Event(),
            runtime_preferences={key: {"value": False, "source": "private-probe"}
                                 for key in ("memoryEnabled", "nativeSearchEnhancementsEnabled")})
        connection.observe = lambda message, ordinal: captures.append({"event": message})
        try:
            connection.call("runtime/capabilities", {})
            created = connection.call("session/create", {"workspace": {
                "workspacePath": str(root / "work"), "workspaceKey": str(root / "work")},
                "mode": "build"})
            session_id = created["result"]["session"]["sessionId"]
            connection.call("session/setModel", {"sessionId": session_id,
                "model": {"providerId": "private-probe", "modelId": "GLM-5.3-Flash",
                          "options": {"reasoningLevel": "low"}},
                "persistAsWorkspaceLastUsed": False})
            connection.call("session/subscribe", {"sessionId": session_id,
                "deliveryKind": "web-remote-replayable", "includeSnapshot": True})
            for kind in ("sendText", "deleteQueueItem"):
                connection.call("v4/command", {"commandId": str(uuid.uuid4()),
                    "clientId": "hmb-private-inquiry-probe", "sessionId": session_id,
                    "type": kind, "payload": {}, "issuedAt": int(time.time() * 1000)})
            queued = connection.call("v4/command", {"commandId": str(uuid.uuid4()),
                "clientId": "hmb-private-inquiry-probe", "sessionId": session_id,
                "type": "sendText", "payload": {"text": "private held queue probe",
                    "requestedDelivery": "queue"}, "issuedAt": int(time.time() * 1000)})
            summary["queueAdmission"] = queued.get("result", queued)
            summary["blockedAt"] = (
                "queue-admission" if queued.get("result", {}).get("status") != "accepted"
                else "queue-readback")
            connection.call("session/subscribe", {"sessionId": session_id,
                "deliveryKind": "web-remote-replayable", "includeSnapshot": True})
        finally:
            try:
                if session_id and process.poll() is None:
                    connection.call("session/close", {"sessionId": session_id})
            finally:
                summary["shutdownConfirmed"], summary["shutdownEscalated"] = halt_owned_session(
                    handle, time.monotonic() + 5)
                process.stdout.close()
                (root / "captures.json").write_text(json.dumps(captures, indent=2))
                summary["rootStarts"] = sum(
                    item.get("event", {}).get("params", {}).get("type") == "turn.started"
                    for item in captures)
                (root / "summary.json").write_text(json.dumps(summary, indent=2))
                print(json.dumps({"output": str(root.relative_to(ROOT)), **summary}))
    if not summary["shutdownConfirmed"] or summary["rootStarts"]:
        return 1
    return 0 if summary["preconditionsConfirmed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
