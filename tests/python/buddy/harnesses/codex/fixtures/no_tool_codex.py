#!/usr/bin/env python3
"""Offline App Server fixture for Codex's no-tool controller."""
import json
import os
import sys
import time
import tomllib
from pathlib import Path

from protocol_items import item_frames

sys.stdin.reconfigure(encoding="utf-8")


def send(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def event(method, turn_id, **params):
    send({"method": method, "params": {"threadId": "thread-1", "turnId": turn_id, **params}})


def main():
    if "--version" in sys.argv:
        print("codex-cli 0.157.0")
        return
    case = os.environ.get("HMB_CODEX_FIXTURE_CASE", "ok")
    turns = 0
    native_config = {"mcp_servers": {"user-tool": {}},
                     "web_search": "enabled", "approval_policy": "on-request"}
    user_config = Path(os.environ.get("CODEX_HOME") or Path(os.environ["HOME"]) / ".codex") / "config.toml"
    if user_config.is_file():
        native_config.update(tomllib.loads(user_config.read_text(encoding="utf-8")))
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
    for raw in sys.stdin:
        request = json.loads(raw)
        if "id" not in request:
            continue
        method, params, ident = request["method"], request.get("params") or {}, request["id"]
        if method == "initialize":
            send({"id": ident, "result": {}})
        elif method == "account/read":
            send({"id": ident, "result": {"account": {"type": "chatgpt", "planType": "plus"}}})
            send({"method": "account/updated", "params": {"authMode": "chatgpt", "planType": "plus"}})
        elif method == "model/list":
            entry = {"model": "fixture-model", "displayName": "Fixture",
                     "supportedReasoningEfforts": [{"reasoningEffort": "low"}]}
            if case == "unlisted-model":
                entry = {"model": "other-model", "displayName": "Other",
                         "supportedReasoningEfforts": [{"reasoningEffort": "low"}]}
            send({"id": ident, "result": {"data": [entry], "nextCursor": None}})
        elif method == "config/read":
            config = dict(native_config)
            if case == "config-mismatch":
                config["web_search"] = "enabled"
            send({"id": ident, "result": {"config": config,
                 "layers": [{"name": {"type": "user", "file": "<native-user-config>"},
                             "config": {"mcp_servers": {"user-tool": {}}, "features": {"apps": True}}, "version": "1"}]}})
        elif method == "thread/start":
            trace = Path(os.environ["HMB_CODEX_FIXTURE_STATE"])
            trace.write_text(json.dumps({"thread": params, "home": os.environ["CODEX_HOME"], "userHome": os.environ["HOME"], "argv": sys.argv}), encoding="utf-8", newline="\n")
            receipt = {"thread": {"id": "thread-1", "cwd": params["cwd"], "environments": []},
                       "model": "other" if case == "wrong-model" else params["model"],
                       "modelProvider": native_config.get("model_provider") or "openai", "approvalPolicy": "never"}
            if case == "missing-model":
                del receipt["model"]
            elif case == "unreadable-model":
                receipt["model"] = ["new-native-shape"]
            if case == "provider-wrong":
                receipt["modelProvider"] = "different-provider"
            elif case == "provider-unreadable":
                receipt["modelProvider"] = []
            elif case == "provider-missing":
                del receipt["modelProvider"]
            send({"id": ident, "result": receipt})
        elif method == "turn/start":
            turns += 1
            trace = Path(os.environ["HMB_CODEX_FIXTURE_STATE"])
            recorded = json.loads(trace.read_text(encoding="utf-8"))
            recorded.setdefault("turns", []).append(params)
            trace.write_text(json.dumps(recorded), encoding="utf-8", newline="\n")
            turn_id = f"turn-{turns}"
            if case == "unlisted-model":
                # The native answer decides: the server rejects the turn under
                # the selected model's own name.
                send({"id": ident, "error": {"code": -32000, "message": "model not found"}})
                continue
            if case == 'native-metadata':
                send({'method': 'thread/started', 'params': {'thread': {
                    'id': 'thread-1', 'environments': [{'id': 'user-native-environment'}]}}})
                send({'method':'thread/settings/updated','params':{'threadId':'thread-1','threadSettings':{}}})
                send({'method':'thread/status/changed','params':{'threadId':'thread-1','status':{'type':'active'}}})
                send({'method':'account/rateLimits/updated','params':{'rateLimits':{}}})
            startup_items = {"startup-unknown": [{"type": "futureThing", "id": "future-1"}],
                             "startup-tool": [{"type": "commandExecution", "id": "command-1"}],
                             "startup-unreadable-item": [17],
                             "startup-unreadable-type": [{"type": []}],
                             "startup-unreadable": "new-native-shape"}.get(case)
            send({"id": ident, "result": {"turn": {"id": turn_id, "items": startup_items}}})
            if case in ("thread-notification", "foreign-turn", "foreign-thread"):
                send({"method": "thread/status/changed" if case == "foreign-turn" else "thread/futureNotification", "params": {
                    "threadId": "thread-foreign" if case == "foreign-thread" else "thread-1",
                    **({"turnId": "turn-foreign"} if case == "foreign-turn" else {})}})
            if case == "foreign-thread-started":
                send({"method": "thread/started", "params": {"thread": {"id": "thread-foreign"}}})
            event("turn/started", turn_id, turn={"id": turn_id})
            if case.startswith("protocol-"):
                for method, item in item_frames(case.removeprefix("protocol-")):
                    event(method, turn_id, item=item)
            if case == "connection-notification":
                send({"method": "account/updated", "params": {"authMode": "chatgpt", "planType": "plus"}})
            if case in ("quota-before", "quota-text", "quota-error", "overload-before", "overload-text", "overload-error"):
                if case in ("quota-text", "overload-text"):
                    event("item/completed", turn_id, item={"id": "partial", "type": "agentMessage", "text": "partial"})
                error = {"message": "Selected model is at capacity. Please try a different model.",
                         "codexErrorInfo": "serverOverloaded"} if case.startswith("overload-") else {
                         "message": "native quota exceeded", "codexErrorInfo": "usageLimitExceeded"}
                if case in ("quota-error", "overload-error"):
                    event("error", turn_id, error=error, willRetry=False)
                else:
                    event("turn/completed", turn_id, turn={"id": turn_id, "status": "failed", "items": [], "error": error})
                continue
            if case == "native-failure":
                event("turn/completed", turn_id, turn={"id": turn_id, "status": "failed", "items": [],
                      "error": {"message": "Native request failed: invalid_json_schema", "codexErrorInfo": "badRequest"}})
                continue
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
            if case in ("usage", "usage-format", "usage-then-missing"):
                if case != "usage-then-missing" or turns == 1:
                    usage = {"inputTokens": 17, "cachedInputTokens": 3, "outputTokens": 5,
                             "reasoningOutputTokens": 1, "totalTokens": 22}
                    event("thread/tokenUsage/updated", turn_id, tokenUsage={
                        "last": usage, "total": {key: value * turns for key, value in usage.items()}})
            answer = "not-json" if case in ("format", "usage-format", "usage-then-missing") and turns == 1 else json.dumps({"buddyId": "foreign" if case == "outside" else "legal"})
            final = {"type": "agentMessage", "id": f"final-{turns}", "phase": "final_answer", "text": answer}
            event("item/completed", turn_id, item=final)
            items = [final]
            if case.startswith("protocol-"):
                items.extend(item for method, item in item_frames(case.removeprefix("protocol-"))
                             if method == "item/completed")
            if case == "turn-item":
                items.append({"type": "dynamicToolCall"})
            event("turn/completed", turn_id, turn={"id": turn_id, "status": "completed", "items": items})
            if case == "late-unknown-frame":
                send({"method": []})
            if case == "late":
                event("rawResponseItem/completed", turn_id, item={"type": "custom_tool_call_output"})
        elif method == "turn/interrupt":
            send({"id": ident, "result": {}})


if __name__ == "__main__":
    main()
