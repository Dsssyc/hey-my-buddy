"""A stopped pre-model refusal requeues the same continuation under service authority."""
from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.cli.cli_views import micro_task_brief, task_brief


class RefusedResumeTests(WorkflowTestCase):
    def native_turn(self, harness="zcode", *, suffix="", continuation_kind="manual"):
        self.executors[harness].native_resume = True
        board = self.board()
        board.call("worker_register", {"workerId": "w1", "capabilities": [harness]})
        board.call("worker_register", {"workerId": "w2", "capabilities": [harness]})
        submitted = self.submit(board, request_id="submit-" + suffix, cwd=str(self.workdir("work-" + suffix)),
                                harness=harness, provider="fixture", model="fixture", effort="high")
        first = self.claim(board, claim_request_id="first-" + suffix)
        self.finish_turn(board, first, disposition="attention", result_mutator=lambda result: result.update(
            nativeSession={"storageOwner": {"codex": "harness-user-store",
                                           "dsh": "hey-my-buddy-micro-task"}.get(harness)}))
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        if continuation_kind == "manual":
            self.continue_run(board, view, command_id="continue-" + suffix, input="Keep this exact continuation input.")
        else:
            self.decide(board, view, view["activeRequest"]["requestId"], reason="Keep this approved input.")
        second = self.claim(board, claim_request_id="native-" + suffix)
        self.assertEqual(second["claim"]["turn"]["resumeMode"], "native-session")
        return board, submitted["runId"], second

    def refuse(self, board, claim, *, started=False, shutdown=True, code="native-resume-unavailable"):
        def result(payload):
            payload.pop("turn")
            payload.update(code=code, modelStarted=started, error="Fixture native refusal explanation.")
        return self.finish_turn(board, claim, runner_status="failed", exit_code=1, seal=False,
                                shutdown=shutdown, result_mutator=result)

    def assert_auto_rebuild(self, harness, *, continuation_kind="manual"):
        board, run_id, second = self.native_turn(harness, continuation_kind=continuation_kind)
        original = second["claim"]["turn"]["input"]
        response = self.refuse(board, second)
        self.assertEqual(response["taskState"], "queued")
        self.assertEqual(response["workflow"]["state"], "executing")
        view = board.call("workflow_get", {"runId": run_id, "includeAudit": True})
        recovery = view["resumeRecovery"]
        self.assertTrue(recovery["autoContinued"])
        self.assertEqual(recovery["reason"], "native-resume-refused")
        self.assertEqual(recovery["detail"], "Fixture native refusal explanation.")
        self.assertEqual(recovery["turnId"], original["turnId"])
        self.assertEqual(view["currentTurn"]["state"], "failed")
        self.assertEqual(view["continuationCount"], 2)
        self.assertEqual(micro_task_brief(view)["resumeRecovery"], recovery)
        status = board.call("task_get", {"runId": run_id})["task"]
        self.assertEqual(task_brief(status)["workflow"]["resumeRecovery"], recovery)
        # Replayed result and reads cannot allocate an attempt or requeue again.
        self.assertTrue(self.refuse(board, second)["duplicate"])
        third = self.claim(board, claim_request_id="rebuild")
        document = third["claim"]["turn"]["input"]
        self.assertEqual(document["resumeMode"], "reconstructed-new-session")
        self.assertEqual(document["context"]["resumeReason"], "native-resume-refused")
        self.assertEqual(document["context"]["resumeDetail"], recovery["detail"])
        self.assertEqual(document["context"]["continuation"], original["context"]["continuation"])
        self.assertEqual(document["executionWorkspace"], original["executionWorkspace"])
        self.assertEqual(self.claim(board, claim_request_id="rebuild")["claim"]["attempt"]["attemptId"],
                         third["claim"]["attempt"]["attemptId"])
        self.assertIsNone(self.claim(board, "w2", claim_request_id="competing", run_id=run_id)["claim"])
        with board.store.db.read() as connection:
            attempts = connection.execute("SELECT execution_state FROM attempts WHERE task_id=?", (run_id,)).fetchall()
            self.assertEqual(sum(row["execution_state"] != "finished" for row in attempts), 1)
            self.assertEqual(len(attempts), 3)
        events = board.call("events_read", {"runId": run_id, "after": 0, "limit": 200})["events"]
        auto = [row["payload"] for row in events if row["kind"] == "workflow.auto_continued"]
        self.assertEqual(len(auto), 1)
        self.assertEqual(auto[0]["reason"], "native-resume-refused")
        self.assertIn("workflow.native_resume_refused", [row["kind"] for row in events])
        self.assertNotIn("workflow.turn_failed", [row["kind"] for row in events])
        # Even another identical refusal is terminal once the turn is reconstructed.
        self.refuse(board, third)
        final = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(final["state"], "failed")
        self.assertEqual(final["continuationCount"], 2)
        self.assertIsNone(self.claim(board, "w2", claim_request_id="after-failure")["claim"])

    def test_codex_refusal_rebuilds_once(self):
        self.assert_auto_rebuild("codex")

    def test_dsh_refusal_rebuilds_once(self):
        self.assert_auto_rebuild("dsh")

    def test_zcode_refusal_rebuilds_once(self):
        self.assert_auto_rebuild("zcode")

    def test_helper_authorized_continuation_keeps_its_one_use_input(self):
        self.assert_auto_rebuild("zcode", continuation_kind="helper")

    def test_started_unknown_and_unconfirmed_failures_do_not_requeue(self):
        for index, facts in enumerate(({"started": True}, {"started": None}, {"started": 0},
                                       {"code": "other-failure"}, {"shutdown": False})):
            with self.subTest(facts=facts):
                board, run_id, second = self.native_turn(suffix=str(index))
                self.refuse(board, second, **facts)
                view = board.call("workflow_get", {"runId": run_id})
                self.assertEqual(view["state"], "failed")
                self.assertEqual(view["continuationCount"], 1)
                self.assertIsNone(view["resumeRecovery"])
                self.assertIsNone(self.claim(board, "w2", claim_request_id="blocked-" + str(index), run_id=run_id)["claim"])

    def test_initial_failure_does_not_requeue(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        self.refuse(board, self.claim(board))
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["state"], "failed")
        self.assertEqual(view["continuationCount"], 0)
        self.assertIsNone(view["resumeRecovery"])

    def test_helper_refusal_keeps_parent_waiting_and_checkout_reserved(self):
        self.executors["dsh"].native_resume = True
        board = self.board()
        self.register(board)
        parent = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        parent = board.call("workflow_get", {"runId": parent["runId"]})
        approved = self.decide(board, parent, parent["activeRequest"]["requestId"], helpers=[{
            "requestId": "helper", "task": "Do helper work", "cwd": str(self.workdir()),
            "executionWorkspace": {"kind": "worktree", "access": "write"},
        }])
        child_id = approved["children"][0]["taskId"]
        first = self.claim(board, claim_request_id="helper-first", run_id=child_id)
        self.finish_turn(board, first, disposition="attention", result_mutator=lambda result: result.update(
            nativeSession={"storageOwner": "hey-my-buddy-micro-task"}))
        parent = board.call("workflow_get", {"runId": parent["runId"]})
        self.decide(board, parent, parent["activeRequest"]["requestId"], command_id="approve-helper")
        second = self.claim(board, claim_request_id="helper-native", run_id=child_id)
        self.assertEqual(second["claim"]["turn"]["resumeMode"], "native-session")
        self.refuse(board, second)
        parent = board.call("workflow_get", {"runId": parent["runId"]})
        self.assertEqual(parent["state"], "waiting-helpers")
        self.assertNotIn(parent["children"][0]["state"], ("failed", "succeeded", "cancelled"))
        with board.store.db.read() as connection:
            held = connection.execute("SELECT state FROM workspace_reservations WHERE holder_task_id=?",
                                      (child_id,)).fetchall()
        self.assertIn("held", [row["state"] for row in held])
        third = self.claim(board, claim_request_id="helper-rebuild", run_id=child_id)
        self.assertEqual(third["claim"]["turn"]["resumeMode"], "reconstructed-new-session")
        self.finish_turn(board, third)
        parent = board.call("workflow_get", {"runId": parent["runId"]})
        self.assertEqual(parent["state"], "executing")
        self.assertEqual(parent["children"][0]["state"], "succeeded")
