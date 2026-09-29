"""Owned DSH direct-LLM controller for one no-tool structured request."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid
import yaml

from ..harness_runtime import command_for
from ..harness_discovery import native_environment
from ..runtime import resource_path
from .base import ProcessHandle
from .read_only import correction_code, no_tool_prompt, valid_answer
from .turn_io import canonical_json, private_json
from .windows_process import owned_popen

_SENTINEL = "buddy-no-tool-structured"
_MAX_NATIVE_RESULT = 256 * 1024


def composed_safe(raw: str, plugin: Path) -> bool:
    """Check load-bearing rows in the native config dump; unknown means unsafe."""
    if len(raw.encode()) > 1024 * 1024:
        return False
    # Compose syntax nodes without constructing custom !!js tags or evaluating
    # their values. Native dumps may put config before id and fold long names.
    try:
        document = yaml.compose(raw, Loader=yaml.BaseLoader)
    except (yaml.YAMLError, RecursionError):
        return False
    if not isinstance(document, yaml.SequenceNode):
        return False
    rows = {}
    for node in document.value:
        if not isinstance(node, yaml.MappingNode):
            return False
        fields = {}
        for key, value in node.value:
            if not isinstance(key, yaml.ScalarNode) or key.value in fields:
                return False
            fields[key.value] = value
        values = {key: value.value for key, value in fields.items()
                  if isinstance(value, yaml.ScalarNode) and value.tag == 'tag:yaml.org,2002:str'}
        identity = values.get('id')
        if identity:
            if identity in rows:
                return False
            rows[identity] = values
    runner = rows.get("headless-runner", {})
    mounted = rows.get("buddy-no-tool-structured", {})
    return (runner.get("disabled") == "true" and mounted.get("disabled") != "true"
            and mounted.get("name") in (str(plugin), plugin.as_uri()))


def run_child(command: list[str], cwd: str, environment: dict, directory: Path,
              deadline: float, cancelled: threading.Event) -> tuple[str, int | None, bool]:
    """Wait for one owned process group, including descendants and cancellation."""
    directory.mkdir(mode=0o700)
    out = os.open(directory / "stdout.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    err = os.open(directory / "stderr.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        process = owned_popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                              stdout=out, stderr=err, start_new_session=True, close_fds=True)
    finally:
        os.close(out)
        os.close(err)
    handle = ProcessHandle(process, own_group=True, log_paths={})
    reason = "exited"
    while process.poll() is None:
        if cancelled.is_set():
            reason = "cancelled"
            handle.terminate(grace_seconds=1)
            break
        if time.monotonic() >= deadline:
            reason = "deadline"
            handle.terminate(grace_seconds=1)
            break
        handle.wait(min(0.05, max(0.0, deadline - time.monotonic())))
    if process.poll() is None or handle.group_alive():
        handle.terminate(grace_seconds=0.5)
    return reason, process.poll(), handle.shutdown_confirmed(settle_seconds=0.5)


def read_object(path: Path) -> dict | None:
    try:
        if path.stat().st_size > _MAX_NATIVE_RESULT:
            return None
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, RecursionError):
        return None


def problem(code: str, *, started: bool = False, stopped: bool = True, exit_code: int | None = None) -> dict:
    return {"status": "cancelled" if code == "cancelled" else "error", "code": code,
            "modelStarted": started, "processState": {"shutdownConfirmed": stopped,
                                                       "nativeExitCode": exit_code}}


def private_profile(directory: Path, source_home: Path) -> Path:
    """Materialize only the shipped headless bundles under this attempt."""
    if not (source_home / "profiles" / "headless" / "package.json").is_file():
        raise FileNotFoundError("the owning DSH headless profile is not installed")
    private_home = directory / "dsh-home"
    profile = private_home / "profiles" / "headless"
    profile.mkdir(mode=0o700, parents=True)
    manifest = {"name": "buddy-no-tool-headless", "private": True, "dependencies": {},
                "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-headless"],
                                    "patchReload": "startup"}}}
    private_json(profile / "package.json", manifest)
    (profile / "cordis.patch.yml").write_text("[]\n")
    os.chmod(profile / "cordis.patch.yml", 0o600)
    return private_home


def run(control: dict, cancelled: threading.Event) -> tuple[dict, int]:
    deadline = time.monotonic() + control["timeoutSeconds"]
    directory = Path(control["directory"])
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    plugin = resource_path("dsh.runner").parent.parent / "plugins" / "no-tool-structured.mjs"
    if not plugin.is_file():
        return problem("adapter-unavailable"), 1
    incoming = dict(os.environ)
    command = command_for("dsh", incoming)
    environment = native_environment(incoming, command=command)
    source_home = Path(incoming.get("DSH_HOME") or Path.home() / ".dsh").resolve()
    if not (source_home / "profiles" / "headless" / "package.json").is_file():
        return problem("no-tool-profile-missing"), 1
    environment["DSH_HOME"] = str(private_profile(directory, source_home))
    request = control["noToolRequest"]
    spec = control["spec"]
    base_prompt = no_tool_prompt(request["prompt"], request["outputSchema"])
    prompt = base_prompt
    for attempt in range(2):
        if cancelled.is_set():
            return problem("cancelled"), 1
        if time.monotonic() >= deadline:
            return problem("deadline"), 1
        call_dir = directory / f"call-{attempt + 1}"
        call_dir.mkdir(mode=0o700)
        call_id = str(uuid.uuid4())
        input_file, result_file, patch_file = (call_dir / name for name in
                                               ("request.json", "result.json", "patch.json"))
        private_json(input_file, {"callId": call_id, "prompt": prompt, "spec": spec})
        patch = [
            {"id": "headless-runner", "disabled": True},
            {"id": "session-telemetry-otel", "disabled": True},
            {"id": "session-title-llm", "disabled": True},
            {"id": "session-persistence-jsonl", "config": {"root": str(call_dir / "sessions")}},
            {"insert": [{"id": "buddy-no-tool-structured", "name": str(plugin),
                         "config": {"requestFile": str(input_file), "outputFile": str(result_file),
                                    "timeoutMs": max(1, int((deadline - time.monotonic()) * 1000))}}]},
        ]
        settings = source_home / "settings.yaml"
        credentials = source_home / ".credentials.yaml"
        if settings.is_file():
            patch.insert(0, {"id": "settings", "config": {"path": str(settings), "watch": False}})
        if credentials.is_file():
            patch.insert(0, {"id": "credentials", "config": {"path": str(credentials), "watch": False}})
        private_json(patch_file, patch)
        argv = [*command, "--profile", "headless", "--patch", str(patch_file)]
        try:
            reason, code, stopped = run_child([*argv, "--dump-config"], control["cwd"],
                                               environment, call_dir / "preflight", deadline, cancelled)
        except OSError:
            return problem("adapter-unavailable"), 1
        if reason != "exited" or code != 0 or not stopped:
            failure = "cancelled" if reason == "cancelled" else "deadline" if reason == "deadline" else "no-tool-profile-unverified"
            return problem(failure, stopped=stopped, exit_code=code), 1
        try:
            dump = (call_dir / "preflight" / "stdout.log").read_text()
        except OSError:
            return problem("no-tool-profile-unverified"), 1
        if not composed_safe(dump, plugin):
            return problem("no-tool-profile-unsafe"), 1
        if cancelled.is_set() or time.monotonic() >= deadline:
            return problem("cancelled" if cancelled.is_set() else "deadline"), 1
        try:
            reason, code, stopped = run_child([*argv, "--", "--", _SENTINEL],
                                               control["cwd"], environment, call_dir / "native",
                                               deadline, cancelled)
        except OSError:
            return problem("adapter-unavailable"), 1
        if reason != "exited" or not stopped:
            failure = "cancelled" if reason == "cancelled" else "deadline" if reason == "deadline" else "native-shutdown-failed"
            return problem(failure, started=True, stopped=stopped, exit_code=code), 1
        native = read_object(result_file)
        if native is None:
            return problem("invalid-native-result", started=True, exit_code=code), 1
        if native.get("status") != "ok":
            code_name = native.get("code")
            if code_name not in ("no-tool-violation", "invalid-native-result", "native-turn-failed",
                                 "configuration-unavailable", "configuration-mismatch", "invalid-configuration",
                                 "no-tool-profile-unsafe", "deadline", "answer-too-large"):
                code_name = "invalid-native-result"
            result = problem(code_name, started=native.get("modelStarted") is True, exit_code=code)
            if isinstance(native.get('nativeFailure'), dict):
                result['nativeFailure'] = {key: value for key, value in native['nativeFailure'].items()
                                           if key in ('finishKind', 'code', 'name', 'category', 'phase', 'status', 'statusCode', 'httpStatus')}
            if code_name == "no-tool-violation":
                result["usage"] = {"toolCalls": 1}
            return result, 1
        if (code != 0 or native.get("streamComplete") is not True or native.get("nativeToolsDisabled") is not True
                or native.get("modelStarted") is not True or native.get("nativeIdentity") != {"callId": call_id}
                or native.get("resolved") != spec or not isinstance(native.get("rawAnswer"), str)
                or not isinstance(native.get("usage"), dict)
                or type((native.get("usage") or {}).get("toolCalls")) is not int
                or native["usage"]["toolCalls"] != 0):
            return problem("invalid-native-result", started=True, exit_code=code), 1
        if request.get("captureEvidence") and (type(native.get("nativeChunkCount")) is not int
                                               or native["nativeChunkCount"] < 2
                                               or native.get("nativeToolSchemaCount") != 0):
            return problem("invalid-native-result", started=True, exit_code=code), 1
        raw = native["rawAnswer"]
        if len(raw.encode()) > 65_536:
            return problem("invalid-native-result", started=True, exit_code=code), 1
        result = {"status": "ok", "rawAnswer": raw, "resolved": spec, "observed": None,
                  "modelStarted": True, "nativeIdentity": native["nativeIdentity"],
                  "usage": {key: value for key, value in native["usage"].items()
                            if key in ("toolCalls", "inputTokens", "outputTokens", "totalTokens",
                                       "cacheReadTokens", "cacheWriteTokens", "reasoningTokens")
                            and type(value) is int and value >= 0}, "zeroToolVerified": True,
                  "answerValid": valid_answer(raw, request["outputSchema"]),
                  "correctionCount": attempt,
                  "processState": {"shutdownConfirmed": True, "nativeExitCode": code}}
        if request.get("captureEvidence"):
            result["nativeEvidence"] = {"chunkCount": native.get("nativeChunkCount"),
                                        "toolSchemaCount": native.get("nativeToolSchemaCount"),
                                        "profileRunnerDisabled": True}
        correction = correction_code(raw, request["outputSchema"])
        if correction is None or attempt:
            return result, 0
        prompt = base_prompt + "\n\nFormat correction: " + correction + ". Return exactly the supplied JSON Schema."
    return problem("invalid-native-result"), 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", required=True)
    args = parser.parse_args()
    cancelled = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT, *((signal.SIGBREAK,) if hasattr(signal, "SIGBREAK") else ())):
        signal.signal(sig, lambda _sig, _frame: cancelled.set())
    try:
        result, code = run(json.loads(Path(args.control).read_text()), cancelled)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        result, code = problem("dsh-controller-failed", stopped=False), 1
    sys.stdout.write(canonical_json(result) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
