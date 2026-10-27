"""Maintenance packet and partial write behavior on a private board."""
from copy import deepcopy
import json

from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.blackboard.routing.maintenance_data import (
    answer_schema, apply_answer, build_packet, merge_domain, pending, rename_domain,
    pending_count,
)
from hey_my_buddy.protocol.structured_schema import schema_errors
from hey_my_buddy.errors import BoardError


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

    def summary(self, packet, citations=None):
        return [{**{key: kind[key] for key in ("harness", "model", "effort")},
                 "text": "Observed work", "citations": citations or []}
                for kind in packet["kinds"]]

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
                                      for run in self.runs], "summaries": self.summary(packet)}
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
                                for run in self.runs], "summaries": self.summary(packet)}
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
            except BoardError as error:
                self.assertEqual(error.code, "INVALID_ARGUMENT")
            else:
                self.fail("duplicate active name was accepted")
            try:
                merge_domain(db, "target", "leaf")
            except BoardError as error:
                self.assertEqual(error.code, "INVALID_ARGUMENT")
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

    def test_bad_shape_does_not_block_independent_facts_and_summary_is_required(self):
        packet = self.packet()
        first, second = (run["runId"] for run in self.runs)
        answer = {
            "newNodes": [
                {"id": "good", "name": "Good", "definition": "A valid node", "parentId": None},
                {"id": "bad", "name": "Bad", "definition": "A valid node", "parentId": 12},
                {"id": "duplicate", "name": "Good", "definition": "A duplicate name", "parentId": None},
                {"id": "cycle-a", "name": "A", "definition": "A node", "parentId": "cycle-b"},
                {"id": "cycle-b", "name": "B", "definition": "B node", "parentId": "cycle-a"},
            ],
            "merges": {},
            "classifications": [
                {"runId": first, "domainNodeId": "good", "taskType": "create"},
                {"runId": second, "domainNodeId": "good", "taskType": "invalid"},
            ],
            "summaries": [],
        }
        with self.board_.store.db.write() as db:
            result = apply_answer(db, packet=packet, answer=answer,
                                  maintenance_run_id=self.maintenance_id)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM domain_nodes").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM micro_task_classifications").fetchone()[0], 1)
            self.assertTrue(pending(db))
        self.assertEqual(result["written"]["nodes"], 1)
        self.assertEqual(result["written"]["classifications"], 1)
        self.assertTrue(any("newNodes[1]" in error for error in result["errors"]))
        self.assertTrue(any("answer.merges: type" in error for error in result["errors"]))
        self.assertTrue(any("active domain name already exists" in error for error in result["errors"]))
        self.assertTrue(any("parent cycle" in error for error in result["errors"]))
        self.assertTrue(any("missing summary" in error for error in result["errors"]))

    def test_merge_target_must_still_be_active(self):
        packet = self.packet()
        answer = {
            "newNodes": [
                {"id": "source", "name": "Source", "definition": "Source", "parentId": None},
                {"id": "child", "name": "Child", "definition": "Child", "parentId": "source"},
                {"id": "target", "name": "Target", "definition": "Target", "parentId": None},
                {"id": "alias", "name": "Alias", "definition": "Alias", "parentId": None},
            ],
            "merges": [{"sourceId": "alias", "targetId": "target"},
                       {"sourceId": "source", "targetId": "alias"}],
            "classifications": [], "summaries": self.summary(packet),
        }
        with self.board_.store.db.write() as db:
            result = apply_answer(db, packet=packet, answer=answer,
                                  maintenance_run_id=self.maintenance_id)
            nodes = {row["node_id"]: row for row in db.execute("SELECT * FROM domain_nodes")}
        self.assertEqual(result["written"]["merges"], 1)
        self.assertTrue(any("merge target is not active" in error for error in result["errors"]))
        self.assertIsNone(nodes["source"]["merged_into_id"])
        self.assertEqual(nodes["child"]["parent_id"], "source")

    def test_host_boundary_count_comes_from_requests(self):
        first = self.runs[0]["runId"]
        with self.board_.store.db.write() as db:
            now = self.board_.store.now()
            db.execute(
                "INSERT INTO workflow_requests(request_id,run_id,kind,summary,payload_json,"
                "state,expected_revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                ("host-boundary-test", first, "assistance", "Review evidence", "{}", "approved", 1,
                 now, now),
            )
        item = next(item for item in self.packet()["microTasks"] if item["runId"] == first)
        self.assertEqual(item["result"]["hostBoundaries"], 1)
        self.assertEqual(item["result"]["hostBoundaryFacts"][0]["requestId"], "host-boundary-test")

    def test_deleted_packet_run_is_skipped_and_affected_summary_is_rejected(self):
        packet = deepcopy(self.packet())
        packet["microTasks"][0]["runId"] = "deleted-after-packet"
        packet["classificationRunIds"][0] = "deleted-after-packet"
        survivor = packet["classificationRunIds"][1]
        answer = {
            "newNodes": [{"id": "survivor", "name": "Survivor", "definition": "Valid work",
                          "parentId": None}],
            "merges": [],
            "classifications": [
                {"runId": "deleted-after-packet", "domainNodeId": "survivor", "taskType": "create"},
                {"runId": survivor, "domainNodeId": "survivor", "taskType": "create"},
            ],
            "summaries": self.summary(packet, ["deleted-after-packet", survivor]),
        }
        with self.board_.store.db.write() as db:
            result = apply_answer(db, packet=packet, answer=answer,
                                  maintenance_run_id=self.maintenance_id)
            rows = list(db.execute("SELECT run_id FROM micro_task_classifications"))
            self.assertIsNone(db.execute("SELECT text FROM kind_summaries").fetchone())
        self.assertEqual([row[0] for row in rows], [survivor])
        self.assertEqual(result["written"]["classifications"], 1)
        self.assertTrue(any("citations" in error for error in result["errors"]))
        answer["summaries"] = self.summary(packet, [survivor])
        with self.board_.store.db.write() as db:
            corrected = apply_answer(db, packet=packet, answer=answer,
                                     maintenance_run_id=self.maintenance_id)
            self.assertIsNotNone(db.execute("SELECT text FROM kind_summaries").fetchone())
        self.assertEqual(corrected["errors"], [])

    def test_console_domain_boundaries_reject_wrong_id_shapes(self):
        with self.board_.store.db.write() as db:
            for action in (lambda: rename_domain(db, {"id": "x"}, "Name", "Definition"),
                           lambda: merge_domain(db, {"id": "x"}, "target"),
                           lambda: merge_domain(db, "source", {"id": "x"})):
                with self.assertRaises(BoardError) as failure:
                    action()
                self.assertEqual(failure.exception.code, "INVALID_ARGUMENT")

    def test_raw_json_answer_and_invalid_json_boundary(self):
        packet = self.packet()
        answer = {
            "newNodes": [{"id": "work", "name": "Work", "definition": "Work tasks", "parentId": None}],
            "merges": [],
            "classifications": [{"runId": run["runId"], "domainNodeId": "work", "taskType": "create"}
                                for run in self.runs],
            "summaries": self.summary(packet),
        }
        with self.board_.store.db.write() as db:
            for raw in (None, "{invalid"):
                rejected = apply_answer(db, packet=packet, answer=raw,
                                        maintenance_run_id=self.maintenance_id)
                self.assertEqual(rejected["errors"], ["answer-invalid-json"])
            applied = apply_answer(db, packet=packet, answer=json.dumps(answer),
                                   maintenance_run_id=self.maintenance_id)
        self.assertEqual(applied["errors"], [])
        self.assertEqual(applied["written"]["classifications"], 2)

    def test_sixty_classified_tasks_are_read_in_two_batches_without_losing_progress(self):
        for number in range(2, 60):
            run = self.submit(self.board_, request_id=f"read-evidence-{number}", task=f"Evidence {number}",
                              cwd=str(self.workdir(f"read-evidence-{number}")), kind="existing")
            self.finish_turn(self.board_, self.claim(self.board_, run_id=run["runId"]))
            self.runs.append(run)
        with self.board_.store.db.write() as db:
            db.execute("UPDATE workflow_runs SET state='concluded'")
            db.execute("INSERT INTO domain_nodes VALUES('existing','Existing','Existing domain',NULL,NULL,?)",
                       (self.maintenance_id,))
            db.executemany("INSERT INTO micro_task_classifications VALUES(?,'existing','review',?)",
                           [(run["runId"], self.maintenance_id) for run in self.runs])
            self.assertEqual(pending_count(db), 60)
            first = build_packet(db)
            self.assertEqual(len(first["microTasks"]), 50)
            self.assertEqual(first["classificationRunIds"], [])
            self.assertTrue(all(item["classification"] == {"domainNodeId": "existing", "taskType": "review"}
                                for item in first["microTasks"]))
            first_ids = {item["runId"] for item in first["microTasks"]}
            result = apply_answer(db, packet=first, answer={
                "newNodes": [], "merges": [], "classifications": [],
                "summaries": self.summary(first, [first["microTasks"][0]["runId"]]),
            }, maintenance_run_id=self.maintenance_id)
            self.assertEqual(result["errors"], [])
            self.assertEqual(pending_count(db), 10)
            second = build_packet(db)
            self.assertEqual(len(second["microTasks"]), 10)
            self.assertFalse(first_ids & {item["runId"] for item in second["microTasks"]})
            result = apply_answer(db, packet=second, answer={
                "newNodes": [], "merges": [], "classifications": [],
                "summaries": self.summary(second, [second["microTasks"][0]["runId"]]),
            }, maintenance_run_id=self.maintenance_id)
            self.assertEqual(result["errors"], [])
            self.assertEqual(pending_count(db), 0)
            self.assertIsNone(build_packet(db))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM kind_summary_evidence").fetchone()[0], 60)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM kind_summary_evidence WHERE cited=1").fetchone()[0], 1)
