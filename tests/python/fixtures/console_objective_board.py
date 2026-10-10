"""Reusable, fictional console/objective board and real HTTP measurement.

Run this file in a fresh interpreter with --source (or --baseline-source),
--state-root, --runtime-root and --output-root. All three writable roots must be
explicit and empty. Nothing is copied from another board; reuse is refused.
The source supplies its own support/MockWorkspace/catalog/native-turn fixtures.
No daemon, Worker process, harness, account discovery or model is launched.

Full scale follows the published *scale*, not an unpublished historical fixture:
60 macros, 360 governed runs (including 10 helpers), 40 plain commands, 130
pending roots, 100 private SQL-injected 1,900,000-byte execution results and
4,000 declared evidence files. Smoke retains every relationship at small scale.
Only execution-result payloads bypass public admission limits; their short,
valid turn outcomes and all relational identities remain intact.

Each coding/scenario gets a discarded warm-up and >=5 retained samples. Writes
finish before the request. HTTP do_GET entry/return and objective_list entry/
return are timed separately from client request/read RTT. Condition waits and
completion bookkeeping are outside the handler timers. Results include
every raw sample, standard ETags/codings, transmitted/decompressed body bytes
and _summary calls. No product headers or response protocols are added.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import http.client
import importlib
import json
import math
import os
import random
import statistics
import sys
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

BASELINE_SHA = "613faa4089c02ef1d5ef9b1d0743920de6f4a7cc"
CONFIGURATION = {"adapter": "dsh", "provider": "deepseek-official",
                 "model": "deepseek-flash", "effort": "off"}
PROFILE_ID = ":".join(CONFIGURATION.values())
NONCE = "synthetic-nonce-1"
SCENARIOS = ("no-write", "unrelated-meta", "unrelated-plain",
             "other-macro", "single-macro", "all-macros")


@dataclass(frozen=True)
class Recipe:
    macros: int = 60
    members_per_macro: int = 6
    helpers: int = 10
    plain: int = 40
    pending_roots: int = 130
    large_results: int = 100
    result_bytes: int = 1_900_000
    evidence_files: int = 4000
    routed_roots: int = 6
    accepted_roots: int = 10

    @classmethod
    def smoke(cls):
        return cls(macros=3, members_per_macro=4, helpers=1, plain=2,
                   pending_roots=4, large_results=2, result_bytes=8192,
                   evidence_files=40, routed_roots=1, accepted_roots=1)


def clean_environment(environment: dict) -> dict:
    """Remove inherited authority *before* adding explicit private paths."""
    return {key: value for key, value in environment.items()
            if not key.startswith(("BUDDY_", "ANTHROPIC_"))
            and key not in {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"}}


def select_source(source: Path):
    """Refuse a mixed-source interpreter; CLI imports the selected tests first."""
    source = Path(source).resolve()
    for relative in ("src", "tests/python"):
        path = str(source / relative)
        if path not in sys.path:
            sys.path.insert(0, path)
    support = importlib.import_module("support")
    from hey_my_buddy.blackboard.tasks import objectives
    for module, relative in ((support, "tests/python"), (objectives, "src")):
        if not Path(module.__file__).resolve().is_relative_to(source / relative):
            raise ValueError("Source already imported from another tree; use a fresh interpreter")
    return support


def _empty_root(path: Path) -> Path:
    path = Path(path).resolve()
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"Refusing nonempty root: {path}; choose a fresh private root")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


@contextmanager
def private_environment(state: Path, runtime: Path, source: Path):
    old = dict(os.environ)
    os.environ.clear()
    os.environ.update(clean_environment(old))
    os.environ.update(BUDDY_STATE_DIR=str(state), BUDDY_RUNTIME_ROOT=str(runtime),
                      BUDDY_DEV_SOURCE="1", BUDDY_CONSOLE_PORT="0",
                      BUDDY_MODEL_FACTS_FILE=str(state / "offline-model-facts.json"),
                      PYTHONPATH=os.pathsep.join((str(source / "src"), str(source / "tests/python"))))
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old)


class SyntheticBoard:
    """Own one newly generated board; closing preserves all files and SQL."""

    def __init__(self, state: Path, runtime: Path, source: Path, *, seed=613,
                 recipe: Recipe | None = None):
        self.source = Path(source).resolve()
        self.state, self.runtime = Path(state).resolve(), Path(runtime).resolve()
        if self.state == self.runtime or self.state.is_relative_to(self.runtime) or self.runtime.is_relative_to(self.state):
            raise ValueError("State and runtime must be separate private roots")
        # Validate both before creating either one's contents.
        for root in (self.state, self.runtime):
            if root.exists() and any(root.iterdir()):
                raise ValueError(f"Refusing nonempty root: {root}")
        self.state, self.runtime = _empty_root(self.state), _empty_root(self.runtime)
        self.seed, self.recipe = seed, recipe or Recipe()
        self.stack = ExitStack()
        self.board = None
        self.macros: list[str] = []
        self.roots: list[str] = []
        self.helpers: list[str] = []
        self.completed_attempts: list[str] = []
        self.plain: list[str] = []
        self.controls: dict[str, dict] = {}
        self.operations = 0

    def __enter__(self):
        try:
            self.stack.enter_context(private_environment(self.state, self.runtime, self.source))
            support = select_source(self.source)
            from mock_workspace import MockWorkspace
            from hey_my_buddy.blackboard.tasks import workflow
            from hey_my_buddy.buddy.harnesses.dsh.adapter import DshAdapter
            self.clock = support.FakeClock()
            self.workspace = MockWorkspace(self.state)
            self.stack.enter_context(patch.object(workflow, "_workspace_module", self.workspace))
            # Same seam as WorkflowTestCase: synthetic receipts, no native executor.
            executor = SimpleNamespace(native_resume=False, validate_turn_provenance=lambda record: None)
            self.stack.enter_context(patch.object(workflow.WorkflowCoordinator, "_execution_adapter", return_value=executor))
            # Existing Router tests use these capability-only fixture seams.
            # There is no start_review/Worker execution in this generator.
            self.stack.enter_context(patch.object(DshAdapter, "read_only_structured", True))
            self.stack.enter_context(patch.object(DshAdapter, "local_read_only_check", return_value={
                "eligible": True, "reasonCode": None, "reason": None,
                "systemSandbox": False, "sameAttemptContinuation": False}))
            self.stack.enter_context(patch.object(DshAdapter, "available", return_value=(True, "synthetic fixture")))
            # Fail closed if any generation/read path tries launching a process.
            self.launch_guard = self.stack.enter_context(patch("subprocess.Popen", side_effect=AssertionError("No subprocess in synthetic board")))
            os.environ["BUDDY_MODEL_CATALOG_FILE"] = str(support.write_catalog_fixture(self.state))
            self.board = support.InProcessBoard(self.state, clock=self.clock)
            with ExitStack() as generation:
                ids = random.Random(self.seed)
                generation.enter_context(patch("uuid.uuid4", side_effect=lambda: uuid.UUID(int=ids.getrandbits(128), version=4)))
                self.generate()
            self.initial_facts = self.facts()
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *_):
        self.close()

    def close(self):
        # InProcessBoard.close unlinks its DB; only stop this fixture's console.
        if self.board is not None and self.board.console is not None:
            self.board.console.close()
        self.stack.close()

    def call(self, operation, params):
        self.clock.advance(1)
        self.operations += 1
        return self.board.call(operation, params)

    def _submit(self, name, *, objective=None, objective_id=None, routed=False):
        cwd = self.state / "fictional-project" / name
        cwd.mkdir(parents=True, exist_ok=True)
        params = {"requestId": f"synthetic-{self.seed}-{name}", "hostId": "fictional-host",
                  "task": f"Fictional work {name}", "title": f"Synthetic {name}", "cwd": str(cwd),
                  "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["src/feature.py"]}}
        if not routed:
            params.update(CONFIGURATION)
        if objective is not None:
            params["objective"] = {"title": objective, "description": "Fictional benchmark; no real work."}
        if objective_id is not None:
            params["objectiveId"] = objective_id
        view = self.call("workflow_submit", params)
        self.controls[view["runId"]] = view["control"]
        if routed:
            self._select(view)
        return view

    def _select(self, view):
        from blackboard.routing.fixtures.router_tool_receipt import claim_tool_receipt
        response = self.call("worker_claim", {"workerId": "synthetic-router", "claimRequestId": f"route-{view['runId']}",
                                               "nonce": NONCE, "runId": view["routing"]["taskId"]})
        if response.get("claim") is None:
            raise AssertionError({"syntheticRouterClaim": response, "routing": view.get("routing"), "waitReason": view.get("waitReason")})
        claim = response["claim"]
        attempt, document = claim["attempt"], claim["decisionInput"]
        self.call("worker_result", {
            "workerId": "synthetic-router", "attemptId": attempt["attemptId"], "generation": attempt["generation"],
            "nonce": NONCE, "status": "ok", "shutdownConfirmed": True,
            "result": {**claim_tool_receipt(claim), "status": "ok", "operation": "select",
                       "tableRevision": document["tableRevision"],
                       "stopEvidence": {"shutdownConfirmed": True, "native": {"shutdownConfirmed": True}},
                       "usage": {"elapsedMs": 0, "toolCalls": 0},
                       "inputVerification": {"unchanged": True, "snapshotSha256": "fixture-digest",
                                             "manifestSha256": document["executionWorkspace"]["manifestSha256"]},
                       "decision": {"profileId": PROFILE_ID, "reason": "Fictional fixture selection", "evidence": []}},
        })

    def _finish(self, run_id, *, disposition="completed"):
        from protocol.fixtures.native_turn import claim_record
        claim = self.call("worker_claim", {"workerId": "synthetic-worker", "claimRequestId": f"execute-{run_id}",
                                           "nonce": NONCE, "runId": run_id})["claim"]
        attempt = claim["attempt"]
        outcome = {"disposition": disposition, "summary": "Fictional short display summary.",
                   "decisions": [], "remaining": [], "artifacts": [], "request": None}
        if disposition == "assistance":
            outcome.update(remaining=["Fictional Host review"], request={
                "summary": "Fictional review", "attempted": "Synthetic implementation",
                "neededWork": ["Inspect fictional output"], "acceptance": "Fictional review recorded",
                "expectedArtifacts": ["Fictional decision"]})
        record = claim_record(claim, outcome=outcome)
        evidence = self.state / "attempts" / run_id / attempt["attemptId"]
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "turn-output.json").write_text(json.dumps(record))
        result = {"status": "ok", "mode": "run", "processState": {"shutdownConfirmed": True},
                  "logPaths": {}, "turn": record, "turnResultPath": str(evidence / "turn-output.json"),
                  "workspaceSeal": self.workspace.seal(self.state, claim["turn"]["input"]["executionWorkspace"],
                                                       run_id, attempt["attemptId"])}
        self.call("worker_result", {"workerId": "synthetic-worker", "attemptId": attempt["attemptId"],
                                    "generation": attempt["generation"], "nonce": NONCE, "status": "ok",
                                    "result": result, "shutdownConfirmed": True, "exitCode": 0})
        if disposition == "completed":
            self.completed_attempts.append(attempt["attemptId"])

    def _helper(self, parent, name):
        view = self.call("workflow_get", {"runId": parent})
        result = self.call("workflow_decide", {
            "runId": parent, **self.controls[parent], "requestId": view["activeRequest"]["requestId"],
            "commandId": f"approve-{parent}", "expectedRevision": view["revision"], "decision": "approve",
            "helpers": [{**CONFIGURATION, "requestId": name, "task": "Fictional helper",
                         "cwd": str(self.state / "fictional-project"),
                         "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["src/feature.py"]}}],
        })
        helper = result["children"][0]["taskId"]
        self.helpers.append(helper)
        self._finish(helper, disposition="assistance")

    def generate(self):
        r = self.recipe
        # MockWorkspace recognizes this empty marker as one fictional project.
        # It is not a Git repository and no Git operation is performed.
        (self.state / "fictional-project/.git").mkdir(parents=True)
        self.call("model_catalog_refresh", {"requestId": "synthetic-catalog"})
        with self.board.store.db.read() as connection:
            revision = connection.execute("SELECT table_revision FROM evaluation_state WHERE id=1").fetchone()[0]
        grant = self.board.console_call("evaluation_write_begin", {"requestId": "synthetic-policy", "expectedRevision": revision, "kind": "human"})
        self.board.console_call("user_policy_publish", {
            "commandId": "synthetic-policy", "writerId": grant["writerId"], "generation": grant["generation"],
            "writerToken": grant["writerToken"], "expectedRevision": grant["tableRevision"],
            "profileSettings": [{"profileId": item, "enabled": True} for item in
                                (PROFILE_ID, "dsh:deepseek-official:deepseek-v4-pro:high")],
            "configuration": {"defaultRoutingMode": "review", "routerProfileIds": [PROFILE_ID]},
        })
        self.call("worker_register", {"workerId": "synthetic-worker", "capabilities": ["dsh", "command"]})
        self.call("worker_register", {"workerId": "synthetic-router", "adapter": "decision", "capabilities": ["decision"]})
        pending, accepted = 0, 0
        for macro in range(r.macros):
            objective_id = None
            parent = None
            for member in range(r.members_per_macro - int(macro < r.helpers)):
                view = self._submit(f"macro-{macro:03d}-root-{member}",
                                    objective=f"Fictional macro {macro:03d}" if objective_id is None else None,
                                    objective_id=objective_id, routed=member == 0 and macro < r.routed_roots)
                run_id = view["runId"]
                self.roots.append(run_id)
                if objective_id is None:
                    objective_id = view["objectiveId"]
                    self.macros.append(objective_id)
                    parent = run_id
                is_pending = pending < r.pending_roots
                self._finish(run_id, disposition="assistance" if is_pending else "completed")
                if is_pending:
                    pending += 1
                elif accepted < r.accepted_roots:
                    delivered = self.call("workflow_get", {"runId": run_id})
                    self.call("workflow_accept", {"runId": run_id, **self.controls[run_id],
                                                  "artifactId": delivered["finalArtifactId"],
                                                  "note": "Synthetic fixture review only",
                                                  "notRequired": "Fictional fixture has no Git target"})
                    accepted += 1
            if macro < r.helpers:
                self._helper(parent, f"synthetic-helper-{macro}")
        for number in range(r.plain):
            view = self.call("task_submit", {"requestId": f"synthetic-plain-{number}", "adapter": "command",
                                             "argv": ["synthetic-never-executed"], "task": "Fictional plain record",
                                             "cwd": str(self.state / "fictional-project")})
            self.plain.append(view["task"]["taskId"])
        self._inject_results()
        self._evidence()

    def _inject_results(self):
        """PRIVATE FIXTURE SQL INJECTION, not a production result submission."""
        rng = random.Random(self.seed + 1)
        with self.board.store.db.write() as connection:
            for attempt in self.completed_attempts[:self.recipe.large_results]:
                result = json.loads(connection.execute("SELECT result_json FROM attempts WHERE attempt_id=?", (attempt,)).fetchone()[0])
                result["privateSyntheticInjection"] = {"seed": self.seed, "trace": ""}
                size = len(json.dumps(result, separators=(",", ":")).encode())
                block = rng.randbytes(256).hex()
                length = self.recipe.result_bytes - size
                if length < 0:
                    raise ValueError("result_bytes is smaller than the admitted result")
                result["privateSyntheticInjection"]["trace"] = (block * math.ceil(length / len(block)))[:length]
                payload = json.dumps(result, separators=(",", ":"))
                connection.execute("UPDATE attempts SET result_json=? WHERE attempt_id=?", (payload, attempt))

    def _evidence(self):
        from hey_my_buddy.protocol.attempt_evidence import FILES, is_evidence
        with self.board.store.db.read() as connection:
            attempts = list(connection.execute("SELECT task_id,attempt_id FROM attempts ORDER BY rowid"))
        paths = [self.state / "attempts" / row["task_id"] / row["attempt_id"] for row in attempts]
        count = sum(1 for path in paths for file in path.rglob("*") if file.is_file() and is_evidence(file.relative_to(path)))
        for name in sorted(FILES):
            for path in paths:
                if count >= self.recipe.evidence_files:
                    return
                file = path / name
                if file.exists():
                    continue
                path.mkdir(parents=True, exist_ok=True)
                file.write_text(json.dumps({"synthetic": True, "seed": self.seed, "ordinal": count}))
                count += 1
        raise ValueError("Recipe requests more evidence than admitted attempts can contain")

    def facts(self):
        from hey_my_buddy.protocol.attempt_evidence import is_evidence
        from hey_my_buddy.blackboard.tasks.objectives import pending_root_count
        with self.board.store.db.read() as connection:
            tables = ("tasks", "workflow_runs", "objectives", "attempts", "workflow_turns", "workflow_requests",
                      "workflow_children", "workflow_routes", "decision_requests", "events", "artifacts",
                      "workflow_artifacts", "workflow_integrations", "workflow_continuations")
            counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}
            lengths = [row[0] for row in connection.execute("SELECT length(CAST(result_json AS BLOB)) FROM attempts WHERE json_type(result_json,'$.privateSyntheticInjection')='object'")]
            outcomes = [json.loads(row[0]) for row in connection.execute("SELECT outcome_json FROM workflow_turns WHERE outcome_json IS NOT NULL")]
            fk = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            schema = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
            event_kinds = dict(connection.execute("SELECT kind,COUNT(*) FROM events GROUP BY kind"))
            states = dict(connection.execute("SELECT state,COUNT(*) FROM workflow_runs GROUP BY state"))
            evidence = [self.state / "attempts" / row[0] / row[1] for row in connection.execute("SELECT task_id,attempt_id FROM attempts")]
        files = [file for path in evidence for file in path.rglob("*") if file.is_file()]
        declared = [file for path in evidence for file in path.rglob("*") if file.is_file() and is_evidence(file.relative_to(path))]
        db_path = self.board.store.db.path
        return {"synthetic": True, "recipeVersion": 1, "seed": self.seed, "recipe": asdict(self.recipe),
                "schemaVersion": int(schema), "counts": counts, "workflowStates": states, "eventKinds": event_kinds,
                "governedRuns": counts["workflow_runs"], "plainRecords": len(self.plain),
                "pendingRoots": pending_root_count(self.board.store),
                "largeResultBytes": lengths, "largeResultTotalBytes": sum(lengths),
                "maxDisplaySummaryCharacters": max(len(outcome["summary"]) for outcome in outcomes),
                "attemptFiles": len(files), "attemptFileBytes": sum(file.stat().st_size for file in files),
                "declaredEvidenceFiles": len(declared), "declaredEvidenceBytes": sum(file.stat().st_size for file in declared),
                "databaseBytes": db_path.stat().st_size,
                "databaseSidecarBytes": {suffix: Path(str(db_path) + suffix).stat().st_size if Path(str(db_path) + suffix).exists() else 0 for suffix in ("-wal", "-shm")},
                "foreignKeyViolations": fk, "integrityCheck": integrity,
                "subprocessLaunchAttempts": self.launch_guard.call_count,
                "runtimeEntries": sorted(path.name for path in self.runtime.iterdir()),
                "paths": {"state": "<private-state>", "runtime": "<private-runtime>", "database": "<private-state>/" + db_path.name},
                "injection": "Private fixture UPDATE attempts.result_json only; admitted turn outcome unchanged",
                "boundaries": "Synthetic native/router receipts and MockWorkspace; no native execution or Git verification"}

    def mutate(self, scenario, ordinal, *, relevant_macro, other_macro):
        """One committed private injection; activity/event updates stay atomic."""
        if scenario == "no-write":
            return
        with self.board.store.db.write() as connection:
            if scenario == "unrelated-meta":
                connection.execute("INSERT INTO meta(key,value) VALUES('synthetic-benchmark-counter',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(ordinal),))
            elif scenario == "unrelated-plain":
                connection.execute("UPDATE tasks SET revision=revision+1 WHERE task_id=?", (self.plain[0],))
            else:
                ids = self.macros if scenario == "all-macros" else [other_macro if scenario == "other-macro" else relevant_macro]
                for identifier in ids:
                    run = connection.execute("SELECT run_id FROM workflow_runs WHERE objective_id=? ORDER BY rowid LIMIT 1", (identifier,)).fetchone()[0]
                    connection.execute("UPDATE workflow_runs SET title=? WHERE run_id=?", (f"Fictional revision {scenario} {ordinal}", run))
                    # Reuse the existing event writer and its activity propagation.
                    self.board.store._append_event(connection, "workflow.synthetic_fixture_change", task_id=run,
                                                   payload={"synthetic": True, "scenario": scenario, "ordinal": ordinal})


class HandlerProbe:
    """Sequential correlation; timer ends before acquiring the completion lock."""

    def __init__(self, board):
        self.board = board
        self.condition = threading.Condition()
        self.records = []
        self.local = threading.local()
        self.stack = ExitStack()

    def __enter__(self):
        from hey_my_buddy.blackboard.tasks import objectives
        factory = self.board.console._handler
        summary, producer = objectives._summary, objectives.objective_list
        service_handler = self.board.service.objective_list

        def counted(*args, **kwargs):
            row = getattr(self.local, "row", None)
            if row is not None:
                row["summaryCalls"] += 1
                row["summaryGroups"].append(args[1])
            return summary(*args, **kwargs)

        def timed_producer(*args, **kwargs):
            start = time.perf_counter_ns()
            try:
                return producer(*args, **kwargs)
            finally:
                elapsed = time.perf_counter_ns() - start
                row = getattr(self.local, "row", None)
                if row is not None:
                    row["objectiveListWallNs"].append(elapsed)

        def timed_service_handler(*args, **kwargs):
            start = time.perf_counter_ns()
            try:
                return service_handler(*args, **kwargs)
            finally:
                elapsed = time.perf_counter_ns() - start
                row = getattr(self.local, "row", None)
                if row is not None:
                    row["serviceHandlerWallNs"].append(elapsed)

        def handler_factory():
            handler = factory()
            original = handler.do_GET

            def measured(instance):
                row = {"summaryCalls": 0, "summaryGroups": [], "objectiveListWallNs": [], "serviceHandlerWallNs": []}
                self.local.row = row
                start = time.perf_counter_ns()
                try:
                    return original(instance)
                finally:
                    end = time.perf_counter_ns()
                    row["handlerWallNs"] = end - start
                    self.local.row = None
                    with self.condition:
                        self.records.append(row)
                        self.condition.notify_all()
            handler.do_GET = measured
            return handler

        self.stack.enter_context(patch.object(objectives, "_summary", counted))
        self.stack.enter_context(patch.object(objectives, "objective_list", timed_producer))
        self.stack.enter_context(patch.object(self.board.service, "objective_list", timed_service_handler))
        self.stack.enter_context(patch.object(self.board.console, "_handler", handler_factory))
        try:
            self.url = self.board.console.start()["url"]
            parts = urlsplit(self.url)
            self.connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=120)
        except BaseException:
            self.board.console.close()
            self.stack.close()
            raise
        return self

    def __exit__(self, *_):
        self.connection.close()
        self.board.console.close()
        self.stack.close()

    def request(self, coding, etag=None, *, query="limit=50"):
        headers = {"Accept-Encoding": coding}
        if etag is not None:
            headers["If-None-Match"] = etag
        expected = len(self.records) + 1
        start = time.perf_counter_ns()
        self.connection.request("GET", "/api/objectives?" + query, headers=headers)
        response = self.connection.getresponse()
        body = response.read()
        end = time.perf_counter_ns()
        # The request is fully read. Thread completion is *not* client RTT.
        with self.condition:
            if not self.condition.wait_for(lambda: len(self.records) >= expected, timeout=120):
                raise TimeoutError("Handler did not return")
        row = dict(self.records[expected - 1])
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        encoding = response_headers.get("content-encoding")
        raw = gzip.decompress(body) if encoding == "gzip" else body
        if response.status not in (200, 304):
            raise AssertionError((response.status, raw[:500]))
        parsed = json.loads(raw) if raw else None
        row.update(clientRoundTripNs=end - start, status=response.status, requestCoding=coding,
                   ifNoneMatch=etag, etag=response_headers.get("etag"), contentEncoding=encoding,
                   wireBodyBytes=len(body), rawBodyBytes=len(raw),
                   gzipBodyBytes=len(body) if encoding == "gzip" else None,
                   bodySha256=hashlib.sha256(body).hexdigest(), rawSha256=hashlib.sha256(raw).hexdigest(),
                   responseHeaders=response_headers, returnedMacros=len(parsed["objectives"]) if parsed else 0,
                   totalMacros=parsed["total"] if parsed else None)
        return row, parsed


def distribution(values):
    """p90 uses the nearest-rank definition, recorded rather than implied."""
    values = sorted(values)
    return {"samples": len(values), "median": statistics.median(values),
            "p90": values[math.ceil(0.9 * len(values)) - 1], "max": max(values)}


def measure(fixture: SyntheticBoard, *, samples=5, on_sample=None):
    if samples < 5:
        raise ValueError("At least five retained samples per scenario/coding are required")
    raw_samples, warmups, aggregates = [], [], {}
    with HandlerProbe(fixture.board) as probe:
        first, page = probe.request("identity")
        warmups.append({"scenario": "initial-page", **first})
        relevant = page["objectives"][0]["objectiveId"]
        # Other macro starts off-page at full scale; its activity may move it
        # onto the page. It is unrelated to the pinned relevant macro, not to
        # the entire board-wide list. Smoke uses one group's stable task-name
        # filter and retains limit=50. The exact query is saved in every sample.
        visible = {item["objectiveId"] for item in page["objectives"]}
        outside = [item for item in fixture.macros if item not in visible]
        other = outside[0] if outside else next(item for item in fixture.macros if item != relevant)
        query = "limit=50" if outside else f"limit=50&query=macro-{fixture.macros.index(relevant):03d}"
        ordinal = 0
        for scenario in SCENARIOS:
            for coding in ("identity", "gzip"):
                # Prime the exact route/coding, then warm up this write scenario.
                prime, _ = probe.request(coding, query=query)
                warmups.append({"scenario": scenario, "phase": "prime", **prime})
                etag = prime["etag"]
                retained = []
                for sample in range(samples + 1):
                    ordinal += 1
                    fixture.mutate(scenario, ordinal, relevant_macro=relevant, other_macro=other)
                    row, _ = probe.request(coding, etag, query=query)
                    row.update(scenario=scenario, sample=sample, mutationOrdinal=ordinal, query=query)
                    etag = row["etag"]
                    if sample == 0:
                        warmups.append({"phase": "discarded-warmup", **row})
                    else:
                        retained.append(row)
                        raw_samples.append(row)
                        if on_sample:
                            on_sample(row)
                aggregates[f"{scenario}/{coding}"] = {
                    "handlerWallMs": distribution([row["handlerWallNs"] / 1e6 for row in retained]),
                    "clientRoundTripMs": distribution([row["clientRoundTripNs"] / 1e6 for row in retained]),
                    "objectiveListWallMs": distribution([sum(row["objectiveListWallNs"]) / 1e6 for row in retained]),
                    "serviceHandlerWallMs": distribution([sum(row["serviceHandlerWallNs"]) / 1e6 for row in retained]),
                    "summaryCalls": distribution([row["summaryCalls"] for row in retained]),
                    "statuses": [row["status"] for row in retained],
                    "wireBodyBytes": [row["wireBodyBytes"] for row in retained],
                    "rawBodyBytes": [row["rawBodyBytes"] for row in retained],
                    "gzipBodyBytes": [row["gzipBodyBytes"] for row in retained],
                }
    return {"samples": raw_samples, "warmups": warmups, "aggregates": aggregates,
            "scenarios": list(SCENARIOS), "pageLimit": 50, "query": query,
            "relevantMacro": relevant, "otherMacro": other, "samplesPerScenarioCoding": samples,
            "p90Definition": "nearest rank: sorted[ceil(0.9*n)-1]",
            "method": "Real Console HTTP do_GET, BoardService.objective_list and objectives.objective_list entry/return boundaries; perf_counter_ns. Writes/init/Condition waits/completion bookkeeping outside timers. _summary counter wrappers execute within real handler wall; overhead is not subtracted or fabricated. No per-call timing of _summary and no profiler.",
            "dataState": "Fresh seed-generated roots; no board copy/reuse. Sequential scenarios, cumulative recorded mutationOrdinal; each write is committed before request. Conditional validators are per coding. Warmups retained separately.",
            "initialFacts": fixture.initial_facts, "finalFacts": fixture.facts()}


def source_digest(source):
    paths = sorted((source / "src").rglob("*.py"))
    paths += [source / "tests/python" / name for name in ("support.py", "mock_workspace.py", "protocol/fixtures/native_turn.py", "blackboard/routing/fixtures/router_tool_receipt.py")]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(source)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    choices = parser.add_mutually_exclusive_group(required=True)
    choices.add_argument("--source", type=Path)
    choices.add_argument("--baseline-source", type=Path, help="git-archive export of the fixed baseline SHA")
    parser.add_argument("--source-revision", help="Host's fixed final source SHA; recorded as declared")
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=613)
    parser.add_argument("--scale", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--generate-only", action="store_true")
    args = parser.parse_args(argv)
    source = (args.source or args.baseline_source).resolve()
    state, runtime, output = (path.resolve() for path in (args.state_root, args.runtime_root, args.output_root))
    roots = (state, runtime, output)
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a) for index, a in enumerate(roots) for b in roots[index + 1:]):
        parser.error("state/runtime/output roots must be separate, non-nested private roots")
    for root in roots:
        if root.exists() and any(root.iterdir()):
            parser.error("Every run requires empty roots; reuse is refused")
    output = _empty_root(output)
    recipe = Recipe.smoke() if args.scale == "smoke" else Recipe()
    failure = None
    with SyntheticBoard(state, runtime, source, seed=args.seed, recipe=recipe) as fixture:
        (output / "fixture-facts.json").write_text(json.dumps(fixture.initial_facts, indent=2) + "\n")
        if args.generate_only:
            report = {"initialFacts": fixture.initial_facts, "measurement": None}
        else:
            with (output / "samples.jsonl").open("x") as stream:
                def save(row):
                    stream.write(json.dumps(row) + "\n")
                    stream.flush()
                try:
                    report = measure(fixture, samples=args.samples, on_sample=save)
                except Exception as error:
                    failure = error
                    report = {"measurementStatus": "failed", "errorType": type(error).__name__,
                              "error": str(error), "initialFacts": fixture.initial_facts,
                              "samples": [json.loads(line) for line in (output / "samples.jsonl").read_text().splitlines()],
                              "aggregates": None}
        report.update(sourceRevisionDeclared=BASELINE_SHA if args.baseline_source else args.source_revision,
                      sourceContentSha256=source_digest(source), helperSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      pythonVersion=sys.version, paths={"source": "<selected-source>", "state": "<private-state>", "runtime": "<private-runtime>", "output": "<private-output>"})
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if failure is not None:
        raise failure
    print(json.dumps({"report": str(output / "report.json"), "retainedState": str(state), "retainedRuntime": str(runtime)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
