"""Queue reads project the current holder from the admission claims."""
from blackboard.tasks import test_lead_owned as lead_fixtures
from blackboard.tasks.test_workflow import WorkflowTestCase


class BlockingTaskTests(WorkflowTestCase):
    lead = lead_fixtures.LeadOwnedTests.lead
    owned = lead_fixtures.LeadOwnedTests.owned

    def assert_projection(self, board, queued, expected, reason="cwd-overlap"):
        params = {"runId": queued["runId"]}
        workflow = board.call("workflow_get", params)
        task = board.call("task_get", params)["task"]
        for view in (workflow, workflow["task"], task):
            self.assertEqual(view["queueReason"], reason)
            self.assertEqual(view["blockingTask"], expected)

    def test_workflow_holder_is_read_from_held_and_retained_claims(self):
        board = self.board(max_concurrent=4)
        parent = self.workdir("parent")
        holder = self.submit(board, cwd=str(parent), title="Read parent", access="read",
                             create_macro={"title": "Parent work", "goal": "Update parent"})
        self.assertIsNotNone(self.claim(board, run_id=holder["runId"])["claim"])
        queued = self.submit(board, cwd=str(parent), request_id="child", access="read")
        self.assertEqual(self.claim(board, run_id=queued["runId"])["reason"], "cwd-overlap")
        expected = {"runId": holder["runId"], "title": "Read parent", "macroTaskId": holder["macroTaskId"]}
        self.assert_projection(board, queued, expected)
        self.assertIsNone(board.call("workflow_get", {"runId": holder["runId"]})["blockingTask"])

        with board.store.db.write() as connection:
            connection.execute("UPDATE resource_claims SET state='retained' WHERE task_id=?", (holder["runId"],))
        self.assert_projection(board, queued, expected)

        with board.store.db.write() as connection:
            connection.execute("UPDATE resource_claims SET state='released' WHERE task_id=?", (holder["runId"],))
            connection.execute(
                "INSERT INTO resource_claims(claim_id,task_id,resource,kind,state,created_at) VALUES(?,?,?,?,?,?)",
                ("self-claim", queued["runId"], str(parent), "cwd", "retained", board.store.now()),
            )
        self.assert_projection(board, queued, None)

    def test_plain_execution_holder_is_only_exposed_for_cwd_overlap(self):
        board = self.board(max_concurrent=2)
        parent = self.workdir("claimed")
        child = parent / "child"
        child.mkdir()
        holder = board.submit_execution(requestId="command", task="Hold the directory", cwd=str(parent),
                                        adapter="command", argv=["fixture-never-run"])["task"]
        self.assertIsNotNone(self.claim(board)["claim"])
        queued = self.submit(board, cwd=str(child), request_id="waiting")
        self.assert_projection(board, queued, {"runId": holder["runId"], "title": None, "macroTaskId": None})

        with board.store.db.write() as connection:
            connection.execute("UPDATE tasks SET queue_reason='capacity' WHERE task_id=?", (queued["runId"],))
        self.assert_projection(board, queued, None, reason="capacity")

    def test_standalone_workflow_holder_projects_its_synthetic_group_id(self):
        board = self.board(max_concurrent=4)
        parent = self.workdir("standalone-holder")
        holder = self.submit(board, cwd=str(parent), title="Standalone work", access="read")
        self.assertIsNone(holder["macroTaskId"])
        self.assertIsNotNone(self.claim(board, run_id=holder["runId"])["claim"])
        queued = self.submit(board, cwd=str(parent), request_id="waiting-standalone", access="read",
                             create_macro={"title": "Other macro", "goal": "Wait on standalone"})
        self.assertEqual(self.claim(board, run_id=queued["runId"])["reason"], "cwd-overlap")
        self.assert_projection(board, queued, {
            "runId": holder["runId"], "title": "Standalone work",
            "macroTaskId": f"run:{holder['runId']}",
        })

    def test_owned_task_read_keeps_the_other_runs_details_out_of_agent_scope(self):
        board = self.board(max_concurrent=4)
        _, claim = self.lead(board)
        cwd = str(self.workdir("unrelated"))
        holder = board.submit_execution(requestId="unrelated", task="Hold another directory", cwd=cwd,
                                        adapter="command", argv=["fixture-never-run"])["task"]
        self.assertIsNotNone(self.claim(board, run_id=holder["runId"])["claim"])
        queued = self.owned(board, claim, cwd=cwd, checkout="existing", access="read")
        self.assertEqual(self.claim(board, run_id=queued["runId"])["reason"], "cwd-overlap")
        self.assert_projection(board, queued, {"runId": holder["runId"], "title": None, "macroTaskId": None})

        params = {"runId": queued["runId"], "credential": claim["agentCredential"]}
        workflow = board.call("workflow_get", params)
        task = board.call("task_get", params)["task"]
        for view in (workflow, workflow["task"], task):
            self.assertEqual(view["queueReason"], "cwd-overlap")
            self.assertIsNone(view["blockingTask"])
