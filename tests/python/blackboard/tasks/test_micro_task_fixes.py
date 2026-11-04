"""A correction names one accepted micro task in the same project and group."""
from __future__ import annotations

from blackboard.tasks import test_lead_owned as lead_owned
from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.errors import BoardError


class MicroTaskFixTests(WorkflowTestCase):
    def submit(self, board, **params):
        if "fixes" in params:
            target = board.call("workflow_get", {"runId": params["fixes"]}) if params["fixes"] else None
            params.setdefault("macroTaskId", target["macroTaskId"] if target and target.get("macroTaskId") else "standalone")
            params.setdefault("kind", "worktree")
        return super().submit(board, **params)

    def project_workdir(self, name: str, *, project="fix-project"):
        root = self.workdir(project)
        (root / ".git").mkdir(exist_ok=True)
        return self.workdir(f"{project}/{name}")

    def accepted_host_run(self, board, name: str, *, worker="w1", **extra):
        run = self.submit(board, request_id=name, cwd=str(self.project_workdir(name)), **extra)
        self.register(board, worker)
        claim = self.claim(board, worker, claim_request_id="claim-" + name, run_id=run["runId"])
        self.finish_turn(board, claim, worker_id=worker)
        self.accept_run(board, board.call("workflow_get", {"runId": run["runId"]}))
        return board.call("workflow_get", {"runId": run["runId"]})

    def accepted_lead_run(self, board, lead_claim, name: str):
        run = lead_owned.LeadOwnedTests.owned(self, board, lead_claim, name)
        delivered, _ = lead_owned.LeadOwnedTests.finish_owned(self, board, run, worker="w2")
        self.accept_run(board, delivered, credential=lead_claim["agentCredential"])
        return board.call("workflow_get", {"runId": run["runId"]})

    def counts(self, board):
        with board.store.db.read() as connection:
            return tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                         for table in ("tasks", "workflow_runs", "workflow_fixes", "events", "macro_tasks"))

    def assert_rejected_without_stock(self, board, code: str, **params):
        before = self.counts(board)
        with self.assertRaises(BoardError) as raised:
            self.submit(board, **params)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(self.counts(board), before)

    def test_accepted_fix_is_readable_in_get_task_and_timeline(self):
        board = self.board()
        original = self.accepted_host_run(board, "original", create_macro={"title": "Corrections", "goal": "Corrections"})
        first = self.submit(board, request_id="fix-one", task="Correct first issue",
                            cwd=str(self.project_workdir("fix-one")), fixes=original["runId"])
        second = self.submit(board, request_id="fix-two", task="Correct second issue",
                             cwd=str(self.project_workdir("fix-two")), fixes=original["runId"])
        self.assertEqual((first["macroTaskId"], second["macroTaskId"]),
                         (original["macroTaskId"], original["macroTaskId"]))
        self.assertEqual(board.call("workflow_get", {"runId": original["runId"]})["fixedBy"],
                         [first["runId"], second["runId"]])
        self.assertEqual(board.call("workflow_get", {"runId": first["runId"]})["fixes"], original["runId"])
        self.assertEqual(board.call("task_get", {"runId": first["runId"]})["task"]["fixes"], original["runId"])
        timeline = board.call("macro_task_timeline", {"macroTaskId": original["macroTaskId"]})
        rows = {row["runId"]: row for row in timeline["rows"]}
        self.assertEqual(rows[original["runId"]]["fixedBy"], [first["runId"], second["runId"]])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_fixes").fetchone()[0], 2)

    def test_accepted_standalone_fix_can_itself_be_fixed(self):
        board = self.board()
        original = self.accepted_host_run(board, "original")
        first = self.submit(board, request_id="first", cwd=str(self.project_workdir("first")), fixes=original["runId"])
        self.finish_turn(board, self.claim(board, claim_request_id="claim-first", run_id=first["runId"]))
        self.accept_run(board, board.call("workflow_get", {"runId": first["runId"]}))
        second = self.submit(board, request_id="second", cwd=str(self.project_workdir("second")), fixes=first["runId"])
        self.assertIsNone(second["macroTaskId"])
        self.assertEqual(board.call("workflow_get", {"runId": first["runId"]})["fixedBy"], [second["runId"]])
        self.assertEqual(board.call("workflow_get", {"runId": original["runId"]})["fixedBy"], [first["runId"]])

    def test_fix_rejects_unaccepted_cross_project_and_wrong_group_without_stock(self):
        board = self.board()
        target = self.accepted_host_run(board, "target", create_macro={"title": "Target group", "goal": "Target group"})
        other = self.accepted_host_run(board, "other", create_macro={"title": "Other group", "goal": "Other group"})
        pending = self.submit(board, request_id="pending", cwd=str(self.project_workdir("pending")))
        self.assert_rejected_without_stock(board, "INVALID_STATE", request_id="pending-fix",
                                           cwd=str(self.project_workdir("pending-fix")), fixes=pending["runId"])
        self.assert_rejected_without_stock(board, "CONFLICT", request_id="cross-project",
                                           cwd=str(self.project_workdir("cross-project", project="another-project")),
                                           fixes=target["runId"])
        self.assert_rejected_without_stock(board, "INVALID_STATE", request_id="wrong-group",
                                           cwd=str(self.project_workdir("wrong-group")), fixes=target["runId"],
                                           macroTaskId=other["macroTaskId"])

    def test_same_creation_key_replays_original_fix_without_extra_relationship(self):
        board = self.board()
        original = self.accepted_host_run(board, "original")
        first = self.submit(board, request_id="fix", cwd=str(self.project_workdir("fix")), fixes=original["runId"])
        replay = self.submit(board, request_id="fix", cwd=str(self.project_workdir("fix")), fixes=original["runId"])
        self.assertEqual(replay["runId"], first["runId"])
        self.assertTrue(replay["alreadyExists"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_fixes WHERE run_id=?",
                                                (first["runId"],)).fetchone()[0], 1)

    def test_lead_may_fix_own_accepted_task_but_foreign_lead_is_denied(self):
        board = self.board(max_concurrent=4)
        lead, claim = lead_owned.LeadOwnedTests.lead(self, board, create_macro={"title": "Lead work", "goal": "Lead work"})
        target = self.accepted_lead_run(board, claim, "owned-target")
        fixed = lead_owned.LeadOwnedTests.owned(self, board, claim, "owned-fix", fixes=target["runId"])
        self.assertEqual(fixed["fixes"], target["runId"])
        self.assertEqual(fixed["macroTaskId"], lead["macroTaskId"])
        _other_lead, other_claim = lead_owned.LeadOwnedTests.lead(self, board, "other-lead", "w3")
        before = self.counts(board)
        with self.assertRaises(BoardError) as raised:
            lead_owned.LeadOwnedTests.owned(self, board, other_claim, "foreign-fix", fixes=target["runId"])
        self.assertEqual(raised.exception.code, "FORBIDDEN")
        self.assertEqual(self.counts(board), before)
