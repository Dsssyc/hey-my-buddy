"""Lead credentials extend the existing operation and target boundaries."""
import json
from unittest.mock import patch

from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from support import enable_fixture_buddy
from hey_my_buddy.blackboard.tasks import workflow
from hey_my_buddy.cli.cli_views import micro_task_brief, task_brief, list_brief
from hey_my_buddy.errors import BoardError


class LeadOwnedTests(WorkflowTestCase):
    def test_execution_credentials_never_receive_host_waiting_counts(self):
        board = self.board()
        self.register(board)
        returned = self.submit(board)
        self.finish_turn(board, self.claim(board))
        self.assertEqual(board.call('workflow_get', {'runId': returned['runId']})['waitingForHost'],
                         {'review': 1})
        lead, claim = self.lead(board, worker='lead-worker')
        child = self.owned(board, claim)
        self.assertNotIn('waitingForHost', child)
        self.assertNotIn('waitingForHost', board.call('workflow_get', {'runId': child['runId']}))
        for operation, params in (
            ('workflow_get', {'runId': lead['runId']}),
            ('task_get', {'runId': lead['runId']}),
            ('task_list', {}),
            ('workflow_get', {'runId': child['runId']}),
        ):
            with self.subTest(operation=operation, params=params):
                response = board.call(operation, {**params, 'credential': claim['agentCredential']})
                self.assertNotIn('waitingForHost', response)

    def lead(self, board, name="lead", worker="w1", **extra):
        enable_fixture_buddy(board.store, BUDDY)
        submitted = self.submit(board, request_id=name, cwd=str(self.workdir(name)), access="full",
                                executionWorkspace={"kind": "existing", "access": "full", "accessNote": "Lead checks"},
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
        self.assertNotIn("control", result)
        board.service.register_console_authority("owned-fixture-console")
        self.controls[result["runId"]] = {"consoleAuthority": "owned-fixture-console"}
        return result

    def finish_owned(self, board, child, worker="w2", **extra):
        self.register(board, worker)
        claim = self.claim(board, worker, claim_request_id="claim-" + child["requestId"], run_id=child["runId"])
        self.finish_turn(board, claim, worker_id=worker, **extra)
        return board.call("workflow_get", {"runId": child["runId"]}), claim["claim"]

    def test_standalone_lead_and_owned_execution_share_one_console_and_record_group(self):
        from hey_my_buddy.blackboard.tasks import macro_task_deletion, macro_tasks
        from hey_my_buddy.blackboard.tasks.session_content import identity
        from hey_my_buddy.private_dirs import session_record_path

        board = self.board()
        lead, lead_claim = self.lead(board)
        owned = self.owned(board, lead_claim)
        _view, owned_claim = self.finish_owned(board, owned)
        group = 'run:' + lead['runId']
        for claim in (lead_claim, owned_claim):
            task_id, attempt_id = claim['task']['taskId'], claim['attempt']['attemptId']
            self.assertEqual(identity(board.store, task_id, attempt_id)['group_id'], group)
            self.assertEqual(claim['sessionRecordPath'],
                             str(session_record_path(board.directory, group, attempt_id)))
        groups = board.call('macro_task_list', {})['macroTasks']
        self.assertEqual([row['macroTaskId'] for row in groups], [group])
        with board.store.db.read() as db:
            members = db.execute(f'SELECT task_id,group_id FROM ({macro_tasks.MEMBERS_SQL})').fetchall()
        self.assertEqual({row['task_id']: row['group_id'] for row in members},
                         {lead['runId']: group, owned['runId']: group})
        self.assertEqual(macro_task_deletion.preview(board.store, group)['microTaskCount'], 2)
        rows = board.call('macro_task_timeline', {'macroTaskId': group})['rows']
        self.assertEqual({row['runId'] for row in rows}, {lead['runId'], owned['runId']})
        self.assertFalse(session_record_path(board.directory, 'run:' + owned['runId'], 'unused').parent.exists())

    def test_submit_records_run_turn_macro_and_source_in_one_admission(self):
        board = self.board()
        lead, claim = self.lead(board, macroTask={"title": "Grouped work"})
        child = self.owned(board, claim)
        ownership = {"leadRunId": lead["runId"], "leadTurnId": claim["turn"]["turnId"],
                     "createdAt": child["createdAt"]}
        self.assertEqual(child["leadOwnership"], ownership)
        self.assertFalse(child["delegationAuthority"])
        self.assertIsNone(child["hostId"])
        fetched = board.call("workflow_get", {"runId": child["runId"], "credential": claim["agentCredential"]})
        self.assertEqual(fetched["leadOwnership"], ownership)
        self.assertEqual(micro_task_brief(fetched)["leadOwnership"], ownership)
        listed = board.call("task_list", {})
        row = next(row for row in listed["runs"] if row["runId"] == child["runId"])
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
            self.assertEqual(tuple(db.execute(
                "SELECT actor_role,actor_id FROM events WHERE kind='task.submitted' AND task_id=?",
                (child["runId"],)).fetchone()), ("lead", lead["runId"]))
            self.assertIsNone(db.execute("SELECT owner FROM tasks WHERE task_id=?", (child["runId"],)).fetchone()[0])
        replay = self.owned(board, claim)
        self.assertTrue(replay["duplicate"])
        self.assertNotIn("control", replay)
        self.assertFalse(replay["controlAvailable"])

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
                       executionWorkspace={"kind": "existing", "access": "full", "accessNote": "forged grant"})
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
            db.execute("INSERT INTO model_service_concurrency(harness,provider,model,concurrency_limit)"
                       " VALUES(?,?,?,4)", (BUDDY["harness"], BUDDY["provider"], BUDDY["model"]))
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
            message = board.store._message_post(view["runId"], "question", "Status?")["message"]
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
                    board.call(operation, {"runId": view["runId"],                                            "credential": claim["agentCredential"]})
                self.assertEqual(caught.exception.code, "FORBIDDEN")
        for operation in workflow.LEAD_OPERATIONS - {"workflow_submit"}:
            with self.subTest(operation=operation), self.assertRaises(BoardError) as caught:
                board.call(operation, {"runId": lead["runId"], **lead["control"], "credential": claim["agentCredential"]})
            self.assertEqual(caught.exception.code, "FORBIDDEN")
        listing = board.call("task_list", {"credential": claim["agentCredential"]})
        self.assertEqual(listing["total"], 1)
        self.assertEqual([row["runId"] for row in listing["runs"]], [owned["runId"]])
        filtered = board.call("task_list", {"credential": claim["agentCredential"], "hostId": "host-1"})
        self.assertEqual(filtered["total"], 0)
        for operation in ("workflow_takeover", "macro_task_stop", "task_cancel", "user_policy_publish", "selection_request", "service_control"):
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
        for operation in ("task_wait", "events_wait"):
            with self.subTest(operation=operation), self.assertRaises(BoardError) as caught:
                board.call(operation, {"runId": child["runId"], "timeoutMs": 0, **cred})
            self.assertEqual(caught.exception.code, "FORBIDDEN")
        board.call("artifact_list", {"runId": child["runId"], **cred})
        board.call("events_read", {"runId": child["runId"], **cred})
        board.call("inquiry_observe", {"runId": child["runId"], "inquiryId": "question", "question": "Status?", **cred})
        message = board.call("message_get", {"runId": child["runId"], "inquiryId": "question", **cred})["message"]
        board.call("message_get", {"messageId": message["messageId"], **cred})
        board.call("message_list", {"runId": child["runId"], **cred})
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
                                                      **cred})
        self.assertEqual(concluded["state"], "concluded")
        cancelled = self.owned(board, claim, "cancelled")
        running = self.claim(board, "w2", claim_request_id="cancel-running", run_id=cancelled["runId"])
        result = board.call("workflow_cancel", {"runId": cancelled["runId"], 'note': "Lead stopped owned work",
                                               **cred})
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
        result = board.call("workflow_cancel", {"runId": child["runId"],                                                "credential": replacement["agentCredential"]})
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
        control = {"credential": claim["agentCredential"]}
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
            db.execute("INSERT INTO model_service_concurrency(harness,provider,model,concurrency_limit)"
                       " VALUES(?,?,?,4)", (BUDDY["harness"], BUDDY["provider"], BUDDY["model"]))
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
            control = {"runId": owned["runId"], "targetRunId": target, "credential": claim["agentCredential"]}
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

    def test_owned_wake_filter_compares_role_and_identity(self):
        from hey_my_buddy.blackboard.tasks.workflow import OWNED_WAKE_FILTER_SQL
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        with board.store.db.write() as db:
            for role, identifier, expected in (("lead", lead["runId"], 0),
                                                ("host", lead["runId"], 1),
                                                ("host", "lead:" + lead["runId"], 1),
                                                ("lead", "another-lead", 1)):
                board.store._append_event(db, "workflow.cancelled", task_id=child["runId"],
                                          payload={"actor": {"role": role, "id": identifier}})
                row = db.execute("SELECT * FROM events ORDER BY seq DESC LIMIT 1").fetchone()
                self.assertEqual((row["actor_role"], row["actor_id"]), (role, identifier))
                self.assertNotIn("actor", json.loads(row["payload_json"]))
                count = db.execute("SELECT COUNT(*) FROM events e JOIN workflow_lead_tasks l"
                                   " ON l.owned_run_id=e.task_id WHERE e.seq=? AND " + OWNED_WAKE_FILTER_SQL,
                                   (row["seq"],)).fetchone()[0]
                self.assertEqual(count, expected)

    def test_without_execution_credential_lead_named_host_is_host(self):
        board = self.board()
        lead, claim = self.lead(board)
        host = self.submit(board, request_id="named-host", host_id="lead:" + lead["runId"],
                           cwd=str(self.workdir("named-host")))
        self.assertIsNone(host["leadOwnership"])
        self.assertEqual(host["hostId"], "lead:" + lead["runId"])
        stopped = board.call("workflow_cancel", {"runId": host["runId"], **host["control"]})
        self.assertEqual(stopped["cancellation"]["actor"], {"role": "host", "id": host["hostId"]})
        child = self.owned(board, claim)
        stopped = board.call("workflow_cancel", {"runId": child["runId"], "credential": claim["agentCredential"]})
        self.assertEqual(stopped["cancellation"]["actor"], {"role": "lead", "id": lead["runId"]})

    def test_owned_mutations_never_accept_a_nameless_host_control(self):
        board = self.board()
        _lead, claim = self.lead(board)
        child = self.owned(board, claim)
        run_id = child["runId"]
        leaked = {"hostId": None, "ownerGeneration": 1,
                  "controlToken": board.store.db.control_token(run_id, 1)}
        operations = (
            ("workflow_cancel", {"note": "uncredentialed stop"}),
            ("workflow_continue", {"commandId": "uncredentialed-continue", "input": "continue", "expectedRevision": child["revision"]}),
            ("workflow_accept", {"artifactId": "unreviewed-artifact", "note": "uncredentialed accept", "notRequired": "fixture"}),
            ("workflow_takeover", {"commandId": "uncredentialed-takeover", "newHostId": "forged-host", "expectedOwnerGeneration": 1}),
        )
        for operation, params in operations:
            for authority in ({}, leaked):
                with self.subTest(operation=operation, authority=bool(authority)), self.assertRaises(BoardError) as caught:
                    board.call(operation, {"runId": run_id, **params, **authority})
                self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        self.assertEqual(board.call("workflow_get", {"runId": run_id})["state"], "executing")

    def test_continuation_uses_current_owner_and_preserves_original_manifest(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        run_id = child["runId"]
        with board.store.db.read() as db:
            original = db.execute("SELECT workspace_manifest_json FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()[0]
            artifact = db.execute("SELECT manifest_json FROM workflow_artifacts WHERE run_id=? AND kind='input'", (run_id,)).fetchone()[0]
        self.assertEqual(json.loads(original)["integrator"], {"role": "lead", "id": lead["runId"]})
        view, _ = self.finish_owned(board, child)
        taken = board.call("workflow_takeover", {"runId": run_id, "commandId": "console-takeover",
                                                "newHostId": "lead:ordinary-host", "expectedOwnerGeneration": 1,
                                                **self.control(child)})
        self.controls[run_id] = taken["control"]
        self.continue_run(board, taken)
        resumed = self.claim(board, "w2", claim_request_id="claim-current-owner", run_id=run_id)["claim"]
        self.assertEqual(resumed["turn"]["input"]["executionWorkspace"]["integrator"],
                         {"role": "host", "id": "lead:ordinary-host"})
        with board.store.db.read() as db:
            historical = db.execute("SELECT manifest_json FROM workflow_artifacts WHERE run_id=? AND kind='input' ORDER BY created_at LIMIT 1", (run_id,)).fetchone()[0]
        self.assertEqual(historical, artifact)
