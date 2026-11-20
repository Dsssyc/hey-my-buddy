"""Real workspace module + the shared role seams, end to end over actual Git.

These tests exercise the merged ``hey_my_buddy.blackboard.tasks.workspace`` implementation and, since ADR-025
step four, the shared role seams that replaced the per-harness adapter carrier:
``worker_executor`` preparation, the ``roles.turn_io`` micro-task-turn import and
seal, and the shared collection over this project's offline fake ACP agent. Git,
manifests, the turn input file, the provenance validation and the shutdown gate
are real; only the native harness is the project's own fake program.
"""
from __future__ import annotations
from hey_my_buddy.protocol.workspace_manifest import turn_manifest, manifest_ref, record_reference, artifact_manifest
from admission import admit_board, admit_store

import hashlib
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from support import BoardTestCase, enable_fixture_buddy, native_write_bytes
from private_acl import assert_private_path
from hey_my_buddy.cli.checks import prepare_test_home
from protocol.fixtures import native_turn
from buddy.harnesses.dsh.acp.support import FAKE_AGENT

from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.buddy.harnesses.base import ExecutionContext
from hey_my_buddy.buddy.roles.controller import worker_executor
from hey_my_buddy.blackboard.store.db import canonical_json, sha256_text
from hey_my_buddy.private_dirs import attempt_root
from hey_my_buddy.paths import native_path

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]

BUDDY = {"harness": "dsh", "provider": "deepseek-official", "model": "deepseek-flash", "effort": "off"}

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Buddy Test",
    "GIT_AUTHOR_EMAIL": "buddy@example.invalid",
    "GIT_COMMITTER_NAME": "Buddy Test",
    "GIT_COMMITTER_EMAIL": "buddy@example.invalid",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def private_environment(case):
    removed = {"HMB_STATE_DIR", "HMB_RUNTIME_ROOT", "HMB_RUNTIME", "HMB_RUNTIME_IDENTITY",
               "HMB_WORKER_STATE", "HMB_WORKER_ID", "HMB_AGENT_CREDENTIAL_FILE",
               "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"}
    environment = {key: value for key, value in os.environ.items() if key not in removed}
    environment.update(HMB_STATE_DIR=str(case.directory), HMB_RUNTIME_ROOT=str(case.directory / "runtime"),
                       HMB_DEV_SOURCE="1")
    case.enterContext(patch.dict(os.environ, environment, clear=True))


class RealWorkspaceTestCase(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        private_environment(self)
        self.catalog_fixture()
        self.submitted_manifests = {}
        self.repo = self.directory / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "buddy@example.invalid")
        self.git("config", "user.name", "Buddy Test")
        # Both the fixture helper and the product must read the same checkout
        # rule, even though the helper suppresses system Git configuration.
        self.git("config", "core.autocrlf", "false")
        (self.repo / "tracked.txt").write_text("base\n", encoding="utf-8", newline="\n")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "feature.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")

    def board(self, **kwargs):
        board = super().board(**kwargs)
        enable_fixture_buddy(board.store, BUDDY)
        return board

    def git(self, *arguments: str, check: bool = True) -> str:
        completed = subprocess.run(
            ["git", "-c", "core.longpaths=true", *arguments],
            cwd=self.repo,
            env={**os.environ, **GIT_ENV},
            capture_output=True,
            text=True, encoding="utf-8",
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
            "integrator": {"role": "host"},
        }
        if access == "write":
            intent["writeScope"] = ["."]
        return intent

    def manifest_for(self, request_id: str, **options) -> dict:
        if request_id in self.submitted_manifests:
            return self.submitted_manifests[request_id]
        return workspace.prepare(self.directory, request_id, self._intent(request_id, **options))

    def submit(self, board, *, request_id="req-1", kind="existing", cwd=None, access="write"):

        cwd = str(cwd or self.repo)
        params = {
            "cwd": cwd, "macroTaskId": "standalone", "title": request_id,
            "task": "real workspace task", "buddy": " / ".join(BUDDY.values()),
            "checkout": kind, "access": access, "expectedSeconds": 1800,
        }
        if kind != "none":
            params["inputs"] = []
            if access in ("write", "full"):
                params["writeScope"] = ["."]
        if access == "full":
            params["accessNote"] = "Fixture grants full access"
        reply = board.call("workflow_submit", params)
        from hey_my_buddy.protocol.workspace_manifest import resolve_manifest
        with board.store.db.read() as connection:
            run = connection.execute(
                "SELECT workspace_id,workspace_manifest_sha256 FROM workflow_runs WHERE run_id=?",
                (reply["runId"],),
            ).fetchone()
        self.submitted_manifests[request_id] = resolve_manifest(self.directory, {
            "workspaceId": run["workspace_id"],
            "manifestSha256": run["workspace_manifest_sha256"],
        })
        return {**board.call("workflow_get", {"runId": reply["runId"]}), **reply}


class CumulativePatchProjectionTests(RealWorkspaceTestCase):
    def test_cumulative_patch_paths_ignore_an_enclosing_git_worktree(self):
        from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
        initial_commit = self.git("rev-parse", "HEAD").strip()
        (self.repo / "tracked.txt").write_text("a real patch change\n", encoding="utf-8")
        raw = self.git("diff", "--binary", "--no-renames").encode()
        state = self.repo / "nested-private-state"
        relative = Path("workspaces") / ("ws-" + "a" * 32) / "outputs" / ("b" * 64) / "output.json"
        directory = (state / relative).parent
        native_path(directory).mkdir(parents=True, mode=0o700)
        native_write_bytes(directory / ("cumulative-" + initial_commit + ".patch"), raw)
        coordinator = SimpleNamespace(board=SimpleNamespace(directory=state),
                                      _initial_input_commit=lambda connection, run_id: initial_commit)
        row = {"content_form": "record-reference", "kind": "output", "run_id": "nested",
               "record_path": relative.as_posix()}
        record = {"inputCommit": "e" * 40, "commit": "f" * 40}
        view = WorkflowCoordinator._cumulative_patch_view(coordinator, row, record, connection=object())
        self.assertEqual(view["changedPaths"], ["tracked.txt"])


class DirtyInputTests(RealWorkspaceTestCase):
    def test_submit_transports_dirty_input_without_changing_the_source_index(self):
        (self.repo / "tracked.txt").write_text("staged change\n", encoding="utf-8", newline="\n")
        self.git("add", "tracked.txt")
        (self.repo / "src" / "feature.py").write_text("value = 2\n", encoding="utf-8", newline="\n")
        (self.repo / "untracked.txt").write_text("new file\n", encoding="utf-8", newline="\n")
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
        verified = workspace.verify(manifest, require_unchanged=True, state_dir=self.directory)
        self.assertTrue(verified["valid"])
        self.assertTrue(verified["unchanged"])
        task = board.call("task_get", {"runId": submitted["runId"]})["task"]
        self.assertEqual(task["cwd"], manifest["path"])
        self.assertEqual(task["spec"]["cwd"], os.path.realpath(self.repo))

    def test_parallel_micro_tasks_get_distinct_effective_checkouts(self):
        board = self.board(max_concurrent=2)
        first = self.submit(board, request_id="req-1", kind="worktree")
        second = self.submit(board, request_id="req-2", kind="worktree")
        first_manifest = self.manifest_for("req-1", kind="worktree")
        second_manifest = self.manifest_for("req-2", kind="worktree")
        self.assertNotEqual(first_manifest["path"], second_manifest["path"])
        self.assertNotEqual(first_manifest["checkoutRoot"], second_manifest["checkoutRoot"])
        self.assertEqual(first_manifest["projectId"], second_manifest["projectId"])
        for worker, submitted in (("w1", first), ("w2", second)):
            pass  # Admission is owned by the service.
            claimed = admit_board(board, {'workerId': worker, 'claimRequestId': f'claim-{worker}', 'nonce': f'{worker}x'.ljust(16, 'y')})
            self.assertIsNotNone(claimed["claim"], claimed)
            self.assertEqual(claimed["claim"]["attempt"]["taskId"], submitted["runId"])
        task = board.call("task_get", {"runId": first["runId"]})["task"]
        self.assertEqual(task["cwd"], first_manifest["path"])
        self.assertEqual(task["spec"]["cwd"], os.path.realpath(self.repo))

    def test_continuation_bases_on_the_sealed_commit_and_seals_new_output(self):
        board = self.board()
        pass  # Admission is owned by the service.
        submitted = self.submit(board)
        run_id = submitted["runId"]
        manifest = self.manifest_for("req-1")
        claim = admit_board(board, {'workerId': 'w1', 'claimRequestId': 'claim-1', 'nonce': 'n' * 16})
        turn = claim["claim"]["turn"]
        self.assertEqual(turn_manifest(self.directory, turn["input"])["manifestSha256"], manifest["manifestSha256"])
        seal = workspace.seal(self.directory, manifest, run_id, claim["claim"]["attempt"]["attemptId"])
        self.assertTrue(seal.get("commit"))
        self.assertTrue(seal.get("snapshotSha256"))
        record = native_turn.claim_record(
            claim["claim"],
            outcome={
                "disposition": "attention",
                "summary": "need a decision",
                "remaining": ["finish"],
                "decisions": [],
                "artifacts": [],
                "request": {
                    "summary": "review",
                    "attempted": "tried",
                    "neededWork": "decide",
                    "expectedArtifacts": ["design"],
                    "acceptance": "approved",
                },
            },
            session_id="sess-1", prompt_sha256=sha256_text("mock turn prompt"),
        )
        board.call(
            'execution_result',
            {'attemptId': claim['claim']['attempt']['attemptId'], 'generation': claim['claim']['attempt']['generation'], 'nonce': 'n' * 16, 'status': 'ok', 'result': {'status': 'ok', 'processState': {'shutdownConfirmed': True}, 'turn': record, 'turnResultPath': '/tmp/turn.json', 'workspaceSeal': record_reference(self.directory, seal)}, 'shutdownConfirmed': True, 'exitCode': 0},
        )
        view = board.call("workflow_get", {"runId": run_id})
        output = [row for row in view["artifacts"] if row["kind"] == "output"][0]
        self.assertEqual(output["outputSnapshotSha256"], seal["snapshotSha256"])
        self.assertEqual(output["sealedEvidence"]["tree"], seal["tree"])
        decided = board.call(
            "workflow_continue",
            {
                "runId": run_id,
                "requestId": view["activeRequest"]["requestId"],
                "input": "Use the current findings",
                "note": "continue on the sealed state",
            },
        )
        self.assertEqual(decided["state"], "executing")
        second = admit_board(board, {'workerId': 'w1', 'claimRequestId': 'claim-2', 'nonce': 'm' * 16})
        second_input = turn_manifest(self.directory, second["claim"]["turn"]["input"])
        self.assertEqual(second_input["baseCommit"], seal["commit"])
        self.assertNotEqual(second_input["manifestSha256"], manifest["manifestSha256"])
        self.assertEqual(second_input["path"], manifest["path"])
        self.assertNotIn("workspaceMode", second["claim"]["turn"]["input"])
        second_seal = workspace.seal(
            self.directory, second_input, run_id, second["claim"]["attempt"]["attemptId"]
        )
        self.assertNotEqual(second_seal["snapshotSha256"], seal["snapshotSha256"])


class RealAdapterTests(RealWorkspaceTestCase):
    """The shared role seams over the real registered DSH run and real Git.

    The retired per-harness carrier (``DshAdapter.prepare``/``_import_turn``/
    ``_seal_workspace``/``collect``/``workspace_cwd``) is gone; these cases hold
    the same boundaries on the seams that replaced it: the shared executor's
    preparation (workspace input binding, exact turn-input bytes, credential
    privacy), and the shared collection over this project's
    offline fake ACP agent, whose finish receipts the real role rules mint.
    The agent declares its own fixture selectors (provider ``fake``, model
    ``m1``), so the executor context's buddy is the fixture's, not the
    board task's; the board submission only supplies the real run identity.
    """

    FIXTURE_BUDDY = {"harness": "dsh", "provider": "fake", "model": "m1", "effort": "high"}

    def _agent_record(self, index: int, *extra: str) -> Path:
        """A selected-harness record pointing at the offline fake ACP agent."""
        command = [sys.executable, str(FAKE_AGENT), "--log",
                   str(self.directory / f"fake-agent-{index}.log"),
                   "--fixture-root", str(self.directory), *extra]
        path = self.directory / f"harness-record-{index}.json"
        path.write_text(json.dumps({"dsh": {"status": "ready", "command": command}}), encoding="utf-8", newline="\n")
        return path

    def _micro_task_record(self, index: int, run_id: str, *extra: str) -> Path:
        attempt_id = f"attempt-{index}"
        checkpoint, _answer, finish = self._qualified_tools(attempt_id)
        bridge = attempt_root(self.directory, "dsh", run_id, attempt_id) / "finish-bridge.json"
        return self._agent_record(
            index, "--prompt-mode", "micro-task", "--bridge-config", str(bridge),
            "--finish-tool", finish, "--checkpoint-tool", checkpoint, *extra)

    @staticmethod
    def _qualified_tools(attempt_id: str) -> tuple[str, str, str]:
        """The mount's mechanical qualified names, derived exactly as the module does."""
        server = "hey_my_buddy_" + hashlib.sha256(attempt_id.encode()).hexdigest()[:16]
        return (f"mcp__{server}__hey_my_buddy_checkpoint",
                f"mcp__{server}__hey_my_buddy_answer_inquiry",
                f"mcp__{server}__hey_my_buddy_finish_turn")

    def _context(self, run_id: str, manifest: dict, *, index: int = 1, record: Path) -> ExecutionContext:
        attempt_id = f"attempt-{index}"
        attempt = self.directory / "attempts" / run_id / attempt_id
        attempt.mkdir(mode=0o700, parents=True, exist_ok=True)
        home = self.directory / f"agent-home-{index}"
        prepare_test_home(home)
        self.enterContext(patch.dict(os.environ, {
            "HOME": str(home), "USERPROFILE": str(home), "DSH_HOME": str(home / ".dsh")}))
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("HMB_") and key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "HOME")}
        environment.update(
            HOME=str(home), USERPROFILE=str(home), DSH_HOME=str(home / ".dsh"), HMB_STATE_DIR=str(self.directory),
            HMB_RUNTIME_ROOT=str(self.directory / "runtime"), HMB_DEV_SOURCE="1",
            PYTHONPATH=str(REPOSITORY_ROOT / "src") + os.pathsep + str(REPOSITORY_ROOT / "tests" / "python"),
            HMB_HARNESS_RECORD_FILE=str(record),
        )
        return ExecutionContext(
            task_id=run_id,
            attempt_id=attempt_id,
            generation=index,
            session_record_path=str(self.directory / "sessions" / "micro-tasks" / run_id / (attempt_id + ".jsonl")),
            spec={**self.FIXTURE_BUDDY, "adapter": "dsh", "task": "do work", "cwd": manifest["path"], "expectedSeconds": 60},
            directory=attempt,
            runtime={"identity": "test"},
            environment=environment,
            turn={
                "turnId": f"turn-{index}",
                "turnIndex": index,
                "resumeMode": "initial",
                "inputSha256": "",
                "input": {
                    "version": 4,
                    "taskId": run_id,
                    "attemptId": attempt_id,
                    "generation": index,
                    "turnId": f"turn-{index}",
                    "resumeMode": "initial",
                    "previousSessionId": None,
                    "context": {"task": "do work"},
                    "executionWorkspace": manifest_ref(manifest),
                },
            },
            agent_credential="credential-token",
        )

    def test_adapter_prepares_verified_workspace_and_exact_input_bytes(self):
        board = self.board()
        submitted = self.submit(board)
        # Prepare a real manifest directly for the executor context. The selected
        # harness record only satisfies the capability check; nothing launches.
        manifest = self.manifest_for("adapter-1")
        context = self._context(submitted["runId"], manifest, record=self._agent_record(1))
        executor = worker_executor("dsh")
        verified: list[bool] = []
        real_verify = workspace.verify

        def recording_verify(value, *, state_dir, require_unchanged=True):
            verified.append(require_unchanged)
            return real_verify(value, state_dir=state_dir, require_unchanged=require_unchanged)

        workspace.verify = recording_verify
        try:
            executor.prepare(context)
        finally:
            workspace.verify = real_verify
        self.assertEqual(verified, [False])
        text = context.turn_input_file().read_text(encoding="utf-8")
        self.assertEqual(text, json.dumps(context.turn_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        self.assertEqual(
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
            sha256_text(canonical_json(context.turn_input)),
        )
        self.assertTrue(context.credential_file().is_file())
        assert_private_path(self, context.credential_file(), 0o600)
        self.assertFalse(native_path(context.turn_output_file()).exists())

    def test_collect_imports_a_turn_only_with_confirmed_shutdown(self):
        board = self.board()
        submitted = self.submit(board)
        manifest = self.manifest_for("adapter-2")
        executor = worker_executor("dsh")
        # The micro-task fake agent holds its turn open at the cooperative
        # checkpoint. Collect only after its same-process result exists.
        holding = self._micro_task_record(2, submitted["runId"], "--wait-for-inquiry", "20")
        held = self._context(submitted["runId"], manifest, index=2, record=holding)
        executor.prepare(held)
        handle = executor.start(held)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if not handle.finished else None)
        log = self.directory / "fake-agent-2.log"
        deadline = time.monotonic() + 15
        while not log.exists() or '"method": "session/prompt"' not in log.read_text(encoding="utf-8"):
            self.assertFalse(handle.finished, "the fixture exited before its native prompt")
            self.assertLess(time.monotonic(), deadline, "the fixture never entered its native prompt")
            time.sleep(0.02)
        self.assertFalse(handle.finished, "the held native run must still be active at collection")
        self.assertFalse(native_path(held.turn_output_file()).exists())
        handle.terminate()
        self.assertIsNotNone(handle.wait(30))
        # Once the run settles and shutdown is confirmed, the same shared
        # collection imports the minted turn and seals the real workspace.
        settling = self._micro_task_record(3, submitted["runId"])
        settled = self._context(submitted["runId"], manifest, index=3, record=settling)
        executor.prepare(settled)
        finished = executor.start(settled)
        self.addCleanup(lambda: finished.terminate(grace_seconds=0.2) if not finished.finished else None)
        self.assertIsNotNone(finished.wait(75),
                             "the native run did not settle within its deadline")
        with patch.object(finished, "shutdown_confirmed", return_value=False):
            unconfirmed = executor.collect(finished, settled)
        self.assertEqual(unconfirmed.status, "failed")
        self.assertNotIn("turn", unconfirmed.result)
        self.assertNotIn("workspaceSeal", unconfirmed.result)
        outcome = executor.collect(finished, settled)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertEqual(outcome.result["turn"]["turnId"], "turn-3")
        self.assertIn("workspaceSeal", outcome.result)
        self.assertEqual(settled.cwd, manifest["path"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class WorkspaceContinuationTestCase(RealWorkspaceTestCase):
    """Publish a synthetic Worker result with a real Git seal."""

    def _turn_record(self, run_id, claim, disposition, session_id, seal):
        return native_turn.claim_record(
            claim["claim"],
            outcome={
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
                    "neededWork": "decide",
                    "expectedArtifacts": ["guidance"],
                    "acceptance": "answered",
                },
            },
            session_id=session_id, prompt_sha256=sha256_text("mock turn prompt"),
        )

    def _finish(self, board, run_id, claim, disposition, seal, session_id, nonce="n" * 16):
        record = self._turn_record(run_id, claim, disposition, session_id, seal)
        return board.call(
            'execution_result',
            {'attemptId': claim['claim']['attempt']['attemptId'], 'generation': claim['claim']['attempt']['generation'], 'nonce': nonce, 'status': 'ok', 'result': {'status': 'ok', 'processState': {'shutdownConfirmed': True}, 'turn': record, 'turnResultPath': '/tmp/turn.json', 'workspaceSeal': record_reference(self.directory, seal)}, 'shutdownConfirmed': True, 'exitCode': 0},
        )

class WorktreeContinuationReuseTests(WorkspaceContinuationTestCase):
    def test_worktree_origin_continuation_reuses_the_same_allocated_worktree(self):
        board = self.board()
        pass  # Admission is owned by the service.
        submitted = self.submit(board, request_id="wt-1", kind="worktree")
        run_id = submitted["runId"]
        first = admit_board(board, {'workerId': 'w1', 'claimRequestId': 'wt-c1', 'nonce': 'n' * 16})
        manifest_one = turn_manifest(self.directory, first["claim"]["turn"]["input"])
        seal_one = workspace.seal(self.directory, manifest_one, run_id, first["claim"]["attempt"]["attemptId"])
        self._finish(board, run_id, first, "attention", seal_one, "sess-w1", "n" * 16)
        view = board.call("workflow_get", {"runId": run_id})
        board.call(
            "workflow_continue",
            {
                "runId": run_id,
                "requestId": view["activeRequest"]["requestId"],
                "input": "Use the current findings",
                "note": "keep working",
            },
        )
        second = admit_board(board, {'workerId': 'w1', 'claimRequestId': 'wt-c2', 'nonce': 'm' * 16, 'runId': run_id})
        manifest_two = turn_manifest(self.directory, second["claim"]["turn"]["input"])
        self.assertEqual(manifest_two["path"], manifest_one["path"])
        self.assertEqual(manifest_two["checkoutRoot"], manifest_one["checkoutRoot"])
        self.assertEqual(manifest_two["baseCommit"], seal_one["commit"])
        self.assertEqual(board.call("task_get", {"runId": run_id})["task"]["cwd"], manifest_one["path"])
        # No second allocation was reserved for this run.
        audit = board.call("workflow_get", {"runId": run_id, "includeAudit": True})
        paths = {
            row["checkoutRoot"]
            for row in audit["audit"]["reservations"]
            if row["state"] == "held"
        }
        self.assertEqual(paths, {manifest_one["checkoutRoot"]})
