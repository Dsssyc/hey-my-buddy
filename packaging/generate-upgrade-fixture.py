#!/usr/bin/env python3
"""Generate schema-28 upgrade-base stock using the frozen installed base source.

No product installation, daemon, native harness or model is started. The base
named service operations and current store writers run in process; native
discovery, execution receipts and absent-process evidence are synthetic. All work directories are retained. The export includes
fixed Git objects, workspace sidecars and the hash preimages needed for private
path binding. Tests restore that text stock; they never execute this generator.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import importlib.util
import importlib.metadata
import itertools
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import uuid

BASE = "fd9f04e828ea49a5f2259cdcae0d71401e5c0d84"
BASE_VERSION = "0.51.0"
BASE_SCHEMA = 28
# An existing five-file repository commit keeps workspace capture bounded.
# This is the workspace input, never the source used to create the database.
WORKSPACE_INPUT = "122ec20edcdeb97cc655ae3993c402c2c9c966a3"
AT = "2026-01-01T00:00:00.000Z"
PROFILES = ["codex:openai:fixture-alpha:high", "codex:openai:fixture-beta:high"]
THIRD_CANDIDATE = "codex:openai:fixture-gamma:high"
LEGACY_PRIVATE_FILES = {
    **{f"harnesses/{harness}/goals/fixture-retired/native-history.jsonl":
       '{"retired":"private native history"}\n' for harness in ("codex", "claude", "zcode", "dsh")},
    "decisions/fixture-retired/request.json": '{"retired":"decision"}\n',
    "legacy-worker-cleanup.lock": "retired cleanup lock\n",
    "worker.log": "retired worker log\n",
}


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(argv, **kwargs):
    subprocess.run(argv, check=True, **kwargs)


def private_environment(work):
    # Drop every inherited product binding, plus provider/harness credentials.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("HMB_", "BUDDY_", "OPENAI_", "ANTHROPIC_", "DEEPSEEK_", "ZAI_", "CODEX_", "CLAUDE_", "C2_", "GIT_"))
           and key not in {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH"}}
    env.update(HMB_STATE_DIR=str(work / "state"), HMB_RUNTIME_ROOT=str(work / "runtime"),
               HMB_DEV_SOURCE="1", HMB_DEBUG="1", UV_CACHE_DIR=str(work.parent / "uv-cache"),
               UV_PYTHON_DOWNLOADS="never", PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(work / "scratch"))
    return env


def generate(args):
    root = Path(__file__).resolve().parents[1]
    retained = args.work_dir.resolve()
    if not retained.is_relative_to(root / "tmp"):
        raise ValueError("work-dir must be a new directory inside this checkout's ignored tmp/")
    if retained.exists():
        raise ValueError("work-dir must not already exist")
    retained.parent.mkdir(parents=True, exist_ok=True)
    # Frozen writers include source paths in IDs, refs and Git commit messages.
    # Generate at one private canonical path, then retain the whole work tree
    # at the caller's fresh destination. Each run still starts from empty stock.
    work = root / "tmp" / ".upgrade-fixture-generation-active"
    work.mkdir(exist_ok=False)
    try:
        generate_in_canonical_slot(args, root, work)
    finally:
        work.rename(retained)


def generate_in_canonical_slot(args, root, work):
    (work / "scratch").mkdir()
    (work / "home").mkdir(mode=0o700)
    source = work / "base"
    source.mkdir()
    archive = work / "base.tar"
    with archive.open("wb") as stream:
        run(["git", "archive", BASE], cwd=root, stdout=stream)
    with tarfile.open(archive) as stream:
        stream.extractall(source, filter="data")
    env = private_environment(work)
    env.update(HOME=str(work / "home"), CODEX_HOME=str(work / "home/codex"),
               CLAUDE_CONFIG_DIR=str(work / "home/claude"), DSH_HOME=str(work / "home/dsh"),
               ZCODE_DATA_BASE_DIR=str(work / "home/zcode"), XDG_CONFIG_HOME=str(work / "home/config"),
               XDG_DATA_HOME=str(work / "home/data"), XDG_CACHE_HOME=str(work / "home/cache"))
    if args.python is None:
        run(["uv", "sync", "--frozen", "--no-install-project", "--link-mode", "copy",
             *(["--offline"] if args.offline else []), "--project", str(source)], cwd=root, env=env)
    # Import an existing commit into a private detached repository. No branch
    # or tag is created/moved; the allocated repository is read only.
    repo = work / "repository"
    run(["git", "init", "--quiet", str(repo)], env=env)
    run(["git", "-C", str(repo), "fetch", "--quiet", "--no-tags", str(root), WORKSPACE_INPUT], env=env)
    (repo / ".git/HEAD").write_bytes((WORKSPACE_INPUT + "\n").encode("utf-8"))
    run(["git", "-C", str(repo), "read-tree", WORKSPACE_INPUT], env=env)
    run(["git", "-C", str(repo), "checkout-index", "--all"], env=env)
    for label in ("delivered", "accepted", "failed", "cancelled",
                  "partial-failed", "partial-interrupted", "rejected-turn", "rejected-seal"):
        run(["git", "-C", str(repo), "worktree", "add", "--quiet", "--detach",
             str(work / "workspaces" / label), WORKSPACE_INPUT], env=env)
    env["PYTHONPATH"] = str(source / "src")
    interpreter = [str(args.python.absolute())] if args.python else ["uv", "run", "--no-sync", "--project", str(source), "python"]
    run([*interpreter, str(Path(__file__).resolve()),
         "--populate", "--work-dir", str(work), "--output-dir", str(args.output_dir.resolve()),
         *(["--python", str(args.python.absolute())] if args.python else [])], cwd=root, env=env)


def populate(args):
    # Import the frozen base package explicitly, never the changing checkout.
    work = args.work_dir.resolve()
    source = work / "base"
    package = importlib.util.spec_from_file_location("hey_my_buddy", source / "src/hey_my_buddy/__init__.py",
                submodule_search_locations=[str(source / "src/hey_my_buddy")])
    module = importlib.util.module_from_spec(package)
    sys.modules["hey_my_buddy"] = module
    package.loader.exec_module(module)
    from unittest.mock import patch
    from hey_my_buddy.blackboard.store import db as db_module
    from hey_my_buddy.blackboard.service import harness_health as health_module
    from hey_my_buddy.buddy.harnesses.codex.adapter import CodexAdapter
    from hey_my_buddy.buddy.harnesses import inquiry_bridge
    from hey_my_buddy.buddy.harnesses.inquiry_bridge import InquiryBridge
    from hey_my_buddy.blackboard.service.service import BoardService, call_operation
    from hey_my_buddy.blackboard.service.live_registry import LiveRegistry
    from hey_my_buddy.blackboard.store.store import BoardStore
    from hey_my_buddy.blackboard.catalog import catalog_store, catalog as catalog_module
    from hey_my_buddy.blackboard.routing import quota_routing
    from hey_my_buddy.blackboard.tasks import workspace, workspace_identity, macro_task_deletion, reminders
    from hey_my_buddy.protocol.execution import RECEIPT, execution_directory, fsync_json, receipt_reference
    from hey_my_buddy.private_dirs import ensure_private_dir
    from hey_my_buddy.protocol.tool_evidence import ToolEventEvidence
    from hey_my_buddy.buddy.harnesses.session_record import SessionRecord
    from hey_my_buddy.protocol.session_content import MessageChunk, TextContent
    from hey_my_buddy.errors import BoardError

    assert Path(db_module.__file__).resolve() == source / "src/hey_my_buddy/blackboard/store/db.py"
    assert db_module.SCHEMA_VERSION == BASE_SCHEMA
    catalog = {"source": "fixture:upgrade-base", "harnessVersion": "fixture-only",
               "providerVersion": "fixture-provider-version",
               "discoveredAt": AT, "discoveries": [{"harness": "codex", "status": "complete", "accountStatus": "confirmed"}],
               "providers": [{"harness": "codex", "provider": "openai", "efforts": ["high"],
                              "models": [{"id": "fixture-alpha", "efforts": ["high"]},
                                         {"id": "fixture-beta", "efforts": ["high"]},
                                         {"id": "fixture-gamma", "efforts": ["high"]}]}]}
    catalog_file = work / "catalog.json"
    catalog_file.write_bytes((json_text(catalog) + "\n").encode("utf-8"))
    os.environ["HMB_MODEL_CATALOG_FILE"] = str(catalog_file)
    operations = []
    ids = {}
    task_only_stock = {}
    inquiry_stock = {}
    counter = itertools.count(1)
    preimages = {}
    native_sha256 = hashlib.sha256

    def recorded_sha256(data=b"", **kwargs):
        # Observe the real base digest; do not alter any hash or its validation.
        result = native_sha256(data, **kwargs)
        caller = sys._getframe(1)
        ascii_json = caller.f_globals.get("__name__") in {"hey_my_buddy.blackboard.tasks.workspace", "hey_my_buddy.blackboard.tasks.workspace_identity"}
        preimages[result.hexdigest()] = (bytes(data).decode("utf-8"), ascii_json)
        return result

    def fixture_identity(path):
        # The released identity includes the directory inode. Use a fixed
        # synthetic inode while generating stock; load_fixture rebinds every
        # identity to the restored directory's real path and inode.
        directory = Path(path).resolve(strict=True)
        fixture_path = str(directory).replace(str(work), "<fixture-root>")
        payload = json.dumps([fixture_path, 0], sort_keys=True,
                             separators=(",", ":"), ensure_ascii=True).encode()
        return hashlib.sha256(payload).hexdigest()

    @contextmanager
    def retained_directory(*, prefix="tmp", suffix="", dir=None, **kwargs):
        yield tempfile.mkdtemp(prefix=prefix, suffix=suffix, dir=dir or work / "scratch")

    with ExitStack() as stack:
        # Old workspace operations allocate private indexes. Retain them as the
        # task requires; no temporary-directory cleanup is performed.
        stack.enter_context(patch("tempfile.TemporaryDirectory", retained_directory))
        hashing = stack.enter_context(ExitStack())
        hashing.enter_context(patch.object(hashlib, "sha256", recorded_sha256))
        stack.enter_context(patch.object(workspace_identity, "stable_identity", fixture_identity))
        # Stable fake secrets and IDs are inputs to the base public operations.
        stack.enter_context(patch("uuid.uuid4", side_effect=lambda: uuid.UUID(int=next(counter))))
        stack.enter_context(patch("secrets.token_hex", side_effect=lambda n=32: "ab" * n))
        stack.enter_context(patch.object(db_module, "utc_now", return_value=AT))
        stack.enter_context(patch.object(health_module, "utc_now", return_value=AT))
        stack.enter_context(patch.object(health_module, "_later", return_value="2026-01-01T01:00:00.000Z"))
        stack.enter_context(patch.object(inquiry_bridge, "_now", return_value=AT))
        # Replace external native discovery only. The base health/catalog writers,
        # validators and certificate lookup remain the real implementations.
        stack.enter_context(patch.object(health_module, "_snapshot", side_effect=lambda name, manual, environment:
            {"harness": name, "manualPath": None, "managerRecords": [], "candidates": [], "fixture": True}))
        stack.enter_context(patch.object(health_module, "_discover", side_effect=lambda name, manual, environment:
            {"harness": name, "status": "ready", "available": True, "version": "0.157.0",
             "command": ["fixture-" + name], "accountStatus": "confirmed", "fixture": True}))
        stack.enter_context(patch.object(CodexAdapter, "discover_models", return_value=catalog))
        stack.enter_context(patch("hey_my_buddy.buddy.harnesses.codex.account_probe.read", return_value=None))
        clock = [AT]
        store = BoardStore(work / "state", clock=lambda: clock[0])
        store.initialize()
        service = BoardService(store, token="fixture-service-token", control={},
                               on_stop=lambda p: p, on_restart=lambda p: p, live_registry=LiveRegistry())
        service.register_console_authority("fixture-console", "f" * 24)

        def call(name, params, *, human=False):
            print("operation:", name, params.get("requestId", params.get("runId", "")), flush=True)
            authority = {"consoleAuthority": {"sessionId": "fixture-console"}} if human else {}
            result = call_operation(service, name, {**params, **authority})
            operations.append({"operation": name, "params": params, "response": result})
            return result

        def internal_call(name, params):
            result = getattr(store.workflow, name)(params)
            operations.append({"operation": "workflow." + name, "params": params, "response": result})
            return result

        def publish(**changes):
            label = "publish-" + str(len(operations))
            return call("user_policy_publish", {"commandId": label, **changes}, human=True)

        def admit(nonce, task_id):
            response = service.admit_execution(nonce, task_id=task_id)
            assert response.get("claim") is not None, response
            operations.append({"operation": "service.admit_execution", "params": {"nonce": nonce, "taskId": task_id},
                               "response": {"reason": response.get("reason")}})
            return response["claim"]

        def report(claim, nonce, *, status, result, error=None):
            attempt = claim["attempt"]
            if claim.get("turn") is not None:
                result = {"workspaceManifest": claim["turn"]["input"]["executionWorkspace"], **result}
            directory = execution_directory(claim, work / "state")
            fsync_json(directory / RECEIPT, {"taskId": claim["task"]["taskId"],
                "attemptId": attempt["attemptId"], "generation": attempt["generation"], "createdAt": AT,
                "report": {"status": status, "shutdownConfirmed": True,
                           "exitCode": 0 if status == "ok" else 1, "result": result,
                           **({"error": error} if error is not None else {})}})
            if claim["sessionRecordPath"] is not None:
                record = SessionRecord(Path(claim["sessionRecordPath"]), attempt["attemptId"])
                record.append(MessageChunk(session_update="agent_message_chunk",
                    content=TextContent(text="Synthetic fixture display record; no native execution.")))
                record.close()
                assert record.failure is None, record.failure
            recorded_sha256((directory / RECEIPT).read_bytes())
            return call("execution_result", {"attemptId": attempt["attemptId"],
                "generation": attempt["generation"], "nonce": nonce, "receipt": receipt_reference(directory)})

        def completed_record(claim, label):
            attempt, turn = claim["attempt"], claim["turn"]
            record = {"version": 1, "taskId": claim["task"]["taskId"], "attemptId": attempt["attemptId"],
                    "generation": attempt["generation"], "turnId": turn["turnId"], "resumeMode": turn["resumeMode"],
                    "previousSessionId": turn["input"]["previousSessionId"], "sessionId": "fixture-session-" + label,
                    "promptSha256": "a" * 64, "inputSha256": turn["inputSha256"],
                    "outcome": {"disposition": "completed", "summary": "模拟结果：无模型执行。", "remaining": [],
                                "decisions": [], "artifacts": [], "request": None},
                    "provenance": {"harness": "codex", "turnEnd": "completed", "outputSchemaValidated": True,
                                   "finalMessageCompleted": True, "nativeTurnStarted": True, "nativeTurnCompleted": True,
                                   "nativeThreadId": "fixture-session-" + label, "nativeTurnId": "fixture-turn-" + label,
                                   "finalItemId": "fixture-item", "eventSeq": 2}}
            if label == "delivered":
                record["outcomeTruncated"] = {}
            elif label == "accepted":
                record["outcomeTruncated"] = {"remaining": {"droppedItems": 5, "shortenedItems": 1}}
            return record

        def answer_router(claim, outcome, *, selected_buddy=PROFILES[0]):
            document = claim["decisionInput"]
            identity = {"sessionId": "fixture-router-" + outcome}
            binding = {"harness": "codex", "taskId": claim["task"]["taskId"],
                       "attemptId": claim["attempt"]["attemptId"], "generation": claim["attempt"]["generation"]}
            result = {"status": "ok", "operation": "select", "nativeIdentity": identity,
                      "toolEvidence": ToolEventEvidence(binding).finish([identity], True),
                      "usage": {"elapsedMs": 100, "toolCalls": 0},
                      "stopEvidence": {"shutdownConfirmed": True, "native": {"shutdownConfirmed": True}},
                      "inputVerification": {"unchanged": True, "snapshotSha256": "fixture-digest",
                          "manifestSha256": (document.get("executionWorkspace") or {}).get("manifestSha256")},
                      "decision": {"buddyId": selected_buddy, "note": "fixture routing answer", "evidence": []}}
            if outcome == "no_answer":
                result.update(status="error", code="fixture-router-no-answer")
            report(claim, nonce, status="ok" if outcome == "answered" else "failed", result=result)

        call("harness_set", {"harness": "codex", "path": None})
        call("model_catalog_refresh", {"requestId": "fixture-catalog"})
        publish(buddySettings=[{"buddyId": profile, "enabled": True} for profile in [*PROFILES, THIRD_CANDIDATE]])
        publish(preferenceChanges=[{"buddyId": PROFILES[0], "mode": "prefer", "note": "fixture preference"},
                                   {"buddyId": PROFILES[1], "mode": "exclude", "note": "fixture exclusion"}],
                modelServiceConcurrency=[{"harness": "codex", "provider": "openai", "model": "fixture-alpha", "limit": 3}])
        publish(configuration={"routerBuddyIds": PROFILES,
                                        "routerRetryIntervalSeconds": 47,
                                        "defaultRoutingMode": "review", "routingBudget": "deep"},
                modelServiceNoteChanges=[{"harness": "codex", "provider": "openai", "model": "fixture-alpha",
                                          "text": "升级夹具的模型备注；应保留。"}])
        # Both candidates must be legal to exercise the Router rather than the
        # sole-candidate shortcut. One Router keeps a no-answer terminal.
        publish(preferenceChanges=[{"buddyId": PROFILES[1], "mode": None}],
                configuration={"routerBuddyIds": [PROFILES[0]]})
        nonce = "fixture-nonce-00000000000000000000"
        for outcome in ("answered", "no_answer"):
            selected = call("selection_request", {"requestId": "fixture-routing-" + outcome,
                                                 "task": "fixture routing " + outcome})
            claim = admit(nonce, selected["runId"])
            answer_router(claim, outcome)
        # The first Router answer becomes ineligible while its admitted call is
        # running. Two legal coding candidates remain, so the released writer
        # records a second, genuinely dispatched Router round for this decision.
        clock[0] = "2026-01-01T00:00:48.000Z"  # Let the prior no-answer's 47-second skip window expire.
        selected = call("selection_request", {"requestId": "fixture-routing-retry",
                                             "task": "fixture routing retry"})
        first_claim = admit(nonce, selected["runId"])
        publish(preferenceChanges=[{"buddyId": PROFILES[0], "mode": "exclude", "note": "fixture changed bound"}],
                configuration={"routerBuddyIds": [PROFILES[1]]})
        answer_router(first_claim, "answered")
        with store.db.read() as connection:
            retry = connection.execute("SELECT task_id,requested_json FROM decision_requests WHERE request_id=?",
                                       ("fixture-routing-retry",)).fetchone()
            assert json.loads(retry["requested_json"])["selectionRetry"] is True
            assert retry["task_id"] != selected["runId"]
        second_claim = admit(nonce, retry["task_id"])
        answer_router(second_claim, "answered", selected_buddy=PROFILES[1])
        # fd9f04e8's writer cannot emit this pre-0.46 dispatch-document form.
        # Preserve one historical exclusion representation as explicit stock,
        # updating its dependent digest and the mutable current input together.
        with store.db.write() as connection:
            task_id = retry["task_id"]
            dispatch = connection.execute("SELECT document_json FROM router_dispatches WHERE task_id=?",
                                          (task_id,)).fetchone()
            document = json.loads(dispatch["document_json"])
            excluded = document["routingBasis"]["excludedBuddies"]
            assert len(excluded) == 1 and excluded[0]["buddyId"] == PROFILES[0]
            excluded[0].pop("note")
            excluded[0]["reason"] = ""
            encoded = json_text(document)
            input_sha = digest(encoded.encode("utf-8"))
            connection.execute("UPDATE router_dispatches SET document_json=?,input_sha256=? WHERE task_id=?",
                               (encoded, input_sha, task_id))
            connection.execute("UPDATE decision_requests SET input_json=?,input_sha256=? WHERE request_id=?",
                               (encoded, input_sha, "fixture-routing-retry"))
            started = connection.execute("SELECT seq,payload_json FROM events WHERE kind='decision.started' "
                                         "AND json_extract(payload_json,'$.attemptId')=?",
                                         (second_claim["attempt"]["attemptId"],)).fetchone()
            assert started is not None
            payload = json.loads(started["payload_json"])
            payload["inputSha256"] = input_sha
            connection.execute("UPDATE events SET payload_json=? WHERE seq=?", (json_text(payload), started["seq"]))

        # The released writer omitted router_requests for a decision admitted
        # with zero or one legal candidate. These are direct, task-only rounds.
        publish(preferenceChanges=[{"buddyId": PROFILES[0], "mode": "exclude", "note": "fixture initial zero"},
                                   {"buddyId": PROFILES[1], "mode": "exclude", "note": "fixture initial zero"},
                                   {"buddyId": THIRD_CANDIDATE, "mode": "exclude", "note": "fixture initial zero"}])
        initial_zero = call("selection_request", {"requestId": "fixture-initial-zero",
                                                  "task": "fixture initial zero candidates"})
        assert initial_zero["status"] == "needs-host" and initial_zero["runId"] is None
        task_only_stock["initialZero"] = initial_zero["decisionId"]
        publish(preferenceChanges=[{"buddyId": PROFILES[0], "mode": None}])
        initial_sole = call("selection_request", {"requestId": "fixture-initial-sole",
                                                  "task": "fixture initial sole candidate"})
        assert initial_sole["status"] == "completed" and initial_sole["runId"] is None
        task_only_stock["initialSole"] = initial_sole["decisionId"]

        # A selected answer can become stale while its Router call is running.
        # With zero or one newly legal candidate the released retry writer
        # records selectionRetry, but dispatches no second Router task.
        for label, excluded in (("retryZero", [*PROFILES, THIRD_CANDIDATE]),
                                ("retrySole", [*PROFILES])):
            publish(preferenceChanges=[{"buddyId": buddy_id, "mode": None}
                                       for buddy_id in [*PROFILES, THIRD_CANDIDATE]])
            request_id = "fixture-" + label.lower()
            selected = call("selection_request", {"requestId": request_id,
                                                 "task": "fixture " + label})
            first_claim = admit(nonce, selected["runId"])
            publish(preferenceChanges=[{"buddyId": buddy_id, "mode": "exclude",
                                        "note": "fixture " + label} for buddy_id in excluded])
            answer_router(first_claim, "answered")
            with store.db.read() as connection:
                row = connection.execute("SELECT task_id,requested_json FROM decision_requests WHERE decision_id=?",
                                         (selected["decisionId"],)).fetchone()
                assert json.loads(row["requested_json"])["selectionRetry"] is True
                assert row["task_id"] == (None if label == "retrySole" else selected["runId"])
                assert connection.execute("SELECT COUNT(*) FROM router_dispatches WHERE decision_id=?",
                                          (selected["decisionId"],)).fetchone()[0] == 1
            task_only_stock[label] = selected["decisionId"]

        publish(preferenceChanges=[{"buddyId": PROFILES[0], "mode": "prefer", "note": "fixture preference"}],
                configuration={"routerBuddyIds": PROFILES})
        publish(preferenceChanges=[{"buddyId": PROFILES[1], "mode": "exclude", "note": "fixture exclusion"}],
                configuration={"routerBuddyIds": PROFILES})
        for label in ("delivered", "accepted", "failed", "cancelled"):
            submitted = call("workflow_submit", {"requestId": "fixture-" + label, "hostId": "fixture-host",
                "submissionToken": "fixture-submission-token-" + label, "task": "fixture " + label,
                "harness": "codex", "provider": "openai", "model": "fixture-alpha", "effort": "high",
                "cwd": str(work / "workspaces" / label), "executionWorkspace": {"kind": "existing", "access": "full", "accessNote": "Isolated fixture stock generation", "writeScope": ["fixture.txt"]}
                    if label in ("delivered", "failed") else {"kind": "existing", "access": "read"},
                **({"macroTask": {"title": "Worker 权限档位升级夹具"}} if not ids else {"macroTaskOf": ids["delivered"]})})
            run_id = submitted["runId"]
            ids[label] = run_id
            control = submitted["control"]
            if label == "cancelled":
                call("workflow_cancel", {"runId": run_id, "commandId": "cancel-fixture", "note": "fixture only", **control})
                continue
            claim = admit(nonce, run_id)
            attempt, turn = claim["attempt"], claim["turn"]
            if label == "failed":
                # Exercise the complete current scope-failure record as well as
                # the native quota refusal. Restore the site after recording it.
                root = work / "workspaces" / label
                forbidden = root / "outside.txt"
                forbidden.write_bytes(b"fixture unauthorized change\n")
                try:
                    workspace.seal(work / "state", turn["input"]["executionWorkspace"], run_id, attempt["attemptId"])
                except BoardError as error:
                    assert error.code == "WORKSPACE_SCOPE_VIOLATION", error
                else:
                    raise AssertionError("Expected a scope failure")
                report(claim, nonce, status="failed", result={"status": "error", "code": "fixture-failed",
                                                              "modelStarted": True,
                                                              "quotaFailure": {"code": "quota-exceeded", "provider": "openai"},
                                                              "stopEvidence": {"shutdownConfirmed": True}})
                view = call("workflow_get", {"runId": run_id})
                assert view["state"] == "awaiting-host", view
                conflict = view["workspaceConflicts"][0]
                call("workflow_workspace_resolve", {"runId": run_id, "commandId": "restore-fixture",
                     "expectedRevision": view["revision"], "conflictId": conflict["conflictId"],
                     "observedFingerprint": conflict["observedFingerprint"], "action": "restore",
                     "paths": ["outside.txt"], **control})
                call("workflow_cancel", {"runId": run_id, "commandId": "close-failed-fixture",
                     "note": "Close the resolved fixture Host boundary", **control})
                call("workflow_conclude", {"runId": run_id, "note": "Fixture Host concluded failed work", **control})
                continue
            manifest = turn["input"]["executionWorkspace"]
            if label == "delivered":
                (work / "workspaces" / label / "fixture.txt").write_bytes(b"fixture sealed change\n")
            sealed = workspace.seal(work / "state", manifest, run_id, attempt["attemptId"])
            record = completed_record(claim, label)
            turn_path = None
            if label == "delivered":
                turn_path = execution_directory(claim, work / "state") / "turn-result.json"
                # Synthetic native output: avoid the base atomic file writer's
                # temporary UUID, which would renumber all existing stock IDs.
                ensure_private_dir(turn_path.parent)
                turn_path.write_bytes(json_text(record).encode("utf-8"))
                turn_path.chmod(0o600)
            report(claim, nonce, status="ok", result={"status": "ok", "turn": record, "workspaceSeal": sealed,
                **({"turnResultPath": str(turn_path)} if turn_path is not None else {}),
                "processState": {"shutdownConfirmed": True}, "fixture": True})
            view = call("workflow_get", {"runId": run_id})
            assert view["state"] == "delivered", view
            if label == "accepted":
                call("workflow_accept", {"runId": run_id, "artifactId": view["finalArtifactId"],
                    "note": "fixture accepted", "notRequired": "Read-only fixture has no separate target",
                    "keepCheckout": True, **control})
            elif label == "delivered":
                call("workflow_conclude", {"runId": run_id, "note": "Fixture Host concluded the delivered work", **control})

        # Preserve this original unavailable question as the first message row.
        # The console authority authors it: a current-form author pair on the
        # oldest row, beside the service-authored stock below.
        original = call("inquiry_observe", {"runId": ids["delivered"], "inquiryId": "fixture-inquiry",
                     "question": "fixture question after the execution ended"}, human=True)
        with store.db.read() as connection:
            first_message = connection.execute(
                "SELECT message_id,attempt_id FROM messages WHERE inquiry_id='fixture-inquiry'").fetchone()
        inquiry_stock["existingUnavailable"] = {"taskId": ids["delivered"],
            "attemptId": first_message["attempt_id"], "inquiryId": "fixture-inquiry",
            "messageId": first_message["message_id"]}

        # Additional base-writer stock for the three forms absent from the first
        # four runs. These IDs are deliberately separate from stableIds.
        stock_ids = {}

        # A delegation-authority run is the current form for lead-owned work. Its
        # attempt credential authors one inquiry while the attempt is live, which
        # settles with the attempt like every other question.
        lead = call("workflow_submit", {"requestId": "fixture-lead", "hostId": "fixture-host",
            "submissionToken": "fixture-submission-token-lead", "task": "fixture lead-authored inquiry",
            "harness": "codex", "provider": "openai", "model": "fixture-alpha", "effort": "high",
            "cwd": str(work / "workspaces" / "cancelled"),
            "executionWorkspace": {"kind": "existing", "access": "full",
                                   "accessNote": "Fixture lead stock generation"},
            "delegationAuthority": True, "macroTaskOf": ids["delivered"]})
        stock_ids["lead"] = lead["runId"]
        lead_claim = admit(nonce, lead["runId"])
        owned = call("workflow_submit", {"requestId": "fixture-lead-owned",
            "submissionToken": "fixture-submission-token-owned", "task": "fixture Lead-admitted work",
            "harness": "codex", "provider": "openai", "model": "fixture-alpha", "effort": "high",
            "cwd": str(work / "workspaces" / "accepted"),
            "executionWorkspace": {"kind": "existing", "access": "read"},
            "credential": lead_claim["agentCredential"]})
        stock_ids["leadOwned"] = owned["runId"]
        call("workflow_cancel", {"runId": owned["runId"], "note": "Settle the Lead-admitted stock",
             "credential": lead_claim["agentCredential"]})
        call("workflow_conclude", {"runId": owned["runId"], "note": "Lead concludes cancelled fixture work",
             "credential": lead_claim["agentCredential"]})
        call("inquiry_observe", {"runId": lead["runId"], "inquiryId": "fixture-lead-inquiry",
             "question": "fixture lead question", "credential": lead_claim["agentCredential"]})
        with store.db.read() as connection:
            lead_message = connection.execute(
                "SELECT message_id FROM messages WHERE inquiry_id='fixture-lead-inquiry'").fetchone()
        inquiry_stock["leadAuthored"] = {"taskId": lead["runId"],
            "attemptId": lead_claim["attempt"]["attemptId"], "inquiryId": "fixture-lead-inquiry",
            "messageId": lead_message["message_id"]}
        lead_manifest = lead_claim["turn"]["input"]["executionWorkspace"]
        lead_seal = workspace.seal(work / "state", lead_manifest, lead["runId"],
                                   lead_claim["attempt"]["attemptId"])
        report(lead_claim, nonce, status="ok", result={"status": "ok",
            "turn": completed_record(lead_claim, "lead"), "workspaceSeal": lead_seal,
            "processState": {"shutdownConfirmed": True}})
        lead_view = call("workflow_get", {"runId": lead["runId"]})
        call("workflow_accept", {"runId": lead["runId"], "artifactId": lead_view["finalArtifactId"],
            "note": "fixture lead accepted", "notRequired": "Full-access fixture has no separate target",
            "keepCheckout": True, **lead["control"]})
        adopted = call("workflow_submit", {"requestId": "fixture-adopted", "hostId": "fixture-host",
            "submissionToken": "fixture-submission-token-adopted", "task": "fixture adopted scope site",
            "harness": "codex", "provider": "openai", "model": "fixture-alpha", "effort": "high",
            "cwd": str(work / "repository"),
            "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["README.md"]},
            "macroTaskOf": ids["delivered"]})
        stock_ids["adopted"] = adopted["runId"]
        adopted_claim = admit(nonce, adopted["runId"])
        adopted_manifest = adopted_claim["turn"]["input"]["executionWorkspace"]
        adopted_checkout = Path(adopted_manifest["path"])
        (adopted_checkout / "README.md").write_bytes(b"fixture authorized change\n")
        (adopted_checkout / "outside.txt").write_bytes(b"fixture adopted change\n")
        try:
            workspace.seal(work / "state", adopted_manifest, adopted["runId"], adopted_claim["attempt"]["attemptId"])
        except BoardError as error:
            assert error.code == "WORKSPACE_SCOPE_VIOLATION", error
        else:
            raise AssertionError("Expected an adoptable scope failure")
        seal_error = "WORKSPACE_SCOPE_VIOLATION: managed changes outside the declared write scope"
        report(adopted_claim, nonce, status="failed", error=seal_error, result={
            "status": "failed", "workspaceSealError": seal_error,
            "turn": completed_record(adopted_claim, "adopted"), "processState": {"shutdownConfirmed": True}})
        adopted_view = call("workflow_get", {"runId": adopted["runId"]})
        adopted_conflict = adopted_view["workspaceConflicts"][0]
        resolved = call("workflow_workspace_resolve", {"runId": adopted["runId"],
            "commandId": "adopt-fixture", "expectedRevision": adopted_view["revision"],
            "conflictId": adopted_conflict["conflictId"],
            "observedFingerprint": adopted_conflict["observedFingerprint"], "action": "adopt",
            "note": "Fixture Host adopts the observed site", **adopted["control"]})
        assert resolved["delivered"] and resolved["delivery"]["resolvedFingerprint"]
        adopted_view = call("workflow_get", {"runId": adopted["runId"]})
        adopted_accepted = call("workflow_accept", {"runId": adopted["runId"],
            "artifactId": adopted_view["finalArtifactId"], "note": "fixture adopted acceptance",
            "notRequired": "Adopted fixture site needs no separate target", "keepCheckout": True,
            **adopted["control"]})
        adopted_plan = internal_call("cleanup_plan", {"runId": adopted["runId"],
            "commandId": "plan-adopted-fixture", "expectedRevision": adopted_accepted["revision"],
            **adopted["control"]})
        assert adopted_plan["plan"]["state"] == "planned"

        verified = call("workflow_submit", {"requestId": "fixture-verified", "hostId": "fixture-host",
            "submissionToken": "fixture-submission-token-verified", "task": "fixture verified integration",
            "harness": "codex", "provider": "openai", "model": "fixture-alpha", "effort": "high",
            "cwd": str(work / "repository"),
            "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["README.md"]},
            "macroTaskOf": ids["delivered"]})
        stock_ids["verified"] = verified["runId"]
        verified_claim = admit(nonce, verified["runId"])
        verified_manifest = verified_claim["turn"]["input"]["executionWorkspace"]
        (Path(verified_manifest["path"]) / "README.md").write_bytes(b"fixture verified integration\n")
        verified_seal = workspace.seal(work / "state", verified_manifest, verified["runId"],
                                         verified_claim["attempt"]["attemptId"])
        report(verified_claim, nonce, status="ok", result={"status": "ok",
            "turn": completed_record(verified_claim, "verified"), "workspaceSeal": verified_seal,
            "processState": {"shutdownConfirmed": True}})
        verified_view = call("workflow_get", {"runId": verified["runId"]})
        assert verified_view["state"] == "delivered"
        artifact = next(item for item in verified_view["artifacts"] if item["kind"] == "output")
        target = work / "workspaces" / "cancelled"
        before = subprocess.check_output(["git", "-C", str(target), "rev-parse", "HEAD"],
                                         text=True, encoding="utf-8").strip()
        run(["git", "-C", str(target), "apply", "--binary", artifact["diffPath"]])
        (target / "README.md").write_bytes(b"fixture adjusted integration\n")
        (target / "fixture-host.txt").write_bytes(b"fixture Host-owned target path\n")
        run(["git", "-C", str(target), "add", "README.md", "fixture-host.txt"])
        commit_env = {**os.environ, "GIT_AUTHOR_DATE": "2026-01-01T00:00:00 +0000",
                      "GIT_COMMITTER_DATE": "2026-01-01T00:00:00 +0000"}
        run(["git", "-C", str(target), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "commit", "--quiet", "-m", "Fixture integration"], env=commit_env)
        after = subprocess.check_output(["git", "-C", str(target), "rev-parse", "HEAD"],
                                        text=True, encoding="utf-8").strip()
        run(["git", "-C", str(work / "repository"), "update-ref", "refs/hey-my-buddy/fixture-target", after])
        verified_accepted = call("workflow_accept", {"runId": verified["runId"],
            "artifactId": artifact["artifactId"], "note": "Fixture Host adjusted the output in the target",
            "target": {"path": str(target), "ref": "HEAD"}, "beforeCommit": before,
            "adjusted": True, "hostPaths": ["fixture-host.txt"], "keepCheckout": True,
            **verified["control"]})
        assert verified_accepted["integration"]["state"] == "verified"
        assert [item["path"] for item in verified_accepted["integration"]["verification"]["differingPaths"]] == ["README.md"]
        assert verified_accepted["integration"]["verification"]["hostPaths"] == ["fixture-host.txt"]
        verified_plan = internal_call("cleanup_plan", {"runId": verified["runId"],
            "commandId": "plan-verified-fixture", "expectedRevision": verified_accepted["revision"],
            **verified["control"]})
        assert verified_plan["plan"]["state"] == "planned"
        verified_applied = internal_call("cleanup_apply", {"runId": verified["runId"],
            "planId": verified_plan["plan"]["planId"], "commandId": "apply-verified-fixture",
            "expectedRevision": verified_plan["targetRevision"],
            "confirmPath": verified_plan["plan"]["path"], **verified["control"]})
        assert verified_applied["plan"]["state"] == "applied"
        # Ask for assistance through a base Worker turn, then approve one real
        # helper. They use separate existing read-only fixture checkouts so
        # native execution and workspace mutation are not needed for this stock.
        helper_parent = call("workflow_submit", {"requestId": "fixture-helper-parent",
            "hostId": "fixture-host", "submissionToken": "fixture-submission-token-helper-parent",
            "task": "fixture parent requesting bounded help", "harness": "codex",
            "provider": "openai", "model": "fixture-alpha", "effort": "high",
            "cwd": str(work / "workspaces" / "cancelled"),
            "executionWorkspace": {"kind": "existing", "access": "read"},
            "macroTaskOf": ids["delivered"]})
        stock_ids["helperParent"] = helper_parent["runId"]
        parent_claim = admit(nonce, helper_parent["runId"])
        parent_task = helper_parent["runId"]
        parent_attempt = parent_claim["attempt"]
        parent_journal = (work / "state" / "attempts" / parent_task /
                          parent_attempt["attemptId"] / "inquiry.results.jsonl")
        parent_journal.parent.mkdir(parents=True, exist_ok=True)
        identity = {"taskId": parent_task, "attemptId": parent_attempt["attemptId"],
                    "generation": parent_attempt["generation"], "turnId": parent_claim["turn"]["turnId"]}
        bridge = InquiryBridge(identity=identity, journal_path=str(parent_journal),
                               error_factory=lambda code, message: BoardError(code, message),
                               event_metadata=lambda event: {"kind": "fixture"})
        bridge.start()
        bridge.activate("fixture-inquiry-session")

        def stock_question(case, *, state=None, reason=None):
            inquiry_id = "fixture-" + case.replace("_", "-")
            question = "Fixture " + case + "?"
            posted = store._message_post(parent_task, inquiry_id, question)["message"]
            operations.append({"operation": "store._message_post", "params": {
                "taskId": parent_task, "inquiryId": inquiry_id, "question": question}, "response": posted})
            if state is not None:
                delivery = {"deliveredAt": AT, "via": "native:in-turn"} if state == "delivered" else None
                updated = store._message_update(parent_task, inquiry_id, actor="fixture-base-writer",
                    state=state, reason=reason, delivery=delivery)["message"]
                operations.append({"operation": "store._message_update", "params": {
                    "taskId": parent_task, "inquiryId": inquiry_id, "state": state,
                    "reason": reason, "delivery": delivery}, "response": updated})
            inquiry_stock[case] = {"taskId": parent_task, "attemptId": parent_attempt["attemptId"],
                                   "inquiryId": inquiry_id, "messageId": posted["messageId"],
                                   "journal": str(parent_journal.relative_to(work / "state"))}
            return inquiry_id, question

        def journal_question(case, *, deliver=False, answer=None, state=None):
            inquiry_id, question = stock_question(case, state=state)
            assert bridge._ask({"inquiryId": inquiry_id, "question": question},
                               inquiry_bridge._owner_response)["ok"]
            if deliver:
                bridge.deliver_native(inquiry_id, {"nativeItemId": "fixture-" + case})
            if answer is not None:
                assert bridge.answer_native(inquiry_id, answer, "fixture-tool-" + case) is None

        journal_question("journalAnsweredEarlier", deliver=True, answer="Fixture durable answer")
        journal_question("journalAnsweredQueued", deliver=True, answer="Fixture queued answer")
        journal_question("journalAnsweredDelivered", deliver=True, answer="Fixture delivered answer", state="delivered")
        journal_question("journalDeliveredOnly", deliver=True)
        stock_question("journalNoEntry")
        stock_question("dbDeliveredNoEntry", state="delivered")
        mismatch_id, mismatch_question = stock_question("journalBindingMismatch")
        foreign = InquiryBridge(identity={**identity, "taskId": "fixture-foreign-task"},
            journal_path=str(parent_journal), error_factory=lambda code, message: BoardError(code, message),
            event_metadata=lambda event: {"kind": "fixture"})
        foreign.start()
        foreign.activate("fixture-foreign-session")
        assert foreign._ask({"inquiryId": mismatch_id, "question": mismatch_question},
                            inquiry_bridge._owner_response)["ok"]
        journal_question("journalAnsweredClaimed", deliver=True, answer="Fixture claimed answer", state="claimed")
        other_id, other_question = stock_question("unavailableOtherReason", state="unavailable",
                       reason={"code": "inquiry-observe-only", "params": {}})
        assert bridge._ask({"inquiryId": other_id, "question": other_question},
                           inquiry_bridge._owner_response)["ok"]
        bridge.deliver_native(other_id, {"nativeItemId": "fixture-unavailable-other"})
        assert bridge.answer_native(other_id, "Answer that must not replace the refusal",
                                    "fixture-unavailable-other-tool") is None
        parent_manifest = parent_claim["turn"]["input"]["executionWorkspace"]
        parent_seal = workspace.seal(work / "state", parent_manifest,
                                     helper_parent["runId"], parent_claim["attempt"]["attemptId"])
        parent_record = completed_record(parent_claim, "helper-parent")
        parent_record["outcome"] = {"disposition": "assistance", "summary": "Need bounded help",
            "remaining": ["Review the fixture task"], "decisions": [], "artifacts": [],
            "request": {"summary": "Approve a bounded helper", "attempted": "Reviewed the input",
                        "neededWork": ["Review the fixture task"], "expectedArtifacts": ["Helper result"],
                        "acceptance": "Helper result recorded"}}
        report(parent_claim, nonce, status="ok", result={"status": "ok", "turn": parent_record,
            "workspaceSeal": parent_seal, "processState": {"shutdownConfirmed": True}})
        parent_view = call("workflow_get", {"runId": helper_parent["runId"]})
        assert parent_view["state"] == "awaiting-host" and parent_view["activeRequest"]
        approved = call("workflow_decide", {"runId": helper_parent["runId"],
            "requestId": parent_view["activeRequest"]["requestId"],
            "commandId": "approve-fixture-helper", "expectedRevision": parent_view["revision"],
            "decision": "approve", "autoContinue": False,
            "helpers": [{"requestId": "fixture-helper-task", "task": "Review the fixture task",
                "harness": "codex", "provider": "openai", "model": "fixture-alpha", "effort": "high",
                "cwd": str(work / "workspaces" / "failed"),
                "executionWorkspace": {"kind": "existing", "access": "read"}, "expectedSeconds": 60}],
            **helper_parent["control"]})
        helper_id = approved["children"][0]["taskId"]
        stock_ids["helper"] = helper_id
        helper_claim = admit(nonce, helper_id)
        missing_id = "fixture-missing-journal"
        missing_question = "Fixture missing journal?"
        missing_posted = store._message_post(helper_id, missing_id, missing_question)["message"]
        operations.append({"operation": "store._message_post", "params": {"taskId": helper_id,
            "inquiryId": missing_id, "question": missing_question}, "response": missing_posted})
        missing_updated = store._message_update(helper_id, missing_id, actor="fixture-base-writer",
            state="delivered", delivery={"deliveredAt": AT, "via": "native:in-turn"})["message"]
        operations.append({"operation": "store._message_update", "params": {"taskId": helper_id,
            "inquiryId": missing_id, "state": "delivered"}, "response": missing_updated})
        missing_path = (work / "state" / "attempts" / helper_id /
                        helper_claim["attempt"]["attemptId"] / "inquiry.results.jsonl")
        assert not missing_path.exists()
        inquiry_stock["missingJournal"] = {"taskId": helper_id,
            "attemptId": helper_claim["attempt"]["attemptId"], "inquiryId": missing_id,
            "messageId": missing_posted["messageId"], "journal": str(missing_path.relative_to(work / "state"))}
        # Current writers produce both suggestion identities, monotone native
        # activity and both reminder forms while this attempt is active.
        activity = {"phase": "tool-running", "observedAt": AT, "eventSeq": 3,
                    "lastNativeActivityAt": AT, "toolName": "fixture-tool"}
        call("execution_progress", {"attemptId": helper_claim["attempt"]["attemptId"],
            "generation": helper_claim["attempt"]["generation"], "nonce": nonce,
            "data": {"activity": activity}})
        suggestion = {"runId": helper_id, "body": "Fixture Worker suggestion"}
        response = store.workflow.suggest(suggestion, scope={"attemptId": helper_claim["attempt"]["attemptId"]})
        operations.append({"operation": "workflow.suggest", "params": suggestion, "response": response})
        call("workflow_suggest", {"runId": helper_parent["runId"], "body": "Fixture agent suggestion"})
        call("reminder_settings_set", {"silenceSeconds": 60}, human=True)
        clock[0] = "2026-01-01T00:02:00.000Z"
        response = reminders.collect(store)
        operations.append({"operation": "reminders.collect", "params": {"now": clock[0]}, "response": response})
        helper_manifest = helper_claim["turn"]["input"]["executionWorkspace"]
        helper_seal = workspace.seal(work / "state", helper_manifest,
                                     helper_id, helper_claim["attempt"]["attemptId"])
        report(helper_claim, nonce, status="ok", result={"status": "ok",
            "turn": completed_record(helper_claim, "helper"), "workspaceSeal": helper_seal,
            "processState": {"shutdownConfirmed": True}})
        helper_view = call("workflow_get", {"runId": helper_id})
        call("workflow_accept", {"runId": helper_parent["runId"], "targetRunId": helper_id,
            "artifactId": helper_view["finalArtifactId"], "note": "Fixture Host accepts helper result",
            "notRequired": "Read-only helper has no integration target", "keepCheckout": True,
            **helper_parent["control"]})
        call("quota_redetect", {"requestId": "fixture-quota-redetect", "harness": "codex", "provider": "openai"})
        # A real continuation requests routing, consumes its input, then uses
        # the recovery writer with synthetic proof that no execution exists.
        # No process is started or signalled.
        publish(preferenceChanges=[{"buddyId": PROFILES[1], "mode": None}],
                configuration={"routerBuddyIds": [PROFILES[1]]})
        parent_view = call("workflow_get", {"runId": helper_parent["runId"]})
        continued = call("workflow_continue", {"runId": helper_parent["runId"],
            "commandId": "continue-fixture-parent", "expectedRevision": parent_view["revision"],
            "input": "Continue with the accepted helper result", "reroute": True,
            **helper_parent["control"]})
        route_claim = admit(nonce, continued["routing"]["taskId"])
        answer_router(route_claim, "answered")
        store.workflow.prepare_continuation_workspace({"runId": helper_parent["runId"]})
        resumed = admit(nonce, helper_parent["runId"])
        identity = {"bootId": "macos:00000000-0000-0000-0000-000000000000", "pid": 2147483647,
                    "startedAt": 1, "processGroupId": 2147483647, "jobName": None}
        response = store.recover_executions(resumed["attempt"]["attemptId"], process_identity=identity,
                                           stopped_evidence={"kind": "identity-absent"})
        assert response and response[0]["attempt"]["shutdownConfirmed"], response
        operations.append({"operation": "store.recover_executions", "params": {"attemptId": resumed["attempt"]["attemptId"],
            "processIdentity": identity, "stoppedEvidence": {"kind": "identity-absent"}}, "response": response})
        publish(preferenceChanges=[{"buddyId": PROFILES[1], "mode": "exclude", "note": "fixture exclusion"}],
                configuration={"routerBuddyIds": PROFILES})
        call("workflow_cancel", {"runId": helper_parent["runId"],
            "commandId": "close-helper-parent-fixture", "note": "Fixture helper stock is settled",
            **helper_parent["control"]})
        call("workflow_conclude", {"runId": helper_parent["runId"],
            "note": "Fixture Host concluded helper parent", **helper_parent["control"]})
        # A successful routed call ended the earlier refusal sequence. Retain a
        # fresh synthetic refusal through the base's transaction writer, then
        # exercise the named re-detection operation on it.
        with store.db.write() as connection:
            quota_routing.record_rejection(connection, "codex", "openai", {"code": "quota-exceeded"}, now=clock[0])
        operations.append({"operation": "quota_routing.record_rejection",
                           "params": {"harness": "codex", "provider": "openai", "failure": {"code": "quota-exceeded"}},
                           "response": "recorded"})
        call("quota_redetect", {"requestId": "fixture-final-quota-redetect", "harness": "codex", "provider": "openai"})
        deleted = call("workflow_submit", {"requestId": "fixture-deleted", "hostId": "fixture-host",
            "submissionToken": "fixture-submission-token-deleted", "task": "fixture deleted macro task",
            "harness": "codex", "provider": "openai", "model": "fixture-alpha", "effort": "high",
            "cwd": str(work / "repository"), "executionWorkspace": {"kind": "existing", "access": "read"},
            "macroTask": {"title": "待删除的升级夹具"}})
        call("workflow_cancel", {"runId": deleted["runId"], "commandId": "cancel-deleted-fixture",
                                 "note": "fixture deletion", **deleted["control"]})
        call("macro_task_delete", {"macroTaskId": deleted["macroTaskId"], "confirm": True, "microTaskCount": 1}, human=True)
        # A trusted missing-model reading starts the base's pending absence row.
        missing_catalog = {**catalog, "source": "fixture:missing-beta", "providerVersion": None, "providers": [{**catalog["providers"][0], "models": [catalog["providers"][0]["models"][0]]}]}
        catalog_store.record(store.evaluation, missing_catalog)
        operations.append({"operation": "catalog_store.record", "params": missing_catalog, "response": "missing-model-pending"})
        catalog_module.coordinate_catalog_reread(store.directory, "codex", reader=lambda: None, wait=True)
        operations.append({"operation": "coordinate_catalog_reread", "params": {"adapter": "codex"}, "response": "window-written"})
        publish(buddySettings=[{"buddyId": PROFILES[1], "enabled": False}],
                preferenceChanges=[{"buddyId": PROFILES[0], "mode": "pin", "note": "fixture pinned buddy"}])
        # A physical-phase failure leaves the current deletion fence, task rows and
        # command receipts intact. No native deletion or directory removal runs.
        with store.db.read() as connection:
            pending_target = connection.execute("SELECT macro_task_id FROM workflow_runs WHERE run_id=?",
                                                (ids["cancelled"],)).fetchone()[0]
        with patch.object(macro_task_deletion, "_delete_sessions",
                          side_effect=BoardError("FIXTURE_STOP", "Retain in-progress deletion stock")):
            pending = call("macro_task_delete", {"macroTaskId": pending_target,
                           "confirm": True, "microTaskCount": 10}, human=True)
        assert pending["state"] == "deleting"
        # Cover default reason-only health and a native result whose JSON status
        # becomes stale after the base writer invalidates it. Codex stays ready
        # for the fixture's already recorded execution history.
        service.harnesses.initialize()
        operations.append({"operation": "harnesses.initialize", "params": {}, "response": "defaults-written"})
        with patch.object(service.harnesses, "catalog_refresh", None):
            zcode_ready = service.harnesses.refresh("zcode", force=True)
        operations.append({"operation": "harnesses.refresh", "params": {"harness": "zcode", "force": True},
                           "response": zcode_ready})
        zcode_stale = service.harnesses.invalidate("zcode", zcode_ready["revision"])
        operations.append({"operation": "harnesses.invalidate",
                           "params": {"harness": "zcode", "revision": zcode_ready["revision"]},
                           "response": zcode_stale})
        # Retain one begun observation as well as completed receipts. Its
        # completion time and response are legitimately NULL in the base form.
        pending_observation = catalog_store.begin(store.evaluation, "fixture-pending-observation")
        operations.append({"operation": "catalog_store.begin",
                           "params": {"requestId": "fixture-pending-observation"},
                           "response": pending_observation})
        # Add old-writer evidence for failed and recovered partial seals, plus a
        # successful native receipt whose turn the base importer rejects. These
        # runs follow the original stock so its stable IDs and facts do not move.
        edge_stock = {}
        edge_parent = None
        for label in ("partial-failed", "partial-interrupted", "rejected-turn", "rejected-seal"):
            submitted = call("workflow_submit", {"requestId": "fixture-" + label, "hostId": "fixture-host",
                "submissionToken": "fixture-submission-token-" + label, "task": "fixture " + label,
                "harness": "codex", "provider": "openai", "model": "fixture-alpha", "effort": "high",
                "cwd": str(work / "workspaces" / label),
                "executionWorkspace": {"kind": "existing", "access": "full",
                                       "accessNote": "Isolated fixture stock generation",
                                       "writeScope": ["fixture.txt"]},
                **({"macroTask": {"title": "Old writer edge stock"}} if edge_parent is None
                   else {"macroTaskOf": edge_parent})})
            if edge_parent is None:
                edge_parent = submitted["runId"]
            claim = admit(nonce, submitted["runId"])
            attempt = claim["attempt"]
            turn = claim["turn"]
            if label in ("partial-failed", "partial-interrupted"):
                (work / "workspaces" / label / "fixture.txt").write_bytes(
                    ("fixture " + label + " change\n").encode("utf-8"))
            edge_stock[label] = {"runId": submitted["runId"], "attemptId": attempt["attemptId"],
                                 "turnId": turn["turnId"]}
            if label == "partial-interrupted":
                identity = {"bootId": "macos:00000000-0000-0000-0000-000000000000",
                            "pid": 2147483646, "startedAt": 1, "processGroupId": 2147483646,
                            "jobName": None}
                recovered = store.recover_executions(attempt["attemptId"], process_identity=identity,
                    stopped_evidence={"kind": "identity-absent"})
                assert recovered and recovered[0]["attempt"]["shutdownConfirmed"], recovered
                operations.append({"operation": "store.recover_executions",
                    "params": {"attemptId": attempt["attemptId"], "processIdentity": identity,
                               "stoppedEvidence": {"kind": "identity-absent"}}, "response": recovered})
                continue
            if label == "partial-failed":
                seal = workspace.seal(work / "state", turn["input"]["executionWorkspace"],
                                      submitted["runId"], attempt["attemptId"])
                report(claim, nonce, status="failed", result={"status": "error",
                    "partialWorkspaceSeal": seal, "processState": {"shutdownConfirmed": True}})
            elif label == "rejected-turn":
                record = completed_record(claim, label)
                record["inputSha256"] = "b" * 64
                report(claim, nonce, status="ok", result={"status": "ok", "turn": record,
                    "processState": {"shutdownConfirmed": True}})
            else:
                record = completed_record(claim, label)
                report(claim, nonce, status="ok", result={"status": "ok", "turn": record,
                    "workspaceSeal": {"manifestSha256": "not-a-hash"},
                    "processState": {"shutdownConfirmed": True}})
            with store.db.read() as connection:
                attempt_row = connection.execute("SELECT result_json FROM attempts WHERE attempt_id=?",
                                                 (attempt["attemptId"],)).fetchone()
                turn_row = connection.execute("SELECT state FROM workflow_turns WHERE turn_id=?",
                                              (turn["turnId"],)).fetchone()
                stored_result = json.loads(attempt_row["result_json"])
                if label == "rejected-turn":
                    assert stored_result["result"]["microTaskError"]["code"] == "TURN_INVALID"
                elif label == "rejected-seal":
                    assert turn_row["state"] == "failed"
                    assert stored_result["result"]["workspaceSeal"] == {"manifestSha256": "not-a-hash"}
                else:
                    assert turn_row["state"] == "failed"
                    assert stored_result["result"]["partialWorkspaceSeal"] == seal
        hashing.close()
        # Base operations created these rows. Export templates keep
        # their original digests; restoration explicitly rebinds the dependency
        # graph before the data-preservation baseline is taken.
        with store.db.read() as connection:
            raw_sql = "\n".join(connection.iterdump()) + "\n"
            assert connection.execute("SELECT COUNT(*) FROM catalog_pending").fetchone()[0] > 0
            source_schema = list(connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))
            row_counts = {row[1]: connection.execute(f'SELECT COUNT(*) FROM "{row[1]}"').fetchone()[0]
                          for row in source_schema if row[0] == "table" and row[1] != "sqlite_sequence"}
        (work / "raw.sql").write_bytes(raw_sql.encode("utf-8"))
        redactions = [(str(work), "<fixture-root>")]
        sql = raw_sql
        for old, new in redactions:
            sql = sql.replace(old, new)
        header = f"-- Upgrade base: source {BASE}; {BASE_VERSION} / schema {BASE_SCHEMA}\n-- Generated by packaging/generate-upgrade-fixture.py; native facts/results are synthetic.\n-- Path template: restore with tests/python/blackboard/store/upgrade_fixture.py before use.\n"
        output = args.output_dir.resolve()
        output.mkdir(parents=True, exist_ok=True)
        sql_path = output / "board.sql"
        sql_path.write_bytes((header + sql).encode("utf-8"))
        (output / "catalog.json").write_bytes((json_text(catalog) + "\n").encode("utf-8"))
        operations_text = json_text(operations).replace(str(work), "<fixture-root>")
        (output / "operations.json").write_bytes((operations_text + "\n").encode("utf-8"))
        for relative, content in LEGACY_PRIVATE_FILES.items():
            path = work / "state" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        resources = export_resources(work, workspace, preimages)
        (output / "workspace-resources.json").write_bytes(
            (json.dumps(resources, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        metadata = {"format": 2, "sourceCommit": BASE, "sourceVersion": BASE_VERSION, "schemaVersion": BASE_SCHEMA,
            "sourceTree": subprocess.check_output(["git", "rev-parse", BASE + "^{tree}"],
                                                  text=True, encoding="utf-8").strip(),
            "workspaceInputCommit": WORKSPACE_INPUT,
            "generationCommand": "python packaging/generate-upgrade-fixture.py" +
                (" --python <prepared-python>" if args.python else " --offline" if args.offline else "") +
                " --work-dir <new-private-tmp-dir>" +
                " --output-dir tests/fixtures/upgrade-base",
            "dependencyMode": "prepared-read-only-interpreter" if args.python else "base-frozen-uv-environment",
            "dependencyVersions": {name: importlib.metadata.version(name) for name in ("c-two", "pydantic", "zstandard", "portalocker")},
            "sourceFiles": {str(p.relative_to(source)): digest(p.read_bytes()) for p in
                            [*sorted((source / "src/hey_my_buddy").rglob("*.py")), source / "pyproject.toml",
                             source / "uv.lock", source / "packaging/runtime-assets.json"]},
            "stableIds": ids, "stockIds": stock_ids, "taskOnlyStock": task_only_stock,
            "edgeStock": edge_stock,
            "inquiryStock": inquiry_stock,
            "tableRowCounts": row_counts,
            "routerBuddyIds": PROFILES, "fixedTimestamp": AT,
            "secret": "ab" * 32, "serviceToken": "fixture-service-token",
            "boundary": "Base BoardService operations, message writers and InquiryBridge journal writers in process; synthetic native discovery, execution results and absent-process evidence; no daemon/IPC/model/account read. Stored rows come from base writers; one completed retry dispatch and its dependent current-input and event digests are patched to represent the pre-0.46 excludedBuddies reason-empty form that fd9f04e8 cannot emit. No SQL seed creates a row. Generation uses a canonical private path and synthetic inode; text templates retain source digests. load_fixture binds private paths, stable path/inode identities and dependent hashes before acceptance; this preparation is not migration or new native execution.",
            "pythonVersion": sys.version.split()[0],
            "pythonInterpreter": "<fixture-root>/base/.venv/bin/python" if args.python is None else "<prepared-python>",
            "restoreInterface": f"load_fixture(state: Path) -> dict; empty private state directory; returns source metadata plus state, fixtureRoot, operations and bindings; schema stays {BASE_SCHEMA}; baseline only after return",
            "schemaSha256": digest(json_text([list(row) for row in source_schema]).encode("utf-8")),
            "sqlSha256": digest(sql_path.read_bytes()), "operationsSha256": digest((output / "operations.json").read_bytes()),
            "catalogSha256": digest((output / "catalog.json").read_bytes()),
            "resourcesSha256": digest((output / "workspace-resources.json").read_bytes())}
        metadata["loadedSourceModules"] = sorted(str(Path(module.__file__).resolve().relative_to(source))
            for name, module in sys.modules.items() if name.startswith("hey_my_buddy.") and getattr(module, "__file__", None))
        metadata["generatorSha256"] = digest(Path(__file__).read_bytes())
        (output / "metadata.json").write_bytes(
            (json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps(verify(output, work / "restored.sqlite3"), ensure_ascii=False))


def export_resources(work, workspace, preimages):
    def template(text):
        return text.replace(str(work), "<fixture-root>")

    identities, checkouts = {}, {}
    for label in ("delivered", "accepted", "failed", "cancelled", "adopted",
                  "partial-failed", "partial-interrupted", "rejected-turn", "rejected-seal"):
        checkout = ((work / "state" / "workspaces") if label == "adopted" else (work / "workspaces"))
        if label == "adopted":
            checkout = next(path / "checkout" for path in checkout.iterdir()
                            if path.is_dir() and (path / "checkout").exists()
                            and (path / "checkout" / "outside.txt").exists())
        else:
            checkout /= label
        actual = workspace.inspect(str(checkout))
        checkouts[label] = template(actual["checkoutRoot"])
        for role, path in (("checkoutId", actual["gitDir"]), ("repositoryId", actual["repositoryPath"])):
            identities[actual[role]] = template(path)
    repository = work / "repository"
    # Git refs, object IDs and types here use ASCII syntax, within UTF-8.
    refs = dict(line.split(" ") for line in subprocess.check_output(
        ["git", "-C", str(repository), "for-each-ref", "--format=%(refname) %(objectname)", "refs/hey-my-buddy/"],
        text=True, encoding="utf-8").splitlines())
    # The fixed input is a root commit: its complete reachable object closure is
    # tiny. Export raw object bytes as hex so no generating repository is needed.
    oids = sorted({line.split(" ")[0] for line in subprocess.check_output(
        ["git", "-C", str(repository), "rev-list", "--objects", WORKSPACE_INPUT, *refs.values()],
        text=True, encoding="utf-8").splitlines()})
    objects = []
    for oid in oids:
        kind = subprocess.check_output(["git", "-C", str(repository), "cat-file", "-t", oid],
                                       text=True, encoding="utf-8").strip()
        data = subprocess.check_output(["git", "-C", str(repository), "cat-file", kind, oid])
        objects.append({"oid": oid, "type": kind, "hex": data.hex()})
    files = {str(path.relative_to(work / "state")): template(path.read_text(encoding="utf-8"))
             for folder in ("workspaces", "harnesses", "sessions", "attempts")
             for path in sorted((work / "state" / folder).rglob("*"))
             if path.is_file() and path.suffix in {".json", ".jsonl", ".patch"}}
    for relative in LEGACY_PRIVATE_FILES:
        files[relative] = (work / "state" / relative).read_text(encoding="utf-8")
    # Emit only path/identity-dependent hashes, in dependency order. Immutable
    # Git and path-free native/history facts need no rebinding.
    selected = dict.fromkeys(identities)
    recipes = []
    pending = {key: value for key, value in preimages.items() if key not in identities}
    while True:
        ready = [(key, value) for key, value in pending.items()
                 if str(work) in value[0] or any(old in value[0] for old in selected)]
        if not ready:
            break
        for key, (payload, ascii_json) in ready:
            selected[key] = None
            recipes.append({"oldSha256": key, "template": template(payload), "asciiJson": ascii_json})
            pending.pop(key)
    # Sorting by actual dependencies avoids assuming the first observation of
    # a repeated digest came before all of its dependencies were observed.
    by_hash = {recipe["oldSha256"]: recipe for recipe in recipes}
    ordered, visited = [], set(identities)

    def visit(key):
        if key in visited:
            return
        recipe = by_hash[key]
        for other in re.findall(r"[0-9a-f]{64}", recipe["template"]):
            if other in by_hash:
                visit(other)
        visited.add(key)
        ordered.append(recipe)

    for key in by_hash:
        visit(key)
    return {"format": 1, "workspaceInputCommit": WORKSPACE_INPUT, "checkouts": checkouts,
            "identities": identities, "gitObjects": objects, "refs": refs, "files": files,
            "hashBindings": ordered}


def verify(output, restored):
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert digest((output / "board.sql").read_bytes()) == metadata["sqlSha256"]
    assert digest((output / "operations.json").read_bytes()) == metadata["operationsSha256"]
    assert digest((output / "catalog.json").read_bytes()) == metadata["catalogSha256"]
    assert digest((output / "workspace-resources.json").read_bytes()) == metadata["resourcesSha256"]
    if restored.exists():
        raise ValueError("restore destination must not exist")
    with sqlite3.connect(restored) as connection:
        connection.executescript((output / "board.sql").read_text(encoding="utf-8"))
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert dict(connection.execute("SELECT key,value FROM meta"))["schema_version"] == str(BASE_SCHEMA)
        assert connection.execute("SELECT COUNT(*) FROM events WHERE kind='attempt.activity'").fetchone()[0] > 0
        assert connection.execute("SELECT COUNT(*) FROM events WHERE kind='router.claimed'").fetchone()[0] > 0
        retry = connection.execute("SELECT decision_id,task_id,requested_json FROM decision_requests "
                                   "WHERE request_id='fixture-routing-retry'").fetchone()
        assert json.loads(retry[2])["selectionRetry"] is True
        dispatches = connection.execute("SELECT d.task_id,d.document_json,t.request_id FROM router_dispatches d "
                                        "JOIN tasks t ON t.task_id=d.task_id WHERE d.decision_id=? "
                                        "ORDER BY t.request_id", (retry[0],)).fetchall()
        assert [row[2] for row in dispatches] == ["decision:fixture-routing-retry:0:0",
                                                     "decision:fixture-routing-retry:1:0"]
        assert retry[1] == dispatches[1][0]
        assert json.loads(dispatches[1][1])["routingBasis"]["excludedBuddies"][0]["reason"] == ""
        for label, decision_id in metadata["taskOnlyStock"].items():
            row = connection.execute(
                "SELECT d.status,d.task,r.requested_json,r.task_id,r.input_json FROM evaluation_decisions d"
                " JOIN decision_requests r ON r.decision_id=d.decision_id WHERE d.decision_id=?",
                (decision_id,)).fetchone()
            assert row is not None and row[1] == json.loads(row[2])["task"]
            assert connection.execute("SELECT COUNT(*) FROM router_requests WHERE decision_id=?",
                                      (decision_id,)).fetchone()[0] == (0 if label.startswith("initial") else 1)
            assert connection.execute("SELECT COUNT(*) FROM router_dispatches WHERE decision_id=?",
                                      (decision_id,)).fetchone()[0] == (0 if label.startswith("initial") else 1)
            assert json.loads(row[2])["routingBasis"]["candidateCount"] == (1 if label.endswith("Sole") else 0)
            assert bool(json.loads(row[2]).get("selectionRetry")) == label.startswith("retry")
            if label == "retryZero":
                assert row[0] == "needs-host" and row[3] is not None and row[4] is not None
            elif label == "retrySole":
                assert row[0] == "completed" and row[3] is None and row[4] is None
            else:
                assert row[3] is None and row[4] is None
        assert connection.execute("SELECT COUNT(*) FROM macro_tasks").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM provider_cooling").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM macro_task_deletions").fetchone()[0] == 1
        for table in ("attempt_interruptions", "attempt_process_identities", "attempt_shutdown_evidence",
                      "workflow_suggestions", "attempt_activity", "attempt_reminders", "workflow_routes",
                      "workflow_continuations", "workflow_workspace_conflicts", "workflow_integrations",
                      "workspace_cleanup_plans", "agent_credentials", "model_service_concurrency",
                      "evaluation_catalog", "catalog_current", "catalog_pending", "catalog_observations"):
            assert connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] > 0, table
        assert set(connection.execute("SELECT kind FROM attempt_reminders")) == {
            ("expected-duration",), ("native-silence",)}
        assert set(connection.execute("SELECT attempt_id IS NULL FROM workflow_suggestions")) == {(0,), (1,)}
        assert set(connection.execute("SELECT state FROM workspace_cleanup_plans")) == {("planned",), ("applied",), ("blocked",)}
        assert connection.execute("SELECT COUNT(*) FROM workflow_continuations WHERE state='consumed'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM attempt_interruptions WHERE state='settled'").fetchone()[0] == 2
        for label, expected in (("delivered", {}),
                                ("accepted", {"remaining": {"droppedItems": 5, "shortenedItems": 1}})):
            row = connection.execute("SELECT w.state,w.outcome_truncated_json,a.result_json,w.turn_result_path FROM workflow_turns w"
                                     " JOIN attempts a ON a.attempt_id=w.attempt_id WHERE w.run_id=?",
                                     (metadata["stableIds"][label],)).fetchone()
            assert row[0] == "returned" and json.loads(row[1]) == expected
            assert json.loads(row[2])["result"]["turn"]["outcomeTruncated"] == expected
            if label == "delivered":
                assert row[3] == json.loads(row[2])["result"]["turnResultPath"]
                assert row[3].endswith("/turn-result.json")
        for label, expected_state in (("partial-failed", "failed"),
                                      ("partial-interrupted", "interrupted"),
                                      ("rejected-turn", "failed"),
                                      ("rejected-seal", "failed")):
            stock = metadata["edgeStock"][label]
            row = connection.execute("SELECT w.state,w.sealed_artifacts_json,a.result_json"
                                     " FROM workflow_turns w JOIN attempts a ON a.attempt_id=w.attempt_id"
                                     " WHERE w.run_id=? AND w.turn_id=? AND a.attempt_id=?",
                                     (stock["runId"], stock["turnId"], stock["attemptId"])).fetchone()
            assert row is not None and row[0] == expected_state
            result = json.loads(row[2])
            assert result["status"] == "failed"
            if label == "rejected-turn":
                assert result["result"]["microTaskError"]["code"] == "TURN_INVALID"
                assert result["result"]["turn"]["inputSha256"] == "b" * 64
            elif label == "rejected-seal":
                assert result["result"]["workspaceSeal"] == {"manifestSha256": "not-a-hash"}
                assert json.loads(row[1]) == []
                result_fields = connection.execute(
                    "SELECT previous_session_id,session_id,prompt_sha256,disposition,"
                    "outcome_json,outcome_truncated_json,provenance_json,turn_result_path"
                    " FROM workflow_turns WHERE turn_id=?", (stock["turnId"],)).fetchone()
                assert result_fields == (None,) * 8
                assert connection.execute("SELECT COUNT(*) FROM workflow_artifacts"
                    " WHERE turn_id=? AND kind='output'", (stock["turnId"],)).fetchone()[0] == 0
            else:
                assert json.loads(row[1])[0]["kind"] == "workspace-seal"
                assert result["result"]["partialWorkspaceSeal"]["changedPaths"] == ["fixture.txt"]
                if label == "partial-interrupted":
                    assert result["terminationReason"] == "interrupted"
                assert connection.execute("SELECT COUNT(*) FROM workflow_artifacts WHERE turn_id=?"
                                          " AND kind='partial-output'", (stock["turnId"],)).fetchone()[0] == 1
        assert set(dict(connection.execute("SELECT key,value FROM meta"))) == {"schema_version", "capability_secret"}
        messages = connection.execute(
            "SELECT message_id,attempt_id,inquiry_id,author_role,author_id,state,reason_code "
            "FROM messages ORDER BY rowid").fetchall()
        assert messages[0][2:] == ("fixture-inquiry", "console", "f" * 24,
                                   "unavailable", "inquiry-root-turn-ended")
        assert {row[3] for row in messages} == {None, "console", "lead"}
        assert {row[5] for row in messages} == {"answered", "unavailable"}
        assert any(row[5] == "unavailable" and row[6] == "inquiry-delivered-at-attempt-end" for row in messages)
        assert {row[0] for row in messages} == {
            item["messageId"] for item in metadata["inquiryStock"].values()}
        by_id = {row[0]: row for row in messages}
        resource_files = json.loads((output / "workspace-resources.json").read_text(encoding="utf-8"))["files"]
        for case, item in metadata["inquiryStock"].items():
            row = by_id[item["messageId"]]
            assert (row[1], row[2]) == (item["attemptId"], item["inquiryId"])
            if item.get("journal"):
                if case == "missingJournal":
                    assert item["journal"] not in resource_files
                else:
                    assert item["journal"] in resource_files
        states = dict(connection.execute("SELECT run_id,state FROM workflow_runs"))
        assert states == {**{metadata["stableIds"][key]: value for key, value in
                            (("delivered", "concluded"), ("accepted", "accepted"), ("failed", "concluded"), ("cancelled", "cancelled"))},
                          **{metadata["stockIds"][key]: "accepted" for key in ("adopted", "verified", "helper", "lead")},
                          metadata["stockIds"]["helperParent"]: "concluded",
                          metadata["stockIds"]["leadOwned"]: "concluded",
                          **{metadata["edgeStock"][key]["runId"]: "failed" for key in
                             ("partial-failed", "partial-interrupted", "rejected-turn", "rejected-seal")}}
        schema = [list(row) for row in connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
        assert digest(json_text(schema).encode("utf-8")) == metadata["schemaSha256"]
        assert "<fixture-root>" in (output / "board.sql").read_text(encoding="utf-8")
        assert "/Users/" not in (output / "board.sql").read_text(encoding="utf-8")
        return {"integrity": "ok", "foreignKeyViolations": 0, "schema": BASE_SCHEMA, "states": states,
                "tables": sum(row[0] == "table" and row[1] != "sqlite_sequence" for row in schema),
                "events": connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], "sourceSchemaMatches": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("tests/fixtures/upgrade-base"))
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--python", type=Path, help="Use an already prepared read-only dependency interpreter")
    parser.add_argument("--populate", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--verify", action="store_true", help="Only restore and validate; no old-source imports")
    args = parser.parse_args()
    if args.verify:
        args.work_dir.mkdir(parents=True, exist_ok=False)
        print(json.dumps(verify(args.output_dir, args.work_dir / "restored.sqlite3"), ensure_ascii=False))
    elif args.populate:
        populate(args)
    else:
        generate(args)


if __name__ == "__main__":
    main()
