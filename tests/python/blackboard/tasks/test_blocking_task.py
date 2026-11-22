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
                                        adapter="command", argv=["fixture-never-run"], expectedSeconds=1800)["task"]
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
                                        adapter="command", argv=["fixture-never-run"], expectedSeconds=1800)["task"]
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

    def test_router_dispatch_holder_projects_owning_micro_task(self):
        board = self.board(max_concurrent=4)
        parent = self.workdir("routed-holder")
        owner = self.submit(board, cwd=str(parent), title="Owning micro task", access="read",
                            create_macro={"title": "Routed macro", "goal": "Verify router claim owner"})
        router_task = board.submit_execution(requestId="router-internal", task="Route", cwd=str(parent),
                                             adapter="command", argv=["fixture-never-run"],
                                             expectedSeconds=1800)["task"]
        self.assertIsNotNone(self.claim(board, run_id=router_task["runId"])["claim"])
        now = board.store.now()
        with board.store.db.write() as connection:
            connection.execute(
                "INSERT INTO evaluation_decisions(decision_id,status,created_at) VALUES(?,?,?)",
                ("dec-router-1", "pending", now),
            )
            connection.execute(
                "INSERT INTO decision_requests(decision_id,request_id,input_fingerprint,updated_at) "
                "VALUES(?,?,?,?)",
                ("dec-router-1", "req-router-1", "fp-router-1", now),
            )
            connection.execute(
                "INSERT INTO workflow_routes(decision_id,run_id,state,exploration,created_at) "
                "VALUES(?,?,?,?,?)",
                ("dec-router-1", owner["runId"], "pending", "ineligible", now),
            )
            connection.execute(
                "INSERT INTO router_requests(decision_id,round,snapshot_json,frozen_at) VALUES(?,?,?,?)",
                ("dec-router-1", 1, "{}", now),
            )
            connection.execute(
                "INSERT INTO router_dispatches(task_id,decision_id,round,router_index,buddy_id,"
                "harness,provider,model,effort,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (router_task["runId"], "dec-router-1", 1, 0, "router-1",
                 "codex", "openai", "gpt-5", "high", now),
            )
        queued = self.submit(board, cwd=str(parent), request_id="waiting-on-router", access="read")
        self.assertEqual(self.claim(board, run_id=queued["runId"])["reason"], "cwd-overlap")
        self.assert_projection(board, queued, {
            "runId": owner["runId"], "title": "Owning micro task", "macroTaskId": owner["macroTaskId"],
        })
