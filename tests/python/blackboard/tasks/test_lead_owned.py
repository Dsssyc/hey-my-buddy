"""Lead credentials derive ownership and remain confined to their own graph."""
from __future__ import annotations

import json
from unittest.mock import patch

from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from hey_my_buddy.blackboard.tasks import workflow
from hey_my_buddy.cli.main import _creation_key
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.workspace_manifest import turn_manifest
from support import enable_fixture_buddy


class LeadOwnedTests(WorkflowTestCase):
    def test_duplicate_admission_rechecks_lead_ownership_and_revocation(self):
        board=self.board(max_concurrent=2)
        _,first=self.lead(board,name="first-lead")
        child=self.owned(board,first)
        _,second=self.lead(board,name="second-lead",worker="w2")
        first_scope=board.store.workflow.resolve_credential(first["agentCredential"])
        second_scope=board.store.workflow.resolve_credential(second["agentCredential"])
        with board.store.db.read() as connection:
            task=connection.execute("SELECT * FROM tasks WHERE task_id=?",(child["runId"],)).fetchone()
            key=connection.execute("SELECT creation_key FROM workflow_runs WHERE run_id=?",(child["runId"],)).fetchone()[0]
        admission={"spec":json.loads(task["spec_json"]),"creationKey":key,"lead":first_scope}
        replay=board.store.task_submit({"requestId":task["request_id"]},micro_task=admission)
        self.assertTrue(replay["duplicate"])
        head=board.store.head()
        with self.assertRaises(BoardError) as foreign:
            board.store.task_submit({"requestId":task["request_id"]},micro_task={**admission,"lead":second_scope})
        self.assertEqual(foreign.exception.code,"FORBIDDEN")
        with board.store.db.write() as connection:
            connection.execute("UPDATE agent_credentials SET state='revoked' WHERE credential_id=?",(first_scope["credentialId"],))
        with self.assertRaises(BoardError) as revoked:
            board.store.task_submit({"requestId":task["request_id"]},micro_task=admission)
        self.assertEqual(revoked.exception.code,"UNAUTHORIZED")
        self.assertEqual(board.store.head(),head)

    # Other lifecycle tests reuse these fixture methods with their own TestCase.
    def lead(self, board, name="lead", worker="w1", **extra):
        enable_fixture_buddy(board.store, BUDDY)
        submitted = self.submit(board, request_id=name, cwd=str(self.workdir(name)), access="full",
                                delegationAuthority=True, **extra)
        self.register(board, worker)
        claim = self.claim(board, worker, claim_request_id="claim-" + name, run_id=submitted["runId"])
        return submitted, claim["claim"]

    def owned(self, board, lead_claim, name="owned", **extra):
        lead_id = lead_claim["task"]["taskId"]
        credential = extra.pop("credential", lead_claim["agentCredential"])
        lead = board.call("workflow_get", {"runId": lead_id, "credential": lead_claim["agentCredential"]})
        title = extra.pop("title", name)
        task = extra.pop("task", "Do owned work")
        cwd = extra.pop("cwd", lead["microTask"]["cwd"])
        checkout = extra.pop("checkout", extra.pop("kind", "worktree"))
        access = extra.pop("access", "write")
        buddy = extra.pop("buddy", ":".join(BUDDY.values()))
        namespace = "macro:" + lead["macroTaskId"] if lead.get("macroTaskId") else "project:" + lead["project"]["id"]
        packet = {"creationKey": _creation_key(["adr032-submit-v1", namespace, title, task]),
                  "credential": credential, "cwd": cwd, "title": title, "task": task,
                  "buddy": buddy, "checkout": checkout, "access": access, "expectedSeconds": 600,
                  "inputs": [], **extra}
        if access in ("write", "full"):
            packet.setdefault("writeScope", ["."])
        if access == "full":
            packet.setdefault("accessNote", "Lead fixture full access")
        response = board.call("workflow_submit", packet)
        view = board.call("workflow_get", {"runId": response["runId"]})
        return {**view, **response}

    def finish_owned(self, board, child, worker="w2", **extra):
        self.register(board, worker)
        claim = self.claim(board, worker, claim_request_id="claim-" + child["runId"], run_id=child["runId"])
        self.finish_turn(board, claim, worker_id=worker, **extra)
        return board.call("workflow_get", {"runId": child["runId"]}), claim["claim"]

    def test_lead_submit_derives_macro_and_ownership_without_host_fields(self):
        board = self.board()
        lead, claim = self.lead(board, create_macro={"title": "Grouped work", "goal": "Grouped work"})
        child = self.owned(board, claim)
        self.assertEqual(child["macroTaskId"], lead["macroTaskId"])
        self.assertEqual(child["leadOwnership"]["leadRunId"], lead["runId"])
        self.assertEqual(child["leadOwnership"]["leadTurnId"], claim["turn"]["turnId"])
        self.assertFalse(child["delegationAuthority"])
        self.assertEqual(child["ownedMicroTasks"], [])
        self.assertEqual(board.call("workflow_get", {"runId": lead["runId"]})["ownedMicroTasks"][0]["runId"],
                         child["runId"])
        self.assertEqual(board.call("workflow_get", {"runId": child["runId"],
                                                     "credential": claim["agentCredential"]})["runId"], child["runId"])
        listed = board.call("task_list", {"credential": claim["agentCredential"]})
        self.assertIn(child["runId"], [row["runId"] for row in listed["runs"]])
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_children").fetchone()[0], 0)
            event = db.execute("SELECT actor_role,actor_id FROM events WHERE kind='task.submitted' AND task_id=?",
                               (child["runId"],)).fetchone()
        self.assertEqual(tuple(event), ("lead", lead["runId"]))
        replay = self.owned(board, claim)
        self.assertEqual(replay["runId"], child["runId"])
        self.assertTrue(replay["alreadyExists"])

    def test_lead_cannot_supply_owner_macro_or_another_grant(self):
        board = self.board()
        _lead, claim = self.lead(board)
        for change in ({"macroTaskId": "forged"}, {"hostId": "forged"},
                       {"delegationAuthority": True}, {"owner": "forged"}):
            with self.subTest(change=change), self.assertRaises(BoardError) as raised:
                self.owned(board, claim, "forged-" + next(iter(change)), **change)
            self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

    def test_lead_credential_cannot_read_collection_or_foreign_run(self):
        board = self.board()
        lead, claim = self.lead(board, create_macro={"title": "Grouped work", "goal": "Grouped work"})
        child = self.owned(board, claim)
        foreign = self.submit(board, request_id="foreign", cwd=str(self.workdir("foreign")))
        for selector in ({"cwd": lead["microTask"]["cwd"]}, {"macroTaskId": lead["macroTaskId"]}):
            with self.subTest(selector=selector), self.assertRaises(BoardError) as raised:
                board.call("workflow_get", {**selector, "credential": claim["agentCredential"]})
            self.assertEqual(raised.exception.code, "FORBIDDEN")
        for operation, packet in (("workflow_get", {"runId": foreign["runId"]}),
                                  ("task_get", {"runId": foreign["runId"]}),
                                  ("task_get", {"taskId": foreign["runId"]}),
                                  ("workflow_cancel", {"runId": foreign["runId"]})):
            with self.subTest(operation=operation, packet=packet), self.assertRaises(BoardError) as raised:
                board.call(operation, {**packet, "credential": claim["agentCredential"]})
            self.assertEqual(raised.exception.code, "FORBIDDEN")
        self.assertEqual(board.call("workflow_get", {"runId": child["runId"]})["state"], "executing")

    def test_ungranted_and_invalid_credentials_never_gain_host_actions(self):
        board = self.board()
        ordinary = self.submit(board, request_id="ordinary")
        claim = self.claim(board, run_id=ordinary["runId"])["claim"]
        for operation in workflow.LEAD_OPERATIONS:
            with self.subTest(operation=operation), self.assertRaises(BoardError) as caught:
                board.call(operation, {"runId": ordinary["runId"],
                                       "credential": claim["agentCredential"]})
            self.assertEqual(caught.exception.code, "FORBIDDEN")
        for credential in ("invalid-credential", ""):
            with self.subTest(credential=credential), self.assertRaises(BoardError) as caught:
                board.call("workflow_cancel", {"runId": ordinary["runId"], "credential": credential})
            self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        self.assertEqual(board.call("workflow_get", {"runId": ordinary["runId"]})["state"], "executing")

    def test_lead_creation_key_cannot_replay_host_work_or_leave_a_partial_link(self):
        board = self.board()
        lead, claim = self.lead(board, create_macro={"title": "Grouped work", "goal": "Grouped work"})
        host = self.submit(board, request_id="owned", title="owned", task="Do owned work",
                           macroTaskId=lead["macroTaskId"], cwd=lead["microTask"]["cwd"], kind="worktree")
        with self.assertRaises(BoardError) as caught:
            self.owned(board, claim)
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        self.assertEqual(board.call("workflow_get", {"runId": host["runId"]})["leadOwnership"], None)

        original = board.store._append_event

        def fail_link(connection, kind, **kwargs):
            if kind == "workflow.lead_task_created":
                raise BoardError("CONFLICT", "Simulated admission rollback")
            return original(connection, kind, **kwargs)

        with patch.object(board.store, "_append_event", side_effect=fail_link), self.assertRaises(BoardError):
            self.owned(board, claim, "rolled-back")
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_lead_tasks").fetchone()[0], 0)
            self.assertIsNone(db.execute("SELECT run_id FROM workflow_runs WHERE title='rolled-back'").fetchone())

    def test_lead_can_review_continue_accept_conclude_and_cancel_owned_work(self):
        board = self.board(max_concurrent=4)
        with board.store.db.write() as db:
            db.execute("INSERT INTO model_service_concurrency(harness,provider,model,concurrency_limit)"
                       " VALUES(?,?,?,8)", (BUDDY["harness"], BUDDY["provider"], BUDDY["model"]))
        _, claim = self.lead(board)
        credential = {"credential": claim["agentCredential"]}
        child = self.owned(board, claim)
        self.assertEqual(board.call("task_get", {"runId": child["runId"], **credential})["task"]["runId"],
                         child["runId"])
        board.call("artifact_list", {"runId": child["runId"], **credential})
        board.call("events_read", {"runId": child["runId"], **credential})
        awaiting, _ = self.finish_owned(board, child, disposition="assistance")
        continued = self.decide(board, awaiting, awaiting["activeRequest"]["requestId"],
                                decision="decline", note="Continue after review", **credential)
        self.assertEqual(continued["state"], "executing")
        next_claim = self.claim(board, "w2", claim_request_id="owned-next", run_id=child["runId"])
        self.finish_turn(board, next_claim, worker_id="w2")
        delivered = board.call("workflow_get", {"runId": child["runId"], **credential})
        self.assertEqual(self.accept_run(board, delivered, **credential)["state"], "accepted")

        ended = self.owned(board, claim, "concluded")
        ended, _ = self.finish_owned(board, ended)
        concluded = board.call("workflow_conclude", {"runId": ended["runId"],
                                                      "artifactId": ended["finalArtifactId"],
                                                      "note": "Observed outcome", **credential})
        self.assertEqual(concluded["state"], "concluded")

        cancelled = self.owned(board, claim, "cancelled")
        self.claim(board, "w2", claim_request_id="cancel-running", run_id=cancelled["runId"])
        stopped = board.call("workflow_cancel", {"runId": cancelled["runId"],
                                                 "note": "Lead stopped owned work", **credential})
        self.assertEqual(stopped["state"], "cancelled")
        attempt = board.call("task_get", {"runId": cancelled["runId"]})["task"]["selectedAttempt"]
        self.assertEqual(attempt["stopInitiator"], "lead")
        self.assertTrue(attempt["cancelRequested"])

    def test_child_execution_does_not_inherit_lead_authority(self):
        board = self.board(max_concurrent=4)
        with board.store.db.write() as db:
            db.execute("INSERT INTO model_service_concurrency(harness,provider,model,concurrency_limit)"
                       " VALUES(?,?,?,8)", (BUDDY["harness"], BUDDY["provider"], BUDDY["model"]))
        _, claim = self.lead(board)
        first = self.owned(board, claim, "first")
        second = self.owned(board, claim, "second")
        child_claim = self.claim(board, "w2", claim_request_id="child", run_id=first["runId"])["claim"]
        self.assertFalse(child_claim["turn"]["input"]["context"]["delegationAuthority"])
        for operation in workflow.LEAD_OPERATIONS:
            with self.subTest(operation=operation), self.assertRaises(BoardError) as caught:
                board.call(operation, {"runId": second["runId"],
                                       "credential": child_claim["agentCredential"]})
            self.assertEqual(caught.exception.code, "FORBIDDEN")

    def test_new_lead_turn_can_control_older_owned_work_without_rewriting_its_origin(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        original = child["leadOwnership"]
        self.finish_turn(board, {"claim": claim})
        with self.assertRaises(BoardError) as caught:
            board.call("workflow_get", {"runId": child["runId"], "credential": claim["agentCredential"]})
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        self.continue_run(board, board.call("workflow_get", {"runId": lead["runId"]}))
        replacement = self.claim(board, claim_request_id="lead-next", run_id=lead["runId"])["claim"]
        self.assertNotEqual(replacement["turn"]["turnId"], original["leadTurnId"])
        stopped = board.call("workflow_cancel", {"runId": child["runId"],
                                                 "credential": replacement["agentCredential"]})
        self.assertEqual(stopped["state"], "cancelled")
        self.assertEqual(board.call("workflow_get", {"runId": child["runId"]})["leadOwnership"], original)

    def test_lead_can_settle_helpers_inside_its_owned_micro_task(self):
        board = self.board(max_concurrent=4)
        with board.store.db.write() as db:
            db.execute("INSERT INTO model_service_concurrency(harness,provider,model,concurrency_limit)"
                       " VALUES(?,?,?,8)", (BUDDY["harness"], BUDDY["provider"], BUDDY["model"]))
        _, claim = self.lead(board)
        owned = self.owned(board, claim)
        view, _ = self.finish_owned(board, owned, disposition="assistance")
        parent = self.decide(board, view, view["activeRequest"]["requestId"], helpers=[{
            "title": name, "task": "Help owned work", "cwd": owned["microTask"]["cwd"],
            "checkout": "worktree", "access": "write", "inputs": [], "writeScope": ["."],
        } for name in ("accept-helper", "conclude-helper")], autoContinue=False,
            credential=claim["agentCredential"])
        helpers = []
        for row in parent["children"]:
            helper = board.call("workflow_get", {"runId": row["taskId"]})
            delivered, _ = self.finish_owned(board, helper)
            helpers.append(delivered)
        first, second = helpers
        credential = {"credential": claim["agentCredential"]}
        accepted = board.call("workflow_accept", {"runId": owned["runId"], "targetRunId": first["runId"],
                                                  "artifactId": first["finalArtifactId"], "note": "Reviewed helper output",
                                                  "notRequired": "No separate integration target", **credential})
        self.assertEqual(accepted["runId"], owned["runId"])
        self.assertEqual(board.call("workflow_get", {"runId": first["runId"]})["state"], "accepted")
        concluded = board.call("workflow_conclude", {"runId": owned["runId"], "targetRunId": second["runId"],
                                                     "artifactId": second["finalArtifactId"],
                                                     "note": "Concluded helper work", **credential})
        self.assertEqual(concluded["runId"], owned["runId"])
        self.assertEqual(board.call("workflow_get", {"runId": second["runId"]})["state"], "concluded")

    def test_lead_can_answer_an_owned_helper_proxy_request(self):
        board = self.board()
        _, claim = self.lead(board)
        owned = self.owned(board, claim)
        awaiting, _ = self.finish_owned(board, owned, disposition="assistance")
        parent = self.decide(board, awaiting, awaiting["activeRequest"]["requestId"], helpers=[{
            "title": "proxy-helper", "task": "Help owned work", "cwd": owned["microTask"]["cwd"],
            "checkout": "worktree", "access": "write", "inputs": [], "writeScope": ["."],
        }], autoContinue=False, credential=claim["agentCredential"])
        helper = board.call("workflow_get", {"runId": parent["children"][0]["taskId"]})
        self.finish_owned(board, helper, disposition="assistance")
        proxy = board.call("workflow_get", {"runId": owned["runId"]})["activeRequest"]
        self.assertEqual(proxy["childTaskId"], helper["runId"])
        answered = self.decide(board, parent, proxy["requestId"], decision="decline",
                               credential=claim["agentCredential"])
        self.assertEqual(answered["runId"], owned["runId"])
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT state FROM workflow_requests WHERE request_id=?",
                                        (proxy["requestId"],)).fetchone()[0], "declined")

    def test_lead_cannot_target_a_foreign_helper_through_its_owned_root(self):
        board = self.board(max_concurrent=4)
        with board.store.db.write() as db:
            db.execute("INSERT INTO model_service_concurrency(harness,provider,model,concurrency_limit)"
                       " VALUES(?,?,?,8)", (BUDDY["harness"], BUDDY["provider"], BUDDY["model"]))
        _, claim = self.lead(board)
        owned = self.owned(board, claim)
        foreign = self.submit(board, request_id="foreign-root", cwd=str(self.workdir("foreign-root")),
                              kind="worktree")
        awaiting, _ = self.finish_owned(board, foreign, disposition="assistance")
        created = self.decide(board, awaiting, awaiting["activeRequest"]["requestId"], helpers=[{
            "title": "foreign-helper", "task": "Help foreign work", "cwd": foreign["microTask"]["cwd"],
            "checkout": "worktree", "access": "write", "inputs": [], "writeScope": ["."],
        }], autoContinue=False)
        helper_id = created["children"][0]["taskId"]
        for operation, fields in (("workflow_cancel", {}),
                                  ("workflow_accept", {"artifactId": "foreign-artifact", "note": "Cannot accept",
                                                       "notRequired": "No integration target"}),
                                  ("workflow_conclude", {"note": "Cannot conclude"})):
            with self.subTest(operation=operation), self.assertRaises(BoardError) as caught:
                board.call(operation, {"runId": owned["runId"], "targetRunId": helper_id,
                                       "credential": claim["agentCredential"], **fields})
            self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        self.assertEqual(board.call("workflow_get", {"runId": helper_id})["state"], "executing")

    def test_owned_continuation_keeps_original_frozen_input(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        with board.store.db.read() as db:
            original = dict(db.execute("SELECT * FROM workflow_artifacts WHERE run_id=? AND kind='input'",
                                       (child["runId"],)).fetchone())
        self.assertEqual(turn_manifest(self.directory, claim["turn"]["input"])["integrator"],
                         {"role": "host"})
        delivered, child_claim = self.finish_owned(board, child)
        self.assertEqual(turn_manifest(self.directory, child_claim["turn"]["input"])["integrator"],
                         {"role": "lead", "id": lead["runId"]})
        self.continue_run(board, delivered, credential=claim["agentCredential"])
        next_claim = self.claim(board, "w2", claim_request_id="continued-owned", run_id=child["runId"])["claim"]
        self.assertEqual(turn_manifest(self.directory, next_claim["turn"]["input"])["integrator"],
                         {"role": "lead", "id": lead["runId"]})
        with board.store.db.read() as db:
            historical = dict(db.execute("SELECT * FROM workflow_artifacts WHERE artifact_id=?",
                                         (original["artifact_id"],)).fetchone())
        self.assertEqual(historical, original)

    def test_revoked_lead_credential_fences_prepared_submission(self):
        self.revocation_fence("submit")

    def test_revoked_lead_credential_fences_prepared_continuation(self):
        self.revocation_fence("continue")

    def revocation_fence(self, mutation):
        board = self.board()
        _, claim = self.lead(board)
        child = self.owned(board, claim)
        if mutation == "continue":
            child, _ = self.finish_owned(board, child)
        validate = board.store.workflow._validated_buddy

        def revoke_after_validation(spec):
            result = validate(spec)
            with board.store.db.write() as db:
                db.execute("UPDATE agent_credentials SET state='revoked' WHERE run_id=?",
                           (claim["task"]["taskId"],))
            return result

        with patch.object(board.store.workflow, "_validated_buddy", side_effect=revoke_after_validation):
            with self.assertRaises(BoardError) as caught:
                if mutation == "submit":
                    self.owned(board, claim, "revoked")
                else:
                    self.continue_run(board, child, buddy=BUDDY,
                                      credential=claim["agentCredential"])
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        with board.store.db.read() as db:
            self.assertIsNone(db.execute("SELECT run_id FROM workflow_runs WHERE title='revoked'").fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_continuations").fetchone()[0], 0)
