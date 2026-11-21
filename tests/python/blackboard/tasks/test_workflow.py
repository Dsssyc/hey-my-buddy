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
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mock_workspace import MockWorkspace
from protocol.fixtures import native_turn
from support import BoardTestCase, FakeClock, enable_fixture_buddy

from hey_my_buddy.blackboard.tasks import workflow as workflow_module
from hey_my_buddy.cli import cli_views
from hey_my_buddy.errors import BoardError
from hey_my_buddy.buddy.harnesses.base import ExecutionContext

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
            selected_buddy = " / ".join(selected_buddy[key] for key in BUDDY)
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
                        "neededWork": "decide the schema",
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

    def continue_run(self, board, view, *, input="keep going", **extra):
        if extra.get("buddy") is not None:
            from support import enable_fixture_buddy
            if isinstance(extra["buddy"], dict):
                enable_fixture_buddy(board.store, extra["buddy"])
                extra["buddy"] = " / ".join(extra["buddy"][key] for key in BUDDY)
            extra.setdefault("note", "Use the explicit fixture buddy for this continuation")
        params = {
            "runId": view["runId"],
            "input": input,
            **extra,
        }
        if not any(key in params for key in ("requestId", "artifactId", "turnId")):
            fresh = board.call("workflow_get", {"runId": view["runId"]})
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
    def test_current_turn_tracks_execution_and_latest_activity_until_settlement(self):
        clock = FakeClock()
        board = self.board(clock=clock)
        for entry in ("progress", "connection"):
            with self.subTest(entry=entry):
                submitted = self.submit(board, request_id=entry, cwd=str(self.workdir(entry)))
                run_id = submitted["runId"]
                claim = self.claim(board, run_id=run_id)
                attempt = claim["claim"]["attempt"]
                attempt_id = attempt["attemptId"]
                actor = {"attemptId": attempt_id, "generation": attempt["generation"], "nonce": NONCE}
                if entry == "connection":
                    self.assertTrue(board.store.execution_connection(attempt_id, connected=False))

                prepared = board.call("workflow_get", {"runId": run_id})
                self.assertEqual(prepared["currentTurn"]["state"], "prepared")
                self.assertNotIn("activity", prepared["currentTurn"])
                entered_at = clock.advance(1)
                if entry == "progress":
                    board.call("execution_progress", {**actor, "phase": "executing"})
                else:
                    self.assertTrue(board.store.execution_connection(attempt_id, connected=True))
                running = board.call("workflow_get", {"runId": run_id})
                self.assertEqual(running["currentTurn"]["state"], "running")
                self.assertEqual(running["currentTurn"]["updatedAt"], entered_at)
                self.assertEqual(running["updatedAt"], entered_at)
                self.assertGreater(entered_at, prepared["currentTurn"]["updatedAt"])
                self.assertGreater(entered_at, prepared["updatedAt"])
                self.assertNotIn("activity", running["currentTurn"])

                board.call("execution_progress", {**actor, "data": {"activity": {
                    "phase": "streaming-model", "observedAt": clock.advance(1), "eventSeq": 1,
                }}})
                observed_at = clock.advance(1)
                activity = {"phase": "tool-running", "observedAt": observed_at,
                            "lastNativeActivityAt": observed_at, "lastToolActivityAt": observed_at,
                            "toolName": "read", "counts": {"toolCalls": 1}}
                native = {**activity, "eventSeq": 2, "nativeSessionId": "native-session"}
                board.call("execution_progress", {**actor, "data": {"activity": native}})
                running = board.call("workflow_get", {"runId": run_id})
                self.assertEqual(running["task"]["activity"], native)
                for mode in ("brief", "full"):
                    self.assertEqual(cli_views.render("get", running, mode)["currentTurn"]["activity"], activity)

                settled_at = clock.advance(1)
                self.finish_turn(board, claim)
                self.assertFalse(board.store.execution_connection(attempt_id, connected=True))
                with self.assertRaises(BoardError) as late:
                    board.call("execution_progress", {**actor, "phase": "executing"})
                self.assertEqual(late.exception.code, "ATTEMPT_FINISHED")
                settled = board.call("workflow_get", {"runId": run_id})
                self.assertIsNone(settled["task"]["activeAttemptId"])
                self.assertEqual(settled["task"]["activity"], native)
                self.assertEqual(settled["updatedAt"], settled_at)
                for mode in ("brief", "full"):
                    current = cli_views.render("get", settled, mode)["currentTurn"]
                    self.assertEqual(current["state"], "returned")
                    self.assertEqual(current["updatedAt"], settled_at)
                    self.assertNotIn("activity", current)

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
                expected = {**BUDDY, 'alias': None} if adapter == 'dsh' else None
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
        self.assertIn("current turn fields", receipt["microTaskError"]["message"])

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


    def test_one_slot_progress_serializes_turns(self):
        board = self.board(max_concurrent=1)
        self.register(board)
        self.register(board, worker_id="w2")
        submitted = self.submit(board)
        run_id = submitted["runId"]
        first = self.claim(board)
        self.finish_turn(board, first, disposition="attention")
        view = board.call("workflow_get", {"runId": run_id})
        continued = self.continue_run(board, view)
        other = self.submit(board, request_id="other", cwd=str(self.workdir("other")))
        children = [continued["runId"], other["runId"]]

        resumed = self.claim(board, claim_request_id="c2", run_id=children[0])
        self.assertIsNotNone(resumed["claim"])
        busy = self.claim(board, worker_id="w2", claim_request_id="c3", run_id=children[1])
        self.assertIsNone(busy["claim"])
        self.assertEqual(busy["reason"], "capacity")
        self.finish_turn(board, resumed)
        next_run = self.claim(board, worker_id="w2", claim_request_id="c4", run_id=children[1])
        self.assertEqual(next_run["claim"]["attempt"]["taskId"], children[1])

    def test_replayed_continuation_and_result_create_no_duplicate(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        run_id = submitted["runId"]
        first = self.claim(board)
        yielded = self.finish_turn(board, first, disposition="attention")
        view = board.call("workflow_get", {"runId": run_id})
        request_id = view["activeRequest"]["requestId"]
        params = {
            "runId": run_id,
            "requestId": request_id,
            "input": "Proceed with the work",
        }
        first_decide = board.call("workflow_continue", params)
        replay = board.call("workflow_continue", params)
        self.assertTrue(replay["alreadyResponded"])
        self.assertEqual(replay["state"], first_decide["state"])
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_continue", {**params, "input": "Use the current findings"})
        self.assertEqual(raised.exception.code, "CONFLICT")

        # Replaying the turn result cannot queue another continuation.
        self.assertEqual(first_decide["state"], "executing")
        with self.assertRaises(BoardError) as caught:
            self.finish_turn(board, first, disposition="attention")
        self.assertEqual(caught.exception.code, "ATTEMPT_FINISHED")
        after = board.call("workflow_get", {"runId": run_id, "includeAudit": True})
        self.assertEqual([turn["turnIndex"] for turn in after["audit"]["turns"]], [1])
        self.assertEqual(len(after["turns"]), 1)

    def test_host_input_and_note_are_frozen_on_the_next_turn(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        run_id = submitted["runId"]
        first = self.claim(board)
        self.finish_turn(board, first, disposition="attention")
        view = board.call("workflow_get", {"runId": run_id})
        continued = self.continue_run(
            board, view, input="Use the existing schema",
            note="The proposed schema does not meet the review criteria",
        )
        self.assertEqual(continued["state"], "executing")
        self.assertIsNone(continued["activeRequest"])
        second = self.claim(board, claim_request_id="c2")
        context = second["claim"]["turn"]["input"]["context"]
        self.assertEqual(context["continuation"]["note"], "The proposed schema does not meet the review criteria")
        self.assertEqual(context["continuation"]["kind"], "manual")

    def test_host_boundary_response_uses_only_the_request_identity(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        run_id = submitted["runId"]
        self.finish_turn(board, self.claim(board), disposition="attention")
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
                    "input": "Use the current findings",
                    "controlToken": "obsolete",
                },
            )
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        decided = board.call(
            "workflow_continue",
            {
                "runId": run_id,
                "requestId": view["activeRequest"]["requestId"],
                "input": "Use the current findings",
            },
        )
        self.assertEqual(decided["state"], "executing")
        self.assertEqual(decided["requestId"], view["activeRequest"]["requestId"])

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
                    "input": "Proceed with the work",
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
        execution = board.submit_execution(**{"requestId": "execution-1", "adapter": "command", "argv": ["/bin/true"], "task": "plain", "cwd": str(self.workdir("execution")),
                                              "expectedSeconds": 1800})
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
        self.finish_turn(board, self.claim(board), disposition="attention")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        return submitted, view

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
                    "input": "Proceed with the work",
                },
            )
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(raised.exception.details["field"], "expectedRevision")
        self.assertEqual(len(self.workspace.prepare_calls), before)

    def test_replayed_continuation_does_not_prepare_again(self):
        board = self.board()
        submitted, view = self._yielded(board)
        params = {"runId": view["runId"], "requestId": view["activeRequest"]["requestId"],
                  "input": "Proceed with the work"}
        board.call("workflow_continue", params)
        after_first = len(self.workspace.prepare_calls)
        replay = board.call("workflow_continue", params)
        self.assertTrue(replay["alreadyResponded"])
        self.assertEqual(len(self.workspace.prepare_calls), after_first)


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
        self.finish_turn(board, self.claim(board), disposition="attention")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(set(view["truncated"]), {"turns", "requests", "ownedMicroTasks", "artifacts", "pendingRequests"})
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

class PriorSessionClaimTests(WorkflowTestCase):
    def test_admission_freezes_only_this_run_prior_attempts(self):
        board = self.board()
        run = self.submit(board, create_macro={"title": "Session history", "goal": "Continue the work"})
        run_id = run["runId"]
        initial = self.claim(board, run_id=run_id)
        first = initial["claim"]
        self.assertEqual(first["turn"]["priorSessions"], [])
        first_path = Path(first["sessionRecordPath"])
        first_path.parent.mkdir(parents=True, exist_ok=True)
        first_path.touch()
        self.finish_turn(board, initial, disposition="attention")
        other = self.submit(board, macroTaskId=run["macroTaskId"], request_id="other", kind="worktree")
        other_claim = self.claim(board, run_id=other["runId"])
        Path(other_claim["claim"]["sessionRecordPath"]).touch()
        self.finish_turn(board, other_claim)

        view = board.call("workflow_get", {"runId": run_id})
        self.continue_run(board, view)
        store = board.store
        first_attempt_id = first["attempt"]["attemptId"]
        other_buddy = {"harness": "codex", "provider": "openai", "model": "gpt-5.4", "effort": "high"}
        # Exercise historical forms together without manufacturing additional
        # live executions or relying on a harness to fail at a specific point.
        with store.db.write() as connection:
            attempt = dict(connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (first_attempt_id,)).fetchone())
            turn = dict(connection.execute("SELECT * FROM workflow_turns WHERE attempt_id=?", (first_attempt_id,)).fetchone())
            for index, state, mode in (
                (2, "interrupted", "available"), (3, "failed", "stopped"),
                (4, "rejected", "available"), (5, "failed", "unintegrated"),
                (6, "failed", "missing"), (7, "failed", "ungrouped"),
            ):
                attempt_id, turn_id = f"prior-{index}", f"turn-{index}"
                adapter = "command" if mode == "unintegrated" else other_buddy["harness"]
                group = None if mode == "ungrouped" else attempt["session_group_id"]
                path = first_path.with_name(attempt_id + ".jsonl")
                result = None
                if mode == "available":
                    path.touch()
                if mode == "stopped":
                    result = json.dumps({"result": {"sessionRecordFailure": {
                        "code": "session-record-write-failed", "params": {"message": str(path)},
                    }}})
                connection.execute(
                    "INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,"
                    " adapter,session_group_id,result_json,result_form,started_at,finished_at,created_at,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (attempt_id, run_id, index, "fixture", "finished", adapter, group, result,
                     "current-report-v2" if result else None, attempt["started_at"], attempt["finished_at"],
                     attempt["created_at"], attempt["updated_at"]),
                )
                turn_input = json.loads(turn["input_json"])
                turn_input.update(attemptId=attempt_id, turnId=turn_id, generation=index)
                turn_input["context"]["buddy"] = other_buddy
                connection.execute(
                    "INSERT INTO workflow_turns(turn_id,run_id,attempt_id,generation,turn_index,resume_mode,"
                    " input_json,workspace_id,workspace_manifest_sha256,access,expected_seconds,state,created_at,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (turn_id, run_id, attempt_id, index, index, turn["resume_mode"], json.dumps(turn_input),
                     turn["workspace_id"], turn["workspace_manifest_sha256"], turn["access"],
                     turn["expected_seconds"], state, turn["created_at"], turn["updated_at"]),
                )

        admitted = self.claim(board, run_id=run_id)["claim"]
        manifest = admitted["turn"]["priorSessions"]
        self.assertEqual(
            [(item["attemptId"], item["turnState"], item["unavailableReason"], item["recordAvailable"]) for item in manifest],
            [("prior-7", "failed", "not-recorded", False), ("prior-6", "failed", "not-recorded", False),
             ("prior-5", "failed", "not-integrated", False), ("prior-3", "failed", "stopped", False),
             ("prior-2", "interrupted", None, True), (first_attempt_id, "returned", None, True)],
        )
        interrupted, returned = manifest[-2:]
        self.assertEqual({key: interrupted[key] for key in other_buddy}, other_buddy)
        self.assertEqual({key: returned[key] for key in BUDDY}, BUDDY)
        self.assertEqual(returned["disposition"], "attention")
        self.assertEqual(returned["turnId"], first["turn"]["turnId"])
        self.assertEqual(returned["recordPath"], str(first_path))
        self.assertEqual(manifest[3]["stopped"], {"code": "session-record-write-failed"})
        self.assertTrue(all(item["recordPath"] is None for item in manifest[:4]))

        context = ExecutionContext(run_id, admitted["attempt"]["attemptId"], admitted["attempt"]["generation"],
                                   {}, board.directory, {}, {}, turn=admitted["turn"])
        self.assertEqual(context.prior_sessions, manifest)
        context.prior_sessions.clear()
        first_path.unlink()
        self.assertEqual(context.prior_sessions, manifest)
        self.assertTrue(context.prior_sessions[-1]["recordAvailable"])
        with store.db.write() as connection:
            connection.execute(
                "INSERT INTO macro_task_deletions VALUES(?, 'macro-task', ?)", ("deletion", run["macroTaskId"]),
            )
            deleted = store.workflow.prior_sessions_for_turn(connection, {"run_id": run_id}, context.attempt_id)
        self.assertEqual([item["attemptId"] for item in deleted], [item["attemptId"] for item in manifest])
        self.assertTrue(all(not item["recordAvailable"] and item["recordPath"] is None
                            and item["unavailableReason"] == "deleted" for item in deleted))


class ContinuationWorkspaceTests(WorkflowTestCase):
    def test_continuation_prepares_a_new_manifest_from_the_sealed_output(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        first = self.claim(board)
        first_manifest = turn_manifest(self.directory, first["claim"]["turn"]["input"])
        self.finish_turn(board, first, disposition="attention", session_id="sess-1")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, view)
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
        # An attention boundary whose turn never sealed an output is not a final
        # artifact, so the Host cannot finish the micro task from it.
        self.finish_turn(board, self.claim(board), disposition="attention", seal=False)
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
            "disposition": "attention",
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
            disposition="attention",
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
        with board.store.db.read() as connection:
            count = connection.execute("SELECT COUNT(*) FROM workflow_artifacts WHERE run_id=?",
                                       (submitted["runId"],)).fetchone()[0]
        self.assertGreater(count, 8)
        claim = self.claim(board, claim_request_id="complete-context", run_id=submitted["runId"])
        turn = claim["claim"]["turn"]
        context = turn["input"]["context"]
        self.assertEqual(turn["input"]["version"], 4)
        self.assertNotIn("task", context)
        self.assertNotIn("helperOutcomes", context)
        # Only the newest handoff summary and the run's actual artifact row count cross the boundary.
        self.assertNotIn("artifacts", context)
        self.assertEqual(context["artifactsTotal"], count)
        self.assertIsNotNone(context["latestHandoff"])
        self.assertNotIn("changedPaths", context["latestHandoff"])
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
