#!/usr/bin/env python3
"""Private probe: can a ZCode app-server child ask its Host a question and resume?

This is the P1 experiment for native agent-to-Host clarification. The production
``NativeConnection.pump`` answers ``interaction/requestUserInput`` with a fixed
decline. This probe runs the same NDJSON transport with one change: the reverse
request is captured and answered with ``{"action": "accept", "content":
{"answers": {...}}}`` carrying a fixed harmless nonce, so a live child can be
observed resuming the SAME session and turn with the nonce in its output.

Protocol facts verified from the installed bundle (zcode.cjs 0.16.9; byte offsets
in the minified file are recorded in the acceptance document):

* The app-server sends ``{"id": "server-N", "method": "interaction/requestUserInput",
  "params": {input, prompt, questions, requestId, schema, sessionId, toolCallId,
  toolName, turnId, origin?}}`` as a JSON-RPC reverse request and re-sends it with a
  NEW id roughly every second (doubling backoff) until any one frame is answered.
* The host reply result must satisfy the strict schema
  ``{action: "accept"|"decline"|"cancel", content?: object, reason?: string}``;
  an unknown member is a schema failure that rejects the pending native promise.
* ``accept`` merges ``content.answers`` (keyed by exact question text), or
  ``content.answer`` for a single question, into the tool input as a permission
  "modify" decision; the AskUserQuestion tool then completes inside the SAME turn
  and its formatted result tells the model the chosen answer texts.
* The tool call is bounded by a native 30s execution timeout; with
  ``askUserQuestionAutoResolutionEnabled: false`` no native auto-resolution
  interferes, so an unanswered question fails as a cancelled tool call.

No production adapter file is modified. The probe keeps every artifact inside one
private root (default: the git-ignored ``tmp`` area), clears inherited
Buddy runtime/worker variables, snapshots provider files read-only, and closes the
owned child process group before exiting.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import secrets
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from buddy.adapters.turn_io import canonical_json  # noqa: E402
from buddy.adapters.zcode_config import cli_command, snapshot_provider_files  # noqa: E402
from buddy.adapters.zcode_protocol import NativeConnection, NativeError  # noqa: E402

#: Inherited runtime/worker/agent variables that must never reach a probe child,
#: matching the AGENTS.md test-subprocess rule (plus every other BUDDY_* pin).
CLEAR_ENV_PREFIXES = ("BUDDY_", "ZCODE_")
CLEAR_ENV_VARS = ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
API_KEY_ACCESS = {"api-key", "zhipu-coding-plan-api-key"}

MAX_EVENT_DUMP_BYTES = 32768
MAX_CAPTURED_EVENTS = 400
DEFAULT_WALL_SECONDS = 300.0

QUESTION_TEXT = "Which release codename should the probe echo?"
QUESTION_HEADER = "Codename"
PROBE_PROMPT = "\n\n".join([
    "You are a private protocol probe agent. This is not real user work; do not create, edit or delete any file.",
    "Follow these steps exactly and use no other tool:",
    '1. Call the AskUserQuestion tool exactly once with a single question: question: "'
    + QUESTION_TEXT + '" header: "' + QUESTION_HEADER
    + '" multiSelect: false, and exactly two options labeled "amber" and "birch", each with a one-line description.',
    "Do not answer the question yourself and do not skip it: you must wait for the user's answer.",
    '2. When the tool returns, end your turn with one short final line that starts with "PROBE-ECHO:" followed by the '
    "exact answer text you received, verbatim, on the same line. No other output, no other tool.",
])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ProbeConnection(NativeConnection):
    """The production transport with one routed difference: user input is answered.

    ``pump`` keeps the production framing, runtime-preference answer (with
    ``askUserQuestionAutoResolutionEnabled: false`` so the child truly waits for
    this Host), permission denial and unknown-method refusal. Only
    ``interaction/requestUserInput`` is delegated to ``on_user_input``.
    """

    def __init__(self, process, deadline, cancelled, on_user_input):
        self.on_user_input = on_user_input
        super().__init__(process, deadline, cancelled)

    def pump(self) -> None:
        if self.cancelled.is_set():
            raise NativeError("cancelled", "the owned ZCode execution was cancelled")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise NativeError("timeout", "the owned ZCode execution exceeded its deadline")
        try:
            message = self.messages.get(timeout=min(remaining, 0.2))
        except queue.Empty:
            return
        if message is None:
            raise NativeError("native-disconnected", "the native app server closed before settlement")
        if isinstance(message, NativeError):
            raise message
        self.ordinal += 1
        if "id" in message and "method" in message:
            if message["method"] == "session/requestRuntimePreferences":
                self.send({"id": message["id"], "result": {
                    "nativeSearchEnhancementsEnabled": True, "memoryEnabled": False,
                    "askUserQuestionAutoResolutionEnabled": False, "modelContextBudgetStrategy": "preflight-v1",
                }})
                return
            if message["method"] == "interaction/requestUserInput":
                self.send({"id": message["id"], "result": self.on_user_input(message)})
                return
            if message["method"] == "interaction/requestPermission":
                self.send({"id": message["id"], "result": {
                    "decision": "deny",
                    "reason": "The probe Host denies unrelated permissions; only the question tool is in scope.",
                }})
                return
            self.send({"id": message["id"], "error": {"code": -32601, "message": "unsupported by this probe"}})
            return
        if "id" in message:
            if len(self.responses) >= 16:
                raise NativeError("invalid-protocol", "too many unclaimed native responses")
            self.responses[message["id"]] = message
        else:
            self.observe(message, self.ordinal)


def accept_answer(questions: list[dict], nonce: str) -> dict:
    """Build the strict accept result for the captured question set.

    ``content.answers`` is keyed by the exact question text, which the bundle's
    merge reads first (``content.answers[questionText] ?? content["answer_" + i]
    ?? single-question content.answer``).
    """
    answers = {q["question"]: nonce for q in questions if q.get("question")}
    if not answers:
        return {"action": "decline", "reason": "the reverse request carried no usable question text"}
    return {"action": "accept", "content": {"answers": answers}}


def validate_identity(params: object, session_id: str, live_turn: str | None) -> tuple[list[dict] | None, str | None]:
    """Return (captured questions, None) or (None, refusal reason) for one reverse request.

    Malformed frames and frames bound to another session or turn are declined,
    never accepted: the native schema lets the Host answer decline, and the
    bundle turns that into a denied tool call instead of resumed input.
    """
    if not isinstance(params, dict):
        return None, "params is not an object"
    questions = params.get("questions")
    if not isinstance(questions, list) or not (1 <= len(questions) <= 4):
        return None, "questions must be a list of one to four entries"
    for question in questions:
        if not isinstance(question, dict) or not isinstance(question.get("question"), str) or not question["question"]:
            return None, "a question entry has no question text"
        options = question.get("options")
        if not isinstance(options, list) or not (2 <= len(options) <= 4):
            return None, "a question entry has an invalid option list"
    if params.get("sessionId") != session_id:
        return None, "the reverse request names a different session"
    if live_turn is not None and isinstance(params.get("turnId"), str) and params["turnId"] != live_turn:
        return None, "the reverse request names a different turn"
    if not isinstance(params.get("toolCallId"), str) or not params["toolCallId"]:
        return None, "the reverse request has no tool call identity"
    if not isinstance(params.get("requestId"), str) or not params["requestId"]:
        return None, "the reverse request has no request identity"
    captured = [{
        "question": q["question"], "header": q.get("header"), "multiSelect": q.get("multiSelect"),
        "options": [{"label": o.get("label"), "value": o.get("value")} for o in q.get("options", []) if isinstance(o, dict)],
    } for q in questions]
    return captured, None


def bounded_dump(value: object, limit: int = MAX_EVENT_DUMP_BYTES) -> object:
    try:
        raw = canonical_json(value)
    except (TypeError, ValueError, RecursionError):
        return None
    return value if len(raw.encode()) <= limit else {"truncated": True, "bytes": len(raw.encode())}


class EventLog:
    """Bounded metadata-first event log; prose is kept only where the nonce must be provable."""

    def __init__(self):
        self.events: list[dict] = []
        self.dropped = 0
        self.turn_started: list[dict] = []
        self.turn_completed: list[dict] = []
        self.tool_events: list[dict] = []
        self.user_input_events: list[dict] = []
        self.prose: list[dict] = []

    def note(self, message: dict, ordinal: int) -> None:
        params = message.get("params") if isinstance(message, dict) else None
        method = message.get("method") if isinstance(message, dict) else None
        if not isinstance(params, dict):
            return
        if method == "state.updated":
            self._append({"ordinal": ordinal, "kind": f"state:{params.get('reason')}", "sessionId": params.get("sessionId")})
            return
        if method != "session/event":
            return
        kind = params.get("type")
        record = {"ordinal": ordinal, "kind": kind, "seq": params.get("seq"),
                  "sessionId": params.get("sessionId"), "turnId": params.get("turnId")}
        payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
        self._append(record)
        if kind == "turn.started":
            self.turn_started.append({**record, "inputId": payload.get("inputId")})
        elif kind == "turn.completed":
            self.turn_completed.append({**record, "failed": False, "payload": bounded_dump(payload)})
            self._keep_prose(record, payload, ("response",))
        elif kind == "turn.failed":
            self.turn_completed.append({**record, "failed": True, "payload": bounded_dump(payload)})
            self._keep_prose(record, payload, ("error", "message"))
        elif kind == "tool.updated":
            self.tool_events.append({**record, "toolCallId": payload.get("toolCallId"),
                                     "toolName": payload.get("toolName"), "toolKind": payload.get("kind")})
        elif kind in ("userInput.requested", "userInput.resolved", "permission.requested", "permission.resolved"):
            self.user_input_events.append({**record, "payload": bounded_dump(payload, 4096)})

    def _append(self, record: dict) -> None:
        if len(self.events) < MAX_CAPTURED_EVENTS:
            self.events.append(record)
        else:
            self.dropped += 1

    def _keep_prose(self, record: dict, payload: dict, keys: tuple[str, ...]) -> None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                self.prose.append({"seq": record.get("seq"), "turnId": record.get("turnId"),
                                   "source": key, "text": value[:4000]})
                del self.prose[:-12]

    def nonce_seen(self, nonce: str) -> bool:
        if any(nonce in entry["text"] for entry in self.prose):
            return True
        for completed in self.turn_completed:
            payload = completed.get("payload")
            if isinstance(payload, dict):
                try:
                    if nonce in canonical_json(payload):
                        return True
                except (TypeError, ValueError):
                    continue
        return False


def isolated_environment(root: Path, *, zcode_cli: str | None = None, node: str | None = None,
                          builtin_provider: str | None = None, personal_provider: str | None = None) -> tuple[dict, dict]:
    """One private environment; inherited Buddy and native pins never reach the child.

    Inherited ``ZCODE_*`` variables are stripped too: this probe may itself run
    inside a Buddy harness whose variables point at that attempt's private native
    root, and the probe must resolve the real user provider files instead. The
    explicit arguments are the only deliberate re-pins: a test fake CLI, a node
    interpreter or fixture provider files must survive the strip to be usable.
    """
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith(CLEAR_ENV_PREFIXES) and k not in CLEAR_ENV_VARS}
    if zcode_cli:
        environment["BUDDY_ZCODE_CLI"] = zcode_cli
    if node:
        environment["BUDDY_NODE"] = node
    if builtin_provider:
        environment["ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"] = builtin_provider
    if personal_provider:
        environment["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"] = personal_provider
    native_root = root / "native"
    (native_root / "storage").mkdir(parents=True, exist_ok=True)
    (root / "native-logs").mkdir(parents=True, exist_ok=True)
    environment, access = snapshot_provider_files(root, environment)
    environment.update({"ZCODE_STORAGE_DIR": str(native_root / "storage"),
                        "ZCODE_SESSION_DB_PATH": str(native_root / "sessions.sqlite"),
                        "ZCODE_LOG_DIR": str(root / "native-logs"),
                        "ZCODE_LOG_CONSOLE": "0", "ZCODE_MODEL_TELEMETRY_ENABLED": "0"})
    return environment, access


def native_version(command: list[str], environment: dict, cwd: str) -> str:
    try:
        result = subprocess.run([*command, "--version"], env=environment, cwd=cwd,
                                capture_output=True, timeout=8)
        return result.stdout.decode(errors="replace").strip()[:80] if result.returncode == 0 else "unknown"
    except (subprocess.TimeoutExpired, OSError):
        return "unknown"


def stop_process(process: subprocess.Popen) -> None:
    try:
        process.stdin.close()
    except OSError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
    try:
        process.stdout.close()
    except OSError:
        pass


def run_probe(root: Path, *, cwd: Path, provider: str | None, model: str | None, effort: str | None,
              nonce: str, wall_seconds: float, zcode_cli: str | None = None,
              node: str | None = None, builtin_provider: str | None = None,
              personal_provider: str | None = None) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    cwd.mkdir(parents=True, exist_ok=True)
    environment, access = isolated_environment(root, zcode_cli=zcode_cli, node=node,
                                                builtin_provider=builtin_provider,
                                                personal_provider=personal_provider)
    command = cli_command(environment)
    version = native_version(command, environment, str(cwd))
    report: dict = {"version": 1, "at": _now(), "nonce": nonce, "harnessVersion": version,
                    "cliCommand": [part if "zcode.cjs" not in part else "…/zcode.cjs" for part in command],
                    "cwd": str(cwd), "phases": [], "status": "error"}
    cancelled = threading.Event()
    stderr_fd = os.open(root / "native.stderr.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        process = subprocess.Popen([*command, "app-server", "--cwd", str(cwd)], env=environment, cwd=str(cwd),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr_fd,
                                   start_new_session=True, close_fds=True)
    finally:
        os.close(stderr_fd)
    deadline = time.monotonic() + wall_seconds
    log = EventLog()
    captured: dict[str, dict] = {}
    refused: list[dict] = []
    frame_replies = {"accept": 0, "decline": 0}
    session_id: str | None = None
    input_id: str | None = None
    input_sha: str | None = None

    def phase(name: str, **fields) -> None:
        report["phases"].append({"at": _now(), "name": name, **fields})

    def handle_user_input(message: dict) -> dict:
        params = message.get("params")
        turns = [entry.get("turnId") for entry in log.turn_started if isinstance(entry.get("turnId"), str)]
        captured_questions, reason = validate_identity(params, session_id or "", turns[-1] if turns else None)
        frame_id = message.get("id")
        if captured_questions is None:
            frame_replies["decline"] += 1
            refused.append({"at": _now(), "frameId": frame_id, "reason": reason,
                            "params": bounded_dump(params, 4096)})
            return {"action": "decline", "reason": f"probe refusal: {reason}"}
        request_id = params["requestId"]
        existing = captured.get(request_id)
        if existing is None:
            captured[request_id] = {
                "firstSeenAt": _now(), "frameId": frame_id, "requestId": request_id,
                "sessionId": params["sessionId"], "turnId": params.get("turnId"),
                "toolCallId": params["toolCallId"], "toolName": params.get("toolName"),
                "prompt": params.get("prompt") if isinstance(params.get("prompt"), str) else None,
                "schema": bounded_dump(params.get("schema"), 2048), "origin": params.get("origin"),
                "questions": captured_questions,
                "input": bounded_dump(params.get("input"), 8192),
            }
        else:
            existing.setdefault("reframeIds", []).append(frame_id)
        frame_replies["accept"] += 1
        return accept_answer(captured_questions, nonce)

    try:
        connection = ProbeConnection(process, deadline, cancelled, on_user_input=handle_user_input)
        connection.observe = log.note
        connection.call("runtime/capabilities", {})
        phase("capabilities")
        snapshot = connection.call("session/create", {"workspace": {"workspacePath": str(cwd), "workspaceKey": str(cwd)},
                                                      "mode": "yolo", "titleGenerationEnabled": False})
        session_id = (snapshot.get("session") or {}).get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise NativeError("no-session", "the app server created no session")
        report["sessionId"] = session_id
        phase("session-created")
        current = (snapshot.get("settings") or {}).get("model", {}).get("current") or {}
        chosen = {"provider": provider or current.get("providerId"), "model": model or current.get("modelId"),
                  "effort": effort or (current.get("options") or {}).get("reasoningLevel")}
        if any(not isinstance(value, str) or not value.strip() for value in chosen.values()):
            raise NativeError("no-model", f"no complete provider/model/effort available (current={current})")
        if access.get(chosen["provider"]) not in API_KEY_ACCESS:
            raise NativeError("unsupported-provider",
                              f"provider access for {chosen['provider']!r} is not API-key based")
        available = (snapshot.get("settings") or {}).get("model", {}).get("available", [])
        choice = next((x for x in available if (x.get("ref") or {}) ==
                       {"providerId": chosen["provider"], "modelId": chosen["model"]}), None)
        if choice is not None:
            levels = [x.get("value") for x in (choice.get("reasoning") or {}).get("levels", []) if isinstance(x, dict)]
            if chosen["effort"] not in levels and levels:
                chosen["effort"] = levels[0]
        target = {"providerId": chosen["provider"], "modelId": chosen["model"],
                  "options": {"reasoningLevel": chosen["effort"]}}
        snapshot = connection.call("session/setModel", {"sessionId": session_id, "model": target,
                                                        "persistAsWorkspaceLastUsed": False})
        snapshot = connection.call("session/setThoughtLevel", {"sessionId": session_id,
                                                               "thoughtLevel": chosen["effort"],
                                                               "persistAsWorkspaceLastUsed": False})
        actual = (snapshot.get("settings") or {}).get("model", {}).get("current") or {}
        report["resolved"] = {"provider": actual.get("providerId"), "model": actual.get("modelId"),
                              "effort": (actual.get("options") or {}).get("reasoningLevel")}
        phase("model-configured", requested=chosen, resolved=report["resolved"])
        connection.call("session/subscribe", {"sessionId": session_id, "deliveryKind": "web-remote-replayable",
                                              "includeSnapshot": False})
        input_id = "probe-" + hashlib.sha256(nonce.encode()).hexdigest()[:16]
        input_sha = hashlib.sha256(PROBE_PROMPT.encode()).hexdigest()
        accepted = connection.call("session/send", {"sessionId": session_id, "inputId": input_id,
                                                    "content": PROBE_PROMPT})
        if accepted.get("accepted") is not True:
            raise NativeError("send-rejected", "the app server did not admit the probe prompt")
        phase("prompt-sent", inputId=input_id)
        settled = False
        while not settled and time.monotonic() < deadline and process.poll() is None:
            connection.pump()
            if log.turn_completed and any(entry.get("kind") == "state:prompt_completed" for entry in log.events):
                settled = True
        if not settled:
            raise NativeError("no-settlement", "the probe turn never settled inside the wall bound")
        closed = connection.call("session/close", {"sessionId": session_id})
        if closed.get("closed") is not True:
            raise NativeError("close-unconfirmed", "the app server did not confirm session close")
        phase("session-closed")
        report["status"] = "ok"
    except NativeError as error:
        report.update(status="error", code=error.code, error=str(error))
    except (OSError, ValueError, TypeError, KeyError) as error:
        report.update(status="error", code="probe-failure", error=f"{error.__class__.__name__}: {error}")
    finally:
        stop_process(process)
        report["processExitCode"] = process.returncode

    report.update({
        "promptSha256": input_sha,
        "capturedUserInput": list(captured.values()),
        "refusedFrames": refused,
        "frameReplies": frame_replies,
        "events": {"dropped": log.dropped, "total": len(log.events),
                   "kinds": sorted({entry["kind"] for entry in log.events})},
        "turnStarted": log.turn_started,
        "turnCompleted": log.turn_completed,
        "toolEvents": log.tool_events,
        "userInputEvents": log.user_input_events,
        "prose": log.prose,
    })
    report["checks"] = verify_checks(report, log, captured, session_id, input_id, nonce)
    return report


def verify_checks(report: dict, log: EventLog, captured: dict, session_id: str | None,
                  input_id: str | None, nonce: str) -> dict:
    """Derive the experiment verdicts from captured evidence only."""
    record = next(iter(captured.values())) if captured else None
    starts = [entry for entry in log.turn_started if input_id is None or entry.get("inputId") == input_id]
    completed = [entry for entry in log.turn_completed if not entry.get("failed")]
    question_induced = bool(record) and len(record["questions"]) == 1 \
        and all(len(question["options"]) >= 2 for question in record["questions"])
    return {
        "questionInduced": question_induced,
        "capturedIdentity": ({k: record[k] for k in ("requestId", "sessionId", "turnId", "toolCallId", "toolName")}
                             if record else None),
        "sameSession": bool(record) and record["sessionId"] == session_id,
        "singleTurnResumed": (bool(record) and len(starts) == 1 and starts[0].get("turnId") == record["turnId"]
                              and bool(record["turnId"])),
        "nonceInOutput": log.nonce_seen(nonce),
        "turnCompleted": bool(completed),
        "turnCompletedSameTurn": (bool(record) and bool(completed)
                                  and completed[0].get("turnId") == record["turnId"]),
        "promptCompleted": any(entry.get("kind") == "state:prompt_completed" for entry in log.events),
        "sessionClosed": any(phase["name"] == "session-closed" for phase in report["phases"]),
        "processExitedZero": report.get("processExitCode") == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="private ZCode requestUserInput probe")
    parser.add_argument("--root", type=Path, default=None, help="private probe root (default: tmp/…)")
    parser.add_argument("--cwd", type=Path, default=None, help="private working directory for the child")
    parser.add_argument("--provider"), parser.add_argument("--model"), parser.add_argument("--effort")
    parser.add_argument("--nonce", default="probe-nonce-" + secrets.token_hex(6))
    parser.add_argument("--wall-seconds", type=float, default=DEFAULT_WALL_SECONDS)
    parser.add_argument("--zcode-cli", default=None, help="explicit CLI path (test/inspection only)")
    parser.add_argument("--node", default=None, help="explicit node interpreter for a .cjs CLI")
    args = parser.parse_args()
    # Absolute paths only: the child receives --cwd verbatim after its own chdir,
    # so a relative value would be resolved twice and point outside the probe root.
    root = (args.root or (REPO_ROOT / "tmp" / "zcode-request-input-probe" /
                          datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))).expanduser().resolve()
    cwd = (args.cwd or (root / "scratch")).expanduser().resolve()
    report = run_probe(root, cwd=cwd, provider=args.provider, model=args.model, effort=args.effort,
                       nonce=args.nonce, wall_seconds=args.wall_seconds,
                       zcode_cli=args.zcode_cli, node=args.node)
    report_path = root / "probe-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    os.chmod(report_path, 0o600)
    print(canonical_json({
        "status": report["status"], "root": str(root), "report": str(report_path),
        "checks": report.get("checks"), "code": report.get("code"), "error": report.get("error"),
    }))
    required = ("questionInduced", "sameSession", "singleTurnResumed", "nonceInOutput", "turnCompleted",
                "turnCompletedSameTurn", "sessionClosed", "processExitedZero")
    return 0 if report["status"] == "ok" and all(report.get("checks", {}).get(key) for key in required) else 1


if __name__ == "__main__":
    raise SystemExit(main())
