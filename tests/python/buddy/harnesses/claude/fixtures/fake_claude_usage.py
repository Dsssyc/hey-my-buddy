#!/usr/bin/env python3
"""Private Claude Code CLI fixture for the ADR-018 native usage records.

It speaks the same stdio control protocol as ``fake_claude.py`` (initialize,
correlated responses, interrupt) but replays the desensitized records of
``native-usage-claude.json``: assistant frames with Anthropic usage objects, a
replayed duplicate, a foreign session, a subagent frame, rate-limit events with
``unifiedWindows`` and a final ``result`` frame. No model, no network, no shared
Claude state.
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

FIXTURE = json.loads(Path(__file__).with_name("native-usage-claude.json").read_text())


def send(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def argv_value(flag):
    for index, item in enumerate(sys.argv):
        if item == flag and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
        if item.startswith(flag + "="):
            return item[len(flag) + 1:]
    return None


def write_state(update):
    path = os.environ.get("BUDDY_CLAUDE_FIXTURE_STATE")
    if not path:
        return
    state = {}
    try:
        state = json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError):
        state = {}
    state.update(update)
    Path(path).write_text(json.dumps(state))


def rewrite(value, session_id):
    """Replace the fixture session identity with the preallocated one."""
    if isinstance(value, str):
        return session_id if value == FIXTURE["sessionId"] else value
    if isinstance(value, dict):
        return {key: rewrite(item, session_id) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite(item, session_id) for item in value]
    return value


def initialize_response():
    # ``default`` is the alias entry the catalog deliberately skips, so the
    # fixture exposes the concrete model id under a normal selectable value.
    return {"models": [{"value": "fixture", "resolvedModel": FIXTURE["modelId"], "displayName": "Fixture",
                        "description": "fixture", "supportsEffort": True,
                        "supportedEffortLevels": ["low", "high"]}],
            "account": {"apiProvider": "firstParty", "tokenSource": "subscription"}}


def outcome_frame():
    return {"disposition": "completed", "summary": "fixture work completed", "remaining": [],
            "decisions": [], "artifacts": [], "request": None}


def result_frame(case, session_id):
    result = {"type": "result", "subtype": "success", "is_error": False, "session_id": session_id,
              "result": "fixture final text", "structured_output": {"outcome": outcome_frame()},
              "permission_denials": [], "num_turns": 2,
              "modelUsage": {FIXTURE["modelId"]: {"inputTokens": 15000, "outputTokens": 350}},
              "total_cost_usd": 0.01}
    if case not in ("usage-partial", "usage-none"):
        result["usage"] = dict(FIXTURE["resultUsage"])
    return result


def run_turn(case, session_id):
    send({"type": "system", "subtype": "init", "session_id": session_id, "cwd": os.getcwd(),
          "model": argv_value("--model") or "", "permissionMode": "default", "tools": []})
    if case == "usage-long-text":
        full = "long-fixture-text:" + "x" * 70000
        raw = full.encode()
        write_state({"longTextBytes": len(raw), "longTextSha256": hashlib.sha256(raw).hexdigest()})
        frames = [{"type": "assistant", "session_id": session_id, "uuid": "frame-fixture-long",
                   "message": {"id": "msg-fixture-long", "role": "assistant",
                               "content": [{"type": "text", "text": full}],
                               "usage": {"input_tokens": 10, "cache_read_input_tokens": 0,
                                         "cache_creation_input_tokens": 0, "output_tokens": 5}}}]
    elif case == "usage-none":
        frames = [{"type": "assistant", "session_id": session_id, "uuid": "frame-fixture-none",
                   "message": {"id": "msg-fixture-none", "role": "assistant",
                               "content": [{"type": "text", "text": "No native usage was reported."}]}}]
    else:
        frames = FIXTURE["assistantFrames"]
    for frame in frames:
        if case == "usage-quota" and frame["message"]["id"] != "msg-fixture-0001":
            continue
        send(rewrite(frame, session_id))
    if case == "usage-quota":
        send({"type": "rate_limit_event", "rate_limit_info": FIXTURE["rateLimitRejected"]["rate_limit_info"]})
        # The controller interrupts the native child after a quota rejection and
        # waits briefly for this acknowledgement.
        for raw in sys.stdin:
            try:
                frame = json.loads(raw)
            except ValueError:
                continue
            if frame.get("type") == "control_request":
                send({"type": "control_response", "response": {
                    "subtype": "success", "request_id": frame["request_id"], "response": {}}})
                return
        return
    if case == "usage-none":
        pass
    else:
        send({"type": "rate_limit_event", "rate_limit_info": FIXTURE["rateLimitWarning"]["rate_limit_info"]})
    send(result_frame(case, session_id))


def main():
    if "--version" in sys.argv:
        print(FIXTURE["harnessVersion"] + " (Claude Code usage fixture)")
        return
    case = os.environ.get("BUDDY_CLAUDE_FIXTURE_CASE", "usage-ok")
    session_id = argv_value("--session-id") or "unallocated"
    for raw in sys.stdin:
        try:
            frame = json.loads(raw)
        except ValueError:
            continue
        if frame.get("type") == "control_request":
            request = frame.get("request") or {}
            if request.get("subtype") == "initialize":
                send({"type": "control_response", "response": {"subtype": "success",
                                                               "request_id": frame["request_id"],
                                                               "response": initialize_response()}})
            elif request.get("subtype") == "interrupt":
                send({"type": "control_response", "response": {"subtype": "success",
                                                               "request_id": frame["request_id"],
                                                               "response": {}}})
                return
            else:
                send({"type": "control_response", "response": {"subtype": "error",
                                                               "request_id": frame["request_id"],
                                                               "error": "unsupported fixture callback"}})
        elif frame.get("type") == "user":
            run_turn(case, session_id)
            return


if __name__ == "__main__":
    main()
