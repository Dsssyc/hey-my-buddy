"""Maintenance uses a private file boundary and the ordinary execution queue."""
from __future__ import annotations

import json
from unittest.mock import patch

from admission import admit_board, release_before_spawn
from blackboard.routing.fixtures.router_tool_receipt import claim_tool_receipt
from blackboard.tasks.test_workflow import WorkflowTestCase
from support import BoardTestCase, FakeClock

from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.blackboard.store.db import canonical_json
from hey_my_buddy.buddy.roles.run_observers import MaintenanceCorrection
from hey_my_buddy.buddy.runtime.execution import Execution
from hey_my_buddy.paths import native_path
from hey_my_buddy.protocol import process_identity
from hey_my_buddy.buddy.harnesses.registry import run_seam
from hey_my_buddy.buddy.harnesses.run_contract import (
    NativeIdentity, RunEnd, RunResult, RunValue, StopEvidence, StopLayer,
)
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import maintenance as files


BUDDY_ID = "dsh:fixture:maintenance:off"
NONCE = "m" * 32
T0 = "2026-01-01T00:00:00.000Z"
OBSERVATIONS = {"toolCalls": 0, "toolMarkerFrames": 0, "deniedInteractions": 0}


class EmptyMaintenanceTests(BoardTestCase):
    def test_empty_board_never_queues_a_model_call(self):
        board = self.board()
        self.assertEqual(board.console_call("maintenance_start", {}), {"status": "no-new-evidence"})
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM maintenance_runs").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks WHERE owner='decision'").fetchone()[0], 0)

    def test_private_material_is_bound_to_run_location_and_digest(self):
        board = self.board()
        reference = files.publish(board.directory, "run-a", {"largeFact": "evidence"})
        self.assertEqual(files.read(board.directory, "run-a", reference), {"largeFact": "evidence"})
        with self.assertRaises(BoardError) as caught:
            files.read(board.directory, "run-b", reference)
        self.assertEqual(caught.exception.code, "PRIVATE_PATH_UNSAFE")
        native_path(reference["location"]).write_text("{}", encoding="utf-8")
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
        with self.board_.store.db.write() as db:
            db.execute("UPDATE workflow_runs SET state='concluded' WHERE run_id=?", (self.evidence_id,))
            db.execute("INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,"
                       "available,enabled,capabilities_json) VALUES(?,?,?,?,?,?,1,1,?)",
                       (BUDDY_ID, "Maintenance", "dsh", "fixture", "maintenance", "off", canonical_json([])))

    def configure(self, *, buddy_id=BUDDY_ID, interval=3600, preset="standard"):
        with self.board_.store.db.write() as db:
            router._write_settings(db, {"maintenanceBuddyId": buddy_id,
                                         "maintenanceIntervalSeconds": interval,
                                         "routingBudget": preset})

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
                "classifications": [{"runId": self.evidence_id, "domainNodeId": "software",
                                     "taskType": "debug"}],
                "summaries": [{"harness": kind["harness"], "model": kind["model"],
                               "effort": kind["effort"], "text": "Parser repair",
                               "citations": [self.evidence_id]} for kind in packet["kinds"]]}

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
        with self.board_.store.db.write() as db:
            db.execute("UPDATE workflow_runs SET state='concluded' WHERE run_id=?", (run["runId"],))
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

    def test_console_pending_read_counts_unclassified_work_and_stale_kinds(self):
        with self.assertRaises(BoardError) as caught:
            self.board_.call("maintenance_pending", {})
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        before = self.board_.store.head()
        self.assertEqual(self.board_.console_call("maintenance_pending", {}),
                         {"unclassifiedMicroTaskCount": 1, "staleBuddyKindCount": 0})
        self.assertEqual(self.board_.store.head(), before)
        self.configure()
        claim = self.claim_maintenance(self.start()["runId"])
        packet = self.packet(claim)
        answer = self.valid_answer(packet)
        answer["summaries"] = []
        self.assertTrue(self.submit_answer(claim, 0, answer)["errors"])
        self.assertEqual(self.board_.console_call("maintenance_pending", {}),
                         {"unclassifiedMicroTaskCount": 0, "staleBuddyKindCount": len(packet["kinds"])})
        self.assertEqual(self.submit_answer(claim, 1, self.valid_answer(packet))["errors"], [])
        self.assertEqual(self.board_.console_call("maintenance_pending", {}),
                         {"unclassifiedMicroTaskCount": 0, "staleBuddyKindCount": 0})

    def test_start_claim_frozen_packet_budget_and_no_router_table_pollution(self):
        self.configure(preset="deep")
        started = self.start()
        self.assertEqual(started["status"], "running")
        self.assertEqual(self.start()["maintenanceRunId"], started["maintenanceRunId"])
        claim = self.claim_maintenance(started["runId"])
        document = claim["decisionInput"]
        self.assertEqual(document["routingMode"], "fast")
        self.assertEqual(document["budget"]["preset"], "deep")
        self.assertGreater(document["budget"]["timeoutSeconds"], 60)
        self.assertEqual(self.packet(claim)["classificationRunIds"], [self.evidence_id])
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
            self.assertEqual(db.execute("SELECT COUNT(*) FROM router_outcomes").fetchone()[0], 0)

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
        self.board_.call("execution_result", {
            "attemptId": claim["attempt"]["attemptId"], "generation": claim["attempt"]["generation"],
            "nonce": NONCE, "status": "failed", "shutdownConfirmed": True,
            "result": {"status": "error", "code": "router-no-answer"}})
        with self.board_.store.db.read() as db:
            run = db.execute("SELECT state,reason_code,finished_at FROM maintenance_runs").fetchone()
            self.assertEqual((run["state"], run["reason_code"]), ("failed", "router-no-answer"))
            self.assertIsNotNone(run["finished_at"])
        self.assertEqual(self.board_.store.maintenance.collect(), 120)
        self.assertEqual(self.start()["status"], "running")
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM maintenance_runs").fetchone()[0], 2)

    def test_native_failure_retains_final_rejected_entries_on_the_run(self):
        self.configure()
        claim = self.claim_maintenance(self.start()["runId"])
        feedback = self.submit_answer(claim, 0, {})
        self.assertTrue(feedback["errors"])
        self.board_.call("execution_result", {
            "attemptId": claim["attempt"]["attemptId"], "generation": claim["attempt"]["generation"],
            "nonce": NONCE, "status": "failed", "shutdownConfirmed": True,
            "result": {"status": "error", "code": "router-budget-exhausted"}})
        with self.board_.store.db.read() as db:
            run = db.execute("SELECT * FROM maintenance_runs").fetchone()
        self.assertEqual(run["reason_code"], "router-budget-exhausted")
        reference = json.loads(run["reason_params_json"])["feedback"]
        self.assertEqual(files.read(self.board_.directory, run["maintenance_run_id"], reference), feedback)

    def test_complete_answer_drains_evidence_without_a_second_model_call(self):
        self.configure()
        started = self.start()
        claim = self.claim_maintenance(started["runId"])
        feedback = self.submit_answer(claim, 0, self.valid_answer(self.packet(claim)))
        self.assertEqual(feedback["errors"], [])
        self.settle_success(claim)
        with self.board_.store.db.read() as db:
            run = db.execute("SELECT state,reason_code FROM maintenance_runs").fetchone()
            self.assertEqual((run["state"], run["reason_code"]), ("completed", None))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM router_outcomes").fetchone()[0], 0)
        self.assertEqual(self.board_.store.maintenance.collect(), 3600)
        self.assertEqual(self.start()["status"], "no-new-evidence")

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
                                       if item["runId"] == self.evidence_id]}
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

    def test_stale_summary_without_unclassified_work_waits_then_manual_bypasses(self):
        self.configure(interval=120)
        started = self.start()
        claim = self.claim_maintenance(started["runId"])
        self.assertEqual(self.submit_answer(claim, 0, self.valid_answer(self.packet(claim)))["errors"], [])
        self.settle_success(claim)
        second_id = self.add_settled_evidence("newer")
        task_id = self.board_.submit_execution(
            requestId="later-maintenance-evidence", task="Later classification run",
            adapter="command", argv=["true"], cwd=str(self.workdir("later-maintenance")))["task"]["runId"]
        self.clock.advance(1)
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO maintenance_runs(maintenance_run_id,trigger_type,buddy_id,task_id,state,"
                       "started_at,finished_at) VALUES(?, 'interval', ?, ?, 'completed', ?, ?)",
                       ("later-summary-fixture", BUDDY_ID, task_id, self.clock.value, self.clock.value))
            db.execute("INSERT INTO micro_task_classifications(run_id,domain_node_id,task_type,maintenance_run_id)"
                       " VALUES(?,'software','debug','later-summary-fixture')", (second_id,))
        self.assertEqual(self.board_.store.maintenance.collect(), 120)
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM maintenance_runs").fetchone()[0], 2)
        manual = self.start()
        self.assertEqual(manual["status"], "running")
        packet = self.packet(self.claim_maintenance(manual["runId"]))
        self.assertEqual(packet["classificationRunIds"], [])
        self.assertIn(second_id, {item["runId"] for item in packet["microTasks"]})

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

        def fake_run(request, *, observer, services, cancelled):
            self.assertEqual(request.permission, "none")
            self.assertEqual(request.budget.timeout_seconds, 300)
            self.assertFalse(cancelled())
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
                    patch.object(module, "check_preparation", return_value=None), \
                    patch.object(module, "native_evidence", return_value={}):
                execution.run()
        self.assertEqual(actions, ["correct", "correct", "continue"], logs)
        with self.board_.store.db.read() as db:
            run = db.execute("SELECT state,reason_code FROM maintenance_runs").fetchone()
            self.assertEqual((run["state"], run["reason_code"]), ("completed", None))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts WHERE task_id=?",
                                        (started["runId"],)).fetchone()[0], 1)
            rounds = [json.loads(row[0])["round"] for row in db.execute(
                "SELECT payload_json FROM events WHERE kind='maintenance.answer_checked' ORDER BY seq")]
            self.assertEqual(rounds, [0, 1, 2])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM micro_task_classifications").fetchone()[0], 1)
