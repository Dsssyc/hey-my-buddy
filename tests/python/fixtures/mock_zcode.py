#!/usr/bin/env python3
"""Deterministic native-protocol fixture; calls the real private MCP finish bridge.

It also implements the confirmed v4 command surface (`v4/command` sendText with an
explicit guide/queue delivery) and the documented reverse-RPC interactive requests,
so the controller's inquiry, preemption and attention paths are exercised with no
model and no network.
"""
import json
import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

if "--version" in sys.argv:
    print("fixture-0.16.9")
    raise SystemExit(0)

case = os.environ.get("BUDDY_ZCODE_TEST_CASE", "ok")
state = Path(os.environ["ZCODE_SESSION_DB_PATH"]).with_suffix(".fixture.json")
log_dir = Path(os.environ["ZCODE_LOG_DIR"])
instance = hashlib.sha256(os.environ["ZCODE_LOG_DIR"].encode()).hexdigest()[:16]
session_id = "sess-fixture-" + instance
turn_id = "turn-fixture-" + instance
selection = {"providerId": "fixture-api", "modelId": "fixture-model", "options": {"reasoningLevel": "low"}}
workspace = {}
mcp = []
pending_create = None
sequence = 0
child = None
prompt_input_id = None
commands = []
methods = []
completed = False
completion_lock = threading.Lock()


def send(value):
    print(json.dumps(value), flush=True)


def record_commands():
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "commands.jsonl").write_text("".join(json.dumps(item) + "\n" for item in commands))
    except OSError:
        pass


def snapshot():
    return {"session": {"sessionId": session_id, "sessionKind": "interactive", "workspace": workspace},
            "settings": {"model": {"current": selection, "available": [
                {"ref": {"providerId": "fixture-api", "modelId": "fixture-model"}, "label": "Fixture model",
                 "providerLabel": "Fixture API", "contextWindow": 200000,
                 "reasoning": {"levels": [] if case == "no-effort" else [{"value": "low"}, {"value": "high"}], "defaultLevel": "low"},
                 "properties": {"inputFormat": {"supportsText": True}}}]},
                "thoughtLevel": {"current": selection["options"]["reasoningLevel"]}}}


def event(kind, payload, *, root=None, turn=None):
    global sequence
    sequence += 1
    send({"method": "session/event", "params": {"type": kind, "sessionId": root or session_id, "turnId": turn or turn_id,
          "seq": sequence, "eventId": "event-" + str(sequence), "payload": payload}})


def mcp_call(tool, arguments):
    """Call one tool on the real private MCP bridge and return its raw content."""
    global child
    config = mcp[0]
    env = {**os.environ, **{x["name"]: x["value"] for x in config.get("env", [])}}
    child = subprocess.Popen([config["command"], *config["args"]], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    child.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": tool, "arguments": arguments}}) + "\n")
    child.stdin.flush()
    response = json.loads(child.stdout.readline())["result"]
    child.stdin.close()
    child.wait(timeout=3)
    if response.get("isError"):
        return None, response["content"][0]["text"]
    return response["content"][0]["text"], None


def finish():
    outcome = {"disposition": "completed", "summary": "fixture work completed", "remaining": [], "decisions": [], "artifacts": [], "request": None}
    if case == "assistance":
        outcome.update(disposition="assistance", request={"summary": "help", "attempted": "examined fixture", "neededWork": "review decision", "expectedArtifacts": [], "acceptance": "decision reviewed"})
    if case == "attention-outcome":
        outcome.update(disposition="attention", request={"summary": "native approval needed", "attempted": "requested fixture permission", "neededWork": "Host decision", "expectedArtifacts": [], "acceptance": "decision recorded"})
    content, _error = mcp_call("buddy_finish_turn", outcome)
    return content


def reply(inquiry_id):
    content, error = mcp_call("buddy_inquiry_reply", {"inquiryId": inquiry_id, "answer": "fixture answer: still running"})
    if error is not None:
        return None, error
    tool = "mcp__" + mcp[0]["name"] + "__buddy_inquiry_reply"
    event("tool.updated", {"kind": "scheduled", "toolName": tool, "toolCallId": "reply-call-1"})
    event("tool.updated", {"kind": "result", "toolCallId": "reply-call-1",
                           "result": {"success": True, "truncated": False, "content": content}})
    return content, None


def complete_turn():
    """Emit the single terminal root finish exactly once."""
    global completed
    with completion_lock:
        if completed:
            return
        completed = True
    content = finish()
    tool = "mcp__" + mcp[0]["name"] + "__buddy_finish_turn"
    if case == "forged-receipt":
        receipt = json.loads(content)
        receipt["outcome"]["summary"] = "changed after the bridge signed it"
        content = json.dumps(receipt)
    if case == "child-first":
        event("tool.updated", {"kind": "scheduled", "toolName": tool, "toolCallId": "child-call"}, root="sess-child", turn="turn-child")
        event("tool.updated", {"kind": "result", "toolCallId": "child-call", "result": {"success": True, "truncated": False, "content": content}}, root="sess-child", turn="turn-child")
    event_root = "sess-wrong" if case == "wrong-root" else session_id
    event_turn = "turn-wrong" if case == "wrong-turn" else turn_id
    event("tool.updated", {"kind": "scheduled", "toolName": tool, "toolCallId": "root-call", "inputOmitted": True, "inputRef": "model_stream"}, root=event_root, turn=event_turn)
    event("tool.updated", {"kind": "result", "toolCallId": "root-call", "result": {"success": case != "tool-error", "truncated": case == "truncated", "content": content}}, root=event_root, turn=event_turn)
    if case == "duplicate":
        event("tool.updated", {"kind": "scheduled", "toolName": tool, "toolCallId": "second-call"})
    event("turn.completed", {"inputId": prompt_input_id, "resultType": "error_during_execution" if case == "turn-failure" else "success", "response": "fixture final text is not the outcome"})
    send({"method": "state.updated", "params": {"sessionId": session_id, "reason": "prompt_failed" if case == "prompt-failure" else "prompt_completed"}})


def journal_path():
    """The private journal the controller and the reply tool share."""
    try:
        config_path = Path(mcp[0]["args"][mcp[0]["args"].index("--config") + 1])
        return Path(json.loads(config_path.read_text())["journalPath"])
    except (ValueError, OSError, KeyError, IndexError):
        return None


def wait_for_delivery(inquiry_id, timeout=4.0):
    """Wait until the controller recorded this question as delivered."""
    path = journal_path()
    deadline = time.monotonic() + timeout
    while path is not None and time.monotonic() < deadline:
        try:
            for line in path.read_text().splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("inquiryId") == inquiry_id and record.get("state") == "delivered":
                    return True
        except OSError:
            pass
        time.sleep(0.05)
    return False


def request_client(request_id, method, params):
    """Send one documented reverse request and read the controller's response."""
    send({"id": request_id, "method": method, "params": params})
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("id") == request_id:
            return message
        if "method" in message:
            handle_message(message)
    return None


def interactive_attention():
    """Exercise the documented interactive requests a governed turn cannot grant."""
    permission = request_client("server-perm-1", "interaction/requestPermission",
                                {"sessionId": session_id, "toolCallId": "fixture-tool", "toolName": "Bash"})
    user_input = request_client("server-input-1", "interaction/requestUserInput",
                                {"sessionId": session_id, "interactionId": "fixture-question"})
    unknown = request_client("server-browser-1", "interaction/browserExecute",
                             {"requestId": "fixture-browser", "command": {"kind": "unsupported-fixture"}})
    return {"permission": permission, "userInput": user_input, "unknown": unknown}


def handle_message(message):
    global pending_create, workspace, mcp, session_id, selection, prompt_input_id, sequence
    if "method" not in message:
        if pending_create:
            send({"id": pending_create, "result": snapshot()})
            pending_create = None
        return
    method, params, request_id = message["method"], message.get("params", {}), message["id"]
    methods.append(method)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "methods.jsonl").write_text("".join(item + "\n" for item in methods))
    except OSError:
        pass
    if method == "runtime/capabilities":
        if case == "invalid-json":
            print('{"id":1,"result":{},"result":{}}', flush=True)
            return
        result = {"independentPlanState": True}
    elif method in ("session/create", "session/resume"):
        workspace, mcp = params["workspace"], params.get("mcpServers", [])
        if method == "session/resume":
            if not state.exists() or case == "resume-missing":
                send({"id": request_id, "error": {"code": -32004, "message": "Session not found"}})
                return
            stored = json.loads(state.read_text())
            if stored["sessionId"] != params["sessionId"] or not mcp:
                raise RuntimeError("native resume lost its exact identity or MCP reinjection")
            session_id, selection = stored["sessionId"], stored["selection"]
            if case == "resume-wrong-root":
                session_id = "sess-different-root"
        pending_create = request_id
        send({"id": "server-preferences", "method": "session/requestRuntimePreferences", "params": {"sessionId": session_id, "scope": "runtime-materialization"}})
        return
    elif method == "session/setModel":
        if case != "config-mismatch":
            selection = params["model"]
        result = snapshot()
    elif method == "session/setThoughtLevel":
        if case != "config-mismatch":
            selection["options"]["reasoningLevel"] = params["thoughtLevel"]
        result = snapshot()
    elif method == "session/subscribe":
        result = {"sessionId": session_id, "events": [], "eventSeq": 0}
    elif method == "session/send":
        prompt_input_id = params["inputId"]
        send({"id": request_id, "result": {"sessionId": session_id, "accepted": True, "stateRevision": 1}})
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"sessionId": session_id, "selection": selection}))
        if case == "hang":
            time.sleep(120)
        event("turn.started", {"inputId": "wrong-input" if case == "wrong-input" else params["inputId"]})
        if case in ("inquiry", "inquiry-preempt"):
            # The turn stays live until a guide question arrives (or the fallback
            # timer settles it); the controller must deliver it in-band.
            fallback = threading.Timer(20.0, complete_turn)
            fallback.daemon = True
            fallback.start()
            return
        if case == "attention":
            answers = interactive_attention()
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                (log_dir / "interaction-responses.json").write_text(json.dumps(answers))
            except OSError:
                pass
        complete_turn()
        return
    elif method == "v4/command":
        commands.append({"type": params.get("type"), "payload": params.get("payload"), "commandId": params.get("commandId"),
                         "sessionId": params.get("sessionId"), "clientId": params.get("clientId"), "issuedAt": params.get("issuedAt")})
        record_commands()
        if params.get("type") == "sendText" and case in ("inquiry", "inquiry-preempt"):
            delivery = "startNow" if case == "inquiry-preempt" else "queue"
            send({"id": request_id, "result": {"commandId": params.get("commandId"), "status": "accepted",
                                               "revisionAtDecision": sequence, "result": {"type": "inputAccepted",
                                                                                           "delivery": delivery,
                                                                                           "inputId": params.get("commandId")}}})
            if case == "inquiry":
                inquiry_id = None
                text = params.get("payload", {}).get("text", "")
                if "[buddy inquiry " in text:
                    inquiry_id = text.split("[buddy inquiry ", 1)[1].split("]", 1)[0]
                if inquiry_id and wait_for_delivery(inquiry_id):
                    _content, error = reply(inquiry_id)
                    if error is None:
                        complete_turn()
            return
        if params.get("type") == "stop":
            send({"id": request_id, "result": {"commandId": params.get("commandId"), "status": "accepted", "revisionAtDecision": sequence}})
            if case == "inquiry-preempt":
                complete_turn()
            return
        send({"id": request_id, "error": {"code": -32601, "message": "unknown command"}})
        return
    elif method == "session/close":
        result = {"closed": case != "close-failed"}
    else:
        send({"id": request_id, "error": {"code": -32601, "message": "unknown method"}})
        return
    send({"id": request_id, "result": result})
    if case == "blocked-input" and method == "session/subscribe":
        time.sleep(120)


for line in sys.stdin:
    handle_message(json.loads(line))
