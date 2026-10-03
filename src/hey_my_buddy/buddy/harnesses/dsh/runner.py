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

from ....errors import BoardError
from ..runtime_selection import command_for
from ..discovery import native_environment
from ....install.runtime import resource_path
from ....protocol.tool_evidence import ToolEventEvidence, normalize_tool_event
from ..base import ProcessHandle
from ...roles.structured_call import correction_code, no_tool_prompt, valid_answer
from ...roles.turn_io import canonical_json, private_json
from ...runtime.windows_process import owned_popen

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
    # The binding is the private Python control identity, never model data. This
    # controller collects and classifies facts only; the single allowance judge
    # stays with the blackboard.
    evidence = ToolEventEvidence({"adapter": "dsh", "taskId": control["taskId"],
                                  "attemptId": control["attemptId"], "generation": control["generation"]})
    roots: list[dict] = []

    def settled(code: str, *, started: bool = False, stopped: bool = True,
                exit_code: int | None = None, complete: bool = False) -> tuple[dict, int]:
        payload = problem(code, started=started, stopped=stopped, exit_code=exit_code)
        payload["toolEvidence"] = evidence.finish(roots, complete)
        return payload, 1

    def projected(native: dict) -> None:
        """Observe every native fact before any acceptance filtering."""
        events = native.get("nativeToolEvents")
        if not isinstance(events, list):
            return
        for fact in events:
            try:
                event = normalize_tool_event("dsh", fact)
            except BoardError:
                evidence.observe_incomplete("dsh", fact)
            else:
                if event is not None:
                    evidence.observe(event)

    def failed(code: str, *, started: bool, exit_code: int | None, complete: bool,
               native_failure: dict | None = None, truncated: bool = False) -> tuple[dict, int]:
        result = problem(code, started=started, exit_code=exit_code)
        if isinstance(native_failure, dict):
            result["nativeFailure"] = {key: value for key, value in native_failure.items()
                                       if key in ('finishKind', 'code', 'name', 'category', 'phase',
                                                  'status', 'statusCode', 'httpStatus')}
        if truncated:
            result["nativeToolEventsTruncated"] = True
        result["toolEvidence"] = evidence.finish(roots, complete)
        return result, 1

    if not plugin.is_file():
        return settled("adapter-unavailable")
    incoming = dict(os.environ)
    command = command_for("dsh", incoming)
    environment = native_environment(incoming, command=command)
    source_home = Path(incoming.get("DSH_HOME") or Path.home() / ".dsh").resolve()
    if not (source_home / "profiles" / "headless" / "package.json").is_file():
        return settled("no-tool-profile-missing")
    environment["DSH_HOME"] = str(private_profile(directory, source_home))
    request = control["noToolRequest"]
    spec = control["spec"]
    base_prompt = no_tool_prompt(request["prompt"], request["outputSchema"])
    prompt = base_prompt
    for attempt in range(2):
        if cancelled.is_set():
            return settled("cancelled")
        if time.monotonic() >= deadline:
            return settled("deadline")
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
            return settled("adapter-unavailable")
        if reason != "exited" or code != 0 or not stopped:
            failure = "cancelled" if reason == "cancelled" else "deadline" if reason == "deadline" else "no-tool-profile-unverified"
            return settled(failure, stopped=stopped, exit_code=code)
        try:
            dump = (call_dir / "preflight" / "stdout.log").read_text()
        except OSError:
            return settled("no-tool-profile-unverified")
        if not composed_safe(dump, plugin):
            return settled("no-tool-profile-unsafe")
        if cancelled.is_set() or time.monotonic() >= deadline:
            return settled("cancelled" if cancelled.is_set() else "deadline")
        try:
            reason, code, stopped = run_child([*argv, "--", "--", _SENTINEL],
                                               control["cwd"], environment, call_dir / "native",
                                               deadline, cancelled)
        except OSError:
            return settled("adapter-unavailable")
        if reason != "exited" or not stopped:
            failure = "cancelled" if reason == "cancelled" else "deadline" if reason == "deadline" else "native-shutdown-failed"
            return settled(failure, started=True, stopped=stopped, exit_code=code)
        native = read_object(result_file)
        if native is None:
            return settled("invalid-native-result", started=True, exit_code=code)
        projected(native)
        # The controller-created call id is this direct call's only root identity,
        # taken from the call the native actually started, one correction turn at
        # a time; it is never inferred back from the observed events.
        if native.get("modelStarted") is True:
            roots.append({"callId": call_id})
        truncated = native.get("nativeToolEventsTruncated") is True
        turn_complete = native.get("streamComplete") is True and not truncated
        if native.get("status") != "ok":
            code_name = native.get("code")
            if code_name not in ("no-tool-violation", "invalid-native-result", "native-turn-failed",
                                 "configuration-unavailable", "configuration-mismatch", "invalid-configuration",
                                 "no-tool-profile-unsafe", "deadline", "answer-too-large"):
                code_name = "invalid-native-result"
            return failed(code_name, started=native.get("modelStarted") is True, exit_code=code,
                          complete=turn_complete, native_failure=native.get("nativeFailure"),
                          truncated=truncated)
        if (code != 0 or native.get("streamComplete") is not True or native.get("nativeToolsDisabled") is not True
                or native.get("modelStarted") is not True or native.get("nativeIdentity") != {"callId": call_id}
                or native.get("nativeToolEvents") != [] or native.get("nativeToolEventsTruncated") is not False
                or native.get("resolved") != spec or not isinstance(native.get("rawAnswer"), str)
                or not isinstance(native.get("usage"), dict)
                or type((native.get("usage") or {}).get("toolCalls")) is not int
                or native["usage"]["toolCalls"] != 0):
            return failed("invalid-native-result", started=True, exit_code=code, complete=turn_complete)
        if request.get("captureEvidence") and (type(native.get("nativeChunkCount")) is not int
                                               or native["nativeChunkCount"] < 2
                                               or native.get("nativeToolSchemaCount") != 0):
            return failed("invalid-native-result", started=True, exit_code=code, complete=turn_complete)
        raw = native["rawAnswer"]
        if len(raw.encode()) > 65_536:
            return failed("invalid-native-result", started=True, exit_code=code, complete=turn_complete)
        correction = correction_code(raw, request["outputSchema"])
        if correction is None or attempt:
            # finish runs once per attempt exit; observing never follows it, so a
            # corrected second turn still accumulates cleanly.
            package = evidence.finish(roots, turn_complete)
            if (package["events"] or not package["streamComplete"] or package["truncated"]
                    or package["toolCalls"] or package["unsettledToolCalls"]):
                result = problem("invalid-native-result", started=True, exit_code=code)
                result["toolEvidence"] = package
                return result, 1
            result = {"status": "ok", "rawAnswer": raw, "resolved": spec, "observed": None,
                      "modelStarted": True, "nativeIdentity": native["nativeIdentity"],
                      "usage": {key: value for key, value in native["usage"].items()
                                if key in ("toolCalls", "inputTokens", "outputTokens", "totalTokens",
                                           "cacheReadTokens", "cacheWriteTokens", "reasoningTokens")
                                and type(value) is int and value >= 0}, "zeroToolVerified": True,
                      "answerValid": valid_answer(raw, request["outputSchema"]),
                      "correctionCount": attempt, "toolEvidence": package,
                      "processState": {"shutdownConfirmed": True, "nativeExitCode": code}}
            if request.get("captureEvidence"):
                result["nativeEvidence"] = {"chunkCount": native.get("nativeChunkCount"),
                                            "toolSchemaCount": native.get("nativeToolSchemaCount"),
                                            "profileRunnerDisabled": True}
            return result, 0
        prompt = base_prompt + "\n\nFormat correction: " + correction + ". Return exactly the supplied JSON Schema."
    return settled("invalid-native-result")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", required=True)
    args = parser.parse_args()
    cancelled = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT, *((signal.SIGBREAK,) if hasattr(signal, "SIGBREAK") else ())):
        signal.signal(sig, lambda _sig, _frame: cancelled.set())
    try:
        control = json.loads(Path(args.control).read_text())
        if "readOnlyRequest" in control:
            result, code = problem("readonly-worker-carrier-unimplemented"), 1
        else:
            result, code = run(control, cancelled)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        result, code = problem("dsh-controller-failed", stopped=False), 1
    sys.stdout.write(canonical_json(result) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
