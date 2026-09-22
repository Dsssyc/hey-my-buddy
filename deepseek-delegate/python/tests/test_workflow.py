"""Governed productivity workflow: lifecycle, exclusion, fencing and idempotency.

Every test drives the real store, the real service validation and the real
transactions through the in-process board harness; only the Git-backed workspace
module and the DSH runner process are substituted, exactly like the contract's
module boundary prescribes.
"""
from __future__ import annotations

import json
import os
import unittest

from mock_workspace import MockWorkspace
from support import BoardTestCase

from buddy import workflow as workflow_module
from buddy.errors import BoardError

NONCE = "n" * 16


class WorkflowTestCase(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.workspace = MockWorkspace(self.directory)
        self._previous_workspace = workflow_module._workspace_module
        workflow_module._workspace_module = self.workspace
        self.controls: dict[str, dict] = {}
        self.addCleanup(self._restore_workspace)

    def _restore_workspace(self) -> None:
        workflow_module._workspace_module = self._previous_workspace

    # -- driven helpers -----------------------------------------------------
    def submit(self, board, *, request_id="req-1", host_id="host-1", task="do the thing", cwd=None, **extra):
        params = {
            "requestId": request_id,
            "hostId": host_id,
            "task": task,
            "cwd": cwd or str(self.workdir()),
            "executionWorkspace": {"kind": extra.pop("kind", "existing"), "access": extra.pop("access", "write")},
            **extra,
        }
        response = board.call("workflow_submit", params)
        if response.get("control"):
            self.controls[response["runId"]] = response["control"]
        return response

    def register(self, board, worker_id="w1"):
        return board.call("worker_register", {"workerId": worker_id, "capabilities": ["dsh", "command"]})

    def claim(self, board, worker_id="w1", *, claim_request_id="c1", nonce=NONCE, run_id=None):
        params = {"workerId": worker_id, "claimRequestId": claim_request_id, "nonce": nonce}
        if run_id:
            params["runId"] = run_id
        return board.call("worker_claim", params)

    def turn_record(self, claim_response, *, disposition="completed", outcome=None, session_id="sess-1", **overrides):
        claim = claim_response["claim"]
        turn = claim["turn"]
        if outcome is None:
            if disposition == "completed":
                outcome = {"disposition": "completed", "summary": "did the work", "remaining": [], "decisions": [],
                           "artifacts": [], "request": None}
            else:
                outcome = {
                    "disposition": disposition,
                    "summary": "need the Host",
                    "remaining": ["finish the integration"],
                    "decisions": [],
                    "artifacts": [],
                    "request": {
                        "summary": "please review the approach",
                        "attempted": "tried the obvious fix",
                        "neededWork": ["decide the schema"],
                        "expectedArtifacts": ["a reviewed design"],
                        "acceptance": "the design is approved",
                    },
                }
        record = {
            "version": 1,
            "taskId": claim["attempt"]["taskId"],
            "attemptId": claim["attempt"]["attemptId"],
            "generation": claim["attempt"]["generation"],
            "turnId": turn["turnId"],
            "resumeMode": turn["resumeMode"],
            "previousSessionId": None,
            "sessionId": session_id,
            "promptSha256": "p" * 64,
            "inputSha256": turn["inputSha256"],
            "outcome": outcome,
            "provenance": {
                "tool": "buddy_finish_turn",
                "turnEnd": "completed",
                "flush": "awaited",
                "rootSessionMatched": True,
            },
        }
        record.update(overrides)
        return record

    def finish_turn(
        self,
        board,
        claim_response,
        *,
        worker_id="w1",
        nonce=NONCE,
        disposition="completed",
        outcome=None,
        record=None,
        seal=True,
        shutdown=True,
        runner_status="ok",
        exit_code=0,
        session_id="sess-1",
        result_mutator=None,
    ):
        claim = claim_response["claim"]
        attempt = claim["attempt"]
        record = record if record is not None else self.turn_record(
            claim_response, disposition=disposition, outcome=outcome, session_id=session_id
        )
        result = {
            "status": runner_status,
            "mode": "run",
            "processState": {"shutdownConfirmed": shutdown},
            "logPaths": {"stdout": "/tmp/out", "stderr": "/tmp/err"},
            "turn": record,
            "turnResultPath": "/tmp/turn-output.json",
        }
        if seal:
            manifest = claim["turn"]["input"]["executionWorkspace"]
            result["workspaceSeal"] = self.workspace.seal(
                self.directory, manifest, attempt["taskId"], attempt["attemptId"]
            )
        if result_mutator is not None:
            result_mutator(result)
        return board.call(
            "worker_result",
            {
                "workerId": worker_id,
                "attemptId": attempt["attemptId"],
                "generation": attempt["generation"],
                "nonce": nonce,
                "status": runner_status,
                "result": result,
                "shutdownConfirmed": shutdown,
                "exitCode": exit_code,
            },
        )

    def control(self, view: dict) -> dict:
        return dict(self.controls[view["runId"]])

    def decide(self, board, view, request_id, *, command_id="decide-1", decision="approve", **extra):
        params = {
            "runId": view["runId"],
            "requestId": request_id,
            "commandId": command_id,
            "expectedRevision": view["revision"],
            "decision": decision,
            **self.control(view),
            **extra,
        }
        return board.call("workflow_decide", params)

    def continue_run(self, board, view, *, command_id="continue-1", input="keep going", helper_policy="keep", **extra):
        params = {
            "runId": view["runId"],
            "commandId": command_id,
            "expectedRevision": view["revision"],
            "input": input,
            "helperPolicy": helper_policy,
            **extra,
        }
        params.update(self.control(view))
        return board.call("workflow_continue", params)


class LifecycleTests(WorkflowTestCase):
    def test_yield_approve_helper_continuation_and_acknowledgement(self):
        board = self.board(max_concurrent=1)
        self.register(board)
        submitted = self.submit(board)
        run_id = submitted["runId"]

        # Turn 1: the parent yields for assistance.
        first = self.claim(board)
        self.assertEqual(first["claim"]["turn"]["resumeMode"], "initial")
        yielded = self.finish_turn(board, first, disposition="assistance")
        self.assertEqual(yielded["taskState"], "queued")
        self.assertEqual(yielded["workflow"]["disposition"], "assistance")

        view = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(view["state"], "awaiting-host")
        self.assertTrue(view["awaitingHost"])
        request = view["activeRequest"]
        self.assertEqual(request["kind"], "assistance")
        self.assertEqual(view["status"], "queued")

        # The Host approves one isolated worktree helper.
        approved = self.decide(
            board,
            view,
            request["requestId"],
            helpers=[
                {
                    "requestId": "helper-1",
                    "task": "implement the schema",
                    "cwd": str(self.workdir()),
                    "executionWorkspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        self.assertEqual(approved["state"], "waiting-helpers")
        self.assertEqual(len(approved["children"]), 1)
        child_id = approved["children"][0]["taskId"]

        # Turn 2: the helper runs and completes; one-use authority continues the parent.
        helper_claim = self.claim(board, claim_request_id="c2", run_id=child_id)
        self.assertEqual(helper_claim["claim"]["attempt"]["taskId"], child_id)
        helper_done = self.finish_turn(board, helper_claim)
        self.assertEqual(helper_done["taskState"], "completed")

        parent = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(parent["state"], "executing")
        self.assertEqual(parent["children"][0]["state"], "succeeded")

        # Turn 3: the parent continues with reconstructed-new-session context.
        second = self.claim(board, claim_request_id="c3")
        turn = second["claim"]["turn"]
        self.assertEqual(turn["resumeMode"], "reconstructed-new-session")
        self.assertEqual(turn["input"]["previousSessionId"], "sess-1")
        self.assertEqual(turn["input"]["context"]["helperOutcomes"][0]["taskId"], child_id)
        done = self.finish_turn(board, second, session_id="sess-2")
        self.assertEqual(done["taskState"], "completed")

        delivered = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(delivered["state"], "delivered")
        self.assertTrue(delivered["artifacts"])
        kinds = [row["kind"] for row in delivered["artifacts"]]
        self.assertEqual(kinds[0], "output")
        self.assertIn("input", kinds)

        acknowledged = board.call(
            "workflow_acknowledge",
            {
                "runId": run_id,
                "note": "inspected the sealed diff",
                "verdict": "accepted",
                **self.control(delivered),
            },
        )
        self.assertEqual(acknowledged["state"], "accepted")
        self.assertEqual(acknowledged["task"]["acceptanceVerdict"], "accepted")
        self.assertIsNotNone(acknowledged["finalArtifactId"])

    def test_one_slot_progress_serializes_turns(self):
        board = self.board(max_concurrent=1)
        self.register(board)
        self.register(board, worker_id="w2")
        submitted = self.submit(board)
        run_id = submitted["runId"]
        first = self.claim(board)
        self.finish_turn(board, first, disposition="attention")
        view = board.call("workflow_get", {"runId": run_id})
        approved = self.decide(
            board,
            view,
            view["activeRequest"]["requestId"],
            helpers=[
                {
                    "requestId": "helper-1",
                    "task": "helper one",
                    "cwd": str(self.workdir("other")),
                    "executionWorkspace": {"kind": "worktree", "access": "write"},
                },
                {
                    "requestId": "helper-2",
                    "task": "helper two",
                    "cwd": str(self.workdir("third")),
                    "executionWorkspace": {"kind": "worktree", "access": "write"},
                },
            ],
        )
        children = [child["taskId"] for child in approved["children"]]
        # With one slot, the first helper claims, the second cannot; the logical run
        # makes progress one attempt at a time and never starts two turns at once.
        first_helper = self.claim(board, claim_request_id="c2", run_id=children[0])
        self.assertIsNotNone(first_helper["claim"])
        busy = self.claim(board, worker_id="w2", claim_request_id="c3", run_id=children[1])
        self.assertIsNone(busy["claim"])
        self.assertEqual(busy["reason"], "capacity")
        self.finish_turn(board, first_helper)
        second_helper = self.claim(board, worker_id="w2", claim_request_id="c4", run_id=children[1])
        self.assertEqual(second_helper["claim"]["attempt"]["taskId"], children[1])

    def test_replayed_turn_approval_and_result_create_no_duplicate(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        run_id = submitted["runId"]
        first = self.claim(board)
        yielded = self.finish_turn(board, first, disposition="assistance")
        view = board.call("workflow_get", {"runId": run_id})
        request_id = view["activeRequest"]["requestId"]
        params = {
            "runId": run_id,
            "requestId": request_id,
            "commandId": "decide-once",
            "expectedRevision": view["revision"],
            "decision": "approve",
            "helpers": [],
            **self.control(view),
        }
        first_decide = board.call("workflow_decide", params)
        replay = board.call("workflow_decide", params)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["state"], first_decide["state"])
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_decide", {**params, "decision": "decline"})
        self.assertEqual(raised.exception.code, "CONFLICT")

        # Approval with no helpers recorded one-use auto continuation: the parent is
        # requeued exactly once, and replaying the turn result adds nothing.
        self.assertEqual(first_decide["state"], "executing")
        replay_result = self.finish_turn(board, first, disposition="assistance")
        self.assertTrue(replay_result["duplicate"])
        after = board.call("workflow_get", {"runId": run_id, "includeAudit": True})
        self.assertEqual([turn["turnIndex"] for turn in after["audit"]["turns"]], [1])
        self.assertEqual(len(after["turns"]), 1)

    def test_decline_continues_parent_with_reason(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        run_id = submitted["runId"]
        first = self.claim(board)
        self.finish_turn(board, first, disposition="assistance")
        view = board.call("workflow_get", {"runId": run_id})
        declined = self.decide(
            board,
            view,
            view["activeRequest"]["requestId"],
            decision="decline",
            reason="the plan is not acceptable; use the existing schema",
        )
        self.assertEqual(declined["state"], "executing")
        self.assertIsNone(declined["activeRequest"])
        second = self.claim(board, claim_request_id="c2")
        context = second["claim"]["turn"]["input"]["context"]
        self.assertEqual(context["continuation"]["reason"], "the plan is not acceptable; use the existing schema")
        self.assertEqual(context["continuation"]["authorizedBy"], "auto")

    def test_decline_without_auto_continue_waits_for_host(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        run_id = submitted["runId"]
        self.finish_turn(board, self.claim(board), disposition="attention")
        view = board.call("workflow_get", {"runId": run_id})
        declined = self.decide(
            board,
            view,
            view["activeRequest"]["requestId"],
            decision="decline",
            autoContinue=False,
        )
        self.assertEqual(declined["state"], "awaiting-host")
        idle = self.claim(board, claim_request_id="c2")
        self.assertIsNone(idle["claim"])
        self.assertEqual(idle["reason"], "awaiting-host")

    def test_failed_helper_feeds_parent(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        first = self.claim(board)
        self.finish_turn(board, first, disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        approved = self.decide(
            board,
            view,
            view["activeRequest"]["requestId"],
            helpers=[
                {
                    "requestId": "helper-1",
                    "task": "helper one",
                    "cwd": str(self.workdir("helper")),
                    "executionWorkspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        child = approved["children"][0]["taskId"]
        helper_claim = self.claim(board, claim_request_id="c2", run_id=child)
        failed = self.finish_turn(board, helper_claim, runner_status="failed", exit_code=1, seal=False)
        self.assertEqual(failed["taskState"], "failed")
        parent = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(parent["children"][0]["state"], "failed")
        self.assertEqual(parent["state"], "executing")
        # The parent's next turn still receives the failed helper's outcome.
        second = self.claim(board, claim_request_id="c3")
        outcomes = second["claim"]["turn"]["input"]["context"]["helperOutcomes"]
        self.assertEqual(outcomes[0]["state"], "failed")

    def test_multiple_helpers_fan_in_with_one_integrator(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        helpers = [
            {
                "requestId": f"helper-{index}",
                "task": f"helper {index}",
                "cwd": str(self.workdir(f"h{index}")),
                "executionWorkspace": {"kind": "worktree", "access": "write"},
                "integrator": index == 2,
            }
            for index in (1, 2)
        ]
        approved = self.decide(board, view, view["activeRequest"]["requestId"], helpers=helpers)
        children = [child["taskId"] for child in approved["children"]]
        self.assertEqual(sorted(child["integrator"] for child in approved["children"]), [False, True])
        # The first helper yields attention; the Host sees it while the sibling runs.
        first_helper = self.claim(board, claim_request_id="c2", run_id=children[0])
        self.finish_turn(board, first_helper, disposition="attention")
        parent = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(parent["state"], "awaiting-host")
        self.assertEqual(parent["activeRequest"]["kind"], "helper-attention")
        states = {child["taskId"]: child["state"] for child in parent["children"]}
        self.assertEqual(states[children[0]], "attention")
        self.assertEqual(states[children[1]], "active")

    def test_helper_attention_surfaces_before_success(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        approved = self.decide(
            board,
            view,
            view["activeRequest"]["requestId"],
            helpers=[
                {
                    "requestId": "helper-1",
                    "task": "helper one",
                    "cwd": str(self.workdir("h")),
                    "executionWorkspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        child = approved["children"][0]["taskId"]
        helper = self.claim(board, claim_request_id="c2", run_id=child)
        self.finish_turn(board, helper, disposition="assistance")
        parent = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(parent["state"], "awaiting-host")
        self.assertEqual(parent["activeRequest"]["childTaskId"], child)

    def test_manual_continue_invalidates_auto_and_cancels_helpers(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        approved = self.decide(
            board,
            view,
            view["activeRequest"]["requestId"],
            helpers=[
                {
                    "requestId": "helper-1",
                    "task": "helper one",
                    "cwd": str(self.workdir("h")),
                    "executionWorkspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        self.assertEqual(approved["state"], "waiting-helpers")
        continued = self.continue_run(board, approved, helper_policy="cancel", input="stop waiting for the helper")
        self.assertEqual(continued["state"], "executing")
        self.assertEqual(continued["helperPolicy"], "cancel")
        audit = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        auto = [item for item in audit["audit"]["continuations"] if item["authorizedBy"] == "auto"]
        self.assertEqual([item["state"] for item in auto], ["invalidated"])
        # The cancelled helper can never requeue the parent later.
        child = approved["children"][0]["taskId"]
        child_task = board.call("task_get", {"runId": child})["task"]
        self.assertIn(child_task["state"], ("queued", "cancelled"))
        next_turn = self.claim(board, claim_request_id="c2")
        self.assertEqual(next_turn["claim"]["turn"]["turnIndex"], 2)

    def test_owner_takeover_fences_old_control(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        run_id = submitted["runId"]
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": run_id})
        old_control = self.control(view)
        taken = board.call(
            "workflow_takeover",
            {
                "runId": run_id,
                "commandId": "takeover-1",
                "expectedOwnerGeneration": view["ownerGeneration"],
                "newHostId": "host-2",
                **old_control,
            },
        )
        self.assertEqual(taken["ownerGeneration"], 2)
        self.assertEqual(taken["hostId"], "host-2")
        self.assertNotEqual(taken["control"]["controlToken"], old_control["controlToken"])
        with self.assertRaises(BoardError) as raised:
            board.call(
                "workflow_decide",
                {
                    "runId": run_id,
                    "requestId": view["activeRequest"]["requestId"],
                    "commandId": "late-decision",
                    "expectedRevision": taken["revision"],
                    "decision": "decline",
                    **old_control,
                },
            )
        self.assertEqual(raised.exception.code, "STALE_GENERATION")
        # The new owner's capability works.
        decided = board.call(
            "workflow_decide",
            {
                "runId": run_id,
                "requestId": view["activeRequest"]["requestId"],
                "commandId": "new-decision",
                "expectedRevision": taken["revision"],
                "decision": "decline",
                "autoContinue": False,
                **taken["control"],
            },
        )
        self.assertEqual(decided["state"], "awaiting-host")
        self.assertEqual(decided["hostId"], "host-2")

    def test_cancel_fences_late_helper_completion(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        run_id = submitted["runId"]
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": run_id})
        approved = self.decide(
            board,
            view,
            view["activeRequest"]["requestId"],
            helpers=[
                {
                    "requestId": "helper-1",
                    "task": "helper one",
                    "cwd": str(self.workdir("h")),
                    "executionWorkspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        child = approved["children"][0]["taskId"]
        helper_claim = self.claim(board, claim_request_id="c2", run_id=child)
        cancelled = board.call(
            "workflow_cancel", {"runId": run_id, "reason": "stop", **self.control(approved)}
        )
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertTrue(cancelled["cancelled"])
        self.assertNotEqual(cancelled["status"], "completed")
        child_view = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(child_view["children"][0]["state"], "cancelled")
        # A helper completion that arrives after the cancel is recorded honestly and
        # never revives the cancelled parent.
        late = self.finish_turn(board, helper_claim)
        parent = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(parent["state"], "cancelled")

    def test_agent_credential_cannot_act_as_host(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        run_id = submitted["runId"]
        claim = self.claim(board)
        credential = claim["claim"]["agentCredential"]
        self.assertTrue(credential)
        allowed = board.call("workflow_get", {"runId": run_id, "credential": credential})
        self.assertEqual(allowed["runId"], run_id)
        for operation, params in (
            ("task_submit", {"requestId": "forged", "task": "x", "cwd": str(self.workdir("evil"))}),
            ("workflow_submit", {"requestId": "forged", "hostId": "host-1", "task": "x", "cwd": str(self.workdir("evil"))}),
            ("evaluation_write_begin", {"requestId": "forged", "expectedRevision": 0}),
            ("worker_list", {}),
        ):
            with self.assertRaises(BoardError) as raised:
                board.call(operation, {**params, "credential": credential})
            self.assertEqual(raised.exception.code, "FORBIDDEN", operation)
        # A forged hostId does not help: the Host decision still needs control.
        with self.assertRaises(BoardError) as raised:
            board.call(
                "workflow_decide",
                {
                    "runId": run_id,
                    "requestId": "req-x",
                    "commandId": "forged",
                    "expectedRevision": 1,
                    "decision": "approve",
                    "hostId": "host-1",
                    "credential": credential,
                },
            )
        self.assertEqual(raised.exception.code, "FORBIDDEN")
        # Another run is out of scope.
        other = self.submit(board, request_id="req-2", cwd=str(self.workdir("other")))
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_get", {"runId": other["runId"], "credential": credential})
        self.assertEqual(raised.exception.code, "FORBIDDEN")
        # An invalid scoped credential never falls back to the administrator token.
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_get", {"runId": run_id, "credential": "forged-token"})
        self.assertEqual(raised.exception.code, "UNAUTHORIZED")

    def test_governed_task_ops_require_host_control(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        run_id = submitted["runId"]
        with self.assertRaises(BoardError) as raised:
            board.call("task_cancel", {"runId": run_id, "reason": "legacy path"})
        self.assertEqual(raised.exception.code, "UNAUTHORIZED")
        cancelled = board.call(
            "task_cancel", {"runId": run_id, "reason": "host stop", **self.control(submitted)}
        )
        self.assertEqual(cancelled["task"]["state"], "cancelled")
        # A legacy (non-governed) task keeps its previous behavior.
        legacy = board.call(
            "task_submit", {"requestId": "legacy-1", "task": "plain", "cwd": str(self.workdir("legacy"))}
        )
        plain = board.call("task_cancel", {"runId": legacy["task"]["runId"]})
        self.assertEqual(plain["task"]["state"], "cancelled")


class WorkspaceTests(WorkflowTestCase):
    def test_sibling_cwd_paths_share_one_writer_exclusion(self):
        board = self.board()
        root = self.workdir("repo")
        (root / ".git").mkdir(exist_ok=True)
        sibling = root / "sub"
        sibling.mkdir()
        self.submit(board, request_id="req-1", cwd=str(root))
        with self.assertRaises(BoardError) as raised:
            self.submit(board, request_id="req-2", cwd=str(sibling))
        self.assertEqual(raised.exception.code, "PREPARATION_CONFLICT")
        self.assertEqual(raised.exception.details["checkoutId"], f"checkout:{os.path.realpath(root)}")

    def test_isolated_worktrees_allow_parallel_writers(self):
        board = self.board()
        source = self.workdir("source")
        (source / ".git").mkdir(exist_ok=True)
        first = self.submit(board, request_id="req-1", cwd=str(source), kind="worktree")
        second = self.submit(board, request_id="req-2", cwd=str(source), kind="worktree")
        self.assertNotEqual(
            first["workspace"]["checkoutId"],
            second["workspace"]["checkoutId"],
        )
        self.assertEqual(first["workspace"]["repositoryId"], second["workspace"]["repositoryId"])

    def test_idempotent_submit_recovers_snapshot_before_inspecting_source(self):
        board = self.board()
        first = self.submit(board, request_id="req-1")
        before = len(self.workspace.prepare_calls)
        self.workspace.dirty = True
        replay = self.submit(board, request_id="req-1")
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["workspace"]["manifestSha256"], first["workspace"]["manifestSha256"])
        self.assertEqual(len(self.workspace.prepare_calls), before)
        with self.assertRaises(BoardError) as raised:
            self.submit(board, request_id="req-1", task="a different goal")
        self.assertEqual(raised.exception.code, "CONFLICT")

    def test_stopped_execution_retains_logical_workspace_until_acknowledgement(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        run_id = submitted["runId"]
        self.finish_turn(board, self.claim(board))
        delivered = board.call("workflow_get", {"runId": run_id})
        audit = board.call("workflow_get", {"runId": run_id, "includeAudit": True})
        reservation = audit["audit"]["reservations"][0]
        self.assertEqual(reservation["state"], "held")
        board.call(
            "workflow_acknowledge",
            {"runId": run_id, "note": "ok", "verdict": "accepted", **self.control(delivered)},
        )
        released = board.call("workflow_get", {"runId": run_id, "includeAudit": True})
        self.assertEqual(released["audit"]["reservations"][0]["state"], "released")

    def test_sequential_helper_transfer_returns_ownership_after_settle(self):
        board = self.board()
        self.register(board)
        parent_cwd = self.workdir("shared")
        (parent_cwd / ".git").mkdir(exist_ok=True)
        submitted = self.submit(board, cwd=str(parent_cwd))
        run_id = submitted["runId"]
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": run_id})
        # The parent is stopped, so the Host may transfer its checkout to one helper.
        approved = self.decide(
            board,
            view,
            view["activeRequest"]["requestId"],
            helpers=[
                {
                    "requestId": "helper-1",
                    "task": "work on the parent checkout",
                    "cwd": str(parent_cwd),
                    "executionWorkspace": {"kind": "existing", "access": "write"},
                }
            ],
        )
        child = approved["children"][0]["taskId"]
        audit = board.call("workflow_get", {"runId": run_id, "includeAudit": True})
        states = {(row["holderTaskId"], row["state"]) for row in audit["audit"]["reservations"]}
        self.assertIn((run_id, "transferred"), states)
        self.assertIn((child, "held"), states)
        # The helper stops with confirmed shutdown; ownership returns to the parent
        # before its continuation turn.
        self.finish_turn(board, self.claim(board, claim_request_id="c2", run_id=child))
        after = board.call("workflow_get", {"runId": run_id, "includeAudit": True})
        states = {(row["holderTaskId"], row["state"]) for row in after["audit"]["reservations"]}
        self.assertIn((run_id, "held"), states)
        self.assertIn((child, "released"), states)
        self.assertEqual(after["state"], "executing")

    def test_unverifiable_output_seal_fails_honestly(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        claim = self.claim(board)

        def bad_seal(result):
            result["workspaceSeal"] = {"manifestSha256": "not-a-hash"}

        failed = self.finish_turn(board, claim, seal=False, result_mutator=bad_seal)
        self.assertEqual(failed["taskState"], "failed")
        self.assertIn("governed", (failed["attempt"]["error"] or "").lower())
        audit = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        self.assertEqual([row for row in audit["artifacts"] if row["kind"] == "output"], [])


class IdempotencyTests(WorkflowTestCase):
    def test_changed_turn_identity_is_rejected(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        claim = self.claim(board)
        record = self.turn_record(claim)
        record["attemptId"] = "different-attempt"
        result = self.finish_turn(board, claim, record=record, seal=False)
        self.assertEqual(result["workflow"]["accepted"], False)
        self.assertEqual(result["taskState"], "failed")
        run = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(run["state"], "failed")

    def test_missing_turn_record_fails_honestly(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        claim = self.claim(board)
        result = self.finish_turn(board, claim, record={}, seal=False)
        self.assertEqual(result["workflow"]["accepted"], False)
        self.assertEqual(result["taskState"], "failed")
        run = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(run["state"], "failed")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class SubmissionAuthorityTests(WorkflowTestCase):
    def _submit(self, board, **extra):
        params = {
            "requestId": "req-1",
            "hostId": "host-1",
            "task": "do the thing",
            "cwd": str(self.workdir()),
            "submissionToken": "s" * 32,
            "executionWorkspace": {"kind": "existing", "access": "write"},
            **extra,
        }
        return board.call("workflow_submit", params)

    def test_control_requires_the_private_submission_capability(self):
        board = self.board()
        first = self._submit(board)
        self.assertTrue(first["control"]["controlToken"])
        replay = self._submit(board)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["control"]["controlToken"], first["control"]["controlToken"])
        # The hostId label alone can never recover control.
        other = self._submit(board, hostId="host-1", submissionToken="x" * 32)
        self.assertNotIn("control", other)
        self.assertFalse(other["controlAvailable"])
        # A changed execution workspace is different input, not a silent reuse.
        with self.assertRaises(BoardError) as raised:
            self._submit(board, executionWorkspace={"kind": "worktree", "access": "write"})
        self.assertEqual(raised.exception.code, "CONFLICT")

    def test_replay_never_upgrades_to_a_later_owner_generation(self):
        board = self.board()
        first = self._submit(board)
        run_id = first["runId"]
        taken = board.call(
            "workflow_takeover",
            {
                "runId": run_id,
                "commandId": "takeover-1",
                "expectedOwnerGeneration": 1,
                "newHostId": "host-2",
                **first["control"],
            },
        )
        self.assertEqual(taken["ownerGeneration"], 2)
        replay = self._submit(board)
        self.assertTrue(replay["duplicate"])
        self.assertNotIn("control", replay)
        self.assertFalse(replay["controlAvailable"])

    def test_minimum_submission_token_length(self):
        board = self.board()
        with self.assertRaises(BoardError) as raised:
            self._submit(board, submissionToken="short")
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")


class DecidePrecheckTests(WorkflowTestCase):
    def _yielded(self, board):
        self.register(board)
        submitted = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        return submitted, view

    def test_unauthorized_decide_never_prepares_a_workspace(self):
        board = self.board()
        _submitted, view = self._yielded(board)
        before = len(self.workspace.prepare_calls)
        with self.assertRaises(BoardError) as raised:
            board.call(
                "workflow_decide",
                {
                    "runId": view["runId"],
                    "requestId": view["activeRequest"]["requestId"],
                    "commandId": "decide-1",
                    "expectedRevision": view["revision"],
                    "decision": "approve",
                    "helpers": [
                        {
                            "requestId": "helper-1",
                            "task": "work",
                            "cwd": str(self.workdir("h")),
                            "executionWorkspace": {"kind": "worktree", "access": "write"},
                        }
                    ],
                },
            )
        self.assertEqual(raised.exception.code, "UNAUTHORIZED")
        self.assertEqual(len(self.workspace.prepare_calls), before)

    def test_stale_revision_never_prepares_a_workspace(self):
        board = self.board()
        submitted, view = self._yielded(board)
        before = len(self.workspace.prepare_calls)
        with self.assertRaises(BoardError) as raised:
            board.call(
                "workflow_decide",
                {
                    "runId": view["runId"],
                    "requestId": view["activeRequest"]["requestId"],
                    "commandId": "decide-1",
                    "expectedRevision": view["revision"] + 5,
                    "decision": "approve",
                    "helpers": [],
                    **self.control(submitted),
                },
            )
        self.assertEqual(raised.exception.code, "REVISION_CONFLICT")
        self.assertEqual(len(self.workspace.prepare_calls), before)

    def test_replayed_decide_does_not_prepare_again(self):
        board = self.board()
        submitted, view = self._yielded(board)
        params = {
            "runId": view["runId"],
            "requestId": view["activeRequest"]["requestId"],
            "commandId": "decide-once",
            "expectedRevision": view["revision"],
            "decision": "approve",
            "helpers": [
                {
                    "requestId": "helper-1",
                    "task": "work",
                    "cwd": str(self.workdir("h")),
                    "executionWorkspace": {"kind": "worktree", "access": "write"},
                }
            ],
            **self.control(submitted),
        }
        board.call("workflow_decide", params)
        after_first = len(self.workspace.prepare_calls)
        replay = board.call("workflow_decide", params)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(len(self.workspace.prepare_calls), after_first)


class ContinuationPolicyTests(WorkflowTestCase):
    def test_continue_requires_an_explicit_helper_policy(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        approved = self.decide(
            board,
            view,
            view["activeRequest"]["requestId"],
            helpers=[
                {
                    "requestId": "helper-1",
                    "task": "work",
                    "cwd": str(self.workdir("h")),
                    "executionWorkspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        with self.assertRaises(BoardError) as raised:
            board.call(
                "workflow_continue",
                {
                    "runId": submitted["runId"],
                    "commandId": "continue-1",
                    "expectedRevision": approved["revision"],
                    "input": "go on",
                    **self.control(submitted),
                },
            )
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        # Without live helpers the default is explicit and safe.
        idle = self.board()
        self.register(idle, worker_id="w2")
        submitted_idle = self.submit(idle, request_id="req-idle", cwd=str(self.workdir("idle")))
        self.finish_turn(
            idle,
            self.claim(
                idle,
                worker_id="w2",
                claim_request_id="idle-c1",
                nonce="z" * 16,
                run_id=submitted_idle["runId"],
            ),
            disposition="assistance",
            worker_id="w2",
            nonce="z" * 16,
        )
        view_idle = idle.call("workflow_get", {"runId": submitted_idle["runId"]})
        decided = self.decide(
            idle,
            view_idle,
            view_idle["activeRequest"]["requestId"],
            command_id="idle-decide-1",
            decision="decline",
        )
        continued = idle.call(
            "workflow_continue",
            {
                "runId": submitted_idle["runId"],
                "commandId": "continue-2",
                "expectedRevision": decided["revision"],
                "input": "go on",
                **self.control(submitted_idle),
            },
        )
        self.assertEqual(continued["helperPolicy"], "keep")


class ArtifactIdentityTests(WorkflowTestCase):
    def test_output_is_bound_to_the_sealed_snapshot(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        claim = self.claim(board)
        manifest = claim["claim"]["turn"]["input"]["executionWorkspace"]
        self.finish_turn(board, claim)
        view = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        output = [row for row in view["artifacts"] if row["kind"] == "output"][0]
        seal = self.workspace.seal(self.directory, manifest, submitted["runId"], claim["claim"]["attempt"]["attemptId"])
        self.assertEqual(output["manifestSha256"], seal["snapshotSha256"])
        self.assertNotEqual(output["manifestSha256"], manifest["manifestSha256"])
        pinned = [
            turn["sealedArtifacts"][0]["snapshotSha256"]
            for turn in view["audit"]["turns"]
            if turn["sealedArtifacts"]
        ]
        self.assertEqual(pinned, [seal["snapshotSha256"]])


class AgentScopeSelectorTests(WorkflowTestCase):
    def test_cross_run_selectors_are_rejected(self):
        board = self.board()
        self.register(board)
        first = self.submit(board, request_id="req-1")
        second = self.submit(board, request_id="req-2", cwd=str(self.workdir("other")))
        claim = self.claim(board)
        credential = claim["claim"]["agentCredential"]
        attempt_id = claim["claim"]["attempt"]["attemptId"]
        other_task = board.call("task_get", {"runId": second["runId"]})["task"]
        with self.assertRaises(BoardError) as raised:
            board.call("artifact_list", {"attemptId": attempt_id, "runId": second["runId"], "credential": credential})
        self.assertEqual(raised.exception.code, "FORBIDDEN")
        with self.assertRaises(BoardError) as raised:
            board.call(
                "message_get", {"inquiryId": "q1", "runId": second["runId"], "credential": credential}
            )
        self.assertEqual(raised.exception.code, "FORBIDDEN")
        with self.assertRaises(BoardError) as raised:
            board.call("task_get", {"requestId": "req-2", "credential": credential})
        self.assertEqual(raised.exception.code, "FORBIDDEN")
        own = board.call("task_get", {"credential": credential})
        self.assertEqual(own["task"]["runId"], first["runId"])
        self.assertEqual(other_task["runId"], second["runId"])

    def test_own_message_selector_is_defaulted(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        claim = self.claim(board)
        credential = claim["claim"]["agentCredential"]
        listed = board.call("message_list", {"credential": credential})
        self.assertIsInstance(listed.get("messages"), list)
        self.assertEqual(submitted["runId"], claim["claim"]["attempt"]["taskId"])


class BoundedViewTests(WorkflowTestCase):
    def test_compact_reports_counts_and_truncation(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(set(view["truncated"]), {"turns", "requests", "children", "artifacts"})
        self.assertEqual(view["counts"]["turns"], 1)
        self.assertEqual(view["counts"]["artifacts"], 2)
        self.assertEqual(view["truncated"]["turns"], 0)


class NestedSpecTests(WorkflowTestCase):
    def test_nested_spec_shape_is_accepted_and_must_agree(self):
        board = self.board()
        nested = {
            "requestId": "req-nested",
            "hostId": "host-1",
            "spec": {"task": "nested task", "cwd": str(self.workdir("nested")), "timeoutSeconds": 600},
            "executionWorkspace": {"kind": "worktree", "access": "write"},
            "submissionToken": "n" * 32,
        }
        created = board.call("workflow_submit", nested)
        self.assertEqual(created["goal"]["task"], "nested task")
        self.assertEqual(created["task"]["timeoutSeconds"], 600)
        with self.assertRaises(BoardError) as raised:
            board.call(
                "workflow_submit",
                {
                    "requestId": "req-conflict",
                    "hostId": "host-1",
                    "task": "flat task",
                    "cwd": str(self.workdir("nested")),
                    "spec": {"task": "different task"},
                },
            )
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")


class LateCancellationTests(WorkflowTestCase):
    def test_late_helper_completion_keeps_its_artifact_after_parent_cancel(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        approved = self.decide(
            board,
            view,
            view["activeRequest"]["requestId"],
            helpers=[
                {
                    "requestId": "helper-1",
                    "task": "work",
                    "cwd": str(self.workdir("h")),
                    "executionWorkspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        child = approved["children"][0]["taskId"]
        helper_claim = self.claim(board, claim_request_id="c2", run_id=child)
        board.call("workflow_cancel", {"runId": submitted["runId"], "reason": "stop", **self.control(approved)})
        late = self.finish_turn(board, helper_claim)
        self.assertEqual(late["taskState"], "completed")
        child_view = board.call("workflow_get", {"runId": child})
        self.assertEqual(child_view["state"], "delivered")
        self.assertEqual([row["kind"] for row in child_view["artifacts"]], ["output", "input"])
        parent = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(parent["state"], "cancelled")


class ContinuationWorkspaceTests(WorkflowTestCase):
    def test_continuation_prepares_a_new_manifest_from_the_sealed_output(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        first = self.claim(board)
        first_manifest = first["claim"]["turn"]["input"]["executionWorkspace"]
        self.finish_turn(board, first, disposition="assistance", session_id="sess-1")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        decided = self.decide(board, view, view["activeRequest"]["requestId"], decision="decline")
        second = self.claim(board, claim_request_id="c2")
        turn_input = second["claim"]["turn"]["input"]
        effective = turn_input["executionWorkspace"]
        self.assertNotEqual(effective["manifestSha256"], first_manifest["manifestSha256"])
        self.assertEqual(effective["baseCommit"], "d" * 40)
        self.assertEqual(
            set(turn_input),
            {
                "version",
                "taskId",
                "attemptId",
                "generation",
                "turnId",
                "resumeMode",
                "previousSessionId",
                "context",
                "executionWorkspace",
            },
        )
        self.assertNotIn("workspaceMode", turn_input)
        self.assertEqual(turn_input["previousSessionId"], "sess-1")


class HelperAttentionResolutionTests(WorkflowTestCase):
    def _attention_with_helper(self, board, request_id="req-1"):
        self.register(board)
        submitted = self.submit(board, request_id=request_id)
        self.finish_turn(board, self.claim(board, claim_request_id=f"{request_id}-c1"), disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        approved = self.decide(
            board,
            view,
            view["activeRequest"]["requestId"],
            command_id=f"{request_id}-decide-1",
            helpers=[
                {
                    "requestId": f"{request_id}-helper-1",
                    "task": "helper work",
                    "cwd": str(self.workdir(f"{request_id}-h")),
                    "executionWorkspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        child = approved["children"][0]["taskId"]
        helper = self.claim(board, claim_request_id=f"{request_id}-c2", run_id=child)
        self.finish_turn(board, helper, disposition="attention", session_id="sess-helper")
        return submitted, approved, child

    def test_parent_authority_alone_resumes_a_yielded_helper(self):
        board = self.board()
        submitted, approved, child = self._attention_with_helper(board)
        parent = board.call("workflow_get", {"runId": submitted["runId"]})
        request = parent["activeRequest"]
        self.assertEqual(request["kind"], "helper-attention")
        self.assertEqual(request["childTaskId"], child)
        # A yielded helper keeps its workspace ownership until a deliberate handoff.
        audit = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        held = [row for row in audit["audit"]["reservations"] if row["holderTaskId"] == child]
        self.assertEqual([row["state"] for row in held], ["held"])
        # The Host answers using ONLY the parent control it already saved.
        resolved = self.decide(
            board,
            parent,
            request["requestId"],
            command_id="parent-answer-1",
            reason="use the narrower approach",
        )
        self.assertEqual(resolved["state"], "waiting-helpers")
        self.assertIsNone(resolved["activeRequest"])
        # The helper gets a fresh reconstructed turn carrying the decision.
        second = self.claim(board, claim_request_id="helper-c3", run_id=child)
        turn = second["claim"]["turn"]
        self.assertEqual(turn["turnIndex"], 2)
        self.assertEqual(turn["resumeMode"], "reconstructed-new-session")
        self.assertIn("use the narrower approach", json.dumps(turn["input"]["context"]))
        self.finish_turn(board, second, session_id="sess-helper-2")
        # No orphan: the helper terminal, one-use authority continues the parent.
        parent = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(parent["state"], "executing")
        self.assertEqual(parent["children"][0]["state"], "succeeded")
        third = self.claim(board, claim_request_id="parent-c4", run_id=submitted["runId"])
        self.assertEqual(third["claim"]["attempt"]["taskId"], submitted["runId"])

    def test_declining_helper_attention_still_resumes_the_helper(self):
        board = self.board()
        submitted, _approved, child = self._attention_with_helper(board, request_id="req-2")
        parent = board.call("workflow_get", {"runId": submitted["runId"]})
        resolved = self.decide(
            board,
            parent,
            parent["activeRequest"]["requestId"],
            command_id="parent-answer-2",
            decision="decline",
            reason="proceed without it",
        )
        self.assertEqual(resolved["state"], "waiting-helpers")
        second = self.claim(board, claim_request_id="helper-c5", run_id=child)
        self.assertEqual(second["claim"]["turn"]["turnIndex"], 2)
        context = second["claim"]["turn"]["input"]["context"]
        self.assertEqual(context["hostDecision"]["decision"], "decline")
