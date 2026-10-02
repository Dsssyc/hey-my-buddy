"""Owned DSH read-only controller for one restricted native Agent review call.

The controller drives the packaged ``read-only-structured.mjs`` bridge through
the same private headless profile helpers the no-tool controller uses. The
native registry keeps its own Agent/ToolRuntime/Session stack and its own LLM
loop; this controller only freezes the request, verifies the composed private
profile before any model runs, projects the native tool facts into the shared
evidence collector, and reports actual stop, budget and protocol failures. The
blackboard alone judges the recorded facts (ADR-021 §4, ADR-023 §3).
"""
from __future__ import annotations

import os
from pathlib import Path
import threading
import time
import uuid
import yaml

from ..errors import BoardError
from ..harness_discovery import native_environment
from ..harness_runtime import command_for
from ..runtime import resource_path
from ..tool_evidence import NATIVE_ID_FIELDS, ToolEventEvidence, normalize_tool_event
from .dsh_runner import private_profile, problem, read_object, run_child
from .read_only import correction_code, valid_answer
from .turn_io import private_json

_SENTINEL = "buddy-read-only-structured"
_BRIDGE_ID = "buddy-read-only-structured"
_MAX_RAW_ANSWER = 65_536
_USAGE_KEYS = ("inputTokens", "outputTokens", "totalTokens", "cacheReadTokens",
               "cacheWriteTokens", "reasoningTokens")
#: Native entry rows the private read-only profile must keep disabled: the
#: ordinary workflow runner, the title model and the telemetry exporter.
_DISABLED_IDS = ("headless-runner", "session-title-llm", "session-telemetry-otel")
#: No enabled row may carry the dynamic-workflow mark: an extra model or plugin
#: workflow would run outside the one restricted Agent turn.
_DYNAMIC_WORKFLOW_MARK = "workflow"
#: Enabled rows of the official stack the bridge drives; a dump missing any of
#: these marks cannot run the restricted Agent turn and fails before the model.
_STACK_IDS = ("agent", "agent-default-model", "agent-loop", "tools", "session", "tool-fs", "tool-fs-search")
_DYNAMIC_IDS = ("workflow-worker-thread", "tool-workflow")
#: Native failure codes the bridge reports; anything else is a protocol
#: violation, never a native verdict of its own.
_NATIVE_CODES = frozenset((
    "read-only-profile-unsafe", "invalid-configuration", "configuration-unavailable",
    "configuration-mismatch", "read-only-tools-unavailable", "tool-surface-expanded",
    "tool-budget-exhausted", "deadline", "stream-incomplete", "native-turn-failed",
    "flush-failed", "stop-unknown", "invalid-native-result", "answer-too-large"))


def bridge_plugin() -> Path:
    """The packaged read-only Node bridge shipped beside the declared runner."""
    return resource_path("dsh.runner").parent.parent / "plugins" / "read-only-structured.mjs"


def _dump_rows(raw: str) -> dict[str, dict[str, str]] | None:
    """Load-bearing rows of the native config dump; unknown shapes fail closed."""
    if len(raw.encode()) > 1024 * 1024:
        return None
    # Compose syntax nodes without constructing custom !!js tags or evaluating
    # their values. Native dumps may put config before id and fold long names.
    try:
        document = yaml.compose(raw, Loader=yaml.BaseLoader)
    except (yaml.YAMLError, RecursionError):
        return None
    if not isinstance(document, yaml.SequenceNode):
        return None
    rows = {}
    for node in document.value:
        if not isinstance(node, yaml.MappingNode):
            return None
        fields = {}
        for key, value in node.value:
            if not isinstance(key, yaml.ScalarNode) or key.value in fields:
                return None
            fields[key.value] = value
        values = {key: value.value for key, value in fields.items()
                  if isinstance(value, yaml.ScalarNode) and value.tag == 'tag:yaml.org,2002:str'}
        identity = values.get('id')
        if identity:
            if identity in rows:
                return None
            rows[identity] = values
    return rows


def read_only_composed(raw: str, plugin: Path) -> bool:
    """Check the read-only profile dump before any model runs; unknown fails."""
    rows = _dump_rows(raw)
    if rows is None:
        return False
    mounted = rows.get(_BRIDGE_ID, {})
    if (mounted.get("disabled") == "true"
            or mounted.get("name") not in (str(plugin), plugin.as_uri())):
        return False
    if any(rows.get(entry, {}).get("disabled") != "true" for entry in _DISABLED_IDS):
        return False
    enabled = [row_id.lower() for row_id, values in rows.items()
               if values.get("disabled") != "true"]
    if any(_DYNAMIC_WORKFLOW_MARK in row_id for row_id in enabled):
        return False
    return all(entry in enabled for entry in _STACK_IDS)


def _root_identity(value) -> dict | None:
    """The native root-session receipt: only real string identity fields."""
    if (not isinstance(value, dict) or not value or set(value) - NATIVE_ID_FIELDS
            or any(not isinstance(item, str) or not item or len(item) > 512
                   for item in value.values())):
        return None
    return dict(value)


def run(control: dict, cancelled: threading.Event) -> tuple[dict, int]:
    started_at = time.monotonic()
    deadline = started_at + control["timeoutSeconds"]
    directory = Path(control["directory"])
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # The binding is the private Python control identity, never model data. This
    # controller collects and classifies facts only; the single allowance judge
    # stays with the blackboard.
    evidence = ToolEventEvidence({"adapter": "dsh", "taskId": control["taskId"],
                                  "attemptId": control["attemptId"], "generation": control["generation"]})
    roots: list[dict] = []
    totals: dict[str, int] = {}
    model_started = False

    def settled(code: str, *, started: bool = False, stopped: bool = True,
                exit_code: int | None = None, complete: bool = False) -> tuple[dict, int]:
        payload = problem(code, started=started or model_started, stopped=stopped, exit_code=exit_code)
        payload["toolEvidence"] = evidence.finish(roots, complete)
        payload["usage"] = {**totals, "toolCalls": evidence.tool_calls, "bytesRead": None,
                            "elapsedMs": round((time.monotonic() - started_at) * 1000)}
        return payload, 1

    def projected(native: dict) -> None:
        """Observe every native fact before any acceptance filtering."""
        events = native.get("nativeToolEvents")
        if not isinstance(events, list):
            evidence.observe_incomplete("dsh", {})
            return
        if type(native.get("nativeToolEventsTruncated")) is not bool or type(native.get("streamComplete")) is not bool:
            evidence.observe_incomplete("dsh", {})
        for fact in events:
            try:
                event = normalize_tool_event("dsh", fact)
            except BoardError:
                evidence.observe_incomplete("dsh", fact)
            else:
                if event is not None:
                    evidence.observe(event)

    def failed(code: str, *, started: bool, exit_code: int | None, complete: bool,
               native_turn_end=None, failure_stage=None) -> tuple[dict, int]:
        result = problem(code, started=started or model_started, stopped=code != "stop-unknown", exit_code=exit_code)
        if isinstance(native_turn_end, str) and 0 < len(native_turn_end) <= 100:
            result["nativeTurnEnd"] = native_turn_end
        if failure_stage in ("provider-registration", "agent-create", "native-turn"):
            result["failureStage"] = failure_stage
        result["toolEvidence"] = evidence.finish(roots, complete)
        result["usage"] = {**totals, "toolCalls": evidence.tool_calls, "bytesRead": None,
                           "elapsedMs": round((time.monotonic() - started_at) * 1000)}
        return result, 1

    try:
        plugin = bridge_plugin()
    except BoardError:
        return settled("adapter-unavailable")
    if not plugin.is_file():
        return settled("adapter-unavailable")
    incoming = dict(os.environ)
    try:
        command = command_for("dsh", incoming)
    except BoardError:
        return settled("adapter-unavailable")
    environment = native_environment(incoming, command=command)
    # The owning harness home follows its default chain: an explicit DSH_HOME
    # when this controller environment carries one, else the user's .dsh. A
    # home without the public headless profile fails closed instead of
    # composing a wrong one.
    source_home = Path(incoming.get("DSH_HOME") or Path.home() / ".dsh").resolve()
    if not (source_home / "profiles" / "headless" / "package.json").is_file():
        return settled("read-only-profile-missing")
    environment["DSH_HOME"] = str(private_profile(directory, source_home))
    request = control.get("readOnlyRequest")
    spec = control.get("spec")
    budget = request.get("budget") if isinstance(request, dict) else None
    if (not isinstance(request, dict) or not isinstance(spec, dict)
            or not all(isinstance(spec.get(key), str) and spec[key] for key in ("provider", "model", "effort"))
            or not isinstance(request.get("prompt"), str) or not request["prompt"].strip()
            or not isinstance(request.get("outputSchema"), dict)
            or not isinstance(budget, dict)
            or type(budget.get("toolCalls")) is not int or budget["toolCalls"] < 0):
        return settled("invalid-configuration")
    total_tools = budget["toolCalls"]
    base_prompt = request["prompt"]
    prompt = base_prompt
    for attempt in range(2):
        if cancelled.is_set():
            return settled("cancelled")
        if time.monotonic() >= deadline:
            return settled("deadline")
        round_base = evidence.tool_calls
        call_dir = directory / f"call-{attempt + 1}"
        call_dir.mkdir(mode=0o700)
        call_id = str(uuid.uuid4())
        input_file, result_file, patch_file = (call_dir / name for name in
                                               ("request.json", "result.json", "patch.json"))
        # One absolute deadline spans preflight, both answer rounds and the
        # accumulated tool budget; a correction round may carry zero remaining
        # tool calls, but never new time.
        private_json(input_file, {"callId": call_id, "cwd": control["cwd"], "spec": spec,
                                  "prompt": prompt, "outputSchema": request["outputSchema"],
                                  "budget": {"timeoutSeconds": max(1, int(deadline - time.monotonic())),
                                             "toolCalls": max(0, total_tools - round_base)}})
        patch = [
            {"id": "headless-runner", "disabled": True},
            {"id": "session-telemetry-otel", "disabled": True},
            {"id": "session-title-llm", "disabled": True},
            *({"id": entry, "disabled": True} for entry in _DYNAMIC_IDS),
            {"id": "session-persistence-jsonl", "config": {"root": str(call_dir / "sessions")}},
            {"insert": [{"id": _BRIDGE_ID, "name": str(plugin),
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
            failure = "cancelled" if reason == "cancelled" else "deadline" if reason == "deadline" else "read-only-profile-unverified"
            return settled(failure, stopped=stopped, exit_code=code)
        try:
            dump = (call_dir / "preflight" / "stdout.log").read_text()
        except OSError:
            return settled("read-only-profile-unverified")
        if not read_only_composed(dump, plugin):
            return settled("read-only-profile-unsafe")
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
        model_started = model_started or native.get("modelStarted") is True
        projected(native)
        # The trusted root identity is the native root session the bridge
        # actually created, one correction turn at a time; it is never inferred
        # back from the observed events.
        identity = _root_identity(native.get("nativeIdentity"))
        if native.get("modelStarted") is True and identity is not None:
            roots.append(identity)
        truncated = native.get("nativeToolEventsTruncated") is True
        turn_complete = native.get("streamComplete") is True and not truncated
        if native.get("status") != "ok":
            code_name = native.get("code")
            if code_name not in _NATIVE_CODES:
                code_name = "invalid-native-result"
            if code_name == "tool-budget-exhausted":
                code_name = "readonly-budget-exhausted"
            return failed(code_name, started=native.get("modelStarted") is True, exit_code=code,
                          complete=turn_complete, native_turn_end=native.get("nativeTurnEnd"),
                          failure_stage=native.get("failureStage"))
        usage = native.get("usage")
        if (code != 0 or native.get("streamComplete") is not True or truncated
                or native.get("modelStarted") is not True
                or not isinstance(native.get("nativeToolEvents"), list)
                or type(native.get("nativeToolEventsTruncated")) is not bool
                or native.get("resolved") != spec or identity is None
                or not isinstance(usage, dict)
                or type(usage.get("toolCalls")) is not int or usage["toolCalls"] < 0
                or usage["toolCalls"] != evidence.tool_calls - round_base
                or not isinstance(native.get("rawAnswer"), str)
                or len(native["rawAnswer"].encode()) > _MAX_RAW_ANSWER):
            return failed("invalid-native-result", started=model_started, exit_code=code, complete=turn_complete)
        for key, value in usage.items():
            if key in _USAGE_KEYS and type(value) is int and value >= 0:
                totals[key] = totals.get(key, 0) + value
        raw = native["rawAnswer"]
        evidence.close_root(identity)
        correction = correction_code(raw, request["outputSchema"])
        if correction is None or attempt:
            # finish runs once per attempt exit; observing never follows it, so
            # a corrected second turn still accumulates cleanly.
            package = evidence.finish(roots, turn_complete)
            result = {"status": "ok", "rawAnswer": raw, "resolved": spec,
                      "observed": None, "modelStarted": True, "nativeIdentity": identity,
                      "usage": {**totals, "toolCalls": evidence.tool_calls, "bytesRead": None,
                                "elapsedMs": round((time.monotonic() - started_at) * 1000)},
                      "answerValid": valid_answer(raw, request["outputSchema"]),
                      "correctionCount": attempt, "toolEvidence": package,
                      "processState": {"shutdownConfirmed": True, "nativeExitCode": code}}
            return result, 0
        # A legal native read/search loop is one call; only a malformed answer
        # shape opens the single correction turn, and a candidate outside the
        # frozen choices is never corrected.
        prompt = base_prompt + "\n\nFormat correction: " + correction + ". Return exactly the supplied JSON Schema."
    return settled("invalid-native-result")
