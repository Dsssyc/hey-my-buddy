"""Deterministic in-process native review fixture; never calls a model."""
from __future__ import annotations

import json
from types import SimpleNamespace
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import patch

from blackboard.routing.fixtures.router_tool_receipt import tool_receipt
from hey_my_buddy.buddy.roles.router_input import verify as verify_frozen_input
from hey_my_buddy.paths import native_path


def prepare_input(manifest, directory, *, state_dir, deadline):
    from hey_my_buddy.buddy.roles.router_input import digest
    root = directory / "mock-frozen-input"
    native_path(root).mkdir(mode=0o700, parents=True)
    native_path(root / "input.txt").write_text("private service-protocol fixture\n", encoding="utf-8", newline="\n")
    return root, digest(root, deadline=deadline)


def verify_input(manifest, root, expected, *, state_dir, deadline):
    result = verify_frozen_input(None, root, expected, state_dir=state_dir, deadline=deadline)
    result["manifestSha256"] = (manifest or {}).get("manifestSha256")
    return result


def install(testcase, *, path=None, **options):
    """Install an offline native review fixture only within this test process."""
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
    testcase.enterContext(patch.object(DshAdapter, "available", side_effect=lambda: (
        testcase._readonly_fixture.is_file(), "mock fixture missing")))
    testcase.input_prepare = testcase.enterContext(patch(
        "hey_my_buddy.buddy.roles.router_input.prepare", side_effect=prepare_input))
    testcase.input_verify = testcase.enterContext(patch(
        "hey_my_buddy.buddy.roles.router_input.verify", side_effect=verify_input))


def _answer(document, options, mode):
    buddies = document["buddies"]
    chosen = options.get("buddy_id") or buddies[0]["buddyId"]
    evidence = []
    if options.get("evidence") == "foreign":
        evidence = [{"kind": "annotation", "ref": "model-note-not-supplied"}]
    if mode in ("abstain", "abstain_with_evidence"):
        chosen = None
        evidence = [{"kind": "file", "ref": "input.txt"}] if mode == "abstain_with_evidence" else []
    if mode == "out_of_candidate":
        chosen = "dsh:not-a-candidate:model:off"
    answer = {"buddyId": chosen, "note": f"mock select chose {chosen}", "evidence": evidence}
    if mode == "answer_error":
        return "{not json", None, "invalid"
    if mode == "answer_extra":
        answer["policyCheck"] = {"hardConstraints": {"harness": "invented"}}
    elif mode == "answer_bad_evidence":
        answer["evidence"] = ["invalid-reference"]
    elif mode == "answer_escape":
        answer["evidence"] = [{"kind": "file", "ref": "../secret"}]
    valid = mode in ("select_first", "abstain", "abstain_with_evidence", "json_answer", "input_changed")
    if mode == "json_answer":
        return json.dumps(answer), None, "valid"
    return json.dumps(answer), answer, "valid" if valid else "invalid"


_END = {"error": ("call-timeout", 1), "protocol_error": ("invalid-native-result", 1),
        "budget": ("observer-interrupt", 1), "deadline": ("deadline", 1)}


def start(harness, context, request, fixture, options):
    """Freeze the production review request and return its direct native result."""
    from hey_my_buddy.buddy.harnesses.run_contract import (
        CheckedBuddy, CheckedValue, NativeIdentity, ResultBuddy, RunEnd, RunIdentity, RunResult, RunValue, StopEvidence, StopLayer)
    from hey_my_buddy.buddy.roles import run_execution
    from hey_my_buddy.private_dirs import context_root, ensure_private_dir

    assert context.turn is None and context.agent_credential is None
    assert context.cwd == request.cwd
    assert set(request.output_schema["properties"]) == {"buddyId", "note", "evidence"}
    assert request.budget == context.decision_input["budget"]
    identity = RunIdentity(task_id=context.task_id, attempt_id=context.attempt_id,
                           generation=context.generation, invocation_id=uuid.uuid4().hex)
    private = ensure_private_dir(context_root(context, harness) / ("review-" + identity.invocation_id))
    control = {
        "operation": "review", "harness": harness, "privateRoot": str(private),
        "stateDir": context.environment["HMB_STATE_DIR"],
        "sessionRecordPath": context.session_record_path,
        "directory": str(context.directory), "executionLiveFile": str(context.directory / "execution-live.json"),
        "nativeRoot": str(ensure_private_dir(context_root(context, harness) / "review-native")),
        "cwd": request.cwd, "timeoutSeconds": request.budget["timeoutSeconds"],
        "taskId": context.task_id, "attemptId": context.attempt_id, "generation": context.generation,
        "invocationId": identity.invocation_id,
        "spec": {key: context.spec[key] for key in ("provider", "model", "effort")},
        "canCorrect": False,
        "readOnlyRequest": {"prompt": request.prompt, "outputSchema": request.output_schema,
                            "budget": request.budget, "captureEvidence": request.capture_evidence},
    }
    built_request, *_ = run_execution.review_request(control, SimpleNamespace(prepare_run_services=None))
    handle = run_execution.InlineRunHandle(control, identity, grace_seconds=10)
    handle.request = built_request
    mode = options.get("mode", "select_first")
    if mode == "sleep":
        def wait_for_stop():
            handle.cancelled.wait(float(options.get("sleep", "30")))
            handle.exit_code = 1
            handle.done.set()
        handle.thread = threading.Thread(target=wait_for_stop, daemon=True)
        handle.thread.start()
        return handle
    if mode == "input_changed":
        native_path(Path(control["cwd"]) / "input.txt").write_text("mutated input\n", encoding="utf-8", newline="\n")
    if mode == "malformed":
        handle.result = RunResult(
            identity=built_request.identity, harness=harness,
            end=RunEnd(status="error", reason_code="invalid-native-result"),
            stop_evidence=StopEvidence(native=StopLayer(group_state="unknown")),
        )
        handle.verdict = {"stopReason": None, "elapsedMs": 200}
        handle.exit_code = 1
    elif mode not in ("no_output", "string_shutdown"):
        raw, parsed, schema_status = _answer(context.decision_input, options, mode)
        reason_code, exit_code = _END.get(mode, (None, 0))
        binding = {"harness": harness, "taskId": context.task_id,
                   "attemptId": context.attempt_id, "generation": context.generation}
        handle.result = RunResult(
            identity=built_request.identity, harness=harness,
            end=RunEnd(status="error" if mode in _END else "ok", reason_code=reason_code,
                       native_exit_code=exit_code), model_started=True,
            buddy=ResultBuddy(requested=built_request.buddy, checked=CheckedBuddy(
                provider=CheckedValue(value=context.spec["provider"]),
                model=CheckedValue(value=context.spec["model"]), effort=CheckedValue(value=context.spec["effort"]))),
            native_identity=NativeIdentity(session_id="mock-native"),
            value=None if mode in _END else RunValue(schema_status=schema_status, raw=raw, parsed=parsed),
            tool_evidence=tool_receipt(binding, 1, state_dir=Path(built_request.private_state.state_dir), native_identity={"sessionId": "mock-native"})["toolEvidence"],
            stop_evidence=StopEvidence(native=StopLayer(
                group_state="unknown" if mode == "no_shutdown" else "gone")),
        )
        handle.verdict = {"stopReason": "readonly-budget-exhausted" if mode == "budget" else None,
                          "elapsedMs": 200}
        handle.exit_code = exit_code
    else:
        handle.exit_code = 0
    handle.done.set()
    return handle
