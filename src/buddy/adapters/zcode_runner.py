"""Small stdlib controller for one governed ZCode native app-server turn."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .base import ProcessHandle
from .turn_io import canonical_json, input_hash, private_json
from .zcode_config import SUPPORTED_ACCESS, cli_command, snapshot_provider_files
from .zcode_protocol import NativeConnection, NativeError, RootTurnEvidence, decode_json


def selected(snapshot: dict) -> dict:
    value = snapshot.get("settings", {}).get("model", {}).get("current")
    if not isinstance(value, dict) or not value.get("providerId") or not value.get("modelId"):
        raise NativeError("configuration-unavailable", "ZCode has no usable native model selection")
    return {"provider": value["providerId"], "model": value["modelId"],
            "effort": value.get("options", {}).get("reasoningLevel")}


def configure_session(connection: NativeConnection, snapshot: dict, spec: dict, access: dict) -> dict:
    session_id = snapshot["session"]["sessionId"]
    if any(not isinstance(spec.get(k), str) or not spec[k].strip() for k in ("provider", "model", "effort")):
        raise NativeError("invalid-configuration", "ZCode coding requires a complete provider, model and effort after routing")
    provider, model, effort = spec["provider"], spec["model"], spec["effort"]
    if access.get(provider) not in SUPPORTED_ACCESS:
        raise NativeError("unsupported-provider", "this ZCode adapter supports API-key providers; OAuth account providers require a native authentication host")
    choices = snapshot.get("settings", {}).get("model", {}).get("available", [])
    choice = next((x for x in choices if x.get("ref") == {"providerId": provider, "modelId": model}), None)
    if choice is None:
        raise NativeError("configuration-unavailable", "the requested ZCode provider/model is not in the native available catalog")
    reasoning = choice.get("reasoning", {})
    efforts = [x["value"] for x in reasoning.get("levels", []) if isinstance(x, dict) and isinstance(x.get("value"), str)]
    if effort not in efforts:
        raise NativeError("invalid-configuration", "the requested reasoning effort is not supported by this native model")
    target = {"providerId": provider, "modelId": model, "options": {"reasoningLevel": effort}}
    snapshot = connection.call("session/setModel", {"sessionId": session_id, "model": target, "persistAsWorkspaceLastUsed": False})
    snapshot = connection.call("session/setThoughtLevel", {"sessionId": session_id, "thoughtLevel": effort, "persistAsWorkspaceLastUsed": False})
    actual = selected(snapshot)
    expected = {"provider": provider, "model": model, "effort": effort}
    if actual != expected or snapshot.get("settings", {}).get("thoughtLevel", {}).get("current") != effort:
        raise NativeError("configuration-mismatch", "native ZCode did not retain the requested model and reasoning settings")
    return actual


def catalog(snapshot: dict, access: dict, version: str) -> dict:
    providers: dict[str, dict] = {}
    missing_effort = False
    for model in snapshot.get("settings", {}).get("model", {}).get("available", []):
        ref = model.get("ref") or {}
        provider = ref.get("providerId")
        if access.get(provider) not in SUPPORTED_ACCESS or not ref.get("modelId"):
            continue
        efforts = [x["value"] for x in model.get("reasoning", {}).get("levels", [])
                   if isinstance(x, dict) and isinstance(x.get("value"), str) and x["value"].strip()]
        if not efforts:
            missing_effort = True
            continue
        entry = providers.setdefault(provider, {"provider": provider, "displayName": model.get("providerLabel") or provider,
                                                 "adapter": "zcode", "packageVersion": version, "accessType": access[provider], "models": []})
        entry["models"].append({"id": ref["modelId"], "name": model.get("label") or ref["modelId"],
                                "efforts": efforts,
                                "contextWindow": model.get("contextWindow"), "available": True,
                                "inputModalities": [name for name in ("text", "image", "audio", "video", "pdf")
                                                    if model.get("properties", {}).get("inputFormat", {}).get("supports" + name.capitalize()) is True]})
    warnings = ["OAuth account providers are unavailable through this adapter"] if any(t == "zhipu-account" for t in access.values()) else []
    if missing_effort:
        warnings.append("Models exposing no configurable native reasoning efforts were omitted")
    return {"source": "zcode-native-app-server", "adapter": "zcode", "harnessVersion": version,
            "discoveredAt": datetime.now(timezone.utc).isoformat(), "providers": list(providers.values()),
            "warnings": warnings}


def run(control: dict, cancelled: threading.Event) -> tuple[dict, int]:
    deadline = time.monotonic() + control["timeoutSeconds"]
    directory = Path(control["directory"])
    root = Path(control["nativeRoot"])
    for path in (directory, root):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    environment, access = snapshot_provider_files(directory, dict(os.environ))
    environment.update({"ZCODE_STORAGE_DIR": str(root / "storage"), "ZCODE_SESSION_DB_PATH": str(root / "sessions.sqlite"),
                        "ZCODE_LOG_DIR": str(directory / "native-logs"), "ZCODE_LOG_CONSOLE": "0",
                        "ZCODE_MODEL_TELEMETRY_ENABLED": "0", "ZCODE_HOME": str(root / "telemetry")})
    command = cli_command(environment)
    version_result = subprocess.run([*command, "--version"], env=environment, cwd=control["cwd"], capture_output=True, timeout=5)
    version = version_result.stdout.decode(errors="replace").strip()[:80] if version_result.returncode == 0 else "unknown"
    native_stderr = directory / "native.stderr.log"
    fd = os.open(native_stderr, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        process = subprocess.Popen([*command, "app-server", "--cwd", control["cwd"]], env=environment,
                                   cwd=control["cwd"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=fd,
                                   start_new_session=True, close_fds=True)
    finally:
        os.close(fd)
    handle = ProcessHandle(process, own_group=True, log_paths={})
    result = {"status": "error", "mode": "zcode", "harnessVersion": version,
              "requested": control.get("spec"), "resolved": None, "observed": None}
    record = None
    session_id = None
    try:
        connection = NativeConnection(process, deadline, cancelled)
        connection.call("runtime/capabilities", {})
        workspace = {"workspacePath": control["cwd"], "workspaceKey": control["cwd"]}
        if control.get("discover"):
            snapshot = connection.call("session/create", {"workspace": workspace, "titleGenerationEnabled": False, "toolAllowlist": []})
            session_id = snapshot["session"]["sessionId"]
            result = {**result, "status": "ok", "catalog": catalog(snapshot, access, version)}
        else:
            turn_input = decode_json(Path(control["inputFile"]).read_bytes())
            if not isinstance(turn_input, dict):
                raise NativeError("invalid-input", "the governed input is not an object")
            identity = {k: turn_input[k] for k in ("taskId", "attemptId", "generation", "turnId")}
            bridge = {"identity": identity, "inputSha256": input_hash(turn_input), "key": secrets.token_hex(32)}
            bridge_path = directory / "finish-bridge.json"
            private_json(bridge_path, bridge, exclusive=True)
            server_name = "buddy_" + hashlib.sha256(identity["attemptId"].encode()).hexdigest()[:16]
            tool_name = f"mcp__{server_name}__buddy_finish_turn"
            mcp = [{"name": server_name, "command": sys.executable,
                    "args": ["-m", "buddy.adapters.zcode_mcp", "--config", str(bridge_path)],
                    "env": [{"name": "PYTHONPATH", "value": os.environ["PYTHONPATH"]}] if os.environ.get("PYTHONPATH") else [],
                    "isolation": "session", "protocolVersion": "legacy"}]
            mode = turn_input.get("resumeMode")
            previous = turn_input.get("previousSessionId")
            configuration = control.get("spec", {})
            if previous is not None and (not isinstance(previous, str) or not previous.strip()):
                raise NativeError("invalid-resume-mode", "the previous native session identity must be null or a nonblank string")
            if mode == "native-session":
                if not isinstance(previous, str) or not previous:
                    raise NativeError("native-resume-unavailable", "native resume requires the exact previous session identity")
                binding_path = root / (hashlib.sha256(previous.encode()).hexdigest() + ".json")
                try:
                    binding = decode_json(binding_path.read_bytes())
                except (OSError, ValueError):
                    raise NativeError("native-resume-unavailable", "the previous native session has no private goal binding") from None
                if binding != {"taskId": identity["taskId"], "sessionId": previous, "cwd": control["cwd"], "configuration": configuration}:
                    raise NativeError("native-resume-unavailable", "the previous native session does not match this goal, checkout and configuration")
                snapshot = connection.call("session/resume", {"sessionId": previous, "workspace": workspace, "mcpServers": mcp})
            elif mode == "reconstructed-new-session" or mode == "initial" and previous is None:
                snapshot = connection.call("session/create", {"workspace": workspace, "mode": "yolo", "titleGenerationEnabled": False, "mcpServers": mcp})
            else:
                raise NativeError("invalid-resume-mode", "ZCode requires initial, an explicitly bound native-session or a reconstructed-new-session turn")
            session = snapshot.get("session") or {}
            session_id = session.get("sessionId")
            if not isinstance(session_id, str) or not session_id or session.get("parentSessionId") or session.get("sessionKind") not in (None, "interactive"):
                raise NativeError("wrong-native-session", "ZCode did not create or restore a root session")
            if mode == "native-session" and session_id != previous:
                raise NativeError("wrong-native-session", "ZCode resumed a different native session")
            if mode == "reconstructed-new-session" and session_id == previous:
                raise NativeError("wrong-native-session", "ZCode reused the previous session instead of creating the requested new root")
            native_cwd = (session.get("workspace") or {}).get("workspacePath")
            if not native_cwd or Path(native_cwd).resolve() != Path(control["cwd"]).resolve():
                raise NativeError("wrong-native-workspace", "ZCode session checkout does not match the allocated workspace")
            result["resolved"] = configure_session(connection, snapshot, configuration, access)
            binding_path = root / (hashlib.sha256(session_id.encode()).hexdigest() + ".json")
            if mode != "native-session":
                private_json(binding_path, {"taskId": identity["taskId"], "sessionId": session_id, "cwd": control["cwd"],
                                           "configuration": result["resolved"]}, exclusive=True)
            input_id = "buddy-" + hashlib.sha256(canonical_json(identity).encode()).hexdigest()
            evidence = RootTurnEvidence(session_id, input_id, tool_name, bridge)
            connection.observe = evidence.observe
            connection.call("session/subscribe", {"sessionId": session_id, "deliveryKind": "web-remote-replayable", "includeSnapshot": False})
            prompt = "\n\n".join([
                "This is a governed Buddy root turn. Complete the authorized task using the available coding tools and internal subagents. Follow the frozen Host input and its allocated workspace.",
                f"Only the root may conclude this Buddy turn. After your work and internal subagents settle, obtain one successful receipt from {tool_name} with the complete structured outcome. Include all six fields: disposition, summary, remaining, decisions, artifacts, request. Completed requires request: null. Use assistance for bounded help or attention for a Host decision. If the tool explicitly fails, correct the arguments and retry in this turn. Plain final text is not a recorded outcome. After a successful finish receipt, do not start more tools; end the native turn.",
                Path(control["taskFile"]).read_text(), canonical_json(turn_input),
            ])
            accepted = connection.call("session/send", {"sessionId": session_id, "inputId": input_id, "content": prompt})
            if accepted.get("accepted") is not True or accepted.get("sessionId") != session_id:
                raise NativeError("native-admission-failed", "ZCode did not admit the intended root input")
            while not evidence.settled_ordinal:
                connection.pump()
            record = {"version": 1, **identity, "inputSha256": bridge["inputSha256"],
                      "promptSha256": hashlib.sha256(prompt.encode()).hexdigest(), "sessionId": session_id,
                      "previousSessionId": previous, "resumeMode": mode, "outcome": evidence.receipt["outcome"]}
            result.update(status="ok", sessionId=session_id)
        closed = connection.call("session/close", {"sessionId": session_id})
        if closed.get("closed") is not True:
            raise NativeError("session-close-unconfirmed", "ZCode did not acknowledge closing the native session")
        if record is not None:
            evidence.close_ordinal = connection.ordinal
            record["provenance"] = evidence.provenance()
    except NativeError as error:
        result.update(status="cancelled" if error.code == "cancelled" else "error", code=error.code, error=str(error))
        record = None
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        result.update(status="error", code="invalid-native-result", error="the native execution returned invalid or incomplete data")
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
    if cancelled.is_set():
        result.update(status="cancelled", code="cancelled", error="the owned ZCode execution was cancelled")
        record = None
    if result["status"] == "ok" and (not shutdown or process.returncode != 0):
        result.update(status="error", code="native-shutdown-failed", error="the native app server did not exit normally with confirmed group shutdown")
        record = None
    if record is not None:
        private_json(Path(control["outputFile"]), record, exclusive=True)
        result["nativeTurnId"] = record["provenance"]["nativeTurnId"]
    return result, 0 if result["status"] == "ok" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", required=True)
    args = parser.parse_args()
    cancelled = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda _sig, _frame: cancelled.set())
    try:
        result, code = run(json.loads(Path(args.control).read_text()), cancelled)
    except (NativeError, OSError, ValueError, subprocess.TimeoutExpired):
        result, code = {"status": "error", "code": "zcode-controller-failed", "error": "ZCode controller failed before verified native settlement",
                        "processState": {"shutdownConfirmed": False}}, 1
    sys.stdout.write(canonical_json(result) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
