"""Offline deletion tests against the authoritative board and private state root."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.blackboard.tasks import macro_task_deletion
from hey_my_buddy.private_dirs import micro_task_root
from hey_my_buddy.errors import BoardError


PHYSICAL_STAGES = (
    "_delete_sessions",
    "_delete_workspaces",
    "_delete_storage_plans",
    "_delete_private_roots",
    "_delete_cli_files",
)


class MacroTaskDeletionTests(WorkflowTestCase):
    def closed(self, board, request_id, *, macro_task=None, macro_task_id=None, project="project"):
        extra = {"macroTask": {"title": macro_task}} if macro_task else {}
        if macro_task_id:
            extra["macroTaskId"] = macro_task_id
        run = self.submit(board, request_id=request_id, cwd=str(self.workdir(project)), **extra)
        cancelled = board.call("workflow_cancel", {"runId": run["runId"], **self.control(run)})
        board.call("workflow_conclude", {
            "runId": run["runId"], "note": "Private deletion fixture is settled",
            **self.control(cancelled),
        })
        return run

    def without_physical_io(self, *, failing=None):
        stack = ExitStack()
        calls = []
        for stage in PHYSICAL_STAGES:
            def perform(store, scope, name=stage):
                self.assertEqual(Path(store.directory).resolve(), self.directory)
                calls.append(name)
                if name == failing:
                    raise RuntimeError(f"interrupted at {name}")
                return []
            stack.enter_context(patch.object(macro_task_deletion, stage, side_effect=perform))
        return stack, calls

    def assert_code(self, code, function, *args):
        with self.assertRaises(BoardError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_preview_and_delete_use_entire_group_and_leave_other_group_untouched(self):
        board = self.board()
        first = self.closed(board, "first", macro_task="Disposable group")
        second = self.closed(board, "second", macro_task_id=first["macroTaskId"])
        other = self.closed(board, "other", macro_task="Retained group", project="retained-project")
        target = first["macroTaskId"]
        preview = macro_task_deletion.preview(board.store, target)
        self.assertEqual(preview["macroTaskId"], target)
        self.assertEqual(preview["microTaskCount"], 2)
        self.assertEqual(preview["unconcludedFailureCount"], 0)
        self.assertEqual(preview["blockerTotal"], 0)
        self.assertEqual(preview["backupRetention"], "until-backup-rotation")
        with self.without_physical_io()[0]:
            deleted = macro_task_deletion.delete(board.store, target, 2)
        self.assertEqual(deleted["state"], "deleted")
        self.assertEqual(deleted["microTaskCount"], 2)
        self.assertIsNotNone(deleted["deletedAt"])
        with board.store.db.read() as db:
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            for run in (first, second):
                self.assertIsNone(db.execute("SELECT 1 FROM workflow_runs WHERE run_id=?", (run["runId"],)).fetchone())
                self.assertIsNone(db.execute("SELECT 1 FROM tasks WHERE task_id=?", (run["runId"],)).fetchone())
            self.assertIsNotNone(db.execute("SELECT 1 FROM workflow_runs WHERE run_id=?", (other["runId"],)).fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='macro_task.deleted'").fetchone()[0], 1)
        duplicate = macro_task_deletion.delete(board.store, target, 2)
        self.assertEqual(duplicate["deletionId"], deleted["deletionId"])
        self.assertTrue(duplicate["duplicate"])
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM macro_task_deletions WHERE target_id=?", (target,)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='macro_task.deleted'").fetchone()[0], 1)

    def test_count_revision_conflict_precedes_mark_and_physical_io(self):
        board = self.board()
        first = self.closed(board, "first", macro_task="Counted group")
        target = first["macroTaskId"]
        self.assertEqual(macro_task_deletion.preview(board.store, target)["microTaskCount"], 1)
        self.closed(board, "second", macro_task_id=target)
        stack, calls = self.without_physical_io()
        with stack:
            self.assert_code("REVISION_CONFLICT", macro_task_deletion.delete, board.store, target, 1)
        self.assertEqual(calls, [])
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM macro_task_deletions").fetchone()[0], 0)

    def test_preview_caps_blocker_detail_but_not_group_count(self):
        board = self.board()
        first = self.submit(board, request_id="queued-0", macroTask={"title": "Large group"})
        for number in range(1, 53):
            self.submit(board, request_id=f"queued-{number}", macroTaskId=first["macroTaskId"], kind="worktree")
        preview = macro_task_deletion.preview(board.store, first["macroTaskId"])
        self.assertEqual(preview["microTaskCount"], 53)
        self.assertGreaterEqual(preview["blockerTotal"], 53)
        self.assertEqual(len(preview["blockers"]), 50)
        self.assertEqual(len({(item["runId"], item["reasonCode"]) for item in preview["blockers"]}), 50)

    def test_run_group_counts_failed_without_conclusion_and_accepts_it(self):
        board = self.board()
        run = self.submit(board, request_id="unconcluded", cwd=str(self.workdir("unconcluded")))
        board.call("workflow_cancel", {"runId": run["runId"], **self.control(run)})
        target = "run:" + run["runId"]
        preview = macro_task_deletion.preview(board.store, target)
        self.assertEqual(preview["microTaskCount"], 1)
        self.assertEqual(preview["unconcludedFailureCount"], 1)
        self.assertEqual(preview["blockerTotal"], 0)
        with self.without_physical_io()[0]:
            result = macro_task_deletion.delete(board.store, target, 1)
        self.assertEqual(result["state"], "deleted")
        with board.store.db.read() as db:
            row = db.execute("SELECT target_kind,target_id FROM macro_task_deletions").fetchone()
            self.assertEqual(tuple(row), ("micro-task-tree", target))

    def test_unsettled_run_rejected_with_structured_blocker(self):
        board = self.board()
        run = self.submit(board, request_id="queued", cwd=str(self.workdir("queued")))
        target = "run:" + run["runId"]
        preview = macro_task_deletion.preview(board.store, target)
        self.assertGreaterEqual(preview["blockerTotal"], 1)
        blocker = next(item for item in preview["blockers"] if item["reasonCode"] == "queued")
        self.assertEqual(blocker["runId"], run["runId"])
        self.assertEqual(blocker["reasonCode"], "queued")
        self.assertIn("resolution", blocker)
        error = self.assert_code("MACRO_TASK_NOT_SETTLED", macro_task_deletion.delete, board.store, target, 1)
        self.assertEqual(error.details["blockerTotal"], preview["blockerTotal"])
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM macro_task_deletions").fetchone()[0], 0)

    def test_historical_router_dispatches_and_unattached_route_events_are_deleted(self):
        board = self.board()
        owner = self.closed(board, "route-owner", macro_task="Route history")
        other = self.closed(board, "route-other", macro_task="Other route history", project="route-other-project")
        target = owner["macroTaskId"]
        now = board.store.now()
        decision_id = "decision-delete-history"
        with board.store.db.write() as db:
            original = dict(db.execute("SELECT * FROM tasks WHERE task_id=?", (owner["runId"],)).fetchone())
            db.execute("INSERT INTO evaluation_decisions(decision_id,status,task,created_at) VALUES(?,?,?,?)",
                       (decision_id, "completed", "Router private text", now))
            db.execute("INSERT INTO decision_requests(decision_id,request_id,kind,input_fingerprint,task_id,created_at,updated_at)"
                       " VALUES(?,?,?,?,?,?,?)", (decision_id, "route-history", "select", "hash", "route-latest", now, now))
            db.execute("INSERT INTO router_requests(decision_id,snapshot_json,frozen_at) VALUES(?,?,?)",
                       (decision_id, "{}", now))
            db.execute("INSERT INTO workflow_routes(decision_id,run_id,owner_generation,state,created_at,updated_at)"
                       " VALUES(?,?,?,?,?,?)", (decision_id, owner["runId"], 1, "resolved", now, now))
            for index, task_id in enumerate(("route-earlier", "route-latest")):
                row = {**original, "task_id": task_id, "request_id": f"route-request-{index}",
                       "adapter": "decision", "expected_seconds": None, "router_budget_seconds": 60,
                       "selected_attempt_id": None, "active_attempt_id": None}
                columns = tuple(row)
                db.execute(f"INSERT INTO tasks({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                           tuple(row.values()))
                attempt_id = f"route-attempt-{index}"
                db.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,"
                           "adapter,shutdown_confirmed,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           (attempt_id, task_id, 1, "fixture", "finished", "decision", 1, now, now))
                db.execute("INSERT INTO router_dispatches(task_id,decision_id,router_index,buddy_id,harness,provider,"
                           "model,effort,document_json,input_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (task_id, decision_id, index, "fixture", "dsh", "deepseek-official",
                            "deepseek-flash", "off", "{}", "hash", now))
                db.execute("INSERT INTO router_claims(attempt_id,task_id,generation,tool_policy_json) VALUES(?,?,?,?)",
                           (attempt_id, task_id, 1, "{}"))
            event = db.execute("INSERT INTO events(kind,payload_json,created_at) VALUES(?,?,?)",
                               ("router.answered", json.dumps({"decisionId": decision_id}), now))
            db.execute("INSERT INTO router_outcomes(receipt_id,plane,request_id,router_index,buddy_id,phase,"
                       "task_id,attempt_id,outcome,event_seq,recorded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       ("route-outcome", "work", "route-history", 0, "fixture", "review",
                        "route-earlier", "route-attempt-0", "answered", event.lastrowid, now))
            db.execute("INSERT INTO commands(command_id,task_id,kind,request_hash,response_json,subject,created_at)"
                       " VALUES(?,?,?,?,?,?,?)", ("route-command", "route-earlier", "fixture", "hash",
                                               '{"private":"Router private text"}', "host-1", now))
        with self.without_physical_io()[0]:
            self.assertEqual(macro_task_deletion.delete(board.store, target, 1)["state"], "deleted")
        with board.store.db.read() as db:
            for table, where, values in (
                ("tasks", "task_id IN (?,?)", ("route-earlier", "route-latest")),
                ("attempts", "attempt_id IN (?,?)", ("route-attempt-0", "route-attempt-1")),
                ("router_dispatches", "decision_id=?", (decision_id,)),
                ("router_claims", "task_id IN (?,?)", ("route-earlier", "route-latest")),
                ("workflow_routes", "decision_id=?", (decision_id,)),
                ("evaluation_decisions", "decision_id=?", (decision_id,)),
                ("router_outcomes", "receipt_id=?", ("route-outcome",)),
                ("events", "kind='router.answered' AND json_extract(payload_json,'$.decisionId')=?", (decision_id,)),
            ):
                with self.subTest(table=table):
                    self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", values).fetchone()[0], 0)
            receipt = db.execute("SELECT task_id,attempt_id,response_json,deletion_id FROM commands WHERE command_id='route-command'").fetchone()
            self.assertEqual(tuple(receipt)[:3], (None, None, "{}"))
            self.assertIsNotNone(receipt["deletion_id"])
            self.assertIsNotNone(db.execute("SELECT 1 FROM workflow_runs WHERE run_id=?", (other["runId"],)).fetchone())
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_each_physical_interruption_keeps_mark_and_rows_for_retry(self):
        board = self.board()
        run = self.closed(board, "interrupted", macro_task="Interrupted group")
        target = run["macroTaskId"]
        deletion_id = None
        for stage in PHYSICAL_STAGES:
            with self.subTest(stage=stage):
                stack, calls = self.without_physical_io(failing=stage)
                with stack, self.assertRaises(RuntimeError):
                    macro_task_deletion.delete(board.store, target, 1)
                self.assertIn(stage, calls)
                with board.store.db.read() as db:
                    deletion = db.execute("SELECT deletion_id,state FROM macro_task_deletions WHERE target_id=?", (target,)).fetchone()
                    self.assertIsNotNone(deletion)
                    self.assertEqual(deletion["state"], "deleting")
                    self.assertIsNotNone(db.execute("SELECT 1 FROM workflow_runs WHERE run_id=?", (run["runId"],)).fetchone())
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='macro_task.deleted'").fetchone()[0], 0)
                    if deletion_id is None:
                        deletion_id = deletion["deletion_id"]
                    self.assertEqual(deletion["deletion_id"], deletion_id)
        with self.without_physical_io()[0]:
            done = macro_task_deletion.delete(board.store, target, 1)
        self.assertEqual(done["deletionId"], deletion_id)
        self.assertEqual(done["state"], "deleted")

    def test_final_transaction_failure_rolls_back_all_rows_then_retries(self):
        board = self.board()
        run = self.closed(board, "transaction", macro_task="Transaction group")
        target = run["macroTaskId"]
        stack, _ = self.without_physical_io()
        with stack, patch.object(macro_task_deletion, "_delete_rows", side_effect=RuntimeError("injected final transaction failure")):
            with self.assertRaises(RuntimeError):
                macro_task_deletion.delete(board.store, target, 1)
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT state FROM macro_task_deletions WHERE target_id=?", (target,)).fetchone()[0], "deleting")
            self.assertIsNotNone(db.execute("SELECT 1 FROM workflow_runs WHERE run_id=?", (run["runId"],)).fetchone())
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        with self.without_physical_io()[0]:
            self.assertEqual(macro_task_deletion.delete(board.store, target, 1)["state"], "deleted")

    def test_private_evidence_controls_and_submissions_are_scoped_to_target(self):
        board = self.board()
        target = self.closed(board, "private-target", macro_task="Private evidence")
        other = self.closed(board, "private-other", macro_task="Other private evidence", project="private-other-project")

        def materialize(run):
            task_id = run["runId"]
            native = micro_task_root(self.directory, "dsh", task_id) / "native"
            evidence = self.directory / "attempts" / task_id / "attempt-fixture"
            control = self.directory / "controls" / f"{task_id}.g1.json"
            submission = self.directory / "submissions" / (
                hashlib.sha256(run["requestId"].encode()).hexdigest()[:32] + ".json"
            )
            for path in (native / "session", evidence / "task.txt", control, submission):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("private fixture")
            return native.parent, evidence.parent, control, submission

        target_paths = materialize(target)
        other_paths = materialize(other)
        with ExitStack() as stack:
            for stage in ("_delete_sessions", "_delete_workspaces", "_delete_storage_plans"):
                stack.enter_context(patch.object(macro_task_deletion, stage, return_value=[]))
            result = macro_task_deletion.delete(board.store, target["macroTaskId"], 1)
        self.assertEqual(result["state"], "deleted")
        for path in target_paths:
            self.assertFalse(path.exists(), path)
        for path in other_paths:
            self.assertTrue(path.exists(), path)

