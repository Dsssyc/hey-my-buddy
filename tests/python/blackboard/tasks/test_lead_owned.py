"""Lead credentials extend the existing operation and target boundaries."""
from unittest.mock import patch

from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from support import enable_fixture_buddy
from hey_my_buddy.blackboard.tasks import workflow
from hey_my_buddy.cli.cli_views import micro_task_brief, task_brief, list_brief
from hey_my_buddy.errors import BoardError


class LeadOwnedTests(WorkflowTestCase):
    def lead(self, board, name="lead", worker="w1", **extra):
        enable_fixture_buddy(board.store, BUDDY)
        submitted = self.submit(board, request_id=name, cwd=str(self.workdir(name)), access="full",
                                executionWorkspace={"kind": "existing", "access": "full", "accessReason": "Lead checks"},
                                delegationAuthority=True, **extra)
        self.register(board, worker)
        claim = self.claim(board, worker, claim_request_id="claim-" + name, run_id=submitted["runId"])
        return submitted, claim["claim"]

    def owned(self, board, lead_claim, name="owned", **extra):
        result = board.call("workflow_submit", {
            **BUDDY, "requestId": name, "task": "Do owned work", "cwd": str(self.workdir(name)),
            "executionWorkspace": {"kind": "existing", "access": "write"},
            "submissionToken": "s" * 32, "credential": lead_claim["agentCredential"], **extra,
        })
        self.controls[result["runId"]] = result["control"]
        return result

    def finish_owned(self, board, child, worker="w2", **extra):
        self.register(board, worker)
        claim = self.claim(board, worker, claim_request_id="claim-" + child["requestId"], run_id=child["runId"])
        self.finish_turn(board, claim, worker_id=worker, **extra)
        return board.call("workflow_get", {"runId": child["runId"]}), claim["claim"]

    def test_submit_records_run_turn_macro_and_source_in_one_admission(self):
        board = self.board()
        lead, claim = self.lead(board, macroTask={"title": "Grouped work"})
        child = self.owned(board, claim)
        ownership = {"leadRunId": lead["runId"], "leadTurnId": claim["turn"]["turnId"],
                     "createdAt": child["createdAt"]}
        self.assertEqual(child["leadOwnership"], ownership)
        self.assertFalse(child["delegationAuthority"])
        self.assertEqual(child["hostId"], "lead:" + lead["runId"])
        fetched = board.call("workflow_get", {"runId": child["runId"], "credential": claim["agentCredential"]})
        self.assertEqual(fetched["leadOwnership"], ownership)
        self.assertEqual(micro_task_brief(fetched)["leadOwnership"], ownership)
        listed = board.call("task_list", {})
        row = next(row for row in listed["tasks"] if row["runId"] == child["runId"])
        self.assertEqual(row["lineage"]["leadOwnership"], ownership)
        self.assertEqual(row["lineage"]["sourceHostId"], child["hostId"])
        self.assertEqual(task_brief(row)["leadOwnership"], ownership)
        self.assertEqual(next(row for row in list_brief(listed)["runs"] if row["runId"] == child["runId"])["leadOwnership"], ownership)
        lead_view = board.call("workflow_get", {"runId": lead["runId"]})
        self.assertEqual(lead_view["ownedMicroTasks"], [{"runId": child["runId"], "leadTurnId": claim["turn"]["turnId"],
                                                       "createdAt": child["createdAt"], "state": "executing"}])
        with board.store.db.read() as db:
            groups = dict(db.execute("SELECT run_id,macro_task_id FROM workflow_runs"))
            self.assertIsNotNone(groups[lead["runId"]])
            self.assertEqual(groups[lead["runId"]], groups[child["runId"]])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_children").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT owner FROM tasks WHERE task_id=?", (child["runId"],)).fetchone()[0],
                             "host:lead:" + lead["runId"])
        replay = self.owned(board, claim)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["control"], child["control"])

    def test_routing_and_shared_field_validation_still_apply(self):
        board = self.board()
        _, claim = self.lead(board)
        params = {"requestId": "routed", "task": "Route normally", "cwd": str(self.workdir("routed")),
                  "credential": claim["agentCredential"], "executionWorkspace": {"kind": "existing", "access": "write"}}
        routed = board.call("workflow_submit", params)
        self.assertIsNotNone(routed["routing"]["decisionId"])
        for change in ({"harness": "dsh"}, {"expectedSeconds": -1}, {"unknown": True}):
            with self.subTest(change=change), self.assertRaises(BoardError) as caught:
                board.call("workflow_submit", {**params, "requestId": "invalid", **change})
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_lead_cannot_supply_attribution_macro_or_another_grant(self):
        board = self.board()
        _, claim = self.lead(board)
        for key, value in (("hostId", "forged"), ("owner", "forged"), ("macroTask", {"title": "forged"}),
                           ("macroTaskId", "macro-forged"), ("macroTaskOf", "forged")):
            with self.subTest(field=key), self.assertRaises(BoardError) as caught:
                self.owned(board, claim, **{key: value})
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as caught:
            self.owned(board, claim, delegationAuthority=True,
                       executionWorkspace={"kind": "existing", "access": "full", "accessReason": "forged grant"})
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_lead_tasks").fetchone()[0], 0)

    def test_ungranted_credential_rejects_every_new_operation_even_with_real_control(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        claim = self.claim(board)["claim"]
        for operation in sorted(workflow.LEAD_OPERATIONS):
            with self.subTest(operation=operation), self.assertRaises(BoardError) as caught:
                board.call(operation, {"runId": submitted["runId"], **submitted["control"],
                                       "credential": claim["agentCredential"]})
            self.assertEqual(caught.exception.code, "FORBIDDEN")

    def test_foreign_host_other_lead_and_helper_targets_are_denied_for_every_selector(self):
        board = self.board(max_concurrent=4)
        lead, claim = self.lead(board)
        owned = self.owned(board, claim)
        with board.store.db.write() as db:
            db.execute("INSERT INTO model_service_concurrency(harness,provider,model,concurrency_limit,created_at,updated_at)"
                       " VALUES(?,?,?,4,?,?)", (BUDDY["harness"], BUDDY["provider"], BUDDY["model"], board.store.now(), board.store.now()))
        foreign = self.submit(board, request_id="host-own", cwd=str(self.workdir("host-own")))
        host_view, host_claim = self.finish_owned(board, foreign, disposition="assistance")
        other, other_claim = self.lead(board, "other-lead", "w3")
        other_owned = self.owned(board, other_claim, "other-owned")
        other_view, other_attempt = self.finish_owned(board, other_owned, "w4", disposition="assistance")
        decided = self.decide(board, host_view, host_view["activeRequest"]["requestId"], helpers=[{
            "requestId": "helper", "task": "Help host", "cwd": str(self.workdir("helper")),
            "executionWorkspace": {"kind": "existing", "access": "write"},
        }], autoContinue=False)
        helper_id = decided["children"][0]["taskId"]
        helper = board.call("workflow_get", {"runId": helper_id})
        helper_view, helper_claim = self.finish_owned(board, helper, "w2", disposition="assistance")
        scope = board.store.workflow.resolve_credential(claim["agentCredential"])
        for view, attempt in ((host_view, host_claim), (other_view, other_attempt), (helper_view, helper_claim)):
            message = board.call("message_post", {"runId": view["runId"], "inquiryId": "question", "question": "Status?"})["message"]
            for operation in sorted(workflow.LEAD_OWNED_OPERATIONS):
                selectors = [{"runId": view["runId"]}, {"taskId": view["runId"]},
                             {"requestId": view["activeRequest"]["requestId"] if operation == "workflow_decide" else view["requestId"]},
                             {"attemptId": attempt["attempt"]["attemptId"]}, {"messageId": message["messageId"]},
                             {"runId": owned["runId"], "taskId": view["runId"]},
                             {"runId": owned["runId"], "attemptId": attempt["attempt"]["attemptId"]},
                             {"runId": owned["runId"], "messageId": message["messageId"]}]
                for selector in selectors:
                    with self.subTest(kind=view["runId"], operation=operation, selector=selector), self.assertRaises(BoardError) as caught:
                        board.store.workflow.authorize_agent_operation(operation, dict(selector), scope)
                    self.assertEqual(caught.exception.code, "FORBIDDEN")
                with self.subTest(operation=operation), self.assertRaises(BoardError) as caught:
                    board.call(operation, {"runId": view["runId"], **self.controls.get(view["runId"], foreign["control"]),
                                           "credential": claim["agentCredential"]})
                self.assertEqual(caught.exception.code, "FORBIDDEN")
        for operation in workflow.LEAD_OPERATIONS - {"workflow_submit"}:
            with self.subTest(operation=operation), self.assertRaises(BoardError) as caught:
                board.call(operation, {"runId": lead["runId"], **lead["control"], "credential": claim["agentCredential"]})
            self.assertEqual(caught.exception.code, "FORBIDDEN")
        listing = board.call("task_list", {"credential": claim["agentCredential"]})
        self.assertEqual(listing["total"], 1)
        self.assertEqual([row["runId"] for row in listing["tasks"]], [owned["runId"]])
        filtered = board.call("task_list", {"credential": claim["agentCredential"], "hostId": "host-1"})
        self.assertEqual(filtered["total"], 0)
        for operation in ("workflow_takeover", "macro_task_stop", "task_submit", "task_cancel", "user_policy_publish", "selection_request", "service_control"):
            with self.subTest(operation=operation), self.assertRaises(BoardError) as caught:
                board.call(operation, {"runId": owned["runId"], "credential": claim["agentCredential"]})
            self.assertEqual(caught.exception.code, "FORBIDDEN")

    def test_submit_cannot_replay_a_foreign_request_or_leave_a_partial_link(self):
        board = self.board()
        lead, claim = self.lead(board)
        foreign = self.submit(board, request_id="foreign", cwd=str(self.workdir("foreign")), host_id="lead:" + lead["runId"])
        with self.assertRaises(BoardError) as caught:
            self.owned(board, claim, "foreign")
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        original = board.store._append_event
        def fail_link(connection, kind, **kwargs):
            if kind == "workflow.lead_task_created":
                raise BoardError("CONFLICT", "Simulated admission rollback")
            return original(connection, kind, **kwargs)
        with patch.object(board.store, "_append_event", side_effect=fail_link), self.assertRaises(BoardError):
            self.owned(board, claim, "rolled-back")
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_lead_tasks").fetchone()[0], 0)
            self.assertIsNone(db.execute("SELECT task_id FROM tasks WHERE request_id='rolled-back'").fetchone())

    def test_owned_read_wait_inquiry_decide_continue_accept_conclude_and_cancel(self):
        board = self.board()
        _, claim = self.lead(board)
        child = self.owned(board, claim)
        cred = {"credential": claim["agentCredential"]}
        self.assertEqual(board.call("task_get", {"requestId": "owned", **cred})["task"]["runId"], child["runId"])
        self.assertEqual(board.call("task_wait", {"runId": child["runId"], "timeoutMs": 0, **cred})["runId"], child["runId"])
        board.call("artifact_list", {"runId": child["runId"], **cred})
        board.call("events_read", {"runId": child["runId"], **cred})
        board.call("events_wait", {"runId": child["runId"], "timeoutMs": 0, **cred})
        board.call("inquiry_observe", {"runId": child["runId"], "inquiryId": "question", "question": "Status?", **cred})
        message = board.call("message_get", {"runId": child["runId"], "inquiryId": "question", **cred})["message"]
        board.call("message_get", {"messageId": message["messageId"], **cred})
        board.call("message_list", {"runId": child["runId"], **cred})
        board.call("message_wait", {"runId": child["runId"], "inquiryId": "question", "timeoutMs": 0, **cred})
        view, _ = self.finish_owned(board, child, disposition="assistance")
        declined = self.decide(board, view, view["activeRequest"]["requestId"], decision="decline", **cred)
        continued = self.continue_run(board, declined, **cred)
        self.assertEqual(continued["state"], "executing")
        second = self.claim(board, "w2", claim_request_id="owned-next", run_id=child["runId"])
        self.finish_turn(board, second, worker_id="w2")
        board.call("task_result", {"runId": child["runId"], **cred})
        delivered = board.call("workflow_get", {"runId": child["runId"], **cred})
        accepted = self.accept_run(board, delivered, **cred)
        self.assertEqual(accepted["state"], "accepted")
        ended = self.owned(board, claim, "concluded")
        ended_view, _ = self.finish_owned(board, ended)
        concluded = board.call("workflow_conclude", {"runId": ended["runId"], "note": "Observed outcome",
                                                      **ended["control"], **cred})
        self.assertEqual(concluded["state"], "concluded")
        cancelled = self.owned(board, claim, "cancelled")
        running = self.claim(board, "w2", claim_request_id="cancel-running", run_id=cancelled["runId"])
        result = board.call("workflow_cancel", {"runId": cancelled["runId"], "reason": "Lead stopped owned work",
                                               **cancelled["control"], **cred})
        self.assertEqual(result["state"], "cancelled")
        attempt = board.call("task_get", {"runId": cancelled["runId"]})["task"]["selectedAttempt"]
        self.assertEqual(attempt["stopInitiator"], "lead")
        self.assertIsNotNone(attempt["graceDeadline"])

    def test_owned_get_is_bounded_and_grant_does_not_transfer_to_child_execution(self):
        board = self.board()
        lead, claim = self.lead(board)
        children = [self.owned(board, claim, "owned-" + str(index)) for index in range(workflow.MAX_CHILD_VIEW + 1)]
        view = board.call("workflow_get", {"runId": lead["runId"]})
        self.assertEqual(len(view["ownedMicroTasks"]), workflow.MAX_CHILD_VIEW)
        self.assertEqual(view["counts"]["ownedMicroTasks"], workflow.MAX_CHILD_VIEW + 1)
        self.assertEqual(view["truncated"]["ownedMicroTasks"], 1)
        self.register(board, "w2")
        child = self.claim(board, "w2", claim_request_id="child", run_id=children[0]["runId"])["claim"]
        self.assertFalse(child["turn"]["input"]["context"]["delegationAuthority"])
        for operation in workflow.LEAD_OPERATIONS:
            with self.subTest(operation=operation), self.assertRaises(BoardError) as caught:
                board.call(operation, {"runId": children[1]["runId"], "credential": child["agentCredential"]})
            self.assertEqual(caught.exception.code, "FORBIDDEN")

    def test_new_lead_turn_controls_existing_owned_tasks_without_rewriting_creation_turn(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        original = child["leadOwnership"]
        self.finish_turn(board, {"claim": claim})
        with self.assertRaises(BoardError) as caught:
            board.call("workflow_get", {"runId": child["runId"], "credential": claim["agentCredential"]})
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        current = board.call("workflow_get", {"runId": lead["runId"]})
        self.continue_run(board, current)
        replacement = self.claim(board, claim_request_id="lead-next", run_id=lead["runId"])["claim"]
        self.assertNotEqual(replacement["turn"]["turnId"], original["leadTurnId"])
        result = board.call("workflow_cancel", {"runId": child["runId"], **child["control"],
                                               "credential": replacement["agentCredential"]})
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(result["leadOwnership"], original)

    def test_lead_answers_its_owned_micro_tasks_helper_proxy_request(self):
        board = self.board()
        _, claim = self.lead(board)
        owned = self.owned(board, claim)
        view, _ = self.finish_owned(board, owned, disposition="assistance")
        decided = self.decide(board, view, view["activeRequest"]["requestId"], helpers=[{
            "requestId": "helper", "task": "Help owned work", "cwd": str(self.workdir("helper")),
            "executionWorkspace": {"kind": "existing", "access": "write"},
        }], autoContinue=False, credential=claim["agentCredential"])
        helper = board.call("workflow_get", {"runId": decided["children"][0]["taskId"]})
        self.finish_owned(board, helper, disposition="assistance")
        parent = board.call("workflow_get", {"runId": owned["runId"]})
        self.assertEqual(parent["activeRequest"]["childTaskId"], helper["runId"])
        request_id = parent["activeRequest"]["requestId"]
        decided = self.decide(board, parent, request_id, command_id="proxy",
                              decision="decline", credential=claim["agentCredential"])
        self.assertEqual(decided["runId"], owned["runId"])
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT state FROM workflow_requests WHERE request_id=?", (request_id,)).fetchone()[0], "declined")

    def test_lead_accepts_and_concludes_its_owned_micro_tasks_helpers(self):
        board = self.board()
        _, claim = self.lead(board)
        owned = self.owned(board, claim)
        view, _ = self.finish_owned(board, owned, disposition="assistance")
        parent = self.decide(board, view, view["activeRequest"]["requestId"], helpers=[{
            "requestId": name, "task": "Help owned work", "cwd": str(self.workdir(name)),
            "executionWorkspace": {"kind": "existing", "access": "write"},
        } for name in ("accept-helper", "conclude-helper")], autoContinue=False, credential=claim["agentCredential"])
        helpers = []
        for child in parent["children"]:
            helper = board.call("workflow_get", {"runId": child["taskId"]})
            finished, _ = self.finish_owned(board, helper)
            helpers.append(finished)
        first, second = helpers
        control = {**owned["control"], "credential": claim["agentCredential"]}
        accepted = board.call("workflow_accept", {"runId": owned["runId"], "targetRunId": first["runId"],
                                                  "artifactId": first["finalArtifactId"], "note": "Reviewed helper output",
                                                  "notRequired": "No separate integration target", **control})
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["targetRunId"], first["runId"])
        concluded = board.call("workflow_conclude", {"runId": owned["runId"], "targetRunId": second["runId"],
                                                     "note": "Concluded helper work", **control})
        self.assertEqual(concluded["state"], "concluded")
        self.assertEqual(concluded["targetRunId"], second["runId"])

    def test_handlers_refuse_foreign_host_and_other_leads_helper_targets(self):
        board = self.board(max_concurrent=4)
        _, claim = self.lead(board)
        with board.store.db.write() as db:
            db.execute("INSERT INTO model_service_concurrency(harness,provider,model,concurrency_limit,created_at,updated_at)"
                       " VALUES(?,?,?,4,?,?)", (BUDDY["harness"], BUDDY["provider"], BUDDY["model"], board.store.now(), board.store.now()))
        owned = self.owned(board, claim)
        host = self.submit(board, request_id="host-root", cwd=str(self.workdir("host-root")))
        _, other_claim = self.lead(board, "other-lead", "w3")
        other_owned = self.owned(board, other_claim, "other-owned")
        for parent in (host, other_owned):
            view, _ = self.finish_owned(board, parent, disposition="assistance")
            created = self.decide(board, view, view["activeRequest"]["requestId"], command_id="approve-" + parent["requestId"], helpers=[{
                "requestId": "helper-" + parent["requestId"], "task": "Foreign help",
                "cwd": str(self.workdir("helper-" + parent["requestId"])),
                "executionWorkspace": {"kind": "existing", "access": "write"},
            }], autoContinue=False)
            target = created["children"][0]["taskId"]
            control = {"runId": owned["runId"], "targetRunId": target, **owned["control"], "credential": claim["agentCredential"]}
            for operation, params in (("workflow_conclude", {"note": "Cannot conclude foreign help"}),
                                      ("workflow_accept", {"artifactId": "foreign-artifact", "note": "Cannot accept foreign help",
                                                           "notRequired": "No separate integration target"})):
                with self.subTest(parent=parent["requestId"], operation=operation), self.assertRaises(BoardError) as caught:
                    board.call(operation, {**control, **params})
                self.assertEqual(caught.exception.code, "UNAUTHORIZED")

    def test_revocation_during_preparation_fences_new_admission(self):
        self.revocation_fence("submit")

    def test_revocation_during_preparation_fences_continuation(self):
        self.revocation_fence("continue")

    def revocation_fence(self, mutation):
        board = self.board()
        _, claim = self.lead(board)
        child = self.owned(board, claim)
        if mutation == "continue":
            view, _ = self.finish_owned(board, child)
        validate = board.store.workflow._validated_buddy
        def revoke_after_validation(spec):
            result = validate(spec)
            with board.store.db.write() as db:
                db.execute("UPDATE agent_credentials SET state='revoked' WHERE run_id=?", (claim["task"]["runId"],))
            return result
        with patch.object(board.store.workflow, "_validated_buddy", side_effect=revoke_after_validation), self.assertRaises(BoardError) as caught:
            if mutation == "submit":
                self.owned(board, claim, "revoked")
            else:
                self.continue_run(board, view, buddy=BUDDY, credential=claim["agentCredential"])
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        with board.store.db.read() as db:
            self.assertIsNone(db.execute("SELECT task_id FROM tasks WHERE request_id='revoked'").fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_continuations").fetchone()[0], 0)
