"""One owned Codex App Server process for discovery or a governed coding turn."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .base import ProcessHandle
from .codex_config import cli_command, native_environment
from .codex_protocol import CodexProtocolError, Connection, OUTCOME_SCHEMA, TurnEvidence, decode_json, parse_outcome
from .turn_io import ASSISTANCE_HINTS, canonical_json, input_hash, private_json


def _catalog(connection: Connection, version: str) -> dict:
    data, cursor, seen = [], None, set()
    while True:
        params = {"limit": 100, "includeHidden": False}
        if cursor:
            params["cursor"] = cursor
        page = connection.call("model/list", params)
        entries = page.get("data")
        if not isinstance(entries, list):
            raise CodexProtocolError("invalid-catalog", "Codex returned no model list")
        data.extend(entries)
        if len(data) > 200:
            raise CodexProtocolError("invalid-catalog", "Codex model list exceeds the catalog bound")
        cursor = page.get("nextCursor")
        if cursor is None:
            break
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise CodexProtocolError("invalid-catalog", "Codex model cursor is invalid")
        seen.add(cursor)
    models, warnings = [], []
    for item in data:
        if not isinstance(item, dict) or item.get("hidden") is True:
            continue
        model_id = item.get("model")
        efforts = [e.get("reasoningEffort") for e in item.get("supportedReasoningEfforts", []) if isinstance(e, dict)]
        efforts = list(dict.fromkeys(e for e in efforts if isinstance(e, str) and e))
        if not isinstance(model_id, str) or not model_id or not efforts:
            warnings.append("A model with no usable identity or reasoning effort was omitted")
            continue
        models.append({"id": model_id, "name": item.get("displayName") or model_id,
                       "description": item.get("description") or "", "efforts": efforts,
                       "inputModalities": item.get("inputModalities") or ["text"], "available": True})
    return {"source": "codex-native-app-server", "adapter": "codex", "harnessVersion": version,
            "discoveredAt": datetime.now(timezone.utc).isoformat(),
            "providers": [{"adapter": "codex", "provider": "openai", "displayName": "OpenAI ChatGPT plan",
                           "packageName": "codex", "packageVersion": version, "models": models}],
            "warnings": list(dict.fromkeys(warnings))}


def _binding_path(root: Path, thread_id: str) -> Path:
    return root / (hashlib.sha256(thread_id.encode()).hexdigest() + ".json")


def _write_binding(root: Path, thread_id: str, binding: dict):
    path = _binding_path(root, thread_id)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    private_json(temp, binding)
    os.replace(temp, path)


def _read_binding(root: Path, thread_id: str) -> dict:
    try:
        value = decode_json(_binding_path(root, thread_id).read_bytes())
        if not isinstance(value, dict):
            raise ValueError("invalid binding")
        return value
    except (OSError, ValueError):
        raise CodexProtocolError("native-resume-unavailable", "the Codex thread has no private goal binding") from None


def _activity(control: dict, evidence: TurnEvidence | None, phase: str, tool: str | None = None):
    if not evidence or not control.get("activityFile"):
        return
    state = control.setdefault("_activityState", {})
    tick = time.monotonic()
    now = datetime.now(timezone.utc).isoformat()
    if tool:
        state["lastToolActivityAt"] = now
        state["toolName"] = tool[:80]
    if state.get("phase") == phase and tick - state.get("lastWrite", 0) < 2:
        return
    payload = {"phase": phase, "observedAt": now, "eventSeq": evidence.event_seq,
               "nativeSessionId": evidence.thread_id, "lastNativeActivityAt": now,
               "counts": {"modelTurns": evidence.model_turns + control.get("_readonlyTurnBase", 0),
                          "toolCalls": evidence.tool_calls + control.get("_readonlyToolBase", 0)}}
    if state.get("lastToolActivityAt"):
        payload.update(lastToolActivityAt=state["lastToolActivityAt"], toolName=state["toolName"])
    record = {"version": 1, "taskId": control["taskId"], "attemptId": control["attemptId"],
              "generation": control["generation"], "activity": payload}
    path = Path(control["activityFile"])
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    private_json(temp, record)
    os.replace(temp, path)
    state.update(phase=phase, lastWrite=tick)


def _attention_outcome(method: str) -> dict:
    summary = f"Codex requested Host interaction through {method}"
    return {"disposition": "attention", "summary": summary, "remaining": [], "decisions": [],
            "artifacts": [], "request": {"summary": summary,
            "attempted": "The owned controller declined the correlated native request without granting access",
            "neededWork": "Host must review the requested action and choose a permitted continuation",
            "expectedArtifacts": [], "acceptance": "The Host resolves this boundary and starts an authorized continuation"}}


def execution_deadline(timeout_seconds) -> float:
    """The one overall native execution deadline; an explicit 0 means unlimited.

    Only this deadline becomes infinite. The version probe and the per-request,
    cancel and shutdown waits keep their own finite bounds, and the ``cancelled``
    event still ends an unlimited turn.
    """
    return math.inf if timeout_seconds == 0 else time.monotonic() + timeout_seconds


def _read_only_call(connection, control, result, catalog):
    """A structured native call with no workflow identity or completion tools."""
    from .read_only import valid_answer
    spec, request = control["spec"], control["readOnlyRequest"]
    if spec.get("provider") != "openai" or not any(
            model["id"] == spec.get("model") and spec.get("effort") in model["efforts"]
            for model in catalog["providers"][0]["models"]):
        raise CodexProtocolError("invalid-configuration", "Unknown native read-only configuration")
    response = connection.call("thread/start", {
        "cwd": control["cwd"], "model": spec["model"], "modelProvider": "openai",
        "approvalPolicy": "never", "sandbox": "read-only", "serviceName": "hey-my-buddy",
        "config": {"web_search": "disabled", "features.apps": False, "features.multi_agent": False},
    })
    thread = response.get("thread") or {}
    thread_id = thread.get("id")
    if not isinstance(thread_id, str) or Path(thread.get("cwd", "")).resolve() != Path(control["cwd"]).resolve():
        raise CodexProtocolError("wrong-native-workspace", "Read-only native checkout differs")
    result.update(sessionId=thread_id, resolved=dict(spec))
    previous_tools = 0
    prompt = request["prompt"]
    for call_index in range(2):
        pending = []
        connection.on_notification = lambda message: pending.append(message)
        control["_readonlyToolBase"] = previous_tools
        control["_readonlyTurnBase"] = call_index
        response = connection.call("turn/start", {
            "threadId": thread_id, "cwd": control["cwd"], "model": spec["model"], "effort": spec["effort"],
            "input": [{"type": "text", "text": prompt}], "approvalPolicy": "never",
            "sandboxPolicy": {"type": "readOnly", "access": {"type": "restricted",
                              "includePlatformDefaults": True, "readableRoots": [control["cwd"]]}},
            "outputSchema": request["outputSchema"],
        })
        turn_id = (response.get("turn") or {}).get("id")
        if not isinstance(turn_id, str):
            raise CodexProtocolError("wrong-native-turn", "No read-only turn identity")
        result["nativeTurnId"] = turn_id
        evidence = TurnEvidence(thread_id, turn_id)
        def observed(message):
            activity = evidence.observe(message)
            result["usage"] = {"toolCalls": previous_tools + evidence.tool_calls, "bytesRead": None}
            # A limit of N allows N native tool calls; the next one is interrupted.
            if previous_tools + evidence.tool_calls > request["budget"]["toolCalls"]:
                raise CodexProtocolError("readonly-budget-exhausted", "Read-only tool budget exhausted")
            if activity:
                _activity(control, evidence, *activity)
        connection.on_notification = observed
        for message in pending:
            observed(message)
        while evidence.completed is None:
            connection.pump()
        item = evidence.final_item
        if not evidence.started or evidence.completed.get("status") != "completed" or not isinstance(item, dict):
            raise CodexProtocolError("native-turn-failed", "No completed native structured answer")
        raw = item.get("text")
        if not valid_answer(raw, request["outputSchema"]):
            # Raw output survives for the caller's semantic boundary classification.
            result.update(status="ok", rawAnswer=raw, answerValid=False)
        else:
            result.update(status="ok", rawAnswer=raw, answerValid=True)
        result["usage"] = {"toolCalls": previous_tools + evidence.tool_calls, "bytesRead": None}
        result["nativeIdentity"] = {"sessionId": thread_id, "turnId": turn_id}
        result["correctionCount"] = call_index
        from .read_only import correction_code
        correction = correction_code(raw, request["outputSchema"])
        if call_index or correction is None:
            break
        previous_tools += evidence.tool_calls
        prompt = request["prompt"] + "\n\nFormat correction: " + correction + ". Return exactly the supplied JSON Schema; do not repeat exploration."


def _run(control: dict, cancelled: threading.Event) -> tuple[dict, int]:
    directory = Path(control["directory"])
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    native_root = Path(control.get("nativeRoot") or directory)
    native_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    deadline = execution_deadline(control["timeoutSeconds"])
    started = time.monotonic()
    environment = native_environment(dict(os.environ))
    if control.get("readOnlyRequest"):
        # Keep user/project tool integrations out of this independent native server.
        old_home = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")
        private_home = native_root / "codex-home"
        private_home.mkdir(mode=0o700, parents=True, exist_ok=True)
        auth = old_home / "auth.json"
        if auth.is_file() and not (private_home / "auth.json").exists():
            (private_home / "auth.json").symlink_to(auth)
        (private_home / "config.toml").write_text('web_search = "disabled"\n[features]\napps = false\nmulti_agent = false\n')
        environment["CODEX_HOME"] = str(private_home)
    command = cli_command(environment)
    # Version is diagnostic only. Discovery never makes a paid model call.
    try:
        version_result = subprocess.run([*command, "--version"], cwd=control["cwd"], env=environment,
                                        capture_output=True, timeout=5)
        version = version_result.stdout.decode(errors="replace").strip()[:80] if version_result.returncode == 0 else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        version = "unknown"
    native_stderr = directory / "native.stderr.log"
    fd = os.open(native_stderr, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        process = subprocess.Popen([*command, "app-server", "--listen", "stdio://"], cwd=control["cwd"],
                                   env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=fd,
                                   start_new_session=True, close_fds=True)
    finally:
        os.close(fd)
    handle = ProcessHandle(process, own_group=True, log_paths={})
    result = {"status": "error", "mode": "codex", "harnessVersion": version,
              "requested": control.get("spec"), "resolved": None, "observed": None}
    record = None
    connection = None
    thread_id = turn_id = None
    evidence = None
    denied_requests = []
    early_notifications = []
    try:
        connection = Connection(process, deadline, cancelled)
        connection.on_notification = lambda message: early_notifications.append(message) if len(early_notifications) < 128 else None
        def on_request(message):
            params = message.get("params")
            method = message.get("method")
            if isinstance(params, dict) and isinstance(method, str) and len(method) <= 80:
                denied_requests.append({"method": method, "threadId": params.get("threadId"), "turnId": params.get("turnId")})
            connection.send({"id": message["id"], "error": {"code": -32601,
                "message": "This Buddy Worker cannot approve interactive requests; report attention in the structured outcome"}})
        connection.on_request = on_request
        connection.call("initialize", {"clientInfo": {"name": "hey_my_buddy", "title": "Hey My Buddy", "version": "0.9.0"}})
        connection.send({"method": "initialized", "params": {}})
        account = connection.call("account/read", {"refreshToken": False}).get("account")
        if not isinstance(account, dict) or account.get("type") != "chatgpt":
            raise CodexProtocolError("account-plan-required", "Codex requires an existing ChatGPT account-plan login")
        catalog = _catalog(connection, version)
        if control.get("discover"):
            result.update(status="ok", catalog=catalog)
        elif control.get("readOnlyRequest"):
            control["_earlyReadOnlyNotifications"] = early_notifications
            _read_only_call(connection, control, result, catalog)
        else:
            turn_input = decode_json(Path(control["inputFile"]).read_bytes())
            if not isinstance(turn_input, dict):
                raise CodexProtocolError("invalid-input", "the governed turn input is not an object")
            spec = control["spec"]
            if spec.get("provider") != "openai" or not any(
                model["id"] == spec.get("model") and spec.get("effort") in model["efforts"]
                for model in catalog["providers"][0]["models"]
            ):
                raise CodexProtocolError("invalid-configuration", "the selected Codex model and effort are not in the current native catalog")
            requested = {key: spec[key] for key in ("provider", "model", "effort")}
            identity = {key: turn_input[key] for key in ("taskId", "attemptId", "generation", "turnId")}
            mode, previous = turn_input.get("resumeMode"), turn_input.get("previousSessionId")
            if mode == "native-session":
                if not isinstance(previous, str) or not previous:
                    raise CodexProtocolError("native-resume-unavailable", "native continuation requires the bound Codex thread")
                binding = _read_binding(native_root, previous)
                expected = {"taskId": identity["taskId"], "threadId": previous,
                            "cwd": control["cwd"], "configuration": requested}
                if {key: binding.get(key) for key in expected} != expected or not isinstance(binding.get("lastTurnId"), str):
                    raise CodexProtocolError("native-resume-unavailable", "the Codex thread binding differs from this goal, checkout or configuration")
                read = connection.call("thread/read", {"threadId": previous, "includeTurns": True}).get("thread")
                turns = read.get("turns") if isinstance(read, dict) else None
                if not isinstance(turns, list) or not turns or turns[-1].get("id") != binding["lastTurnId"] or turns[-1].get("status") != "completed":
                    raise CodexProtocolError("native-resume-unavailable", "the Codex thread has changed since its bound completed turn")
                response = connection.call("thread/resume", {"threadId": previous, "cwd": control["cwd"],
                                                            "model": requested["model"], "modelProvider": "openai",
                                                            "approvalPolicy": "never", "sandbox": "workspace-write"})
            elif mode in ("initial", "reconstructed-new-session") and (mode != "initial" or previous is None):
                response = connection.call("thread/start", {"cwd": control["cwd"], "model": requested["model"],
                                                           "modelProvider": "openai", "approvalPolicy": "never",
                                                           "sandbox": "workspace-write", "serviceName": "hey-my-buddy"})
            else:
                raise CodexProtocolError("invalid-resume-mode", "Codex requires an explicit initial, native or reconstructed turn")
            thread = response.get("thread")
            thread_id = thread.get("id") if isinstance(thread, dict) else None
            if not isinstance(thread_id, str) or not thread_id or mode == "native-session" and thread_id != previous or mode == "reconstructed-new-session" and thread_id == previous:
                raise CodexProtocolError("wrong-native-thread", "Codex returned an unexpected thread identity")
            if not isinstance(thread.get("cwd"), str) or Path(thread["cwd"]).resolve() != Path(control["cwd"]).resolve():
                raise CodexProtocolError("wrong-native-workspace", "Codex thread checkout differs from the allocated workspace")
            if thread.get("modelProvider") not in (None, "openai"):
                raise CodexProtocolError("wrong-native-provider", "Codex thread selected a different provider")
            result["resolved"] = requested
            result["sessionId"] = thread_id
            prompt = "\n\n".join([
                "This is a governed Buddy root turn executed through Codex. Work only inside the allocated checkout and honor the frozen Host scope. Internal Codex subagents may assist. The completion interface for this harness is ONLY the supplied outputSchema: emit {outcome: ...} as the final answer. No buddy_finish_turn tool exists or is required here. A completed outcome must have request:null. Use assistance or attention, with a request object, only when actual work or a Host decision remains. Do not create another Buddy goal.",
                *ASSISTANCE_HINTS,
                Path(control["taskFile"]).read_text(), canonical_json(turn_input),
            ])
            response = connection.call("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": prompt}],
                                                       "cwd": control["cwd"], "model": requested["model"],
                                                       "effort": requested["effort"], "approvalPolicy": "never",
                                                       "sandboxPolicy": {"type": "workspaceWrite", "writableRoots": [control["cwd"]], "networkAccess": False},
                                                       "outputSchema": OUTCOME_SCHEMA})
            turn = response.get("turn")
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if not isinstance(turn_id, str) or not turn_id:
                raise CodexProtocolError("wrong-native-turn", "Codex did not acknowledge a native turn")
            result["nativeTurnId"] = turn_id
            evidence = TurnEvidence(thread_id, turn_id)
            _activity(control, evidence, "waiting-model")
            def on_notification(message):
                observed = evidence.observe(message)
                if observed:
                    phase, tool = observed
                    _activity(control, evidence, phase, tool)
            connection.on_notification = on_notification
            for message in early_notifications:
                on_notification(message)
            early_notifications.clear()
            while evidence.completed is None:
                connection.pump()
            native_turn = evidence.completed
            if evidence.final_item is None and isinstance(native_turn.get("items"), list):
                finals = [item for item in native_turn["items"] if isinstance(item, dict) and
                          item.get("type") == "agentMessage" and item.get("phase") == "final_answer"]
                if len(finals) == 1:
                    evidence.final_item = finals[0]
            if native_turn.get("status") != "completed" or not evidence.started:
                raise CodexProtocolError("native-turn-failed", "Codex turn did not complete successfully")
            correlated = next((request for request in denied_requests
                               if request["threadId"] == thread_id and request["turnId"] == turn_id), None)
            item = evidence.final_item
            outcome = None
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                try:
                    outcome = parse_outcome(item["text"])
                except (ValueError, RecursionError):
                    pass
            controller_attention = correlated is not None and (outcome is None or outcome["disposition"] == "completed")
            if controller_attention:
                outcome = _attention_outcome(correlated["method"])
            elif outcome is None:
                raise CodexProtocolError("invalid-result", "the native root turn has no strict structured final outcome")
            provenance = {"adapter": "codex", "nativeThreadId": thread_id,
                          "nativeTurnId": turn_id, "finalItemId": item.get("id") if isinstance(item, dict) else None,
                          "turnEnd": "completed", "outputSchemaValidated": not controller_attention,
                          "finalMessageCompleted": isinstance(item, dict), "nativeTurnStarted": True,
                          "nativeTurnCompleted": True, "eventSeq": evidence.event_seq}
            if correlated:
                provenance.update(nativeRequestMethod=correlated["method"],
                                  nativeRequestThreadId=correlated["threadId"], nativeRequestTurnId=correlated["turnId"])
            if controller_attention:
                provenance["controllerAttention"] = True
            record = {"version": 1, **identity, "inputSha256": input_hash(turn_input),
                      "promptSha256": hashlib.sha256(prompt.encode()).hexdigest(),
                      "sessionId": thread_id, "previousSessionId": previous, "resumeMode": mode,
                      "outcome": outcome, "provenance": provenance}
            result.update(status="ok", sessionId=thread_id, nativeTurnId=turn_id)
    except CodexProtocolError as error:
        result.update(status="cancelled" if error.code == "user-cancel" else "error", code=error.code, error=str(error))
        record = None
        thread_id = thread_id or result.get("sessionId")
        turn_id = turn_id or result.get("nativeTurnId")
        if connection and thread_id and turn_id and error.code in ("user-cancel", "deadline", "readonly-budget-exhausted"):
            try:
                # A fresh short control budget permits a native interrupt after the main deadline.
                connection.on_notification = lambda message: None
                result["nativeInterruptRequested"] = True
                connection.cancelled = threading.Event()
                connection.deadline = time.monotonic() + 2
                connection.call("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
                result["nativeInterruptAcknowledged"] = True
            except CodexProtocolError:
                pass
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        result.update(status="error", code="invalid-native-result", error="Codex returned invalid or incomplete native data")
        record = None
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        handle.wait(min(3.0, max(0.0, deadline - time.monotonic())))
        if not handle.shutdown_confirmed(settle_seconds=0.2):
            handle.terminate(grace_seconds=1.0)
        shutdown = handle.shutdown_confirmed(settle_seconds=0.5)
        result["processState"] = {"shutdownConfirmed": shutdown, "nativeExitCode": process.returncode}
        process.stdout.close()
    if cancelled.is_set() and result["status"] != "ok":
        result.update(status="cancelled", code="user-cancel", error="the Codex execution was cancelled")
    if result["status"] == "ok" and (not shutdown or process.returncode != 0):
        result.update(status="error", code="native-shutdown-failed", error="Codex App Server did not exit with confirmed process-group shutdown")
        record = None
    if record is not None:
        binding = {"taskId": record["taskId"], "threadId": thread_id, "cwd": control["cwd"],
                   "configuration": result["resolved"], "lastTurnId": turn_id}
        _write_binding(native_root, thread_id, binding)
        private_json(Path(control["outputFile"]), record, exclusive=True)
    if control.get("readOnlyRequest"):
        result.setdefault("usage", {"toolCalls": None, "bytesRead": None})["elapsedMs"] = round((time.monotonic() - started) * 1000)
    return result, 0 if result["status"] == "ok" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", required=True)
    args = parser.parse_args()
    cancelled = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda _sig, _frame: cancelled.set())
    try:
        result, code = _run(json.loads(Path(args.control).read_text()), cancelled)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        result, code = {"status": "error", "code": "codex-controller-failed",
                        "error": "Codex controller failed before verified native settlement",
                        "processState": {"shutdownConfirmed": False}}, 1
    sys.stdout.write(canonical_json(result) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
