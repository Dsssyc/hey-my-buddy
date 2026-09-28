"""One owned Claude Code native process for discovery or a governed coding turn.

The controller runs in its own process group and owns a separate native child
group. It speaks the stdio control protocol (initialize, correlated responses,
``can_use_tool`` denial, interrupt on cancellation), never trusts final text or
its own exit alone, and imports a turn record only from a non-error result whose
``session_id`` equals the UUID Buddy preallocated for the attempt.
"""
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
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .base import ProcessHandle
from .windows_process import owned_popen
from .claude_config import (DEFAULT_EFFORT, DEFAULT_MODEL_ALIAS, TOOL_DENIAL_MESSAGE, account_problem,
                            cli_command, discovery_args, execution_args, native_environment, read_auth_status,
                            sandbox_settings, settings_policy, third_party_overrides, token_source_missing)
from .claude_protocol import (ClaudeProtocolError, Connection, QUOTA_REJECTED_ERROR, QuotaRejected, TurnEvidence,
                              decode_json, model_usage_keys, parse_structured_output, result_quota_denial,
                              total_cost_usd)
from .turn_io import ASSISTANCE_HINTS, canonical_json, input_hash, private_json

HARNESS_PREAMBLE = (
    "This is a governed Buddy root turn executed through Claude Code. Work only inside the allocated "
    "checkout and honor the frozen Host scope. Internal subagents may assist. The completion interface "
    "for this harness is ONLY the supplied structured-output schema: emit {outcome: ...} exactly once as "
    "the final structured result. No buddy_finish_turn tool exists or is required here. A completed "
    "outcome must have request:null. Use assistance or attention, with a request object, only when "
    "actual work or a Host decision remains. Do not create another Buddy goal."
)


def _catalog(initialize: dict, version: str, verify_login=None) -> tuple[dict, dict]:
    """The catalog from initialize only: no user message, no model turn.

    ``verify_login`` is the bounded auth-status fallback for the one eligible
    shape (first-party provider, tokenSource absent or null); any other account
    problem, or a failed readback, is refused with its bounded reason.
    """
    account = initialize.get("account")
    problem = account_problem(account)
    if problem:
        if verify_login is None or not token_source_missing(account):
            raise ClaudeProtocolError("first-party-auth-required", problem)
        auth_problem = verify_login()
        if auth_problem:
            raise ClaudeProtocolError("first-party-auth-required", auth_problem)
    entries = initialize.get("models")
    if not isinstance(entries, list):
        raise ClaudeProtocolError("invalid-catalog", "Claude returned no model list")
    if len(entries) > 200:
        raise ClaudeProtocolError("invalid-catalog", "Claude model list exceeds the catalog bound")
    models, warnings, seen = [], [], set()
    for item in entries:
        if not isinstance(item, dict) or item.get("value") == DEFAULT_MODEL_ALIAS:
            continue
        model_id = item.get("resolvedModel")
        if not isinstance(model_id, str) or not model_id or len(model_id) > 128:
            warnings.append("A model without a resolved identity was omitted")
            continue
        if model_id in seen:
            continue
        seen.add(model_id)
        levels = item.get("supportedEffortLevels")
        if levels is None or levels == []:
            efforts = [DEFAULT_EFFORT]
        elif not isinstance(levels, list) or len(levels) > 16 or any(
                level not in ("low", "medium", "high", "xhigh", "max") for level in levels):
            warnings.append("A model with an invalid effort directory was omitted")
            continue
        else:
            efforts = list(dict.fromkeys(levels))
        models.append({"id": model_id, "name": item.get("displayName") or model_id,
                       "description": item.get("description") or "", "efforts": efforts,
                       "inputModalities": ["text"], "available": True})
    catalog = {"source": "claude-code-initialize", "adapter": "claude", "harnessVersion": version,
               "discoveredAt": datetime.now(timezone.utc).isoformat(),
               "providers": [{"adapter": "claude", "provider": "anthropic",
                              "displayName": "Anthropic Claude (first-party)", "packageName": "claude-code",
                              "packageVersion": version, "models": models}],
               "warnings": list(dict.fromkeys(warnings))}
    return catalog, {"apiProvider": account.get("apiProvider"), "tokenSource": account.get("tokenSource")}


def _activity(control: dict, evidence: TurnEvidence, phase: str, tool: str | None = None):
    if not control.get("activityFile"):
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
               "nativeSessionId": evidence.session_id, "lastNativeActivityAt": now,
               "counts": {"modelTurns": evidence.model_messages, "toolCalls": evidence.tool_calls}}
    if state.get("lastToolActivityAt"):
        payload.update(lastToolActivityAt=state["lastToolActivityAt"], toolName=state["toolName"])
    record = {"version": 1, "taskId": control["taskId"], "attemptId": control["attemptId"],
              "generation": control["generation"], "activity": payload}
    path = Path(control["activityFile"])
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    private_json(temp, record)
    os.replace(temp, path)
    state.update(phase=phase, lastWrite=tick)


def _attention_outcome(denied_requests: list[dict], result_denials: int) -> dict:
    if denied_requests:
        names = ", ".join(dict.fromkeys(item["toolName"] for item in denied_requests))[:200]
        summary = f"Claude requested Host interaction through a native tool permission ({names})"
    else:
        summary = f"Claude denied {result_denials} native tool permission request(s) during the turn"
    return {"disposition": "attention", "summary": summary, "remaining": [], "decisions": [],
            "artifacts": [], "request": {"summary": summary,
            "attempted": "The owned controller denied the correlated native permission request without granting access",
            "neededWork": "Host must review the requested action and choose a permitted continuation",
            "expectedArtifacts": [], "acceptance": "The Host resolves this boundary and starts an authorized continuation"}}


def _interrupt(connection: Connection | None) -> bool:
    if connection is None:
        return False
    try:
        # A fresh short control budget permits a native interrupt after the main deadline.
        connection.cancelled = threading.Event()
        connection.deadline = time.monotonic() + 2
        connection.call({"subtype": "interrupt"})
        return True
    except (ClaudeProtocolError, QuotaRejected, OSError, ValueError):
        # A late quota frame while awaiting interrupt acknowledgement must not
        # escape the cleanup path and suppress the controller's stop receipt.
        return False


def _latest_rejected(rate_limits: dict) -> tuple[str, object]:
    for rate_limit_type in reversed(list(rate_limits)):
        if rate_limits[rate_limit_type].get("status") == "rejected":
            return rate_limit_type, rate_limits[rate_limit_type].get("resetsAt")
    return "unknown", None


def execution_deadline(timeout_seconds) -> float:
    """The one overall native execution deadline; an explicit 0 means unlimited.

    Only this deadline becomes infinite. The version probe and the per-request,
    cancel and shutdown waits keep their own finite bounds, and the ``cancelled``
    event still ends an unlimited turn.
    """
    return math.inf if timeout_seconds == 0 else time.monotonic() + timeout_seconds


def _read_only_call(connection, control, result, catalog, early_messages, user_sent, process):
    """Use native restricted file tools and JSON Schema without a workflow turn."""
    from .read_only import valid_answer
    spec, request, session_id = control["spec"], control["readOnlyRequest"], control["sessionId"]
    if spec.get("provider") != "anthropic" or not any(
            model["id"] == spec.get("model") and spec.get("effort") in model["efforts"]
            for model in catalog["providers"][0]["models"]):
        raise ClaudeProtocolError("invalid-configuration", "Unknown native read-only configuration")
    connection.pump_available()
    result["modelStarted"] = True
    connection.send({"type": "user", "message": {"role": "user",
                    "content": [{"type": "text", "text": request["prompt"]}]}})
    user_sent["value"] = True
    evidence = TurnEvidence(session_id, control["cwd"])
    def observed(frame):
        activity = evidence.observe(frame)
        result["usage"] = {"toolCalls": evidence.tool_calls, "bytesRead": None}
        # A limit of N allows N native tool calls; the next one is interrupted.
        if evidence.tool_calls > request["budget"]["toolCalls"]:
            raise ClaudeProtocolError("readonly-budget-exhausted", "Read-only tool budget exhausted")
        if activity:
            _activity(control, evidence, *activity)
    connection.on_message = observed
    for was_sent, message in early_messages:
        if not was_sent and message.get("type") in ("assistant", "result", "stream_event"):
            raise ClaudeProtocolError("native-turn-started-early", "Model output preceded the read-only request")
        observed(message)
    early_messages.clear()
    while evidence.result is None:
        connection.pump()
    process.stdin.close()
    connection.drain_until_closed(min(10.0, max(0.0, connection.deadline - time.monotonic())))
    native_result = evidence.result
    if (not evidence.init_observed or native_result.get("session_id") != session_id
            or native_result.get("subtype") != "success" or native_result.get("is_error") is not False
            or evidence.unsettled_background_tasks()):
        raise ClaudeProtocolError("native-turn-failed", "No completed native structured answer")
    raw = native_result.get("structured_output")
    result.update(status="ok", rawAnswer=raw, answerValid=valid_answer(raw, request["outputSchema"]),
                  resolved=dict(spec), nativeIdentity={"sessionId": session_id},
                  usage={"toolCalls": evidence.tool_calls, "bytesRead": None})
    return evidence


def _run(control: dict, cancelled: threading.Event) -> tuple[dict, int]:
    directory = Path(control["directory"])
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    native_root = Path(control.get("nativeRoot") or directory)
    native_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    started = time.monotonic()
    deadline = execution_deadline(control["timeoutSeconds"])
    result = {"status": "error", "mode": "claude", "harnessVersion": "unknown",
              "requested": control.get("spec"), "resolved": None, "observed": None, "modelStarted": False}
    record = None
    connection = None
    process = None
    handle = None
    evidence = None
    try:
        incoming = dict(os.environ)
        # Refusals and the policy gate inspect the incoming environment before
        # the allowlisted child environment would drop the offending names.
        overrides = third_party_overrides(incoming)
        if overrides:
            raise ClaudeProtocolError("third-party-provider",
                                      "Claude execution refuses third-party provider overrides: " + ", ".join(overrides))
        if not control.get("discover") and settings_policy(incoming) is None:
            raise ClaudeProtocolError("settings-policy-unsupported",
                                      "Claude P1 supports only BUDDY_CLAUDE_SETTINGS_POLICY=isolated; "
                                      "an explicit unsupported settings policy was supplied")
        command = cli_command(incoming)
        environment = native_environment(incoming)
        discover = bool(control.get("discover"))
        args = discovery_args()
        session_id = None
        if not discover:
            session_id = control.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                raise ClaudeProtocolError("invalid-control", "the attempt has no preallocated native session id")
            try:
                uuid.UUID(session_id, version=4)
            except ValueError:
                raise ClaudeProtocolError("invalid-control", "the preallocated Claude session id is not a UUID") from None
            settings = sandbox_settings()
            if control.get("readOnlyRequest"):
                settings["sandbox"]["network"]["allowedDomains"] = []
            private_json(native_root / "settings.json", settings)
            spec = control["spec"]
            args = execution_args(session_id=session_id, model=spec["model"], effort=spec["effort"],
                                  settings_path=str(native_root / "settings.json"),
                                  read_only=control.get("access") == "read",
                                  output_schema=(control.get("readOnlyRequest") or {}).get("outputSchema"))
        # Version is diagnostic only. Discovery never makes a paid model call.
        try:
            version_result = subprocess.run([*command, "--version"], cwd=control["cwd"], env=environment,
                                            capture_output=True, timeout=5)
            version = version_result.stdout.decode(errors="replace").strip()[:80] if version_result.returncode == 0 else "unknown"
        except (OSError, subprocess.TimeoutExpired):
            version = "unknown"
        result["harnessVersion"] = version
        native_stderr = directory / "native.stderr.log"
        fd = os.open(native_stderr, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            process = owned_popen([*command, *args], cwd=control["cwd"],
                                       env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=fd,
                                       start_new_session=True, close_fds=True)
        finally:
            os.close(fd)
        handle = ProcessHandle(process, own_group=True, log_paths={})
        connection = Connection(process, deadline, cancelled)
        denied_requests: list[dict] = []
        unsupported_requests = 0
        early_messages: list[tuple[bool, dict]] = []
        user_sent = {"value": False}

        def buffer_message(message):
            # Frames are tagged by whether the user message had already been
            # sent when they arrived; model output from before it is rejected.
            if len(early_messages) >= 128:
                raise ClaudeProtocolError("invalid-protocol", "Claude exceeded the initialization frame bound")
            early_messages.append((user_sent["value"], message))
        connection.on_message = buffer_message

        def on_request(frame):
            nonlocal unsupported_requests
            request = frame.get("request") or {}
            if request.get("subtype") == "can_use_tool":
                if len(denied_requests) >= 32:
                    raise ClaudeProtocolError("native-request-limit", "Claude exceeded the native permission request bound")
                tool = request.get("tool_name")
                tool_id = request.get("tool_use_id")
                request_id = frame.get("request_id")
                denied_requests.append({
                    "toolName": tool[:80] if isinstance(tool, str) and tool else "<unknown>",
                    "toolUseId": tool_id[:256] if isinstance(tool_id, str) and tool_id else None,
                    # The control request's own identity: the durable correlation
                    # for a denial when the native frame carried no tool_use_id.
                    "requestId": request_id[:256] if isinstance(request_id, str) and request_id else "<missing>"})
                connection.send({"type": "control_response",
                                 "response": {"subtype": "success", "request_id": frame["request_id"],
                                              "response": {"behavior": "deny", "message": TOOL_DENIAL_MESSAGE}}})
            else:
                unsupported_requests += 1
                connection.send({"type": "control_response",
                                 "response": {"subtype": "error", "request_id": frame["request_id"],
                                              "error": "This Buddy Worker controller does not provide this native callback"}})
        connection.on_request = on_request

        initialize = connection.call({"subtype": "initialize"})
        # A missing/null tokenSource is verified through the SAME resolved
        # executable, allowlisted child environment and cwd as this execution;
        # every other account problem is refused outright.
        catalog, account = _catalog(initialize, version, verify_login=lambda: read_auth_status(
            command, cwd=control["cwd"], environment=environment))
        if discover:
            result.update(status="ok", catalog=catalog, account=account)
        elif control.get("readOnlyRequest"):
            evidence = _read_only_call(connection, control, result, catalog, early_messages, user_sent, process)
        else:
            turn_input = decode_json(Path(control["inputFile"]).read_bytes())
            if not isinstance(turn_input, dict):
                raise ClaudeProtocolError("invalid-input", "the governed turn input is not an object")
            spec = control["spec"]
            if spec.get("provider") != "anthropic" or not any(
                model["id"] == spec.get("model") and spec.get("effort") in model["efforts"]
                for model in catalog["providers"][0]["models"]
            ):
                raise ClaudeProtocolError("invalid-configuration",
                                          "the selected Claude model and effort are not in the current native catalog")
            requested = {key: spec[key] for key in ("provider", "model", "effort")}
            # ``resolved`` restates the requested configuration the runner
            # validated against the native catalog; no applied-effort readback
            # exists in the result frame, so none is ever claimed.
            mode, previous = turn_input.get("resumeMode"), turn_input.get("previousSessionId")
            if mode not in ("initial", "reconstructed-new-session"):
                raise ClaudeProtocolError("invalid-resume-mode",
                                          "Claude P1 supports only initial and reconstructed-new-session turns")
            if mode == "initial" and previous is not None:
                raise ClaudeProtocolError("invalid-resume-mode", "an initial Claude turn must not carry a previous session")
            if mode == "reconstructed-new-session" and session_id == previous:
                raise ClaudeProtocolError("wrong-native-session",
                                          "a reconstructed Claude turn must allocate a fresh session id")
            identity = {key: turn_input[key] for key in ("taskId", "attemptId", "generation", "turnId")}
            # The user-message boundary: anything the CLI already emitted is
            # pre-user output and is rejected when replayed below.
            connection.pump_available()
            prompt = "\n\n".join([HARNESS_PREAMBLE, *ASSISTANCE_HINTS,
                                  Path(control["taskFile"]).read_text(), canonical_json(turn_input)])
            result["modelStarted"] = True
            connection.send({"type": "user", "message": {"role": "user",
                                                         "content": [{"type": "text", "text": prompt}]}})
            user_sent["value"] = True
            evidence = TurnEvidence(session_id, control["cwd"])

            def on_message(frame):
                observed = evidence.observe(frame)
                if observed:
                    phase, tool = observed
                    _activity(control, evidence, phase, tool)
            connection.on_message = on_message
            for was_sent, message in early_messages:
                if not was_sent and message.get("type") in ("assistant", "result", "stream_event"):
                    raise ClaudeProtocolError("native-turn-started-early",
                                              "Claude emitted model output before the governed user message")
                on_message(message)
            early_messages.clear()
            _activity(control, evidence, "waiting-model")
            while evidence.result is None:
                connection.pump()
            # A quiet window cannot prove the stream ended: close the input and
            # keep validating trailing frames until the observed end of stream,
            # so a delayed duplicate, result or quota frame is still rejected.
            try:
                process.stdin.close()
            except OSError:
                pass
            connection.drain_until_closed(min(10.0, max(0.0, deadline - time.monotonic())))
            native_result = evidence.result
            result_denials = native_result.get("permission_denials")
            if isinstance(result_denials, list):
                evidence.permission_denials = min(len(result_denials), 128)
            if not evidence.init_observed:
                raise ClaudeProtocolError("native-init-missing", "Claude did not report the session initialization")
            if native_result.get("session_id") != session_id:
                raise ClaudeProtocolError("wrong-native-session", "Claude returned an unexpected session identity")
            # A terminal result may arrive while reported background agents or
            # workflows still run; that must not be imported as completed work.
            if evidence.unsettled_background_tasks():
                raise ClaudeProtocolError("background-work-unsettled",
                                          "Claude reported unsettled background work at the terminal result")
            subtype = native_result.get("subtype")
            # Success is explicit: subtype "success" with is_error exactly false.
            # Missing fields, wrong types or unknown subtypes are failures (with
            # the structured quota denial classified first), never silent wins.
            if subtype != "success" or native_result.get("is_error") is not False:
                if result_quota_denial(native_result):
                    raise QuotaRejected(*_latest_rejected(evidence.rate_limits))
                raise ClaudeProtocolError("native-turn-failed",
                                          "the Claude native turn did not end with an explicit success result")
            outcome = None
            try:
                outcome = parse_structured_output(native_result.get("structured_output"))
            except (ValueError, TypeError, RecursionError):
                pass
            attention = bool(denied_requests or evidence.permission_denials)
            controller_attention = attention and (outcome is None or outcome["disposition"] == "completed")
            if controller_attention:
                outcome = _attention_outcome(denied_requests, evidence.permission_denials)
            elif outcome is None:
                raise ClaudeProtocolError("invalid-result", "the native root turn has no strict structured final outcome")
            provenance = {"adapter": "claude", "nativeSessionId": session_id,
                          "resultSubtype": subtype if isinstance(subtype, str) and 0 < len(subtype) <= 64 else None,
                          "resultIsError": False, "turnEnd": "completed",
                          "structuredOutputValidated": not controller_attention,
                          "structuredOutputSource": "json-schema",
                          "initObserved": True, "backgroundSettled": True, "eventSeq": evidence.event_seq,
                          "sessionModel": evidence.session_model,
                          "modelUsage": model_usage_keys(native_result),
                          "permissionDenials": evidence.permission_denials}
            if denied_requests:
                provenance["deniedControlRequestIds"] = [item["requestId"] for item in denied_requests]
                provenance["deniedToolUseIds"] = [item["toolUseId"] for item in denied_requests if item["toolUseId"]]
            if controller_attention:
                provenance["controllerAttention"] = True
                provenance["deniedToolNames"] = [item["toolName"] for item in denied_requests[:32]]
            record = {"version": 1, **identity, "inputSha256": input_hash(turn_input),
                      "promptSha256": hashlib.sha256(prompt.encode()).hexdigest(),
                      "sessionId": session_id, "previousSessionId": previous, "resumeMode": mode,
                      "outcome": outcome, "provenance": provenance}
            result.update(status="ok", resolved=requested, sessionId=session_id, observed=None,
                          sessionModel=evidence.session_model,
                          observedModels=model_usage_keys(native_result),
                          totalCostUsd=total_cost_usd(native_result),
                          rateLimitObservations=dict(sorted(evidence.rate_limits.items())),
                          nativeAttention={"requests": len(denied_requests),
                                           "resultDenials": evidence.permission_denials})
            if unsupported_requests:
                result["unsupportedNativeRequests"] = min(unsupported_requests, 128)
    except QuotaRejected as error:
        # Quota exhaustion is infrastructure unavailability: no retry, no outcome, no seal.
        result.update(status="error", code="quota-rejected", error=QUOTA_REJECTED_ERROR,
                      quotaFailure={"rateLimitType": error.rate_limit_type, "resetsAt": error.resets_at})
        record = None
        result["nativeInterruptAcknowledged"] = _interrupt(connection)
    except ClaudeProtocolError as error:
        result.update(status="cancelled" if error.code == "user-cancel" else "error", code=error.code, error=str(error))
        record = None
        if error.code in ("user-cancel", "deadline", "readonly-budget-exhausted"):
            result["nativeInterruptRequested"] = True
            result["nativeInterruptAcknowledged"] = _interrupt(connection)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        result.update(status="error", code="invalid-native-result", error="Claude returned invalid or incomplete native data")
        record = None
    finally:
        if evidence is not None:
            result["rateLimitObservations"] = dict(sorted(evidence.rate_limits.items()))
        if process is not None:
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
        else:
            result["processState"] = {"shutdownConfirmed": False, "nativeExitCode": None}
    if cancelled.is_set() and result["status"] != "ok":
        result.update(status="cancelled", code="user-cancel", error="the Claude execution was cancelled")
    if result["status"] == "ok" and (
            not result.get("processState", {}).get("shutdownConfirmed") or process is None or process.returncode != 0):
        result.update(status="error", code="native-shutdown-failed",
                      error="Claude native process did not exit with confirmed process-group shutdown")
        record = None
    if record is not None:
        private_json(Path(control["outputFile"]), record, exclusive=True)
    if control.get("readOnlyRequest"):
        result.setdefault("usage", {"toolCalls": None, "bytesRead": None})["elapsedMs"] = round((time.monotonic() - started) * 1000)
    return result, 0 if result["status"] == "ok" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", required=True)
    args = parser.parse_args()
    cancelled = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT,
                *((signal.SIGBREAK,) if hasattr(signal, "SIGBREAK") else ())):
        signal.signal(sig, lambda _sig, _frame: cancelled.set())
    try:
        result, code = _run(json.loads(Path(args.control).read_text()), cancelled)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        result, code = {"status": "error", "code": "claude-controller-failed",
                        "error": "Claude controller failed before verified native settlement",
                        "processState": {"shutdownConfirmed": False}}, 1
    sys.stdout.write(canonical_json(result) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
