"""Accepted micro tasks can be corrected without changing their execution input."""
from __future__ import annotations

import json

from blackboard.tasks import test_lead_owned as lead_owned
from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.cli.cli_views import micro_task_brief, task_brief, list_brief
from hey_my_buddy.errors import BoardError


class MicroTaskFixTests(WorkflowTestCase):
    def accepted_host_run(self, board, name: str, *, worker="w1", **extra):
        run = self.submit(board, request_id=name, cwd=str(self.workdir(name)), **extra)
        self.register(board, worker)
        claim = self.claim(board, worker, claim_request_id="claim-" + name, run_id=run["runId"])
        self.finish_turn(board, claim, worker_id=worker)
        return self.accept_run(board, board.call("workflow_get", {"runId": run["runId"]}))

    def accepted_lead_run(self, board, lead_claim, name: str):
        run = lead_owned.LeadOwnedTests.owned(self, board, lead_claim, name)
        delivered, _ = lead_owned.LeadOwnedTests.finish_owned(self, board, run, worker="w2")
        return self.accept_run(board, delivered, credential=lead_claim["agentCredential"])

    def counts(self, board):
        with board.store.db.read() as db:
            return tuple(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                         for table in ("tasks", "workflow_runs", "workflow_fixes", "events"))

    def assert_rejected_without_stock(self, board, code: str, **params):
        before = self.counts(board)
        with self.assertRaises(BoardError) as caught:
            self.submit(board, **params)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.counts(board), before)

    def test_host_correction_chain_reads_status_list_and_timeline(self):
        board = self.board()
        original = self.accepted_host_run(board, "original", macroTask={"title": "Corrections"})
        for view in (board.call("workflow_get", {"runId": original["runId"]}),
                     board.call("task_get", {"runId": original["runId"]})["task"]):
            self.assertNotIn("fixes", view)
            self.assertNotIn("fixedBy", view)
        self.assertNotIn("fixes", micro_task_brief(board.call("workflow_get", {"runId": original["runId"]})))
        self.assertNotIn("fixedBy", task_brief(board.call("task_get", {"runId": original["runId"]})["task"]))
        first = self.submit(board, request_id="fix-one", task="Correct the first issue",
                            cwd=str(self.workdir("fix-one")), fixes=original["runId"])
        second = self.submit(board, request_id="fix-two", task="Correct the second issue",
                             cwd=str(self.workdir("fix-two")), fixes=original["runId"])
        self.assertEqual(first["macroTaskId"], original["macroTaskId"])
        self.assertEqual(second["macroTaskId"], original["macroTaskId"])
        with board.store.db.write() as db:
            db.execute("UPDATE workflow_fixes SET created_at=? WHERE run_id IN (?,?)",
                       ("2026-01-01T00:00:00Z", first["runId"], second["runId"]))
        self.assertEqual(first["fixes"], original["runId"])
        self.assertEqual(second["fixes"], original["runId"])
        self.assertEqual(board.call("workflow_get", {"runId": original["runId"]})["fixedBy"],
                         [first["runId"], second["runId"]])
        self.assertEqual(board.call("workflow_get", {"runId": original["runId"]})["state"], "accepted")
        self.assertEqual(board.call("workflow_get", {"runId": first["runId"]})["fixes"], original["runId"])
        self.assertEqual(micro_task_brief(board.call("workflow_get", {"runId": original["runId"]}))["fixedBy"],
                         [first["runId"], second["runId"]])
        self.assertEqual(micro_task_brief(board.call("workflow_get", {"runId": second["runId"]}))["fixes"],
                         original["runId"])
        original_task = board.call("task_get", {"runId": original["runId"]})["task"]
        first_task = board.call("task_get", {"runId": first["runId"]})["task"]
        self.assertEqual(original_task["fixedBy"], [first["runId"], second["runId"]])
        self.assertEqual(first_task["fixes"], original["runId"])
        self.assertEqual(task_brief(original_task)["fixedBy"], [first["runId"], second["runId"]])
        self.assertEqual(task_brief(first_task)["fixes"], original["runId"])
        listed = board.call("task_list", {})
        self.assertTrue(all("fixes" not in row and "fixedBy" not in row for row in listed["tasks"]))
        self.assertTrue(all("fixes" not in row and "fixedBy" not in row for row in list_brief(listed)["runs"]))
        timeline = board.call("macro_task_timeline", {"macroTaskId": original["macroTaskId"]})
        rows = {row["runId"]: row for row in timeline["rows"]}
        self.assertEqual(rows[original["runId"]]["fixedBy"], [first["runId"], second["runId"]])
        self.assertEqual(rows[first["runId"]]["fixes"], original["runId"])
        self.assertEqual(rows[second["runId"]]["fixes"], original["runId"])
        with board.store.db.read() as db:
            self.assertEqual([(row["run_id"], row["fixed_run_id"]) for row in db.execute(
                "SELECT run_id,fixed_run_id FROM workflow_fixes ORDER BY created_at,rowid")],
                [(first["runId"], original["runId"]), (second["runId"], original["runId"])])
            self.assertNotIn("fixes", json.loads(db.execute(
                "SELECT micro_task_json FROM workflow_runs WHERE run_id=?", (first["runId"],)).fetchone()[0]))
            self.assertNotIn("fixes", json.loads(db.execute(
                "SELECT spec_json FROM tasks WHERE task_id=?", (first["runId"],)).fetchone()[0]))

    def test_an_accepted_fix_can_itself_be_fixed_without_a_macro_task(self):
        board = self.board()
        original = self.accepted_host_run(board, "original")
        self.assertNotIn("fixes", original)
        self.assertNotIn("fixedBy", original)
        first = self.submit(board, request_id="first", cwd=str(self.workdir("first")), fixes=original["runId"])
        self.register(board)
        self.finish_turn(board, self.claim(board, claim_request_id="claim-first", run_id=first["runId"]))
        accepted = self.accept_run(board, board.call("workflow_get", {"runId": first["runId"]}))
        second = self.submit(board, request_id="second", cwd=str(self.workdir("second")), fixes=accepted["runId"])
        self.assertIsNone(second["macroTaskId"])
        self.assertEqual(board.call("workflow_get", {"runId": second["runId"]})["fixes"], first["runId"])
        self.assertEqual(board.call("workflow_get", {"runId": first["runId"]})["fixedBy"], [second["runId"]])
        self.assertEqual(board.call("workflow_get", {"runId": original["runId"]})["fixedBy"], [first["runId"]])

    def test_host_rejections_and_replay_are_atomic(self):
        board = self.board()
        original = self.accepted_host_run(board, "original", macroTask={"title": "Original group"})
        other = self.accepted_host_run(board, "other", macroTask={"title": "Other group"})
        pending = self.submit(board, request_id="pending", cwd=str(self.workdir("pending")))
        invalid = ((None, "INVALID_ARGUMENT"), ([], "INVALID_ARGUMENT"), ("", "INVALID_ARGUMENT"),
                   ("unknown-run", "NOT_FOUND"), (pending["runId"], "INVALID_STATE"))
        for index, (target, code) in enumerate(invalid):
            with self.subTest(target=target):
                self.assert_rejected_without_stock(board, code, request_id=f"invalid-{index}",
                                                   cwd=str(self.workdir(f"invalid-{index}")), fixes=target)
        self.assert_rejected_without_stock(board, "FORBIDDEN", request_id="foreign",
                                           host_id="another-host", cwd=str(self.workdir("foreign")),
                                           fixes=original["runId"])
        self.assert_rejected_without_stock(board, "CONFLICT", request_id="wrong-group",
                                           cwd=str(self.workdir("wrong-group")), fixes=original["runId"],
                                           macroTaskId=other["macroTaskId"])
        first = self.submit(board, request_id="fix", cwd=str(self.workdir("fix")), fixes=original["runId"])
        replay = self.submit(board, request_id="fix", cwd=str(self.workdir("fix")), fixes=original["runId"])
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["runId"], first["runId"])
        self.assert_rejected_without_stock(board, "CONFLICT", request_id="fix",
                                           cwd=str(self.workdir("fix")), fixes=other["runId"])
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_fixes WHERE run_id=?",
                                        (first["runId"],)).fetchone()[0], 1)

    def test_explicit_matching_macro_selectors_and_different_group_rejection(self):
        board = self.board()
        target = self.accepted_host_run(board, "target", macroTask={"title": "Target group"})
        different = self.accepted_host_run(board, "different", macroTask={"title": "Different group"})
        by_id = self.submit(board, request_id="by-id", cwd=str(self.workdir("by-id")),
                            fixes=target["runId"], macroTaskId=target["macroTaskId"])
        by_run = self.submit(board, request_id="by-run", cwd=str(self.workdir("by-run")),
                             fixes=target["runId"], macroTaskOf=target["runId"])
        self.assertEqual((by_id["macroTaskId"], by_run["macroTaskId"]),
                         (target["macroTaskId"], target["macroTaskId"]))
        self.assert_rejected_without_stock(board, "CONFLICT", request_id="wrong-run",
                                           cwd=str(self.workdir("wrong-run")), fixes=target["runId"],
                                           macroTaskOf=different["runId"])
        self.assert_rejected_without_stock(board, "CONFLICT", request_id="new-group",
                                           cwd=str(self.workdir("new-group")), fixes=target["runId"],
                                           macroTask={"title": "New group"})

    def test_lead_fix_requires_its_own_accepted_target_and_uses_its_macro_task(self):
        board = self.board(max_concurrent=4)
        lead, claim = lead_owned.LeadOwnedTests.lead(self, board, macroTask={"title": "Lead work"})
        target = self.accepted_lead_run(board, claim, "owned-target")
        fixed = lead_owned.LeadOwnedTests.owned(self, board, claim, "owned-fix", fixes=target["runId"])
        self.assertEqual(fixed["fixes"], target["runId"])
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT macro_task_id FROM workflow_runs WHERE run_id=?",
                                        (fixed["runId"],)).fetchone()[0], lead["macroTaskId"])
        self.assertEqual(board.call("workflow_get", {"runId": target["runId"]})["fixedBy"], [fixed["runId"]])
        self.assert_rejected_without_stock(board, "FORBIDDEN", request_id="host-fixes-lead",
                                           cwd=str(self.workdir("host-fixes-lead")), fixes=target["runId"])
        host = self.accepted_host_run(board, "host-target", worker="w4")
        _, other_claim = lead_owned.LeadOwnedTests.lead(self, board, "other-lead", "w3")
        for index, (credential, target_id) in enumerate(((claim, host["runId"]),
                                                         (other_claim, target["runId"]))):
            with self.subTest(target=target_id):
                before = self.counts(board)
                with self.assertRaises(BoardError) as caught:
                    lead_owned.LeadOwnedTests.owned(self, board, credential, f"foreign-{index}", fixes=target_id)
                self.assertEqual(caught.exception.code, "FORBIDDEN")
                self.assertEqual(self.counts(board), before)
        for field, value in (("macroTask", {"title": "Forgery"}),
                             ("macroTaskId", lead["macroTaskId"]), ("macroTaskOf", target["runId"])):
            with self.subTest(field=field):
                before = self.counts(board)
                with self.assertRaises(BoardError) as caught:
                    lead_owned.LeadOwnedTests.owned(self, board, claim, "forged-" + field,
                                        fixes=target["runId"], **{field: value})
                self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
                self.assertEqual(self.counts(board), before)
