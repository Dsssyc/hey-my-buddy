#!/usr/bin/env python3
"""Dedicated Claude stream-json tool fixture for the read-only evidence tests.

No model, no shared Claude state, no network. The fixture scripts the native
tool frames the shared projection must survive: assistant and stream starts,
user tool_result ends, block_stop transport frames, subagent substreams,
foreign sessions, replays, conflicts and the 128-event overflow.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

DEFAULT_MODELS = [
    {"value": "default", "resolvedModel": "claude-opus-5-5[1m]", "displayName": "Default",
     "description": "alias entry", "supportsEffort": True, "supportedEffortLevels": ["low", "high"]},
    {"value": "opus", "resolvedModel": "claude-opus-5-5[1m]", "displayName": "Opus",
     "description": "canonical opus", "supportsEffort": True, "supportedEffortLevels": ["low", "high"]},
]


def send(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def state_path():
    return Path(os.environ["BUDDY_CLAUDE_FIXTURE_STATE"])


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


def frame_fields(session=None, parent=None):
    fields = {}
    if session is not None:
        fields["session_id"] = session
    if parent is not None:
        fields["parent_tool_use_id"] = parent
    return fields


def assistant_tool(name, call_id, session=None, parent=None, with_name=True):
    block = {"type": "tool_use", "id": call_id}
    if with_name:
        block["name"] = name
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "checking"}, block]}, **frame_fields(session, parent)}


def stream_tool_start(name, call_id, session=None, parent=None):
    return {"type": "stream_event", "event": {"type": "content_block_start", "index": 1,
            "content_block": {"type": "tool_use", "id": call_id, "name": name}},
            **frame_fields(session, parent)}


def block_stop(index=1, session=None):
    return {"type": "stream_event", "event": {"type": "content_block_stop", "index": index},
            **frame_fields(session)}


def tool_result(call_id, session=None, parent=None):
    return {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call_id, "content": "ok"}]},
            **frame_fields(session, parent)}


def structured_use(call_id, value, session=None, parent=None):
    """The built-in StructuredOutput delivery the --json-schema CLI itself adds.

    Shaped from the Host-verified fact that ``--json-schema`` is implemented
    through a StructuredOutput tool: the final assistant turn carries one
    tool_use block whose input is the structured value.
    """
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": call_id, "name": "StructuredOutput", "input": value}]},
        **frame_fields(session, parent)}


def result_frame(session_id):
    structured = {"outcome": {"disposition": "completed", "summary": "stream fixture", "remaining": [],
                              "decisions": [], "artifacts": [], "request": None}}
    if "--json-schema" in sys.argv:
        structured = {"profileId": "legal", "reason": "Read-only stream fixture", "evidence": []}
    return {"type": "result", "subtype": "success", "is_error": False, "session_id": session_id,
            "structured_output": structured}


def handle_control(frame, case):
    request = frame.get("request") or {}
    rid = frame.get("request_id")
    if request.get("subtype") == "initialize":
        state = read_state()
        state["initialize"] = True
        write_state(state)
        if case == "early-before-response":
            # Written before the initialize response, so the controller's
            # reader queues it first: the frame is provably delivered while
            # the pre-user buffer is still installed, making the boundary
            # rejection a matter of pipe order, never of thread timing.
            send(assistant_tool("Read", "toolu_early_1"))
        if case == "premature":
            # Tool output emitted before the controller could send its user
            # message, and written before the initialize response for the same
            # pipe-order reason: a frame sent only after the response could
            # race the controller's own send and deadlock the turn instead of
            # proving the boundary.
            send(assistant_tool("Read", "toolu_early_1"))
        send({"type": "control_response", "response": {"subtype": "success", "request_id": rid,
                                                       "response": {"models": DEFAULT_MODELS,
                                                                    "account": {"apiProvider": "firstParty",
                                                                                "tokenSource": "subscription"}}}})
        if case == "premature":
            for _ in sys.stdin:
                pass
            sys.exit(0)
        return
    if request.get("subtype") == "interrupt":
        state = read_state()
        state["interrupted"] = True
        write_state(state)
        send({"type": "control_response", "response": {"subtype": "success", "request_id": rid, "response": {}}})
        sys.exit(0)
    send({"type": "control_response", "response": {"subtype": "error", "request_id": rid, "error": "unknown"}})


def wait_for_interrupt():
    for raw in sys.stdin:
        try:
            frame = json.loads(raw)
        except ValueError:
            continue
        if frame.get("type") == "control_request":
            handle_control(frame, os.environ.get("BUDDY_CLAUDE_FIXTURE_CASE", "clean"))


def run_stream(case, session_id):
    if case == "unproven":
        # A root tool fact that arrives before any handshake cannot be proven.
        send(assistant_tool("Read", "toolu_early_1"))
    send({"type": "system", "subtype": "init", "session_id": session_id, "cwd": os.getcwd(),
          "model": argv_value("--model") or "", "permissionMode": "default", "tools": []})
    if case == "clean":
        send(assistant_tool("Read", "toolu_1"))
        send(tool_result("toolu_1"))
        send(stream_tool_start("Grep", "toolu_2"))
        send(tool_result("toolu_2"))
        send(block_stop())
    elif case == "replay":
        send(assistant_tool("Read", "toolu_1"))
        send(assistant_tool("Read", "toolu_1"))
        send(stream_tool_start("Read", "toolu_1"))
        send(tool_result("toolu_1"))
    elif case == "conflict":
        send(assistant_tool("Read", "toolu_1"))
        send(tool_result("toolu_1"))
        send(assistant_tool("Grep", "toolu_1"))
        send(tool_result("toolu_1"))
    elif case == "subagent":
        send(assistant_tool("Read", "toolu_1"))
        send(tool_result("toolu_1"))
        send(assistant_tool("Glob", "toolu_sub_1", session=session_id, parent="toolu_parent_9"))
        send(tool_result("toolu_sub_1", session=session_id, parent="toolu_parent_9"))
    elif case == "foreign":
        send(assistant_tool("Read", "toolu_1", session="session-other-stream"))
        send(tool_result("toolu_1", session="session-other-stream"))
        # After a foreign session a frame without its own session is unprovable.
        send(assistant_tool("Read", "toolu_2"))
    elif case == "nameless":
        send(assistant_tool("", "toolu_1", with_name=False))
    elif case == "unknown":
        send(assistant_tool("mcp__server__tool", "toolu_1"))
        send(tool_result("toolu_1"))
        send(assistant_tool("Task", "toolu_2"))
        send(tool_result("toolu_2"))
    elif case == "block-stop":
        send(stream_tool_start("Read", "toolu_1"))
        send(block_stop())
    elif case == "late":
        send(assistant_tool("Read", "toolu_1"))
        send(tool_result("toolu_1"))
        send(result_frame(session_id))
        send(assistant_tool("Grep", "toolu_2"))
        return
    elif case == "overflow":
        for index in range(65):
            call = f"toolu_{index}"
            send(assistant_tool("Read", call))
            send(tool_result(call))
    elif case == "budget":
        send(assistant_tool("Read", "toolu_1"))
        send(tool_result("toolu_1"))
        send(assistant_tool("Grep", "toolu_2"))
        wait_for_interrupt()
        return
    elif case == "structured-clean":
        # A real read, then the built-in delivery and its tool_result pairing.
        structured = result_frame(session_id)["structured_output"]
        send(assistant_tool("Read", "toolu_1"))
        send(tool_result("toolu_1"))
        send(structured_use("toolu_so_1", structured))
        send(tool_result("toolu_so_1"))
    elif case == "structured-only":
        # The delivery alone: a zero tool budget must still accept this turn.
        structured = result_frame(session_id)["structured_output"]
        send(structured_use("toolu_so_1", structured))
        send(tool_result("toolu_so_1"))
    elif case == "structured-dupes":
        # The same delivery projected twice: a stream content_block_start and
        # the assistant full block, one correlated tool_result end.
        structured = result_frame(session_id)["structured_output"]
        send(stream_tool_start("StructuredOutput", "toolu_so_1"))
        send(block_stop())
        send(structured_use("toolu_so_1", structured))
        send(tool_result("toolu_so_1"))
    elif case == "structured-unverified":
        # The bare name without the association: the use input is not the
        # value the result frame carried, so nothing may be exempted.
        send(structured_use("toolu_so_1", {"profileId": "forged",
                                           "reason": "not the delivered value", "evidence": []}))
        send(tool_result("toolu_so_1"))
    elif case in ("structured-conflict-ab", "structured-conflict-ba"):
        # One native call id whose two full projections disagree on the input.
        # Whichever order they arrive in, the conflict refuses the exemption.
        structured = result_frame(session_id)["structured_output"]
        other = {"profileId": "conflict", "reason": "a different complete input", "evidence": []}
        order = (structured, other) if case == "structured-conflict-ab" else (other, structured)
        for value in order:
            send(structured_use("toolu_so_1", value))
        send(tool_result("toolu_so_1"))
    elif case == "structured-evict-conflict":
        # Nine delivery uses fill the input retention bound and evict c1's
        # first full text; c1 then carries a second, different complete input
        # that matches the final value. The remembered conflict must keep all
        # nine calls counted.
        structured = result_frame(session_id)["structured_output"]
        send(structured_use("toolu_so_1", {"answer": "first"}))
        for index in range(2, 10):
            send(structured_use(f"toolu_so_{index}", {"answer": f"different-{index}"}))
        send(structured_use("toolu_so_1", structured))
    elif case == "structured-evict-same":
        # The same shape, but c1's returning input is the same value again: a
        # consistent repeat whose recaptured text re-proves the delivery.
        structured = result_frame(session_id)["structured_output"]
        send(structured_use("toolu_so_1", structured))
        for index in range(2, 10):
            send(structured_use(f"toolu_so_{index}", {"answer": f"different-{index}"}))
        send(structured_use("toolu_so_1", structured))
    elif case == "structured-mcp":
        # A mounted MCP tool that merely resembles the built-in's name.
        send(assistant_tool("mcp__server__StructuredOutput", "toolu_1"))
        send(tool_result("toolu_1"))
    elif case == "structured-subagent":
        # A subagent substream calling the same built-in name: its facts keep
        # the parent-call identity and never inherit the root's exemption.
        structured = result_frame(session_id)["structured_output"]
        send(structured_use("toolu_sub_so", structured, session=session_id, parent="toolu_parent_9"))
        send(tool_result("toolu_sub_so", session=session_id, parent="toolu_parent_9"))
        send(structured_use("toolu_so_1", structured))
        send(tool_result("toolu_so_1"))
    send(result_frame(session_id))
    # A -p CLI exits after its final result; the controller drains to this EOF.
    sys.exit(0)


def main():
    if "--version" in sys.argv:
        print("2.1.282 (Claude stream fixture)")
        return
    case = os.environ.get("BUDDY_CLAUDE_FIXTURE_CASE", "clean")
    state = read_state()
    write_state({"argv": sys.argv, "cwd": os.getcwd(), "initialize": False, "userTurns": 0, "interrupted": False,
                 "runs": state.get("runs", 0) + 1})
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
            run_stream(case, argv_value("--session-id") or "unallocated")


if __name__ == "__main__":
    main()
