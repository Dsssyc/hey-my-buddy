#!/usr/bin/env python3
"""Deterministic native-protocol fixture; calls the real private MCP bridge.

It also implements the documented reverse-RPC interactive requests and the v4
command surface, so the controller's attention handling is exercised with no
model and no network. The ``inquiry-*`` cases drive the cooperative checkpoint
channel: the fixture plays the root model calling the real ``buddy_checkpoint``
and ``buddy_answer_inquiry`` tools, including child-relay, forged-receipt and
finish-refusal choreography.
"""
import json
import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

if "--version" in sys.argv:
    if os.environ.get("BUDDY_ZCODE_TEST_CASE") == "slow-version":
        time.sleep(6)
    print("fixture-0.16.9")
    raise SystemExit(0)

case = os.environ.get("BUDDY_ZCODE_TEST_CASE", "ok")
state = Path(os.environ["ZCODE_SESSION_DB_PATH"]).with_suffix(".fixture.json")
log_dir = Path(os.environ["ZCODE_LOG_DIR"])
instance = hashlib.sha256(os.environ["ZCODE_LOG_DIR"].encode()).hexdigest()[:16]
session_id = "sess-fixture-" + instance
turn_id = "turn-fixture-" + instance
selection = {"providerId": "fixture-api", "modelId": "fixture-model", "options": {"reasoningLevel": "low"}}
workspace = {}
mcp = []
pending_create = None
sequence = 0
child = None
prompt_input_id = None
commands = []
methods = []
completed = False
completion_lock = threading.Lock()


def send(value):
    print(json.dumps(value), flush=True)


def wait_file(name, timeout=20.0):
    """Block until the test creates a coordination file, always bounded."""
    path = log_dir / name
    log_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not path.exists():
        time.sleep(0.02)
    return path.exists()


def record_commands():
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "commands.jsonl").write_text("".join(json.dumps(item) + "\n" for item in commands))
    except OSError:
        pass


def snapshot():
    available = [] if case == "catalog-empty" else [{
        "ref": {"providerId": "fixture-api", "modelId": "fixture-model"}, "label": "Fixture model",
        "providerLabel": "Fixture API", "contextWindow": 200000,
        "reasoning": {"levels": [] if case == "no-effort" else [{"value": "low"}, {"value": "high"}], "defaultLevel": "low"},
        "properties": {"inputFormat": {"supportsText": True}}}]
    return {"session": {"sessionId": session_id, "sessionKind": "interactive", "workspace": workspace},
            "settings": {"model": {"current": selection, "available": available},
                         "thoughtLevel": {"current": selection["options"]["reasoningLevel"]}}}


def event(kind, payload, *, root=None, turn=None):
    global sequence
    sequence += 1
    send({"method": "session/event", "params": {"type": kind, "sessionId": root or session_id, "turnId": turn or turn_id,
          "seq": sequence, "eventId": "event-" + str(sequence), "payload": payload}})


def mcp_call(tool, arguments):
    """Call one tool on the real private MCP bridge and return its raw content."""
    global child
    config = mcp[0]
    env = {**os.environ, **{x["name"]: x["value"] for x in config.get("env", [])}}
    child = subprocess.Popen([config["command"], *config["args"]], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    child.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": tool, "arguments": arguments}}) + "\n")
    child.stdin.flush()
    response = json.loads(child.stdout.readline())["result"]
    child.stdin.close()
    child.wait(timeout=3)
    if response.get("isError"):
        return None, response["content"][0]["text"]
    return response["content"][0]["text"], None


def native_tool_call(tool, arguments, *, call_id=None, root=None, turn=None, relay=None, tamper=None):
    """One root (or relayed child) session tool call with its native events.

    This mirrors what the real app-server emits around an MCP call: a
    ``tool.updated`` scheduled event, the tool response, then a result or error
    event carrying the response content.
    """
    name = "mcp__" + mcp[0]["name"] + "__" + tool
    call_id = call_id or f"call-{tool}-{sequence + 1}"
    payload_scheduled = {"kind": "scheduled", "toolName": name, "toolCallId": call_id}
    if relay:
        payload_scheduled.update(relay)
    event("tool.updated", payload_scheduled, root=root, turn=turn)
    content, error = mcp_call(tool, arguments)
    if content is None:
        event("tool.updated", {"kind": "error", "toolCallId": call_id, "error": error[:400]}, root=root, turn=turn)
        return None, error
    if tamper:
        content = tamper(content)
    payload_result = {"toolCallId": call_id}
    if relay:
        payload_result.update(relay)
    event("tool.updated", {"kind": "result", **payload_result,
                           "result": {"success": True, "truncated": False, "content": content}}, root=root, turn=turn)
    return content, None


def outcome_for(disposition="completed"):
    outcome = {"disposition": disposition, "summary": "fixture work completed", "remaining": [], "decisions": [], "artifacts": [], "request": None}
    if disposition == "assistance":
        outcome.update(summary="fixture asks for help", request={"summary": "help", "attempted": "examined fixture", "neededWork": "review decision", "expectedArtifacts": [], "acceptance": "decision reviewed"})
    if disposition == "attention":
        outcome.update(summary="native approval needed", request={"summary": "native approval needed", "attempted": "requested fixture permission", "neededWork": "Host decision", "expectedArtifacts": [], "acceptance": "decision recorded"})
    return outcome


def finish(disposition="completed"):
    """Call the real session-private finish tool and return its signed receipt."""
    return mcp_call("buddy_finish_turn", outcome_for(disposition))


def checkpoint_and_answer(*, prefix="fixture answer for"):
    """The compliant root flow: pick up queued questions, answer each, then finish."""
    content, error = native_tool_call("buddy_checkpoint", {}, call_id="call-checkpoint-root")
    if content is None:
        return content, error
    for item in json.loads(content)["inquiries"]:
        native_tool_call("buddy_answer_inquiry", {"inquiryId": item["inquiryId"], "answer": f"{prefix} {item['inquiryId']}"},
                         call_id="call-answer-" + item["inquiryId"])
    return content, None


def settle_turn(result_type="success"):
    event("turn.completed", {"inputId": prompt_input_id, "resultType": result_type, "response": "fixture final text is not the outcome"})
    send({"method": "state.updated", "params": {"sessionId": session_id, "reason": "prompt_failed" if result_type != "success" else "prompt_completed"}})


def complete_turn():
    """Emit the single terminal root finish exactly once."""
    global completed
    with completion_lock:
        if completed:
            return
        completed = True
    content, _error = finish("assistance" if case == "assistance" else "completed")
    if content is None and case in ("attention", "live"):
        # The finish tool refuses completed while a native interactive request is
        # refused or a queued Host question is unanswered; a compliant root
        # retries with the softer outcome in the same turn.
        content, _error = finish("assistance" if case == "live" else "attention")
    tool = "mcp__" + mcp[0]["name"] + "__buddy_finish_turn"
    if case == "forged-receipt":
        receipt = json.loads(content)
        receipt["outcome"]["summary"] = "changed after the bridge signed it"
        content = json.dumps(receipt)
    if case == "child-first":
        event("tool.updated", {"kind": "scheduled", "toolName": tool, "toolCallId": "child-call"}, root="sess-child", turn="turn-child")
        event("tool.updated", {"kind": "result", "toolCallId": "child-call", "result": {"success": True, "truncated": False, "content": content}}, root="sess-child", turn="turn-child")
    event_root = "sess-wrong" if case == "wrong-root" else session_id
    event_turn = "turn-wrong" if case == "wrong-turn" else turn_id
    event("tool.updated", {"kind": "scheduled", "toolName": tool, "toolCallId": "root-call", "inputOmitted": True, "inputRef": "model_stream"}, root=event_root, turn=event_turn)
    event("tool.updated", {"kind": "result", "toolCallId": "root-call", "result": {"success": case != "tool-error", "truncated": case == "truncated", "content": content}}, root=event_root, turn=event_turn)
    if case == "duplicate":
        event("tool.updated", {"kind": "scheduled", "toolName": tool, "toolCallId": "second-call"})
    event("turn.completed", {"inputId": prompt_input_id, "resultType": "error_during_execution" if case == "turn-failure" else "success", "response": "fixture final text is not the outcome"})
    send({"method": "state.updated", "params": {"sessionId": session_id, "reason": "prompt_failed" if case == "prompt-failure" else "prompt_completed"}})


def inquiry_turn():
    """Cooperative inquiry choreography after the test released the live turn.

    The test queues its Host question through the bridge socket before creating
    the release file, so by the time this runs the journal holds the committed
    question the root is expected to pick up.
    """
    global completed
    with completion_lock:
        completed = True
    relay = {"source": "subagent", "childSessionId": "sess-child", "childToolCallId": "child-ckpt"}
    if case == "inquiry-live":
        checkpoint_and_answer()
        native_tool_call("buddy_finish_turn", outcome_for("completed"), call_id="call-finish-final")
        settle_turn()
        return
    if case == "inquiry-finish-refused":
        # A completed finish is refused while the question is unanswered; the
        # compliant root then checkpoints, answers and retries the finish.
        native_tool_call("buddy_finish_turn", outcome_for("completed"), call_id="call-finish-first")
        checkpoint_and_answer()
        native_tool_call("buddy_finish_turn", outcome_for("completed"), call_id="call-finish-final")
        settle_turn()
        return
    if case == "inquiry-unanswered":
        # Assistance stays legal with a pending question; settlement then marks
        # the unanswered entry unavailable without waking anything.
        native_tool_call("buddy_checkpoint", {}, call_id="call-checkpoint-root")
        native_tool_call("buddy_finish_turn", outcome_for("assistance"), call_id="call-finish-final")
        settle_turn()
        return
    if case == "inquiry-discarded":
        # The asking side withdrew the question before release, so nothing is
        # pending and a completed finish is accepted.
        native_tool_call("buddy_checkpoint", {}, call_id="call-checkpoint-root")
        native_tool_call("buddy_finish_turn", outcome_for("completed"), call_id="call-finish-final")
        settle_turn()
        return
    if case == "inquiry-late":
        # The finish receipt is already accepted when the Host question arrives;
        # the turn settles and the late question honestly becomes unavailable.
        native_tool_call("buddy_finish_turn", outcome_for("completed"), call_id="call-finish-final")
        (log_dir / "finish-accepted").touch()
        wait_file("late-asked")
        settle_turn()
        return
    if case == "inquiry-child":
        # A child relay tries to observe and answer first; its tool evidence can
        # never count, and the root's own answer is the recorded one.
        native_tool_call("buddy_checkpoint", {}, call_id="child-ckpt", root="sess-child", turn="turn-child", relay=relay)
        native_tool_call("buddy_answer_inquiry", {"inquiryId": "q-child", "answer": "child answer must not count"},
                         call_id="child-answer", root="sess-child", turn="turn-child", relay=relay)
        checkpoint_and_answer()
        native_tool_call("buddy_finish_turn", outcome_for("completed"), call_id="call-finish-final")
        settle_turn()
        return
    if case == "inquiry-discard-race":
        # The answer receipt is minted while the question is still answerable;
        # the Host withdraws it before the native result event arrives, so the
        # controller sees a valid root answer racing a discard and must keep the
        # discarded state, drop the late answer and still finish normally.
        native_tool_call("buddy_checkpoint", {}, call_id="call-checkpoint-root")
        answer_name = "mcp__" + mcp[0]["name"] + "__buddy_answer_inquiry"
        event("tool.updated", {"kind": "scheduled", "toolName": answer_name, "toolCallId": "call-answer-race"})
        receipt, error = mcp_call("buddy_answer_inquiry", {"inquiryId": "q-1", "answer": "late but valid"})
        (log_dir / "answer-receipt.json").write_text(receipt or error or "")
        wait_file("discard-done")
        event("tool.updated", {"kind": "result", "toolCallId": "call-answer-race",
                               "result": {"success": True, "truncated": False, "content": receipt}})
        native_tool_call("buddy_finish_turn", outcome_for("completed"), call_id="call-finish-final")
        settle_turn()
        return
    if case == "inquiry-forged":
        # A native result claims checkpoint success for a receipt whose binding
        # was changed after the bridge signed it; the controller must fail the
        # turn instead of importing the forged delivery.
        def forge(content):
            receipt = json.loads(content)
            receipt["inquiries"] = [{"inquiryId": "q-forged", "question": "never committed here",
                                     "questionSha256": "f" * 64, "state": "queued",
                                     "askedAt": "2026-01-01T00:00:00Z"}]
            return json.dumps(receipt)

        native_tool_call("buddy_checkpoint", {}, call_id="call-checkpoint-forged", tamper=forge)
        return
    if case == "inquiry-live-blocked":
        # The root never checkpoints; the completed finish is refused with the
        # question, and the root honestly settles on assistance instead.
        first = native_tool_call("buddy_finish_turn", outcome_for("completed"), call_id="call-finish-first")
        (log_dir / "finish-refusal.json").write_text(first[1] or "")
        native_tool_call("buddy_finish_turn", outcome_for("assistance"), call_id="call-finish-final")
        settle_turn()
        return
    complete_turn()


def request_client(request_id, method, params):
    """Send one documented reverse request and read the controller's response."""
    send({"id": request_id, "method": method, "params": params})
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("id") == request_id:
            return message
        if "method" in message:
            handle_message(message)
    return None


def interactive_attention():
    """Exercise the documented interactive requests a governed turn cannot grant."""
    permission = request_client("server-perm-1", "interaction/requestPermission",
                                {"sessionId": session_id, "toolCallId": "fixture-tool", "toolName": "Bash"})
    user_input = request_client("server-input-1", "interaction/requestUserInput",
                                {"sessionId": session_id, "interactionId": "fixture-question"})
    unknown = request_client("server-browser-1", "interaction/browserExecute",
                             {"requestId": "fixture-browser", "command": {"kind": "unsupported-fixture"}})
    return {"permission": permission, "userInput": user_input, "unknown": unknown}


def handle_message(message):
    global pending_create, workspace, mcp, session_id, selection, prompt_input_id, sequence
    if "method" not in message:
        if pending_create:
            send({"id": pending_create, "result": snapshot()})
            pending_create = None
        return
    method, params, request_id = message["method"], message.get("params", {}), message["id"]
    methods.append(method)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "methods.jsonl").write_text("".join(item + "\n" for item in methods))
    except OSError:
        pass
    if method == "runtime/capabilities":
        if case == "invalid-json":
            print('{"id":1,"result":{},"result":{}}', flush=True)
            return
        result = {"independentPlanState": True}
    elif method in ("session/create", "session/resume"):
        workspace, mcp = params["workspace"], params.get("mcpServers", [])
        if method == "session/resume":
            if not state.exists() or case == "resume-missing":
                send({"id": request_id, "error": {"code": -32004, "message": "Session not found"}})
                return
            stored = json.loads(state.read_text())
            if stored["sessionId"] != params["sessionId"] or not mcp:
                raise RuntimeError("native resume lost its exact identity or MCP reinjection")
            session_id, selection = stored["sessionId"], stored["selection"]
            if case == "resume-wrong-root":
                session_id = "sess-different-root"
        pending_create = request_id
        send({"id": "server-preferences", "method": "session/requestRuntimePreferences", "params": {"sessionId": session_id, "scope": "runtime-materialization"}})
        return
    elif method == "session/setModel":
        if case != "config-mismatch":
            selection = params["model"]
        result = snapshot()
    elif method == "session/setThoughtLevel":
        if case != "config-mismatch":
            selection["options"]["reasoningLevel"] = params["thoughtLevel"]
        result = snapshot()
    elif method == "session/subscribe":
        result = {"sessionId": session_id, "events": [], "eventSeq": 0}
    elif method == "session/send":
        prompt_input_id = params["inputId"]
        send({"id": request_id, "result": {"sessionId": session_id, "accepted": True, "stateRevision": 1}})
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"sessionId": session_id, "selection": selection}))
        if case == "hang":
            time.sleep(120)
        event("turn.started", {"inputId": "wrong-input" if case == "wrong-input" else params["inputId"]})
        if case in ("turn-failed-quota", "turn-failed-quiet"):
            # The exported native turn.failed shape: error.type plus, for the
            # quota case, error.code and the strict attribution object. The raw
            # message is deliberately secret-shaped so a test can prove the
            # controller never surfaces provider text, keys or URLs.
            error = {"type": "AiSdkModelAdapterError",
                     "message": "stream failed for https://models.example.com/v1 key sk-fixture-never-public"}
            if case == "turn-failed-quota":
                error.update({"code": "model_rate_limited", "retryable": False, "attribution": {
                    "source": "provider", "reason": "rate_limited", "errorPhase": "stream",
                    "exceptionKind": "provider_business", "providerId": "fixture-api",
                    "modelId": "fixture-model", "providerKind": "anthropic", "transport": "sse",
                    "statusCode": 429, "providerErrorCode": "1308", "retryable": False}})
            event("turn.failed", {"error": error, "turnPhase": "stream", "inputId": params["inputId"]})
            return
        if case.startswith("inquiry-"):
            # Stay live until the test releases the turn, bounded so a broken
            # test can never hang the suite. No question may end or restart
            # this turn, and exactly one session/send was admitted.
            if wait_file("release-turn"):
                inquiry_turn()
            else:
                settle_turn("error_during_execution")
            return
        if case in ("live", "live-activity"):
            # Stay live until the test releases the turn, bounded so a broken test
            # can never hang the suite. No question may end or restart this turn.
            release = log_dir / "release-turn"
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline and not release.exists():
                if case == "live-activity":
                    send({"method": "v4/telemetry/event", "params": {
                        "kind": "model-delta", "text": "fixture-private-progress",
                    }})
                time.sleep(0.05)
            complete_turn()
            return
        if case in ("attention", "attention-after-finish"):
            accepted = finish("completed")[0] if case == "attention-after-finish" else None
            answers = interactive_attention()
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                (log_dir / "interaction-responses.json").write_text(json.dumps(answers))
            except OSError:
                pass
            if case == "attention-after-finish":
                # A receipt accepted before the refusal is still refused by the
                # adapter because a completed outcome cannot stand in for the
                # Host attention the refused request requires.
                tool = "mcp__" + mcp[0]["name"] + "__buddy_finish_turn"
                event("tool.updated", {"kind": "scheduled", "toolName": tool, "toolCallId": "root-call"}, root=session_id, turn=turn_id)
                event("tool.updated", {"kind": "result", "toolCallId": "root-call",
                                       "result": {"success": True, "truncated": False, "content": accepted}}, root=session_id, turn=turn_id)
                event("turn.completed", {"inputId": prompt_input_id, "resultType": "success", "response": "completed after a refused native request"})
                send({"method": "state.updated", "params": {"sessionId": session_id, "reason": "prompt_completed"}})
                return
        complete_turn()
        return
    elif method == "v4/command":
        # The controller must never send one: questions are delivered
        # cooperatively through the session's own MCP tools, never injected.
        commands.append({"type": params.get("type"), "payload": params.get("payload"), "commandId": params.get("commandId"),
                         "sessionId": params.get("sessionId"), "clientId": params.get("clientId"), "issuedAt": params.get("issuedAt")})
        record_commands()
        send({"id": request_id, "error": {"code": -32601, "message": "no client command is used by this adapter"}})
        return
    elif method == "session/close":
        result = {"closed": case != "close-failed"}
    else:
        send({"id": request_id, "error": {"code": -32601, "message": "unknown method"}})
        return
    send({"id": request_id, "result": result})
    if case == "blocked-input" and method == "session/subscribe":
        time.sleep(120)


for line in sys.stdin:
    handle_message(json.loads(line))
