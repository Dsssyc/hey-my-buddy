#!/usr/bin/env python3
"""Offline App Server fixture for Codex's no-tool controller."""
import json
import os
import sys
import time
import tomllib
from pathlib import Path


def send(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def event(method, turn_id, **params):
    send({"method": method, "params": {"threadId": "thread-1", "turnId": turn_id, **params}})


def main():
    if "--version" in sys.argv:
        print("codex-cli 0.157.0")
        return
    case = os.environ.get("BUDDY_CODEX_FIXTURE_CASE", "ok")
    turns = 0
    for raw in sys.stdin:
        request = json.loads(raw)
        if "id" not in request:
            continue
        method, params, ident = request["method"], request.get("params") or {}, request["id"]
        if method == "initialize":
            send({"id": ident, "result": {}})
        elif method == "account/read":
            send({"id": ident, "result": {"account": {"type": "chatgpt"}}})
        elif method == "model/list":
            entry = {"model": "fixture-model", "displayName": "Fixture",
                     "supportedReasoningEfforts": [{"reasoningEffort": "low"}]}
            if case == "unlisted-model":
                entry = {"model": "other-model", "displayName": "Other",
                         "supportedReasoningEfforts": [{"reasoningEffort": "low"}]}
            send({"id": ident, "result": {"data": [entry], "nextCursor": None}})
        elif method == "config/read":
            config = tomllib.loads((Path(os.environ["CODEX_HOME"]) / "config.toml").read_text())
            if case == "config-mismatch":
                config["features"]["sleep_tool"] = True
            send({"id": ident, "result": {"config": {"web_search": "disabled", "approval_policy": "never"},
                 "layers": [{"name": {"type": "user", "file": str(Path(os.environ["CODEX_HOME"]) / "config.toml")},
                             "config": config, "version": "1"}]}})
        elif method == "thread/start":
            trace = Path(os.environ["BUDDY_CODEX_FIXTURE_STATE"])
            trace.write_text(json.dumps({"thread": params, "home": os.environ["CODEX_HOME"]}))
            send({"id": ident, "result": {"thread": {"id": "thread-1", "cwd": params["cwd"], "environments": []},
                "model": "other" if case == "wrong-model" else params["model"],
                "modelProvider": "openai", "approvalPolicy": "never"}})
        elif method == "turn/start":
            turns += 1
            trace = Path(os.environ["BUDDY_CODEX_FIXTURE_STATE"])
            recorded = json.loads(trace.read_text())
            recorded.setdefault("turns", []).append(params)
            trace.write_text(json.dumps(recorded))
            turn_id = f"turn-{turns}"
            if case == "unlisted-model":
                # The native answer decides: the server rejects the turn under
                # the selected model's own name.
                send({"id": ident, "error": {"code": -32000, "message": "model not found"}})
                continue
            if case == 'native-metadata':
                send({'method':'thread/settings/updated','params':{'threadId':'thread-1','threadSettings':{}}})
                send({'method':'thread/status/changed','params':{'threadId':'thread-1','status':{'type':'active'}}})
                send({'method':'account/rateLimits/updated','params':{'rateLimits':{}}})
            send({"id": ident, "result": {"turn": {"id": turn_id}}})
            event("turn/started", turn_id, turn={"id": turn_id})
            if case == "hang":
                time.sleep(60)
                continue
            if case == "eof":
                return
            if case == "truncated":
                sys.stdout.write('{"method":"item/completed","params":')
                sys.stdout.flush()
                return
            if case == "request":
                send({"id": 99, "method": "item/commandExecution/requestApproval",
                      "params": {"threadId": "thread-1", "turnId": turn_id}})
            if case in ("typed", "collab"):
                event("item/started", turn_id, item={"type": "collabAgentToolCall" if case == "collab" else "commandExecution"})
            if case in ("raw", "raw-no-id"):
                item = {"type": "function_call", "name": "exec_command"}
                if case == "raw":
                    item["call_id"] = "call-1"
                event("rawResponseItem/completed", turn_id, item=item)
            if case == "unknown":
                event("item/completed", turn_id, item={"type": "futureThing"})
            answer = "not-json" if case == "format" and turns == 1 else json.dumps({"profileId": "foreign" if case == "outside" else "legal"})
            final = {"type": "agentMessage", "id": f"final-{turns}", "phase": "final_answer", "text": answer}
            event("item/completed", turn_id, item=final)
            items = [final]
            if case == "turn-item":
                items.append({"type": "dynamicToolCall"})
            event("turn/completed", turn_id, turn={"id": turn_id, "status": "completed", "items": items})
            if case == "late":
                event("rawResponseItem/completed", turn_id, item={"type": "custom_tool_call_output"})
        elif method == "turn/interrupt":
            send({"id": ident, "result": {}})


if __name__ == "__main__":
    main()
