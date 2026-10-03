"""Root Host decisions drain stable queues and follow owned proxy chains."""
from hey_my_buddy.errors import BoardError
from blackboard.tasks.test_workflow import WorkflowTestCase
from blackboard.tasks.test_workflow_cancellation import private_environment


class WorkflowBoundaryTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        private_environment(self)

    def parent_with_helpers(self, count=1):
        board = self.board(max_concurrent=2)
        self.register(board, "w1")
        self.register(board, "w2")
        root = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = self.view(board, root["runId"])
        approved = self.decide(board, view, view["activeRequest"]["requestId"], helpers=[
            self.helper_spec(f"helper-{number}") for number in range(count)
        ])
        return board, root, [child["taskId"] for child in approved["children"]]

    def helper_spec(self, request_id):
        return {"requestId": request_id, "task": "bounded helper", "cwd": str(self.workdir()),
                "executionWorkspace": {"kind": "worktree", "access": "write"}}

    def view(self, board, run_id):
        return board.call("workflow_get", {"runId": run_id})

    def yield_helper(self, board, run_id, worker, command):
        claim = self.claim(board, worker, claim_request_id=command, run_id=run_id)
        self.finish_turn(board, claim, worker_id=worker, disposition="attention")
        return claim

    def answer(self, board, root_id, command, **extra):
        view = self.view(board, root_id)
        return self.decide(board, view, view["activeRequest"]["requestId"], command_id=command,
                           reason="Host supplied the requested direction", **extra)

    def test_two_attention_helpers_remain_visible_and_parent_continues_once(self):
        board, root, (first, second) = self.parent_with_helpers(2)
        self.yield_helper(board, first, "w1", "first-yield")
        first_boundary = self.view(board, root["runId"])["activeRequest"]["requestId"]
        self.yield_helper(board, second, "w2", "second-yield")
        waiting = self.view(board, root["runId"])
        self.assertEqual(waiting["activeRequest"]["requestId"], first_boundary)
        self.assertEqual(waiting["counts"]["openRequests"], 2)
        self.assertEqual(len(waiting["pendingRequests"]), 2)
        answered = self.answer(board, root["runId"], "answer-first")
        self.assertEqual(answered["activeRequest"]["childTaskId"], second)
        self.assertEqual(answered["state"], "awaiting-host")
        self.finish_turn(board, self.claim(board, "w1", claim_request_id="finish-first", run_id=first))
        waiting = self.view(board, root["runId"])
        self.assertEqual(waiting["activeRequest"]["childTaskId"], second)
        self.assertTrue(waiting["awaitingHost"])
        self.assertIsNone(self.claim(board, "w1", claim_request_id="root-too-early", run_id=root["runId"])["claim"])
        answered = self.answer(board, root["runId"], "answer-second")
        self.assertEqual(answered["state"], "waiting-helpers")
        self.assertEqual(answered["counts"]["openRequests"], 0)
        final_helper = self.claim(board, "w2", claim_request_id="finish-second", run_id=second)
        self.finish_turn(board, final_helper, worker_id="w2")
        ready = self.view(board, root["runId"])
        self.assertEqual(ready["state"], "executing")
        self.assertEqual(ready["continuationCount"], 1)
        root_claim = self.claim(board, "w1", claim_request_id="root-resumed", run_id=root["runId"])
        outcomes = root_claim["claim"]["turn"]["input"]["context"]["helperOutcomes"]
        self.assertEqual({outcome["taskId"] for outcome in outcomes}, {first, second})
        self.assertTrue(all(outcome["artifact"]["outputCommit"] for outcome in outcomes))
        self.finish_turn(board, root_claim)
        with board.store.db.read() as connection:
            count = connection.execute("SELECT COUNT(*) FROM events WHERE task_id=? AND kind='workflow.auto_continued'", (root["runId"],)).fetchone()[0]
            self.assertEqual(count, 1)

    def test_nested_attention_uses_root_control_to_resume_only_the_leaf(self):
        board, root, (middle,) = self.parent_with_helpers()
        self.yield_helper(board, middle, "w1", "middle-yield")
        self.answer(board, root["runId"], "grant-dependency", helpers=[self.helper_spec("leaf")])
        leaf = self.view(board, middle)["children"][0]["taskId"]
        self.yield_helper(board, leaf, "w2", "leaf-yield")
        boundary = self.view(board, root["runId"])
        self.assertEqual(boundary["state"], "awaiting-host")
        self.assertEqual(boundary["activeRequest"]["origin"]["runId"], leaf)
        self.assertEqual(boundary["activeRequest"]["proxy"]["runId"], middle)
        middle_view = self.view(board, middle)
        with self.assertRaises(BoardError) as wrong_run:
            board.call("workflow_decide", {"runId": middle, "requestId": middle_view["activeRequest"]["requestId"],
                       "expectedRevision": middle_view["revision"], "commandId": "wrong-control", "decision": "approve",
                       **self.control(root)})
        self.assertEqual(wrong_run.exception.code, "UNAUTHORIZED")
        self.answer(board, root["runId"], "answer-leaf")
        self.assertEqual(self.view(board, leaf)["state"], "executing")
        self.assertEqual(self.view(board, middle)["state"], "waiting-helpers")
        self.assertEqual(self.view(board, root["runId"])["state"], "waiting-helpers")
        leaf_claim = self.claim(board, "w2", claim_request_id="leaf-resumed", run_id=leaf)
        self.finish_turn(board, leaf_claim, worker_id="w2")
        self.assertEqual(self.view(board, middle)["state"], "executing")
        self.assertEqual(self.view(board, root["runId"])["state"], "waiting-helpers")
        middle_claim = self.claim(board, "w1", claim_request_id="middle-resumed", run_id=middle)
        self.assertEqual(middle_claim["claim"]["turn"]["input"]["context"]["helperOutcomes"][0]["taskId"], leaf)
        self.finish_turn(board, middle_claim)
        self.assertEqual(self.view(board, root["runId"])["state"], "executing")
        root_claim = self.claim(board, "w1", claim_request_id="root-resumed", run_id=root["runId"])
        self.finish_turn(board, root_claim)
        for run_id in (leaf, middle, root["runId"]):
            view = self.view(board, run_id)
            self.assertEqual(view["continuationCount"], 1)
            self.assertEqual(view["counts"]["openRequests"], 0)

    def test_cancelled_ancestor_fences_a_pending_nested_proxy(self):
        board, root, (middle,) = self.parent_with_helpers()
        self.yield_helper(board, middle, "w1", "middle-yield")
        self.answer(board, root["runId"], "grant-dependency", helpers=[self.helper_spec("leaf")])
        leaf = self.view(board, middle)["children"][0]["taskId"]
        self.yield_helper(board, leaf, "w2", "leaf-yield")
        waiting = self.view(board, root["runId"])
        board.call("workflow_cancel", {"runId": root["runId"], **self.control(root)})
        with self.assertRaises(BoardError):
            self.decide(board, waiting, waiting["activeRequest"]["requestId"], command_id="late-answer")
        for run_id in (root["runId"], middle, leaf):
            view = self.view(board, run_id)
            self.assertEqual(view["state"], "cancelled")
            self.assertIsNone(view["activeRequest"])
            self.assertEqual(view["counts"]["openRequests"], 0)

    def test_two_nested_leaf_boundaries_keep_both_proxy_queues_reviewable(self):
        board, root, (middle,) = self.parent_with_helpers()
        self.yield_helper(board, middle, "w1", "middle-yield")
        self.answer(board, root["runId"], "grant-dependencies", helpers=[self.helper_spec("leaf-one"), self.helper_spec("leaf-two")])
        first, second = [child["taskId"] for child in self.view(board, middle)["children"]]
        self.yield_helper(board, first, "w1", "first-leaf-yield")
        self.yield_helper(board, second, "w2", "second-leaf-yield")
        for run_id in (root["runId"], middle):
            self.assertEqual(self.view(board, run_id)["counts"]["openRequests"], 2)
        self.answer(board, root["runId"], "answer-first-leaf")
        self.finish_turn(board, self.claim(board, "w1", claim_request_id="first-leaf-resumed", run_id=first))
        for run_id in (root["runId"], middle):
            view = self.view(board, run_id)
            self.assertEqual(view["state"], "awaiting-host")
            self.assertEqual(view["activeRequest"]["origin"]["runId"], second)
        self.answer(board, root["runId"], "answer-second-leaf")
        self.finish_turn(board, self.claim(board, "w2", claim_request_id="second-leaf-resumed", run_id=second), worker_id="w2")
        self.assertEqual(self.view(board, middle)["state"], "executing")
        self.assertEqual(self.view(board, root["runId"])["state"], "waiting-helpers")
        self.finish_turn(board, self.claim(board, "w1", claim_request_id="middle-finished", run_id=middle))
        self.assertEqual(self.view(board, root["runId"])["continuationCount"], 1)
        with board.store.db.read() as connection:
            count = connection.execute("SELECT COUNT(*) FROM events WHERE task_id=? AND kind='workflow.boundary_updated'", (root["runId"],)).fetchone()[0]
            self.assertGreater(count, 0, "the outer Host wait must receive its own committed wakeup evidence")

    def test_cancelled_intermediate_closes_its_root_proxy_without_revival(self):
        board, root, (middle,) = self.parent_with_helpers()
        self.yield_helper(board, middle, "w1", "middle-yield")
        self.answer(board, root["runId"], "grant-dependency", helpers=[self.helper_spec("leaf")])
        leaf = self.view(board, middle)["children"][0]["taskId"]
        self.yield_helper(board, leaf, "w2", "leaf-yield")
        board.store.workflow.cancel({"runId": middle}, console_authority={"sessionId": "test-console"})
        for run_id in (middle, leaf):
            self.assertEqual(self.view(board, run_id)["state"], "cancelled")
        view = self.view(board, root["runId"])
        self.assertEqual(view["counts"]["openRequests"], 0)
        self.assertIsNone(view["activeRequest"])
        self.assertEqual(view["state"], "executing")
        self.assertEqual(view["continuationCount"], 1)
