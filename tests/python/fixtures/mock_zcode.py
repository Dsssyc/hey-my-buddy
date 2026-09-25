#!/usr/bin/env python3
"""Deterministic native-protocol fixture; calls the real private MCP finish bridge.

It also implements the documented reverse-RPC interactive requests and the v4
command surface, so the controller's attention handling and its deliberate refusal
to inject an inquiry are exercised with no model and no network.
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
    if os.environ.get("BUDDY_ZCODE_TEST_CASE") == "slow-version":
        time.sleep(6)
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
    available = [] if case == "catalog-empty" else [{
        "ref": {"providerId": "fixture-api", "modelId": "fixture-model"}, "label": "Fixture model",
        "providerLabel": "Fixture API", "contextWindow": 200000,
        "reasoning": {"levels": [] if case == "no-effort" else [{"value": "low"}, {"value": "high"}], "defaultLevel": "low"},
        "properties": {"inputFormat": {"supportsText": True}}}]
    return {"session": {"sessionId": session_id, "sessionKind": "interactive", "workspace": workspace},
            "settings": {"model": {"current": selection, "available": available},
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


def finish(disposition="completed"):
    """Call the real session-private finish tool and return its signed receipt."""
    outcome = {"disposition": disposition, "summary": "fixture work completed", "remaining": [], "decisions": [], "artifacts": [], "request": None}
    if disposition == "assistance":
        outcome.update(summary="fixture asks for help", request={"summary": "help", "attempted": "examined fixture", "neededWork": "review decision", "expectedArtifacts": [], "acceptance": "decision reviewed"})
    if disposition == "attention":
        outcome.update(summary="native approval needed", request={"summary": "native approval needed", "attempted": "requested fixture permission", "neededWork": "Host decision", "expectedArtifacts": [], "acceptance": "decision recorded"})
    return mcp_call("buddy_finish_turn", outcome)


def complete_turn():
    """Emit the single terminal root finish exactly once."""
    global completed
    with completion_lock:
        if completed:
            return
        completed = True
    content, _error = finish("assistance" if case == "assistance" else "completed")
    if case == "attention" and content is None:
        # The finish tool refuses completed while a native interactive request is
        # refused; a compliant root retries with the attention outcome.
        content, _error = finish("attention")
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
        if case in ("live", "live-activity"):
            # Stay live until the test releases the turn, bounded so a broken test
            # can never hang the suite. No question may end or restart this turn.
            release = log_dir / "release-turn"
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline and not release.exists():
                if case == "live-activity":
                    send({"method": "v4/telemetry/event", "params": {
                        "kind": "model-delta", "text": "fixture-private-progress",
                    }})
                time.sleep(0.05)
            complete_turn()
            return
        if case in ("attention", "attention-after-finish"):
            accepted = finish("completed")[0] if case == "attention-after-finish" else None
            answers = interactive_attention()
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                (log_dir / "interaction-responses.json").write_text(json.dumps(answers))
            except OSError:
                pass
            if case == "attention-after-finish":
                # A receipt accepted before the refusal is still refused by the
                # adapter because a completed outcome cannot stand in for the
                # Host attention the refused request requires.
                tool = "mcp__" + mcp[0]["name"] + "__buddy_finish_turn"
                event("tool.updated", {"kind": "scheduled", "toolName": tool, "toolCallId": "root-call"}, root=session_id, turn=turn_id)
                event("tool.updated", {"kind": "result", "toolCallId": "root-call",
                                       "result": {"success": True, "truncated": False, "content": accepted}}, root=session_id, turn=turn_id)
                event("turn.completed", {"inputId": prompt_input_id, "resultType": "success", "response": "completed after a refused native request"})
                send({"method": "state.updated", "params": {"sessionId": session_id, "reason": "prompt_completed"}})
                return
        complete_turn()
        return
    elif method == "v4/command":
        # The controller must never send one: the installed protocol has no
        # turn-bound in-turn input, so an inquiry is refused locally instead.
        commands.append({"type": params.get("type"), "payload": params.get("payload"), "commandId": params.get("commandId"),
                         "sessionId": params.get("sessionId"), "clientId": params.get("clientId"), "issuedAt": params.get("issuedAt")})
        record_commands()
        send({"id": request_id, "error": {"code": -32601, "message": "no client command is used by this adapter"}})
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
