#!/usr/bin/env python3
"""Manually generate the L18 board using the fixed upgrade-base source.

No product installation, daemon, native harness or model is started. The old
named service operations run in process; only native discovery is synthetic.
All work directories are retained. The export includes fixed Git objects,
workspace sidecars and the old hash preimages needed for private path binding.
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

BASE = "e4fbc91696f354991252ea2abddc73d45f286aa5"
INPUT = "df9318f38b732681e1004cf190e71bb4bc25fe17"
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
           if not key.startswith(("BUDDY_", "OPENAI_", "ANTHROPIC_", "DEEPSEEK_", "ZAI_", "CODEX_", "CLAUDE_", "C2_", "GIT_"))
           and key not in {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH"}}
    env.update(BUDDY_STATE_DIR=str(work / "state"), BUDDY_RUNTIME_ROOT=str(work / "runtime"),
               BUDDY_DEV_SOURCE="1", UV_CACHE_DIR=str(work.parent / "uv-cache"),
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
    env["HOME"] = str(work / "home")
    if args.python is None:
        run(["uv", "sync", "--frozen", "--no-install-project", "--link-mode", "copy",
             *(["--offline"] if args.offline else []), "--project", str(source)], cwd=root, env=env)
    # Import an existing commit into a private detached repository. No commit,
    # branch or tag is created/moved; the allocated repository is read only.
    repo = work / "repository"
    run(["git", "init", "--quiet", str(repo)], env=env)
    run(["git", "-C", str(repo), "fetch", "--quiet", "--no-tags", str(root), WORKSPACE_INPUT], env=env)
    (repo / ".git/HEAD").write_text(WORKSPACE_INPUT + "\n")
    run(["git", "-C", str(repo), "read-tree", WORKSPACE_INPUT], env=env)
    run(["git", "-C", str(repo), "checkout-index", "--all"], env=env)
    for label in ("delivered", "accepted", "rejected", "cancelled"):
        run(["git", "-C", str(repo), "worktree", "add", "--quiet", "--detach",
             str(work / "workspaces" / label), WORKSPACE_INPUT], env=env)
    env["PYTHONPATH"] = os.pathsep.join(map(str, (root / "src", root / "tests/python", source / "src")))
    interpreter = [str(args.python.absolute())] if args.python else ["uv", "run", "--no-sync", "--project", str(source), "python"]
    run([*interpreter, str(Path(__file__).resolve()),
         "--populate", "--work-dir", str(work), "--output-dir", str(args.output_dir.resolve()),
         *(["--python", str(args.python.absolute())] if args.python else [])], cwd=root, env=env)


def populate(args):
    # tests/python contains a test package also named buddy. Keep the authorized
    # source/test paths first while explicitly loading the archived base package.
    work = args.work_dir.resolve()
    source = work / "base"
    package = importlib.util.spec_from_file_location("buddy", source / "src/buddy/__init__.py",
                submodule_search_locations=[str(source / "src/buddy")])
    module = importlib.util.module_from_spec(package)
    sys.modules["buddy"] = module
    package.loader.exec_module(module)
    from unittest.mock import patch
    import buddy.db as db_module
    import buddy.harness_health as health_module
    from buddy.adapters.codex import CodexAdapter
    from buddy.service import BoardService, dispatch_local
    from buddy.store import BoardStore
    from buddy import accounts, workspace

    assert Path(db_module.__file__).resolve() == source / "src/buddy/db.py"
    assert db_module.SCHEMA_VERSION == 15
    catalog = {"source": "fixture:adr021-l18", "harnessVersion": "fixture-only",
               "discoveredAt": AT, "discoveries": [{"adapter": "codex", "status": "complete"}],
               "providers": [{"adapter": "codex", "provider": "openai", "efforts": ["high"],
                              "models": [{"id": "fixture-alpha", "efforts": ["high"]},
                                         {"id": "fixture-beta", "efforts": ["high"]}]}]}
    catalog_file = work / "catalog.json"
    catalog_file.write_text(json_text(catalog) + "\n")
    os.environ["BUDDY_MODEL_CATALOG_FILE"] = str(catalog_file)
    operations = []
    ids = {}
    counter = itertools.count(1)
    preimages = {}
    native_sha256 = hashlib.sha256

    def recorded_sha256(data=b"", **kwargs):
        # Observe the real old digest; do not alter any hash or its validation.
        result = native_sha256(data, **kwargs)
        caller = sys._getframe(1)
        ascii_json = caller.f_globals.get("__name__") == "buddy.workspace"
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
        stack.enter_context(patch.object(health_module, "_snapshot", return_value={"fixture": True}))
        stack.enter_context(patch.object(health_module, "_discover", return_value={
            "adapter": "codex", "status": "ready", "version": "0.157.0", "fixture": True}))
        stack.enter_context(patch.object(CodexAdapter, "discover_models", return_value=catalog))
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

        def publish(kind, **changes):
            label = "publish-" + str(len(operations))
            grant = call("evaluation_write_begin", {"requestId": label,
                         "expectedRevision": store.evaluation.snapshot({})["tableRevision"], "kind": kind}, human=kind == "human")
            return call("user_policy_publish" if kind == "human" else "assessment_publish",
                        {"commandId": label, "writerId": grant["writerId"], "generation": grant["generation"],
                         "writerToken": grant["writerToken"], "expectedRevision": grant["tableRevision"], **changes}, human=kind == "human")

        call("harness_set", {"adapter": "codex", "path": None})
        call("model_catalog_refresh", {"requestId": "fixture-catalog"})
        publish("human", profileSettings=[{"profileId": profile, "enabled": True} for profile in PROFILES])
        publish("human", configuration={"fastRouterProfileId": PROFILES[0], "reviewRouterProfileId": PROFILES[1],
                                        "defaultRoutingMode": "review", "routingBudget": "standard"},
                familyAnnotationChanges=[{"adapter": "codex", "provider": "openai", "model": "fixture-alpha",
                                          "text": "升级夹具的家族备注；应保留。"}])
        publish("maintenance", cards=[{"profileId": PROFILES[0], "summary": "升级夹具的评价；应保留。",
                                       "strengths": [], "limitations": [], "risks": [], "evidenceIds": []}])
        call("worker_register", {"workerId": "fixture-worker", "capabilities": ["codex"]})
        for label in ("delivered", "accepted", "rejected", "cancelled"):
            submitted = call("workflow_submit", {"requestId": "fixture-" + label, "hostId": "fixture-host",
                "submissionToken": "fixture-submission-token-" + label, "task": "fixture " + label,
                "adapter": "codex", "provider": "openai", "model": "fixture-alpha", "effort": "high",
                "cwd": str(work / "workspaces" / label), "executionWorkspace": {"kind": "existing", "access": "read"},
                **({"objective": {"title": "ADR-021 L18 升级夹具"}} if not ids else {"objectiveOf": ids["delivered"]})})
            run_id = submitted["runId"]
            ids[label] = run_id
            control = submitted["control"]
            if label == "cancelled":
                call("workflow_cancel", {"runId": run_id, "commandId": "cancel-fixture", "reason": "fixture only", **control})
                continue
            claim = call("worker_claim", {"workerId": "fixture-worker", "claimRequestId": "claim-" + label,
                                          "nonce": "fixture-nonce-00000000000000000000", "runId": run_id})["claim"]
            attempt, turn = claim["attempt"], claim["turn"]
            manifest = turn["input"]["executionWorkspace"]
            sealed = workspace.seal(work / "state", manifest, run_id, attempt["attemptId"])
            record = {"version": 1, "taskId": run_id, "attemptId": attempt["attemptId"],
                      "generation": attempt["generation"], "turnId": turn["turnId"], "resumeMode": turn["resumeMode"],
                      "previousSessionId": turn["input"]["previousSessionId"], "sessionId": "fixture-session-" + label,
                      "promptSha256": "a" * 64, "inputSha256": turn["inputSha256"],
                      "outcome": {"disposition": "completed", "summary": "模拟结果：无模型执行。", "remaining": [],
                                  "decisions": [], "artifacts": [], "request": None},
                      "provenance": {"adapter": "codex", "turnEnd": "completed", "outputSchemaValidated": True,
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
            if label in ("accepted", "rejected"):
                if label == "accepted":
                    integration = call("workflow_integration_record", {"runId": run_id, "commandId": "integrate-fixture",
                        "expectedRevision": view["revision"], "artifactId": view["finalArtifactId"], "notRequired": True,
                        "reason": "只读夹具，无文件变更。", **control})
                    view = call("workflow_get", {"runId": run_id})
                call("workflow_acknowledge", {"runId": run_id, "commandId": "review-" + label,
                    "expectedRevision": view["revision"], "artifactId": view["finalArtifactId"],
                    "verdict": label, "note": "fixture " + label, "evidence": ["fixture:model-free"], **control})
        # Add the reservation only after the sample workflows: their stable IDs
        # keep the same UUID sequence. The base owns the active→uncertain write;
        # no native process is started and no shutdown evidence is fabricated.
        with store.db.read() as connection:
            account = accounts.selection(connection, "codex")
        purpose = "fixture-shutdown-unconfirmed"
        with accounts.native_operation(store.db, account, purpose) as stop:
            assert stop["shutdownConfirmed"] is False
        account_operation = {"source": "buddy.accounts.native_operation", "account": account,
                             "purpose": purpose, **stop}
        operations.append({"operation": "accounts.native_operation", "params": {"account": account, "purpose": purpose},
                           "response": dict(stop)})
        hashing.close()
        # All rows were created by base public operations. Export templates keep
        # their original digests; restoration explicitly rebinds the dependency
        # graph before any migration or data-preservation baseline is taken.
        with store.db.read() as connection:
            raw_sql = "\n".join(connection.iterdump()) + "\n"
            source_schema = list(connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))
        (work / "raw.sql").write_text(raw_sql)
        redactions = [(str(work), "<fixture-root>")]
        sql = raw_sql
        for old, new in redactions:
            sql = sql.replace(old, new)
        header = f"-- ADR-021 L18: source {BASE}; 0.27.0 / schema 15\n-- Generated by packaging/generate-upgrade-fixture.py; native facts/results are synthetic.\n-- Path template: restore with tests/python/blackboard/store/upgrade_fixture.py before use.\n"
        output = args.output_dir.resolve()
        output.mkdir(parents=True, exist_ok=True)
        sql_path = output / "board.sql"
        sql_path.write_text(header + sql)
        (output / "catalog.json").write_text(json_text(catalog) + "\n")
        operations_text = json_text(operations).replace(str(work), "<fixture-root>")
        (output / "operations.json").write_text(operations_text + "\n")
        resources = export_resources(work, workspace, preimages)
        (output / "workspace-resources.json").write_text(json.dumps(resources, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        metadata = {"format": 2, "sourceCommit": BASE, "inputCommit": INPUT, "sourceVersion": "0.27.0", "schemaVersion": 15,
            "sourceTree": subprocess.check_output(["git", "rev-parse", BASE + "^{tree}"], text=True).strip(),
            "workspaceInputCommit": WORKSPACE_INPUT,
            "generationCommand": "python packaging/generate-upgrade-fixture.py " +
                ("--python <prepared-python>" if args.python else "--offline") +
                " --work-dir " + str(work.relative_to(Path(__file__).resolve().parents[1])) +
                " --output-dir tests/fixtures/adr021-l18",
            "dependencyMode": "prepared-read-only-interpreter" if args.python else "base-frozen-uv-environment",
            "dependencyVersions": {name: importlib.metadata.version(name) for name in ("c-two", "fastdb4py", "numpy", "portalocker")},
            "sourceFiles": {str(p.relative_to(source)): digest(p.read_bytes()) for p in
                            [*sorted((source / "src/buddy").rglob("*.py")), source / "pyproject.toml",
                             source / "uv.lock", source / "src/buddy/review-certificates.json"]},
            "stableIds": ids, "accountOperation": account_operation,
            "routerSlots": {"fast": PROFILES[0], "review": PROFILES[1]}, "fixedTimestamp": AT,
            "secret": "ab" * 32, "serviceToken": "fixture-service-token",
            "boundary": "Old BoardService named operations in process; synthetic native discovery and worker result input; no daemon/IPC/model/account read. Text templates retain source digests. load_fixture binds private paths, legacy device/inode identities and dependent hashes before upgrade; this preparation is not migration or new native execution. Real host_seal is tested separately; service workflow_conclude after upgrade remains Host verification.",
            "restoreInterface": "load_fixture(state: Path) -> dict; empty private state directory; returns source metadata plus state, fixtureRoot, operations and bindings; schema stays 15; baseline only after return",
            "schemaSha256": digest(json_text([list(row) for row in source_schema]).encode()),
            "sqlSha256": digest(sql_path.read_bytes()), "operationsSha256": digest((output / "operations.json").read_bytes()),
            "catalogSha256": digest((output / "catalog.json").read_bytes()),
            "resourcesSha256": digest((output / "workspace-resources.json").read_bytes())}
        metadata["loadedSourceModules"] = sorted(str(Path(module.__file__).resolve().relative_to(source))
            for name, module in sys.modules.items() if name.startswith("buddy.") and getattr(module, "__file__", None))
        metadata["generatorSha256"] = digest(Path(__file__).read_bytes())
        (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(verify(output, work / "restored.sqlite3"), ensure_ascii=False))


def export_resources(work, workspace, preimages):
    def template(text):
        return text.replace(str(work), "<fixture-root>")

    identities, checkouts = {}, {}
    for label in ("delivered", "accepted", "rejected", "cancelled"):
        actual = workspace.inspect(str(work / "workspaces" / label))
        checkouts[label] = template(actual["checkoutRoot"])
        for role, path in (("checkoutId", actual["gitDir"]), ("repositoryId", actual["repositoryPath"])):
            identities[actual[role]] = template(path)
    repository = work / "repository"
    refs = dict(line.split(" ") for line in subprocess.check_output(
        ["git", "-C", str(repository), "for-each-ref", "--format=%(refname) %(objectname)", "refs/buddy/"], text=True).splitlines())
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
        assert dict(connection.execute("SELECT key,value FROM meta"))["schema_version"] == "15"
        assert connection.execute("SELECT COUNT(*) FROM objectives").fetchone()[0] == 1
        states = dict(connection.execute("SELECT run_id,state FROM workflow_runs"))
        assert states == {metadata["stableIds"][key]: value for key, value in
                          (("delivered", "delivered"), ("accepted", "accepted"), ("rejected", "awaiting-host"), ("cancelled", "cancelled"))}
        keys = dict(connection.execute("SELECT key,value FROM meta"))
        assert keys["router_configuration_version"] == "1"
        assert keys["router_fast_profile_id"] == PROFILES[0] and keys["router_review_profile_id"] == PROFILES[1]
        assert keys["capability_secret"] == "ab" * 32
        provenance = metadata["accountOperation"]
        assert provenance["source"] == "buddy.accounts.native_operation"
        assert provenance["shutdownConfirmed"] is False
        account = provenance["account"]
        assert json.loads(keys[provenance["operationId"]]) == {
            "adapter": account["adapter"], "source": account["source"],
            "credentialRevision": account["credentialRevision"],
            "purpose": provenance["purpose"], "status": "uncertain"}
        for table in ("evaluation_cards", "family_annotations", "evaluation_catalog", "catalog_current"):
            assert connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] >= 1
        reviews = {json.loads(row[0])["verdict"] for row in connection.execute(
            "SELECT payload_json FROM events WHERE kind='workflow.acknowledged'")}
        assert reviews == {"accepted", "rejected"}
        assert connection.execute("SELECT COUNT(*) FROM workflow_host_conclusions WHERE run_id=?", (metadata["stableIds"]["delivered"],)).fetchone()[0] == 0
        schema = [list(row) for row in connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
        assert digest(json_text(schema).encode()) == metadata["schemaSha256"]
        assert "<fixture-root>" in (output / "board.sql").read_text()
        assert "/Users/" not in (output / "board.sql").read_text()
        return {"integrity": "ok", "foreignKeyViolations": 0, "schema": 15, "states": states,
                "tables": sum(row[0] == "table" and row[1] != "sqlite_sequence" for row in schema),
                "events": connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], "sourceSchemaMatches": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("tests/fixtures/adr021-l18"))
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
