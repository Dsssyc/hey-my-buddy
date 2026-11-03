"""Input refusal uses one durable Host boundary after an observed stop."""
from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.capabilities import INPUT_LIMITS
from support import settle_fixture_receipt


def failure(stage):
    known = stage == "preflight"
    limit = INPUT_LIMITS["codex" if known else "dsh"].to_payload()
    total = limit["value"] + 1 if known else 14
    measurement = {"unit": "unicode-scalars", "total": total,
                   "parts": {"role": 0, "task": total, "hostInput": 0,
                             "context": 0, "delivery": 0, "format": 0}}
    if not known:
        measurement.update(utf8Bytes=total, utf8Parts=dict(measurement["parts"]))
    return {"version": 1, "stage": stage,
            "harness": {"name": "codex" if known else "dsh", "form": limit["form"],
                        "version": "0.164.0" if known else None},
            "limit": limit, "measurement": measurement,
            "nativeRequestSent": not known, "modelStarted": False,
            **({} if known else {"nativeMethod": "session/prompt", "nativeCode": -32602,
                                 "input_error_code": "input_too_large", "max_chars": 10,
                                 "actual_chars": total, "message": "The native input was rejected"})}


class InputFailureWorkflowTests(WorkflowTestCase):
    def rejected(self, stage, *, shutdown=True, provider_rejection=None, code="HARNESS_INPUT_REJECTED"):
        board = self.board()
        self.register(board)
        submitted = self.submit(board, **({"harness": "codex"} if stage == "preflight" else {}))
        claimed = self.claim(board)

        def receipt(result):
            result.pop("turn")
            result.update(code=code, inputFailure=failure(stage))
            if provider_rejection is not None:
                result["providerRejection"] = provider_rejection

        settled = self.finish_turn(board, claimed, seal=False, shutdown=shutdown,
                                   runner_status="failed", exit_code=1, result_mutator=receipt)
        return board, submitted, claimed, settled

    def assert_stage_opens_host_boundary(self, stage):
        board, submitted, claimed, settled = self.rejected(stage)
        run_id = submitted["runId"]
        view = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(settled["workflow"]["state"], "awaiting-host")
        self.assertEqual(view["state"], "awaiting-host")
        self.assertEqual(view["currentTurn"]["state"], "failed")
        self.assertEqual(view["currentTurn"]["inputFailure"]["stage"], stage)
        self.assertEqual(view["activeRequest"]["reason"], {
            "code": "workflow.input-rejected",
            "params": {"harness": failure(stage)["harness"]["name"]}})
        self.assertEqual(view["activeRequest"]["inputFailure"], failure(stage))
        self.assertEqual(board.call("task_result", {"runId": run_id})["inputFailure"], failure(stage))
        with board.store.db.read() as connection:
            credential = connection.execute("SELECT state FROM agent_credentials WHERE attempt_id=?",
                (claimed["claim"]["attempt"]["attemptId"],)).fetchone()
            self.assertEqual(credential[0], "revoked")
        resumed = self.continue_run(board, view, input="Use a shorter task input")
        self.assertEqual(resumed["state"], "executing")
        with self.assertRaises(BoardError) as stale:
            self.continue_run(board, view, command_id="stale-continue")
        self.assertEqual(stale.exception.code, "REVISION_CONFLICT")

    def test_preflight_rejection_opens_host_boundary(self):
        self.assert_stage_opens_host_boundary("preflight")

    def test_native_admission_rejection_opens_host_boundary(self):
        self.assert_stage_opens_host_boundary("native-admission")

    def test_unknown_shutdown_retains_workspace_and_waits_for_reconciliation(self):
        board, submitted, claimed, settled = self.rejected("native-admission", shutdown=False)
        run_id = submitted["runId"]
        view = board.call("workflow_get", {"runId": run_id})
        self.assertFalse(settled["committed"])
        self.assertEqual(settled["attempt"]["executionState"], "finalizing")
        self.assertEqual(view["state"], "executing")
        self.assertIsNone(view["activeRequest"])
        self.assertFalse(view["task"]["shutdownConfirmed"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT state FROM workspace_reservations WHERE holder_task_id=? ORDER BY rowid DESC LIMIT 1",
                (run_id,)).fetchone()[0], "held")
        with self.assertRaises(BoardError) as unsafe:
            self.continue_run(board, view)
        self.assertEqual(unsafe.exception.code, "CONFLICT")
        settled = settle_fixture_receipt(board, claimed["claim"]["attempt"]["attemptId"], native_stopped=True)
        self.assertEqual(settled["workflow"]["state"], "awaiting-host")
        view = board.call("workflow_get", {"runId": run_id})
        self.assertTrue(view["task"]["shutdownConfirmed"])
        self.assertEqual(view["activeRequest"]["reason"]["code"], "workflow.input-rejected")

    def test_input_code_prevents_quota_cooling_and_automatic_reroute(self):
        contradiction = {"code": "quota-exceeded", "nativeCode": "usageLimitExceeded",
                         "source": "synthetic/turn-error", "observedAt": "2026-01-01T00:00:00.000Z",
                         "resetsAt": None, "rejectionPhase": "before-model"}
        board, submitted, _claimed, settled = self.rejected("native-admission", provider_rejection=contradiction)
        self.assertFalse(settled["workflow"].get("autoContinued", False))
        self.assertEqual(board.call("workflow_get", {"runId": submitted["runId"]})["state"], "awaiting-host")
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM provider_cooling").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE kind='workflow.auto_continued'").fetchone()[0], 0)

    def test_generic_native_error_keeps_diagnostics_without_input_attention(self):
        board, submitted, _claimed, settled = self.rejected("native-admission", code="harness-error")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(settled["workflow"]["state"], "failed")
        self.assertIsNone(view["activeRequest"])
        self.assertEqual(view["currentTurn"]["inputFailure"], failure("native-admission"))

    def test_service_boundary_requires_explicit_native_code_and_a_sent_failed_request(self):
        board = self.board()
        for status, changes in (("failed", {"input_error_code": "different"}),
                                ("failed", {"nativeRequestSent": None}), ("ok", {})):
            facts = {**failure("native-admission"), **changes}
            with self.subTest(status=status, changes=changes), self.assertRaises(BoardError) as caught:
                board.store._commit_execution_result({"status": status,
                    "result": {"code": "HARNESS_INPUT_REJECTED", "inputFailure": facts}}, seal_stash=None)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
