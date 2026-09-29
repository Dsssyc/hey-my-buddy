#!/usr/bin/env python3
"""Deterministic ZCode app-server fixture for the ADR-018 native usage records.

It speaks the documented app-server methods the controller uses for one governed
turn and serves the desensitized records of ``native-usage-zcode.json``: bounded
``session/messages`` pages (with the native ``afterMessageId`` cursor),
``v4/telemetry/event`` ``usage.delta`` notifications including a replay and
foreign sessions, and the exported ``turn.failed`` shape for a quota failure.
The finish receipt is minted by the real private MCP bridge, exactly like
``mock_zcode.py``; no model and no network are used.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if "--version" in sys.argv:
    print("fixture-0.16.9-usage")
    raise SystemExit(0)

FIXTURE = json.loads(Path(__file__).with_name("native-usage-zcode.json").read_text())
case = os.environ.get("BUDDY_ZCODE_TEST_CASE", "usage-ok")
log_dir = Path(os.environ["ZCODE_LOG_DIR"])
instance = hashlib.sha256(os.environ["ZCODE_SESSION_DB_PATH"].encode()).hexdigest()[:16]
session_id = "sess-fixture-" + instance
turn_id = "turn-fixture-" + instance + ("-b" if case == "resume-ok" else "")
selection = {"providerId": "fixture-api", "modelId": "fixture-model", "options": {"reasoningLevel": "low"}}
workspace = {}
mcp = []
sequence = 0
prompt_input_id = None
baseline_served = False
RESUME_CASES = ("resume-ok", "cursor-lost", "delta-only")


def send(value):
    print(json.dumps(value), flush=True)


def rewrite(value):
    if isinstance(value, str):
        if value == FIXTURE["sessionId"]:
            return session_id
        return turn_id if value == FIXTURE["turnId"] else value
    if isinstance(value, dict):
        return {key: rewrite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite(item) for item in value]
    return value


def snapshot():
    available = [{
        "ref": {"providerId": "fixture-api", "modelId": "fixture-model"}, "label": "Fixture model",
        "providerLabel": "Fixture API", "contextWindow": 200000,
        "reasoning": {"levels": [{"value": "low"}, {"value": "high"}], "defaultLevel": "low"},
        "properties": {"inputFormat": {"supportsText": True}}}]
    return {"session": {"sessionId": session_id, "sessionKind": "interactive", "workspace": workspace},
            "settings": {"model": {"current": selection, "available": available},
                         "thoughtLevel": {"current": selection["options"]["reasoningLevel"]}}}


def event(kind, payload):
    global sequence
    sequence += 1
    send({"method": "session/event", "params": {"type": kind, "sessionId": session_id, "turnId": turn_id,
          "seq": sequence, "eventId": "event-" + str(sequence), "payload": payload}})


def mcp_call(tool, arguments):
    config = mcp[0]
    env = {**os.environ, **{x["name"]: x["value"] for x in config.get("env", [])}}
    child = subprocess.Popen([config["command"], *config["args"]], env=env, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
    child.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": tool, "arguments": arguments}}) + "\n")
    child.stdin.flush()
    response = json.loads(child.stdout.readline())["result"]
    child.stdin.close()
    child.wait(timeout=3)
    if response.get("isError"):
        return None, response["content"][0]["text"]
    return response["content"][0]["text"], None


def finish_turn():
    tool = "mcp__" + mcp[0]["name"] + "__buddy_finish_turn"
    outcome = {"disposition": "completed", "summary": "fixture work completed", "remaining": [],
               "decisions": [], "artifacts": [], "request": None}
    content, error = mcp_call("buddy_finish_turn", outcome)
    event("tool.updated", {"kind": "scheduled", "toolName": tool, "toolCallId": "root-call"})
    event("tool.updated", {"kind": "result", "toolCallId": "root-call",
                           "result": {"success": content is not None, "truncated": False,
                                      "content": content if content is not None else error}})
    return content


def emit_deltas():
    for delta in FIXTURE["usageDeltas"]:
        send({"method": "v4/telemetry/event", "params": rewrite(dict(delta))})


def run_turn(params):
    global prompt_input_id
    prompt_input_id = params["inputId"]
    event("turn.started", {"inputId": prompt_input_id, "resultType": None})
    if case == "usage-quota":
        emit_deltas()
        failure = FIXTURE["quotaFailureTurn"]
        event("turn.failed", {"error": failure["error"], "turnPhase": failure["turnPhase"],
                              "inputId": prompt_input_id})
        return
    if case != "usage-empty":
        emit_deltas()
    finish_turn()
    event("turn.completed", {"inputId": prompt_input_id, "resultType": "success",
                            "response": "fixture final text is not the outcome"})
    send({"method": "state.updated", "params": {"sessionId": session_id, "reason": "prompt_completed"}})


def message_page(request_id, params):
    """Serve one bounded messages page, honouring the native afterMessageId cursor."""
    global baseline_served
    if case == "delta-only":
        send({"id": request_id, "error": {"code": -32601, "message": "unknown method"}})
        return
    if not baseline_served:
        baseline_served = True
        if case == "metadata-timeout":
            # A stalled optional read: the controller must bound it itself and
            # restore its main deadline.
            time.sleep(3.0)
        page = FIXTURE["baselineMessages"] if case in RESUME_CASES else {"messages": []}
    elif case == "cursor-lost":
        # The native server could not resolve the cursor and returned the whole
        # conversation, so nothing in this page is provably new.
        page = {"messages": FIXTURE["baselineMessages"]["messages"] + FIXTURE["finalMessages"]["messages"]}
    elif "afterMessageId" in params and params["afterMessageId"] != baseline_cursor():
        send({"id": request_id, "error": {"code": -32602, "message": "unknown cursor"}})
        return
    else:
        page = FIXTURE["finalMessages"] if case != "usage-empty" else {"messages": []}
    # The controller's real session id replaces the fixture's placeholder in every
    # info and part entry, exactly like the native server would report it.
    send({"id": request_id, "result": rewrite(page)})


def baseline_cursor():
    messages = FIXTURE["baselineMessages"]["messages"] if case in RESUME_CASES else []
    return messages[-1]["info"]["messageId"] if messages else None


for line in sys.stdin:
    try:
        request = json.loads(line)
    except ValueError:
        continue
    method = request.get("method")
    params = request.get("params") or {}
    request_id = request.get("id")
    if method == "runtime/capabilities":
        send({"id": request_id, "result": {}})
    elif method == "session/create":
        workspace = params.get("workspace") or {}
        mcp = params.get("mcpServers") or []
        send({"id": request_id, "result": snapshot()})
    elif method == "session/resume":
        workspace = params.get("workspace") or {}
        mcp = params.get("mcpServers") or []
        session_id = params["sessionId"]
        send({"id": request_id, "result": snapshot()})
    elif method == "session/setModel":
        selection = params["model"]
        send({"id": request_id, "result": snapshot()})
    elif method == "session/setThoughtLevel":
        selection.setdefault("options", {})["reasoningLevel"] = params["thoughtLevel"]
        send({"id": request_id, "result": snapshot()})
    elif method == "session/subscribe":
        send({"id": request_id, "result": {"sessionId": session_id, "events": [], "eventSeq": 0}})
    elif method == "session/messages":
        message_page(request_id, params)
    elif method == "session/send":
        send({"id": request_id, "result": {"sessionId": session_id, "accepted": True, "stateRevision": 1}})
        run_turn(params)
    elif method == "session/close":
        send({"id": request_id, "result": {"closed": True}})
    else:
        send({"id": request_id, "error": {"code": -32601, "message": "unknown method"}})
