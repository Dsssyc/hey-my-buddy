#!/usr/bin/env python3
"""Explicit installed-ZCode plan/yolo comparison; outside the check suite.

Run from the checkout root:
    PYTHONPATH=src:tests/python python tests/probes/zcode_plan_mode.py

Starts the installed macOS ZCode bundle through Node and sandbox-exec, a
loopback HTTP fake model, and the session-private Python completion MCP.
Reuses zcode_native.py's private HOME, provider configuration, session roots
and loopback-only sandbox; never reads the user's ZCode configuration or
calls a paid provider. Fresh sessions and resumed plan turns request Write,
Bash writes through printf, Python, tee, touch and cp, Read, read-only ls and
the mounted finish tool. Measures plan->plan, plan->yolo and yolo->plan on
the same respective sessions; the yolo continuation requests Write and finish.
Tools are requested even when absent from the advertised list. The last stdout
line is JSON with native evidence, file effects and settlement for all turns.
Errors are observations, not skips.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest import mock

from zcode_native import InstalledZcodeTests
from buddy.harnesses.zcode.test_native_run import MockNativeCase
from hey_my_buddy.buddy.harnesses.registry import worker_permission
from hey_my_buddy.buddy.harnesses.run_contract import RunContinuation
from hey_my_buddy.buddy.harnesses.zcode import native_run
from hey_my_buddy.buddy.roles.run_observers import worker_observer
from hey_my_buddy.errors import BoardError


MARKERS = {"write": "probe-write-ok", "command": "probe-command-ok", "read": "probe-read-ok",
           "python_command": "probe-python-ok", "tee_command": "probe-tee-ok",
           "touch_command": "", "copy_command": "probe-read-ok"}


class ModeProbe(InstalledZcodeTests):
    def setUp(self):
        # No requests occur until native_run.run: keep the installed probe's
        # isolation and replace only the localhost model's response sequence.
        super().setUp()
        self.base = self.root
        self.observations = []
        self.native_events = []
        self.next_operation = 0
        self.http.RequestHandlerClass = self.model_handler()
        self.write_target = self.cwd / "written.txt"
        self.command_target = self.cwd / "command.txt"
        self.read_target = self.cwd / "read.txt"
        self.read_target.write_text(MARKERS["read"])

    def model_handler(self):
        probe = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                tools = [tool["function"]["name"] for tool in data.get("tools", [])]
                for message in data["messages"]:
                    if message["role"] == "tool":
                        for observation in probe.observations:
                            if message.get("tool_call_id") == observation["callId"]:
                                if message not in observation["modelToolResults"]:
                                    observation["modelToolResults"].append(message)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

                def chunk(delta, reason=None):
                    body = {"id": "chatcmpl-local-mode-probe", "object": "chat.completion.chunk",
                            "created": 1, "model": data["model"],
                            "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
                    self.wfile.write(("data: " + json.dumps(body) + "\n\n").encode())
                    self.wfile.flush()

                # ZCode's native title requests omit tools. They must not
                # consume one of the root-turn operations.
                if "tools" in data and probe.next_operation < len(probe.operations):
                    operation, name, arguments = probe.operations[probe.next_operation]
                    call_id = f"{probe.call_prefix}_{operation}"
                    probe.observations.append({"operation": operation, "tool": name,
                                               "callId": call_id, "advertised": name in tools,
                                               "advertisedTools": tools,
                                               "arguments": arguments, "modelToolResults": []})
                    probe.next_operation += 1
                    chunk({"role": "assistant", "tool_calls": [{"index": 0, "id": call_id,
                           "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]})
                    chunk({}, "tool_calls")
                else:
                    chunk({"role": "assistant", "content": "Local mode probe finished."})
                    chunk({}, "stop")
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        return Handler

    def measure(self, mode, timeout, *, previous=None):
        self.observations, self.native_events, self.next_operation = [], [], 0
        self.turn_index = self.turn_index + 1 if previous else 1
        turn_input = self.context(index=self.turn_index, previous=previous, timeout=timeout).turn["input"]
        continuation = RunContinuation(mode="native-session", previous_session_id=previous) if previous else None
        request, services = MockNativeCase.worker_request(self, turn_input, timeout=timeout, continuation=continuation)
        permission = worker_permission("zcode", "write")
        request = request.model_copy(update={
            "configuration": request.configuration.model_copy(update={"model": "GLM-5.3-Flash"}),
            "worker_permission": permission})
        finish = next(name for name in request.session_services[0].tool_names if name.endswith("__buddy_finish_turn"))
        self.call_prefix = "call_probe_" + request.identity.invocation_id[:12]
        prefix = f"resumed-{self.turn_index}-" if previous else ""
        if previous:
            self.write_target = self.cwd / (prefix + "written.txt")
            self.command_target = self.cwd / (prefix + "command.txt")
        targets = {"write": self.write_target, "command": self.command_target,
                   "python_command": self.cwd / (prefix + "python.txt"),
                   "tee_command": self.cwd / (prefix + "tee.txt"),
                   "touch_command": self.cwd / (prefix + "touch.txt"),
                   "copy_command": self.cwd / (prefix + "copy.txt")}
        python_write = f"open({str(targets['python_command'])!r}, 'w').write({MARKERS['python_command']!r})"
        self.operations = [
            ("write", "Write", {"file_path": str(self.write_target), "content": MARKERS["write"]}),
            ("command", "Bash", {"command": "printf %s " + shlex.quote(MARKERS["command"])
                                 + " > " + shlex.quote(str(self.command_target)), "description": "Write probe marker"}),
            ("python_command", "Bash", {"command": "python3 -c " + shlex.quote(python_write),
                                         "description": "Write a marker through Python"}),
            ("tee_command", "Bash", {"command": "printf %s " + shlex.quote(MARKERS["tee_command"])
                                      + " | tee " + shlex.quote(str(targets["tee_command"])),
                                      "description": "Write a marker through tee"}),
            ("touch_command", "Bash", {"command": "touch " + shlex.quote(str(targets["touch_command"])),
                                        "description": "Create an empty probe file"}),
            ("copy_command", "Bash", {"command": "cp " + shlex.quote(str(self.read_target))
                                       + " " + shlex.quote(str(targets["copy_command"])),
                                       "description": "Copy the probe input file"}),
            ("read", "Read", {"file_path": str(self.read_target)}),
            ("read_command", "Bash", {"command": "ls " + shlex.quote(str(self.cwd)),
                                       "description": "List the probe directory without writing"}),
            ("finish", finish, {"disposition": "completed", "summary": "Local mode probe completed",
                                "remaining": [], "decisions": [], "artifacts": [], "request": None}),
        ]
        if previous and mode == "yolo":
            self.operations = [self.operations[0], self.operations[-1]]
        original = native_run.ZcodeToolFacts.observe

        def observe(collector, message):
            params = message.get("params", {})
            if (message.get("method") != "session/event"
                    or params.get("type") in ("tool.updated", "turn.completed", "turn.failed", "error")):
                self.native_events.append(json.loads(json.dumps(message)))
            return original(collector, message)

        mode_calls = []
        original_call = native_run.NativeConnection.call

        def call(connection, method, params):
            response = original_call(connection, method, params)
            if method in ("session/create", "session/resume", "session/setMode"):
                settings = response.get("settings")
                mode_calls.append({"method": method, "params": {key: params[key]
                                   for key in ("sessionId", "mode") if key in params},
                                   "readback": settings.get("mode") if isinstance(settings, dict) else settings,
                                   "session": response.get("session")})
            return response

        # The deliberate probe selects the mode in this process and exercises
        # the product's normal create/resume/setMode and completion paths.
        with mock.patch.dict(os.environ, self.environment, clear=True), \
                mock.patch.object(native_run, "_session_mode", return_value=mode), \
                mock.patch.object(native_run.ZcodeToolFacts, "observe", observe), \
                mock.patch.object(native_run.NativeConnection, "call", call):
            result = native_run.run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.last_result = result
        effects = {operation: target.read_text() if target.exists() else None for operation, target in targets.items()}
        completed = bool(result.end.status == "ok" and result.value is not None
                         and result.value.schema_status == "valid"
                         and result.stop_evidence.native.group_state == "gone")
        items = self.operation_reports(effects, completed)
        denied_refs = [json.loads(Path(ref.location).read_text()) for ref in result.evidence_refs
                       if ref.kind == "denied-interactions"]
        stderr = services.native_stderr
        if stderr is None:
            stderr = next((ref.location for ref in result.evidence_refs if ref.kind == "native-stderr"), None)
        return {"mode": mode, "harnessVersion": result.harness_version, "operations": items, "turnCompleted": completed,
                "filesCreated": [target.name for target in targets.values() if target.exists()],
                "previousSessionId": previous, "sameSession": result.native_identity.session_id == previous
                if previous and result.native_identity else None, "modeCalls": mode_calls,
                "end": result.end.to_payload(), "stopEvidence": result.stop_evidence.to_payload(),
                "effectivePolicy": result.effective_policy.to_payload(), "nativeIdentity":
                result.native_identity.to_payload() if result.native_identity else None,
                "deniedInteractions": denied_refs, "nativeStderr": Path(stderr).read_text()[-16000:] if stderr else ""}

    def operation_reports(self, effects, completed):
        items = []
        for operation, name, _arguments in self.operations:
            observed = next((item for item in self.observations if item["operation"] == operation), None)
            if observed is None:
                items.append({"operation": operation, "tool": name, "handling": "not-requested"})
                continue
            events = [event for event in self.native_events if observed["callId"] in json.dumps(event)]
            evidence = json.dumps([observed["modelToolResults"], events])
            allowed = (effects[operation] == MARKERS[operation] if operation in effects
                       else MARKERS["read"] in json.dumps(observed["modelToolResults"]) if operation == "read"
                       else self.read_target.name in json.dumps(observed["modelToolResults"]) if operation == "read_command"
                       else completed)
            denied = bool(re.search(r"denied|not allowed|not permitted|not available in plan|read.only mode|"
                                    r"Plan mode only allows read-only, non-destructive tools", evidence, re.I))
            items.append({**observed, "handling": "allowed" if allowed else "denied" if denied else "other",
                          "nativeEvents": events, "fileContent": effects.get(operation)})
        return items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=45, help="deadline in seconds for each mode")
    args = parser.parse_args()
    resume_keys = ("resumePlanToPlan", "resumePlanToYolo", "resumeYoloToPlan")
    report = {"probe": "zcode-plan-mode", "modes": [], **{key: {
        "turnCompleted": False, "handling": "not-run", "reason": "the previous turn did not complete"}
        for key in resume_keys}}
    for mode in ("plan", "yolo"):
        probe = ModeProbe()
        stage = "private-fixture-setup"
        resume_key, current_mode = None, mode
        try:
            probe.setUp()
            stage = "native-session-run"
            report["modes"].append(probe.measure(mode, args.timeout))
            continuations = (("resumePlanToPlan", "plan"), ("resumePlanToYolo", "yolo")) if mode == "plan" else (("resumeYoloToPlan", "plan"),)
            previous_completed = report["modes"][-1]["turnCompleted"]
            for resume_key, current_mode in continuations:
                if previous_completed:
                    stage = "native-session-resume"
                    report[resume_key] = probe.measure(current_mode, args.timeout,
                        previous=probe.last_result.native_identity.session_id)
                    previous_completed = report[resume_key]["turnCompleted"]
        except (OSError, ValueError, AssertionError, TypeError, BoardError, native_run.NativeError) as error:
            failure = {"mode": current_mode, "turnCompleted": False, "stage": stage,
                       "probeError": {"type": type(error).__name__, "message": str(error)}}
            if stage == "native-session-resume":
                report[resume_key] = failure
            else:
                report["modes"].append(failure)
            if stage == "private-fixture-setup":
                # A denied setup cannot measure either mode. Do not retry it
                # in a second private root or through another launcher.
                break
        finally:
            probe.doCleanups()
    print(json.dumps(report, ensure_ascii=False))
    return 0 if (len(report["modes"]) == 2 and all(item.get("turnCompleted") for item in report["modes"])
                 and all(report[key]["turnCompleted"] for key in resume_keys)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
