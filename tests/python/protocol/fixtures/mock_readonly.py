#!/usr/bin/env python3
"""Deterministic stand-in native review over the registered role-run protocol; never a model.

Capability admission (``DshAdapter`` eligibility and availability) and the frozen
input mirror (``router_input``) are in-process test patches. Everything after
``start_review`` is the production path: the stand-in ``start`` freezes the same
role control, ``RunIdentity`` binding and private roots as
``hey_my_buddy.buddy.roles.run_execution._launch`` and spawns this module as the
controller process. The child builds the real ``RunRequest`` through
``run_execution.review_request``, publishes it with the real run-request codec,
answers through the ``RunResult`` models encoded by ``encode_run_result`` into the
invocation's ``role-run-result.json``, and writes the real verdict file. The role
collector (``structured_call.collect`` over ``collect_controller``,
``read_review_result`` and the two-layer ``router_stop_confirmed`` rule) then runs
unmodified; the handle's ``log_paths["stdout"]`` names the result frame file the
frozen control binding carries, while the child's console stdout keeps the role
projection (``run_execution._review_result``) of that same frame as the retained
runner log.

Isolation boundary: nothing here ran a harness or a model. The tool facts are the
shared ``router_tool_receipt`` fixture package (fake receipts), the native root
identity is the constant ``mock-native``, the checked configuration stays empty,
and the verdict elapsed time is a fixture constant. The frame reports the
simulated native outcome a completed review would report — a started model and a
settled stop — so the blackboard's publication, abstention and health rules judge
the flow exactly as a real native review; every process-level fact (the owned
group, its logs, the outer stop observation and the frame files) is real. A stop
fact that is not exactly ``True`` — including a frame the strict codec refuses —
never verifies shutdown.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid
from unittest.mock import patch
from blackboard.routing.fixtures.router_tool_receipt import tool_receipt


def prepare_input(manifest, directory):
    from hey_my_buddy.buddy.roles.router_input import digest

    root = directory / "mock-frozen-input"
    root.mkdir(mode=0o700, parents=True)
    (root / "input.txt").write_text("private service-protocol fixture\n")
    return root, digest(root)


def verify_input(manifest, root, expected):
    from hey_my_buddy.buddy.roles.router_input import digest

    return {"unchanged": digest(root) == expected,
            "manifestSha256": (manifest or {}).get("manifestSha256"),
            "snapshotSha256": expected}


def install(testcase, *, path=None, **options):
    """Inject locally eligible native capability only within this test process."""
    from hey_my_buddy.buddy.harnesses.dsh.adapter import DshAdapter

    testcase._readonly_fixture = Path(path) if path is not None else Path(__file__).resolve()
    testcase._readonly_options = options
    if getattr(testcase, "_readonly_installed", False):
        testcase.readonly_start.reset_mock()
        testcase.input_prepare.reset_mock()
        testcase.input_verify.reset_mock()
        return
    testcase._readonly_installed = True
    testcase.readonly_start = testcase.enterContext(patch(
        "hey_my_buddy.buddy.roles.run_execution.start_review", autospec=True,
        side_effect=lambda harness, context, request: start(
            harness, context, request, testcase._readonly_fixture, testcase._readonly_options),
    ))
    testcase.enterContext(patch.object(DshAdapter, "read_only_structured", True))
    testcase.enterContext(patch.object(DshAdapter, "local_read_only_check", return_value={
        "eligible": True, "reasonCode": None, "reason": None,
        "systemSandbox": False,
        "sameAttemptContinuation": False,
    }))
    testcase.enterContext(patch.object(DshAdapter, "available", side_effect=lambda: (testcase._readonly_fixture.is_file(), "mock fixture missing")))
    testcase.input_prepare = testcase.enterContext(patch("hey_my_buddy.buddy.roles.router_input.prepare", side_effect=prepare_input))
    testcase.input_verify = testcase.enterContext(patch("hey_my_buddy.buddy.roles.router_input.verify", side_effect=verify_input))


def start(harness, context, request, fixture, options):
    """The stand-in ``start_review``: the production control freeze and spawn."""
    from hey_my_buddy.buddy.harnesses.base import ProcessHandle, open_logs
    from hey_my_buddy.buddy.harnesses.run_contract import RunIdentity
    from hey_my_buddy.buddy.harnesses.runtime_selection import controller_environment
    from hey_my_buddy.buddy.roles.run_execution import _REQUEST, _VERDICT, _account_environment
    from hey_my_buddy.buddy.roles.turn_io import canonical_json, guard_private_path, private_json
    from hey_my_buddy.json_codec import decode_strict_json
    from hey_my_buddy.private_dirs import context_root, ensure_private_dir

    assert context.turn is None and context.agent_credential is None
    assert context.cwd == request.cwd
    assert set(request.output_schema["properties"]) == {"profileId", "reason", "evidence"}
    assert request.budget == context.decision_input["budget"]
    identity = RunIdentity(task_id=context.task_id, attempt_id=context.attempt_id,
                           generation=context.generation, invocation_id=uuid.uuid4().hex)
    private = ensure_private_dir(context_root(context, harness) / ("review-" + identity.invocation_id))
    control = {
        "operation": "review", "harness": harness, "privateRoot": str(private),
        "directory": str(context.directory),
        "nativeRoot": str(ensure_private_dir(context_root(context, harness) / "review-native")),
        "account": context.runtime.get("account"),
        "cwd": request.cwd, "timeoutSeconds": request.budget["timeoutSeconds"],
        "taskId": context.task_id, "attemptId": context.attempt_id, "generation": context.generation,
        "spec": {key: context.spec[key] for key in ("provider", "model", "effort")},
        "canCorrect": False,
        "readOnlyRequest": {"prompt": request.prompt, "outputSchema": request.output_schema,
                            "budget": request.budget, "captureEvidence": request.capture_evidence},
        # The stand-in's own material: it never reaches the role reader's keys.
        "fixture": {"document": context.decision_input, "options": options},
    }
    control.update(invocationId=identity.invocation_id,
                   requestFile=str(private / _REQUEST), verdictFile=str(private / _VERDICT),
                   # Production carries the result frame on the controller's stdout;
                   # the stand-in names its frame file here instead and keeps the
                   # role projection of that frame on the real stdout log.
                   resultFile=str(private / "role-run-result.json"))
    path = private / "role-run-control.json"
    private_json(path, control)
    for log_path in context.log_paths().values():
        guard_private_path(Path(log_path))
    environment = controller_environment(context.directory,
                                         _account_environment(harness, context, purpose="review"),
                                         read_only=True)
    stdout, stderr = open_logs(context.log_paths())
    try:
        process = subprocess.Popen([sys.executable, str(fixture), str(path)],
                                   cwd=request.cwd, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, start_new_session=True, close_fds=True)
    finally:
        os.close(stdout)
        os.close(stderr)
    handle = ProcessHandle(process, own_group=True,
                           log_paths={**context.log_paths(), "stdout": control["resultFile"]})
    # Held bindings, frozen exactly as the production launch freezes them.
    handle.role_run_control = decode_strict_json(canonical_json(control))
    handle.role_run_identity = identity
    return handle


def _answer(document, options, mode):
    """The stand-in's answer choice plus its honest schema fact."""
    profiles = document["profiles"]
    chosen = options.get("profile_id") or profiles[0]["profileId"]
    evidence = [{"kind": "card", "ref": card["profileId"]}
                for card in document.get("cards", []) if card["profileId"] == chosen][:2]
    if options.get("evidence") == "none":
        evidence = []
    elif options.get("evidence") == "foreign":
        evidence = [{"kind": "card", "ref": "card-not-supplied"}]
    if mode in ("abstain", "abstain_with_evidence"):
        chosen = None
        evidence = [{"kind": "file", "ref": "input.txt"}] if mode == "abstain_with_evidence" else []
    if mode == "out_of_candidate":
        chosen = "dsh:not-a-candidate:model:off"
    answer = {"profileId": chosen, "reason": f"mock select chose {chosen}", "evidence": evidence}
    if mode == "answer_error":
        # Not JSON at all: the raw carrier keeps the delivery text, nothing parsed.
        return "{not json", None, "invalid"
    if mode == "answer_extra":
        answer["policyCheck"] = {"hardConstraints": {"adapter": "invented"}}
    elif mode == "answer_bad_evidence":
        answer["evidence"] = ["card-id"]
    elif mode == "answer_escape":
        answer["evidence"] = [{"kind": "file", "ref": "../secret"}]
    valid = mode in ("select_first", "abstain", "abstain_with_evidence", "json_answer", "input_changed")
    if mode == "json_answer":
        # A raw JSON string; the generic collector parses it.
        return json.dumps(answer), None, "valid"
    return json.dumps(answer), answer, "valid" if valid else "invalid"


#: The native end fact of each failure mode: reason code and exit code.
_END = {"error": ("call-timeout", 1), "protocol_error": ("invalid-native-result", 1),
        "budget": ("observer-interrupt", 1), "deadline": ("deadline", 1)}


def main(control_path):
    from hey_my_buddy.buddy.harnesses.run_contract import (
        NativeIdentity, ResultConfiguration, RunEnd, RunResult, RunValue,
        StopEvidence, StopLayer, decode_run_result, encode_run_request, encode_run_result)
    from hey_my_buddy.buddy.roles import run_execution
    from hey_my_buddy.buddy.roles.turn_io import _private_bytes, canonical_json, private_json

    control = json.loads(Path(control_path).read_text())
    options = control["fixture"]["options"]
    mode = options.get("mode", "select_first")
    if mode == "sleep":
        time.sleep(float(options.get("sleep", "30")))
        return 0
    if mode == "no_output":
        return 0
    # The actual role constructor and codec, over the frozen stored control.
    request, _services, _observer, _correction = run_execution.review_request(control, None)
    _private_bytes(Path(control["requestFile"]), encode_run_request(request).encode(), exclusive=True)
    if mode == "malformed":
        _private_bytes(Path(control["resultFile"]), b"{not json", exclusive=True)
        return 0
    document = control["fixture"]["document"]
    if mode == "input_changed":
        (Path(control["cwd"]) / "input.txt").write_text("mutated input\n")
    binding = {"adapter": control["harness"], "taskId": control["taskId"],
               "attemptId": control["attemptId"], "generation": control["generation"]}
    raw, parsed, schema_status = _answer(document, options, mode)
    reason_code, exit_code = _END.get(mode, (None, 0))
    result = RunResult(
        identity=request.identity, harness=control["harness"],
        end=RunEnd(status="error" if mode in _END else "ok", reason_code=reason_code,
                   native_exit_code=exit_code),
        model_started=True,
        configuration=ResultConfiguration(requested=request.configuration),
        native_identity=NativeIdentity(session_id="mock-native"),
        value=None if mode in _END else RunValue(schema_status=schema_status, raw=raw, parsed=parsed),
        tool_evidence=tool_receipt(binding, 1, native_identity={"sessionId": "mock-native"})["toolEvidence"],
        stop_evidence=StopEvidence(native=StopLayer(
            group_state="unknown" if mode == "no_shutdown" else "gone")),
    )
    verdict = {"stopReason": "readonly-budget-exhausted" if mode == "budget" else None,
               "elapsedMs": 200}
    private_json(Path(control["verdictFile"]), verdict, exclusive=True)
    frame = encode_run_result(result)
    if mode == "string_shutdown":
        # A stop fact that is not the exact boolean can no longer travel: hand the
        # collector a wire frame whose native group state is a string and let the
        # strict codec refuse the whole frame.
        corrupted = json.loads(frame)
        corrupted["stopEvidence"]["native"]["groupState"] = "false"
        _private_bytes(Path(control["resultFile"]), canonical_json(corrupted).encode(), exclusive=True)
        return 0
    _private_bytes(Path(control["resultFile"]), frame.encode(), exclusive=True)
    # The retained runner log is the role projection of this exact frame.
    sys.stdout.write(canonical_json(run_execution._review_result(
        decode_run_result(frame), request, verdict)))
    sys.stdout.write("\n")
    return exit_code


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    sys.exit(main(sys.argv[1]))
