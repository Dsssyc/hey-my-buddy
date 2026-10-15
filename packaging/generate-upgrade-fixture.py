#!/usr/bin/env python3
"""Generate schema-22 upgrade stock using the frozen installed base source.

No product installation, daemon, native harness or model is started. The base
named service operations run in process; only native discovery is synthetic.
All work directories are retained. The export includes fixed Git objects,
workspace sidecars and the hash preimages needed for private path binding.
Tests restore that text stock; they never execute this generator.
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

BASE = "b9632dd9421b02fab170752175edabf683a8aeb1"
BASE_VERSION = "0.39.0"
BASE_SCHEMA = 22
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
    (repo / ".git/HEAD").write_text(WORKSPACE_INPUT + "\n")
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
    from hey_my_buddy.blackboard.store.store import BoardStore
    from hey_my_buddy.blackboard.catalog import catalog_store, catalog as catalog_module
    from hey_my_buddy.blackboard.tasks import workspace
    from hey_my_buddy.blackboard.evaluation import native_observations
    from hey_my_buddy.blackboard.evaluation import model_facts
    from hey_my_buddy.blackboard.routing import quota_routing

    assert Path(db_module.__file__).resolve() == source / "src/hey_my_buddy/blackboard/store/db.py"
    assert db_module.SCHEMA_VERSION == BASE_SCHEMA
    catalog = {"source": "fixture:upgrade-base", "harnessVersion": "fixture-only",
               "discoveredAt": AT, "discoveries": [{"harness": "codex", "status": "complete", "accountStatus": "confirmed"}],
               "providers": [{"harness": "codex", "provider": "openai", "efforts": ["high"],
                              "models": [{"id": "fixture-alpha", "efforts": ["high"]},
                                         {"id": "fixture-beta", "efforts": ["high"]}]}]}
    catalog_file = work / "catalog.json"
    catalog_file.write_text(json_text(catalog) + "\n")
    os.environ["HMB_MODEL_CATALOG_FILE"] = str(catalog_file)
    operations = []
    ids = {}
    counter = itertools.count(1)
    preimages = {}
    native_sha256 = hashlib.sha256

    def recorded_sha256(data=b"", **kwargs):
        # Observe the real old digest; do not alter any hash or its validation.
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
        # Stable fake secrets and IDs are inputs to the OLD public operations.
        stack.enter_context(patch("uuid.uuid4", side_effect=lambda: uuid.UUID(int=next(counter))))
        stack.enter_context(patch("secrets.token_hex", side_effect=lambda n=32: "ab" * n))
        stack.enter_context(patch.object(db_module, "utc_now", return_value=AT))
        stack.enter_context(patch.object(health_module, "utc_now", return_value=AT))
        stack.enter_context(patch.object(health_module, "_later", return_value="2026-01-01T01:00:00.000Z"))
        # Replace external native discovery only. The old health/catalog writers,
        # validators and certificate lookup remain the real implementations.
        stack.enter_context(patch.object(health_module, "_snapshot", return_value={"harness": "codex", "manualPath": None, "managerRecords": [], "candidates": [], "fixture": True}))
        stack.enter_context(patch.object(health_module, "_discover", return_value={
            "harness": "codex", "status": "ready", "version": "0.157.0", "command": ["fixture-codex"], "accountStatus": "confirmed", "fixture": True}))
        stack.enter_context(patch.object(CodexAdapter, "discover_models", return_value=catalog))
        stack.enter_context(patch.object(model_facts, "refresh_model_services", return_value=None))
        stack.enter_context(patch("hey_my_buddy.buddy.harnesses.codex.account_probe.read", return_value=(None, None, None)))
        store = BoardStore(work / "state", clock=lambda: AT)
        store.initialize()
        service = BoardService(store, token="fixture-service-token", control={},
                               on_stop=lambda p: p, on_restart=lambda p: p)
        service.register_console_authority("fixture-console", "f" * 24)

        def call(name, params, *, human=False):
            print("operation:", name, params.get("requestId", params.get("runId", "")), flush=True)
            result = dispatch_local(service, name, {**params, **({"consoleAuthority": "fixture-console"} if human else {})})
            operations.append({"operation": name, "params": params, "response": result})
            return result

        def publish(**changes):
            label = "publish-" + str(len(operations))
            return call("user_policy_publish", {"commandId": label, **changes}, human=True)

        call("harness_set", {"harness": "codex", "path": None})
        call("model_catalog_refresh", {"requestId": "fixture-catalog"})
        publish(buddySettings=[{"buddyId": profile, "enabled": True} for profile in PROFILES])
        publish(preferenceChanges=[{"buddyId": PROFILES[0], "mode": "prefer", "reason": "fixture preference"},
                                   {"buddyId": PROFILES[1], "mode": "exclude", "reason": "fixture exclusion"}],
                modelServiceConcurrency=[{"harness": "codex", "provider": "openai", "model": "fixture-alpha", "limit": 3}])
        publish(configuration={"routerBuddyIds": PROFILES,
                                        "routerRetryIntervalSeconds": 47,
                                        "defaultRoutingMode": "review", "routingBudget": "deep"},
                modelServiceNoteChanges=[{"harness": "codex", "provider": "openai", "model": "fixture-alpha",
                                          "text": "升级夹具的模型备注；应保留。"}])
        call("worker_register", {"workerId": "fixture-worker", "capabilities": ["codex"]})
        for label in ("delivered", "accepted", "failed", "cancelled"):
            submitted = call("workflow_submit", {"requestId": "fixture-" + label, "hostId": "fixture-host",
                "submissionToken": "fixture-submission-token-" + label, "task": "fixture " + label,
                "harness": "codex", "provider": "openai", "model": "fixture-alpha", "effort": "high",
                "cwd": str(work / "workspaces" / label), "executionWorkspace": {"kind": "existing", "access": "read"},
                **({"macroTask": {"title": "Worker 权限档位升级夹具"}} if not ids else {"macroTaskOf": ids["delivered"]})})
            run_id = submitted["runId"]
            ids[label] = run_id
            control = submitted["control"]
            if label == "cancelled":
                call("workflow_cancel", {"runId": run_id, "commandId": "cancel-fixture", "reason": "fixture only", **control})
                continue
            claim = call("worker_claim", {"workerId": "fixture-worker", "claimRequestId": "claim-" + label,
                                          "nonce": "fixture-nonce-00000000000000000000", "runId": run_id})["claim"]
            attempt, turn = claim["attempt"], claim["turn"]
            if label == "failed":
                call("worker_result", {"workerId": "fixture-worker", "attemptId": attempt["attemptId"],
                    "generation": attempt["generation"], "nonce": "fixture-nonce-00000000000000000000",
                    "status": "failed", "shutdownConfirmed": True, "exitCode": 1,
                    "result": {"status": "error", "code": "fixture-failed", "stopEvidence": {"shutdownConfirmed": True}}})
                view = call("workflow_get", {"runId": run_id})
                assert view["state"] == "failed", view
                call("workflow_conclude", {"runId": run_id, "note": "Fixture Host concluded failed work", **control})
                continue
            manifest = turn["input"]["executionWorkspace"]
            sealed = workspace.seal(work / "state", manifest, run_id, attempt["attemptId"])
            record = {"version": 1, "taskId": run_id, "attemptId": attempt["attemptId"],
                      "generation": attempt["generation"], "turnId": turn["turnId"], "resumeMode": turn["resumeMode"],
                      "previousSessionId": turn["input"]["previousSessionId"], "sessionId": "fixture-session-" + label,
                      "promptSha256": "a" * 64, "inputSha256": turn["inputSha256"],
                      "outcome": {"disposition": "completed", "summary": "模拟结果：无模型执行。", "remaining": [],
                                  "decisions": [], "artifacts": [], "request": None},
                      "provenance": {"harness": "codex", "turnEnd": "completed", "outputSchemaValidated": True,
                                     "finalMessageCompleted": True, "nativeTurnStarted": True, "nativeTurnCompleted": True,
                                     "nativeThreadId": "fixture-session-" + label, "nativeTurnId": "fixture-turn-" + label,
                                     "finalItemId": "fixture-item", "eventSeq": 2}}
            call("worker_result", {"workerId": "fixture-worker", "attemptId": attempt["attemptId"],
                "generation": attempt["generation"], "nonce": "fixture-nonce-00000000000000000000", "status": "ok",
                "shutdownConfirmed": True, "exitCode": 0,
                "result": {"status": "ok", "turn": record, "workspaceSeal": sealed,
                           "processState": {"shutdownConfirmed": True}, "fixture": True}})
            view = call("workflow_get", {"runId": run_id})
            assert view["state"] == "delivered", view
            if label == "accepted":
                call("workflow_accept", {"runId": run_id, "artifactId": view["finalArtifactId"],
                    "note": "fixture accepted", "notRequired": "Read-only fixture has no separate target",
                    "keepCheckout": True, **control})
            elif label == "delivered":
                call("workflow_conclude", {"runId": run_id, "note": "Fixture Host concluded the delivered work", **control})
        # Freeze a real Router claim via base operations, then cancel it with
        # explicit synthetic result/stop input; no harness process is spawned.
        call("worker_register", {"workerId": "fixture-router", "adapter": "decision", "capabilities": ["decision"]})
        requested = call("selection_request", {"requestId": "fixture-routing", "task": "fixture routing choice"})
        while requested.get("runId"):
            claim = call("worker_claim", {"workerId": "fixture-router", "claimRequestId": "claim-route-" + requested["runId"],
                "nonce": "fixture-router-nonce-000000000000", "taskId": requested["runId"]}).get("claim")
            assert claim is not None, requested
            call("worker_result", {"workerId": "fixture-router", "attemptId": claim["attempt"]["attemptId"],
                "generation": claim["attempt"]["generation"], "nonce": "fixture-router-nonce-000000000000",
                "status": "failed", "shutdownConfirmed": True, "exitCode": 0,
                "result": {"status": "error", "code": "fixture-no-answer", "stopEvidence": {"shutdownConfirmed": True}}})
            requested = call("selection_get", {"decisionId": requested["decisionId"]})["decision"]
            if requested["status"] != "queued":
                break
        quota = {"source": "fixture/native-quota", "provider": "openai", "observedAt": AT,
                 "ordinaryUsageAllowed": False, "windows": []}
        with store.db.write() as connection:
            native_observations.record_quota(connection, "codex", quota, now=AT)
            quota_routing.redetect(connection, "codex", "openai", now=AT)
        operations.append({"operation": "native_observations.record_quota + quota_routing.redetect", "params": quota, "response": "stored"})
        # A trusted missing-model reading starts the base's pending absence row.
        missing_catalog = {**catalog, "source": "fixture:missing-beta", "providers": [{**catalog["providers"][0], "models": [catalog["providers"][0]["models"][0]]}]}
        catalog_store.record(store.evaluation, missing_catalog)
        operations.append({"operation": "catalog_store.record", "params": missing_catalog, "response": "missing-model-pending"})
        catalog_module.coordinate_catalog_reread(store.directory, "codex", reader=lambda: None, wait=True)
        operations.append({"operation": "coordinate_catalog_reread", "params": {"adapter": "codex"}, "response": "window-written"})
        publish(buddySettings=[{"buddyId": PROFILES[1], "enabled": False}],
                preferenceChanges=[{"buddyId": PROFILES[0], "mode": "pin", "reason": "fixture pinned buddy"}])
        hashing.close()
        # All rows were created by base public operations. Export templates keep
        # their original digests; restoration explicitly rebinds the dependency
        # graph before any migration or data-preservation baseline is taken.
        with store.db.read() as connection:
            raw_sql = "\n".join(connection.iterdump()) + "\n"
            assert connection.execute("SELECT COUNT(*) FROM catalog_pending").fetchone()[0] > 0
            source_schema = list(connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))
        (work / "raw.sql").write_text(raw_sql)
        redactions = [(str(work), "<fixture-root>")]
        sql = raw_sql
        for old, new in redactions:
            sql = sql.replace(old, new)
        header = f"-- Upgrade base: source {BASE}; {BASE_VERSION} / schema {BASE_SCHEMA}\n-- Generated by packaging/generate-upgrade-fixture.py; native facts/results are synthetic.\n-- Path template: restore with tests/python/blackboard/store/upgrade_fixture.py before use.\n"
        output = args.output_dir.resolve()
        output.mkdir(parents=True, exist_ok=True)
        sql_path = output / "board.sql"
        sql_path.write_text(header + sql)
        (output / "catalog.json").write_text(json_text(catalog) + "\n")
        operations_text = json_text(operations).replace(str(work), "<fixture-root>")
        (output / "operations.json").write_text(operations_text + "\n")
        resources = export_resources(work, workspace, preimages)
        (output / "workspace-resources.json").write_text(json.dumps(resources, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        metadata = {"format": 2, "sourceCommit": BASE, "sourceVersion": BASE_VERSION, "schemaVersion": BASE_SCHEMA,
            "sourceTree": subprocess.check_output(["git", "rev-parse", BASE + "^{tree}"], text=True).strip(),
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
            "stableIds": ids,
            "routerBuddyIds": PROFILES, "fixedTimestamp": AT,
            "secret": "ab" * 32, "serviceToken": "fixture-service-token",
            "boundary": "Base BoardService named operations in process; synthetic native discovery and worker result input; no daemon/IPC/model/account read. Text templates retain source digests. load_fixture binds private paths, stable path/inode identities and dependent hashes before upgrade; this preparation is not migration or new native execution. Host conclusions are base history only; real host_seal is tested separately.",
            "restoreInterface": f"load_fixture(state: Path) -> dict; empty private state directory; returns source metadata plus state, fixtureRoot, operations and bindings; schema stays {BASE_SCHEMA}; baseline only after return",
            "schemaSha256": digest(json_text([list(row) for row in source_schema]).encode()),
            "sqlSha256": digest(sql_path.read_bytes()), "operationsSha256": digest((output / "operations.json").read_bytes()),
            "catalogSha256": digest((output / "catalog.json").read_bytes()),
            "resourcesSha256": digest((output / "workspace-resources.json").read_bytes())}
        metadata["loadedSourceModules"] = sorted(str(Path(module.__file__).resolve().relative_to(source))
            for name, module in sys.modules.items() if name.startswith("hey_my_buddy.") and getattr(module, "__file__", None))
        metadata["generatorSha256"] = digest(Path(__file__).read_bytes())
        (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(verify(output, work / "restored.sqlite3"), ensure_ascii=False))


def export_resources(work, workspace, preimages):
    def template(text):
        return text.replace(str(work), "<fixture-root>")

    identities, checkouts = {}, {}
    for label in ("delivered", "accepted", "failed", "cancelled"):
        actual = workspace.inspect(str(work / "workspaces" / label))
        checkouts[label] = template(actual["checkoutRoot"])
        for role, path in (("checkoutId", actual["gitDir"]), ("repositoryId", actual["repositoryPath"])):
            identities[actual[role]] = template(path)
    repository = work / "repository"
    refs = dict(line.split(" ") for line in subprocess.check_output(
        ["git", "-C", str(repository), "for-each-ref", "--format=%(refname) %(objectname)", "refs/hey-my-buddy/"], text=True).splitlines())
    # The fixed input is a root commit: its complete reachable object closure is
    # tiny. Export raw object bytes as hex so no generating repository is needed.
    oids = sorted({line.split(" ")[0] for line in subprocess.check_output(
        ["git", "-C", str(repository), "rev-list", "--objects", WORKSPACE_INPUT], text=True).splitlines()})
    objects = []
    for oid in oids:
        kind = subprocess.check_output(["git", "-C", str(repository), "cat-file", "-t", oid], text=True).strip()
        data = subprocess.check_output(["git", "-C", str(repository), "cat-file", kind, oid])
        objects.append({"oid": oid, "type": kind, "hex": data.hex()})
    files = {str(path.relative_to(work / "state")): template(path.read_text())
             for path in sorted((work / "state/workspaces").rglob("*"))
             if path.is_file() and path.name in {"input.json", "manifest.json", "pending.json", "output.json", "output.patch"}}
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
    metadata = json.loads((output / "metadata.json").read_text())
    assert digest((output / "board.sql").read_bytes()) == metadata["sqlSha256"]
    assert digest((output / "operations.json").read_bytes()) == metadata["operationsSha256"]
    assert digest((output / "catalog.json").read_bytes()) == metadata["catalogSha256"]
    assert digest((output / "workspace-resources.json").read_bytes()) == metadata["resourcesSha256"]
    if restored.exists():
        raise ValueError("restore destination must not exist")
    with sqlite3.connect(restored) as connection:
        connection.executescript((output / "board.sql").read_text())
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert dict(connection.execute("SELECT key,value FROM meta"))["schema_version"] == str(BASE_SCHEMA)
        assert connection.execute("SELECT COUNT(*) FROM macro_tasks").fetchone()[0] == 1
        states = dict(connection.execute("SELECT run_id,state FROM workflow_runs"))
        assert states == {metadata["stableIds"][key]: value for key, value in
                          (("delivered", "concluded"), ("accepted", "accepted"), ("failed", "concluded"), ("cancelled", "cancelled"))}
        keys = dict(connection.execute("SELECT key,value FROM meta"))
        assert set(keys) == {"schema_version", "created_at", "capability_secret"}
        assert list(connection.execute("SELECT buddy_id FROM router_buddies ORDER BY position")) == [(item,) for item in PROFILES]
        assert list(connection.execute("SELECT default_mode,budget_preset,retry_interval_seconds FROM router_settings")) == [("review", "deep", 47)]
        assert keys["capability_secret"] == "ab" * 32
        for table in ("model_service_notes", "evaluation_preferences", "evaluation_catalog", "catalog_current"):
            assert connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] >= 1
        assert connection.execute("SELECT COUNT(*) FROM workflow_host_conclusions WHERE run_id=?", (metadata["stableIds"]["delivered"],)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM workflow_host_conclusions WHERE run_id=?", (metadata["stableIds"]["failed"],)).fetchone()[0] == 1
        schema = [list(row) for row in connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
        assert digest(json_text(schema).encode()) == metadata["schemaSha256"]
        assert "<fixture-root>" in (output / "board.sql").read_text()
        assert "/Users/" not in (output / "board.sql").read_text()
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
