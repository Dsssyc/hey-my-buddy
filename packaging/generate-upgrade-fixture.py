#!/usr/bin/env python3
"""Generate schema-27 upgrade stock using the frozen installed base source.

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

BASE = "ea7ff3a4967154b2d2bf258aa2412f744d252b59"
BASE_VERSION = "0.49.0"
BASE_SCHEMA = 27
# An existing five-file repository commit keeps workspace capture bounded.
# This is the workspace input, never the source used to create the database.
WORKSPACE_INPUT = "122ec20edcdeb97cc655ae3993c402c2c9c966a3"
AT = "2026-01-01T00:00:00.000Z"
PROFILES = ["codex:openai:fixture-alpha:high", "codex:openai:fixture-beta:high"]


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
    work = args.work_dir.resolve()
    if not work.is_relative_to(root / "tmp"):
        raise ValueError("work-dir must be a new directory inside this checkout's ignored tmp/")
    work.mkdir(parents=True, exist_ok=False)
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
    for label in ("delivered", "accepted", "failed", "cancelled"):
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
    from hey_my_buddy.blackboard.service.service import BoardService, dispatch_local
    from hey_my_buddy.blackboard.service.live_registry import LiveRegistry
    from hey_my_buddy.blackboard.store.store import BoardStore
    from hey_my_buddy.blackboard.catalog import catalog_store, catalog as catalog_module
    from hey_my_buddy.blackboard.routing import quota_routing
    from hey_my_buddy.blackboard.tasks import workspace, macro_task_deletion, reminders
    from hey_my_buddy.protocol.execution import RECEIPT, execution_directory, fsync_json, receipt_reference
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
                                         {"id": "fixture-beta", "efforts": ["high"]}]}]}
    catalog_file = work / "catalog.json"
    catalog_file.write_bytes((json_text(catalog) + "\n").encode("utf-8"))
    os.environ["HMB_MODEL_CATALOG_FILE"] = str(catalog_file)
    operations = []
    ids = {}
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
    @contextmanager
    def retained_directory(*, prefix="tmp", suffix="", dir=None, **kwargs):
        yield tempfile.mkdtemp(prefix=prefix, suffix=suffix, dir=dir or work / "scratch")

    with ExitStack() as stack:
        # Old workspace operations allocate private indexes. Retain them as the
        # task requires; no temporary-directory cleanup is performed.
        stack.enter_context(patch("tempfile.TemporaryDirectory", retained_directory))
        hashing = stack.enter_context(ExitStack())
        hashing.enter_context(patch.object(hashlib, "sha256", recorded_sha256))
        # Stable fake secrets and IDs are inputs to the base public operations.
        stack.enter_context(patch("uuid.uuid4", side_effect=lambda: uuid.UUID(int=next(counter))))
        stack.enter_context(patch("secrets.token_hex", side_effect=lambda n=32: "ab" * n))
        stack.enter_context(patch.object(db_module, "utc_now", return_value=AT))
        stack.enter_context(patch.object(health_module, "utc_now", return_value=AT))
        stack.enter_context(patch.object(health_module, "_later", return_value="2026-01-01T01:00:00.000Z"))
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
            result = dispatch_local(service, name, {**params, **({"consoleAuthority": "fixture-console"} if human else {})})
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
            return {"version": 1, "taskId": claim["task"]["taskId"], "attemptId": attempt["attemptId"],
                    "generation": attempt["generation"], "turnId": turn["turnId"], "resumeMode": turn["resumeMode"],
                    "previousSessionId": turn["input"]["previousSessionId"], "sessionId": "fixture-session-" + label,
                    "promptSha256": "a" * 64, "inputSha256": turn["inputSha256"],
                    "outcome": {"disposition": "completed", "summary": "模拟结果：无模型执行。", "remaining": [],
                                "decisions": [], "artifacts": [], "request": None},
                    "provenance": {"harness": "codex", "turnEnd": "completed", "outputSchemaValidated": True,
                                   "finalMessageCompleted": True, "nativeTurnStarted": True, "nativeTurnCompleted": True,
                                   "nativeThreadId": "fixture-session-" + label, "nativeTurnId": "fixture-turn-" + label,
                                   "finalItemId": "fixture-item", "eventSeq": 2}}

        def answer_router(claim, outcome):
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
                      "decision": {"buddyId": PROFILES[0], "note": "fixture routing answer", "evidence": []}}
            if outcome == "no_answer":
                result.update(status="error", code="fixture-router-no-answer")
            report(claim, nonce, status="ok" if outcome == "answered" else "failed", result=result)

        call("harness_set", {"harness": "codex", "path": None})
        call("model_catalog_refresh", {"requestId": "fixture-catalog"})
        publish(buddySettings=[{"buddyId": profile, "enabled": True} for profile in PROFILES])
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
            report(claim, nonce, status="ok", result={"status": "ok", "turn": record, "workspaceSeal": sealed,
                                                      "processState": {"shutdownConfirmed": True}, "fixture": True})
            view = call("workflow_get", {"runId": run_id})
            assert view["state"] == "delivered", view
            if label == "accepted":
                call("workflow_accept", {"runId": run_id, "artifactId": view["finalArtifactId"],
                    "note": "fixture accepted", "notRequired": "Read-only fixture has no separate target",
                    "keepCheckout": True, **control})
            elif label == "delivered":
                call("workflow_conclude", {"runId": run_id, "note": "Fixture Host concluded the delivered work", **control})

        # Additional base-writer stock for the three forms absent from the first
        # four runs. These IDs are deliberately separate from stableIds.
        stock_ids = {}
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
        assert verified_accepted["integration"]["verification"]["adjustments"] == ["README.md"]
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
        clock[0] = "2026-01-01T00:01:00.000Z"
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
        call("inquiry_observe", {"runId": ids["delivered"], "inquiryId": "fixture-inquiry",
                              "question": "fixture question after the execution ended"})
        # The base writer fixes author to cli and leaves these retired fields
        # at their defaults. Give the pending upgrade non-default stock to drop.
        with store.db.write() as connection:
            connection.execute("UPDATE messages SET recipient=?,attempts_count=? WHERE inquiry_id=?",
                               ("fixture-retired-recipient", 3, "fixture-inquiry"))
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
                           "confirm": True, "microTaskCount": 8}, human=True)
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
        resources = export_resources(work, workspace, preimages)
        (output / "workspace-resources.json").write_bytes(
            (json.dumps(resources, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        metadata = {"format": 2, "sourceCommit": BASE, "sourceVersion": BASE_VERSION, "schemaVersion": BASE_SCHEMA,
            "sourceTree": subprocess.check_output(["git", "rev-parse", BASE + "^{tree}"],
                                                  text=True, encoding="utf-8").strip(),
            "workspaceInputCommit": WORKSPACE_INPUT,
            "generationCommand": "python packaging/generate-upgrade-fixture.py " +
                ("--python <prepared-python>" if args.python else "--offline") +
                " --work-dir " + str(work.relative_to(Path(__file__).resolve().parents[1])) +
                " --output-dir tests/fixtures/upgrade-base",
            "dependencyMode": "prepared-read-only-interpreter" if args.python else "base-frozen-uv-environment",
            "dependencyVersions": {name: importlib.metadata.version(name) for name in ("c-two", "pydantic", "zstandard", "portalocker")},
            "sourceFiles": {str(p.relative_to(source)): digest(p.read_bytes()) for p in
                            [*sorted((source / "src/hey_my_buddy").rglob("*.py")), source / "pyproject.toml",
                             source / "uv.lock", source / "packaging/runtime-assets.json"]},
            "stableIds": ids, "stockIds": stock_ids, "tableRowCounts": row_counts,
            "routerBuddyIds": PROFILES, "fixedTimestamp": AT,
            "secret": "ab" * 32, "serviceToken": "fixture-service-token",
            "boundary": "Base BoardService operations and current store writers in process; synthetic native discovery, execution results and absent-process evidence; no daemon/IPC/model/account read. The only SQL stock write seeds retired message recipient and attempts_count after the base operation. Text templates retain source digests. load_fixture binds private paths, stable path/inode identities and dependent hashes before acceptance; this preparation is not migration or new native execution.",
            "retiredMessageStock": {"recipient": "fixture-retired-recipient", "attemptsCount": 3,
                "author": "cli", "authorBoundary": "The base's sole messages INSERT in blackboard/store/store.py fixes author to cli; no base writer records a non-cli author."},
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
    for label in ("delivered", "accepted", "failed", "cancelled", "adopted"):
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
             for folder in ("workspaces", "harnesses", "sessions")
             for path in sorted((work / "state" / folder).rglob("*"))
             if path.is_file() and (path.suffix in {".json", ".jsonl", ".patch"})}
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
        assert connection.execute("SELECT COUNT(*) FROM macro_tasks").fetchone()[0] == 1
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
        assert connection.execute("SELECT COUNT(*) FROM attempt_interruptions WHERE state='settled'").fetchone()[0] == 1
        assert set(dict(connection.execute("SELECT key,value FROM meta"))) == {"schema_version", "capability_secret"}
        assert connection.execute("SELECT author,recipient,attempts_count FROM messages").fetchall() == [
            ("cli", "fixture-retired-recipient", 3)]
        states = dict(connection.execute("SELECT run_id,state FROM workflow_runs"))
        assert states == {**{metadata["stableIds"][key]: value for key, value in
                            (("delivered", "concluded"), ("accepted", "accepted"), ("failed", "concluded"), ("cancelled", "cancelled"))},
                          **{metadata["stockIds"][key]: "accepted" for key in ("adopted", "verified", "helper")},
                          metadata["stockIds"]["helperParent"]: "concluded"}
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
