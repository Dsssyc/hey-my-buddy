#!/usr/bin/env python3
"""The single developer-approved ZCode inquiry experiment (2026-10-31 A).

Preflight does not send model input. The approved run consumes a checkout-local
one-use receipt before session/send; a failure never retries that root turn.
Provider files are used natively, never read or copied by this probe. Retained
protocol facts are whitelisted, so provider credentials/headers stay out of it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.buddy.harnesses.discovery import native_environment
from hey_my_buddy.buddy.harnesses.inquiry_bridge import InquiryBridge, _owner_response
from hey_my_buddy.buddy.harnesses.native_support import halt_owned_session
from hey_my_buddy.buddy.harnesses.session_receipts import verify_inquiry_receipt
from hey_my_buddy.buddy.harnesses.version import probe_version
from hey_my_buddy.buddy.harnesses.zcode.config import load_runtime_preferences, provider_paths
from hey_my_buddy.buddy.harnesses.zcode.native_run import configure_session
from hey_my_buddy.buddy.harnesses.zcode.protocol import NativeConnection, NativeError, NativeRpcError, decode_native_failure
from hey_my_buddy.buddy.roles import worker_services
from hey_my_buddy.buddy.roles.turn_io import private_json
from hey_my_buddy.protocol.owned_process import owned_popen


class ProbeConnection(NativeConnection):
    def call(self, method, params):
        request_id = self.next_id
        self.next_id += 1
        self.send({"id": request_id, "method": method, "params": params})
        while request_id not in self.responses:
            self.pump()
        response = self.responses.pop(request_id)
        if "error" in response:
            error = response["error"]
            self.failure_fact = {"method": method, "code": error.get("code")}
            if error.get("code") in (-32601, -32602):
                self.failure_fact["validation"] = error.get("message")
            raise NativeRpcError("native-rpc-error", "native RPC failed")
        return response.get("result", {})


def serve_mcp(path: Path) -> int:
    configuration = json.loads(path.read_text())
    root = Path(configuration["probeRoot"])
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        method = request["method"]
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "inquiry-probe", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [
                {"name": "probe_gate", "description": "Wait once for the probe controller to release this tool.",
                 "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
                {"name": "hey_my_buddy_answer_inquiry", "description": worker_services.ANSWER_DESCRIPTION,
                 "inputSchema": worker_services.ANSWER_SCHEMA}]}
        elif method == "tools/call":
            params = request["params"]
            if params["name"] == "probe_gate":
                (root / "gate-entered").touch()
                until = time.monotonic() + 15
                while not (root / "gate-release").exists() and time.monotonic() < until:
                    time.sleep(0.02)
                result = {"content": [{"type": "text", "text": "Gate released; answer the Host inquiry now."}]}
            else:
                result = worker_services.call_session_tool(params["name"], params.get("arguments"), configuration)
        else:
            result = {}
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
    return 0


def run(paid: bool) -> int:
    root = ROOT / "tmp" / "zcode-inquiry-approved-A" / uuid.uuid4().hex
    root.mkdir(parents=True, mode=0o700)
    work = root / "work"
    work.mkdir()
    (work / "t").mkdir()
    cli = Path("/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs")
    command = [shutil.which("node"), str(cli)]
    environment = native_environment(os.environ, command=command)
    builtin, _ = provider_paths(environment, command)
    environment.update({"TMPDIR": "t", "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": str(builtin),
        "ZCODE_STORAGE_DIR": str(root / "storage"), "ZCODE_SESSION_DB_PATH": str(root / "sessions.sqlite"),
        "ZCODE_LOG_DIR": str(root / "native-logs"), "ZCODE_LOG_CONSOLE": "0"})
    identity = {"taskId": str(uuid.uuid4()), "attemptId": str(uuid.uuid4()),
                "turnId": str(uuid.uuid4()), "generation": 1}
    configuration = {"identity": identity, "inputSha256": hashlib.sha256(b"approved-inquiry-probe").hexdigest(),
        "key": secrets.token_hex(32), "attentionPath": str(root / "attention.json"),
        "inquiryJournalPath": str(root / "inquiry.jsonl"), "probeRoot": str(root)}
    config_path = root / "mcp.json"
    private_json(config_path, configuration)
    server = "inquiry_probe_" + uuid.uuid4().hex[:8]
    gate_tool = f"mcp__{server}__probe_gate"
    answer_tool = f"mcp__{server}__hey_my_buddy_answer_inquiry"
    model = {"model": "GLM-5.3-Flash", "effort": "low"}
    report = {"harnessVersion": probe_version(command, environment, cwd=str(work),
        deadline=time.monotonic() + 5, owned=[]), "paidRunRequested": paid,
        "checks": {}, "events": [], "commands": [], "rootStarts": [], "phase": "prepare"}
    session_id = None
    root_turn = None
    guide_command = None
    root_input = None
    queued_events = []
    drained_events = []
    answers = {}
    snapshots = {}
    settled = False
    bridge = InquiryBridge(identity=identity, journal_path=configuration["inquiryJournalPath"],
        error_factory=NativeError, event_metadata=lambda _: {"kind": "probe"},
        delivery_mode="realtime", checkpoint_tool=None)
    process = owned_popen([*command, "app-server", "--cwd", str(work)], env=environment,
        cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True)
    handle = ProcessHandle(process, stop_channel="stdin", log_paths={})
    connection = ProbeConnection(process, time.monotonic() + 300, threading.Event(),
        runtime_preferences=load_runtime_preferences(environment))

    def observe(message, ordinal):
        nonlocal root_turn, settled
        params = message.get("params", {})
        if message.get("method") == "v4/conversation/frame":
            if params.get("kind") == "complete":
                payload = params["frame"]["payload"]
                if payload.get("kind") == "snapshot":
                    snapshots[params["subscriptionId"]] = payload["snapshot"]
            return
        if params.get("sessionId") != session_id:
            return
        if message.get("method") == "state.updated":
            settled |= params.get("reason") == "prompt_completed"
            return
        if message.get("method") != "session/event":
            return
        kind = params.get("type")
        data = params.get("payload", {})
        if kind == "turn.started":
            report["rootStarts"].append(params["turnId"])
            if root_turn is not None or data.get("inputId") != root_input:
                raise RuntimeError("another or unbound root turn started")
            root_turn = params["turnId"]
        if kind in ("turn.started", "turn.steerQueued", "turn.steerDrained", "turn.completed"):
            fact = {"type": kind, "sessionId": session_id, "turnId": params.get("turnId"), "seq": params["seq"],
                "payload": {key: data[key] for key in ("inputId", "pendingInputId", "pendingInputIds", "targetTurnId",
                    "delivery", "resultType", "injectedMessageIds", "drainedInputs") if key in data}}
            report["events"].append(fact)
            if kind == "turn.steerQueued":
                queued_events.append(fact)
            if kind == "turn.steerDrained":
                drained_events.append(fact)
        if kind == "turn.failed":
            report["nativeFailure"] = decode_native_failure(data)
            raise RuntimeError("native root failed")
        if kind == "tool.updated" and params.get("turnId") == root_turn and not any(
                data.get(key) for key in ("source", "parentToolCallId", "childSessionId", "childToolCallId", "agentId", "background")):
            if data.get("kind") == "scheduled" and data.get("toolName") == answer_tool:
                answers[data["toolCallId"]] = None
            if data.get("kind") == "result" and data.get("toolCallId") in answers:
                result = data.get("result", {})
                if result.get("success") is True and result.get("truncated") is False:
                    receipt = verify_inquiry_receipt(result["content"], configuration, "inquiry-answer")
                    bridge.record_answer(receipt, data["toolCallId"])
                    answers[data["toolCallId"]] = receipt
                    report["answer"] = {key: receipt[key] for key in ("inquiryId", "answer", "questionSha256")}

    connection.observe = observe

    def queue_snapshot():
        response = connection.call("v4/conversation/subscribe", {"topic": "conversation/" + session_id,
            "connectionId": server, "clientMode": "web-remote-replayable"})
        sub = response["ack"]["subscriptionId"]
        while sub not in snapshots:
            connection.pump()
        snapshot = snapshots.pop(sub)
        report.setdefault("snapshots", []).append({key: snapshot[key] for key in
            ("revision", "logEpoch", "control", "queue")})
        connection.call("v4/conversation/unsubscribe", {"topic": "conversation/" + session_id,
            "connectionId": server, "subscriptionId": sub})
        return snapshot

    def v4(kind, payload, cas=None):
        command_id = str(uuid.uuid4())
        envelope = {"commandId": command_id, "clientId": server, "sessionId": session_id,
            "type": kind, "payload": payload, "issuedAt": int(time.time() * 1000)}
        if cas is not None:
            envelope.update(baseRevision=cas["revision"], baseLogEpoch=cas["logEpoch"])
        result = connection.call("v4/command", envelope)
        report["commands"].append({"request": envelope, "response": {
            key: result[key] for key in ("commandId", "status", "revisionAtDecision", "reasonCode", "result") if key in result}})
        return command_id, result

    try:
        connection.call("runtime/capabilities", {})
        initial = connection.call("session/create", {"workspace": {"workspacePath": str(work), "workspaceKey": str(work)},
            "mode": "yolo", "mcpServers": [{"name": server, "command": sys.executable,
                "args": [str(Path(__file__).resolve()), "--mcp", str(config_path)],
                "env": [], "isolation": "session", "protocolVersion": "legacy"}]})
        session_id = initial["session"]["sessionId"]
        current = initial.get("settings", {}).get("model", {}).get("current") or {}
        rows = initial.get("settings", {}).get("model", {}).get("available", [])
        choices = [row["ref"] for row in rows if row.get("ref", {}).get("modelId") == model["model"]]
        if not choices:
            raise RuntimeError("approved model is absent from the native catalog")
        chosen = next((ref for ref in choices if ref["providerId"] == current.get("providerId")), choices[0])
        model["provider"] = chosen["providerId"]
        report["resolved"] = configure_session(connection, initial, model)
        connection.call("session/subscribe", {"sessionId": session_id,
            "deliveryKind": "web-remote-replayable", "includeSnapshot": False})
        snapshot = queue_snapshot()
        v4("setAutoDrain", {"autoDrain": False}, snapshot)
        snapshot = queue_snapshot()
        report["checks"]["autoDrainDisabled"] = snapshot["queue"]["autoDrain"] is False
        if not report["checks"]["autoDrainDisabled"]:
            raise RuntimeError("autoDrain was not disabled")
        report["phase"] = "preflight-complete"
        if paid:
            approval = ROOT / "tmp" / "zcode-inquiry-approval-2026-10-31-A.used"
            fd = os.open(approval, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(str(root.relative_to(ROOT)))
            report["phase"] = "one-approved-root"
            prompt = ("This is one approved private protocol experiment. Use the two named MCP tools; "
                "native ToolSearch is allowed only if needed to discover them. "
                f"First call {gate_tool} exactly once with an empty object. While it waits, a Host inquiry will arrive. "
                f"After the gate returns, answer that inquiry using {answer_tool} with its exact inquiryId and marker. "
                "Then end this native turn with the short final text PROBE-DONE. Do not call other tools or ask questions.")
            root_input = "approved-inquiry-root-" + uuid.uuid4().hex
            connection.call("session/send", {"sessionId": session_id, "inputId": root_input,
                                             "content": prompt})
            while not (root / "gate-entered").exists() or root_turn is None:
                if settled:
                    raise RuntimeError("root completed without entering the probe gate")
                connection.pump()
            bridge.start()
            bridge.activate(session_id)
            nonce = "LIME-" + uuid.uuid4().hex[:12]
            question_id = "approved-guide-A"
            question = "Report the exact marker " + nonce + " and continue."
            bridge._ask({"inquiryId": question_id, "question": question}, _owner_response)
            guide_text = (f"Host inquiry. Answer using {answer_tool}. "
                + json.dumps({"inquiryId": question_id, "question": question}))
            guide_command, _ = v4("sendText", {"text": guide_text, "requestedDelivery": "guide"})
            held_command, _ = v4("sendText", {"text": "Retained inquiry C: do not start another turn.", "requestedDelivery": "queue"})
            (root / "gate-release").touch()
            while not settled:
                connection.pump()
            queued = [event for event in queued_events if event["payload"].get("inputId") == guide_command]
            drained = [event for event in drained_events if queued and
                queued[0]["payload"]["pendingInputId"] in event["payload"]["pendingInputIds"]]
            report["checks"]["guideDrainedInRoot"] = bool(queued and drained and all(
                event["turnId"] == root_turn and event["payload"]["targetTurnId"] == root_turn
                for event in (queued[0], drained[0])))
            report["checks"]["modelAnsweredThroughTool"] = nonce in report.get("answer", {}).get("answer", "")
            until = time.monotonic() + 0.5
            while time.monotonic() < until:
                connection.pump()
            snapshot = queue_snapshot()
            items = [item for item in snapshot["queue"]["items"] if item["sourceCommandId"] == held_command]
            report["checks"]["heldAfterRootEnd"] = len(items) == 1 and snapshot["queue"]["autoDrain"] is False
            if items:
                v4("deleteQueueItem", {"queueItemId": items[0]["queueItemId"]}, snapshot)
            snapshot = queue_snapshot()
            report["checks"]["removedAfterCasDelete"] = not any(
                item["sourceCommandId"] == held_command for item in snapshot["queue"]["items"])
            until = time.monotonic() + 0.5
            while time.monotonic() < until:
                connection.pump()
            report["checks"]["oneRootOnly"] = len(report["rootStarts"]) == 1
            if not all(report["checks"].values()):
                raise RuntimeError("approved experiment did not establish every condition")
            report["phase"] = "verified-one-root"
    except Exception as error:
        report["failure"] = {"type": type(error).__name__, "code": getattr(error, "code", None),
                             "phase": report["phase"]}
        if hasattr(connection, "failure_fact"):
            report["failure"]["rpc"] = connection.failure_fact
    finally:
        try:
            if session_id and process.poll() is None:
                connection.deadline = min(connection.deadline, time.monotonic() + 3)
                connection.call("session/close", {"sessionId": session_id})
        except NativeError as error:
            report["closeFailure"] = error.code
        finally:
            report["shutdownConfirmed"], report["shutdownEscalated"] = halt_owned_session(handle, time.monotonic() + 5)
            process.stdout.close()
            bridge.close()
            private_json(root / "report.json", report)
            print(json.dumps({"output": str(root.relative_to(ROOT)), "phase": report["phase"],
                "checks": report["checks"], "failure": report.get("failure"), "shutdownConfirmed": report["shutdownConfirmed"]}))
    return 0 if "failure" not in report and report["shutdownConfirmed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--run-approved-once", action="store_true")
    mode.add_argument("--mcp", type=Path)
    args = parser.parse_args()
    raise SystemExit(serve_mcp(args.mcp) if args.mcp else run(args.run_approved_once))
