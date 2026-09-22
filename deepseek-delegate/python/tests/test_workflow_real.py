"""Real workspace module + real DSH adapter, end to end over actual Git.

These tests exercise the merged ``buddy.workspace`` implementation and the real
``DshAdapter`` turn staging/import/sealing logic. Only the model process is absent:
Git, manifests, the turn input file and the shutdown gate are real.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import unittest
from pathlib import Path

from support import BoardTestCase

from buddy import workspace
from buddy.adapters.base import ExecutionContext, ProcessHandle
from buddy.adapters.dsh import DshAdapter
from buddy.db import canonical_json, sha256_text

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Buddy Test",
    "GIT_AUTHOR_EMAIL": "buddy@example.invalid",
    "GIT_COMMITTER_NAME": "Buddy Test",
    "GIT_COMMITTER_EMAIL": "buddy@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


class RealWorkspaceTestCase(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.directory / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "buddy@example.invalid")
        self.git("config", "user.name", "Buddy Test")
        (self.repo / "tracked.txt").write_text("base\n")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "feature.py").write_text("value = 1\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")

    def git(self, *arguments: str, check: bool = True) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=self.repo,
            env={**os.environ, **GIT_ENV},
            capture_output=True,
            text=True,
        )
        if check and completed.returncode != 0:
            raise AssertionError(f"git {' '.join(arguments)} failed: {completed.stderr}")
        return completed.stdout

    def state(self) -> dict:
        return {
            "head": self.git("rev-parse", "HEAD").strip(),
            "status": self.git("status", "--porcelain=v1"),
            "staged": self.git("diff", "--cached"),
            "unstaged": self.git("diff"),
        }

    def _intent(self, request_id: str, *, kind: str = "existing", cwd=None, access: str = "write") -> dict:
        """Exactly the normalized intent the service prepares with (so prepare is idempotent)."""
        intent = {
            "kind": kind,
            "cwd": str(cwd or self.repo),
            "access": access,
            "base": {"kind": "working-tree"},
            "integrator": "host:host-1",
        }
        if access == "write":
            intent["writeScope"] = ["."]
        return intent

    def manifest_for(self, request_id: str, **options) -> dict:
        return workspace.prepare(self.directory, request_id, self._intent(request_id, **options))

    def submit(self, board, *, request_id="req-1", kind="existing", cwd=None, access="write"):
        return board.call(
            "workflow_submit",
            {
                "requestId": request_id,
                "hostId": "host-1",
                "task": "real workspace task",
                "cwd": str(cwd or self.repo),
                "submissionToken": "t" * 32,
                "executionWorkspace": {"kind": kind, "access": access},
            },
        )


class DirtyInputTests(RealWorkspaceTestCase):
    def test_submit_transports_dirty_input_without_changing_the_source_index(self):
        (self.repo / "tracked.txt").write_text("staged change\n")
        self.git("add", "tracked.txt")
        (self.repo / "src" / "feature.py").write_text("value = 2\n")
        (self.repo / "untracked.txt").write_text("new file\n")
        before = self.state()
        board = self.board()
        submitted = self.submit(board)
        after = self.state()
        self.assertEqual(before, after, "the source HEAD, index and worktree must not change")
        manifest = self.manifest_for("req-1")
        self.assertEqual(submitted["workspace"]["path"], os.path.realpath(self.repo))
        self.assertEqual(manifest["path"], os.path.realpath(self.repo))
        self.assertEqual(manifest["baseCommit"], before["head"])
        self.assertTrue(manifest["snapshot"]["stagedTree"])
        self.assertNotEqual(manifest["snapshot"]["executionFingerprint"], "")
        verified = workspace.verify(manifest, require_unchanged=True)
        self.assertTrue(verified["valid"])
        self.assertTrue(verified["unchanged"])
        task = board.call("task_get", {"runId": submitted["runId"]})["task"]
        self.assertEqual(task["cwd"], manifest["path"])
        self.assertEqual(task["spec"]["cwd"], os.path.realpath(self.repo))

    def test_worktree_helpers_get_distinct_effective_checkouts(self):
        board = self.board(max_concurrent=2)
        first = self.submit(board, request_id="req-1", kind="worktree")
        second = self.submit(board, request_id="req-2", kind="worktree")
        first_manifest = self.manifest_for("req-1", kind="worktree")
        second_manifest = self.manifest_for("req-2", kind="worktree")
        self.assertNotEqual(first_manifest["path"], second_manifest["path"])
        self.assertNotEqual(first_manifest["checkoutId"], second_manifest["checkoutId"])
        self.assertEqual(first_manifest["repositoryId"], second_manifest["repositoryId"])
        for worker, submitted in (("w1", first), ("w2", second)):
            board.call("worker_register", {"workerId": worker, "capabilities": ["dsh"]})
            claimed = board.call(
                "worker_claim",
                {
                    "workerId": worker,
                    "claimRequestId": f"claim-{worker}",
                    "nonce": f"{worker}x".ljust(16, "y"),
                },
            )
            self.assertIsNotNone(claimed["claim"], claimed)
            self.assertEqual(claimed["claim"]["attempt"]["taskId"], submitted["runId"])
        task = board.call("task_get", {"runId": first["runId"]})["task"]
        self.assertEqual(task["cwd"], first_manifest["path"])
        self.assertEqual(task["spec"]["cwd"], os.path.realpath(self.repo))

    def test_continuation_bases_on_the_sealed_commit_and_seals_new_output(self):
        board = self.board()
        board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh"]})
        submitted = self.submit(board)
        run_id = submitted["runId"]
        manifest = self.manifest_for("req-1")
        claim = board.call("worker_claim", {"workerId": "w1", "claimRequestId": "claim-1", "nonce": "n" * 16})
        turn = claim["claim"]["turn"]
        self.assertEqual(turn["input"]["executionWorkspace"]["manifestSha256"], manifest["manifestSha256"])
        seal = workspace.seal(self.directory, manifest, run_id, claim["claim"]["attempt"]["attemptId"])
        self.assertTrue(seal.get("commit"))
        self.assertTrue(seal.get("snapshotSha256"))
        record = {
            "version": 1,
            "taskId": run_id,
            "attemptId": claim["claim"]["attempt"]["attemptId"],
            "generation": claim["claim"]["attempt"]["generation"],
            "turnId": turn["turnId"],
            "resumeMode": turn["resumeMode"],
            "previousSessionId": None,
            "sessionId": "sess-1",
            "promptSha256": "p" * 64,
            "inputSha256": turn["inputSha256"],
            "outcome": {
                "disposition": "assistance",
                "summary": "need a decision",
                "remaining": ["finish"],
                "decisions": [],
                "artifacts": [],
                "request": {
                    "summary": "review",
                    "attempted": "tried",
                    "neededWork": ["decide"],
                    "expectedArtifacts": ["design"],
                    "acceptance": "approved",
                },
            },
            "provenance": {
                "tool": "buddy_finish_turn",
                "turnEnd": "completed",
                "flush": "awaited",
                "rootSessionMatched": True,
            },
        }
        board.call(
            "worker_result",
            {
                "workerId": "w1",
                "attemptId": claim["claim"]["attempt"]["attemptId"],
                "generation": claim["claim"]["attempt"]["generation"],
                "nonce": "n" * 16,
                "status": "ok",
                "result": {
                    "status": "ok",
                    "processState": {"shutdownConfirmed": True},
                    "turn": record,
                    "turnResultPath": "/tmp/turn.json",
                    "workspaceSeal": seal,
                },
                "shutdownConfirmed": True,
                "exitCode": 0,
            },
        )
        view = board.call("workflow_get", {"runId": run_id})
        output = [row for row in view["artifacts"] if row["kind"] == "output"][0]
        self.assertEqual(output["manifestSha256"], seal["snapshotSha256"])
        self.assertEqual(output["outputCommit"], seal["commit"])
        decided = board.call(
            "workflow_decide",
            {
                "runId": run_id,
                "requestId": view["activeRequest"]["requestId"],
                "commandId": "decide-1",
                "expectedRevision": view["revision"],
                "decision": "decline",
                "reason": "continue on the sealed state",
                **submitted["control"],
            },
        )
        self.assertEqual(decided["state"], "executing")
        second = board.call("worker_claim", {"workerId": "w1", "claimRequestId": "claim-2", "nonce": "m" * 16})
        second_input = second["claim"]["turn"]["input"]["executionWorkspace"]
        self.assertEqual(second_input["baseCommit"], seal["commit"])
        self.assertNotEqual(second_input["manifestSha256"], manifest["manifestSha256"])
        self.assertEqual(second_input["path"], manifest["path"])
        self.assertNotIn("workspaceMode", second["claim"]["turn"]["input"])
        second_seal = workspace.seal(
            self.directory, second_input, run_id, second["claim"]["attempt"]["attemptId"]
        )
        self.assertNotEqual(second_seal["snapshotSha256"], seal["snapshotSha256"])


class RealAdapterTests(RealWorkspaceTestCase):
    def _context(self, run_id: str, manifest: dict) -> ExecutionContext:
        attempt = self.directory / "attempts" / run_id / "attempt-1"
        attempt.mkdir(mode=0o700, parents=True, exist_ok=True)
        return ExecutionContext(
            task_id=run_id,
            attempt_id="attempt-1",
            generation=1,
            spec={"task": "do work", "cwd": manifest["path"], "timeoutSeconds": 60, "workspace": True},
            directory=attempt,
            runtime={"identity": "test"},
            environment={"BUDDY_STATE_DIR": str(self.directory)},
            turn={
                "turnId": "turn-1",
                "turnIndex": 1,
                "resumeMode": "initial",
                "inputSha256": "",
                "input": {
                    "version": 1,
                    "taskId": run_id,
                    "attemptId": "attempt-1",
                    "generation": 1,
                    "turnId": "turn-1",
                    "resumeMode": "initial",
                    "previousSessionId": None,
                    "context": {"objective": "do work"},
                    "executionWorkspace": manifest,
                },
            },
            agent_credential="credential-token",
        )

    def test_adapter_verifies_input_writes_exact_bytes_and_gates_the_seal(self):
        board = self.board()
        submitted = self.submit(board)
        # Prepare a real manifest directly for the adapter context.
        manifest = self.manifest_for("adapter-1")
        context = self._context(submitted["runId"], manifest)
        adapter = DshAdapter()
        verified: list[bool] = []
        real_verify = workspace.verify

        def recording_verify(value, *, require_unchanged=True):
            verified.append(require_unchanged)
            return real_verify(value, require_unchanged=require_unchanged)

        workspace.verify = recording_verify
        try:
            adapter.prepare(context)
        finally:
            workspace.verify = real_verify
        self.assertEqual(verified, [False])
        text = context.turn_input_file().read_text()
        self.assertEqual(text, json.dumps(context.turn_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        self.assertEqual(
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
            sha256_text(canonical_json(context.turn_input)),
        )
        self.assertTrue(context.credential_file().is_file())
        self.assertEqual(context.credential_file().stat().st_mode & 0o777, 0o600)
        # No turn output and no confirmed stop: nothing is imported and nothing sealed.
        record, error = adapter._import_turn(context, False, 0)
        self.assertIsNone(record)
        self.assertIn("shutdown", error or "")
        record, error = adapter._import_turn(context, True, 1)
        self.assertIsNone(record)
        self.assertIn("exit zero", error or "")
        context.turn_output_file().write_text(json.dumps({"version": 1}))
        record, error = adapter._import_turn(context, True, 0)
        self.assertIsNone(record)
        self.assertIn("turn record", error or "")
        # A complete record imports, and sealing produces a real immutable output.
        service_hash = sha256_text(canonical_json(context.turn_input))
        complete = {
            "version": 1,
            "taskId": context.task_id,
            "attemptId": context.attempt_id,
            "generation": context.generation,
            "turnId": "turn-1",
            "resumeMode": "initial",
            "previousSessionId": None,
            "sessionId": "sess-1",
            "promptSha256": "p" * 64,
            "inputSha256": service_hash,
            "outcome": {
                "disposition": "completed",
                "summary": "done",
                "remaining": [],
                "decisions": [],
                "artifacts": [],
                "request": None,
            },
            "provenance": {
                "tool": "buddy_finish_turn",
                "turnEnd": "completed",
                "flush": "awaited",
                "rootSessionMatched": True,
            },
        }
        context.turn_output_file().write_text(json.dumps(complete))
        record, error = adapter._import_turn(context, True, 0)
        self.assertIsNone(error)
        self.assertEqual(record["turnId"], "turn-1")
        seal, seal_error = adapter._seal_workspace(context)
        self.assertIsNone(seal_error)
        self.assertTrue(seal["snapshotSha256"])

    def test_collect_imports_a_turn_only_with_confirmed_shutdown(self):
        board = self.board()
        submitted = self.submit(board)
        manifest = self.manifest_for("adapter-2")
        context = self._context(submitted["runId"], manifest)
        adapter = DshAdapter()
        adapter.prepare(context)
        stdout = Path(context.log_paths()["stdout"])
        stdout.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "logPaths": {"capture": None},
                    "processState": {"shutdownConfirmed": False},
                }
            )
            + "\n"
        )
        process = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
        process.wait()
        handle = ProcessHandle(process, own_group=True, log_paths=context.log_paths())
        outcome = adapter.collect(handle, context)
        self.assertNotEqual(outcome.status, "ok")
        self.assertNotIn("workspaceSeal", outcome.result)
        # With confirmed shutdown the same payload imports the turn and seals.
        stdout.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "logPaths": {"capture": None},
                    "processState": {"shutdownConfirmed": True},
                }
            )
            + "\n"
        )
        service_hash = sha256_text(canonical_json(context.turn_input))
        context.turn_output_file().write_text(
            json.dumps(
                {
                    "version": 1,
                    "taskId": context.task_id,
                    "attemptId": context.attempt_id,
                    "generation": 1,
                    "turnId": "turn-1",
                    "resumeMode": "initial",
                    "previousSessionId": None,
                    "sessionId": "sess-2",
                    "promptSha256": "q" * 64,
                    "inputSha256": service_hash,
                    "outcome": {
                        "disposition": "completed",
                        "summary": "done",
                        "remaining": [],
                        "decisions": [],
                        "artifacts": [],
                        "request": None,
                    },
                    "provenance": {
                        "tool": "buddy_finish_turn",
                        "turnEnd": "completed",
                        "flush": "awaited",
                        "rootSessionMatched": True,
                    },
                }
            )
        )
        outcome = adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.error)
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertEqual(outcome.result["turn"]["turnId"], "turn-1")
        self.assertIn("workspaceSeal", outcome.result)
        self.assertEqual(adapter.workspace_cwd(context), manifest["path"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class TransferredCheckoutContinuationTests(RealWorkspaceTestCase):
    """Real Git: a helper on the transferred checkout reseals the parent's baseline."""

    def _turn_record(self, run_id, claim, disposition, session_id, seal):
        turn = claim["claim"]["turn"]
        return {
            "version": 1,
            "taskId": run_id,
            "attemptId": claim["claim"]["attempt"]["attemptId"],
            "generation": claim["claim"]["attempt"]["generation"],
            "turnId": turn["turnId"],
            "resumeMode": turn["resumeMode"],
            "previousSessionId": turn["input"]["previousSessionId"],
            "sessionId": session_id,
            "promptSha256": "p" * 64,
            "inputSha256": turn["inputSha256"],
            "outcome": {
                "disposition": disposition,
                "summary": f"{disposition} turn",
                "remaining": [] if disposition == "completed" else ["finish the integration"],
                "decisions": [],
                "artifacts": [],
                "request": None
                if disposition == "completed"
                else {
                    "summary": "need guidance",
                    "attempted": "tried",
                    "neededWork": ["decide"],
                    "expectedArtifacts": ["guidance"],
                    "acceptance": "answered",
                },
            },
            "provenance": {
                "tool": "buddy_finish_turn",
                "turnEnd": "completed",
                "flush": "awaited",
                "rootSessionMatched": True,
            },
        }

    def _finish(self, board, run_id, claim, disposition, seal, session_id, nonce="n" * 16):
        record = self._turn_record(run_id, claim, disposition, session_id, seal)
        return board.call(
            "worker_result",
            {
                "workerId": "w1",
                "attemptId": claim["claim"]["attempt"]["attemptId"],
                "generation": claim["claim"]["attempt"]["generation"],
                "nonce": nonce,
                "status": "ok",
                "result": {
                    "status": "ok",
                    "processState": {"shutdownConfirmed": True},
                    "turn": record,
                    "turnResultPath": "/tmp/turn.json",
                    "workspaceSeal": seal,
                },
                "shutdownConfirmed": True,
                "exitCode": 0,
            },
        )

    def test_parent_continuation_bases_on_the_helpers_sealed_commit(self):
        board = self.board()
        board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh"]})
        (self.repo / "dirty.txt").write_text("parent dirty input\n")
        submitted = self.submit(board)
        run_id = submitted["runId"]

        parent_claim = board.call(
            "worker_claim", {"workerId": "w1", "claimRequestId": "claim-1", "nonce": "n" * 16}
        )
        parent_manifest = parent_claim["claim"]["turn"]["input"]["executionWorkspace"]
        parent_seal = workspace.seal(
            self.directory, parent_manifest, run_id, parent_claim["claim"]["attempt"]["attemptId"]
        )
        self._finish(board, run_id, parent_claim, "assistance", parent_seal, "sess-parent-1")
        view = board.call("workflow_get", {"runId": run_id})
        approved = board.call(
            "workflow_decide",
            {
                "runId": run_id,
                "requestId": view["activeRequest"]["requestId"],
                "commandId": "decide-1",
                "expectedRevision": view["revision"],
                "decision": "approve",
                "reason": "hand the checkout over",
                "helpers": [
                    {
                        "requestId": "helper-1",
                        "task": "helper work on the shared checkout",
                        "cwd": str(self.repo),
                        "executionWorkspace": {"kind": "existing", "access": "write"},
                    }
                ],
                **submitted["control"],
            },
        )
        child = approved["children"][0]["taskId"]
        audit = board.call("workflow_get", {"runId": run_id, "includeAudit": True})
        states = {(row["holderTaskId"], row["state"]) for row in audit["audit"]["reservations"]}
        self.assertIn((run_id, "transferred"), states)
        self.assertIn((child, "held"), states)

        helper_claim = board.call(
            "worker_claim",
            {"workerId": "w1", "claimRequestId": "claim-2", "nonce": "m" * 16, "runId": child},
        )
        helper_manifest = helper_claim["claim"]["turn"]["input"]["executionWorkspace"]
        self.assertEqual(helper_manifest["checkoutId"], parent_manifest["checkoutId"])
        # The helper writes a new file and seals its output on the shared checkout.
        (self.repo / "helper-new.txt").write_text("helper output\n")
        helper_seal = workspace.seal(
            self.directory, helper_manifest, child, helper_claim["claim"]["attempt"]["attemptId"]
        )
        self.assertIn("helper-new.txt", helper_seal["changedPaths"] + helper_seal["includedUntracked"])
        completed = self._finish(
            board, child, helper_claim, "completed", helper_seal, "sess-helper", nonce="m" * 16
        )
        self.assertEqual(completed["taskState"], "completed")

        # Ownership returns to the parent, and its continuation is based on the
        # helper's sealed commit (never the parent's older seal).
        parent_second = board.call(
            "worker_claim",
            {"workerId": "w1", "claimRequestId": "claim-3", "nonce": "k" * 16, "runId": run_id},
        )
        effective = parent_second["claim"]["turn"]["input"]["executionWorkspace"]
        self.assertEqual(effective["baseCommit"], helper_seal["commit"])
        self.assertEqual(effective["inputTree"], helper_seal["tree"])
        verified = workspace.verify(effective, require_unchanged=True)
        self.assertTrue(verified["valid"])
        (self.repo / "parent-integration.txt").write_text("integrated\n")
        final_seal = workspace.seal(
            self.directory, effective, run_id, parent_second["claim"]["attempt"]["attemptId"]
        )
        self.assertIn("parent-integration.txt", final_seal["changedPaths"])
        done = self._finish(
            board, run_id, parent_second, "completed", final_seal, "sess-parent-2", nonce="k" * 16
        )
        self.assertEqual(done["taskState"], "completed")
        delivered = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(delivered["state"], "delivered")
        self.assertTrue([row for row in delivered["artifacts"] if row["kind"] == "output"])


class PinnedHelperHandoffTests(RealWorkspaceTestCase):
    """Two real helper worktrees: the parent receives their exact immutable refs."""

    def _record(self, run_id, claim, disposition, seal, session_id):
        turn = claim["claim"]["turn"]
        return {
            "version": 1,
            "taskId": run_id,
            "attemptId": claim["claim"]["attempt"]["attemptId"],
            "generation": claim["claim"]["attempt"]["generation"],
            "turnId": turn["turnId"],
            "resumeMode": turn["resumeMode"],
            "previousSessionId": turn["input"]["previousSessionId"],
            "sessionId": session_id,
            "promptSha256": "p" * 64,
            "inputSha256": turn["inputSha256"],
            "outcome": {
                "disposition": disposition,
                "summary": f"{disposition} turn",
                "remaining": [] if disposition == "completed" else ["needs the Host"],
                "decisions": [],
                "artifacts": [],
                "request": None
                if disposition == "completed"
                else {
                    "summary": "review",
                    "attempted": "tried",
                    "neededWork": "decide",
                    "expectedArtifacts": ["design"],
                    "acceptance": "approved",
                },
            },
            "provenance": {
                "tool": "buddy_finish_turn",
                "turnEnd": "completed",
                "flush": "awaited",
                "rootSessionMatched": True,
            },
        }

    def _finish(self, board, run_id, claim, disposition, seal, session_id, nonce):
        board.call(
            "worker_result",
            {
                "workerId": "w1",
                "attemptId": claim["claim"]["attempt"]["attemptId"],
                "generation": claim["claim"]["attempt"]["generation"],
                "nonce": nonce,
                "status": "ok",
                "result": {
                    "status": "ok",
                    "processState": {"shutdownConfirmed": True},
                    "turn": self._record(run_id, claim, disposition, seal, session_id),
                    "turnResultPath": "/tmp/turn.json",
                    "workspaceSeal": seal,
                },
                "shutdownConfirmed": True,
                "exitCode": 0,
            },
        )

    def test_helper_refs_are_frozen_into_the_parent_continuation(self):
        board = self.board()
        board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh"]})
        (self.repo / "dirty.txt").write_text("dirty parent input\n")
        submitted = self.submit(board)
        run_id = submitted["runId"]
        parent_claim = board.call(
            "worker_claim", {"workerId": "w1", "claimRequestId": "claim-1", "nonce": "n" * 16}
        )
        parent_manifest = parent_claim["claim"]["turn"]["input"]["executionWorkspace"]
        parent_seal = workspace.seal(
            self.directory, parent_manifest, run_id, parent_claim["claim"]["attempt"]["attemptId"]
        )
        self._finish(board, run_id, parent_claim, "assistance", parent_seal, "sess-p1", "n" * 16)
        view = board.call("workflow_get", {"runId": run_id})
        approved = board.call(
            "workflow_decide",
            {
                "runId": run_id,
                "requestId": view["activeRequest"]["requestId"],
                "commandId": "decide-1",
                "expectedRevision": view["revision"],
                "decision": "approve",
                "reason": "two independent helpers",
                "helpers": [
                    {
                        "requestId": f"helper-{index}",
                        "task": f"helper {index}",
                        "cwd": str(self.repo),
                        "executionWorkspace": {"kind": "worktree", "access": "write"},
                    }
                    for index in (1, 2)
                ],
                **submitted["control"],
            },
        )
        seals: dict[str, dict] = {}
        manifests: dict[str, dict] = {}
        for index, child in enumerate([row["taskId"] for row in approved["children"]], start=1):
            nonce = chr(ord("a") + index) * 16
            claim = board.call(
                "worker_claim",
                {"workerId": "w1", "claimRequestId": f"claim-h{index}", "nonce": nonce, "runId": child},
            )
            manifest = claim["claim"]["turn"]["input"]["executionWorkspace"]
            manifests[child] = manifest
            (Path(manifest["path"]) / f"helper-{index}.txt").write_text(f"helper {index} output\n")
            seal = workspace.seal(self.directory, manifest, child, claim["claim"]["attempt"]["attemptId"])
            seals[child] = seal
            self._finish(board, child, claim, "completed", seal, f"sess-h{index}", nonce)

        parent_second = board.call(
            "worker_claim", {"workerId": "w1", "claimRequestId": "claim-2", "nonce": "z" * 16, "runId": run_id}
        )
        context = parent_second["claim"]["turn"]["input"]["context"]
        self.assertTrue(context.get("helperOutcomesFrozen"))
        outcomes = {entry["taskId"]: entry for entry in context["helperOutcomes"]}
        self.assertEqual(set(outcomes), set(seals))
        for index, (child, seal) in enumerate(seals.items(), start=1):
            entry = outcomes[child]
            artifact = entry["artifact"]
            self.assertEqual(artifact["outputCommit"], seal["commit"])
            self.assertEqual(artifact["snapshotSha256"], seal["snapshotSha256"])
            self.assertEqual(artifact["diffSha256"], seal["diffSha256"])
            self.assertEqual(artifact["inputCommit"], seal["inputCommit"])
            self.assertIn(f"helper-{index}.txt", artifact["changedPaths"])
            self.assertTrue(entry["attemptId"])
            self.assertTrue(entry["workspace"]["checkoutId"])
            self.assertEqual(entry["workspace"]["manifestSha256"], manifests[child]["manifestSha256"])
        # The continuation snapshot is the frozen record, not a live re-query.
        audit = board.call("workflow_get", {"runId": run_id, "includeAudit": True})
        consumed = [row for row in audit["audit"]["continuations"] if row["state"] == "consumed"]
        self.assertTrue(consumed)
        frozen = {row["taskId"]: row["artifact"]["outputCommit"] for row in consumed[-1]["helperOutcomes"]}
        self.assertEqual(frozen, {child: seal["commit"] for child, seal in seals.items()})
        # Editing a helper worktree afterwards cannot rewrite what the parent was given.
        for entry in outcomes.values():
            Path(entry["workspace"]["path"], "later.txt").write_text("later edit\n")
        again = board.call("workflow_get", {"runId": run_id, "includeAudit": True})
        frozen_again = {
            row["taskId"]: row["artifact"]["outputCommit"]
            for row in [item for item in again["audit"]["continuations"] if item["state"] == "consumed"][-1][
                "helperOutcomes"
            ]
        }
        self.assertEqual(frozen_again, {child: seal["commit"] for child, seal in seals.items()})
        for child, seal in seals.items():
            artifacts = board.call("workflow_get", {"runId": child})["artifacts"]
            self.assertIn(seal["snapshotSha256"], [row["manifestSha256"] for row in artifacts])


class WorktreeContinuationReuseTests(TransferredCheckoutContinuationTests):
    def test_worktree_origin_continuation_reuses_the_same_allocated_worktree(self):
        board = self.board()
        board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh"]})
        submitted = self.submit(board, request_id="wt-1", kind="worktree")
        run_id = submitted["runId"]
        first = board.call("worker_claim", {"workerId": "w1", "claimRequestId": "wt-c1", "nonce": "n" * 16})
        manifest_one = first["claim"]["turn"]["input"]["executionWorkspace"]
        seal_one = workspace.seal(self.directory, manifest_one, run_id, first["claim"]["attempt"]["attemptId"])
        self._finish(board, run_id, first, "assistance", seal_one, "sess-w1", "n" * 16)
        view = board.call("workflow_get", {"runId": run_id})
        board.call(
            "workflow_decide",
            {
                "runId": run_id,
                "requestId": view["activeRequest"]["requestId"],
                "commandId": "wt-decide-1",
                "expectedRevision": view["revision"],
                "decision": "decline",
                "reason": "keep working",
                **submitted["control"],
            },
        )
        second = board.call(
            "worker_claim", {"workerId": "w1", "claimRequestId": "wt-c2", "nonce": "m" * 16, "runId": run_id}
        )
        manifest_two = second["claim"]["turn"]["input"]["executionWorkspace"]
        self.assertEqual(manifest_two["path"], manifest_one["path"])
        self.assertEqual(manifest_two["checkoutId"], manifest_one["checkoutId"])
        self.assertEqual(manifest_two["baseCommit"], seal_one["commit"])
        self.assertEqual(board.call("task_get", {"runId": run_id})["task"]["cwd"], manifest_one["path"])
        # No second allocation was reserved for this run.
        audit = board.call("workflow_get", {"runId": run_id, "includeAudit": True})
        paths = {
            row["path"]
            for row in audit["audit"]["reservations"]
            if row["state"] in ("held", "transferred")
        }
        self.assertEqual(paths, {manifest_one["path"]})


class ContinuationCheckoutReuseTests(TransferredCheckoutContinuationTests):
    """The allocated checkout survives continuations; the baseline is the latest seal."""

    def test_sequential_helper_and_parent_edit_keep_one_checkout_and_latest_baseline(self):
        board = self.board()
        board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh"]})
        (self.repo / "dirty.txt").write_text("dirty parent input\n")
        submitted = self.submit(board)
        run_id = submitted["runId"]

        # Turn 1: the parent yields in its allocated checkout.
        first = board.call("worker_claim", {"workerId": "w1", "claimRequestId": "r-c1", "nonce": "n" * 16})
        manifest_one = first["claim"]["turn"]["input"]["executionWorkspace"]
        seal_one = workspace.seal(self.directory, manifest_one, run_id, first["claim"]["attempt"]["attemptId"])
        self._finish(board, run_id, first, "assistance", seal_one, "sess-1", "n" * 16)

        # A helper takes the same checkout over and adds a file.
        view = board.call("workflow_get", {"runId": run_id})
        approved = board.call(
            "workflow_decide",
            {
                "runId": run_id,
                "requestId": view["activeRequest"]["requestId"],
                "commandId": "r-decide-1",
                "expectedRevision": view["revision"],
                "decision": "approve",
                "reason": "sequential helper",
                "helpers": [
                    {
                        "requestId": "helper-1",
                        "task": "helper on the shared checkout",
                        "cwd": str(self.repo),
                        "executionWorkspace": {"kind": "existing", "access": "write"},
                    }
                ],
                **submitted["control"],
            },
        )
        child = approved["children"][0]["taskId"]
        helper = board.call(
            "worker_claim",
            {"workerId": "w1", "claimRequestId": "r-c2", "nonce": "m" * 16, "runId": child},
        )
        helper_manifest = helper["claim"]["turn"]["input"]["executionWorkspace"]
        self.assertEqual(helper_manifest["path"], manifest_one["path"])
        (self.repo / "helper-new.txt").write_text("helper output\n")
        seal_two = workspace.seal(
            self.directory, helper_manifest, child, helper["claim"]["attempt"]["attemptId"]
        )
        self._finish(board, child, helper, "completed", seal_two, "sess-h", "m" * 16)

        # Turn 2 continues in the SAME physical checkout, based on the helper's seal.
        second = board.call(
            "worker_claim", {"workerId": "w1", "claimRequestId": "r-c3", "nonce": "k" * 16, "runId": run_id}
        )
        manifest_two = second["claim"]["turn"]["input"]["executionWorkspace"]
        self.assertEqual(manifest_two["path"], manifest_one["path"])
        self.assertEqual(manifest_two["baseCommit"], seal_two["commit"])
        (self.repo / "parent-turn2.txt").write_text("parent integration\n")
        seal_three = workspace.seal(
            self.directory, manifest_two, run_id, second["claim"]["attempt"]["attemptId"]
        )
        self._finish(board, run_id, second, "assistance", seal_three, "sess-2", "k" * 16)

        # Turn 3 must base on the parent's own newer seal, not the older helper seal.
        view = board.call("workflow_get", {"runId": run_id})
        board.call(
            "workflow_decide",
            {
                "runId": run_id,
                "requestId": view["activeRequest"]["requestId"],
                "commandId": "r-decide-2",
                "expectedRevision": view["revision"],
                "decision": "decline",
                "reason": "continue on the integrated state",
                **submitted["control"],
            },
        )
        third = board.call(
            "worker_claim", {"workerId": "w1", "claimRequestId": "r-c4", "nonce": "j" * 16, "runId": run_id}
        )
        manifest_three = third["claim"]["turn"]["input"]["executionWorkspace"]
        self.assertEqual(manifest_three["path"], manifest_one["path"])
        self.assertEqual(manifest_three["baseCommit"], seal_three["commit"])
        self.assertEqual(manifest_three["inputTree"], seal_three["tree"])
        self.assertEqual(manifest_three["checkoutId"], manifest_one["checkoutId"])
        self.assertEqual(
            board.call("task_get", {"runId": run_id})["task"]["cwd"], manifest_one["path"]
        )
        # Exactly one writer reservation remains, held by the parent on that checkout.
        audit = board.call("workflow_get", {"runId": run_id, "includeAudit": True})
        held = [
            row
            for row in audit["audit"]["reservations"]
            if row["state"] == "held" and row["access"] == "write"
        ]
        self.assertEqual([(row["holderTaskId"], row["checkoutId"]) for row in held], [(run_id, manifest_one["checkoutId"])])
        view = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(view["workspace"]["path"], manifest_one["path"])
        # Sibling paths of the same checkout still cannot bypass the write exclusion.
        sibling = self.repo / "sub"
        sibling.mkdir()
        with self.assertRaises(Exception) as raised:
            self.submit(board, request_id="req-sibling", cwd=str(sibling))
        self.assertEqual(getattr(raised.exception, "code", None), "PREPARATION_CONFLICT")
