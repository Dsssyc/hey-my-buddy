#!/usr/bin/env python3
"""Deterministic generic native envelope; never invokes a model.

Capability injection and input preparation are in-process test patches. The child
only writes the native stdout protocol, exercising real process ownership, logs,
collection and durable Worker receipts. Git manifest verification is deliberately
outside these service mocks and belongs to the real-workspace tests.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from unittest.mock import patch
from fixtures.router_tool_receipt import tool_receipt


def prepare_input(manifest, directory):
    from buddy.router_input import digest

    root = directory / "mock-frozen-input"
    root.mkdir(mode=0o700, parents=True)
    (root / "input.txt").write_text("private service-protocol fixture\n")
    return root, digest(root)


def verify_input(manifest, root, expected):
    from buddy.router_input import digest

    return {"unchanged": digest(root) == expected,
            "manifestSha256": (manifest or {}).get("manifestSha256"),
            "snapshotSha256": expected}


def install(testcase, *, path=None, **options):
    """Inject locally eligible native capability only within this test process."""
    from buddy.adapters.dsh import DshAdapter

    testcase._readonly_fixture = Path(path) if path is not None else Path(__file__).resolve()
    testcase._readonly_options = options
    if getattr(testcase, "_readonly_installed", False):
        testcase.readonly_start.reset_mock()
        testcase.input_prepare.reset_mock()
        testcase.input_verify.reset_mock()
        return
    testcase._readonly_installed = True
    testcase.readonly_start = testcase.enterContext(patch.object(
        DshAdapter, "start_read_only_structured", autospec=True,
        side_effect=lambda _native, context, request: start(
            context, request, testcase._readonly_fixture, testcase._readonly_options),
    ))
    testcase.enterContext(patch.object(DshAdapter, "read_only_structured", True))
    testcase.enterContext(patch.object(DshAdapter, "local_read_only_check", return_value={
        "eligible": True, "reasonCode": None, "reason": None,
        "systemSandbox": False,
        "sameAttemptContinuation": False,
    }))
    testcase.enterContext(patch.object(DshAdapter, "available", side_effect=lambda: (testcase._readonly_fixture.is_file(), "mock fixture missing")))
    testcase.input_prepare = testcase.enterContext(patch("buddy.router_input.prepare", side_effect=prepare_input))
    testcase.input_verify = testcase.enterContext(patch("buddy.router_input.verify", side_effect=verify_input))


def start(context, request, fixture, options):
    from buddy.adapters.base import ProcessHandle, open_logs

    assert context.turn is None and context.agent_credential is None
    assert context.cwd == request.cwd
    assert set(request.output_schema["properties"]) == {"profileId", "reason", "evidence"}
    assert request.budget == context.decision_input["budget"]
    control = context.directory / "mock-readonly.json"
    control.write_text(json.dumps({"document": context.decision_input, "cwd": request.cwd,
                                  "prompt": request.prompt, "schema": request.output_schema,
                                  "budget": request.budget, "options": options,
                                  "binding": {"adapter": context.decision_input["profile"]["adapter"],
                                              "taskId": context.task_id, "attemptId": context.attempt_id,
                                              "generation": context.generation}}))
    control.chmod(0o600)
    stdout, stderr = open_logs(context.log_paths())
    # Never inherit a governed Worker credential into the stand-in child.
    environment = {key: value for key, value in context.environment.items()
                   if not key.startswith(("BUDDY_AGENT_", "BUDDY_WORKER_"))}
    try:
        process = subprocess.Popen([sys.executable, str(fixture), str(control)], cwd=request.cwd,
                                   env=environment, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   start_new_session=True, close_fds=True)
    finally:
        os.close(stdout)
        os.close(stderr)
    return ProcessHandle(process, own_group=True, log_paths=context.log_paths())


def main(control_path):
    control = json.loads(Path(control_path).read_text())
    document, options = control["document"], control["options"]
    mode = options.get("mode", "select_first")
    if mode == "sleep":
        time.sleep(float(options.get("sleep", "30")))
        return 0
    if mode == "no_output":
        return 0
    if mode == "malformed":
        sys.stdout.write("{not json")
        return 0
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
        answer = "{not json"
    elif mode == "answer_extra":
        answer["policyCheck"] = {"hardConstraints": {"adapter": "invented"}}
    elif mode == "answer_bad_evidence":
        answer["evidence"] = ["card-id"]
    elif mode == "answer_escape":
        answer["evidence"] = [{"kind": "file", "ref": "../secret"}]
    if mode == "input_changed":
        (Path(control["cwd"]) / "input.txt").write_text("mutated input\n")
    shutdown = "false" if mode == "string_shutdown" else mode != "no_shutdown"
    envelope = {"status": "ok", "rawAnswer": json.dumps(answer) if mode == "json_answer" else answer,
                "processState": {"shutdownConfirmed": shutdown}, "requested": document["profile"],
                "resolved": {**document["profile"], "reasoningEffort": document["profile"]["effort"]},
                **tool_receipt(control["binding"], 1, native_identity={"sessionId": "mock-native"}),
                "usage": {"elapsedMs": 200, "toolCalls": 1, "bytesRead": 33}}
    codes = {"error": "call-timeout", "protocol_error": "invalid-native-result",
             "budget": "readonly-budget-exhausted", "deadline": "deadline"}
    if mode in codes:
        envelope.update(status="error", code=codes[mode])
    json.dump(envelope, sys.stdout)
    sys.stdout.write("\n")
    return 1 if mode in codes else 0


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    sys.exit(main(sys.argv[1]))
