#!/usr/bin/env python3
"""Deterministic native-protocol fixture; calls the real private MCP finish bridge."""
import json
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

if "--version" in sys.argv:
    print("fixture-0.16.9")
    raise SystemExit(0)

case = os.environ.get("BUDDY_ZCODE_TEST_CASE", "ok")
state = Path(os.environ["ZCODE_SESSION_DB_PATH"]).with_suffix(".fixture.json")
instance = hashlib.sha256(os.environ["ZCODE_LOG_DIR"].encode()).hexdigest()[:16]
session_id = "sess-fixture-" + instance
turn_id = "turn-fixture-" + instance
selection = {"providerId": "fixture-api", "modelId": "fixture-model", "options": {"reasoningLevel": "low"}}
workspace = {}
mcp = []
pending_create = None
sequence = 0
child = None


def send(value):
    print(json.dumps(value), flush=True)


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


def finish():
    global child
    config = mcp[0]
    env = {**os.environ, **{x["name"]: x["value"] for x in config.get("env", [])}}
    child = subprocess.Popen([config["command"], *config["args"]], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    outcome = {"disposition": "completed", "summary": "fixture work completed", "remaining": [], "decisions": [], "artifacts": [], "request": None}
    if case == "assistance":
        outcome.update(disposition="assistance", request={"summary": "help", "attempted": "examined fixture", "neededWork": "review decision", "expectedArtifacts": [], "acceptance": "decision reviewed"})
    child.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "buddy_finish_turn", "arguments": outcome}}) + "\n")
    child.stdin.flush()
    response = json.loads(child.stdout.readline())["result"]
    child.stdin.close()
    child.wait(timeout=3)
    return response["content"][0]["text"]


for line in sys.stdin:
    message = json.loads(line)
    if "method" not in message:
        if pending_create:
            send({"id": pending_create, "result": snapshot()})
            pending_create = None
        continue
    method, params, request_id = message["method"], message.get("params", {}), message["id"]
    if method == "runtime/capabilities":
        if case == "invalid-json":
            print('{"id":1,"result":{},"result":{}}', flush=True)
            continue
        result = {"independentPlanState": True}
    elif method in ("session/create", "session/resume"):
        workspace, mcp = params["workspace"], params.get("mcpServers", [])
        if method == "session/resume":
            if not state.exists() or case == "resume-missing":
                send({"id": request_id, "error": {"code": -32004, "message": "Session not found"}})
                continue
            stored = json.loads(state.read_text())
            if stored["sessionId"] != params["sessionId"] or not mcp:
                raise RuntimeError("native resume lost its exact identity or MCP reinjection")
            session_id, selection = stored["sessionId"], stored["selection"]
            if case == "resume-wrong-root":
                session_id = "sess-different-root"
        pending_create = request_id
        send({"id": "server-preferences", "method": "session/requestRuntimePreferences", "params": {"sessionId": session_id, "scope": "runtime-materialization"}})
        continue
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
        send({"id": request_id, "result": {"sessionId": session_id, "accepted": True, "stateRevision": 1}})
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"sessionId": session_id, "selection": selection}))
        if case == "hang":
            time.sleep(120)
        event("turn.started", {"inputId": "wrong-input" if case == "wrong-input" else params["inputId"]})
        content = finish()
        if case == "forged-receipt":
            receipt = json.loads(content)
            receipt["outcome"]["summary"] = "changed after the bridge signed it"
            content = json.dumps(receipt)
        tool = "mcp__" + mcp[0]["name"] + "__buddy_finish_turn"
        if case == "child-first":
            event("tool.updated", {"kind": "scheduled", "toolName": tool, "toolCallId": "child-call"}, root="sess-child", turn="turn-child")
            event("tool.updated", {"kind": "result", "toolCallId": "child-call", "result": {"success": True, "truncated": False, "content": content}}, root="sess-child", turn="turn-child")
        event_root = "sess-wrong" if case == "wrong-root" else session_id
        event_turn = "turn-wrong" if case == "wrong-turn" else turn_id
        event("tool.updated", {"kind": "scheduled", "toolName": tool, "toolCallId": "root-call", "inputOmitted": True, "inputRef": "model_stream"}, root=event_root, turn=event_turn)
        event("tool.updated", {"kind": "result", "toolCallId": "root-call", "result": {"success": case != "tool-error", "truncated": case == "truncated", "content": content}}, root=event_root, turn=event_turn)
        if case == "duplicate":
            event("tool.updated", {"kind": "scheduled", "toolName": tool, "toolCallId": "second-call"})
        event("turn.completed", {"inputId": params["inputId"], "resultType": "error_during_execution" if case == "turn-failure" else "success", "response": "fixture final text is not the outcome"})
        send({"method": "state.updated", "params": {"sessionId": session_id, "reason": "prompt_failed" if case == "prompt-failure" else "prompt_completed"}})
        continue
    elif method == "session/close":
        result = {"closed": case != "close-failed"}
    else:
        send({"id": request_id, "error": {"code": -32601, "message": "unknown method"}})
        continue
    send({"id": request_id, "result": result})
    if case == "blocked-input" and method == "session/subscribe":
        time.sleep(120)
