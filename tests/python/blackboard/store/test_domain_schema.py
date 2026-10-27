"""Domain evaluation constraints on a private, model-free board."""
import sqlite3

from blackboard.tasks.test_workflow import WorkflowTestCase


class DomainSchemaTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.board_ = self.board()
        self.run = self.submit(self.board_, request_id="classified")
        self.tasks = [self.board_.submit_execution(
            requestId=f"maintenance-{index}", task="Synthetic maintenance execution",
            adapter="command", argv=["true"], cwd=str(self.workdir(f"maintenance-{index}")),
        )["task"]["runId"] for index in range(2)]
        self.now = self.board_.store.now()
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO maintenance_runs VALUES('maintenance','manual','fixture',?,'completed',NULL,NULL,?,?)",
                       (self.tasks[0], self.now, self.now))
            db.execute("INSERT INTO domain_nodes VALUES('root','Engineering','Engineering expertise',NULL,NULL,'maintenance')")

    def test_defaults_and_a_single_running_maintenance(self):
        with self.board_.store.db.write() as db:
            settings = db.execute("SELECT maintenance_buddy_id,maintenance_interval_seconds FROM router_settings").fetchone()
            self.assertEqual(tuple(settings), (None, 3600))
            for value in (0, -1, None):
                with self.subTest(interval=value), self.assertRaises(sqlite3.IntegrityError):
                    db.execute("UPDATE router_settings SET maintenance_interval_seconds=?", (value,))
            db.execute("UPDATE maintenance_runs SET state='running',finished_at=NULL")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO maintenance_runs VALUES('second','interval','fixture',?,'running',NULL,NULL,?,NULL)",
                           (self.tasks[1], self.now))

    def test_maintenance_state_time_trigger_and_execution_binding(self):
        with self.board_.store.db.write() as db:
            for column, value in (("trigger_type", "automatic"), ("task_id", "missing"),
                                  ("state", "queued"), ("finished_at", None),
                                  ("state", "failed"), ("finished_at", "1900-01-01"),
                                  ("maintenance_run_id", None), ("reason_code", "maintenance.invalid-answer"),
                                  ("reason_params_json", "{}")):
                with self.subTest(column=column, value=value), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(f"UPDATE maintenance_runs SET {column}=?", (value,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO maintenance_runs VALUES('second','interval','fixture',?,'completed',NULL,NULL,?,?)",
                           (self.tasks[0], self.now, self.now))
            db.execute("UPDATE maintenance_runs SET state='failed',reason_code='maintenance.provider-unavailable',"
                       "reason_params_json='{}'")
            for params in (None, 'invalid-json', '[]', 'null'):
                with self.subTest(params=params), self.assertRaises(sqlite3.IntegrityError):
                    db.execute("UPDATE maintenance_runs SET reason_params_json=?", (params,))

    def test_active_names_unique_and_merged_nodes_keep_their_identity(self):
        with self.board_.store.db.write() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO domain_nodes VALUES('duplicate','Engineering','Same name',NULL,NULL,'maintenance')")
            db.execute("INSERT INTO domain_nodes VALUES('old','Earlier','Earlier expertise',NULL,NULL,'maintenance')")
            db.execute("UPDATE domain_nodes SET merged_into_id='root' WHERE node_id='old'")
            db.execute("INSERT INTO domain_nodes VALUES('replacement','Earlier','New meaning',NULL,NULL,'maintenance')")
            for column, value in (("parent_id", "root"), ("merged_into_id", "root"),
                                  ("parent_id", "missing"), ("merged_into_id", "missing"),
                                  ("maintenance_run_id", "missing"), ("node_id", None),
                                  ("name", "  "), ("definition", "")):
                with self.subTest(column=column, value=value), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(f"UPDATE domain_nodes SET {column}=? WHERE node_id='root'", (value,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM domain_nodes WHERE node_id='root'")

    def test_classification_is_one_row_per_micro_task_and_has_closed_types(self):
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO micro_task_classifications VALUES(?, 'root','create','maintenance')",
                       (self.run["runId"],))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO micro_task_classifications VALUES(?, 'root','debug','maintenance')",
                           (self.run["runId"],))
            for task_type in ("create", "change", "debug", "verify", "review", "research", "write"):
                db.execute("UPDATE micro_task_classifications SET task_type=?", (task_type,))
            for column, value in (("task_type", "other"), ("run_id", "missing"),
                                  ("run_id", None), ("domain_node_id", None),
                                  ("domain_node_id", "missing"), ("maintenance_run_id", "missing")):
                with self.subTest(column=column, value=value), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(f"UPDATE micro_task_classifications SET {column}=?", (value,))

    def test_summary_evidence_records_read_and_cited_state_with_natural_keys(self):
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO kind_summaries VALUES('codex','fixture','high','Observed expertise','maintenance')")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO kind_summaries VALUES('codex','fixture','high','Duplicate','maintenance')")
            db.execute("INSERT INTO kind_summary_evidence VALUES('codex','fixture','high',?,0)", (self.run["runId"],))
            self.assertEqual(db.execute("SELECT cited FROM kind_summary_evidence").fetchone()[0], 0)
            db.execute("UPDATE kind_summary_evidence SET cited=1")
            self.assertEqual(db.execute("SELECT cited FROM kind_summary_evidence").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO kind_summary_evidence VALUES('codex','fixture','high',?,0)", (self.run["runId"],))
            for column, value in (("effort", "missing"), ("run_id", "missing"),
                                  ("cited", None), ("cited", -1), ("cited", 2)):
                with self.subTest(column=column), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(f"UPDATE kind_summary_evidence SET {column}=?", (value,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE kind_summaries SET maintenance_run_id='missing'")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM kind_summaries")

    def test_absent_routing_judgment_differs_from_explicitly_unclassified(self):
        with self.board_.store.db.write() as db:
            db.executemany("INSERT INTO evaluation_decisions(decision_id,status,created_at) VALUES(?,'completed',?)",
                           [(name, self.now) for name in ("unrecorded", "unclassified", "classified")])
            db.execute("INSERT INTO routing_judgments VALUES('unclassified',NULL,'research')")
            db.execute("INSERT INTO routing_judgments VALUES('classified','root','debug')")
            rows = {row["decision_id"]: row["domain_node_id"] for row in db.execute("SELECT * FROM routing_judgments")}
            self.assertNotIn("unrecorded", rows)
            self.assertIsNone(rows["unclassified"])
            self.assertEqual(rows["classified"], "root")
            for column, value in (("task_type", "other"), ("domain_node_id", "missing"),
                                  ("decision_id", "missing"), ("decision_id", None)):
                with self.subTest(column=column), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(f"UPDATE routing_judgments SET {column}=? WHERE decision_id='classified'", (value,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO routing_judgments VALUES('classified',NULL,'write')")
