#!/usr/bin/env python3
"""Fake ZCode CLI executable for the L6-B read-only controller wiring.

It speaks the same restricted app-server NDJSON protocol as the L6-A fixture,
but as a standalone CLI: ``--version`` answers the controller's metadata probe
and ``app-server --cwd <dir>`` runs one restricted read-only review. The case
is selected with ``BUDDY_ZCODE_TEST_CASE`` (the runner's existing test
passthrough); every session/create is checked against the fixed restricted
parameters and appended verbatim to ``read-only-create-params.jsonl`` inside
``ZCODE_STORAGE_DIR`` so tests can assert the exact request shape the whole
controller stack sent. No model call, credential or user state is touched; the
process exits when its stdin closes.
"""
import json
import os
import sys
import time
from pathlib import Path

if len(sys.argv) > 1 and sys.argv[1] == "--version":
    print("fixture-readonly-0.16.9")
    sys.exit(0)

CASE = os.environ.get("BUDDY_ZCODE_TEST_CASE", "ok")
RECORD = Path(os.environ.get("ZCODE_STORAGE_DIR") or os.getcwd()) / "read-only-create-params.jsonl"

RESTRICTED = {"mode": "plan", "titleGenerationEnabled": False,
              "toolAllowlist": ["Read", "Glob", "Grep"], "mcpServers": [],
              "offPeakToolEnabled": False, "dynamicWorkflowEnabled": False}

session = 0
workspace = {}
subscribed = False
closed = False


def send(data):
    print(json.dumps(data), flush=True)


def event(kind, seq, payload, *, session_id=None, turn_id=None):
    send({"method": "session/event",
          "params": {"sessionId": session_id or f"s-{session}",
                     "turnId": turn_id or f"t-{session}",
                     "seq": seq, "type": kind, "payload": payload}})


def frame(kind, seq, call_id, name, **identity):
    payload = {"kind": kind}
    if call_id is not None:
        payload["toolCallId"] = call_id
    if name is not None:
        payload["toolName"] = name
    event("tool.updated", seq, payload, **identity)


def completed(ident, seq, answer):
    event("turn.completed", seq, {"inputId": ident, "resultType": "success", "response": answer})


def settled():
    send({"method": "state.updated", "params": {"sessionId": f"s-{session}", "reason": "prompt_completed"}})


def tool_round(start_seq, ident, pairs, answer_seq, answer):
    """One root turn: start, paired read/search calls, completion, settlement."""
    event("turn.started", start_seq, {"inputId": ident})
    seq = start_seq + 1
    for call_id, name in pairs:
        frame("scheduled", seq, call_id, name)
        frame("result", seq + 1, call_id, None)
        seq += 2
    completed(ident, answer_seq, answer)
    settled()


for line in sys.stdin:
    call = json.loads(line)
    method = call.get("method")
    if method is None:
        continue  # a client reply to a server request
    p = call.get("params") or {}
    result = {}
    if method == "runtime/capabilities":
        send({"method": "startup/storageState", "params": {"phase": "ready", "elapsedMs": 10}})
        send({"id": "runtime-preferences", "method": "session/requestRuntimePreferences", "params": {}})
        reply = json.loads(sys.stdin.readline())
        assert reply["result"]["nativeSearchEnhancementsEnabled"] is False
    elif method == "session/create":
        try:
            RECORD.parent.mkdir(parents=True, exist_ok=True)
            with open(RECORD, "a") as handle:
                handle.write(json.dumps(p, sort_keys=True) + "\n")
        except OSError:
            pass
        violations = [key for key, value in RESTRICTED.items() if p.get(key) != value]
        if (violations or set(p) != set(RESTRICTED) | {"workspace"}
                or not isinstance(p.get("workspace"), dict)
                or not isinstance(p["workspace"].get("workspacePath"), str)):
            send({"id": call["id"], "error": {"code": -32602, "message":
                  "session/create violated the read-only contract: " + ",".join(violations or ["shape"])}})
            continue
        session += 1
        created = {"sessionId": f"s-{session}", "workspace": p["workspace"]}
        if CASE == "child-root":
            created["parentSessionId"] = "s-parent"
        if CASE == "ws-mismatch":
            created["workspace"] = {**p["workspace"], "workspacePath": "/other-checkout"}
        workspace = created["workspace"]
        subscribed = False
        closed = False
        result = {"session": created,
                  "settings": {"model": {"available": [
                      {"ref": {"providerId": "fixture-api", "modelId": "fixture-model"},
                       "reasoning": {"levels": [{"value": "low"}]}}]},
                      "thoughtLevel": {"current": "low"}}}
    elif method in ("session/setModel", "session/setThoughtLevel"):
        current = {"providerId": "fixture-api", "modelId": "fixture-model",
                   "options": {"reasoningLevel": "low"}}
        level = "low"
        if CASE == "config-mismatch":
            current["modelId"] = "other-model"
        if CASE == "effort-mismatch":
            level = "high"
        result = {"session": {"sessionId": f"s-{session}", "workspace": workspace},
                  "settings": {"model": {"current": current}, "thoughtLevel": {"current": level}}}
    elif method == "session/subscribe":
        assert p["deliveryKind"] == "web-remote-replayable" and p["includeSnapshot"] is False
        subscribed = True
        # The real handshake reports the session's own identity, its nonnegative
        # event sequence and the replay window; a fresh session with
        # includeSnapshot=false and no afterSeq replays nothing. The sub-*
        # cases break exactly one member of that report.
        result = {"sessionId": f"s-{session}", "eventSeq": 0, "events": []}
        if CASE == "sub-wrong-sid":
            result["sessionId"] = "s-other"
        elif CASE == "sub-bad-seq":
            result["eventSeq"] = True
        elif CASE == "sub-replay":
            result = {"sessionId": f"s-{session}", "eventSeq": 3,
                      "events": [{"eventId": "e-1", "type": "message.upserted",
                                  "sessionId": f"s-{session}", "seq": 1}]}
    elif method == "session/send":
        assert subscribed
        result = {"accepted": True, "sessionId": f"s-{session}"}
    elif method == "session/close":
        if closed:
            send({"id": call["id"], "error": {"code": -32004, "message": "Session is not active"}})
            continue
        closed = True
        result = {"closed": CASE != "close-fail"}
    send({"id": call["id"], "result": result})
    if method == "session/close" and CASE == "exit-dirty":
        # The restricted round settled and closed; the server then dies badly so
        # the controller reports an abnormal native exit with its facts kept.
        sys.exit(3)
    if method != "session/send":
        continue
    ident = p["inputId"]
    if CASE in ("ok", "tools3", "exit-dirty"):
        pairs = [("c-1", "Read"), ("c-2", "Glob"), ("c-3", "Grep")] if CASE == "tools3" else []
        tool_round(1, ident, pairs, 8 if pairs else 2, '{"choice":"a"}')
    elif CASE == "bash":
        tool_round(1, ident, [("c-1", "Bash")], 4, '{"choice":"a"}')
    elif CASE == "correct":
        tool_round(1, ident, [], 2, 'bad JSON' if session == 1 else '{"choice":"a"}')
    elif CASE == "correct-tools":
        if session == 1:
            tool_round(1, ident, [("c-1", "Read")], 4, 'bad JSON')
        else:
            tool_round(1, ident, [("c-2", "Glob")], 4, '{"choice":"a"}')
    elif CASE == "enum":
        tool_round(1, ident, [], 2, '{"choice":"b"}')
    elif CASE == "close-fail":
        tool_round(1, ident, [], 2, '{"choice":"a"}')
    elif CASE == "foreign":
        event("turn.started", 1, {"inputId": ident})
        frame("scheduled", 1, "c-f", "Glob", session_id="s-child", turn_id="t-child")
        completed(ident, 2, '{"choice":"a"}')
        settled()
    elif CASE == "late":
        event("turn.started", 1, {"inputId": ident})
        completed(ident, 2, '{"choice":"a"}')
        frame("scheduled", 3, "c-1", "Read")
        settled()
    elif CASE == "end-first":
        event("turn.started", 1, {"inputId": ident})
        frame("result", 2, "c-1", None)
        completed(ident, 3, '{"choice":"a"}')
        settled()
    elif CASE == "mcp":
        tool_round(1, ident, [("c-1", "mcp__fix__tool")], 4, '{"choice":"a"}')
    elif CASE == "missing-id":
        event("turn.started", 1, {"inputId": ident})
        frame("scheduled", 2, None, "Bash")
        completed(ident, 3, '{"choice":"a"}')
        settled()
    elif CASE == "badseq":
        event("turn.started", 1, {"inputId": ident})
        completed(ident, 1, '{"choice":"a"}')
    elif CASE == "wronginput":
        event("turn.started", 1, {"inputId": "other"})
        completed(ident, 2, '{"choice":"a"}')
        settled()
    elif CASE == "unknown":
        event("turn.started", 1, {"inputId": ident})
        event("future.unknown", 2, {})
    elif CASE == "rpc":
        # A native reverse request inside the restricted round: the controller
        # auto-refuses it (no interactive host) and the call fails with the
        # refused-interaction code while every already-projected fact is kept.
        event("turn.started", 1, {"inputId": ident})
        send({"id": "perm-1", "method": "interaction/requestPermission",
              "params": {"tool": "Bash", "input": "rm -rf /"}})
        for reply in sys.stdin:
            pass
        sys.exit(0)
    elif CASE == "truncated":
        event("turn.started", 1, {"inputId": ident})
        sys.exit(0)
    elif CASE == "sleep":
        # For the outer-stop-unknown case: a short turn-shaped stall so a killed
        # controller leaves this server briefly alive, then a plain exit.
        event("turn.started", 1, {"inputId": ident})
        time.sleep(2.5)
        sys.exit(0)
    elif CASE == "timeout":
        event("turn.started", 1, {"inputId": ident})
        time.sleep(30)
