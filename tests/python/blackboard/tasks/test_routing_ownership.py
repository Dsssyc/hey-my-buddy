"""All dispatches of a replaced Router retain the same micro-task owner.

Private real routing/claim transactions create both dispatches; stop evidence is
varied explicitly to test readers of historical attempts. No harness runs.
"""
import json
from unittest.mock import patch

from blackboard.routing.test_router_dispatch import RouterDispatchTestCase, B, T0, NONCE
from blackboard.routing import test_router_failover_integration as failover
from console.test_console import ConsoleTestCase
from hey_my_buddy import private_dirs
from hey_my_buddy.blackboard.tasks import micro_task, storage
from hey_my_buddy.errors import BoardError


class RoutingOwnershipTests(RouterDispatchTestCase, ConsoleTestCase):
    start = failover.FailoverTests.start
    answer = failover.FailoverTests.answer
    failure = failover.FailoverTests.failure
    report = failover.FailoverTests.report
    current_task = failover.FailoverTests.current_task
    next_claim = failover.FailoverTests.next_claim

    def claim(self, suffix="1", *, task_id=None):
        result = super().claim(suffix, task_id=task_id)
        if result["claim"]:
            result["claim"]["nonce"] = NONCE + suffix
        return result

    def replaced_router(self):
        request, first = self.start(micro_task=True)
        self.report(first, self.failure(first), status="failed")
        second = self.next_claim(request)
        self.report(second, self.answer(second, buddy_id=B))
        with self.board_.store.db.read() as db:
            tasks = db.execute("SELECT task_id FROM router_dispatches WHERE decision_id=?",
                               (request["decisionId"],)).fetchall()
            self.assertEqual({row[0] for row in tasks}, {first["task"]["taskId"], second["task"]["taskId"]})
            self.assertEqual(db.execute("SELECT task_id FROM decision_requests WHERE decision_id=?",
                                        (request["decisionId"],)).fetchone()[0], second["task"]["taskId"])
            claims = db.execute("SELECT task_id,attempt_id FROM router_claims").fetchall()
            self.assertEqual({tuple(row) for row in claims}, {
                (claim["task"]["taskId"], claim["attempt"]["attemptId"]) for claim in (first, second)})
        return request, first, second

    def test_routing_stop_requires_every_dispatch_to_be_confirmed(self):
        _request, first, second = self.replaced_router()
        for stops in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(stops=stops), self.board_.store.db.write() as db:
                for claim, confirmed in zip((first, second), stops):
                    db.execute("UPDATE attempts SET shutdown_confirmed=? WHERE attempt_id=?",
                               (confirmed, claim["attempt"]["attemptId"]))
                if all(stops):
                    self.board_.store.workflow._require_routing_stopped(db, "run-1")
                else:
                    with self.assertRaises(BoardError) as error:
                        self.board_.store.workflow._require_routing_stopped(db, "run-1")
                    self.assertEqual(error.exception.code, "SHUTDOWN_UNCONFIRMED")
                    self.assertIn(error.exception.details["routingTaskId"], {
                        claim["task"]["taskId"] for claim, confirmed in zip((first, second), stops) if not confirmed})
                summary = self.board_.store.workflow.shutdown_summary(db, "run-1")
                self.assertEqual(set(summary["unconfirmedRunIds"]), {
                    claim["task"]["taskId"] for claim, confirmed in zip((first, second), stops) if not confirmed})
                self.assertEqual(summary["unconfirmedCount"], stops.count(False))
                self.assertEqual(summary["descendantsConfirmed"], all(stops))

    def test_history_attributes_both_dispatches_to_the_same_owner(self):
        request, first, second = self.replaced_router()
        for claim in (first, second):
            view = self.board_.call("task_get", {"runId": claim["task"]["taskId"]})["task"]
            self.assertEqual(view["lineage"]["parentRunId"], "run-1")
            self.assertEqual(view["lineage"]["rootRunId"], "run-1")
            with self.board_.store.db.read() as db:
                row = db.execute(f"SELECT * FROM ({micro_task.RELATION_SQL}) WHERE task_id=?",
                                 (claim["task"]["taskId"],)).fetchone()
                self.assertEqual(row["decision_id"], request["decisionId"])
                self.assertEqual(row["source_host_id"], "host-1")
                self.assertEqual(row["router_model"], claim["decisionInput"]["routerBuddy"]["model"])

    def test_review_gate_includes_the_replaced_routers_attempt(self):
        _request, first, second = self.replaced_router()
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,adapter,"
                       "shutdown_confirmed,result_json,created_at,updated_at)"
                       " VALUES('root-attempt','run-1',1,'verifier','finished','dsh',1,'{}',?,?)", (T0, T0))
            db.execute("UPDATE tasks SET state='completed',selected_attempt_id='root-attempt' WHERE task_id='run-1'")
            db.execute("UPDATE workflow_runs SET state='delivered' WHERE run_id='run-1'")
        for claim in (first, second):
            with self.subTest(task_id=claim["task"]["taskId"]):
                with self.board_.store.db.write() as db:
                    db.execute("UPDATE attempts SET shutdown_confirmed=0 WHERE attempt_id=?", (claim["attempt"]["attemptId"],))
                self.assertEqual(self.board_.call("task_list", {"filter": "review"})["runs"], [])
                with self.board_.store.db.write() as db:
                    db.execute("UPDATE attempts SET shutdown_confirmed=1 WHERE attempt_id=?", (claim["attempt"]["attemptId"],))
                self.assertEqual([row["runId"] for row in self.board_.call("task_list", {"filter": "review"})["runs"]], ["run-1"])

    def test_storage_attributes_both_router_private_directories(self):
        _request, first, second = self.replaced_router()
        paths = []
        for claim in (first, second):
            task_id = claim["task"]["taskId"]
            for adapter in ("codex", "zcode"):
                native = private_dirs.ensure_private_dir(private_dirs.native_root(self.board_.directory, adapter, task_id))
                paths.append(native.parent)
        with patch.object(storage, "process_inventory", return_value=([], [], True)):
            inventory = storage.inspect(self.board_.store)
        rows = {row["path"]: row for row in inventory["candidates"]}
        for path in paths:
            self.assertEqual(rows[str(path)]["runId"], "run-1")
            self.assertNotIn("owner-unproven", rows[str(path)]["reasons"])

    def test_console_timeline_retains_both_router_execution_spans(self):
        request, first, second = self.replaced_router()
        _server, browser = self.open_console(self.board_)
        status, _headers, raw = browser.get("/api/macro-tasks/run%3Arun-1/timeline")
        self.assertEqual(status, 200, raw)
        timeline = json.loads(raw)
        spans = [span for span in timeline["spans"] if span["kind"] == "routing"]
        self.assertEqual({(span["decisionTaskId"], span["attemptId"]) for span in spans}, {
            (claim["task"]["taskId"], claim["attempt"]["attemptId"]) for claim in (first, second)})
        self.assertTrue(all(span["runId"] == "run-1" and span["shutdownConfirmed"] for span in spans))
        for span in spans:
            with self.subTest(task_id=span["decisionTaskId"]):
                self.assertEqual(span["decisionId"], request["decisionId"])
                selected = span["routing"]["selectedBuddy"]
                self.assertEqual(selected["buddyId"], B)
                self.assertEqual({key: selected[key] for key in ("harness", "provider", "model", "effort")}, {
                    "harness": "dsh", "provider": "fixture", "model": "bravo", "effort": "max"})
                self.assertEqual(span["routing"]["reason"], "The frozen legal choice")
                self.assertEqual(span["routing"]["usage"], {
                    "elapsedMs": 100, "toolCalls": 0, "bytesRead": None})
