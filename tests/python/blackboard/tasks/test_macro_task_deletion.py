"""Offline deletion tests against the authoritative board and private state root."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.blackboard.tasks import macro_task_deletion, macro_tasks
from hey_my_buddy.buddy.harnesses import registry as harness_registry
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

    def test_failure_after_row_removal_rolls_back_entire_final_transaction(self):
        board = self.board()
        run = self.closed(board, "after-rows", macro_task="After rows")
        target = run["macroTaskId"]
        original = macro_task_deletion._delete_rows
        def fail_after_rows(*args):
            original(*args)
            raise RuntimeError("injected before final commit")
        with self.without_physical_io()[0], patch.object(macro_task_deletion, "_delete_rows",
                                                         side_effect=fail_after_rows):
            with self.assertRaisesRegex(RuntimeError, "before final commit"):
                macro_task_deletion.delete(board.store, target, 1)
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT state FROM macro_task_deletions WHERE target_id=?",
                                        (target,)).fetchone()[0], "deleting")
            self.assertIsNotNone(db.execute("SELECT 1 FROM workflow_runs WHERE run_id=?",
                                            (run["runId"],)).fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='macro_task.deleted'").fetchone()[0], 0)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        with self.without_physical_io()[0]:
            self.assertEqual(macro_task_deletion.delete(board.store, target, 1)["state"], "deleted")

    def test_notification_failure_after_commit_retries_as_single_deleted_record(self):
        board = self.board()
        run = self.closed(board, "after-commit", macro_task="After commit")
        target = run["macroTaskId"]
        with self.without_physical_io()[0], patch.object(board.store, "_notify",
                                                         side_effect=RuntimeError("injected after commit")):
            with self.assertRaisesRegex(RuntimeError, "after commit"):
                macro_task_deletion.delete(board.store, target, 1)
        with board.store.db.read() as db:
            record = db.execute("SELECT deletion_id,state FROM macro_task_deletions WHERE target_id=?",
                                (target,)).fetchone()
            self.assertEqual(record["state"], "deleted")
            self.assertIsNone(db.execute("SELECT 1 FROM workflow_runs WHERE run_id=?", (run["runId"],)).fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='macro_task.deleted'").fetchone()[0], 1)
        repeated = macro_task_deletion.delete(board.store, target, 1)
        self.assertEqual((repeated["state"], repeated["deletionId"], repeated["duplicate"]),
                         ("deleted", record["deletion_id"], True))
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='macro_task.deleted'").fetchone()[0], 1)

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

    def test_run_tree_closes_recursive_helpers_lead_ownership_and_fixes(self):
        board = self.board()
        root = self.closed(board, "tree-root")
        helper = self.closed(board, "tree-helper", project="tree-helper")
        lead_owned = self.closed(board, "tree-lead-owned", project="tree-lead-owned")
        grandchild = self.closed(board, "tree-grandchild", project="tree-grandchild")
        other = self.closed(board, "tree-other", project="tree-other")
        now = board.store.now()
        with board.store.db.write() as db:
            db.execute("INSERT INTO workflow_turns(turn_id,run_id,turn_index,resume_mode,input_json,access,"
                       "expected_seconds,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       ("lead-tree-turn", helper["runId"], 1, "initial", "{}", "write", 60,
                        "returned", now, now))
            for child, parent, request in ((helper, root, "tree-help-request"),
                                           (grandchild, lead_owned, "tree-grand-request")):
                db.execute("INSERT INTO workflow_children(child_task_id,parent_run_id,request_id,"
                           "decision_command_id,state,workspace_intent_json,created_at,updated_at)"
                           " VALUES(?,?,?,?,?,?,?,?)",
                           (child["runId"], parent["runId"], request, request + "-command",
                            "succeeded", "{}", now, now))
            db.execute("INSERT INTO workflow_lead_tasks(owned_run_id,lead_run_id,lead_turn_id,created_at)"
                       " VALUES(?,?,?,?)", (lead_owned["runId"], helper["runId"], "lead-tree-turn", now))
            db.execute("INSERT INTO workflow_fixes(run_id,fixed_run_id,created_at) VALUES(?,?,?)",
                       (grandchild["runId"], root["runId"], now))
        target = "run:" + root["runId"]
        self.assertEqual(macro_task_deletion.preview(board.store, target)["microTaskCount"], 4)
        with self.without_physical_io()[0]:
            self.assertEqual(macro_task_deletion.delete(board.store, target, 4)["state"], "deleted")
        with board.store.db.read() as db:
            for run in (root, helper, lead_owned, grandchild):
                self.assertIsNone(db.execute("SELECT 1 FROM workflow_runs WHERE run_id=?", (run["runId"],)).fetchone())
            self.assertIsNotNone(db.execute("SELECT 1 FROM workflow_runs WHERE run_id=?", (other["runId"],)).fetchone())
            for table in ("workflow_children", "workflow_lead_tasks", "workflow_fixes"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_multiple_attempt_generations_and_fk_dependents_are_removed_together(self):
        board = self.board()
        run = self.closed(board, "generations", macro_task="Generations")
        other = self.closed(board, "generations-other", macro_task="Other generations",
                            project="generations-other")
        task_id = run["runId"]
        now = board.store.now()
        attempt_ids = ("generation-one", "generation-two")
        with board.store.db.write() as db:
            for generation, attempt_id in enumerate(attempt_ids, 1):
                db.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,"
                           "adapter,shutdown_confirmed,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           (attempt_id, task_id, generation, "fixture", "finished", "dsh", 1, now, now))
                db.execute("INSERT INTO workflow_turns(turn_id,run_id,attempt_id,generation,turn_index,"
                           "resume_mode,input_json,access,expected_seconds,state,created_at,updated_at)"
                           " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (f"turn-{generation}", task_id, attempt_id, generation, generation,
                            "initial", "{}", "write", 60, "returned", now, now))
                db.execute("INSERT INTO attempt_process_identities(attempt_id,boot_id,root_pid,"
                           "root_started_at,process_group_id) VALUES(?,?,?,?,?)",
                           (attempt_id, "fixture-boot", generation + 100, generation + 1000,
                            generation + 100))
                db.execute("INSERT INTO attempt_interruptions(attempt_id,evidence_kind,observed_at,state)"
                           " VALUES(?,?,?,?)", (attempt_id, "identity-absent", now, "settled"))
                db.execute("INSERT INTO attempt_shutdown_evidence(attempt_id,evidence_kind,detail_json,observed_at)"
                           " VALUES(?,?,?,?)", (attempt_id, "identity-absent", "{}", now))
                db.execute("INSERT INTO execution_issues(attempt_id,stage,code,message,observed_at)"
                           " VALUES(?,?,?,?,?)", (attempt_id, "fixture", "FIXTURE", "private issue", now))
                db.execute("INSERT INTO attempt_activity(attempt_id,task_id,generation,activity_json,"
                           "created_at,updated_at) VALUES(?,?,?,?,?,?)",
                           (attempt_id, task_id, generation, "{}", now, now))
                db.execute("INSERT INTO attempt_reminders(task_id,attempt_id,kind,expected_multiple,"
                           "elapsed_seconds,tool_in_progress,created_at) VALUES(?,?,?,?,?,?,?)",
                           (task_id, attempt_id, "expected-duration", 2, 1.0, 0, now))
                db.execute("INSERT INTO messages(message_id,task_id,attempt_id,inquiry_id,direction,author,"
                           "body,body_bytes,payload_hash,state,created_at,updated_at)"
                           " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (f"message-{generation}", task_id, attempt_id, f"inquiry-{generation}",
                            "outbound", "fixture", "private content", 15, "hash", "delivered", now, now))
                db.execute("INSERT INTO artifacts(artifact_id,task_id,attempt_id,kind,location,content_hash,"
                           "size_bytes,created_at) VALUES(?,?,?,?,?,?,?,?)",
                           (f"artifact-{generation}", task_id, attempt_id, "fixture", "private-location",
                            f"hash-{generation}", 15, now))
                db.execute("INSERT INTO resource_claims(claim_id,task_id,attempt_id,resource,kind,state,created_at)"
                           " VALUES(?,?,?,?,?,?,?)", (f"claim-{generation}", task_id, attempt_id,
                            f"fixture-{generation}", "fixture", "released", now))
            db.execute("UPDATE tasks SET selected_attempt_id=?,active_attempt_id=? WHERE task_id=?",
                       (attempt_ids[0], attempt_ids[1], task_id))
        with self.without_physical_io()[0]:
            self.assertEqual(macro_task_deletion.delete(board.store, run["macroTaskId"], 1)["state"], "deleted")
        with board.store.db.read() as db:
            for table in ("attempts", "workflow_turns", "attempt_process_identities",
                          "attempt_interruptions", "attempt_shutdown_evidence", "execution_issues",
                          "attempt_activity", "attempt_reminders", "messages", "artifacts",
                          "resource_claims"):
                with self.subTest(table=table):
                    self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table} WHERE " +
                                                ("run_id=?" if table == "workflow_turns" else
                                                 "task_id=?" if table in ("attempts", "attempt_activity",
                                                                           "attempt_reminders", "messages",
                                                                           "artifacts", "resource_claims") else
                                                 "attempt_id IN (?,?)"),
                                                (task_id,) if table in ("attempts", "workflow_turns",
                                                                        "attempt_activity", "attempt_reminders",
                                                                        "messages", "artifacts", "resource_claims")
                                                else attempt_ids).fetchone()[0], 0)
            self.assertIsNotNone(db.execute("SELECT 1 FROM workflow_runs WHERE run_id=?", (other["runId"],)).fetchone())
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_unsettled_states_and_dangling_attempt_pointers_refuse_before_mark(self):
        board = self.board()
        run = self.closed(board, "eligibility", macro_task="Eligibility")
        target = run["macroTaskId"]
        with board.store.db.read() as db:
            original_task = dict(db.execute("SELECT * FROM tasks WHERE task_id=?", (run["runId"],)).fetchone())
            original_run = dict(db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run["runId"],)).fetchone())
        cases = (
            ("queued", original_run["state"], "queued"),
            ("running", original_run["state"], "running"),
            ("cancelling", original_run["state"], "running"),
            ("reconciliation-needed", original_run["state"], "running"),
            (original_task["state"], "executing", "running"),
            (original_task["state"], "awaiting-host", "awaiting-host"),
            (original_task["state"], "waiting-helpers", "running"),
            (original_task["state"], "waiting-owned", "running"),
            (original_task["state"], "delivered", "review"),
        )
        for task_state, run_state, reason in cases:
            with self.subTest(task_state=task_state, run_state=run_state):
                with board.store.db.write() as db:
                    db.execute("UPDATE tasks SET state=? WHERE task_id=?", (task_state, run["runId"]))
                    db.execute("UPDATE workflow_runs SET state=? WHERE run_id=?", (run_state, run["runId"]))
                preview = macro_task_deletion.preview(board.store, target)
                self.assertIn(reason, {blocker["reasonCode"] for blocker in preview["blockers"]})
                error = self.assert_code("MACRO_TASK_NOT_SETTLED", macro_task_deletion.delete,
                                         board.store, target, 1)
                self.assertIn(reason, {blocker["reasonCode"] for blocker in error.details["blockers"]})
        with board.store.db.write() as db:
            db.execute("UPDATE tasks SET state=?,selected_attempt_id='missing-attempt' WHERE task_id=?",
                       (original_task["state"], run["runId"]))
            db.execute("UPDATE workflow_runs SET state=? WHERE run_id=?", (original_run["state"], run["runId"]))
        self.assertIn("stop-unconfirmed", {b["reasonCode"] for b in
                         macro_task_deletion.preview(board.store, target)["blockers"]})
        with board.store.db.write() as db:
            db.execute("UPDATE tasks SET selected_attempt_id=NULL,active_attempt_id='missing-attempt'"
                       " WHERE task_id=?", (run["runId"],))
        self.assertIn("stop-unconfirmed", {b["reasonCode"] for b in
                         macro_task_deletion.preview(board.store, target)["blockers"]})
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM macro_task_deletions").fetchone()[0], 0)

    def test_finished_but_unconfirmed_attempt_refuses_and_confirmed_attempt_allows(self):
        board = self.board()
        run = self.closed(board, "unconfirmed", macro_task="Unconfirmed")
        now = board.store.now()
        with board.store.db.write() as db:
            db.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,"
                       "adapter,shutdown_confirmed,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       ("finished-unconfirmed", run["runId"], 1, "fixture", "finished", "dsh", 0, now, now))
        preview = macro_task_deletion.preview(board.store, run["macroTaskId"])
        stop = next(b for b in preview["blockers"] if b["reasonCode"] == "stop-unconfirmed")
        self.assertEqual(stop["attemptIds"], ["finished-unconfirmed"])
        self.assert_code("MACRO_TASK_NOT_SETTLED", macro_task_deletion.delete,
                         board.store, run["macroTaskId"], 1)
        with board.store.db.write() as db:
            db.execute("UPDATE attempts SET shutdown_confirmed=1 WHERE attempt_id='finished-unconfirmed'")
        self.assertEqual(macro_task_deletion.preview(board.store, run["macroTaskId"])["blockerTotal"], 0)
        with self.without_physical_io()[0]:
            self.assertEqual(macro_task_deletion.delete(board.store, run["macroTaskId"], 1)["state"], "deleted")

    def test_open_request_pending_continuation_route_and_applying_cleanup_refuse(self):
        board = self.board()
        run = self.closed(board, "pending-links", macro_task="Pending links")
        now = board.store.now()
        target = run["macroTaskId"]
        with board.store.db.write() as db:
            db.execute("INSERT INTO workflow_requests(request_id,run_id,kind,summary,payload_json,state,"
                       "expected_revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       ("pending-request", run["runId"], "attention", "private", "{}", "open", 1, now, now))
        self.assertIn("awaiting-host", {b["reasonCode"] for b in macro_task_deletion.preview(board.store, target)["blockers"]})
        with board.store.db.write() as db:
            db.execute("UPDATE workflow_requests SET state='declined' WHERE request_id='pending-request'")
            db.execute("INSERT INTO workflow_continuations(continuation_id,run_id,kind,expected_revision,"
                       "input_text,input_bytes,state,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       ("pending-continuation", run["runId"], "manual", 1, "private", 7, "recorded", now))
        self.assertIn("queued", {b["reasonCode"] for b in macro_task_deletion.preview(board.store, target)["blockers"]})
        with board.store.db.write() as db:
            db.execute("UPDATE workflow_continuations SET state='queued' WHERE continuation_id='pending-continuation'")
        self.assertIn("queued", {b["reasonCode"] for b in macro_task_deletion.preview(board.store, target)["blockers"]})
        with board.store.db.write() as db:
            db.execute("UPDATE workflow_continuations SET state='cancelled' WHERE continuation_id='pending-continuation'")
            db.execute("INSERT INTO evaluation_decisions(decision_id,status,task,created_at) VALUES(?,?,?,?)",
                       ("pending-decision", "pending", "private", now))
            db.execute("INSERT INTO decision_requests(decision_id,request_id,kind,input_fingerprint,created_at,"
                       "updated_at) VALUES(?,?,?,?,?,?)", ("pending-decision", "pending-route", "select", "hash", now, now))
            db.execute("INSERT INTO workflow_routes(decision_id,run_id,owner_generation,state,created_at,updated_at)"
                       " VALUES(?,?,?,?,?,?)", ("pending-decision", run["runId"], 1, "pending", now, now))
        self.assertIn("routing", {b["reasonCode"] for b in macro_task_deletion.preview(board.store, target)["blockers"]})
        with board.store.db.write() as db:
            db.execute("UPDATE workflow_routes SET state='resolved' WHERE decision_id='pending-decision'")
            db.execute("INSERT INTO workspace_cleanup_plans(plan_id,run_id,workspace_id,checkout_id,path,kind,"
                       "state,evidence_json,retention_json,actor_role,actor_id,created_at,expires_at)"
                       " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("applying-plan", run["runId"], "workspace", "checkout", "private", "worktree",
                        "applying", "{}", "{}", "host", "fixture", now, now))
        self.assert_code("MACRO_TASK_BUSY", macro_task_deletion.preview, board.store, target)
        self.assert_code("MACRO_TASK_BUSY", macro_task_deletion.delete, board.store, target, 1)
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM macro_task_deletions").fetchone()[0], 0)
        with board.store.db.write() as db:
            db.execute("UPDATE workspace_cleanup_plans SET state='applied' WHERE plan_id='applying-plan'")
        with self.without_physical_io()[0]:
            self.assertEqual(macro_task_deletion.delete(board.store, target, 1)["state"], "deleted")
        with board.store.db.read() as db:
            for table in ("workflow_requests", "workflow_continuations", "workflow_routes",
                          "workspace_cleanup_plans", "decision_requests", "evaluation_decisions"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_complete_set_exceeds_list_limits_when_every_run_is_settled(self):
        board = self.board()
        first = self.closed(board, "large-set-0", macro_task="Large settled set")
        members = [first]
        count = max(macro_tasks.LIST_MAX_LIMIT,macro_tasks.TIMELINE_MAX_LIMIT,macro_tasks.SUMMARY_LIMIT) + 1
        for index in range(1, count):
            members.append(self.closed(board, f"large-set-{index}", macro_task_id=first["macroTaskId"]))
        other = self.closed(board, "large-set-other", macro_task="Retained large-set neighbor",
                            project="large-set-other")
        self.assertEqual(macro_task_deletion.preview(board.store, first["macroTaskId"])["microTaskCount"], count)
        with self.without_physical_io()[0]:
            self.assertEqual(macro_task_deletion.delete(board.store, first["macroTaskId"], count)["microTaskCount"], count)
        with board.store.db.read() as db:
            clause = ",".join("?" for _ in members)
            ids = tuple(run["runId"] for run in members)
            self.assertEqual(db.execute(f"SELECT COUNT(*) FROM workflow_runs WHERE run_id IN ({clause})", ids).fetchone()[0], 0)
            self.assertIsNotNone(db.execute("SELECT 1 FROM workflow_runs WHERE run_id=?", (other["runId"],)).fetchone())
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_shared_exact_workspace_artifact_checkout_and_integration_refuse(self):
        board = self.board()
        owner = self.closed(board, "shared-owner", macro_task="Shared owner")
        other = self.closed(board, "shared-other", macro_task="Shared neighbor", project="shared-other")
        target = owner["macroTaskId"]
        manifest = {"workspaceId": "shared-workspace", "repositoryId": "shared-repository",
                    "checkoutId": "shared-checkout", "manifestSha256": "shared-manifest-hash", "snapshot": {}}
        now = board.store.now()
        with board.store.db.write() as db:
            db.execute("UPDATE workflow_runs SET workspace_manifest_json=? WHERE run_id=?",
                       (json.dumps(manifest), owner["runId"]))
            db.execute("UPDATE workflow_runs SET workspace_manifest_json='bad unrelated json' WHERE run_id=?",
                       (other["runId"],))
        self.assertEqual(macro_task_deletion.preview(board.store, target)["blockerTotal"], 0)

        def shared_kind(expected):
            error = self.assert_code("MACRO_TASK_STORAGE_IN_USE", macro_task_deletion.preview,
                                     board.store, target)
            self.assertIn(expected, {obj["objectKind"] for obj in error.details["objects"]})
            self.assert_code("MACRO_TASK_STORAGE_IN_USE", macro_task_deletion.delete,
                             board.store, target, 1)
            with board.store.db.read() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM macro_task_deletions").fetchone()[0], 0)

        with board.store.db.write() as db:
            db.execute("UPDATE workflow_runs SET workspace_manifest_json=? WHERE run_id=?",
                       (json.dumps({**manifest, "workspaceId": "other-workspace"}), other["runId"]))
        shared_kind("workspace")  # Same repository and checkout, different workspace ID.
        with board.store.db.write() as db:
            db.execute("UPDATE workflow_runs SET workspace_manifest_json='bad unrelated json' WHERE run_id=?",
                       (other["runId"],))
            db.execute("INSERT INTO workflow_artifacts(artifact_id,run_id,kind,manifest_json,manifest_sha256,"
                       "created_at) VALUES(?,?,?,?,?,?)",
                       ("other-shared-artifact", other["runId"], "input", json.dumps(manifest), "fixture", now))
        shared_kind("workspace")
        with board.store.db.write() as db:
            db.execute("DELETE FROM workflow_artifacts WHERE artifact_id='other-shared-artifact'")
            db.execute("INSERT INTO workspace_reservations(reservation_id,workspace_id,checkout_id,repository_id,"
                       "path,access,holder_task_id,holder_kind,manifest_sha256,state,created_at,updated_at)"
                       " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("other-shared-reservation", "other-workspace", "shared-checkout", "shared-repository",
                        "private", "read", other["runId"], "reader", "fixture", "released", now, now))
        shared_kind("checkout")
        with board.store.db.write() as db:
            db.execute("DELETE FROM workspace_reservations WHERE reservation_id='other-shared-reservation'")
            db.execute("INSERT INTO workflow_integrations(integration_id,run_id,artifact_id,state,strategy,"
                       "binding_sha256,target_kind,target_repository_id,target_checkout_id,verification_json,"
                       "actor_role,actor_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("other-shared-integration", other["runId"], "other-artifact", "verified", "fixture",
                        "fixture", "checkout", "shared-repository", "shared-checkout", "{}", "host", "fixture", now))
        shared_kind("integration-target")
        with board.store.db.write() as db:
            db.execute("DELETE FROM workflow_integrations WHERE integration_id='other-shared-integration'")
            db.execute("UPDATE workflow_runs SET workspace_manifest_json=? WHERE run_id=?",
                       (json.dumps({**manifest, "workspaceId": "other-workspace",
                                    "repositoryId": "other-repository",
                                    "manifestSha256": "other-manifest-hash"}), other["runId"]))
        self.assertEqual(macro_task_deletion.preview(board.store, target)["blockerTotal"], 0)

    def test_native_session_identity_is_deduplicated_and_failure_keeps_mark(self):
        board = self.board()
        run = self.closed(board, "native-owner", macro_task="Native owner")
        target = run["macroTaskId"]
        session_id = "11111111-1111-4111-8111-111111111111"
        now = board.store.now()
        with board.store.db.write() as db:
            for generation, mode in ((1, "initial"), (2, "native-session")):
                attempt_id = f"native-attempt-{generation}"
                receipt = {"result": {"nativeSession": {"harness": "codex", "sessionId": session_id,
                               "captured": True, "storageOwner": "harness-user-store", "resumeMode": mode},
                            "nativeCheckpoint": {"version": 1, "sessionId": session_id,
                               "taskId": run["runId"], "attemptId": attempt_id}}}
                db.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,"
                           "adapter,shutdown_confirmed,result_json,created_at,updated_at)"
                           " VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (attempt_id, run["runId"], generation, "fixture", "finished", "codex", 1,
                            json.dumps(receipt), now, now))
        calls = []
        def delete_native(ids):
            calls.append(ids)
            if len(calls) == 1:
                raise OSError("offline adapter interruption")
            return {session_id: "already-gone"}
        with ExitStack() as stack:
            stack.enter_context(patch.object(harness_registry, "adapter",
                                             side_effect=lambda harness: SimpleNamespace(delete_sessions=delete_native)))
            for stage in PHYSICAL_STAGES[1:]:
                stack.enter_context(patch.object(macro_task_deletion, stage, return_value=[]))
            interrupted = macro_task_deletion.delete(board.store, target, 1)
            self.assertEqual((interrupted["state"], interrupted["reasonCode"], interrupted["objectKind"]),
                             ("deleting", "IO_ERROR", "native-session"))
            with board.store.db.read() as db:
                self.assertIsNotNone(db.execute("SELECT 1 FROM attempts WHERE attempt_id='native-attempt-1'").fetchone())
            completed = macro_task_deletion.delete(board.store, target, 1)
        self.assertEqual(completed["state"], "deleted")
        self.assertEqual(completed["deletionId"], interrupted["deletionId"])
        self.assertEqual(calls, [[session_id], [session_id]])
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='macro_task.deleted'").fetchone()[0], 1)

    def test_native_session_reused_by_other_run_refuses_deletion(self):
        board = self.board()
        owner = self.closed(board, "native-shared-owner", macro_task="Native shared owner")
        other = self.closed(board, "native-shared-other", macro_task="Native shared neighbor",
                            project="native-shared-other")
        session_id = "22222222-2222-4222-8222-222222222222"
        now = board.store.now()
        with board.store.db.write() as db:
            for run, mode, index in ((owner, "initial", 1), (other, "native-session", 2)):
                attempt_id = f"native-shared-attempt-{index}"
                receipt = {"result": {"nativeSession": {"harness": "codex", "sessionId": session_id,
                               "captured": True, "storageOwner": "harness-user-store", "resumeMode": mode},
                            "nativeCheckpoint": {"version": 1, "sessionId": session_id,
                               "taskId": run["runId"], "attemptId": attempt_id}}}
                db.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,"
                           "adapter,shutdown_confirmed,result_json,created_at,updated_at)"
                           " VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (attempt_id, run["runId"], 1, "fixture", "finished", "codex", 1,
                            json.dumps(receipt), now, now))
        error = self.assert_code("MACRO_TASK_STORAGE_IN_USE", macro_task_deletion.preview,
                                 board.store, owner["macroTaskId"])
        self.assertIn({"objectKind": "native-session", "holderRunIds": [other["runId"]]},
                      error.details["objects"])
        self.assert_code("MACRO_TASK_STORAGE_IN_USE", macro_task_deletion.delete,
                         board.store, owner["macroTaskId"], 1)
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM macro_task_deletions").fetchone()[0], 0)

    def test_remaining_owned_fk_rows_and_quota_link_are_removed_atomically(self):
        board = self.board()
        run = self.closed(board, "owned-facts", macro_task="Owned facts")
        other = self.closed(board, "other-facts", macro_task="Other facts", project="other-facts")
        now = board.store.now()
        with board.store.db.write() as db:
            db.execute("INSERT INTO workflow_scope_versions(run_id,scope_version,actor_role,actor_id,"
                       "write_scope_json,stopped_evidence_json,created_at) VALUES(?,?,?,?,?,?,?)",
                       (run["runId"], 900, "host", "fixture", "{}", "{}", now))
            db.execute("INSERT INTO workflow_workspace_conflicts(conflict_id,run_id,attempt_id,"
                       "manifest_sha256,observed_fingerprint,blocking_paths_json,evidence_json,state,"
                       "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       ("owned-conflict", run["runId"], "old-attempt", "manifest", "fingerprint",
                        "[]", "{}", "abandoned", now, now))
            db.execute("INSERT INTO workflow_artifacts(artifact_id,run_id,kind,manifest_json,"
                       "manifest_sha256,created_at) VALUES(?,?,?,?,?,?)",
                       ("owned-workflow-artifact", run["runId"], "output", "{}", "owned-hash", now))
            db.execute("INSERT INTO workflow_integrations(integration_id,run_id,artifact_id,state,"
                       "strategy,binding_sha256,target_kind,verification_json,actor_role,actor_id,created_at)"
                       " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       ("owned-integration", run["runId"], "owned-workflow-artifact", "not-required",
                        "fixture", "owned-binding", "none", "{}", "host", "fixture", now))
            db.execute("INSERT INTO workflow_suggestions(suggestion_id,run_id,author,body,created_at)"
                       " VALUES(?,?,?,?,?)", ("owned-suggestion", run["runId"], "fixture", "private", now))
            db.execute("INSERT INTO workspace_reservations(reservation_id,workspace_id,checkout_id,repository_id,"
                       "path,access,holder_task_id,holder_kind,parent_run_id,manifest_sha256,state,created_at,updated_at)"
                       " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("owned-reservation", "owned-workspace", "owned-checkout", "owned-repository",
                        "private", "read", run["runId"], "parent", run["runId"], "fixture", "released", now, now))
            db.execute("INSERT INTO agent_credentials(credential_id,run_id,task_id,token_verifier,state,"
                       "created_at) VALUES(?,?,?,?,?,?)",
                       ("owned-credential", run["runId"], run["runId"], "private", "revoked", now))
            db.execute("INSERT INTO evaluation_decisions(decision_id,status,task,created_at) VALUES(?,?,?,?)",
                       ("owned-decision", "completed", "private", now))
            db.execute("INSERT INTO decision_requests(decision_id,request_id,kind,input_fingerprint,"
                       "created_at,updated_at) VALUES(?,?,?,?,?,?)",
                       ("owned-decision", "owned-decision-request", "select", "hash", now, now))
            db.execute("INSERT INTO workflow_routes(decision_id,run_id,owner_generation,state,created_at,"
                       "updated_at) VALUES(?,?,?,?,?,?)",
                       ("owned-decision", run["runId"], 1, "resolved", now, now))
            db.execute("INSERT INTO quota_current(harness,provider,limit_id,blocked,source,observed_at,"
                       "last_observed_at,retry_consumed_at,retry_consumed_by) VALUES(?,?,?,?,?,?,?,?,?)",
                       ("codex", "fixture", "owned-limit", 0, "fixture", now, now, now,
                        "owned-decision"))
            db.execute("INSERT INTO commands(command_id,task_id,kind,request_hash,response_json,subject,"
                       "created_at) VALUES(?,?,?,?,?,?,?)",
                       ("owned-content-command", run["runId"], "workflow.fixture", "hash",
                        json.dumps({"runId": run["runId"], "private": "content"}), "host-1", now))
            db.execute("INSERT INTO commands(command_id,kind,request_hash,response_json,subject,created_at)"
                       " VALUES(?,?,?,?,?,?)", ("owned-stop-command", "macro_task.stop", "hash",
                       json.dumps({"macroTaskId": run["macroTaskId"], "private": "content"}), "host-1", now))
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertGreater(db.execute("SELECT COUNT(*) FROM workflow_host_conclusions WHERE run_id=?",
                                                (run["runId"],)).fetchone()[0], 0)
        with self.without_physical_io()[0]:
            result = macro_task_deletion.delete(board.store, run["macroTaskId"], 1)
        self.assertEqual(result["state"], "deleted")
        with board.store.db.read() as db:
            for table, column, value in (
                ("workflow_scope_versions", "run_id", run["runId"]),
                ("workflow_workspace_conflicts", "conflict_id", "owned-conflict"),
                ("workflow_artifacts", "artifact_id", "owned-workflow-artifact"),
                ("workflow_integrations", "integration_id", "owned-integration"),
                ("workflow_suggestions", "suggestion_id", "owned-suggestion"),
                ("workspace_reservations", "reservation_id", "owned-reservation"),
                ("agent_credentials", "credential_id", "owned-credential"),
                ("workflow_host_conclusions", "run_id", run["runId"]),
                ("workflow_routes", "decision_id", "owned-decision"),
                ("decision_requests", "decision_id", "owned-decision"),
                ("evaluation_decisions", "decision_id", "owned-decision"),
            ):
                with self.subTest(table=table):
                    self.assertIsNone(db.execute(f"SELECT 1 FROM {table} WHERE {column}=?", (value,)).fetchone())
            command = db.execute("SELECT task_id,attempt_id,response_json,deletion_id FROM commands"
                                 " WHERE command_id='owned-content-command'").fetchone()
            self.assertEqual(tuple(command)[:3], (None, None, "{}"))
            self.assertEqual(command["deletion_id"], result["deletionId"])
            stop = db.execute("SELECT response_json,deletion_id FROM commands"
                              " WHERE command_id='owned-stop-command'").fetchone()
            self.assertEqual(tuple(stop), ("{}", result["deletionId"]))
            quota = db.execute("SELECT retry_consumed_at,retry_consumed_by FROM quota_current"
                               " WHERE limit_id='owned-limit'").fetchone()
            self.assertEqual(tuple(quota), (now, None))
            self.assertIsNotNone(db.execute("SELECT 1 FROM workflow_runs WHERE run_id=?",
                                            (other["runId"],)).fetchone())
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])


    def test_marker_obeys_allocation_lock_and_reports_applying_cleanup_as_busy(self):
        from hey_my_buddy.blackboard.tasks import workspace
        board = self.board()
        run = self.closed(board, 'a' * 32, macro_task='Allocation admission')
        directory = board.directory / 'workspaces' / ('ws-' + 'a' * 32)
        directory.mkdir(parents=True)
        (directory / '.lock').touch()
        with workspace._lock(directory):
            self.assert_code('WORKSPACE_BUSY',macro_task_deletion.delete,board.store,run['macroTaskId'],1)
            now = board.store.now()
            with board.store.db.write() as db:
                db.execute('INSERT INTO workspace_cleanup_plans(plan_id,run_id,workspace_id,checkout_id,path,kind,'
                           'state,evidence_json,retention_json,actor_role,actor_id,created_at,expires_at)'
                           ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           ('held-plan',run['runId'],'ws-'+'a'*32,'checkout',str(directory/'checkout'),'worktree',
                            'applying','{}','{}','host','fixture',now,now))
            self.assert_code('MACRO_TASK_BUSY',macro_task_deletion.delete,board.store,run['macroTaskId'],1)
        with board.store.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM macro_task_deletions').fetchone()[0],0)
