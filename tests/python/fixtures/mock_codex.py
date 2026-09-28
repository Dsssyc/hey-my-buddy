#!/usr/bin/env python3
"""Private Codex App Server fixture. No model or shared Codex state is touched."""
import json
import os
import sys
import time
from pathlib import Path


def send(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def state_path():
    return Path(os.environ["BUDDY_CODEX_FIXTURE_STATE"])


def read_state():
    try:
        return json.loads(state_path().read_text())
    except FileNotFoundError:
        return {"next": 1, "threads": {}}


def write_state(value):
    state_path().write_text(json.dumps(value))


def outcome(case):
    request = None
    disposition = "completed"
    if case == "assistance":
        disposition = "assistance"
        request = {"summary": "Need helper", "attempted": "Inspected source", "neededWork": "Check dependency",
                   "expectedArtifacts": [], "acceptance": "Dependency verified"}
    if case == "completed-request":
        request = {"summary": "Contradictory request", "attempted": "Finished", "neededWork": "None",
                   "expectedArtifacts": [], "acceptance": "Already complete"}
    return {"disposition": disposition, "summary": "fixture work completed", "remaining": [],
            "decisions": [], "artifacts": [], "request": request}


def main():
    if "--version" in sys.argv:
        print("codex-cli fixture")
        return
    case = os.environ.get("BUDDY_CODEX_FIXTURE_CASE", "ok")
    for raw in sys.stdin:
        req = json.loads(raw)
        if "id" not in req:
            continue
        method, params, ident = req["method"], req.get("params") or {}, req["id"]
        if method == "initialize":
            send({"id": ident, "result": {"userAgent": "fixture"}})
        elif method == "account/read":
            send({"id": ident, "result": {"account": {"type": "apiKey" if case == "api-key" or os.environ.get("OPENAI_API_KEY") or os.environ.get("CODEX_API_KEY") else "chatgpt",
                                                            "email": None, "planType": "plus"}, "requiresOpenaiAuth": True}})
        elif method == "model/list":
            send({"id": ident, "result": {"data": [] if case == "empty-catalog" else [{"id": "fixture-model", "model": "fixture-model",
                "displayName": "Fixture", "description": "fixture", "hidden": False, "isDefault": True,
                "defaultReasoningEffort": "low", "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}]}],
                "nextCursor": None}})
        elif method == "thread/start":
            state = read_state()
            thread_id = f"thread-{state['next']}"
            state["next"] += 1
            state["threads"][thread_id] = {"cwd": params["cwd"], "turns": []}
            write_state(state)
            policy = ({'activePermissionProfile': {'id': params['permissions']},
                       'sandbox': {'type': 'readOnly', 'networkAccess': False}, 'approvalPolicy': 'never',
                       'model': params['model'], 'modelProvider': 'openai', 'cwd': params['cwd']}
                      if params.get('permissions') else {})
            if case == 'readonly-policy-mismatch':
                policy['sandbox']['networkAccess'] = True
            send({"id": ident, "result": {**policy, "thread": {"id": thread_id, "cwd": params["cwd"],
                                                       "modelProvider": "openai", "turns": []}}})
        elif method == "thread/read":
            state = read_state()["threads"].get(params["threadId"])
            if not state:
                send({"id": ident, "error": {"code": -1, "message": "missing"}})
            else:
                send({"id": ident, "result": {"thread": {"id": params["threadId"], **state}}})
        elif method == "thread/resume":
            state = read_state()["threads"].get(params["threadId"])
            if not state:
                send({"id": ident, "error": {"code": -1, "message": "missing"}})
            else:
                send({"id": ident, "result": {"thread": {"id": params["threadId"], "cwd": state["cwd"],
                                                           "modelProvider": "openai", "turns": state["turns"]}}})
        elif method == "turn/start":
            thread_id = params["threadId"]
            state = read_state()
            turn_id = f"native-turn-{len(state['threads'][thread_id]['turns']) + 1}"
            send({"id": ident, "result": {"turn": {"id": turn_id, "status": "inProgress", "items": []}}})
            send({"method": "turn/started", "params": {"threadId": thread_id, "turn": {"id": turn_id, "status": "inProgress", "items": []}}})
            if case == "hang":
                time.sleep(60)
                continue
            if case == "readonly-budget":
                send({"method": "item/started", "params": {"threadId": thread_id, "turnId": turn_id,
                      "item": {"type": "commandExecution", "id": "read-1"}}})
            if case in ("approval", "approval-failed"):
                send({"id": 99, "method": "item/commandExecution/requestApproval",
                      "params": {"threadId": thread_id, "turnId": turn_id, "itemId": "tool-1", "startedAtMs": 1}})
                response = json.loads(sys.stdin.readline())
                if response.get("id") != 99 or "error" not in response:
                    raise RuntimeError("the controller unexpectedly approved the native request")
            item = {"type": "agentMessage", "id": "final-1", "phase": "final_answer",
                    "text": json.dumps({"outcome": outcome(case)}) if case != "invalid-json" else "not json"}
            if "profileId" in params.get("outputSchema", {}).get("properties", {}):
                item["text"] = json.dumps({"profileId": "foreign" if case == "readonly-outside" else "legal", "reason": "Read-only fixture", "evidence": []})
                if case == "readonly-repair" and not state['threads'][thread_id]['turns']:
                    item["text"] = "not-json"
            if case != "no-final":
                send({"method": "item/completed", "params": {"threadId": thread_id, "turnId": turn_id,
                                                            "item": item, "completedAtMs": 1}})
            status = "failed" if case in ("failed", "approval-failed") else "completed"
            turn = {"id": turn_id, "status": status, "items": [] if case == "no-final" else [item]}
            send({"method": "turn/completed", "params": {"threadId": thread_id, "turn": turn}})
            state["threads"][thread_id]["turns"].append(turn)
            write_state(state)
        elif method == "turn/interrupt":
            send({"id": ident, "result": {}})
        else:
            send({"id": ident, "error": {"code": -32601, "message": "unknown"}})


if __name__ == "__main__":
    main()
