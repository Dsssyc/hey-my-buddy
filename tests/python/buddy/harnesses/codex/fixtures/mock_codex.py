#!/usr/bin/env python3
"""Private Codex App Server fixture. No model or shared Codex state is touched."""
import json
import os
import sys
import time
import tomllib
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


#: Two native requests inside one turn. The second one repeats the exact same
#: thread totals, so a replayed notification must not be counted twice.
USAGE_REQUESTS = (
    {"inputTokens": 44150, "cachedInputTokens": 43776, "cacheWriteInputTokens": 0,
     "outputTokens": 119, "reasoningOutputTokens": 32, "totalTokens": 44269},
    {"inputTokens": 45210, "cachedInputTokens": 44736, "cacheWriteInputTokens": 0,
     "outputTokens": 96, "reasoningOutputTokens": 6, "totalTokens": 45306},
)
#: A resumed thread already carries its earlier turns in the native total.
PRIOR_THREAD_TOKENS = 250000

RATE_LIMITS_READ = {
    "ordinaryUsageAllowed": True,
    "rateLimits": {
        "limitId": "codex", "limitName": None, "normalModelSlug": None,
        "primary": {"usedPercent": 42.5, "windowDurationMins": 300, "resetsAt": 1791050000},
        "secondary": {"usedPercent": 77, "windowDurationMins": 10080, "resetsAt": 1791046876},
        "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
        "individualLimit": None, "spendControlReached": False, "planType": "pro",
        "rateLimitReachedType": None,
    },
    "accountId": "account-fixture-0001",
    "rateLimitUpsell": None,
}
RATE_LIMITS_NOTIFICATION = {
    "limitId": "codex", "limitName": None, "normalModelSlug": None,
    "primary": {"usedPercent": 77, "windowDurationMins": 10080, "resetsAt": 1791046876},
    "secondary": None,
    "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
    "individualLimit": None, "spendControlReached": False, "planType": "pro",
    "rateLimitReachedType": None,
}


def cumulative(turn_index: int, requests: list) -> dict:
    """One native ``total`` breakdown: prior thread usage plus these requests.

    A resumed thread's totals already contain its earlier turns; only the delta
    from this turn's baseline is this attempt's usage.
    """
    prior = {
        "inputTokens": PRIOR_THREAD_TOKENS * turn_index,
        "cachedInputTokens": (PRIOR_THREAD_TOKENS - 1000) * turn_index,
        "cacheWriteInputTokens": 0,
        "outputTokens": 1000 * turn_index,
        "reasoningOutputTokens": 0,
        "totalTokens": PRIOR_THREAD_TOKENS * turn_index,
    }
    return {key: prior[key] + sum(request[key] for request in requests) for key in prior}


def send_usage(case: str, thread_id: str, turn_id: str, turn_index: int) -> None:
    """Emit this fixture's native usage and quota notifications, in native order."""
    if case not in ("usage", "usage-read", "quota-failure"):
        return
    observed = []
    for request in USAGE_REQUESTS:
        observed.append(request)
        send({"method": "thread/tokenUsage/updated", "params": {
            "threadId": thread_id, "turnId": turn_id,
            "tokenUsage": {"last": request, "total": cumulative(turn_index, observed),
                           "modelContextWindow": 828400}}})
    send({"method": "account/rateLimits/updated", "params": {"rateLimits": RATE_LIMITS_NOTIFICATION}})
    # The native stream may repeat the final snapshot; a replay is not new usage.
    send({"method": "thread/tokenUsage/updated", "params": {
        "threadId": thread_id, "turnId": turn_id,
        "tokenUsage": {"last": USAGE_REQUESTS[-1], "total": cumulative(turn_index, observed),
                       "modelContextWindow": 828400}}})


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
    summary = "fixture work completed"
    if case == "long-summary-citation":
        summary = "调研结论。" * 600 + "\n<oai-mem-citation>reference</oai-mem-citation>"
    return {"disposition": disposition, "summary": summary, "remaining": [],
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
        elif method == 'config/read':
            configured = tomllib.loads((Path(os.environ['CODEX_HOME']) / 'config.toml').read_text())
            if case == 'readonly-config-mismatch':
                configured['features']['apps'] = True
            send({'id': ident, 'result': {'config': configured}})
        elif method == "account/read":
            send({"id": ident, "result": {"account": {"type": "apiKey" if case == "api-key" or os.environ.get("OPENAI_API_KEY") or os.environ.get("CODEX_API_KEY") else "chatgpt",
                                                            "email": None, "planType": "plus"}, "requiresOpenaiAuth": True}})
        elif method == "account/rateLimits/read":
            if case in ("usage", "quota-failure"):
                # The rolling notifications already carried the quota; a failed
                # read must not fail the turn and must not erase what was seen.
                send({"id": ident, "error": {"code": -32603, "message": "usage read unavailable"}})
            else:
                send({"id": ident, "result": RATE_LIMITS_READ})
        elif method == "model/list":
            send({"id": ident, "result": {"data": [] if case == "empty-catalog" else [{"id": "fixture-model", "model": "fixture-model",
                "displayName": "Fixture", "description": "fixture", "hidden": False, "isDefault": True,
                "defaultReasoningEffort": "low", "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}]}],
                "nextCursor": None}})
        elif method == "thread/start":
            state = read_state()
            thread_id = f"thread-{state['next']}"
            state["next"] += 1
            state["threads"][thread_id] = {"cwd": params["cwd"], "turns": [],
                                         'codexHome': os.environ.get('CODEX_HOME'),
                                         'sqliteHome': os.environ.get('CODEX_SQLITE_HOME')}
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
            if not state or state.get('codexHome') != os.environ.get('CODEX_HOME'):
                send({"id": ident, "error": {"code": -1, "message": "missing"}})
            else:
                send({"id": ident, "result": {"thread": {"id": params["threadId"], **state}}})
        elif method == "thread/resume":
            state = read_state()["threads"].get(params["threadId"])
            if not state or state.get('codexHome') != os.environ.get('CODEX_HOME'):
                send({"id": ident, "error": {"code": -1, "message": "missing"}})
            else:
                send({"id": ident, "result": {"thread": {"id": params["threadId"], "cwd": state["cwd"],
                                                           "modelProvider": "openai", "turns": state["turns"]}}})
        elif method == "turn/start":
            thread_id = params["threadId"]
            state = read_state()
            turn_index = len(state['threads'][thread_id]['turns'])
            turn_id = f"native-turn-{turn_index + 1}"
            send({"id": ident, "result": {"turn": {"id": turn_id, "status": "inProgress", "items": []}}})
            send({"method": "turn/started", "params": {"threadId": thread_id, "turn": {"id": turn_id, "status": "inProgress", "items": []}}})
            send_usage(case, thread_id, turn_id, turn_index)
            if case == "hang":
                time.sleep(60)
                continue
            if case == 'worker-tool':
                send({"method": "item/started", "params": {"threadId": thread_id, "turnId": turn_id,
                      "item": {"type": "commandExecution", "id": "tool-1"}}})
                send({"method": "item/completed", "params": {"threadId": thread_id, "turnId": turn_id,
                      "item": {"type": "commandExecution", "id": "tool-1", "exitCode": 0}}})
            if case == 'worker-unknown':
                send({"method": "thread/hologram/updated", "params": {"threadId": thread_id, "turnId": turn_id}})
            if case == 'worker-unknown-flood':
                # More distinct unclassified types in one turn than the public
                # result carries: the run must still return a bounded result.
                for number in range(65):
                    send({"method": f"native/future/{number}",
                          "params": {"threadId": thread_id, "turnId": turn_id}})
            if case == "readonly-budget":
                send({"method": "item/started", "params": {"threadId": thread_id, "turnId": turn_id,
                      "item": {"type": "commandExecution", "id": "read-1"}}})
            if case == 'readonly-denied-budget':
                send({'method': 'rawResponseItem/completed', 'params': {'threadId': thread_id, 'turnId': turn_id,
                      'item': {'type': 'function_call', 'call_id': 'denied-1', 'name': 'exec_command', 'arguments': '{"cmd":"denied"}'}}})
            if case == 'readonly-evidence':
                send({"method": "item/started", "params": {"threadId": thread_id, "turnId": turn_id,
                      "item": {"type": "commandExecution", "id": "item-1"}}})
                send({"method": "item/completed", "params": {"threadId": thread_id, "turnId": turn_id,
                      "item": {"type": "commandExecution", "id": "item-1", "exitCode": 0}}})
                raw_call = {"method": "rawResponseItem/completed", "params": {"threadId": thread_id, "turnId": turn_id,
                            "item": {"type": "function_call", "call_id": "call-1", "name": "shell", "arguments": '{"cmd":"ls"}'}}}
                send(raw_call)
                # The native stream may repeat a raw item; a replay is not a second call.
                send(raw_call)
                send({"method": "rawResponseItem/completed", "params": {"threadId": thread_id, "turnId": turn_id,
                      "item": {"type": "function_call_output", "call_id": "call-1", "output": "listing"}}})
            if case == 'readonly-conflict':
                # The same call id carries two contradicting native projections.
                send({"method": "item/started", "params": {"threadId": thread_id, "turnId": turn_id,
                      "item": {"type": "commandExecution", "id": "call-1"}}})
                send({"method": "item/completed", "params": {"threadId": thread_id, "turnId": turn_id,
                      "item": {"type": "commandExecution", "id": "call-1"}}})
                send({"method": "rawResponseItem/completed", "params": {"threadId": thread_id, "turnId": turn_id,
                      "item": {"type": "function_call", "call_id": "call-1", "name": "shell", "arguments": "{}"}}})
                send({"method": "rawResponseItem/completed", "params": {"threadId": thread_id, "turnId": turn_id,
                      "item": {"type": "function_call_output", "call_id": "call-1", "output": "ok"}}})
            if case == 'readonly-foreign':
                send({"method": "item/started", "params": {"threadId": "thread-sub", "turnId": "sub-turn-1",
                      "item": {"type": "commandExecution", "id": "sub-1"}}})
                send({"method": "rawResponseItem/completed", "params": {"threadId": thread_id, "turnId": "native-turn-99",
                      "item": {"type": "web_search_call", "call_id": "web-1"}}})
            if case == 'readonly-truncated':
                for number in range(130):
                    send({"method": "item/started", "params": {"threadId": thread_id, "turnId": turn_id,
                          "item": {"type": "commandExecution", "id": f"item-{number}"}}})
                    send({"method": "item/completed", "params": {"threadId": thread_id, "turnId": turn_id,
                          "item": {"type": "commandExecution", "id": f"item-{number}"}}})
            if case == 'readonly-repair-evidence':
                if turn_index == 0:
                    send({"method": "item/started", "params": {"threadId": thread_id, "turnId": turn_id,
                          "item": {"type": "commandExecution", "id": "item-1"}}})
                    send({"method": "item/completed", "params": {"threadId": thread_id, "turnId": turn_id,
                          "item": {"type": "commandExecution", "id": "item-1"}}})
                else:
                    send({"method": "rawResponseItem/completed", "params": {"threadId": thread_id, "turnId": turn_id,
                          "item": {"type": "function_call", "call_id": "call-1", "name": "shell", "arguments": "{}"}}})
                    send({"method": "rawResponseItem/completed", "params": {"threadId": thread_id, "turnId": turn_id,
                          "item": {"type": "function_call_output", "call_id": "call-1", "output": "ok"}}})
                    # A late duplicate of the previous root turn's item stays an old-turn fact.
                    send({"method": "item/started", "params": {"threadId": thread_id, "turnId": "native-turn-1",
                          "item": {"type": "commandExecution", "id": "item-stale"}}})
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
                if case in ("readonly-repair", "readonly-repair-evidence") and not state['threads'][thread_id]['turns']:
                    item["text"] = "not-json"
            if case != "no-final":
                send({"method": "item/completed", "params": {"threadId": thread_id, "turnId": turn_id,
                                                            "item": item, "completedAtMs": 1}})
            if case == "disconnect-after-message":
                return
            status = "failed" if case in ("failed", "approval-failed", "quota-failure") else "completed"
            turn = {"id": turn_id, "status": status, "items": [] if case == "no-final" else [item]}
            if case == "quota-failure":
                turn["error"] = {"message": "provider wording that must never be retained",
                                 "codexErrorInfo": "usageLimitExceeded"}
            send({"method": "turn/completed", "params": {"threadId": thread_id, "turn": turn}})
            state["threads"][thread_id]["turns"].append(turn)
            write_state(state)
        elif method == "turn/interrupt":
            send({"id": ident, "result": {}})
        else:
            send({"id": ident, "error": {"code": -32601, "message": "unknown"}})


if __name__ == "__main__":
    main()
