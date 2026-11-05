"""Micro-task productivity workflow: lifecycle, exclusion and idempotency.

Every test drives the real store, the real service validation and the real
transactions through the in-process board harness; only the Git-backed workspace
module and the DSH runner process are substituted, exactly like the contract's
module boundary prescribes.
"""
from __future__ import annotations
from hey_my_buddy.protocol.workspace_manifest import turn_manifest, manifest_ref, record_reference, artifact_manifest
from admission import admit_board, admit_store

import json
import sqlite3
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mock_workspace import MockWorkspace
from protocol.fixtures import native_turn
from support import BoardTestCase, enable_fixture_buddy

from hey_my_buddy.blackboard.tasks import workflow as workflow_module
from hey_my_buddy.errors import BoardError

NONCE = "n" * 16
BUDDY = {"harness": "dsh", "provider": "deepseek-official", "model": "deepseek-flash", "effort": "off"}


class WorkflowTestCase(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.workspace = MockWorkspace(self.directory)
        self._previous_workspace = workflow_module._workspace_module
        workflow_module._workspace_module = self.workspace
        self.addCleanup(self._restore_workspace)
        self.catalog_validation = self.enterContext(patch("hey_my_buddy.blackboard.catalog.catalog.validate_buddy", create=True,
                                                        side_effect=lambda buddy, **_: dict(buddy)))
        self.executors = {name: SimpleNamespace(native_resume=name == "zcode",
                                               interrupted_resume=name in ("codex", "dsh"),
                                               native_resume_permission_change=name == "zcode",
                                               validate_turn_provenance=lambda record: None)
                          for name in ("dsh", "zcode", "codex", "claude")}
        self.enterContext(patch.object(workflow_module.WorkflowCoordinator, "_execution_adapter",
                                       side_effect=lambda name: self.executors[name]))

    def _restore_workspace(self) -> None:
        workflow_module._workspace_module = self._previous_workspace

    # -- driven helpers -----------------------------------------------------
    def submit(self, board, *, request_id="req-1", task="do the thing", cwd=None, **extra):
        """Create the requested macro task, then submit one current micro task."""
        cwd = cwd or str(self.workdir())
        title = extra.pop("title", request_id)
        macro = extra.pop("create_macro", None)
        macro_id = extra.pop("macroTaskId", None)
        if macro is not None:
            created = board.call("workflow_create", {"cwd": cwd, **macro})
            macro_id = created["macroTaskId"]
        workspace = extra.pop("workspace", None) or {}
        checkout = extra.pop("kind", workspace.get("kind", "existing"))
        access = extra.pop("access", workspace.get("access", "write"))
        buddy = {key: extra.pop(key, BUDDY[key]) for key in BUDDY}
        selected_buddy = extra.pop("buddy", buddy)
        if isinstance(selected_buddy, dict):
            enable_fixture_buddy(board.store, selected_buddy)
            selected_buddy = ":".join(selected_buddy[key] for key in BUDDY)
        params = {"cwd": cwd, "title": title, "task": task, "buddy": selected_buddy,
                  "checkout": checkout, "access": access, "expectedSeconds": extra.pop("expectedSeconds", 1800),
                  "macroTaskId": macro_id or "standalone",
                  **extra}
        if checkout != "none":
            params.setdefault("inputs", workspace.get("includeUntracked", []))
            if access in ("write", "full"):
                params.setdefault("writeScope", workspace.get("writeScope", ["."]))
        if access == "full":
            params.setdefault("accessNote", workspace.get("accessNote", "Fixture grants full access"))
        response = board.call("workflow_submit", params)
        view = board.call("workflow_get", {"runId": response["runId"]})
        return {**view, **response}

    def register(self, board, worker_id="w1"):
        return None

    def claim(self, board, worker_id="w1", *, claim_request_id="c1", nonce=NONCE, run_id=None):
        params = {'nonce': nonce}
        if run_id:
            params["runId"] = run_id
        return admit_board(board, params)

    def turn_record(self, claim_response, *, disposition="completed", outcome=None, session_id="sess-1", **overrides):
        """A synthetic micro-task turn record in the current provenance format.

        This suite's boundary keeps its own executor stand-ins (the workspace is
        the mock module), so the record passes the stand-in validator here while
        staying in the real current format (``protocol.fixtures.native_turn``);
        no harness ran and nothing here is real native verification.
        """
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
        record = native_turn.claim_record(claim_response["claim"], outcome=outcome, session_id=session_id)
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
            from hey_my_buddy.protocol.workspace_manifest import turn_manifest, record_reference
            manifest = turn_manifest(board.directory, claim["turn"]["input"])
            sealed = self.workspace.seal(board.directory, manifest, attempt["taskId"], attempt["attemptId"])
            original = claim["turn"]["input"]["context"]["microTaskInputCommit"]
            self.workspace.cumulative_patch(manifest, sealed, original)
            result["workspaceSeal"] = record_reference(board.directory, sealed)
        if result_mutator is not None:
            result_mutator(result)
        return board.call(
            'execution_result',
            {'attemptId': attempt['attemptId'], 'generation': attempt['generation'], 'nonce': nonce, 'status': runner_status, 'result': result, 'shutdownConfirmed': shutdown, 'exitCode': exit_code},
        )

    def decide(self, board, view, request_id, *, decision="approve", **extra):
        request = view.get("activeRequest")
        if request is None or request["requestId"] != request_id:
            request = board.call("workflow_get", {"runId": view["runId"]})["activeRequest"]
        assert request["requestId"] == request_id
        target_run_id = (request.get("origin") or {}).get("runId") or request.get("childTaskId")
        if "helpers" in extra:
            helpers = []
            for index, helper in enumerate(extra["helpers"]):
                selected = helper.get("buddy")
                if selected == "route":
                    buddy_name = "route"
                else:
                    buddy = selected if isinstance(selected, dict) else {
                        key: helper.get(key, BUDDY[key]) for key in BUDDY}
                    enable_fixture_buddy(board.store, buddy)
                    buddy_name = ":".join(buddy[key] for key in BUDDY)
                workspace = helper.get("workspace", {})
                checkout = helper.get("checkout", workspace.get("kind", "existing"))
                access = helper.get("access", workspace.get("access", "write"))
                packet = {"title": helper.get("title", f"helper-{index}"),
                          "task": helper["task"], "cwd": helper.get("cwd", view["task"]["cwd"]),
                          "buddy": buddy_name,
                          "checkout": checkout, "access": access,
                          "expectedSeconds": helper.get("expectedSeconds", 1800)}
                if checkout != "none":
                    packet["inputs"] = helper.get("inputs", helper.get("includeUntracked", []))
                    if access in ("write", "full"):
                        packet["writeScope"] = helper.get("writeScope", workspace.get("writeScope", ["."]))
                if access == "full":
                    packet["accessNote"] = helper.get("accessNote", workspace.get("accessNote", "Fixture full helper"))
                helpers.append(packet)
            extra["helpers"] = helpers
            extra.setdefault("autoContinue", True)
        params = {
            "runId": view["runId"],
            **({"targetRunId": target_run_id} if target_run_id and target_run_id != view["runId"] else {}),
            "requestId": request_id,
            "decision": decision,
            **extra,
        }
        response = board.call("workflow_continue", params)
        return {**board.call("workflow_get", {"runId": view["runId"]}), **response}

    def continue_run(self, board, view, *, input="keep going", helper_policy="keep", **extra):
        if extra.get("buddy") is not None:
            from support import enable_fixture_buddy
            if isinstance(extra["buddy"], dict):
                enable_fixture_buddy(board.store, extra["buddy"])
                extra["buddy"] = ":".join(extra["buddy"][key] for key in BUDDY)
            extra.setdefault("note", "Use the explicit fixture buddy for this continuation")
        params = {
            "runId": view["runId"],
            "input": input,
            **extra,
        }
        target_run_id = params.get("targetRunId", view["runId"])
        if not any(key in params for key in ("requestId", "artifactId", "turnId")):
            fresh = board.call("workflow_get", {"runId": target_run_id})
            if fresh.get("activeRequest"):
                params["requestId"] = fresh["activeRequest"]["requestId"]
            elif fresh.get("finalArtifactId"):
                params["artifactId"] = fresh["finalArtifactId"]
            elif partial := next((item for item in reversed(fresh.get("artifacts", []))
                                  if item["kind"] in ("partial-output", "output")
                                  and item["turnId"] == fresh["currentTurn"]["turnId"]), None):
                params["artifactId"] = partial["artifactId"]
            else:
                params["turnId"] = fresh["currentTurn"]["turnId"]
        with board.store.db.read() as connection:
            live = board.store.workflow._unfinished_helpers(connection, target_run_id)
        if live:
            params["helperPolicy"] = helper_policy
        response = board.call("workflow_continue", params)
        return {**board.call("workflow_get", {"runId": view["runId"]}), **response}

    def accept_run(self, board, view, *, artifact_id=None, note="inspected the sealed diff", **extra):
        """One-step acceptance with an explicit not-required integration: this fixture has no separate Git target."""
        params = {
            "runId": view["runId"],
            "artifactId": artifact_id or view["finalArtifactId"],
            "note": note,
            "notRequired": "this fixture is reviewed without a separate repository target",
        }
        params.update(extra)
        return board.call("workflow_accept", params)


class LifecycleTests(WorkflowTestCase):
    def test_turn_buddy_uses_the_bound_execution(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        claimed = self.claim(board)['claim']
        attempt_id = claimed['attempt']['attemptId']
        turn_id = claimed['turn']['turnId']
        original = claimed['turn']['input']
        for adapter, frozen in (("dsh", BUDDY), ("command", {"adapter": "command"})):
            with self.subTest(adapter=adapter, buddy=frozen):
                payload = {**original, 'context': {**original['context'], 'buddy': frozen}}
                raw = json.dumps(payload)
                with board.store.db.write() as connection:
                    connection.execute('UPDATE attempts SET adapter=? WHERE attempt_id=?', (adapter, attempt_id))
                    connection.execute('UPDATE workflow_turns SET input_json=? WHERE turn_id=?', (raw, turn_id))
                view = board.call('workflow_get', {'runId': submitted['runId'], 'includeAudit': True})
                expected = BUDDY if adapter == 'dsh' else None
                self.assertEqual(view['currentTurn']['buddy'], expected)
                self.assertEqual(view['audit']['turns'][0]['buddy'], expected)
                self.assertEqual(view['audit']['turns'][0]['input'], payload)
                with board.store.db.read() as connection:
                    self.assertEqual(connection.execute('SELECT input_json FROM workflow_turns WHERE turn_id=?',
                                                        (turn_id,)).fetchone()[0], raw)

    def test_turn_input_versions_keep_their_frozen_annotation_keys(self):
        board = self.board()
        self.register(board)
        task = "historical task " * 1000
        submitted = self.submit(board, task=task)
        claimed = self.claim(board)['claim']
        original = claimed['turn']['input']
        self.assertEqual(original['version'], 4)
        self.assertNotIn('task', original['context'])
        self.assertNotIn('taskTruncated', original['context'])
        turn_id = claimed['turn']['turnId']
        for version, key in ((1, 'reason'), (2, 'note')):
            with self.subTest(version=version):
                payload = {**original, 'version': version,
                           'executionWorkspace': turn_manifest(board.directory, original),
                           'context': {**original['context'], 'task': task[:8000], 'taskTruncated': True,
                                       'hostDecision': {key: 'Host text'}}}
                raw = json.dumps(payload)
                with board.store.db.write() as connection:
                    connection.execute('UPDATE workflow_turns SET input_json=? WHERE turn_id=?', (raw, turn_id))
                view = board.call('workflow_get', {'runId': submitted['runId'], 'includeAudit': True})
                self.assertEqual(view['audit']['turns'][0]['input'], payload)
                with board.store.db.read() as connection:
                    self.assertEqual(connection.execute('SELECT input_json FROM workflow_turns WHERE turn_id=?',
                                                        (turn_id,)).fetchone()[0], raw)
        with self.assertRaises(sqlite3.IntegrityError), board.store.db.write() as connection:
            connection.execute('UPDATE workflow_turns SET input_json=? WHERE turn_id=?',
                               (json.dumps({**original, 'version': 6}), turn_id))

    def test_execution_record_fields_are_rejected_before_preparation(self):
        board = self.board()
        for fields in ({'adapter': 'command', 'argv': ['true']}, {'adapter': 'external'},
                       {'adapter': None}, {'argv': ['true']}, {'argv': None}):
            with self.subTest(fields=fields), self.assertRaises(BoardError) as caught:
                self.submit(board, **fields)
            self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')
            self.assertIn('Extra inputs are not permitted', caught.exception.message)
        self.catalog_validation.assert_not_called()
        self.assertEqual(self.workspace.prepare_calls, [])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_runs').fetchone()[0], 0)

    def test_new_outcome_truncation_metadata_is_rejected(self):
        board = self.board()
        submitted = self.submit(board)
        claim = self.claim(board)
        result = self.finish_turn(board, claim, record=self.turn_record(claim, outcomeTruncated={}))
        self.assertEqual(result["taskState"], "failed")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["state"], "failed")
        self.assertEqual(view["currentTurn"]["state"], "failed")
        receipt = board.call("task_result", {"runId": submitted["runId"]})["result"]
        self.assertEqual(receipt["microTaskError"]["code"], "TURN_INVALID")
        self.assertIn("outcomeTruncated", receipt["microTaskError"]["message"])

    def test_stored_outcome_truncation_metadata_stays_readable(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        claim = self.claim(board)
        truncated = {"remaining": {"droppedItems": 5, "shortenedItems": 1}}
        self.finish_turn(board, claim)
        # Model a receipt stored before S4; new receipts cannot publish this field.
        with board.store.db.write() as connection:
            connection.execute(
                "UPDATE attempts SET result_json=json_set(result_json,'$.result.turn.outcomeTruncated',json(?))"
                " WHERE attempt_id=?",
                (json.dumps(truncated), claim["claim"]["attempt"]["attemptId"]),
            )

        with board.store.db.read() as connection:
            stored = connection.execute(
                "SELECT json_extract(result_json,'$.result.turn.outcomeTruncated') AS outcome_truncated_json"
                " FROM attempts WHERE attempt_id=?",
                (claim["claim"]["attempt"]["attemptId"],),
            ).fetchone()
        self.assertEqual(json.loads(stored["outcome_truncated_json"]), truncated)
        view = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        self.assertEqual(view["currentTurn"]["state"], "returned")
        self.assertEqual(view["turns"][0]["state"], "returned")
        self.assertEqual(view["audit"]["turns"][0]["state"], "returned")
        self.assertEqual(view["currentTurn"]["outcomeTruncated"], truncated)
        self.assertEqual(view["turns"][0]["outcomeTruncated"], truncated)
        self.assertEqual(view["audit"]["turns"][0]["outcomeTruncated"], truncated)
        self.assertNotIn("outcomeTruncated", view["audit"]["turns"][0]["outcome"])

    def test_failed_receipt_turn_outcome_and_unaccepted_seal_stay_hidden(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        claim = self.claim(board)
        self.finish_turn(board, claim, runner_status="failed", exit_code=1)
        with board.store.db.read() as connection:
            receipt = json.loads(connection.execute(
                "SELECT result_json FROM attempts WHERE attempt_id=?",
                (claim["claim"]["attempt"]["attemptId"],),
            ).fetchone()[0])
        self.assertEqual(receipt["result"]["turn"]["outcome"]["summary"], "did the work")

        view = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        current = view["currentTurn"]
        audit = view["audit"]["turns"][0]
        self.assertEqual(current["state"], "failed")
        self.assertIsNone(current["disposition"])
        self.assertNotIn("summary", current)
        self.assertIsNone(audit["outcome"])
        self.assertIsNone(audit["provenance"])
        self.assertIsNone(audit["sessionId"])
        self.assertIsNone(audit["promptSha256"])
        self.assertIsNone(audit["inputSha256"])
        self.assertIsNone(audit["turnResultPath"])
        self.assertEqual(audit["sealedArtifacts"], [])

    def test_unselected_buddy_reads_have_no_provider_cooling_fact(self):
        board = self.board()
        with patch('hey_my_buddy.blackboard.routing.quota_routing.provider_state',
                   side_effect=AssertionError('an unselected buddy has no provider cooling reader')):
            submitted = self.submit(board, request_id='no-buddy', buddy='route')
            self.assertIsNone(submitted['buddy'])
            self.assertIsNone(submitted['providerCooling'])
            self.assertNotIn('quotaWarnings', submitted)
            view = board.call('workflow_get', {'runId': submitted['runId']})
            self.assertIsNone(view['providerCooling'])


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
                    "title": "helper-1",
                    "task": "implement the schema",
                    "cwd": str(self.workdir()),
                    "workspace": {"kind": "worktree", "access": "write"},
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

        accepted = self.accept_run(board, delivered)
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["artifactId"], delivered["finalArtifactId"])
        accepted_view = board.call("workflow_get", {"runId": run_id})
        self.assertIsNotNone(accepted_view["task"]["acceptedAt"])
        self.assertNotIn("acceptanceVerdict", accepted_view["task"])
        self.assertIsNone(accepted_view["task"]["reviewRejectedAt"])
        self.assertIsNone(accepted_view["task"]["reviewRejectionNote"])
        self.assertEqual(accepted_view["finalArtifactId"], delivered["finalArtifactId"])

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
                    "title": "helper-1",
                    "task": "helper one",
                    "cwd": str(self.workdir("other")),
                    "workspace": {"kind": "worktree", "access": "write"},
                },
                {
                    "title": "helper-2",
                    "task": "helper two",
                    "cwd": str(self.workdir("third")),
                    "workspace": {"kind": "worktree", "access": "write"},
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
            "decision": "approve",
        }
        first_decide = board.call("workflow_continue", params)
        replay = board.call("workflow_continue", params)
        self.assertTrue(replay["alreadyResponded"])
        self.assertEqual(replay["state"], first_decide["state"])
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_continue", {**params, "decision": "decline"})
        self.assertEqual(raised.exception.code, "CONFLICT")

        # Approval with no helpers recorded one-use auto continuation: the parent is
        # requeued exactly once, and replaying the turn result adds nothing.
        self.assertEqual(first_decide["state"], "executing")
        with self.assertRaises(BoardError) as caught:
            self.finish_turn(board, first, disposition="assistance")
        self.assertEqual(caught.exception.code, "ATTEMPT_FINISHED")
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
            note="the plan is not acceptable; use the existing schema",
        )
        self.assertEqual(declined["state"], "executing")
        self.assertIsNone(declined["activeRequest"])
        second = self.claim(board, claim_request_id="c2")
        context = second["claim"]["turn"]["input"]["context"]
        self.assertEqual(context["continuation"]["note"], "the plan is not acceptable; use the existing schema")
        self.assertEqual(context["continuation"]["kind"], "helper")

    def test_decline_without_helpers_continues_parent_once(self):
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
        )
        self.assertEqual(declined["state"], "executing")
        resumed = self.claim(board, claim_request_id="c2")
        self.assertEqual(resumed["claim"]["turn"]["turnIndex"], 2)

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
                    "title": "helper-1",
                    "task": "helper one",
                    "cwd": str(self.workdir("helper")),
                    "workspace": {"kind": "worktree", "access": "write"},
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

    def test_multiple_helpers_fan_in_after_one_needs_attention(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        helpers = [
            {
                "title": f"helper-{index}",
                "task": f"helper {index}",
                "cwd": str(self.workdir(f"h{index}")),
                "workspace": {"kind": "worktree", "access": "write"},
            }
            for index in (1, 2)
        ]
        approved = self.decide(board, view, view["activeRequest"]["requestId"], helpers=helpers)
        children = [child["taskId"] for child in approved["children"]]
        with board.store.db.read() as connection:
            persisted = connection.execute(
                "SELECT child_task_id FROM workflow_children WHERE parent_run_id=?",
                (submitted["runId"],),
            ).fetchall()
        self.assertEqual({row["child_task_id"] for row in persisted}, set(children))
        # The first helper yields attention; the Host sees it while the sibling runs.
        first_helper = self.claim(board, claim_request_id="c2", run_id=children[0])
        self.finish_turn(board, first_helper, disposition="attention")
        parent = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(parent["state"], "awaiting-host")
        self.assertEqual(parent["activeRequest"]["kind"], "helper-attention")
        states = {child["taskId"]: child["state"] for child in parent["children"]}
        self.assertEqual(states[children[0]], "attention")
        self.assertEqual(states[children[1]], "active")
        sibling = self.claim(board, claim_request_id="c3", run_id=children[1])
        self.finish_turn(board, sibling)
        parent = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(parent["state"], "awaiting-host")
        resolved = self.decide(board, parent, parent["activeRequest"]["requestId"],
                               note="finish the first helper")
        self.assertEqual(resolved["state"], "waiting-helpers")
        first_again = self.claim(board, claim_request_id="c4", run_id=children[0])
        self.finish_turn(board, first_again)
        parent = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(parent["state"], "executing")
        self.assertEqual({child["taskId"]: child["state"] for child in parent["children"]},
                         {children[0]: "succeeded", children[1]: "succeeded"})
        resumed = self.claim(board, claim_request_id="c5", run_id=submitted["runId"])
        self.assertEqual({item["taskId"] for item in resumed["claim"]["turn"]["input"]["context"]["helperOutcomes"]},
                         set(children))

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
                    "title": "helper-1",
                    "task": "helper one",
                    "cwd": str(self.workdir("h")),
                    "workspace": {"kind": "worktree", "access": "write"},
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
                    "title": "helper-1",
                    "task": "helper one",
                    "cwd": str(self.workdir("h")),
                    "workspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        self.assertEqual(approved["state"], "waiting-helpers")
        continued = self.continue_run(board, approved, helper_policy="cancel", input="stop waiting for the helper")
        self.assertEqual(continued["state"], "executing")
        audit = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        self.assertEqual(audit["audit"]["continuations"][-1]["helperPolicy"], "cancel")
        auto = [item for item in audit["audit"]["continuations"] if item["kind"] == "helper"]
        self.assertEqual([item["state"] for item in auto], ["invalidated"])
        self.assertTrue(all("authorizedBy" not in item for item in audit["audit"]["continuations"]))
        # The cancelled helper can never requeue the parent later.
        child = approved["children"][0]["taskId"]
        child_task = board.call("task_get", {"runId": child})["task"]
        self.assertIn(child_task["state"], ("queued", "cancelled"))
        next_turn = self.claim(board, claim_request_id="c2")
        self.assertEqual(next_turn["claim"]["turn"]["turnIndex"], 2)

    def test_host_boundary_response_uses_only_the_request_identity(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        run_id = submitted["runId"]
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": run_id})
        self.assertNotIn("control", view)
        self.assertNotIn("ownerGeneration", view)
        self.assertNotIn("revision", view)
        with self.assertRaises(BoardError) as raised:
            board.call(
                "workflow_continue",
                {
                    "runId": run_id,
                    "requestId": view["activeRequest"]["requestId"],
                    "decision": "decline",
                    "controlToken": "obsolete",
                },
            )
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        decided = board.call(
            "workflow_continue",
            {
                "runId": run_id,
                "requestId": view["activeRequest"]["requestId"],
                "decision": "decline",
            },
        )
        self.assertEqual(decided["state"], "executing")
        self.assertEqual(decided["requestId"], view["activeRequest"]["requestId"])

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
                    "title": "helper-1",
                    "task": "helper one",
                    "cwd": str(self.workdir("h")),
                    "workspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        child = approved["children"][0]["taskId"]
        helper_claim = self.claim(board, claim_request_id="c2", run_id=child)
        cancelled = board.call(
            "workflow_cancel", {"runId": run_id, 'note': "stop"}
        )
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertTrue(cancelled["stopEventId"])
        self.assertNotEqual(board.call("workflow_get", {"runId": run_id})["status"], "completed")
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
            ("workflow_submit", {"cwd": str(self.workdir("evil")), "macroTaskId": "standalone",
                                 "title": "forged", "task": "x", "buddy": "route", "checkout": "existing",
                                 "access": "read", "inputs": [], "expectedSeconds": 60}),
            ("execution_progress", {}),
        ):
            with self.assertRaises(BoardError) as raised:
                board.call(operation, {**params, "credential": credential})
            self.assertEqual(raised.exception.code, "FORBIDDEN", operation)
        # An attempt credential cannot perform a Host decision even with the id.
        with self.assertRaises(BoardError) as raised:
            board.call(
                "workflow_continue",
                {
                    "runId": run_id,
                    "requestId": "req-x",
                    "decision": "approve",
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

    def test_micro_task_cannot_use_ordinary_task_control(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        run_id = submitted["runId"]
        with self.assertRaises(BoardError) as raised:
            board.call("task_cancel", {"runId": run_id})
        self.assertEqual(raised.exception.code, "MICRO_TASK_REQUIRED")
        self.assertIn("workflow_cancel", raised.exception.message)
        for retired in ("task_retry", "task_acknowledge"):
            with self.subTest(operation=retired), self.assertRaises(BoardError) as raised:
                board.call(retired, {"runId": run_id})
            self.assertEqual(raised.exception.code, "METHOD_NOT_FOUND")
        unchanged = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(unchanged["state"], "executing")
        cancelled = board.call("workflow_cancel", {"runId": run_id})
        self.assertEqual(cancelled["state"], "cancelled")
        # The caller-owned execution primitive remains independently cancellable.
        execution = board.submit_execution(**{"requestId": "execution-1", "adapter": "command", "argv": ["/bin/true"], "task": "plain", "cwd": str(self.workdir("execution"))})
        plain = board.call("task_cancel", {"runId": execution["task"]["runId"]})
        self.assertEqual(plain["task"]["state"], "cancelled")


class WorkspaceTests(WorkflowTestCase):
    def test_submit_requires_explicit_workspace_kind_before_catalog_or_git_work(self):
        board = self.board()
        for index, checkout in enumerate(({}, {"checkout": None}, {"checkout": "invalid"})):
            with self.subTest(checkout=checkout):
                with self.assertRaises(BoardError) as raised:
                    board.call("workflow_submit", {
                        "cwd": str(self.workdir(f"missing-workspace-{index}")),
                        "macroTaskId": "standalone", "title": f"missing-workspace-{index}",
                        "task": "must choose a checkout", "buddy": "route", "access": "write",
                        "inputs": [], "writeScope": ["."], "expectedSeconds": 600,
                        **checkout,
                    })
                self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
                self.assertIn("checkout", raised.exception.message)
                self.assertEqual(self.catalog_validation.call_count, 0)
                self.assertEqual(self.workspace.prepare_calls, [])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

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
        self.assertEqual(raised.exception.details["checkoutRoot"], os.path.realpath(root))

    def test_isolated_worktrees_allow_parallel_writers(self):
        board = self.board()
        source = self.workdir("source")
        (source / ".git").mkdir(exist_ok=True)
        first = self.submit(board, request_id="req-1", cwd=str(source), kind="worktree")
        second = self.submit(board, request_id="req-2", cwd=str(source), kind="worktree")
        self.assertNotEqual(
            first["workspace"]["checkoutRoot"],
            second["workspace"]["checkoutRoot"],
        )
        self.assertEqual(first["workspace"]["projectId"], second["workspace"]["projectId"])

    def test_idempotent_submit_recovers_snapshot_before_inspecting_source(self):
        board = self.board()
        first = self.submit(board, request_id="req-1", kind="worktree")
        before = len(self.workspace.prepare_calls)
        self.workspace.dirty = True
        replay = self.submit(board, request_id="req-1", kind="worktree")
        self.assertTrue(replay["alreadyExists"])
        self.assertEqual(replay["runId"], first["runId"])
        self.assertEqual(replay["workspace"]["manifestSha256"], first["workspace"]["manifestSha256"])
        self.assertEqual(len(self.workspace.prepare_calls), before)
        self.workspace.dirty = False
        distinct = self.submit(board, request_id="req-1", task="a different micro task", kind="worktree")
        self.assertNotEqual(distinct["runId"], first["runId"])

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
        self.accept_run(board, delivered, note="ok")
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
                    "title": "helper-1",
                    "task": "work on the parent checkout",
                    "cwd": str(parent_cwd),
                    "workspace": {"kind": "existing", "access": "write"},
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
        self.assertEqual(failed["attempt"]["result"]["result"]["microTaskError"]["code"], "ARTIFACT_MISMATCH")
        audit = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        self.assertEqual([row for row in audit["artifacts"] if row["kind"] == "output"], [])


class IdempotencyTests(WorkflowTestCase):
    def test_capture_cannot_switch_the_project_that_determined_its_key(self):
        board = self.board()
        root = str(self.workdir())
        with patch.object(self.workspace, '_checkout_identity', return_value={
                'projectId': 'replacement-project', 'checkoutRoot': root}):
            with self.assertRaises(BoardError) as rejected:
                self.submit(board, kind='worktree')
        self.assertEqual(rejected.exception.code, 'WORKSPACE_CHANGED')
        with board.store.db.read() as connection:
            for table in ('tasks', 'workflow_runs', 'workspace_reservations', 'workflow_artifacts'):
                self.assertEqual(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)

    def test_creation_key_replay_uses_the_first_authorization(self):
        board = self.board()
        first = self.submit(board, kind="worktree", access="read")
        before = len(self.workspace.prepare_calls)
        replay = self.submit(board, kind="worktree", access="write")
        self.assertEqual(replay["runId"], first["runId"])
        self.assertTrue(replay["alreadyExists"])
        self.assertEqual(replay["access"], "read")
        self.assertEqual(len(self.workspace.prepare_calls), before)

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
    def test_replay_returns_original_authorization_without_control(self):
        board = self.board()
        first = self.submit(board, kind="worktree", access="write")
        replay = self.submit(board, kind="worktree", access="full")
        self.assertTrue(replay["alreadyExists"])
        self.assertEqual(replay["runId"], first["runId"])
        self.assertEqual(replay["access"], "write")
        for field in ("control", "controlAvailable", "ownerGeneration", "revision", "hostId"):
            self.assertNotIn(field, replay)

    def test_obsolete_submission_authority_fields_are_rejected(self):
        board = self.board()
        for field, value in (("hostId", "host-1"), ("submissionToken", "s" * 32),
                             ("owner", "host-1"), ("requestId", "req-1")):
            with self.subTest(field=field), self.assertRaises(BoardError) as raised:
                self.submit(board, **{field: value})
            self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
            self.assertEqual(raised.exception.details["field"], field)
        self.assertEqual(self.workspace.prepare_calls, [])


class DecidePrecheckTests(WorkflowTestCase):
    def _yielded(self, board):
        self.register(board)
        submitted = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        return submitted, view

    def test_helper_cannot_supply_a_request_identity(self):
        board = self.board()
        _submitted, view = self._yielded(board)
        before = len(self.workspace.prepare_calls)
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_continue", {
                "runId": view["runId"], "requestId": view["activeRequest"]["requestId"],
                "decision": "approve", "autoContinue": True,
                "helpers": [{"title": "helper", "task": "work", "cwd": str(self.workdir("helper")),
                             "buddy": ":".join(BUDDY.values()), "checkout": "worktree", "access": "read",
                             "inputs": [], "expectedSeconds": 1800, "requestId": "caller-chosen"}],
            })
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertIn("requestId", raised.exception.details["field"])
        self.assertEqual(len(self.workspace.prepare_calls), before)
        unchanged = board.call("workflow_get", {"runId": view["runId"]})
        self.assertEqual(unchanged["activeRequest"]["requestId"], view["activeRequest"]["requestId"])

    def test_foreign_target_decision_never_prepares_a_workspace(self):
        board = self.board()
        _submitted, view = self._yielded(board)
        foreign = self.submit(board, request_id="foreign", cwd=str(self.workdir("foreign")))
        before = len(self.workspace.prepare_calls)
        with self.assertRaises(BoardError) as raised:
            self.decide(board, view, view["activeRequest"]["requestId"],
                        targetRunId=foreign["runId"], helpers=[{
                            "task": "work", "cwd": str(self.workdir("h")),
                            "workspace": {"kind": "worktree", "access": "write"},
                        }])
        self.assertEqual(raised.exception.code, "UNAUTHORIZED")
        self.assertEqual(len(self.workspace.prepare_calls), before)

    def test_every_helper_requires_explicit_workspace_kind_before_preparation(self):
        board = self.board()
        _submitted, view = self._yielded(board)
        prepared = len(self.workspace.prepare_calls)
        validated = self.catalog_validation.call_count
        valid = {"title": "valid", "task": "valid first helper", "cwd": str(self.workdir("valid-helper")),
                 "buddy": ":".join(BUDDY.values()), "checkout": "worktree", "access": "write",
                 "inputs": [], "writeScope": ["."], "expectedSeconds": 1800}
        for checkout in ({}, {"checkout": None}, {"checkout": "invalid"}):
            with self.subTest(checkout=checkout):
                with self.assertRaises(BoardError) as raised:
                    board.call("workflow_continue", {
                        "runId": view["runId"], "requestId": view["activeRequest"]["requestId"],
                        "decision": "approve", "autoContinue": True,
                        "helpers": [valid, {**valid, "title": "invalid", "task": "must choose a checkout",
                                            **({"checkout": checkout["checkout"]} if checkout else {"checkout": None})}],
                    })
                self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
                self.assertIn("checkout", raised.exception.message)
                self.assertEqual(len(self.workspace.prepare_calls), prepared)
                self.assertEqual(self.catalog_validation.call_count, validated)
        unchanged = board.call("workflow_get", {"runId": view["runId"]})
        self.assertEqual(unchanged["counts"]["children"], 0)

    def test_obsolete_revision_never_prepares_a_workspace(self):
        board = self.board()
        submitted, view = self._yielded(board)
        before = len(self.workspace.prepare_calls)
        with self.assertRaises(BoardError) as raised:
            board.call(
                "workflow_continue",
                {
                    "runId": view["runId"],
                    "requestId": view["activeRequest"]["requestId"],
                    "expectedRevision": 99,
                    "decision": "approve",
                },
            )
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(raised.exception.details["field"], "expectedRevision")
        self.assertEqual(len(self.workspace.prepare_calls), before)

    def test_replayed_decide_does_not_prepare_again(self):
        board = self.board()
        submitted, view = self._yielded(board)
        params = {"runId": view["runId"], "requestId": view["activeRequest"]["requestId"],
                  "decision": "approve", "autoContinue": True,
                  "helpers": [{"title": "helper", "task": "work", "cwd": str(self.workdir("h")),
                               "buddy": ":".join(BUDDY.values()), "checkout": "worktree", "access": "write",
                               "inputs": [], "writeScope": ["."], "expectedSeconds": 1800}]}
        board.call("workflow_continue", params)
        after_first = len(self.workspace.prepare_calls)
        replay = board.call("workflow_continue", params)
        self.assertTrue(replay["alreadyResponded"])
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
                    "title": "helper-1",
                    "task": "work",
                    "cwd": str(self.workdir("h")),
                    "workspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        with self.assertRaises(BoardError) as raised:
            board.call(
                "workflow_continue",
                {
                    "runId": submitted["runId"],
                    "artifactId": next(item["artifactId"] for item in approved["artifacts"]
                                       if item["kind"] == "output"),
                    "input": "go on",
                },
            )
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(raised.exception.details["field"], "helperPolicy")
        # Without live helpers the continuation has no helperPolicy field.
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
            worker_id="w2",
            nonce="z" * 16,
        )
        view_idle = idle.call("workflow_get", {"runId": submitted_idle["runId"]})
        continued = idle.call(
            "workflow_continue",
            {
                "runId": submitted_idle["runId"],
                "artifactId": view_idle["finalArtifactId"],
                "input": "go on",
            },
        )
        self.assertEqual(continued["state"], "executing")


class ArtifactIdentityTests(WorkflowTestCase):
    def test_result_import_reads_the_output_record_once(self):
        from hey_my_buddy.protocol import workspace_manifest as references
        board = self.board()
        submitted = self.submit(board)
        claim = self.claim(board)
        attempt = claim["claim"]["attempt"]
        manifest = turn_manifest(board.directory, claim["claim"]["turn"]["input"])
        sealed = self.workspace.seal(board.directory, manifest, submitted["runId"], attempt["attemptId"])
        reference = record_reference(board.directory, sealed)
        params = {"attemptId": attempt["attemptId"], "status": "ok", "shutdownConfirmed": True,
                  "result": {"turn": self.turn_record(claim), "workspaceSeal": reference}}
        with patch.object(references, "read_bytes", wraps=references.read_bytes) as reader:
            result = board.store.execution_result(params, stop_evidence={"kind": "process-reaped", "native": "receipt"})
        reads = [call for call in reader.call_args_list
                 if call.args[0] == board.directory / reference["recordFile"]["path"]]
        self.assertEqual(result["workflow"]["state"], "delivered")
        self.assertEqual(len(reads), 1, "Validated output bytes must be passed into settlement without reopening")

    def test_output_is_bound_to_the_sealed_snapshot(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        claim = self.claim(board)
        manifest = turn_manifest(self.directory, claim["claim"]["turn"]["input"])
        self.finish_turn(board, claim)
        view = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        output = [row for row in view["artifacts"] if row["kind"] == "output"][0]
        seal = self.workspace.seal(self.directory, manifest, submitted["runId"], claim["claim"]["attempt"]["attemptId"])
        self.assertEqual(output["outputSnapshotSha256"], seal["snapshotSha256"])
        self.assertNotEqual(output["outputSnapshotSha256"], manifest["manifestSha256"])
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
        self.assertEqual(set(view["truncated"]), {"turns", "requests", "children", "ownedMicroTasks", "artifacts", "pendingRequests"})
        self.assertEqual(view["counts"]["turns"], 1)
        # The submitted input, the sealed output and the turn's frozen
        # shared-stash fact record are each one fixed artifact.
        self.assertEqual(view["counts"]["artifacts"], 3)
        self.assertEqual({row["kind"] for row in view["artifacts"]},
                         {"output", "input", "shared-refs"})
        self.assertEqual(view["truncated"]["turns"], 0)
        self.assertEqual(view["counts"]["openRequests"], 1)
        self.assertEqual(len(view["pendingRequests"]), 1)
        self.assertEqual(view["pendingRequests"][0]["requestId"], view["activeRequest"]["requestId"])
        self.assertEqual(view["truncated"]["pendingRequests"], 0)


class NestedSpecTests(WorkflowTestCase):
    def test_explicit_expected_duration_is_frozen_on_each_turn(self):
        board = self.board()
        submitted = self.submit(board, request_id="expected-default")
        self.assertEqual(submitted["task"]["expectedSeconds"], 1800)
        self.register(board)
        claim = self.claim(board)["claim"]
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["task"]["expectedSeconds"], 1800)
        self.assertEqual(view["turns"][0]["expectedSeconds"], 1800)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT expected_seconds FROM workflow_turns WHERE attempt_id=?",
                                               (claim["attempt"]["attemptId"],)).fetchone()[0], 1800)

    def test_expected_duration_is_positive_and_unknown_name_is_rejected(self):
        board = self.board()
        with self.assertRaises(BoardError) as missing:
            board.call("workflow_submit", {"cwd": str(self.workdir("missing-duration")),
                                           "macroTaskId": "standalone", "title": "missing duration",
                                           "task": "duration required", "buddy": "route", "checkout": "existing",
                                           "access": "read", "inputs": []})
        self.assertEqual(missing.exception.code, "INVALID_ARGUMENT")
        self.assertIn("expectedSeconds", missing.exception.message)
        for value in (0, -1, True, None, 1.5):
            with self.subTest(value=value), self.assertRaises(BoardError):
                self.submit(board, request_id="bad-expected", expectedSeconds=value)
        with self.assertRaises(BoardError) as raised:
            self.submit(board, request_id="unknown-duration", timeoutSeconds=60)
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(raised.exception.details["field"], "timeoutSeconds")
        submitted = self.submit(board, request_id="positive-expected", expectedSeconds=1)
        self.assertEqual(submitted["task"]["expectedSeconds"], 1)

    def test_nested_spec_shape_is_rejected(self):
        board = self.board()
        params = {"cwd": str(self.workdir("nested")), "macroTaskId": "standalone",
                  "title": "flat title", "task": "flat task", "buddy": "route", "checkout": "worktree",
                  "access": "write", "inputs": [], "writeScope": ["."], "expectedSeconds": 600}
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_submit", {**params, "spec": {"task": "different task"}})
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(raised.exception.details["field"], "spec")


class LateCancellationTests(WorkflowTestCase):
    def test_late_turn_with_an_import_error_publishes_no_output(self):
        board = self.board()
        submitted = self.submit(board)
        claim = self.claim(board)
        board.call("workflow_cancel", {"runId": submitted["runId"], "note": "stop"})

        def bad_seal(result):
            result["workspaceSeal"] = {"manifestSha256": "not-a-hash"}

        failed = self.finish_turn(board, claim, seal=False, result_mutator=bad_seal)
        result = failed["attempt"]["result"]["result"]
        self.assertEqual(result["microTaskError"]["code"], "ARTIFACT_MISMATCH")
        self.assertNotIn("receiptRejection", result)
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["state"], "cancelled")
        self.assertEqual(view["currentTurn"]["state"], "failed")
        self.assertFalse(any(row["kind"] in ("output", "partial-output") for row in view["artifacts"]))

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
                    "title": "helper-1",
                    "task": "work",
                    "cwd": str(self.workdir("h")),
                    "workspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        child = approved["children"][0]["taskId"]
        helper_claim = self.claim(board, claim_request_id="c2", run_id=child)
        board.call("workflow_cancel", {"runId": submitted["runId"], 'note': "stop"})
        late = self.finish_turn(board, helper_claim)
        self.assertEqual(late["taskState"], "completed")
        child_view = board.call("workflow_get", {"runId": child})
        # The helper's run was cancelled with the parent, so its late completion is
        # recorded as evidence and can never revive the micro task; the sealed output it
        # produced stays pinned as an immutable reference.
        self.assertEqual(child_view["state"], "cancelled")
        self.assertIn("output", [row["kind"] for row in child_view["artifacts"]])
        parent = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(parent["state"], "cancelled")


class ContinuationWorkspaceTests(WorkflowTestCase):
    def test_continuation_prepares_a_new_manifest_from_the_sealed_output(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        first = self.claim(board)
        first_manifest = turn_manifest(self.directory, first["claim"]["turn"]["input"])
        self.finish_turn(board, first, disposition="assistance", session_id="sess-1")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        decided = self.decide(board, view, view["activeRequest"]["requestId"], decision="decline")
        second = self.claim(board, claim_request_id="c2")
        turn_input = second["claim"]["turn"]["input"]
        effective = turn_manifest(board.directory, turn_input)
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

            helpers=[
                {
                    "title": f"{request_id}-helper-1",
                    "task": "helper work",
                    "cwd": str(self.workdir(f"{request_id}-h")),
                    "workspace": {"kind": "worktree", "access": "write"},
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

            note="use the narrower approach",
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

            decision="decline",
            note="proceed without it",
        )
        self.assertEqual(resolved["state"], "waiting-helpers")
        second = self.claim(board, claim_request_id="helper-c5", run_id=child)
        self.assertEqual(second["claim"]["turn"]["turnIndex"], 2)
        context = second["claim"]["turn"]["input"]["context"]
        self.assertEqual(context["hostDecision"]["decision"], "decline")


class AcknowledgementBoundaryTests(WorkflowTestCase):
    def _delivered(self, board, request_id="req-1"):
        self.register(board)
        submitted = self.submit(board, request_id=request_id)
        claim = self.claim(board, claim_request_id=f"{request_id}-c1")
        self.finish_turn(board, claim)
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["state"], "delivered")
        output = [row for row in view["artifacts"] if row["kind"] == "output"][0]
        return submitted, view, output

    def test_host_boundary_without_a_sealed_output_cannot_be_accepted(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        # An assistance boundary whose turn never sealed an output is not a final
        # artifact, so the Host cannot finish the micro task from it.
        self.finish_turn(board, self.claim(board), disposition="assistance", seal=False)
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertFalse(any(row["kind"] == "output" for row in view["artifacts"]))
        params = {"runId": submitted["runId"], "note": "looks done",
                  "notRequired": "no separate target", "artifactId": "art-no-sealed-output"}
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", params)
        self.assertEqual(raised.exception.code, "NOT_FOUND")
        audit = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        self.assertEqual(audit["state"], "awaiting-host")
        self.assertEqual([row["state"] for row in audit["audit"]["reservations"]], ["held"])

    def test_acknowledgement_while_helpers_are_live_is_refused(self):
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
                    "title": "helper-1",
                    "task": "work",
                    "cwd": str(self.workdir("h")),
                    "workspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        with self.assertRaises(BoardError) as raised:
            board.call(
                "workflow_accept",
                {"runId": submitted["runId"], "note": "accepted early",
                 "artifactId": next(item["artifactId"] for item in approved["artifacts"]
                                    if item["kind"] == "output"), "notRequired": "no separate target"},
            )
        self.assertEqual(raised.exception.code, "NOT_READY")

    def test_only_the_exact_current_output_artifact_can_be_accepted(self):
        board = self.board()
        submitted, view, output = self._delivered(board)
        input_artifact = [row for row in view["artifacts"] if row["kind"] == "input"][0]
        with self.assertRaises(BoardError) as raised:
            board.call("task_acknowledge", {"runId": submitted["runId"], "note": "bypass artifact review"})
        self.assertEqual(raised.exception.code, "METHOD_NOT_FOUND")
        with self.assertRaises(BoardError) as raised:
            board.call(
                "workflow_accept",
                {
                    "runId": submitted["runId"],
                    "artifactId": input_artifact["artifactId"],
                    "note": "wrong artifact",
                    "notRequired": "no separate target",
                },
            )
        self.assertEqual(raised.exception.code, "CONFLICT")
        with self.assertRaises(BoardError) as raised:
            board.call(
                "workflow_accept",
                {"runId": submitted["runId"], "artifactId": "art-does-not-exist", "note": "x",
                 "notRequired": "no separate target"},
            )
        self.assertEqual(raised.exception.code, "NOT_FOUND")
        accepted = board.call(
            "workflow_accept",
            {
                "runId": submitted["runId"],
                "artifactId": output["artifactId"],
                "note": "the exact sealed output",
                "notRequired": "no separate target",
            },
        )
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["artifactId"], output["artifactId"])
        # A genuine exact replay is idempotent; a different review is not a relabel.
        replay = board.call(
            "workflow_accept",
            {
                "runId": submitted["runId"],
                "artifactId": output["artifactId"],
                "note": "the exact sealed output",
                "notRequired": "no separate target",
            },
        )
        self.assertTrue(replay["alreadyResponded"])
        with self.assertRaises(BoardError) as raised:
            board.call(
                "workflow_accept",
                {"runId": submitted["runId"], "note": "different note",
                 "artifactId": output["artifactId"], "notRequired": "no separate target"},
            )
        self.assertEqual(raised.exception.code, "CONFLICT")

    def test_rejected_review_then_continuation_then_new_acceptance(self):
        board = self.board()
        submitted, view, output = self._delivered(board)
        # Rejecting a delivered outcome is a continuation: the negative review and
        # the Host's opinion are recorded next to the immutable artifact.
        turned = board.call("workflow_get", {"runId": submitted["runId"]})
        continued = self.continue_run(board, turned,  input="address the review",
                                      note="not good enough")
        self.assertEqual(continued["state"], "executing")
        archived = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        # The delivered outcome is recorded as rejected, never silently carried
        # into the continuation as an acceptance.
        self.assertNotIn("acceptanceVerdict", archived["task"])
        self.assertIsNone(archived["task"]["acceptedAt"])
        self.assertIsNone(archived["task"]["acceptanceNote"])
        self.assertIsNotNone(archived["task"]["reviewRejectedAt"])
        self.assertEqual(archived["task"]["reviewRejectionNote"], "not good enough")
        events = board.call(
            "events_read", {"runId": submitted["runId"], "after": 0, "limit": 200}
        )["events"]
        rejection = next(event for event in events if event["kind"] == "workflow.review_rejected")
        self.assertEqual(rejection["payload"]["artifactId"], output["artifactId"])
        self.assertEqual(rejection["payload"]["note"], "not good enough")
        second = self.claim(board, claim_request_id="c2")
        self.finish_turn(board, second, session_id="sess-2")
        delivered = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(delivered["state"], "delivered")
        continued_again = self.continue_run(board, delivered,
                                            input="Fix the remaining defect", note="second review")
        self.assertEqual(continued_again["task"]["reviewRejectionNote"], "second review")
        self.assertIsNotNone(continued_again["task"]["reviewRejectedAt"])
        self.assertIsNone(continued_again["task"]["acceptedAt"])
        events = board.call("events_read", {"runId": submitted["runId"], "after": 0, "limit": 200})["events"]
        archived_reviews = [event["payload"] for event in events if event["kind"] == "task.review_archived"]
        self.assertEqual(archived_reviews[-1]["note"], "not good enough")
        self.assertEqual(archived_reviews[-1]["verdict"], "rejected")
        third = self.claim(board, claim_request_id="c3")
        self.finish_turn(board, third, session_id="sess-3")
        delivered = board.call("workflow_get", {"runId": submitted["runId"]})
        new_output = [row for row in delivered["artifacts"] if row["kind"] == "output"][0]
        accepted = self.accept_run(board, delivered, note="now it is good")
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["artifactId"], new_output["artifactId"])
        accepted_view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertIsNotNone(accepted_view["task"]["acceptedAt"])
        self.assertNotIn("acceptanceVerdict", accepted_view["task"])
        self.assertIsNone(accepted_view["task"]["reviewRejectedAt"])
        self.assertIsNone(accepted_view["task"]["reviewRejectionNote"])

    def test_late_result_never_reopens_a_cancelled_micro_task(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        claim = self.claim(board)
        cancelled = board.call(
            "workflow_cancel", {"runId": submitted["runId"], 'note': "stop"}
        )
        self.assertEqual(cancelled["state"], "cancelled")
        late = self.finish_turn(board, claim)
        self.assertEqual(late["workflow"]["state"], "cancelled")
        self.assertTrue(late["workflow"]["late"])
        parent = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(parent["state"], "cancelled")
        self.assertEqual(parent["currentTurn"]["state"], "returned")
        self.assertIsNone(parent["currentTurn"]["outcomeTruncated"])


class NativeRequestShapeTests(WorkflowTestCase):
    def _native_request_outcome(self):
        return {
            "disposition": "assistance",
            "summary": "need the Host",
            "remaining": ["finish"],
            "decisions": [],
            "artifacts": [],
            "request": {
                "summary": "please review",
                "attempted": "tried the obvious fix",
                "neededWork": "decide the schema",  # native single string
                "expectedArtifacts": ["a reviewed design"],
                "acceptance": "the design is approved",
            },
        }

    def test_native_string_needed_work_surfaces_as_one_item(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        self.finish_turn(
            board,
            self.claim(board),
            disposition="assistance",
            outcome=self._native_request_outcome(),
        )
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["activeRequest"]["neededWork"], ["decide the schema"])
        self.assertEqual(view["activeRequest"]["attempted"], "tried the obvious fix")
        self.assertEqual(view["activeRequest"]["acceptance"], "the design is approved")
        self.assertEqual(view["activeRequest"]["expectedArtifacts"], ["a reviewed design"])

    def test_request_projection_keeps_long_text_and_every_reference(self):
        board = self.board()
        submitted = self.submit(board)
        outcome = self._native_request_outcome()
        request = outcome["request"]
        request["neededWork"] = "n" * 8001
        request["expectedArtifacts"] = ["a" * 4097] + ["artifact"] * 36
        self.finish_turn(board, self.claim(board), outcome=outcome)
        view = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        self.assertEqual(view["activeRequest"]["neededWork"], [request["neededWork"]])
        self.assertEqual(view["activeRequest"]["expectedArtifacts"], request["expectedArtifacts"])

    def test_helper_attention_proxies_the_native_needed_work(self):
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
                    "title": "helper-1",
                    "task": "helper work",
                    "cwd": str(self.workdir("h")),
                    "workspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        child = approved["children"][0]["taskId"]
        helper = self.claim(board, claim_request_id="c2", run_id=child)
        outcome = self._native_request_outcome()
        outcome["summary"] = "s" * 5000
        outcome["remaining"] = ["r" * 4097] + ["remaining"] * 36
        self.finish_turn(
            board,
            helper,
            disposition="attention",
            outcome=outcome,
        )
        parent = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(parent["activeRequest"]["kind"], "helper-attention")
        self.assertEqual(parent["activeRequest"]["neededWork"], ["decide the schema"])
        with board.store.db.read() as connection:
            frozen = board.store.workflow._helper_outcomes(connection, submitted["runId"])[0]
        self.assertEqual(frozen["summary"], outcome["summary"])
        self.assertEqual(frozen["remaining"], outcome["remaining"])


class HelperDependencyTests(WorkflowTestCase):
    def test_helper_dependency_defers_resume_until_it_settles(self):
        board = self.board()
        for worker in ("w1", "w2", "w3"):
            self.register(board, worker_id=worker)
        submitted = self.submit(board)
        run_id = submitted["runId"]
        self.finish_turn(
            board,
            self.claim(board, worker_id="w1", claim_request_id="c1", nonce="a" * 16),
            disposition="assistance",
            worker_id="w1",
            nonce="a" * 16,
        )
        view = board.call("workflow_get", {"runId": run_id})
        approved = self.decide(
            board,
            view,
            view["activeRequest"]["requestId"],

            helpers=[
                {
                    "title": "helper-a",
                    "task": "helper A",
                    "cwd": str(self.workdir("ha")),
                    "workspace": {"kind": "worktree", "access": "write"},
                },
                {
                    "title": "helper-b",
                    "task": "helper B",
                    "cwd": str(self.workdir("hb")),
                    "workspace": {"kind": "worktree", "access": "write"},
                },
            ],
        )
        helper_a, helper_b = [child["taskId"] for child in approved["children"]]
        # Helper A yields attention while sibling B is still active.
        a_claim = self.claim(board, worker_id="w1", claim_request_id="c2", nonce="b" * 16, run_id=helper_a)
        self.finish_turn(board, a_claim, disposition="attention", worker_id="w1", nonce="b" * 16)
        parent = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(parent["activeRequest"]["childTaskId"], helper_a)
        # Sibling B finishes first; it must not resume the parent or helper A.
        b_claim = self.claim(board, worker_id="w2", claim_request_id="c3", nonce="c" * 16, run_id=helper_b)
        self.finish_turn(board, b_claim, worker_id="w2", nonce="c" * 16)
        parent = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(parent["state"], "awaiting-host")
        states = {child["taskId"]: child["state"] for child in parent["children"]}
        self.assertEqual(states[helper_a], "attention")
        self.assertEqual(states[helper_b], "succeeded")
        # The Host grants the request with a dependency helper; A waits for it.
        resolved = self.decide(
            board,
            parent,
            parent["activeRequest"]["requestId"],

            note="use the dependency output",
            helpers=[
                {
                    "title": "helper-c",
                    "task": "dependency C",
                    "cwd": str(self.workdir("hc")),
                    "workspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        self.assertEqual(resolved["state"], "waiting-helpers")
        blocked = self.claim(board, worker_id="w1", claim_request_id="c4", nonce="d" * 16, run_id=helper_a)
        self.assertIsNone(blocked["claim"], "the child must wait for its dependency")
        a_view = board.call("workflow_get", {"runId": helper_a})
        states = {child["taskId"]: child["state"] for child in a_view["children"]}
        self.assertEqual(list(states.values()), ["active"])
        dependency = next(iter(states))
        # The dependency settles: now A auto-resumes, then the parent.
        c_claim = self.claim(board, worker_id="w3", claim_request_id="c5", nonce="e" * 16, run_id=dependency)
        self.finish_turn(board, c_claim, worker_id="w3", nonce="e" * 16, session_id="sess-c")
        a_second = self.claim(board, worker_id="w1", claim_request_id="c6", nonce="f" * 16, run_id=helper_a)
        self.assertEqual(a_second["claim"]["turn"]["turnIndex"], 2)
        self.finish_turn(board, a_second, worker_id="w1", nonce="f" * 16)
        parent = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(parent["state"], "executing")
        self.assertEqual([child["state"] for child in parent["children"]], ["succeeded", "succeeded"])
        final_turn = self.claim(board, worker_id="w1", claim_request_id="c7", nonce="g" * 16, run_id=run_id)
        self.assertEqual(final_turn["claim"]["attempt"]["taskId"], run_id)


class RetiredTakeoverTests(WorkflowTestCase):
    def test_takeover_is_not_an_operation_and_run_stays_addressable(self):
        board = self.board()
        submitted = self.submit(board)
        run_id = submitted["runId"]
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_takeover", {"runId": run_id})
        self.assertEqual(raised.exception.code, "METHOD_NOT_FOUND")
        view = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(view["runId"], run_id)
        self.assertNotIn("control", view)
        self.assertNotIn("ownerGeneration", view)


class CompactReadBoundTests(WorkflowTestCase):
    def test_large_task_reads_whole_in_view_and_original_in_audit(self):
        board = self.board()
        self.register(board)
        big_task = "deliver the specification " * 4000  # ~100 KB task
        submitted = self.submit(board, task=big_task)
        self.finish_turn(board, self.claim(board))
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["microTask"]["task"], big_task)
        self.assertNotIn("taskTruncated", view["microTask"])
        self.assertNotIn("taskBytes", view["microTask"])
        self.assertNotIn("spec", view["task"])
        serialized = json.dumps(view)
        # Everything outside the whole task text stays bounded.
        self.assertLess(len(serialized), len(big_task) + 20000)
        audit = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        self.assertNotIn("spec", audit["audit"])
        self.assertEqual(audit["audit"]["microTask"]["task"], big_task)
        self.assertNotIn("task", audit["audit"]["turns"][0]["input"]["context"])
        self.assertNotIn("taskTruncated", audit["audit"]["turns"][0]["input"]["context"])


class CompleteTurnInputTests(WorkflowTestCase):
    def test_turn_keeps_frozen_context_without_repeating_the_task(self):
        board = self.board()
        task = "task " * 70000
        submitted = self.submit(board, task=task)
        for index in range(4):
            claim = self.claim(board, claim_request_id=f"turn-{index}", run_id=submitted["runId"])
            if index == 0:
                self.assertIsNone(claim["claim"]["turn"]["input"]["context"]["latestHandoff"])
                self.assertEqual(claim["claim"]["turn"]["input"]["context"]["artifactsTotal"], 1)
            self.finish_turn(board, claim)
            view = board.call("workflow_get", {"runId": submitted["runId"]})
            continuation = self.continue_run(board, view)
        helpers = [{"taskId": f"helper-{index}", "state": "completed", "summary": "s" * 24000,
                    "remaining": ["r" * 4097] + ["remaining"] * 36} for index in range(12)]
        # A continuation freezes accumulated helper outcomes before the next admission.
        with board.store.db.write() as connection:
            connection.execute("UPDATE workflow_continuations SET helper_outcomes_json=? WHERE continuation_id=?",
                               (json.dumps(helpers), continuation["continuationId"]))
            count = connection.execute("SELECT COUNT(*) FROM workflow_artifacts WHERE run_id=?",
                                       (submitted["runId"],)).fetchone()[0]
        self.assertGreater(count, 8)
        claim = self.claim(board, claim_request_id="complete-context", run_id=submitted["runId"])
        turn = claim["claim"]["turn"]
        context = turn["input"]["context"]
        self.assertEqual(turn["input"]["version"], 4)
        self.assertNotIn("task", context)
        self.assertEqual(context["helperOutcomes"], helpers)
        # Only the newest handoff summary and the run's actual artifact row count cross the boundary.
        self.assertNotIn("artifacts", context)
        self.assertEqual(context["artifactsTotal"], count)
        self.assertIsNotNone(context["latestHandoff"])
        self.assertNotIn("changedPaths", context["latestHandoff"])
        self.assertGreater(len(workflow_module.canonical_json(turn["input"]).encode()), 256 * 1024)
        self.assertEqual(len(context["nextActions"]), 2)
        for field in ("taskTruncated", "helperOutcomeTotal", "artifactTotal", "artifactsTruncated"):
            self.assertNotIn(field, context)
        with board.store.db.read() as connection:
            rows = connection.execute("SELECT input_json FROM workflow_turns WHERE run_id=?",
                                      (submitted["runId"],)).fetchall()
            spec = json.loads(connection.execute("SELECT spec_json FROM tasks WHERE task_id=?",
                                                (submitted["runId"],)).fetchone()[0])
        self.assertEqual(spec["task"], task)
        for row in rows:
            document = json.loads(row["input_json"])
            self.assertEqual(document["version"], 4)
            self.assertNotIn("task", document["context"])
            self.assertNotIn("taskTruncated", document["context"])

    def test_a_new_turn_preserves_its_previous_v2_input_without_copying_task_text(self):
        board = self.board()
        task = "original task " * 1000
        submitted = self.submit(board, task=task)
        first = self.claim(board)
        turn = first["claim"]["turn"]
        historical = {**turn["input"], "version": 2,
                      "executionWorkspace": turn_manifest(board.directory, turn["input"]),
                      "context": {**turn["input"]["context"], "task": task[:8000], "taskTruncated": True}}
        frozen = workflow_module.canonical_json(historical)
        turn["input"] = historical
        turn["inputSha256"] = workflow_module.sha256_text(frozen)
        with board.store.db.write() as connection:
            connection.execute("UPDATE workflow_turns SET input_json=? WHERE turn_id=?", (frozen, turn["turnId"]))
        self.finish_turn(board, first)
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, view)
        second = self.claim(board, claim_request_id="after-v2", run_id=submitted["runId"])
        new_input = second["claim"]["turn"]["input"]
        self.assertEqual(new_input["version"], 4)
        self.assertNotIn("task", new_input["context"])
        self.assertNotIn("taskTruncated", new_input["context"])
        with board.store.db.read() as connection:
            retained = connection.execute("SELECT input_json FROM workflow_turns WHERE turn_id=?",
                                          (turn["turnId"],)).fetchone()[0]
        self.assertEqual(retained, frozen)
        audit = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        prior = next(row for row in audit["audit"]["turns"] if row["turnId"] == turn["turnId"])
        self.assertEqual(prior["input"], historical)
        self.assertEqual(prior["inputSha256"], workflow_module.sha256_text(frozen))

    def test_host_continuation_uses_the_task_text_byte_budget(self):
        board = self.board()
        submitted = self.submit(board)
        self.finish_turn(board, self.claim(board))
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        maximum = workflow_module.schemas.MAX_TASK_BYTES
        value = "汉" * (maximum // 3) + "x" * (maximum % 3)
        with self.assertRaises(BoardError) as wrong_type:
            self.continue_run(board, view, input={"text": "object input is not allowed"})
        self.assertEqual(wrong_type.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as oversized:
            self.continue_run(board, view, input=value + "x")
        self.assertEqual(oversized.exception.code, "INVALID_ARGUMENT")
        self.continue_run(board, view, input=value)
        claim = self.claim(board, claim_request_id="full-input", run_id=submitted["runId"])
        text = claim["claim"]["turn"]["input"]["context"]["continuation"]["input"]
        self.assertEqual(text, value)
        self.assertEqual(len(text.encode()), maximum)


class CancellationCascadeTests(WorkflowTestCase):
    def _yielded_parent(self, board, root):
        self.register(board)
        submitted = self.submit(board, cwd=str(root))
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        return submitted, view

    def _approve_helper(self, board, view, submitted, root, request_id="helper-1"):
        approved = self.decide(
            board,
            view,
            view["activeRequest"]["requestId"],

            helpers=[
                {
                    "title": request_id,
                    "task": "helper work",
                    "cwd": str(root),
                    "workspace": {"kind": "existing", "access": "write"},
                }
            ],
        )
        return approved["children"][0]["taskId"]

    def test_queued_helper_on_a_transferred_checkout_is_cancelled_and_released(self):
        board = self.board()
        root = self.workdir("repo")
        (root / ".git").mkdir()
        submitted, view = self._yielded_parent(board, root)
        child = self._approve_helper(board, view, submitted, root)
        approved = board.call("workflow_get", {"runId": submitted["runId"]})
        cancelled = board.call(
            "workflow_cancel", {"runId": submitted["runId"], 'note': "stop"}
        )
        self.assertEqual(cancelled["state"], "cancelled")
        child_view = board.call("workflow_get", {"runId": child})
        self.assertEqual(child_view["state"], "cancelled")
        self.assertEqual(child_view["status"], "cancelled")
        audit = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        states = {(row["holderTaskId"], row["state"]) for row in audit["audit"]["reservations"]}
        self.assertIn((child, "released"), states)
        self.assertIn((submitted["runId"], "released"), states)
        # The checkout is reusable: a new run is admitted with no leaked writer.
        again = self.submit(board, request_id="req-2", cwd=str(root))
        self.assertEqual(again["workspace"]["checkoutRoot"], submitted["workspace"]["checkoutRoot"])

    def test_yielded_helper_is_cancelled_after_proven_stop(self):
        board = self.board()
        root = self.workdir("repo")
        (root / ".git").mkdir()
        submitted, view = self._yielded_parent(board, root)
        child = self._approve_helper(board, view, submitted, root)
        helper = self.claim(board, claim_request_id="c2", run_id=child)
        self.finish_turn(board, helper, disposition="attention")
        parent = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(parent["activeRequest"]["kind"], "helper-attention")
        cancelled = board.call(
            "workflow_cancel", {"runId": submitted["runId"], 'note': "stop"}
        )
        self.assertEqual(cancelled["state"], "cancelled")
        child_view = board.call("workflow_get", {"runId": child, "includeAudit": True})
        self.assertEqual(child_view["state"], "cancelled")
        self.assertEqual([row["state"] for row in child_view["audit"]["reservations"]], ["released"])

    def test_nested_dependency_is_cancelled_recursively(self):
        board = self.board()
        root = self.workdir("repo")
        (root / ".git").mkdir()
        submitted, view = self._yielded_parent(board, root)
        child = self._approve_helper(board, view, submitted, root)
        helper = self.claim(board, claim_request_id="c2", run_id=child)
        self.finish_turn(board, helper, disposition="attention")
        parent = board.call("workflow_get", {"runId": submitted["runId"]})
        nested = self.decide(
            board,
            parent,
            parent["activeRequest"]["requestId"],

            helpers=[
                {
                    "title": "dependency-1",
                    "task": "dependency work",
                    "cwd": str(self.workdir("dep")),
                    "workspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        self.assertEqual(nested["state"], "waiting-helpers")
        dependency = board.call("workflow_get", {"runId": child})["children"][0]["taskId"]
        cancelled = board.call(
            "workflow_cancel", {"runId": submitted["runId"], 'note': "stop"}
        )
        self.assertEqual(cancelled["state"], "cancelled")
        for task_id in (child, dependency):
            view = board.call("workflow_get", {"runId": task_id})
            self.assertEqual(view["state"], "cancelled", task_id)
        # The never-started dependency released its worktree reservation.
        audit = board.call("workflow_get", {"runId": child, "includeAudit": True})
        states = {(row["holderTaskId"], row["state"]) for row in audit["audit"]["reservations"]}
        self.assertIn((dependency, "released"), states)

    def test_active_child_with_unconfirmed_stop_retains_its_reservation(self):
        board = self.board()
        root = self.workdir("repo")
        (root / ".git").mkdir()
        submitted, view = self._yielded_parent(board, root)
        child = self._approve_helper(board, view, submitted, root)
        helper = self.claim(board, claim_request_id="c2", run_id=child)
        approved = board.call("workflow_get", {"runId": submitted["runId"]})
        cancelled = board.call(
            "workflow_cancel", {"runId": submitted["runId"], 'note': "stop"}
        )
        self.assertEqual(cancelled["state"], "cancelled")
        child_view = board.call("workflow_get", {"runId": child})
        self.assertEqual(child_view["state"], "cancelled")
        self.assertEqual(child_view["task"]["state"], "cancelling")
        audit = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        states = {(row["holderTaskId"], row["state"]) for row in audit["audit"]["reservations"]}
        self.assertIn((child, "held"), states)
        # A late result with unconfirmed shutdown never revives the ancestor.
        self.finish_turn(board, helper, shutdown=False, seal=False)
        parent = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(parent["state"], "cancelled")
        audit = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        states = {(row["holderTaskId"], row["state"]) for row in audit["audit"]["reservations"]}
        # No release happens without a proven stop, so the reservation stays held.
        self.assertIn((child, "held"), states)
