#!/usr/bin/env python3
"""Private Codex App Server fixture. No model or shared Codex state is touched."""
import json
import hashlib
import os
import sys
import time
import tomllib
from pathlib import Path

sys.stdin.reconfigure(encoding="utf-8")

from protocol_items import item_frames, session_frames


def send(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def state_path():
    return Path(os.environ["HMB_CODEX_FIXTURE_STATE"])


def read_state():
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"next": 1, "threads": {}}


def write_state(value):
    state_path().write_text(json.dumps(value), encoding="utf-8", newline="\n")


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
    """Emit this fixture's native token usage notifications, in native order."""
    if case not in ("usage", "quota-failure"):
        return
    observed = []
    for request in USAGE_REQUESTS:
        observed.append(request)
        send({"method": "thread/tokenUsage/updated", "params": {
            "threadId": thread_id, "turnId": turn_id,
            "tokenUsage": {"last": request, "total": cumulative(turn_index, observed),
                           "modelContextWindow": 828400}}})
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
                   "expectedArtifacts": [], "acceptance": "Dependency verified", "suggestedBuddyId": None}
    if case == "completed-request":
        request = {"summary": "Contradictory request", "attempted": "Finished", "neededWork": "None",
                   "expectedArtifacts": [], "acceptance": "Already complete", "suggestedBuddyId": None}
    summary = "fixture work completed"
    if case == "wrap-up":
        summary = "Stopped partial work summary"
    if case == "long-summary-citation":
        summary = "调研结论。" * 600 + "\n<oai-mem-citation>reference</oai-mem-citation>"
    return {"disposition": disposition, "summary": summary, "remaining": [],
            "decisions": [], "artifacts": [], "request": request}


def main():
    if "--version" in sys.argv:
        print("codex-cli fixture")
        return
    case = os.environ.get("HMB_CODEX_FIXTURE_CASE", "ok")
    native_config = {"mcp_servers": {"user-tool": {}},
                     "features": {"apps": True}, "web_search": "enabled", "approval_policy": "on-request"}
    if case == "custom-provider":
        native_config["model_provider"] = "fixture-provider"
    elif case == "provider-null":
        native_config["model_provider"] = None
    user_config = Path(os.environ.get("CODEX_HOME") or Path(os.environ["HOME"]) / ".codex") / "config.toml"
    if user_config.is_file():
        native_config.update(tomllib.loads(user_config.read_text(encoding="utf-8")))
    provider = native_config.get("model_provider") or "openai"
    def merge(target, update):
        for key, value in update.items():
            if isinstance(value, dict):
                merge(target.setdefault(key, {}), value)
            else:
                target[key] = value
    for index, argument in enumerate(sys.argv[:-1]):
        if argument == "-c":
            key, _, literal = sys.argv[index + 1].partition("=")
            patch = tomllib.loads("value = " + literal)["value"]
            for segment in reversed(key.split(".")):
                patch = {segment: patch}
            merge(native_config, patch)
    initialization = None
    for raw in sys.stdin:
        req = json.loads(raw)
        if "id" not in req:
            continue
        method, params, ident = req["method"], req.get("params") or {}, req["id"]
        if method == "initialize":
            initialization = params
            send({"id": ident, "result": {"userAgent": "fixture"}})
        elif method == 'config/read':
            configured = dict(native_config)
            result = {'config': [] if case == 'config-unreadable' else configured}
            if case == 'config-result-unreadable':
                result = []
            elif case == 'config-missing':
                result = {}
            send({'id': ident, 'result': result})
        elif method == "account/read":
            if case == "account-unknown":
                account = {"type": "unrecognized"}
            elif case == "account-plan-missing":
                account = {"type": "chatgpt", "email": None}
            elif case == "account-plan-null":
                account = {"type": "chatgpt", "email": None, "planType": None}
            elif case == "account-plan-unknown":
                account = {"type": "chatgpt", "email": None, "planType": "unknown"}
            else:
                account = {"type": "apiKey" if case == "api-key" or os.environ.get("OPENAI_API_KEY") or os.environ.get("CODEX_API_KEY") else "chatgpt",
                           "email": None, "planType": "plus"}
            send({"id": ident, "result": {"account": account, "requiresOpenaiAuth": True}})
            send({"method": "account/updated", "params": {"authMode": account["type"], "planType": account.get("planType")}})
        elif method == "model/list":
            if case == "unreadable-catalog-result":
                send({"id": ident, "result": []})
                continue
            if case == "bad-catalog":
                send({"id": ident, "result": {"data": {"not": "a list"}, "nextCursor": None}})
                continue
            if case in ("unlisted-model", "long-native-error"):
                send({"id": ident, "result": {"data": [{"model": "other-model", "displayName": "Other",
                    "description": "not the selected model", "hidden": False, "isDefault": True,
                    "defaultReasoningEffort": "low", "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}]}],
                    "nextCursor": None}})
                continue
            if case == "no-efforts-row":
                # The read lists the selected model's identity but offers no
                # legal effort for it: the discovery omits the row, and the
                # execution must still see the model as listed.
                send({"id": ident, "result": {"data": [{"model": "fixture-model", "displayName": "Fixture",
                    "description": "listed with no legal efforts", "hidden": False, "isDefault": True,
                    "defaultReasoningEffort": "low", "supportedReasoningEfforts": []}],
                    "nextCursor": None}})
                continue
            send({"id": ident, "result": {"data": [] if case == "empty-catalog" else [{"id": "fixture-model", "model": "fixture-model",
                "displayName": "Fixture", "description": "fixture", "hidden": False, "isDefault": True,
                "defaultReasoningEffort": "low", "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}]}],
                "nextCursor": None}})
        elif method == "thread/start":
            state = read_state()
            thread_id = f"thread-{state['next']}"
            state["next"] += 1
            state["threads"][thread_id] = {"cwd": params["cwd"], "turns": [],
                                         'model': params['model'], 'home': os.environ.get('HOME'), 'start': params, 'argv': sys.argv, 'initialize': initialization,
                                         'codexHome': os.environ.get('CODEX_HOME'),
                                         'sqliteHome': os.environ.get('CODEX_SQLITE_HOME')}
            write_state(state)
            policy = ({'sandbox': {'type': 'readOnly', 'networkAccess': True}, 'approvalPolicy': 'never',
                       'model': params['model'], 'home': os.environ.get('HOME'), 'start': params, 'argv': sys.argv, 'modelProvider': provider, 'cwd': params['cwd']}
                      if params.get('sandbox') == 'read-only' else {})
            if case == 'readonly-network-disabled':
                policy['sandbox']['networkAccess'] = False
            elif case == 'readonly-sandbox-mismatch':
                policy['sandbox']['type'] = 'workspaceWrite'
            elif case == 'readonly-sandbox-unreadable':
                policy['sandbox'] = []
            elif case == 'readonly-model-mismatch':
                policy['model'] = 'different-model'
            elif case == 'readonly-model-missing':
                del policy['model']
            elif case == 'readonly-model-unreadable':
                policy['model'] = 17
            receipt = {**policy, "modelProvider": provider, "thread": {"id": thread_id, "cwd": params["cwd"],
                                                                       "turns": []}}
            if case == 'provider-wrong':
                receipt['modelProvider'] = 'different-provider'
            elif case == 'provider-unreadable':
                receipt['modelProvider'] = []
            elif case == 'provider-missing':
                del receipt['modelProvider']
            send({"id": ident, "result": receipt})
        elif method == "thread/read":
            if case == 'read-refused':
                send({"id": ident, "error": {"code": -32600, "message":
                      f"thread not loaded: {params['threadId']}"}})
                continue
            state = read_state()["threads"].get(params["threadId"])
            if not state or state.get('codexHome') != os.environ.get('CODEX_HOME'):
                send({"id": ident, "error": {"code": -1, "message": "missing"}})
            else:
                send({"id": ident, "result": {"thread": {"id": params["threadId"], **state}}})
        elif method == "thread/resume":
            if case in ('resume-refused', 'resume-other-refusal'):
                message = (f"no rollout found for thread id {params['threadId']}" if case == 'resume-refused'
                           else 'required MCP server failed')
                send({"id": ident, "error": {"code": -32600, "message": message}})
                continue
            if case == 'resume-no-answer':
                continue
            full_state = read_state()
            state = full_state["threads"].get(params["threadId"])
            if state:
                state.setdefault("resumeRequests", []).append(params)
                write_state(full_state)
            if not state or state.get('codexHome') != os.environ.get('CODEX_HOME'):
                send({"id": ident, "error": {"code": -1, "message": "missing"}})
            else:
                receipt = {"modelProvider": provider, "thread": {"id": params["threadId"], "cwd": state["cwd"],
                                                                 "turns": state["turns"]}}
                if case == 'provider-wrong':
                    receipt['modelProvider'] = 'different-provider'
                elif case == 'provider-unreadable':
                    receipt['modelProvider'] = []
                elif case == 'provider-missing':
                    del receipt['modelProvider']
                send({"id": ident, "result": receipt})
        elif method == "turn/start":
            if case == "large-prompt":
                state_path().with_suffix(".prompt.txt").write_bytes(params["input"][0]["text"].encode("utf-8"))
            thread_id = params["threadId"]
            state = read_state()
            state['threads'][thread_id].setdefault('turnRequests', []).append(
                {key: params[key] for key in ('cwd', 'approvalPolicy', 'sandboxPolicy', 'permissions')
                 if key in params})
            write_state(state)
            turn_index = len(state['threads'][thread_id]['turns'])
            turn_id = f"native-turn-{turn_index + 1}"
            if case in ("unlisted-model", "long-native-error"):
                # The native answer, not the adapter's catalog reading, decides:
                # the server rejects the turn under the selected model's own
                # name. The long case carries a native reason of repeated
                # three-byte CJK plus four-byte characters, far past the
                # public UTF-8 byte budget.
                state['threads'][thread_id].setdefault('rejectedTurns', []).append(
                    {'model': params['model'], 'effort': params['effort']})
                write_state(state)
                native_reason = ("模型不可用" * 80 + "\U0001D11E" * 10) if case == "long-native-error" \
                    else f"model not found: {params['model']}"
                send({"id": ident, "error": {"code": -32000, "message": native_reason}})
                continue
            if case == "quota-before-overflow":
                for _ in range(140):
                    send({"method": "thread/status/changed", "params": {"threadId": thread_id,
                          "turnId": turn_id, "status": {"type": "active"}}})
            send({"id": ident, "result": {"turn": {"id": turn_id, "status": "inProgress", "items": []}}})
            send({"method": "turn/started", "params": {"threadId": thread_id, "turn": {"id": turn_id, "status": "inProgress", "items": []}}})
            if case.startswith("quota-before") or case.startswith("quota-activity-"):
                activity = case.removeprefix("quota-activity-")
                if activity in ("text", "reasoning", "tool"):
                    item = {"type": {"text": "agentMessage", "reasoning": "reasoning",
                                      "tool": "mcpToolCall"}[activity], "id": "activity-1"}
                    if activity == "text":
                        item["text"] = "partial output"
                    send({"method": "item/started", "params": {
                        "threadId": thread_id, "turnId": turn_id, "item": item}})
                elif activity == "tokens":
                    send_usage("quota-failure", thread_id, turn_id, turn_index)
                error = {"message": "native quota exceeded", "codexErrorInfo": "usageLimitExceeded"}
                if case == "quota-before-truncated":
                    send({"method": "error", "params": {"threadId": thread_id, "turnId": turn_id,
                                                           "error": error, "willRetry": False}})
                    sys.stdout.write('{"method":')
                    sys.stdout.flush()
                    return
                turn = {"id": turn_id, "status": "failed", "items": [], "error": error}
                send({"method": "turn/completed", "params": {"threadId": thread_id, "turn": turn}})
                continue
            if case in ("inquiry-active", "inquiry-unanswered", "inquiry-invalid-retry"):
                state['threads'][thread_id]['turnStart'] = {"threadId": thread_id, "turnId": turn_id}
                write_state(state)
                steer = json.loads(sys.stdin.readline())
                if steer.get("method") != "turn/steer" or steer["params"].get("expectedTurnId") != turn_id:
                    raise RuntimeError("expected active-turn inquiry steer")
                state = read_state()
                state['threads'][thread_id]['steer'] = steer['params']
                write_state(state)
                send({"id": steer["id"], "result": {"turnId": turn_id}})
                if case != "inquiry-unanswered":
                    prompt = steer['params']['input'][0]['text']
                    inquiry_id = json.loads(prompt.splitlines()[-1])['inquiryId']
                    tools = state['threads'][thread_id]['start']['dynamicTools']
                    if len(tools) != 1 or tools[0]['name'] != 'hey_my_buddy_answer_inquiry':
                        raise RuntimeError("expected one inquiry client tool")
                    calls = []
                    if case == "inquiry-invalid-retry":
                        calls.extend([
                            {"threadId": "child-thread"}, {"turnId": "old-turn"},
                            {"arguments": {"inquiryId": inquiry_id, "answer": "42", "extra": True}},
                            {"arguments": {"inquiryId": "unknown", "answer": "42"}},
                        ])
                    calls.append({})
                    if case == "inquiry-invalid-retry":
                        calls.append({})  # already answered
                    for index, overrides in enumerate(calls):
                        call_params = {"threadId": thread_id, "turnId": turn_id,
                                       "callId": f"inquiry-call-{index}", "tool": tools[0]['name'],
                                       "arguments": {"inquiryId": inquiry_id, "answer": "42"}, **overrides}
                        send({"id": f"client-tool-{index}", "method": "item/tool/call", "params": call_params})
                        reply = json.loads(sys.stdin.readline())
                        if reply.get('id') != f"client-tool-{index}":
                            raise RuntimeError("uncorrelated client tool reply")
                        state = read_state()
                        state['threads'][thread_id].setdefault('clientReplies', []).append(reply)
                        write_state(state)
            send_usage(case, thread_id, turn_id, turn_index)
            if case == 'session-rewrite':
                send({'method': 'item/agentMessage/delta', 'params': {'threadId': thread_id, 'turnId': turn_id,
                      'itemId': 'revised', 'delta': 'streamed wording'}})
                send({'method': 'item/completed', 'params': {'threadId': thread_id, 'turnId': turn_id,
                      'item': {'id': 'revised', 'type': 'agentMessage', 'text': 'different completed wording'}}})
                send({'method': 'item/started', 'params': {'threadId': thread_id, 'turnId': turn_id,
                      'item': {'id': '', 'type': 'commandExecution', 'command': 'empty native id'}}})
            if case in ("session-stream", "session-stream-disconnect"):
                for method, fields in session_frames(params['input'][0]['text']):
                    send({"method": method, "params": {"threadId": thread_id, "turnId": turn_id, **fields}})
                # The controller test releases this only after it reads a
                # flushed message while no completed item has been sent.
                release = state_path().with_suffix('.release')
                deadline = time.monotonic() + 5
                while not release.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                if not release.exists():
                    raise RuntimeError('session stream was not visible before completion')
                if case == 'session-stream-disconnect':
                    return
                send({"method": "item/completed", "params": {"threadId": thread_id, "turnId": turn_id,
                      "item": {"id": "stream", "type": "agentMessage", "phase": "commentary", "text": "运行中𝄞，结束"}}})
            if case.startswith("protocol-"):
                for method, protocol_item in item_frames(case.removeprefix("protocol-")):
                    send({"method": method, "params": {"threadId": thread_id, "turnId": turn_id,
                          "item": protocol_item}})
            if case in ("schema-failure", "long-schema-failure"):
                turn = {"id": turn_id, "status": "failed", "items": [], "error": {
                    "message": "Invalid schema: required is missing suggestedBuddyId" if case == "schema-failure" else "原生格式错误𝄞" * 2000,
                    "codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 400}},
                    "additionalDetails": "invalid_request_error / invalid_json_schema",
                    "unrelated": "do not retain this field"}}
                send({"method": "turn/completed", "params": {"threadId": thread_id, "turn": turn}})
                state["threads"][thread_id]["turns"].append(turn)
                write_state(state)
                continue
            if case in ("hang", "interrupt-settled", "interrupt-foreign", "session-interrupt") or (case == "wrap-up" and turn_index == 0):
                # These scenarios record an explicit start fact, and only
                # after the turn/start response and the turn/started notification
                # were actually emitted: a thread merely existing never counts as a
                # started turn. The cancellation tests wait for this fact instead
                # of sleeping blindly.
                state = read_state()
                state['threads'][thread_id]['turnStart'] = {"threadId": thread_id, "turnId": turn_id}
                write_state(state)
                if case == "hang":
                    time.sleep(60)
                else:
                    request = json.loads(sys.stdin.readline())
                    if request.get("method") != "turn/interrupt":
                        raise RuntimeError("expected the native turn interrupt")
                    send({"id": request["id"], "result": {}})
                    if case == 'session-interrupt':
                        send({"method": "item/agentMessage/delta", "params": {"threadId": thread_id,
                              "turnId": turn_id, "itemId": "late", "delta": "received during interrupt"}})
                    terminal_id = "foreign-turn" if case == "interrupt-foreign" else turn_id
                    turn = {"id": terminal_id, "status": "interrupted", "items": []}
                    send({"method": "turn/completed", "params": {"threadId": thread_id, "turn": turn}})
                    state = read_state()
                    state["threads"][thread_id]["turns"].append(turn)
                    write_state(state)
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
            if case == "readonly-unknown-item-shape":
                send({"method": "thread/futureItem", "params": {"threadId": thread_id, "turnId": turn_id,
                      "item": "new-native-shape"}})
                send({"method": "item/started", "params": {"threadId": thread_id, "turnId": turn_id,
                      "item": "new-native-shape"}})
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
            items = []
            if case in ("final-invalid-then-valid", "final-valid-then-valid", "final-last-invalid"):
                earlier = {**item, "text": json.dumps({"outcome": {
                    **outcome(case), "summary": "较早的最终回答"}}, ensure_ascii=False)}
                if case == "final-invalid-then-valid":
                    earlier["text"] = '{"outcome":{"summary":"未完成"}' + " " * 680000
                items.append(earlier)
                send({"method": "item/completed", "params": {
                    "threadId": thread_id, "turnId": turn_id, "item": earlier}})
                item["id"] = "final-2"
                if case == "final-last-invalid":
                    item["text"] = "not json"
            if "buddyId" in params.get("outputSchema", {}).get("properties", {}):
                item["text"] = json.dumps({"buddyId": "foreign" if case == "readonly-outside" else "legal", "note": "Read-only fixture", "evidence": []})
                if case in ("readonly-repair", "readonly-repair-evidence") and not state['threads'][thread_id]['turns']:
                    item["text"] = "not-json"
            if case != "no-final":
                items.append(item)
                send({"method": "item/completed", "params": {"threadId": thread_id, "turnId": turn_id,
                                                            "item": item, "completedAtMs": 1}})
            if case == "disconnect-after-message":
                return
            status = "failed" if case in ("failed", "approval-failed", "quota-failure") else "completed"
            turn = {"id": turn_id, "status": status, "items": items}
            if case == "quota-failure":
                turn["error"] = {"message": "native usage limit exceeded",
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
