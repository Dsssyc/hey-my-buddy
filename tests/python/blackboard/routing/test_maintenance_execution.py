"""Maintenance uses a private file boundary and the ordinary execution queue."""
from __future__ import annotations

import json
import hashlib
from unittest.mock import patch

from admission import admit_board, release_before_spawn
from blackboard.routing.fixtures.router_tool_receipt import claim_tool_receipt
from blackboard.tasks.test_workflow import WorkflowTestCase
from support import BoardTestCase, FakeClock

from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.blackboard.store.db import canonical_json
from hey_my_buddy.buddy.roles.run_observers import MaintenanceCorrection
from hey_my_buddy.buddy.roles.router import DecisionAdapter
from hey_my_buddy.buddy.runtime.execution import Execution, classify_termination
from hey_my_buddy.buddy.harnesses.base import AdapterOutcome
from hey_my_buddy.paths import native_path
from hey_my_buddy.protocol import process_identity
from hey_my_buddy.buddy.harnesses.registry import run_seam
from hey_my_buddy.buddy.harnesses.run_contract import (
    NativeIdentity, RunEnd, RunResult, RunValue, StopEvidence, StopLayer,
)
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import maintenance as files
from hey_my_buddy.protocol.reasons import REGISTERED_REASON_PARAMETERS


BUDDY_ID = "dsh:fixture:maintenance:off"
NONCE = "m" * 32
T0 = "2026-01-01T00:00:00.000Z"
OBSERVATIONS = {"toolCalls": 0, "toolMarkerFrames": 0, "deniedInteractions": 0}


class EmptyMaintenanceTests(BoardTestCase):
    def test_empty_board_never_queues_a_model_call(self):
        board = self.board()
        with patch("hey_my_buddy.blackboard.routing.maintenance_data.build_packet", side_effect=AssertionError("must not scan")), \
                patch("hey_my_buddy.blackboard.routing.maintenance_data.pending", side_effect=AssertionError("must not scan")):
            self.assertEqual(board.console_call("maintenance_start", {}), {"status": "not-configured"})
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM maintenance_runs").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks WHERE owner='decision'").fetchone()[0], 0)

    def test_private_material_is_bound_to_run_and_digest(self):
        board = self.board()
        reference = files.publish(board.directory, "run-a", {"largeFact": "evidence"})
        self.assertEqual(set(reference), {"sha256"})
        self.assertEqual(files.read(board.directory, "run-a", reference), {"largeFact": "evidence"})
        with self.assertRaises((FileNotFoundError, BoardError)):
            files.read(board.directory, "run-b", reference)
        path = board.directory / "maintenance" / hashlib.sha256(b"run-a").hexdigest() / (reference["sha256"] + ".json")
        native_path(path).write_text("{}", encoding="utf-8")
        with self.assertRaises(BoardError) as caught:
            files.read(board.directory, "run-a", reference)
        self.assertEqual(caught.exception.code, "ARTIFACT_MISMATCH")


class MaintenanceExecutionTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock(T0)
        self.board_ = self.board(clock=self.clock, max_concurrent=1)
        run = self.submit(self.board_, request_id="maintenance-evidence", task="Repair a parser",
                          cwd=str(self.workdir("source")), kind="worktree")
        self.evidence_id = run["runId"]
        self.finish_turn(self.board_, self.claim(self.board_, run_id=self.evidence_id))
        self.accept_run(self.board_, self.board_.call("workflow_get", {"runId": self.evidence_id}))
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,"
                       "available,enabled,capabilities_json) VALUES(?,?,?,?,?,?,1,1,?)",
                       (BUDDY_ID, "Maintenance", "dsh", "fixture", "maintenance", "off", canonical_json([])))

    def configure(self, *, buddy_id=BUDDY_ID, interval=3600, preset="standard"):
        with self.board_.store.db.write() as db:
            router._write_settings(db, {"maintenanceBuddyId": buddy_id,
                                         "maintenanceIntervalSeconds": interval,
                                         "routingBudget": preset})

    def admission_input(self):
        from hey_my_buddy.blackboard.routing.maintenance_data import build_packet

        with self.board_.store.db.read() as db:
            settings = dict(db.execute("SELECT * FROM router_settings WHERE id=1").fetchone())
            buddy, _code, _facts = router.buddy_problem(db, settings["maintenance_buddy_id"], "fast", now=self.clock.value)
            return {"settings": settings, "buddy": buddy, "packet": build_packet(db), "trigger_type": "manual"}

    def start(self):
        return self.board_.console_call("maintenance_start", {})

    def claim_maintenance(self, task_id):
        claimed = admit_board(self.board_, {"nonce": NONCE, "taskId": task_id})
        self.assertIsNotNone(claimed["claim"], claimed)
        return claimed["claim"]

    def packet(self, claim):
        document = claim["decisionInput"]
        return files.read(self.board_.directory, document["maintenanceRunId"], document["input"])["packet"]

    def submit_answer(self, claim, round_index, answer, *, observations=OBSERVATIONS, nonce=NONCE):
        reference = files.publish(self.board_.directory, claim["decisionInput"]["maintenanceRunId"],
                                  {"answer": answer, "observations": observations})
        attempt = claim["attempt"]
        result = self.board_.call("execution_maintenance", {
            "attemptId": attempt["attemptId"], "generation": attempt["generation"],
            "nonce": nonce, "round": round_index, "answer": reference})
        return files.read(self.board_.directory, claim["decisionInput"]["maintenanceRunId"], result["feedback"])

    def valid_answer(self, packet):
        return {"newNodes": [{"id": "software", "name": "Software", "definition": "Software work",
                               "parentId": None}], "merges": [],
                "classifications": [{"runId": run_id, "domainNodeId": "software",
                                     "taskType": "debug"} for run_id in packet["classificationRunIds"]],
                "summaries": {kind["kindId"]: {"text": "Parser repair",
                                               "citations": kind["citationRunIds"][:1]}
                              for kind in packet["kinds"]}}

    def run_files(self, run_id):
        root = self.board_.directory / "maintenance" / hashlib.sha256(run_id.encode()).hexdigest()
        return list(root.iterdir()) if root.exists() else []

    def settle_success(self, claim):
        result = {**claim_tool_receipt(claim, state_dir=self.board_.directory),
                  "status": "ok", "operation": "maintain", "requested": claim["decisionInput"]["buddy"],
                  "usage": {"elapsedMs": 100, "toolCalls": 0},
                  "stopEvidence": {"shutdownConfirmed": True, "native": {"shutdownConfirmed": True}}}
        self.board_.call("execution_result", {
            "attemptId": claim["attempt"]["attemptId"], "generation": claim["attempt"]["generation"],
            "nonce": NONCE, "status": "ok", "shutdownConfirmed": True, "result": result})

    def add_settled_evidence(self, suffix):
        run = self.submit(self.board_, request_id=f"maintenance-{suffix}", task=f"Task {suffix}",
                          cwd=str(self.workdir(f"source-{suffix}")), kind="worktree")
        self.finish_turn(self.board_, self.claim(self.board_, run_id=run["runId"]))
        self.accept_run(self.board_, self.board_.call("workflow_get", {"runId": run["runId"]}))
        return run["runId"]

    def test_console_authority_configuration_and_no_tool_eligibility(self):
        with self.assertRaises(BoardError) as caught:
            self.board_.call("maintenance_start", {})
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        self.assertEqual(self.start()["status"], "not-configured")
        self.configure()
        with patch.dict("hey_my_buddy.protocol.capabilities.PERMISSION_LEVELS",
                        {"dsh": {"read": "enforced", "write": "enforced", "full": "enforced"}}):
            unavailable = self.start()
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertEqual(unavailable["reason"]["code"], "router-no-tool-unsupported")
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0 WHERE buddy_id=?", (BUDDY_ID,))
        self.assertEqual(self.start()["reason"]["code"], "router-disabled")
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM maintenance_runs").fetchone()[0], 0)

    def test_console_publishes_maintenance_settings_and_can_clear_the_buddy(self):
        response = self.board_.console_call("user_policy_publish", {
            "commandId": "maintenance-settings", "configuration": {
                "maintenanceBuddyId": BUDDY_ID, "maintenanceIntervalSeconds": 7200}})
        self.assertTrue(response["published"])
        settings = self.board_.call("console_snapshot", {})["configuration"]
        self.assertEqual(settings["maintenanceBuddyId"], BUDDY_ID)
        self.assertEqual(settings["maintenanceIntervalSeconds"], 7200)
        with self.assertRaises(BoardError) as caught:
            self.board_.console_call("user_policy_publish", {
                "commandId": "maintenance-unknown", "configuration": {"maintenanceBuddyId": "missing"}})
        self.assertEqual(caught.exception.code, "NOT_FOUND")
        self.board_.console_call("user_policy_publish", {
            "commandId": "maintenance-clear", "configuration": {"maintenanceBuddyId": None}})
        self.assertIsNone(self.board_.call("console_snapshot", {})["configuration"]["maintenanceBuddyId"])

    def test_unknown_maintenance_load_in_queue_and_in_flight_is_a_lower_bound(self):
        from hey_my_buddy.blackboard.routing.evidence import _loads
        from hey_my_buddy.cli.cli_views import task_brief
        self.configure()
        started = self.start()
        identity = ('dsh', 'fixture', 'maintenance')
        def load():
            with self.board_.store.db.read() as db:
                return _loads(self.board_.store, db, {identity}, now=self.clock.value)[identity]
        queued = load()
        self.assertEqual((queued['active'], queued['queued']), (0, 1))
        self.assertEqual(queued['wait'], {'seconds': 0, 'atLeast': True, 'overdue': 0})
        task = self.board_.call('task_get', {'runId': started['runId']})['task']
        self.assertNotIn('routerBudgetSeconds', task_brief(task))
        self.assertIsNone(self.board_.call('inquiry_observe', {'runId': started['runId']})['routerBudgetSeconds'])
        self.claim_maintenance(started['runId'])
        self.clock.advance(10_000)
        running = load()
        self.assertEqual((running['active'], running['queued']), (1, 0))
        self.assertEqual(running['wait'], {'seconds': 0, 'atLeast': True, 'overdue': 0})

    def test_domain_operations_require_console_and_preserve_merge_invariants(self):
        for operation, params in (("domain_rename", {"nodeId": "x", "name": "Name", "definition": "Meaning"}),
                                  ("domain_merge", {"sourceId": "x", "targetId": "y"})):
            with self.assertRaises(BoardError) as caught:
                self.board_.call(operation, params)
            self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        self.configure()
        claim = self.claim_maintenance(self.start()["runId"])
        self.submit_answer(claim, 0, self.valid_answer(self.packet(claim)))
        self.board_.console_call("domain_rename", {"nodeId": "software", "name": "Engineering", "definition": "Engineering work"})
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO domain_nodes VALUES('child','Child','Child work','software',NULL,?)",
                       (claim["decisionInput"]["maintenanceRunId"],))
            db.execute("INSERT INTO domain_nodes VALUES('target','Target','Target work',NULL,NULL,?)",
                       (claim["decisionInput"]["maintenanceRunId"],))
        with self.assertRaises(BoardError) as caught:
            self.board_.console_call("domain_merge", {"sourceId": "software", "targetId": "child"})
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.board_.console_call("domain_merge", {"sourceId": "software", "targetId": "target"})
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT parent_id FROM domain_nodes WHERE node_id='child'").fetchone()[0], "target")
            self.assertEqual(db.execute("SELECT merged_into_id FROM domain_nodes WHERE node_id='software'").fetchone()[0], "target")

    def test_missing_configured_buddy_does_not_create_an_internal_task(self):
        self.configure(buddy_id="dsh:fixture:missing:off")
        response = self.start()
        self.assertEqual(response["status"], "unavailable")
        self.assertEqual(response["reason"]["code"], "router-catalog-unavailable")
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM maintenance_runs").fetchone()[0], 0)

    def test_console_pending_read_counts_tasks_missing_classification_or_kind_evidence(self):
        with self.assertRaises(BoardError) as caught:
            self.board_.call("maintenance_pending", {})
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        before = self.board_.store.head()
        self.assertEqual(self.board_.console_call("maintenance_pending", {}),
                         {"pendingMicroTaskCount": 1})
        self.assertEqual(self.board_.store.head(), before)
        self.configure()
        claim = self.claim_maintenance(self.start()["runId"])
        packet = self.packet(claim)
        answer = self.valid_answer(packet)
        answer["summaries"] = {}
        self.assertTrue(self.submit_answer(claim, 0, answer)["errors"])
        self.assertEqual(self.board_.console_call("maintenance_pending", {}),
                         {"pendingMicroTaskCount": 1})
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM micro_task_classifications").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM kind_summary_evidence").fetchone()[0], 0)
        self.assertEqual(self.submit_answer(claim, 1, self.valid_answer(packet))["errors"], [])
        self.assertEqual(self.board_.console_call("maintenance_pending", {}),
                         {"pendingMicroTaskCount": 0})

    def test_pending_count_requires_host_accepted_or_concluded_state(self):
        run = self.submit(self.board_, request_id="pending-state", task="Review another parser",
                          cwd=str(self.workdir("pending-state")), kind="worktree")
        self.assertEqual(self.board_.console_call("maintenance_pending", {}),
                         {"pendingMicroTaskCount": 1})
        self.finish_turn(self.board_, self.claim(self.board_, run_id=run["runId"]))
        self.assertEqual(self.board_.call("workflow_get", {"runId": run["runId"]})["state"], "delivered")
        self.assertEqual(self.board_.console_call("maintenance_pending", {}),
                         {"pendingMicroTaskCount": 1})
        self.accept_run(self.board_, self.board_.call("workflow_get", {"runId": run["runId"]}))
        self.assertEqual(self.board_.console_call("maintenance_pending", {}),
                         {"pendingMicroTaskCount": 2})
        concluded = self.submit(self.board_, request_id="concluded-state", task="Investigate parser failure",
                                cwd=str(self.workdir("concluded-state")), kind="worktree")
        self.finish_turn(self.board_, self.claim(self.board_, run_id=concluded["runId"]))
        self.board_.call("workflow_conclude", {"runId": concluded["runId"],
                                                "note": "Investigation ended without acceptance",
                                                **self.control(concluded)})
        self.assertEqual(self.board_.console_call("maintenance_pending", {}),
                         {"pendingMicroTaskCount": 3})

    def test_start_claim_unlimited_frozen_packet_and_no_router_table_pollution(self):
        self.configure(preset="deep")
        started = self.start()
        self.assertEqual(started["status"], "running")
        self.assertEqual(self.start()["maintenanceRunId"], started["maintenanceRunId"])
        claim = self.claim_maintenance(started["runId"])
        document = claim["decisionInput"]
        self.assertEqual(document["routingMode"], "fast")
        self.assertNotIn("budget", document)
        self.assertNotIn("routerBudgetSeconds", claim["task"]["spec"])
        self.assertNotIn("routerBudgetSeconds", claim["task"]["spec"]["decision"])
        self.assertIsNone(self.board_.call("task_get", {"runId": started["runId"]})["task"]["routerBudgetSeconds"])
        self.assertEqual(self.packet(claim)["classificationRunIds"], [self.evidence_id])
        self.assertEqual(self.packet(claim)["microTasks"][0]["classification"], None)
        self.assertEqual(self.packet(claim)["kinds"][0]["unreadRunIds"], [self.evidence_id])
        self.assertEqual(claim["task"]["spec"]["decision"]["kind"], "maintain")
        with self.board_.store.db.read() as db:
            run = db.execute("SELECT task_id,state FROM maintenance_runs").fetchone()
            self.assertEqual((run["task_id"], run["state"]), (started["runId"], "running"))
            for table in ("router_requests", "router_dispatches", "router_claims", "router_outcomes",
                          "evaluation_decisions"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)

    def test_answer_fences_actor_and_writes_valid_part_at_each_round(self):
        self.configure()
        started = self.start()
        claim = self.claim_maintenance(started["runId"])
        packet = self.packet(claim)
        answer = self.valid_answer(packet)
        answer["classifications"][0]["domainNodeId"] = "missing"
        with self.assertRaises(BoardError) as caught:
            self.submit_answer(claim, 0, answer, nonce="wrong")
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        with self.assertRaises(BoardError) as caught:
            self.submit_answer(claim, 0, answer,
                               observations={"toolCalls": 1, "toolMarkerFrames": 0,
                                             "deniedInteractions": 0})
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        reference = files.publish(self.board_.directory, claim["decisionInput"]["maintenanceRunId"],
                                  {"answer": answer, "observations": OBSERVATIONS})
        with self.assertRaises(BoardError) as caught:
            self.board_.call("execution_maintenance", {
                "attemptId": claim["attempt"]["attemptId"],
                "generation": claim["attempt"]["generation"] + 1,
                "nonce": NONCE, "round": 0, "answer": reference})
        self.assertEqual(caught.exception.code, "STALE_GENERATION")
        first = self.submit_answer(claim, 0, answer)
        self.assertTrue(first["errors"])
        self.assertEqual(first["written"]["nodes"], 1)
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM domain_nodes").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM micro_task_classifications").fetchone()[0], 0)
        answer["classifications"][0]["domainNodeId"] = "software"
        corrected = self.submit_answer(claim, 1, answer)
        self.assertEqual(corrected["errors"], [])
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT task_type FROM micro_task_classifications").fetchone()[0], "debug")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM kind_summaries").fetchone()[0], len(packet["kinds"]))
            self.assertEqual(tuple(db.execute("SELECT run_id,cited FROM kind_summary_evidence").fetchone()),
                             (self.evidence_id, 1))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM router_outcomes").fetchone()[0], 0)

    def test_answer_rollback_removes_unbound_files_and_preserves_active_input(self):
        self.configure()
        claim = self.claim_maintenance(self.start()["runId"])
        run_id = claim["decisionInput"]["maintenanceRunId"]
        before = self.run_files(run_id)
        with patch.object(self.board_.store, "_append_event", side_effect=RuntimeError("answer event failed")):
            with self.assertRaises(BoardError) as caught:
                self.submit_answer(claim, 0, self.valid_answer(self.packet(claim)))
        self.assertEqual(caught.exception.code, "INTERNAL_ERROR")
        self.assertEqual(self.run_files(run_id), before)
        self.assertEqual(self.packet(claim)["classificationRunIds"], [self.evidence_id])
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM micro_task_classifications").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM kind_summaries").fetchone()[0], 0)

    def test_restart_file_reconciliation_preserves_active_and_removes_orphans(self):
        self.configure()
        started = self.start()
        active = self.run_files(started["maintenanceRunId"])
        files.publish(self.board_.directory, "uncommitted-run", {"packet": "orphan"})
        self.assertTrue(self.run_files("uncommitted-run"))
        self.board_.store.maintenance.recover_files()
        self.assertEqual(self.run_files("uncommitted-run"), [])
        self.assertEqual(self.run_files(started["maintenanceRunId"]), active)

    def test_observer_corrects_twice_in_one_run_and_stops_before_tool_use(self):
        feedbacks = [{"errors": ["first rejection"]}, {"errors": ["second rejection"]}, {"errors": []}]
        seen = []

        def publish(raw, round_index, observations):
            seen.append((raw, round_index, observations))
            return feedbacks[round_index]

        observer = MaintenanceCorrection({}, "frozen prompt", publish)
        for expected in ("correct", "correct", "continue"):
            action = observer.observer({"settled": True, "rawAnswer": "{}", **OBSERVATIONS})
            self.assertEqual(action.action, expected)
        self.assertEqual([round_index for _, round_index, _ in seen], [0, 1, 2])
        self.assertEqual(observer.correction_count, 2)
        self.assertEqual(observer.observer({"toolCalls": 1, "settled": True, "rawAnswer": "{}"}).action,
                         "stop")
        self.assertEqual(len(seen), 3)

    def test_failed_completion_waits_for_interval_and_console_can_retry(self):
        self.configure(interval=120)
        started = self.start()
        claim = self.claim_maintenance(started["runId"])
        self.assertTrue(self.run_files(started["maintenanceRunId"]))
        self.board_.call("execution_result", {
            "attemptId": claim["attempt"]["attemptId"], "generation": claim["attempt"]["generation"],
            "nonce": NONCE, "status": "failed", "shutdownConfirmed": True,
            "result": {"status": "error", "code": "router-no-answer"}})
        with self.board_.store.db.read() as db:
            run = db.execute("SELECT state,reason_code,finished_at FROM maintenance_runs").fetchone()
            self.assertEqual((run["state"], run["reason_code"]), ("failed", "router-no-answer"))
            self.assertIsNotNone(run["finished_at"])
        self.assertEqual(self.run_files(started["maintenanceRunId"]), [])
        self.assertEqual(self.board_.store.maintenance.collect(), 120)
        self.assertEqual(self.start()["status"], "running")
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM maintenance_runs").fetchone()[0], 2)

    def test_console_reads_native_activity_and_stops_the_existing_execution(self):
        from console.test_console import Browser

        self.configure(interval=120)
        started = self.start()
        claim = self.claim_maintenance(started["runId"])
        browser = Browser(self.board_.call("console", {"action": "open"})["url"])
        snapshot = browser.bootstrap()
        csrf = snapshot["csrfToken"]
        actor = {"role": "console", "id": snapshot["consoleSession"]["id"]}
        activity = {"phase": "streaming-model", "lastNativeActivityAt": T0, "eventSeq": 5}
        self.board_.call("execution_progress", {
            "attemptId": claim["attempt"]["attemptId"], "generation": claim["attempt"]["generation"],
            "nonce": NONCE, "data": {"activity": activity}})
        status, _, body = browser.get("/api/tasks/" + started["runId"])
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["activity"]["lastNativeActivityAt"], T0)
        denied, _, _ = browser.command("task_cancel", {"runId": started["runId"]})
        self.assertEqual(denied, 403)
        denied, _, body = browser.command("task_cancel", {"runId": self.evidence_id}, csrf=csrf)
        self.assertNotEqual(denied, 200)
        self.assertEqual(json.loads(body)["error"]["code"], "FORBIDDEN")
        status, _, body = browser.command("task_cancel", {"runId": started["runId"]}, csrf=csrf)
        self.assertEqual(status, 200, body)
        task = json.loads(body)["result"]["task"]
        self.assertEqual(task["state"], "cancelling")
        self.assertTrue(task["cancelRequested"])
        self.assertEqual(task["stopInitiator"], "console")
        events = self.board_.call("events_read", {"runId": started["runId"], "after": 0})["events"]
        cancel = next(event["payload"] for event in events if event["kind"] == "task.cancel_requested")
        self.assertEqual(cancel["actor"], actor)
        self.assertEqual(browser.bootstrap()["maintenance"]["runs"][0]["state"], "running")
        outcome = AdapterOutcome(status="cancelled", result={"status": "error", "code": "user-cancel"},
                                 shutdown_confirmed=True)
        termination = classify_termination(outcome, timed_out=False, cancel_requested=task["cancelRequested"],
                                           stop_initiator=task["stopInitiator"])
        self.board_.call("execution_result", {
            "attemptId": claim["attempt"]["attemptId"], "generation": claim["attempt"]["generation"],
            "nonce": NONCE, "status": "cancelled", "shutdownConfirmed": True,
            "terminationReason": termination, "result": outcome.result})
        finished = self.board_.call("task_get", {"runId": started["runId"]})["task"]
        self.assertEqual(finished["terminationReason"], "console-stop")
        self.assertEqual(finished["selectedAttempt"]["stopInitiator"], "console")
        run = browser.bootstrap()["maintenance"]["runs"][0]
        self.assertEqual((run["state"], run["reason"]["code"], run["finishedAt"]), ("failed", "user-cancel", T0))
        self.assertEqual(self.board_.store.maintenance.collect(), 120)

    def test_console_cannot_supply_requested_by_even_for_maintenance(self):
        from console.test_console import Browser

        self.configure()
        run_id = self.start()["runId"]
        browser = Browser(self.board_.call("console", {"action": "open"})["url"])
        csrf = browser.bootstrap()["csrfToken"]
        before = self.board_.store.head()
        for requested_by in ("forged-host", None):
            status, _, body = browser.command("task_cancel", {"runId": run_id, "requestedBy": requested_by}, csrf=csrf)
            self.assertEqual(status, 400, body)
            self.assertEqual(json.loads(body)["error"]["code"], "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as refused:
            self.board_.console_call("task_cancel", {"runId": run_id, "requestedBy": "forged-host"})
        self.assertEqual(refused.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(self.board_.call("task_get", {"runId": run_id})["task"]["state"], "queued")
        self.assertEqual(self.board_.store.head(), before)

    def test_failed_native_execution_keeps_reported_usage_and_leaves_missing_usage_unknown(self):
        from console.test_console import Browser

        self.configure()
        browser = Browser(self.board_.call("console", {"action": "open"})["url"])
        reported = {"inputTokens": 120, "cachedInputTokens": 5, "outputTokens": None,
                    "source": "synthetic-native", "scope": "attempt"}
        for usage in (reported, None):
            with self.subTest(usage=usage):
                claim = self.claim_maintenance(self.start()["runId"])

                def fake_run(request, *, observer, services, cancelled):
                    return RunResult(identity=request.identity, harness=request.harness,
                        end=RunEnd(status="error", reason_code="deadline", native_exit_code=1),
                        model_started=True, usage=usage,
                        stop_evidence=StopEvidence(native=StopLayer(group_state="gone")))

                module = run_seam("dsh")
                logs = []
                with patch.object(process_identity, "_active_native_journal", None):
                    execution = Execution(claim, self.board_.directory, NONCE, client=self.board_.client(),
                                          session_record_path=claim["sessionRecordPath"], log=logs.append)
                    native_path(execution.directory).mkdir(parents=True, mode=0o700, exist_ok=True)
                    native_path(execution.evidence_directory).mkdir(parents=True, mode=0o700, exist_ok=True)
                    with patch.object(module, "run", side_effect=fake_run), \
                            patch.object(DecisionAdapter, "available", return_value=(True, None)), \
                            patch.object(module, "check_preparation", return_value=None), \
                            patch.object(module, "native_evidence", return_value={}):
                        execution.run()
                run = browser.bootstrap()["maintenance"]["runs"][0]
                self.assertEqual(run["state"], "failed", logs)
                self.assertEqual(run["reason"]["code"], "router-budget-exhausted")
                actual = run["usage"]["attempts"][0]["tokenUsage"]
                if usage is None:
                    self.assertIsNone(actual)
                else:
                    self.assertEqual({key: actual[key] for key in reported}, reported)

    def test_stop_unconfirmed_keeps_its_reason_over_a_rejected_answer(self):
        self.configure()
        started = self.start()
        claim = self.claim_maintenance(started["runId"])
        feedback = self.submit_answer(claim, 0, {})
        self.assertTrue(feedback["errors"])
        with self.board_.store.db.write() as db:
            task = db.execute("SELECT * FROM tasks WHERE task_id=?", (started["runId"],)).fetchone()
            attempt = db.execute("SELECT * FROM attempts WHERE attempt_id=?",
                                 (claim["attempt"]["attemptId"],)).fetchone()
            self.board_.store.maintenance.complete(
                db, task=task, attempt=attempt, status="ok", result={"status": "ok"},
                shutdown_confirmed=False, error=None, now=self.clock.value)
        with self.board_.store.db.read() as db:
            run = db.execute("SELECT * FROM maintenance_runs").fetchone()
        self.assertEqual(run["reason_code"], "router-stop-unconfirmed")
        self.assertEqual(json.loads(run["reason_params_json"]), {})
        self.assertEqual(self.run_files(started["maintenanceRunId"]), [])

    def test_interruption_fence_keeps_its_reason_over_a_rejected_answer(self):
        self.configure()
        claim = self.claim_maintenance(self.start()["runId"])
        feedback = self.submit_answer(claim, 0, {})
        self.assertTrue(feedback["errors"])
        with self.board_.store.db.write() as db:
            attempt = self.board_.store._attempt_row(db, claim["attempt"]["attemptId"])
            self.board_.store.decisions.fence_attempt(db, attempt=attempt,
                reason={"code": "interrupted-before-delivery", "params": {}}, now=self.clock.value)
        with self.board_.store.db.read() as db:
            run = db.execute("SELECT * FROM maintenance_runs").fetchone()
        self.assertEqual(run["reason_code"], "interrupted-before-delivery")
        self.assertEqual(json.loads(run["reason_params_json"]), {})
        self.assertEqual(self.run_files(run["maintenance_run_id"]), [])

    def test_final_invalid_answer_has_a_distinct_fixed_reason_shape(self):
        self.configure()
        claim = self.claim_maintenance(self.start()["runId"])
        for round_index in range(3):
            feedback = self.submit_answer(claim, round_index, {})
            self.assertTrue(feedback["errors"])
        self.settle_success(claim)
        with self.board_.store.db.read() as db:
            run = db.execute("SELECT state,reason_code,reason_params_json FROM maintenance_runs").fetchone()
        self.assertEqual((run["state"], run["reason_code"]), ("failed", "maintenance-answer-invalid"))
        self.assertEqual(json.loads(run["reason_params_json"]), {"errors": feedback["errors"]})
        self.assertEqual(REGISTERED_REASON_PARAMETERS["maintenance-answer-invalid"], ("errors",))
        self.assertEqual(REGISTERED_REASON_PARAMETERS["answer-shape"], ())

    def test_complete_answer_drains_evidence_without_a_second_model_call(self):
        self.configure()
        started = self.start()
        claim = self.claim_maintenance(started["runId"])
        feedback = self.submit_answer(claim, 0, self.valid_answer(self.packet(claim)))
        self.assertEqual(feedback["errors"], [])
        self.assertEqual(self.start()["maintenanceRunId"], started["maintenanceRunId"])
        self.settle_success(claim)
        self.assertEqual(self.run_files(started["maintenanceRunId"]), [])
        with self.board_.store.db.read() as db:
            run = db.execute("SELECT state,reason_code FROM maintenance_runs").fetchone()
            self.assertEqual((run["state"], run["reason_code"]), ("completed", None))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM router_outcomes").fetchone()[0], 0)
        self.assertEqual(self.board_.store.maintenance.collect(), 3600)
        self.assertEqual(self.start()["status"], "no-new-evidence")

    def test_rolled_back_creation_removes_private_input_and_run(self):
        self.configure()
        prepared = self.admission_input()
        with self.assertRaisesRegex(RuntimeError, "abort maintenance creation"):
            with self.board_.store.db.write() as db:
                response, _ = self.board_.store.maintenance._admit(db, **prepared)
                self.assertEqual(response["status"], "running")
                self.assertTrue(self.run_files(response["maintenanceRunId"]))
                run_id = response["maintenanceRunId"]
                raise RuntimeError("abort maintenance creation")
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM maintenance_runs").fetchone()[0], 0)
        self.assertEqual(self.run_files(run_id), [])

    def test_rolled_back_finish_keeps_active_input(self):
        self.configure()
        started = self.start()
        before = self.run_files(started["maintenanceRunId"])
        self.assertTrue(before)
        with self.assertRaisesRegex(RuntimeError, "abort maintenance finish"):
            with self.board_.store.db.write() as db:
                self.board_.store.maintenance.finish(
                    db, task_id=started["runId"], now=self.board_.store.now(), code="test-abort")
                raise RuntimeError("abort maintenance finish")
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT state FROM maintenance_runs WHERE task_id=?",
                                        (started["runId"],)).fetchone()[0], "running")
        self.assertEqual(self.run_files(started["maintenanceRunId"]), before)
        claim = self.claim_maintenance(started["runId"])
        self.assertEqual(self.packet(claim)["classificationRunIds"], [self.evidence_id])

    def test_collect_skips_pending_scan_without_buddy_and_before_deadline(self):
        from hey_my_buddy.blackboard.routing import maintenance_data

        with patch.object(maintenance_data, "pending", side_effect=AssertionError("unexpected scan")):
            self.assertIsNone(self.board_.store.maintenance.collect())
        self.configure(interval=120)
        started = self.start()
        claim = self.claim_maintenance(started["runId"])
        self.board_.call("execution_result", {
            "attemptId": claim["attempt"]["attemptId"], "generation": claim["attempt"]["generation"],
            "nonce": NONCE, "status": "failed", "shutdownConfirmed": True,
            "result": {"status": "error", "code": "router-no-answer"}})
        with patch.object(maintenance_data, "pending", side_effect=AssertionError("unexpected scan")):
            self.assertEqual(self.board_.store.maintenance.collect(), 120)
            with self.board_.store.db.write() as db:
                self.board_.store._append_event(db, "fixture.unrelated", payload={})
                head = self.board_.store._head_of(db)
            self.board_.store._notify(head)
            self.assertEqual(self.board_.store.maintenance.collect(), 120)

    def test_queued_claim_cancels_after_buddy_clear_or_reassignment(self):
        for replacement in (None, "dsh:fixture:other:off"):
            with self.subTest(replacement=replacement):
                self.configure()
                started = self.start()
                self.configure(buddy_id=replacement)
                outcome = admit_board(self.board_, {"nonce": NONCE, "taskId": started["runId"]})
                self.assertIsNone(outcome["claim"])
                with self.board_.store.db.read() as db:
                    run = db.execute("SELECT state,reason_code FROM maintenance_runs WHERE task_id=?",
                                     (started["runId"],)).fetchone()
                    self.assertEqual(run["state"], "failed")
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts WHERE task_id=?",
                                                (started["runId"],)).fetchone()[0], 0)

    def test_running_maintenance_can_finish_after_buddy_clear(self):
        self.configure()
        started = self.start()
        claim = self.claim_maintenance(started["runId"])
        self.configure(buddy_id=None)
        self.assertEqual(self.submit_answer(claim, 0, self.valid_answer(self.packet(claim)))["errors"], [])
        self.settle_success(claim)
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT state FROM maintenance_runs WHERE task_id=?",
                                        (started["runId"],)).fetchone()[0], "completed")

    def test_success_with_unclassified_evidence_starts_next_batch_immediately(self):
        second_id = self.add_settled_evidence("second")
        self.configure(interval=3600)
        with self.board_.store.db.read() as db:
            from hey_my_buddy.blackboard.routing.maintenance_data import build_packet
            full_packet = build_packet(db)
        self.assertEqual(len(full_packet["classificationRunIds"]), 2)
        first_packet = {**full_packet,
                        "classificationRunIds": [self.evidence_id],
                        "microTasks": [item for item in full_packet["microTasks"]
                                       if item["runId"] == self.evidence_id],
                        "kinds": [{**kind, "unreadRunIds": [self.evidence_id]}
                                  for kind in full_packet["kinds"]]}
        with patch("hey_my_buddy.blackboard.routing.maintenance_data.build_packet",
                   return_value=first_packet):
            started = self.start()
        claim = self.claim_maintenance(started["runId"])
        self.assertEqual(self.submit_answer(claim, 0, self.valid_answer(self.packet(claim)))["errors"], [])
        self.settle_success(claim)
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM micro_task_classifications").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT run_id FROM workflow_runs WHERE run_id=?",
                                        (second_id,)).fetchone()[0], second_id)
        self.assertIsNone(self.board_.store.maintenance.collect())
        with self.board_.store.db.read() as db:
            runs = list(db.execute("SELECT trigger_type,state FROM maintenance_runs ORDER BY rowid"))
        self.assertEqual([(row["trigger_type"], row["state"]) for row in runs],
                         [("manual", "completed"), ("interval", "running")])

    def test_classification_survives_rejected_summary_and_next_batch_reads_only_missing_kind(self):
        self.configure(interval=120)
        started = self.start()
        claim = self.claim_maintenance(started["runId"])
        answer = self.valid_answer(self.packet(claim))
        answer["summaries"] = {}
        self.assertTrue(self.submit_answer(claim, 0, answer)["errors"])
        self.settle_success(claim)
        self.assertEqual(self.board_.console_call("maintenance_pending", {}),
                         {"pendingMicroTaskCount": 1})
        with self.board_.store.db.write() as db:
            self.assertEqual(db.execute("SELECT task_type FROM micro_task_classifications WHERE run_id=?",
                                        (self.evidence_id,)).fetchone()[0], "debug")
        self.assertEqual(self.board_.store.maintenance.collect(), 120)
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM maintenance_runs").fetchone()[0], 1)
        manual = self.start()
        self.assertEqual(manual["status"], "running")
        packet = self.packet(self.claim_maintenance(manual["runId"]))
        self.assertEqual(packet["classificationRunIds"], [])
        self.assertEqual([item["runId"] for item in packet["microTasks"]], [self.evidence_id])
        self.assertEqual(packet["microTasks"][0]["classification"]["taskType"], "debug")
        self.assertEqual(packet["kinds"][0]["unreadRunIds"], [self.evidence_id])

    def test_summary_survives_rejected_classification_and_next_batch_does_not_reread_kind(self):
        self.configure()
        started = self.start()
        claim = self.claim_maintenance(started["runId"])
        answer = self.valid_answer(self.packet(claim))
        answer["classifications"][0]["domainNodeId"] = "missing"
        self.assertTrue(self.submit_answer(claim, 0, answer)["errors"])
        self.settle_success(claim)
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT text FROM kind_summaries").fetchone()[0], "Parser repair")
            self.assertEqual(tuple(db.execute("SELECT run_id,cited FROM kind_summary_evidence").fetchone()),
                             (self.evidence_id, 1))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM micro_task_classifications").fetchone()[0], 0)
        self.assertEqual(self.board_.console_call("maintenance_pending", {}),
                         {"pendingMicroTaskCount": 1})
        next_run = self.start()
        next_claim = self.claim_maintenance(next_run["runId"])
        packet = self.packet(next_claim)
        self.assertEqual(packet["classificationRunIds"], [self.evidence_id])
        self.assertIsNone(packet["microTasks"][0]["classification"])
        self.assertEqual(packet["kinds"], [])
        correction = self.valid_answer(packet)
        correction["newNodes"] = []
        self.assertEqual(self.submit_answer(next_claim, 0, correction)["errors"], [])
        self.assertEqual(self.board_.console_call("maintenance_pending", {}),
                         {"pendingMicroTaskCount": 0})
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT text FROM kind_summaries").fetchone()[0], "Parser repair")

    def test_one_run_with_two_kinds_remains_pending_until_both_kinds_are_read(self):
        self.configure()
        original = self.claim_maintenance(self.start()["runId"])
        self.assertEqual(self.submit_answer(original, 0, self.valid_answer(self.packet(original)))["errors"], [])
        self.settle_success(original)
        from blackboard.tasks.test_workflow import BUDDY
        run = self.submit(self.board_, request_id="two-kind-evidence", task="Repair another parser",
                          cwd=str(self.workdir("two-kind-source")), kind="worktree")
        self.finish_turn(self.board_, self.claim(self.board_, run_id=run["runId"]))
        delivered = self.board_.call("workflow_get", {"runId": run["runId"]})
        other = {**BUDDY, "model": "maintenance-other"}
        self.continue_run(self.board_, delivered, buddy=other, command_id="two-kind-continue")
        self.finish_turn(self.board_, self.claim(self.board_, run_id=run["runId"], nonce="n" * 16),
                         nonce="n" * 16, session_id="sess-2")
        self.accept_run(self.board_, self.board_.call("workflow_get", {"runId": run["runId"]}))
        self.evidence_id = run["runId"]
        started = self.start()
        claim = self.claim_maintenance(started["runId"])
        packet = self.packet(claim)
        self.assertEqual(len(packet["kinds"]), 2)
        self.assertTrue(all(kind["unreadRunIds"] == [self.evidence_id] for kind in packet["kinds"]))
        self.assertTrue(all(turn["failureCategory"] == "ability" for turn in packet["microTasks"][0]["result"]["turns"]))
        answer = self.valid_answer(packet)
        answer["summaries"].pop(next(kind["kindId"] for kind in packet["kinds"]
                                     if kind["model"] == "maintenance-other"))
        self.assertTrue(self.submit_answer(claim, 0, answer)["errors"])
        self.settle_success(claim)
        self.assertEqual(self.board_.console_call("maintenance_pending", {}),
                         {"pendingMicroTaskCount": 1})
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM micro_task_classifications WHERE run_id=?",
                                        (self.evidence_id,)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM kind_summary_evidence WHERE run_id=?",
                                        (self.evidence_id,)).fetchone()[0], 1)
        next_run = self.start()
        next_claim = self.claim_maintenance(next_run["runId"])
        packet = self.packet(next_claim)
        self.assertEqual(packet["classificationRunIds"], [])
        self.assertEqual(len(packet["kinds"]), 1)
        self.assertEqual(packet["kinds"][0]["unreadRunIds"], [self.evidence_id])
        self.assertEqual(packet["kinds"][0]["model"], "maintenance-other")
        correction = self.valid_answer(packet)
        correction["newNodes"] = []
        self.assertEqual(self.submit_answer(next_claim, 0, correction)["errors"], [])
        self.assertEqual(self.board_.console_call("maintenance_pending", {}),
                         {"pendingMicroTaskCount": 0})

    def test_new_evidence_refreshes_summary_without_forgetting_earlier_read_run(self):
        self.configure()
        first = self.start()
        first_claim = self.claim_maintenance(first["runId"])
        self.assertEqual(self.submit_answer(first_claim, 0, self.valid_answer(self.packet(first_claim)))["errors"], [])
        self.settle_success(first_claim)
        second_id = self.add_settled_evidence("newer")
        next_run = self.start()
        next_claim = self.claim_maintenance(next_run["runId"])
        packet = self.packet(next_claim)
        self.assertEqual(packet["classificationRunIds"], [second_id])
        self.assertEqual(packet["kinds"][0]["unreadRunIds"], [second_id])
        self.assertEqual(packet["kinds"][0]["summary"]["citations"], [self.evidence_id])
        answer = self.valid_answer(packet)
        answer["newNodes"] = []
        answer["summaries"][packet["kinds"][0]["kindId"]]["citations"] = [second_id]
        self.assertEqual(self.submit_answer(next_claim, 0, answer)["errors"], [])
        with self.board_.store.db.read() as db:
            evidence = {row["run_id"]: row["cited"] for row in db.execute(
                "SELECT run_id,cited FROM kind_summary_evidence")}
        self.assertEqual(evidence, {self.evidence_id: 0, second_id: 1})
        self.assertEqual(self.board_.console_call("maintenance_pending", {}),
                         {"pendingMicroTaskCount": 0})

    def test_large_packet_stays_in_private_file_and_claim_is_bounded(self):
        self.configure()
        # Inflate an already settled fixture row to exercise the transport limit;
        # the product's Host text limit is a separate admission boundary.
        with self.board_.store.db.write() as db:
            spec = json.loads(db.execute("SELECT spec_json FROM tasks WHERE task_id=?",
                                         (self.evidence_id,)).fetchone()[0])
            spec["task"] = "large evidence " + "x" * (8 * 1024 * 1024)
            db.execute("UPDATE tasks SET spec_json=? WHERE task_id=?",
                       (canonical_json(spec), self.evidence_id))
        started = self.start()
        claim = self.claim_maintenance(started["runId"])
        self.assertLess(len(canonical_json(claim).encode()), 100_000)
        self.assertGreater(len(canonical_json(self.packet(claim)).encode()), 8 * 1024 * 1024)

    def test_ordinary_capacity_blocks_maintenance_until_released(self):
        occupied = self.board_.submit_execution(
            requestId="capacity-holder", task="A model-free occupied slot", adapter="command",
            argv=["true"], cwd=str(self.workdir("capacity")))["task"]["runId"]
        blocker = admit_board(self.board_, {"nonce": "c" * 32, "taskId": occupied})["claim"]
        self.assertIsNotNone(blocker)
        self.configure()
        started = self.start()
        held = admit_board(self.board_, {"nonce": NONCE, "taskId": started["runId"]})
        self.assertIsNone(held["claim"])
        self.assertEqual(held["reason"], "capacity")
        release_before_spawn(self.board_.client(), blocker["attempt"]["attemptId"])
        self.assertIsNotNone(self.claim_maintenance(started["runId"]))

    def test_worker_and_maintenance_share_the_same_model_service_limit(self):
        from blackboard.tasks.test_workflow import BUDDY

        self.board_.store.max_concurrent = 8
        with self.board_.store.db.write() as db:
            buddy_id = ":".join(BUDDY[key] for key in ("harness", "provider", "model", "effort"))
            db.execute("INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,enabled,capabilities_json)"
                       " VALUES(?,?,?,?,?,?,1,1,'[]')", (buddy_id, "Shared model service", *(
                           BUDDY[key] for key in ("harness", "provider", "model", "effort"))))
            db.execute("INSERT INTO model_service_concurrency VALUES(?,?,?,1)"
                       " ON CONFLICT(harness,provider,model) DO UPDATE SET concurrency_limit=1",
                       tuple(BUDDY[key] for key in ("harness", "provider", "model")))
        work = self.submit(self.board_, request_id="same-service-worker", task="Occupy a model slot",
                           cwd=str(self.workdir("same-service-worker")), kind="worktree")
        occupied = self.claim(self.board_, run_id=work["runId"])
        self.configure(buddy_id=buddy_id)
        maintenance = self.start()
        blocked = admit_board(self.board_, {"nonce": NONCE, "taskId": maintenance["runId"]})
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "model-capacity")
        release_before_spawn(self.board_.client(), occupied["claim"]["attempt"]["attemptId"])
        self.assertIsNotNone(self.claim_maintenance(maintenance["runId"]))

    def test_queued_cancellation_closes_maintenance_and_releases_single_flight(self):
        self.configure()
        started = self.start()
        self.board_.call("task_cancel", {"runId": started["runId"]})
        with self.board_.store.db.read() as db:
            first = db.execute("SELECT state,reason_code FROM maintenance_runs").fetchone()
            self.assertEqual(first["state"], "failed")
            self.assertIsNotNone(first["reason_code"])
        retry = self.start()
        self.assertEqual(retry["status"], "running")
        self.assertNotEqual(retry["maintenanceRunId"], started["maintenanceRunId"])

    def test_one_execution_drives_three_settled_answers_through_real_observer_and_service(self):
        self.configure()
        started = self.start()
        with self.board_.store.db.read() as db:
            packet = files.read(self.board_.directory, started["maintenanceRunId"],
                json.loads(db.execute("SELECT spec_json FROM tasks WHERE task_id=?",
                                      (started["runId"],)).fetchone()[0])["decision"]["document"]["input"])["packet"]
        good = self.valid_answer(packet)
        first = {**good, "classifications": [{**good["classifications"][0], "domainNodeId": "missing"}]}
        second = {**good, "classifications": [{**good["classifications"][0],
                                                "domainNodeId": "still-missing"}]}
        actions = []
        transcripts = []

        def fake_run(request, *, observer, services, cancelled):
            self.assertEqual(request.permission, "none")
            self.assertIsNone(request.budget.timeout_seconds)
            self.assertFalse(cancelled())
            session = native_path(request.private_state.native_root) / "dsh-home" / "sessions" / "session.jsonl"
            session.parent.mkdir(parents=True, exist_ok=True)
            session.write_text(request.input_text + "\n" + canonical_json(good), encoding="utf-8")
            transcripts.append(session)
            for answer in (first, second, good):
                response = observer({"settled": True, "rawAnswer": canonical_json(answer), **OBSERVATIONS})
                actions.append(response.action)
            self.assertEqual(actions, ["correct", "correct", "continue"])
            attempt = request.identity
            receipt = claim_tool_receipt({"task": {"taskId": attempt.task_id},
                                          "attempt": {"attemptId": attempt.attempt_id,
                                                      "generation": attempt.generation},
                                          "decisionInput": {"buddy": {"harness": "dsh"}}},
                                         state_dir=self.board_.directory,
                                         native_identity={"sessionId": "fake-maintenance-native"})
            return RunResult(identity=request.identity, harness=request.harness,
                             end=RunEnd(status="ok", native_exit_code=0), model_started=True,
                             native_identity=NativeIdentity(session_id="fake-maintenance-native"),
                             native_event_count=3,
                             value=RunValue(schema_status="valid", raw=canonical_json(good),
                                            correction_count=2),
                             tool_evidence=receipt["toolEvidence"],
                             stop_evidence=StopEvidence(native=StopLayer(group_state="gone")))

        module = run_seam("dsh")
        claim = self.claim_maintenance(started["runId"])
        logs = []
        with patch.object(process_identity, "_active_native_journal", None):
            execution = Execution(claim, self.board_.directory, NONCE, client=self.board_.client(),
                                  session_record_path=claim["sessionRecordPath"], log=logs.append)
            native_path(execution.directory).mkdir(parents=True, mode=0o700, exist_ok=True)
            native_path(execution.evidence_directory).mkdir(parents=True, mode=0o700, exist_ok=True)
            with patch.object(module, "run", side_effect=fake_run), \
                    patch.object(DecisionAdapter, "available", return_value=(True, None)), \
                    patch.object(module, "check_preparation", return_value=None), \
                    patch.object(module, "native_evidence", return_value={}):
                execution.run()
        self.assertEqual(actions, ["correct", "correct", "continue"], logs)
        self.assertTrue(transcripts)
        self.assertTrue(all(not path.exists() for path in transcripts))
        with self.board_.store.db.read() as db:
            run = db.execute("SELECT state,reason_code FROM maintenance_runs").fetchone()
            self.assertEqual((run["state"], run["reason_code"]), ("completed", None))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts WHERE task_id=?",
                                        (started["runId"],)).fetchone()[0], 1)
            rounds = [json.loads(row[0])["round"] for row in db.execute(
                "SELECT payload_json FROM events WHERE kind='maintenance.answer_checked' ORDER BY seq")]
            self.assertEqual(rounds, [0, 1, 2])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM micro_task_classifications").fetchone()[0], 1)
        audits = self.board_.directory / "attempts" / started["runId"]
        requests = list(native_path(audits).rglob("request.json"))
        self.assertTrue(requests)
        for path in requests:
            self.assertNotIn("prompt", json.loads(path.read_text(encoding="utf-8")))
        for path in native_path(audits).rglob("result.json"):
            self.assertNotIn("rawAnswer", json.loads(path.read_text(encoding="utf-8")))
