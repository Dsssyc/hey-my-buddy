"""Model-free end-to-end checks of ADR-032 admission and response identities."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from unittest import mock

from blackboard.tasks.test_workflow import WorkflowTestCase, BUDDY
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.store.db import canonical_json, sha256_text


class CoreContractTests(WorkflowTestCase):
    def test_a_new_continuation_generation_reholds_released_cwd_claims(self):
        board = self.board(max_concurrent=2)
        submitted = self.submit(board)
        first = self.claim(board)["claim"]["attempt"]
        board.store.release_execution(first["attemptId"],reason="execution-process-not-created")
        self.assertEqual(board.store.resource_claims(),[])
        view = board.call("workflow_get",{"runId":submitted["runId"]})
        self.continue_run(board,view)
        second = self.claim(board,claim_request_id="second-generation",run_id=submitted["runId"])["claim"]["attempt"]
        self.assertEqual(second["generation"],first["generation"]+1)
        claims = board.store.resource_claims()
        self.assertEqual(len(claims),1)
        self.assertEqual(claims[0]["attemptId"],second["attemptId"])
        self.assertEqual(claims[0]["state"],"held")
        from pathlib import Path
        cwd = board.call("task_get",{"runId":submitted["runId"]})["task"]["cwd"]
        nested = Path(cwd) / "nested"
        nested.mkdir()
        blocked = board.submit_execution(requestId="blocked-after-continuation",task="Competing write",cwd=str(nested),
                                         adapter="command",argv=["fixture-never-run"])["task"]
        self.assertEqual(blocked["queueReason"],"cwd-overlap")

    def test_old_attempts_cannot_mutate_the_next_continuation_generation(self):
        board = self.board()
        client = board.client()
        for outcome in ("completed","never-started"):
            with self.subTest(outcome=outcome):
                submitted = self.submit(board,request_id=outcome,cwd=str(self.workdir(outcome)))
                first = self.claim(board,run_id=submitted["runId"])
                attempt = first["claim"]["attempt"]
                if outcome == "completed":
                    self.finish_turn(board,first)
                else:
                    board.store.release_execution(attempt["attemptId"],reason="execution-process-not-created")
                self.continue_run(board,board.call("workflow_get",{"runId":submitted["runId"]}))
                second = self.claim(board,claim_request_id="replacement-"+outcome,run_id=submitted["runId"])["claim"]["attempt"]
                self.assertEqual(second["generation"],attempt["generation"]+1)
                head = board.store.head()
                for old_call in (
                    lambda: client.progress(attempt["attemptId"],attempt["generation"],"n"*16),
                    lambda: client.submit_result(attempt["attemptId"],attempt["generation"],"n"*16,
                                                 {"status":"ok","result":{"status":"ok"},"shutdownConfirmed":True}),
                ):
                    with self.assertRaises(BoardError) as refused:
                        old_call()
                    self.assertIn(refused.exception.code,("STALE_GENERATION","UNAUTHORIZED","ATTEMPT_FINISHED"))
                current = board.call("task_get",{"runId":submitted["runId"]})["task"]
                self.assertEqual(current["selectedAttemptId"],second["attemptId"])
                self.assertEqual(current["attemptGeneration"],second["generation"])
                self.assertEqual(board.store.head(),head)

    def test_artifact_continuation_supersedes_the_same_turn_boundary(self):
        board = self.board()
        submitted = self.submit(board)
        self.finish_turn(board,self.claim(board),disposition="assistance")
        view = board.call("workflow_get",{"runId":submitted["runId"]})
        request_id = view["activeRequest"]["requestId"]
        artifact_id = next(item["artifactId"] for item in view["artifacts"] if item["kind"]=="output")
        packet = {"runId":view["runId"],"artifactId":artifact_id,"input":"Continue after the recorded review"}
        first = board.call("workflow_continue",packet)
        self.assertEqual(first["state"],"executing")
        self.assertEqual(first["actions"],["await","inquire","cancel","get"])
        self.assertNotIn("requestId",first)
        with board.store.db.read() as connection:
            request = connection.execute("SELECT * FROM workflow_requests WHERE request_id=?",(request_id,)).fetchone()
            self.assertEqual(request["state"],"superseded")
            self.assertEqual(request["response_id"],first["continuationId"])
        for change in ({"requestId":request_id,"input":"A second answer"},
                       {"requestId":request_id,"decision":"approve"}):
            with self.subTest(change=change),self.assertRaises(BoardError) as refused:
                board.call("workflow_continue",{"runId":view["runId"],**change})
            self.assertEqual(refused.exception.code,"CONFLICT")
        replay = board.call("workflow_continue",packet)
        self.assertEqual(replay["continuationId"],first["continuationId"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_continuations WHERE run_id=?",(view["runId"],)).fetchone()[0],1)

    def test_invalid_conclude_does_not_stop_the_lead_or_owned_work(self):
        from blackboard.tasks.test_lead_owned import LeadOwnedTests
        board = self.board(max_concurrent=2)
        lead,claim = LeadOwnedTests.lead(self,board)
        child = LeadOwnedTests.owned(self,board,claim)
        before = board.store.head()
        with mock.patch.object(board.store.workflow,"_stop_lead_for_conclusion",side_effect=AssertionError("validation must precede stop")):
            with self.assertRaises(BoardError) as refused:
                board.call("workflow_conclude",{"runId":lead["runId"],"artifactId":"missing","note":"Invalid result identity"})
        self.assertEqual(refused.exception.code,"NOT_FOUND")
        self.assertEqual(board.store.head(),before)
        self.assertEqual(board.call("workflow_get",{"runId":lead["runId"]})["state"],"executing")
        self.assertEqual(board.call("workflow_get",{"runId":child["runId"]})["state"],"executing")

    def test_failed_turn_continuation_supersedes_its_system_boundary(self):
        from hey_my_buddy.protocol.reasons import reason
        board = self.board()
        submitted = self.submit(board)
        self.finish_turn(board,self.claim(board),runner_status="failed",exit_code=1,seal=False)
        # The service can record an input-admission boundary on a stopped failed
        # turn, which has no output artifact to answer.
        with board.store.db.write() as connection:
            run = board.store.workflow._run_row(connection,submitted["runId"])
            request_id = board.store.workflow._record_system_request(
                connection,run,reason("workflow.input-rejected",harness="dsh"),{},board.store.now())
        view = board.call("workflow_get",{"runId":submitted["runId"]})
        self.assertEqual(view["state"],"awaiting-host")
        packet = {"runId":submitted["runId"],"turnId":view["currentTurn"]["turnId"],"input":"Use this smaller input"}
        first = board.call("workflow_continue",packet)
        self.assertEqual(first["state"],"executing")
        self.assertEqual(first["actions"],["await","inquire","cancel","get"])
        with self.assertRaises(BoardError) as refused:
            board.call("workflow_continue",{"runId":submitted["runId"],"requestId":request_id,"input":"Second answer"})
        self.assertEqual(refused.exception.code,"CONFLICT")
        self.assertEqual(board.call("workflow_continue",packet)["continuationId"],first["continuationId"])

    def test_helper_artifact_continuation_closes_its_ancestor_proxy(self):
        board = self.board()
        root = self.submit(board)
        self.finish_turn(board,self.claim(board),disposition="assistance")
        waiting = board.call("workflow_get",{"runId":root["runId"]})
        granted = self.decide(board,waiting,waiting["activeRequest"]["requestId"],autoContinue=False,helpers=[{
            "title":"helper","task":"Inspect a dependency","cwd":str(self.workdir("proxy-helper")),
            "buddy":":".join(BUDDY.values()),"checkout":"worktree","access":"read",
            "inputs":[],"expectedSeconds":60,
        }])
        child_id = granted["children"][0]["taskId"]
        self.finish_turn(board,self.claim(board,run_id=child_id),disposition="assistance")
        child = board.call("workflow_get",{"runId":child_id})
        proxy = board.call("workflow_get",{"runId":root["runId"]})["activeRequest"]
        artifact = next(item["artifactId"] for item in child["artifacts"] if item["kind"]=="output")
        result = board.call("workflow_continue",{"runId":root["runId"],"targetRunId":child_id,
                                                 "artifactId":artifact,"input":"Continue the helper"})
        with board.store.db.read() as connection:
            rows = list(connection.execute("SELECT state,response_id FROM workflow_requests WHERE request_id IN (?,?)",
                                           (proxy["requestId"],child["activeRequest"]["requestId"])))
        self.assertEqual(len(rows),2)
        self.assertTrue(all(row["state"]=="superseded" and row["response_id"]==result["continuationId"] for row in rows))
        self.assertIsNone(board.call("workflow_get",{"runId":root["runId"]})["activeRequest"])
        with self.assertRaises(BoardError) as refused:
            board.call("workflow_continue",{"runId":root["runId"],"targetRunId":child_id,
                                            "requestId":proxy["requestId"],"input":"Second answer"})
        self.assertEqual(refused.exception.code,"CONFLICT")

    def test_cancelled_reply_result_can_be_concluded_and_replayed(self):
        board = self.board()
        submitted = self.submit(board)
        self.finish_turn(board,self.claim(board))
        stopped = board.call("workflow_cancel",{"runId":submitted["runId"]})
        self.assertEqual(stopped["state"],"cancelled")
        self.assertIn("conclude",stopped["actions"])
        packet = {"runId":submitted["runId"],"artifactId":stopped["artifactId"],"note":"Close the returned result"}
        concluded = board.call("workflow_conclude",packet)
        self.assertEqual(concluded["state"],"concluded")
        replay = board.call("workflow_conclude",packet)
        self.assertEqual(replay["conclusionId"],concluded["conclusionId"])
        with board.store.db.read() as connection:
            artifact = connection.execute("SELECT * FROM workflow_artifacts WHERE artifact_id=?",(packet["artifactId"],)).fetchone()
            self.assertEqual(artifact["response_kind"],"conclude")
            self.assertEqual(artifact["response_id"],concluded["conclusionId"])

    def test_failed_current_result_can_be_concluded(self):
        board = self.board()
        submitted = self.submit(board)
        def partial_result(result):
            result["partialWorkspaceSeal"] = result.pop("workspaceSeal")
        self.finish_turn(board,self.claim(board),runner_status="failed",exit_code=1,result_mutator=partial_result)
        view = board.call("workflow_get",{"runId":submitted["runId"]})
        self.assertEqual(view["state"],"failed")
        artifact = next(item["artifactId"] for item in view["artifacts"] if item["kind"] in ("output","partial-output"))
        packet = {"runId":submitted["runId"],"artifactId":artifact,"note":"Close the failed turn's fixed result"}
        first = board.call("workflow_conclude",packet)
        replay = board.call("workflow_conclude",packet)
        self.assertEqual(first["state"],"concluded")
        self.assertEqual(replay["conclusionId"],first["conclusionId"])

    def test_decision_replay_normalizes_an_explicit_self_target(self):
        board = self.board()
        submitted = self.submit(board)
        self.finish_turn(board,self.claim(board),disposition="assistance")
        request = board.call("workflow_get",{"runId":submitted["runId"]})["activeRequest"]
        packet = {"runId":submitted["runId"],"requestId":request["requestId"],"decision":"decline"}
        first = board.call("workflow_continue",{**packet,"targetRunId":submitted["runId"]})
        replay = board.call("workflow_continue",packet)
        self.assertTrue(replay["alreadyResponded"])
        self.assertEqual(first["decision"],replay["decision"])

    def test_console_escalation_preserves_the_first_stop_identity(self):
        board = self.board()
        submitted = self.submit(board)
        claim = self.claim(board)
        first = board.call("workflow_cancel",{"runId":submitted["runId"]})
        before = board.call("task_get",{"runId":submitted["runId"]})["task"]["selectedAttempt"]
        escalated = board.console_call("workflow_cancel",{"runId":submitted["runId"]})
        after = board.call("task_get",{"runId":submitted["runId"]})["task"]["selectedAttempt"]
        self.assertEqual(first["stopEventId"],escalated["stopEventId"])
        self.assertEqual(after["stopInitiator"],"console")
        self.assertEqual(after["graceDeadline"],before["graceDeadline"])
        self.assertFalse(after["shutdownConfirmed"])
        head = board.store.head()
        replay = board.console_call("workflow_cancel",{"runId":submitted["runId"]})
        self.assertEqual(replay["stopEventId"],first["stopEventId"])
        self.assertEqual(board.store.head(),head)

    def test_host_helper_mutations_require_the_root_and_current_descendant(self):
        board=self.board()
        root=self.submit(board)
        self.finish_turn(board,self.claim(board),disposition="assistance")
        waiting=board.call("workflow_get",{"runId":root["runId"]})
        granted=self.decide(board,waiting,waiting["activeRequest"]["requestId"],autoContinue=False,helpers=[{
            "title":"helper", "task":"Inspect one isolated dependency", "cwd":str(self.workdir("helper")),
            "buddy":":".join(BUDDY.values()), "checkout":"worktree", "access":"write",
            "inputs":[], "writeScope":["."], "expectedSeconds":60,
        }])
        helper=granted["children"][0]
        child_id=helper["taskId"]
        self.assertNotIn("role",helper)
        self.assertNotIn("integrator",helper)
        for operation,parameters in (
            ("workflow_cancel",{}),
            ("workflow_continue",{"turnId":"missing","input":"Continue the helper"}),
            ("workflow_accept",{"artifactId":"missing","note":"Reviewed","notRequired":"No target"}),
            ("workflow_conclude",{"note":"End the helper"}),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(BoardError) as refused:
                    board.call(operation,{"runId":child_id,**parameters})
                self.assertEqual(refused.exception.code,"UNAUTHORIZED")
        foreign=self.submit(board,request_id="foreign",cwd=str(self.workdir("foreign")))
        with self.assertRaises(BoardError) as refused:
            board.call("workflow_cancel",{"runId":root["runId"],"targetRunId":foreign["runId"]})
        self.assertEqual(refused.exception.code,"UNAUTHORIZED")
        stopped=board.call("workflow_cancel",{"runId":root["runId"],"targetRunId":child_id})
        self.assertEqual(stopped["targetRunId"],child_id)
        self.assertEqual(board.call("workflow_get",{"runId":child_id})["state"],"cancelled")
        self.assertNotEqual(board.call("workflow_get",{"runId":root["runId"]})["state"],"cancelled")

    def test_same_creation_key_returns_original_choices_without_preparation(self):
        board = self.board()
        first = self.submit(board, access="read")
        with board.store.db.read() as connection:
            key = connection.execute("SELECT creation_key FROM workflow_runs WHERE run_id=?", (first["runId"],)).fetchone()[0]
        prepared = len(self.workspace.prepare_calls)
        replay = board.call("workflow_submit", {
            "cwd": first["microTask"]["cwd"], "title": "different ignored title", "task": "different ignored task",
            "macroTaskId": "standalone", "buddy": "nonexistent:provider:model:effort", "checkout": "worktree",
            "access": "full", "accessNote": "A different selection must not change the first admission",
            "writeScope": ["."], "inputs": [], "expectedSeconds": 60, "creationKey": key,
        })
        self.assertEqual(replay["runId"], first["runId"])
        self.assertTrue(replay["alreadyExists"])
        self.assertEqual(replay["access"], "read")
        self.assertEqual(replay["checkout"], "existing")
        self.assertEqual(replay["buddy"], BUDDY)
        self.assertEqual(len(self.workspace.prepare_calls), prepared)

    def test_request_is_consumed_by_only_one_decision(self):
        board = self.board()
        submitted = self.submit(board)
        claim = self.claim(board)
        self.finish_turn(board, claim, disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        request_id = view["activeRequest"]["requestId"]

        def decide(choice):
            try:
                return board.call("workflow_continue", {"runId": view["runId"], "requestId": request_id, "decision": choice})
            except BoardError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(decide, ["approve", "decline"]))
        self.assertEqual(sum(isinstance(value, dict) for value in outcomes), 1)
        self.assertIn("CONFLICT", outcomes)
        winner = next(value for value in outcomes if isinstance(value, dict))
        replay = board.call("workflow_continue", {"runId": view["runId"], "requestId": request_id, "decision": winner["decision"]})
        self.assertTrue(replay["alreadyResponded"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_continuations WHERE run_id=?", (view["runId"],)).fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM commands WHERE kind GLOB 'workflow.*'").fetchone()[0], 0)

    def test_accept_and_continue_cannot_both_consume_one_artifact(self):
        board = self.board()
        submitted = self.submit(board)
        claim = self.claim(board)
        self.finish_turn(board, claim)
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        artifact_id = view["finalArtifactId"]
        accepted = board.call("workflow_accept", {"runId": view["runId"], "artifactId": artifact_id,
            "note": "Reviewed the fixed output", "notRequired": "This isolated fixture has no integration target"})
        self.assertEqual(accepted["state"], "accepted")
        replay = board.call("workflow_accept", {"runId": view["runId"], "artifactId": artifact_id,
            "note": "Reviewed the fixed output", "notRequired": "This isolated fixture has no integration target"})
        self.assertEqual(replay["integrationId"], accepted["integrationId"])
        with self.assertRaises(BoardError) as conflict:
            board.call("workflow_continue", {"runId": view["runId"], "artifactId": artifact_id, "input": "Try again"})
        self.assertEqual(conflict.exception.code, "CONFLICT")

    def test_inquiry_identity_deduplicates_and_reads_without_question(self):
        board = self.board()
        submitted = self.submit(board)
        question = "What evidence remains?"
        first = board.call("inquiry_observe", {"runId": submitted["runId"], "question": question})
        inquiry_id = "inq-" + sha256_text(canonical_json(["adr032-inquiry-v1", submitted["runId"], question]))
        self.assertEqual(first["inquiryId"], inquiry_id)
        duplicate = board.call("inquiry_observe", {"runId": submitted["runId"], "question": question})
        read = board.call("inquiry_observe", {"runId": submitted["runId"], "inquiryId": inquiry_id})
        self.assertTrue(duplicate["inquiry"]["duplicate"])
        self.assertEqual(read["inquiry"]["state"], first["inquiry"]["state"])
        self.assertEqual(read["inquiry"]["questionPreview"], question)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages WHERE task_id=?", (submitted["runId"],)).fetchone()[0], 1)
        with self.assertRaises(BoardError):
            board.call("inquiry_observe", {"runId": submitted["runId"], "inquiryId": "missing"})

    def test_cancel_and_conclude_replay_their_domain_records(self):
        board = self.board()
        submitted = self.submit(board)
        first = board.call("workflow_cancel", {"runId": submitted["runId"], "note": "Stop this queued work"})
        second = board.call("workflow_cancel", {"runId": submitted["runId"], "note": "A retry cannot replace the original request"})
        self.assertEqual(first["stopEventId"], second["stopEventId"])
        concluded = board.call("workflow_conclude", {"runId": submitted["runId"], "note": "Closed after confirmed stop"})
        replay = board.call("workflow_conclude", {"runId": submitted["runId"], "note": "Closed after confirmed stop"})
        self.assertEqual(concluded["conclusionId"], replay["conclusionId"])
        self.assertEqual(replay["state"], "concluded")
