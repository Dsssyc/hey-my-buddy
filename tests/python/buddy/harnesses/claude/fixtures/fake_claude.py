#!/usr/bin/env python3
"""Private Claude Code CLI fixture. No model, no shared Claude state, no network."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# The exact native shapes the Host verified on the real 2.1.282 CLI through an
# initialize-only probe (2026-09-26): the ``default`` alias entry, canonical
# resolved models including the raw ``[1m]`` suffix, and haiku without levels.
DEFAULT_MODELS = [
    {"value": "default", "resolvedModel": "claude-opus-5-5[1m]", "displayName": "Default", "description": "alias entry",
     "supportsEffort": True, "supportedEffortLevels": ["low", "high"]},
    {"value": "opus", "resolvedModel": "claude-opus-5-5[1m]", "displayName": "Opus", "description": "canonical opus",
     "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high"]},
    {"value": "opus-duplicate", "resolvedModel": "claude-opus-5-5[1m]", "displayName": "Opus again", "description": "duplicate",
     "supportsEffort": True, "supportedEffortLevels": ["low"]},
    {"value": "fable", "resolvedModel": "claude-fable-5-1", "displayName": "Fable", "description": "fable",
     "supportsEffort": True, "supportedEffortLevels": ["low", "high"]},
    {"value": "sonnet", "resolvedModel": "claude-sonnet-5", "displayName": "Sonnet", "description": "sonnet",
     "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high"]},
    {"value": "haiku", "resolvedModel": "claude-haiku-4-5-20251001", "displayName": "Haiku", "description": "no effort levels",
     "supportsEffort": False},
]


def send(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def state_path():
    return Path(os.environ["HMB_CLAUDE_FIXTURE_STATE"])


def read_state():
    try:
        return json.loads(state_path().read_text())
    except (FileNotFoundError, ValueError):
        return {}


def write_state(update):
    state = read_state()
    state.update(update)
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Tests poll while the mock is running. Publish a complete old/new snapshot
    # instead of exposing the empty interval created by write_text truncation.
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=path.name+".", delete=False) as stream:
        json.dump(state, stream)
        temporary = stream.name
    os.replace(temporary, path)


def argv_value(flag):
    for index, item in enumerate(sys.argv):
        if item == flag and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
        if item.startswith(flag + "="):
            return item[len(flag) + 1:]
    return None


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


def initialize_response(case):
    # Host's initialize-only read of Claude Code 2.1.284 (2026-10-12):
    # subscription login supplies account details without tokenSource.
    account = {"apiProvider": "firstParty", "email": "subscriber@example.invalid",
               "organization": "fixture-organization", "subscriptionType": "max"}
    if case == "token-source":
        account = {"apiProvider": "firstParty", "tokenSource": "subscription"}
    if case == "third-party":
        account = {"apiProvider": "bedrock"}
    if case == "api-key-source":
        account = {"apiProvider": "firstParty", "tokenSource": "none", "apiKeySource": "ANTHROPIC_API_KEY"}
    if case == "null-token":
        account["tokenSource"] = None
    if case == "provider-only":
        account = {"apiProvider": "firstParty"}
    if case == "empty-account":
        account = {}
    if case == "invalid-account":
        account = "not-an-account-object"
    response = {"models": None if case == "invalid-catalog" else DEFAULT_MODELS}
    if case == "empty-catalog":
        response["models"] = []
    if case == "oversized-catalog":
        response["models"] = [{}] * 201
    if case != "missing-account":
        response["account"] = None if case == "null-account" else account
    return response


def result_frame(case, session_id):
    structured = {"outcome": outcome(case)} if case != "bad-structured" else "not-an-object"
    if "--json-schema" in sys.argv:
        schema = json.loads(sys.argv[sys.argv.index("--json-schema") + 1])
        if "buddyId" in schema.get("properties", {}):
            structured = {"buddyId": "legal", "reason": "Read-only fixture", "evidence": []}
    result = {"type": "result", "subtype": "success", "is_error": False, "session_id": session_id,
              "result": "fixture final text", "structured_output": structured,
              "permission_denials": [{"tool_name": "WebFetch", "reason": "fixture denial"}]
                  if case in ("permission", "result-denials") else [],
              "modelUsage": {"claude-opus-5-5[1m]": {"inputTokens": 10}, "claude-haiku-4-5-20251001": {"inputTokens": 2}},
              "total_cost_usd": 0.01}
    if case == "large-value":
        # A delivery below the native 8 MiB frame bound but above the run
        # contract's value bound: the driver must drop the value alone and
        # keep reporting every other confirmed fact.
        result["structured_output"] = {"outcome": {**structured["outcome"],
                                                   "summary": "x" * (600 * 1024)}}
    if case == "failed":
        result.update(is_error=True, subtype="error_during_execution")
    if case == "error-subtype-without-flag":
        result["subtype"] = "error_during_execution"
        result.pop("is_error")
    if case == "error-flag-with-unknown-subtype":
        result.update(is_error=True, subtype="future")
    if case == "result-quota":
        result.update(is_error=True, subtype="error_quota_during_execution")
    if case == "api-429":
        result.update(is_error=True, api_error_status=429)
    if case in ("bad-subtype", "unknown-initialize-with-unread-terminal"):
        result["subtype"] = "completed"
    if case == "missing-is-error":
        result.pop("is_error")
    if case == "non-root-result":
        # A subagent result frame never ends the root turn; the root result follows.
        return [{**result, "parent_tool_use_id": "toolu_subagent_1"}, result]
    return result


def handle_control(frame, case):
    request = frame.get("request") or {}
    rid = frame.get("request_id")
    if request.get("subtype") == "initialize":
        state = read_state()
        state["initialize"] = True
        write_state(state)
        if case == "early-result":
            # Emit before the initialize acknowledgement: the controller cannot
            # send its user message until it has consumed these preceding frames.
            send({"type": "assistant", "message": {"role": "assistant",
                                                   "content": [{"type": "text", "text": "premature"}]}})
            send({"type": "result", "subtype": "success", "session_id": "whatever",
                  "structured_output": {"outcome": outcome("ok")}})
        if case in ("missing-event-type", "unknown-initialize-with-untyped-event",
                    "unknown-initialize-with-unread-terminal"):
            send({"payload": "credential-fragment"})
        send({"type": "control_response", "response": {
            "subtype": "future" if case in ("unknown-initialize", "unknown-initialize-with-untyped-event",
                                             "unknown-initialize-with-unread-terminal") else
                       "error" if case == "initialize-error" else "success",
            "request_id": rid, "response": initialize_response(case)}})
        if case == "early-result":
            for _ in sys.stdin:
                pass
            sys.exit(0)
    elif request.get("subtype") == "interrupt":
        state = read_state()
        state["interrupted"] = True
        write_state(state)
        if case == "interrupt-quota":
            send({"type": "rate_limit_event", "rate_limit_info": {
                "status": "rejected", "rateLimitType": "five_hour", "resetsAt": 1790467200}})
        send({"type": "control_response", "response": {"subtype": "success", "request_id": rid, "response": {}}})
        sys.exit(0)
    else:
        send({"type": "control_response", "response": {"subtype": "error", "request_id": rid, "error": "unknown"}})


def wait_for_interrupt():
    for raw in sys.stdin:
        try:
            frame = json.loads(raw)
        except ValueError:
            continue
        if frame.get("type") == "control_request":
            handle_control(frame, os.environ.get("HMB_CLAUDE_FIXTURE_CASE", "ok"))


def run_turn(case):
    session_id = argv_value("--session-id") or "unallocated"
    if case == "invalid-json":
        sys.stdout.write("{not json\n")
        sys.stdout.flush()
        return
    if case == "nonfinite":
        sys.stdout.write('{"type":"assistant","cost":NaN}\n')
        sys.stdout.flush()
        return
    if case == "unknown-response":
        send({"type": "control_response", "response": {"subtype": "success", "request_id": "hey-my-buddy-999", "response": {}}})
        return
    if case in ("permission", "permission-command", "permission-path", "permission-long-command"):
        tool, inputs = {
            "permission": ("WebFetch", {"url": "https://example.com"}),
            "permission-command": ("Bash", {"command": "FOO=bar npm --prefix apps/console test"}),
            "permission-path": ("Write", {"file_path": "/outside/denied.txt", "content": "not an input summary"}),
            "permission-long-command": ("Bash", {"command": "echo " + "测" * 300}),
        }[case]
        send({"type": "control_request", "request_id": "perm-1",
              "request": {"subtype": "can_use_tool", "tool_name": tool,
                          "input": inputs, "tool_use_id": "toolu_fixture_1"}})
        reply = json.loads(sys.stdin.readline())
        response = reply.get("response") or {}
        write_state({"permissionReply": {"subtype": response.get("subtype"),
                                         "behavior": (response.get("response") or {}).get("behavior")}})
        if (response.get("response") or {}).get("behavior") != "deny":
            raise RuntimeError("the controller unexpectedly approved the native tool request")
    if case in ("hang", "quota-rejected", "quota-bad-type", "quota-free-text-type", "warning-then-rejected",
                "interrupt-quota"):
        if case == "warning-then-rejected":
            send({"type": "rate_limit_event", "rate_limit_info": {
                "status": "allowed_warning", "rateLimitType": "seven_day", "utilization": .9}})
        if case in ("quota-rejected", "warning-then-rejected"):
            send({"type": "rate_limit_event", "rate_limit_info": {
                "status": "rejected", "rateLimitType": "five_hour", "resetsAt": "2026-09-26T12:00:00Z",
                "utilization": {"fiveHourPctUsed": 100}}})
        if case == "quota-bad-type":
            send({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}})
        if case == "quota-free-text-type":
            send({"type": "rate_limit_event", "rate_limit_info": {
                "status": "rejected", "rateLimitType": "Bearer sk-ant-api03-SECRET-FRAGMENT\n",
                "resetsAt": "2026-09-26T12:00:00Z"}})
        wait_for_interrupt()
        return
    init = {"type": "system", "subtype": "init",
          "session_id": "not-the-allocation" if case == "init-wrong-session" else session_id,
          "cwd": "/definitely/elsewhere" if case == "wrong-cwd" else os.getcwd(),
          "model": argv_value("--model") or "",
          "permissionMode": "default" if case == "permission-mode-mismatch" else
          {"future": "mode"} if case == "permission-mode-unread" else
          "future-mode" if case == "permission-mode-future" else
          argv_value("--permission-mode") or "default", "tools": []}
    if case == "permission-mode-missing":
        del init["permissionMode"]
    send(init)
    if case in ("bg-running", "bg-settled", "bg-not-completed"):
        send({"type": "system", "subtype": "task_started", "task_id": "task-bg-1", "task_type": "local_agent"})
        if case == "bg-settled":
            send({"type": "system", "subtype": "task_updated", "task_id": "task-bg-1",
                  "patch": {"status": "completed"}})
        if case == "bg-not-completed":
            # A status outside the exact terminal set must not settle the task.
            send({"type": "system", "subtype": "task_updated", "task_id": "task-bg-1",
                  "patch": {"status": "not_completed"}})
    if case == "bg-notification":
        # The 2.1.284 shape the Host reproduced for a foreground Bash command
        # that ran long enough to get a task identity: the CLI closes it with
        # ``task_notification`` and sends no ``task_updated``.
        send({"type": "system", "subtype": "task_started", "task_id": "b67sc680t",
              "tool_use_id": "toolu_fixture_bg_1", "description": "Sleep briefly and print short",
              "is_backgrounded": False, "task_type": "local_bash"})
        send({"type": "system", "subtype": "task_notification", "task_id": "b67sc680t",
              "tool_use_id": "toolu_fixture_bg_1", "status": "completed", "output_file": "",
              "summary": "Sleep briefly and print short"})
    if case == "quota-warning":
        send({"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed_warning", "rateLimitType": "seven_day", "resetsAt": "2026-09-27T00:00:00Z",
            "utilization": {"sevenDayPctUsed": 43}}})
        send({"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "rateLimitType": "five_hour", "resetsAt": None,
            "utilization": {"fiveHourPctUsed": 12}}})
    send({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "working"}]}})
    if case == "no-result":
        # Exit without a result frame: the controller must see the stream close.
        sys.exit(0)
    result = result_frame(case, "00000000-0000-4000-8000-000000000000" if case == "wrong-session" else session_id)
    frames = result if isinstance(result, list) else [result]
    for frame in frames:
        send(frame)
    if case == "duplicate-result":
        send(dict(frames[-1]))
    # A -p CLI exits after its final result; the controller drains to this EOF.
    sys.exit(0)


def main():
    if "--version" in sys.argv:
        print("2.1.282 (Claude Code fixture)")
        return
    if "auth" in sys.argv and "status" in sys.argv:
        state = read_state()
        write_state({"authStatusRuns": state.get("authStatusRuns", 0) + 1})
        send({"loggedIn": True, "apiProvider": "firstParty"})
        return
    case = os.environ.get("HMB_CLAUDE_FIXTURE_CASE", "ok")
    state = read_state()
    write_state({"argv": sys.argv, "cwd": os.getcwd(), "initialize": False, "userTurns": 0, "interrupted": False,
                 "runs": state.get("runs", 0) + 1,
                 "seenBuddyEnv": sorted(key for key in os.environ if key.startswith("HMB_")),
                 "seenAnthropicEnv": sorted(key for key in os.environ if key.startswith("ANTHROPIC_"))})
    for raw in sys.stdin:
        try:
            frame = json.loads(raw)
        except ValueError:
            continue
        if frame.get("type") == "control_request":
            handle_control(frame, case)
        elif frame.get("type") == "user":
            state = read_state()
            state["userTurns"] = state.get("userTurns", 0) + 1
            write_state(state)
            run_turn(case)


if __name__ == "__main__":
    main()
