"""Maintenance packet and partial write behavior on a private board."""
from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.blackboard.routing.maintenance_data import (
    answer_schema, apply_answer, build_packet, merge_domain, pending, rename_domain,
)
from hey_my_buddy.buddy.roles.structured_call import schema_errors


class MaintenanceDataTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.board_ = self.board()
        self.runs = [self.submit(self.board_, request_id=f"evidence-{n}", task=f"Task {n}",
                                 cwd=str(self.workdir(f"work-{n}")), kind="worktree")
                     for n in range(2)]
        for run in self.runs:
            claim = self.claim(self.board_, run_id=run["runId"])
            self.finish_turn(self.board_, claim)
        task_id = self.board_.submit_execution(
            requestId="maintenance-execution", task="Synthetic maintenance execution",
            adapter="command", argv=["true"], cwd=str(self.workdir("maintenance")),
        )["task"]["runId"]
        self.maintenance_id = "maintenance-test"
        with self.board_.store.db.write() as db:
            db.execute("UPDATE workflow_runs SET state='concluded' WHERE run_id IN (?,?)",
                       [run["runId"] for run in self.runs])
            now = self.board_.store.now()
            db.execute("INSERT INTO maintenance_runs VALUES(?, 'manual','fixture',?, 'completed',NULL,NULL,?,?)",
                       (self.maintenance_id, task_id, now, now))

    def packet(self):
        with self.board_.store.db.read() as db:
            return build_packet(db)

    def test_packet_has_oldest_unclassified_tasks_and_declared_schema(self):
        packet = self.packet()
        with self.board_.store.db.read() as db:
            self.assertTrue(pending(db))
        self.assertEqual({item["runId"] for item in packet["microTasks"]},
                         {run["runId"] for run in self.runs})
        self.assertEqual(len(packet["taskTypes"]), 7)
        self.assertEqual(packet["microTasks"][0]["task"], "Task 0")
        schema = answer_schema(packet)
        answer = {"newNodes": [{"id": "new", "name": "Systems", "definition": "Systems work",
                                 "parentId": None}], "merges": [],
                  "classifications": [{"runId": run["runId"], "domainNodeId": "new", "taskType": "debug"}
                                      for run in self.runs], "summaries": []}
        self.assertEqual(schema_errors(answer, schema), [])
        with self.board_.store.db.write() as db:
            result = apply_answer(db, packet=packet, answer=answer,
                                  maintenance_run_id=self.maintenance_id)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["written"]["classifications"], 2)
        self.assertIsNone(self.packet())
        with self.board_.store.db.read() as db:
            self.assertFalse(pending(db))

    def test_partial_write_correction_cannot_replace_accepted_facts(self):
        packet = self.packet()
        first = self.runs[0]["runId"]
        second = self.runs[1]["runId"]
        kind = {key: packet["kinds"][0][key] for key in ("harness", "model", "effort")}
        answer = {"newNodes": [{"id": "root", "name": "Engineering", "definition": "Build systems",
                                 "parentId": None}], "merges": [],
                  "classifications": [{"runId": first, "domainNodeId": "root", "taskType": "create"},
                                      {"runId": second, "domainNodeId": "missing", "taskType": "debug"}],
                  "summaries": [{**kind, "text": "Observed engineering work", "citations": [first]}]}
        with self.board_.store.db.write() as db:
            result = apply_answer(db, packet=packet, answer=answer,
                                  maintenance_run_id=self.maintenance_id)
        self.assertTrue(any(second in error for error in result["errors"]))
        self.assertEqual(result["written"]["summaries"], 1)
        answer["classifications"] = [
            {"runId": first, "domainNodeId": "root", "taskType": "debug"},
            {"runId": second, "domainNodeId": "root", "taskType": "debug"}]
        answer["summaries"][0]["text"] = "Attempted replacement"
        with self.board_.store.db.write() as db:
            result = apply_answer(db, packet=packet, answer=answer,
                                  maintenance_run_id=self.maintenance_id)
            rows = list(db.execute("SELECT run_id,task_type FROM micro_task_classifications ORDER BY run_id"))
            summary = db.execute("SELECT text FROM kind_summaries").fetchone()[0]
        self.assertEqual(result["errors"], [])
        self.assertEqual(dict(rows), {first: "create", second: "debug"})
        self.assertEqual(summary, "Observed engineering work")

    def test_new_parent_chain_merge_flatten_and_rename_validation(self):
        packet = self.packet()
        answer = {"newNodes": [
            {"id": "leaf", "name": "Leaf", "definition": "Leaf expertise", "parentId": "source"},
            {"id": "source", "name": "Source", "definition": "Source expertise", "parentId": None},
            {"id": "target", "name": "Target", "definition": "Target expertise", "parentId": None},
            {"id": "alias", "name": "Alias", "definition": "Alias expertise", "parentId": None}],
            "merges": [{"sourceId": "alias", "targetId": "source"},
                       {"sourceId": "source", "targetId": "target"}],
            "classifications": [{"runId": run["runId"], "domainNodeId": "leaf", "taskType": "create"}
                                for run in self.runs], "summaries": []}
        with self.board_.store.db.write() as db:
            result = apply_answer(db, packet=packet, answer=answer,
                                  maintenance_run_id=self.maintenance_id)
            self.assertEqual(result["errors"], [])
            rows = {r["node_id"]: dict(r) for r in db.execute("SELECT * FROM domain_nodes")}
            self.assertEqual(rows["leaf"]["parent_id"], "target")
            self.assertEqual(rows["alias"]["merged_into_id"], "target")
            repeated = apply_answer(db, packet=packet, answer=answer,
                                    maintenance_run_id=self.maintenance_id)
            self.assertEqual(repeated["errors"], [])
            self.assertEqual(repeated["written"]["merges"], 0)
            rename_domain(db, "target", "Retained", "Renamed expertise")
            try:
                rename_domain(db, "leaf", "Retained", "Duplicate")
            except ValueError:
                pass
            else:
                self.fail("duplicate active name was accepted")
            try:
                merge_domain(db, "target", "leaf")
            except ValueError:
                pass
            else:
                self.fail("descendant merge was accepted")

    def test_invalid_citation_keeps_previous_summary(self):
        packet = self.packet()
        kind = {key: packet["kinds"][0][key] for key in ("harness", "model", "effort")}
        answer = {"newNodes": [], "merges": [], "classifications": [],
                  "summaries": [{**kind, "text": "Unsupported", "citations": ["outside-batch"]}]}
        with self.board_.store.db.write() as db:
            result = apply_answer(db, packet=packet, answer=answer,
                                  maintenance_run_id=self.maintenance_id)
            self.assertIsNone(db.execute("SELECT text FROM kind_summaries").fetchone())
        self.assertTrue(any("citations" in error for error in result["errors"]))
