"""DSH native storage rides the common prepare, private-dir, backup and
reclamation rules; the capability under test stays a candidate injection.

The real ``WorkerRunExecutor.prepare`` runs here over the real dsh run seam with
the explicit fake-selection fixture (a ready selected record); no installed
harness, no controller process, no model call and no native ACP path is
exercised. The candidate DshAdapter instance below carries ``native_resume``
``True`` only inside these tests, and the false-capability paths use their own
explicitly ``False`` local instance, so no case depends on the product class's
current declared value; every injecting case captures that declaration before
and after the injection and compares it instead of pinning it. The storage
cases reuse this suite's governed-run fixture and the general storage and backup
rules; no new reclamation protocol is added. Fine-grained DSH_HOME isolation and
the per-turn native facts belong to the candidate-native work and the Host's
native checks, and only this file's own dummy credential bytes appear anywhere.
"""
from __future__ import annotations

import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.buddy.harnesses.base import ExecutionContext
from hey_my_buddy.buddy.harnesses.dsh.adapter import DshAdapter
from hey_my_buddy.buddy.harnesses.registry import adapter as registry_adapter, run_seam
from hey_my_buddy.buddy.roles.run_execution import WorkerRunExecutor
from hey_my_buddy.errors import BoardError
from hey_my_buddy.json_codec import decode_strict_json
from hey_my_buddy.private_dirs import context_root, native_root

from blackboard.tasks.test_workflow import WorkflowTestCase
from support import BoardTestCase

READY_SELECTION = {"status": "ready", "command": ["/fixture/dsh"]}
DUMMY_CREDENTIAL = "fixture-dummy-credential"
ROLLOUT_BYTES = b"fixture rollout bytes"


class CandidatePrepareTests(BoardTestCase):
    """Real prepare over a private state root; the goal native root is shared."""

    def setUp(self) -> None:
        super().setUp()
        self.state = self.directory / "state"
        self.state.mkdir(mode=0o700)
        cleared = {"BUDDY_STATE_DIR", "BUDDY_RUNTIME_ROOT", "BUDDY_RUNTIME", "BUDDY_RUNTIME_IDENTITY",
                   "BUDDY_WORKER_STATE", "BUDDY_WORKER_ID", "BUDDY_AGENT_CREDENTIAL",
                   "BUDDY_AGENT_CREDENTIAL_FILE", "BUDDY_CHECKS_TMPDIR", "VIRTUAL_ENV",
                   "UV_PROJECT_ENVIRONMENT"}
        self.environment = {key: value for key, value in os.environ.items()
                            if key not in cleared and not key.startswith("ANTHROPIC_")}
        self.environment["BUDDY_STATE_DIR"] = str(self.state)

    def candidate_executor(self) -> WorkerRunExecutor:
        # Candidate-only injection: a fresh instance carries native_resume True,
        # an override on that instance alone. The registered product adapter is
        # never touched; each case compares its declared value across the
        # injection instead of asserting any particular value.
        candidate = DshAdapter()
        candidate.native_resume = True
        return WorkerRunExecutor(candidate, run_seam("dsh"))

    def false_capability_executor(self) -> WorkerRunExecutor:
        # An explicit local instance with the capability off: the old
        # attempt-private prepare path never relies on the product's default.
        false_instance = DshAdapter()
        false_instance.native_resume = False
        return WorkerRunExecutor(false_instance, run_seam("dsh"))

    def assert_declaration_stable(self, before) -> None:
        self.assertEqual(registry_adapter("dsh").native_resume, before,
                         "the executor instance injection must not change the product declaration")

    def context(self, task_id: str, attempt_id: str, *, mode: str, previous: str | None = None) -> ExecutionContext:
        identity = {"version": 1, "taskId": task_id, "attemptId": attempt_id, "generation": 1,
                    "turnId": "turn-1", "resumeMode": mode, "previousSessionId": previous,
                    "context": {}, "executionWorkspace": {}}
        return ExecutionContext(
            task_id=task_id, attempt_id=attempt_id, generation=1,
            spec={"cwd": str(self.workdir()), "task": "fixture governed task", "timeoutSeconds": 30,
                  "provider": "fixture-provider", "model": "fixture-model", "effort": "low"},
            directory=self.directory / "turns" / attempt_id, runtime={},
            environment=dict(self.environment), agent_credential=DUMMY_CREDENTIAL,
            turn={"turnId": "turn-1", "input": identity})

    def prepare(self, executor: WorkerRunExecutor, context: ExecutionContext) -> None:
        from hey_my_buddy.buddy.harnesses import runtime_selection
        with mock.patch.object(runtime_selection, "selected", return_value=dict(READY_SELECTION)):
            executor.prepare(context)

    def control(self, context: ExecutionContext) -> dict:
        return decode_strict_json((context_root(context, "dsh") / "role-run-control.json").read_bytes())

    def test_candidate_native_resume_prepares_one_goal_native_root_for_both_attempts(self):
        before = registry_adapter("dsh").native_resume
        first = self.context("task-goal", "attempt-a1", mode="initial")
        second = self.context("task-goal", "attempt-a2", mode="native-session", previous="sess-1")
        executor = self.candidate_executor()
        self.prepare(executor, first)
        self.prepare(executor, second)
        self.assert_declaration_stable(before)

        expected = str(native_root(self.state, "dsh", "task-goal"))
        first_root, second_root = context_root(first, "dsh"), context_root(second, "dsh")
        first_control, second_control = self.control(first), self.control(second)
        self.assertEqual(first_control["nativeRoot"], expected)
        self.assertEqual(second_control["nativeRoot"], expected)
        self.assertEqual(first_control["privateRoot"], str(first_root))
        self.assertEqual(second_control["privateRoot"], str(second_root))
        self.assertNotEqual(first_root, second_root)
        native = Path(expected)
        self.assertTrue(native.is_dir())
        self.assertEqual(stat.S_IMODE(native.stat().st_mode) & 0o777, 0o700)

        # Each attempt still holds its own input, control and credential
        # reference; only the dummy token this test minted exists anywhere.
        for context, root, entry in ((first, first_root, first_control), (second, second_root, second_control)):
            self.assertEqual(entry["inputFile"], str(context.turn_input_file()))
            self.assertEqual(entry["outputFile"], str(context.turn_output_file()))
            self.assertEqual(entry["cwd"], str(Path(self.workdir())))
            self.assertTrue((context.directory / "turn-input.json").is_file())
            self.assertTrue((context.directory / "task.txt").is_file())
            self.assertTrue((root / "role-run-control.json").is_file())
            credential = root / "agent-credential.json"
            self.assertTrue(credential.is_file())
            document = decode_strict_json(credential.read_bytes())
            self.assertEqual(document["token"], DUMMY_CREDENTIAL)
            self.assertEqual(document["attemptId"], context.attempt_id)
            self.assertEqual(context.environment.get("BUDDY_AGENT_CREDENTIAL_FILE"), str(credential))

    def test_different_tasks_prepare_isolated_native_roots(self):
        before = registry_adapter("dsh").native_resume
        one = self.context("task-one", "attempt-a1", mode="native-session", previous="sess-1")
        two = self.context("task-two", "attempt-a2", mode="native-session", previous="sess-1")
        executor = self.candidate_executor()
        self.prepare(executor, one)
        self.prepare(executor, two)
        self.assert_declaration_stable(before)
        one_control, two_control = self.control(one), self.control(two)
        self.assertEqual(one_control["nativeRoot"], str(native_root(self.state, "dsh", "task-one")))
        self.assertEqual(two_control["nativeRoot"], str(native_root(self.state, "dsh", "task-two")))
        self.assertNotEqual(one_control["nativeRoot"], two_control["nativeRoot"])

    def test_a_false_capability_instance_keeps_attempt_private_native_paths(self):
        before = registry_adapter("dsh").native_resume
        executor = self.false_capability_executor()
        first = self.context("task-goal", "attempt-a1", mode="reconstructed-new-session")
        second = self.context("task-goal", "attempt-a2", mode="reconstructed-new-session")
        self.prepare(executor, first)
        self.prepare(executor, second)
        self.assert_declaration_stable(before)
        first_control, second_control = self.control(first), self.control(second)
        self.assertEqual(first_control["nativeRoot"], str(context_root(first, "dsh") / "dsh-private"))
        self.assertEqual(second_control["nativeRoot"], str(context_root(second, "dsh") / "dsh-private"))
        self.assertNotEqual(first_control["nativeRoot"], second_control["nativeRoot"])
        self.assertNotEqual(first_control["nativeRoot"], str(native_root(self.state, "dsh", "task-goal")))

        # A false-capability executor still refuses a native-session turn.
        refused = self.context("task-goal", "attempt-a3", mode="native-session", previous="sess-1")
        with self.assertRaises(BoardError) as caught:
            self.prepare(executor, refused)
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertIn("native-session", caught.exception.message)


class DshNativeStorageRulesTests(WorkflowTestCase):
    """The general backup and storage rules over a real governed dsh run's goal."""

    def setUp(self) -> None:
        super().setUp()
        inventory = mock.patch("hey_my_buddy.blackboard.tasks.storage.process_inventory",
                               return_value=([], [], True))
        inventory.start()
        self.addCleanup(inventory.stop)

    def goal_row(self, planned: dict, goal_dir: Path) -> dict:
        return next(row for row in planned["candidates"]
                    if row["category"] == "harnesses" and row["adapter"] == "dsh"
                    and row["path"] == str(goal_dir))

    def test_goal_native_state_stays_out_of_the_verified_backup(self):
        board = self.board()
        state = board.directory.resolve()
        rollout = native_root(state, "dsh", "task-b") / "sessions" / "session.v3.jsonl.zstd"
        rollout.parent.mkdir(parents=True)
        rollout.write_bytes(ROLLOUT_BYTES)
        first = board.call("backup", {})
        self.assertTrue(first["verified"])
        manifest = backup.verify(Path(first["path"]))
        self.assertFalse(any("harnesses" in name for name in manifest["files"]))
        self.assertFalse((Path(first["path"]) / "state" / "harnesses").exists())
        self.assertEqual(rollout.read_bytes(), ROLLOUT_BYTES)

    def test_awaiting_and_grace_goals_are_kept_and_reclaim_takes_only_the_qualified_goal(self):
        board = self.board()
        state = board.directory.resolve()
        self.register(board)
        submitted = self.submit(board)
        run_id = submitted["runId"]
        goal_dir = native_root(state, "dsh", run_id).parent
        rollout = native_root(state, "dsh", run_id) / "sessions" / "session.v3.jsonl.zstd"
        rollout.parent.mkdir(parents=True)
        rollout.write_bytes(ROLLOUT_BYTES)
        foreign_native = native_root(state, "dsh", "foreign-task")
        foreign_native.mkdir(parents=True)
        foreign_binding = foreign_native / "binding.json"
        foreign_binding.write_bytes(b"fixture foreign binding")

        # Awaiting the Host on an open attention request: the goal is retained,
        # and the unknown-owner goal beside it is never a reclaim candidate.
        first = self.claim(board)
        self.finish_turn(board, first, disposition="attention", session_id="dsh-native-1")
        view = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(view["state"], "awaiting-host")
        foreign_goal = foreign_native.parent
        planned = board.call("storage_plan", {})
        row = self.goal_row(planned, goal_dir)
        self.assertFalse(row["eligible"])
        self.assertIn("continuation-supported", row["reasons"])
        foreign_row = next(entry for entry in planned["candidates"]
                           if entry["category"] == "harnesses" and entry["path"] == str(foreign_goal))
        self.assertFalse(foreign_row["eligible"])
        self.assertIn("owner-unproven", foreign_row["reasons"])
        board.call("storage_apply", {"planId": planned["planId"], "commandId": "keep-awaiting", "confirm": True})
        self.assertEqual(rollout.read_bytes(), ROLLOUT_BYTES)
        self.assertEqual(foreign_binding.read_bytes(), b"fixture foreign binding")

        # Delivered but not yet accepted: still retained.
        self.continue_run(board, view, command_id="continue-to-completion")
        second = self.claim(board, claim_request_id="c2")
        self.finish_turn(board, second, session_id="dsh-native-2")
        delivered = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(delivered["state"], "delivered")
        planned = board.call("storage_plan", {})
        row = self.goal_row(planned, goal_dir)
        self.assertFalse(row["eligible"])
        self.assertIn("continuation-supported", row["reasons"])

        # Accepted but inside the retention grace period: still retained.
        self.accept_run(board, delivered)
        planned = board.call("storage_plan", {})
        row = self.goal_row(planned, goal_dir)
        self.assertFalse(row["eligible"])
        self.assertIn("grace-period", row["reasons"])
        board.call("storage_apply", {"planId": planned["planId"], "commandId": "keep-grace", "confirm": True})
        self.assertEqual(rollout.read_bytes(), ROLLOUT_BYTES)
        self.assertEqual(foreign_binding.read_bytes(), b"fixture foreign binding")

        # Past grace with proven stop: exactly this run's own goal reclaims, and
        # the unknown-owner goal beside it stays untouched.
        with board.store.db.write() as db:
            db.execute("UPDATE tasks SET accepted_at=? WHERE task_id=?",
                       ((datetime.now(timezone.utc) - timedelta(days=4)).isoformat(), run_id))
        planned = board.call("storage_plan", {})
        row = self.goal_row(planned, goal_dir)
        self.assertTrue(row["eligible"])
        self.assertEqual(row["reasons"], [])
        foreign_row = next(entry for entry in planned["candidates"]
                           if entry["category"] == "harnesses" and entry["path"] == str(foreign_goal))
        self.assertFalse(foreign_row["eligible"])
        self.assertIn("owner-unproven", foreign_row["reasons"])
        result = board.call("storage_apply",
                            {"planId": planned["planId"], "commandId": "reclaim-own-goal", "confirm": True})
        self.assertEqual([entry["path"] for entry in result["removed"]], [str(goal_dir)])
        self.assertFalse(goal_dir.exists())
        self.assertEqual(foreign_binding.read_bytes(), b"fixture foreign binding")


if __name__ == "__main__":
    import unittest
    unittest.main()
