"""Real cross-process governed workflow: daemon, worker, workspace and CLI routes.

The DSH runner is a mock node script that implements the turn protocol; everything
else is the real service: real daemon, real supervisor/worker, the merged Git-backed
workspace module, the real C-Two/CLI path and the real console command route.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import unittest
from pathlib import Path

from support import BoardTestCase, wait_for
from test_console import Browser

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Buddy Test",
    "GIT_AUTHOR_EMAIL": "buddy@example.invalid",
    "GIT_COMMITTER_NAME": "Buddy Test",
    "GIT_COMMITTER_EMAIL": "buddy@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}
RUNNER = Path(__file__).resolve().parent / "fixtures" / "mock_turn_runner.mjs"
CONFIGURATION = {"adapter": "dsh", "provider": "deepseek-official", "model": "deepseek-flash", "effort": "off"}


class GovernedWorkerTestCase(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.catalog_fixture()
        self.repo = self.directory / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "buddy@example.invalid")
        self.git("config", "user.name", "Buddy Test")
        (self.repo / "tracked.txt").write_text("base\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")

    def git(self, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments], cwd=self.repo, env={**os.environ, **GIT_ENV}, capture_output=True, text=True
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout

    def env(self) -> dict:
        return {"BUDDY_RUNNER_PATH": str(RUNNER)}

    def submit(self, request_id: str = "worker-req-1") -> tuple[int, dict]:
        return self.cli(
            "submit",
            json.dumps(
                {
                    **CONFIGURATION,
                    "requestId": request_id,
                    "hostId": "host-1",
                    "task": "run the mock turn",
                    "cwd": str(self.repo),
                    "executionWorkspace": {"kind": "existing", "access": "write"},
                }
            ),
            env=self.env(),
        )


class RealWorkerTurnTests(GovernedWorkerTestCase):
    def execution_diagnostics(self, run_id: str, view: dict) -> str:
        code, receipt = self.cli("result", json.dumps({"runId": run_id}), env=self.env())
        meta = receipt.get("resultMeta") or {}
        result = receipt.get("result") or {}
        details = {
            "goalState": view.get("state"),
            "taskState": (view.get("task") or {}).get("state"),
            "resultExitCode": code,
            "resultError": receipt.get("error"),
            "resultMeta": {key: meta.get(key) for key in ("status", "error", "exitCode", "shutdownConfirmed")},
            "turnError": result.get("turnError"),
            "governedError": result.get("governedError"),
            "logs": {},
        }
        for name, location in (receipt.get("logPaths") or {}).items():
            if name not in ("stdout", "stderr") or not location:
                continue
            path = Path(location).resolve()
            if path.is_relative_to(self.directory.resolve()) and path.is_file():
                details["logs"][name] = path.read_text(errors="replace")[-4000:]
        return json.dumps(details, ensure_ascii=False, indent=2)

    def test_real_worker_imports_a_turn_and_seals_the_output(self):
        with self.daemon(env=self.env()):
            code, submitted = self.submit()
            self.assertEqual(code, 0, submitted)
            run_id = submitted["runId"]
            self.assertIn("controlFile", submitted)
            self.assertNotIn("controlToken", json.dumps(submitted))
            control_path = Path(submitted["controlFile"])
            self.assertEqual(stat.S_IMODE(control_path.stat().st_mode), 0o600)

            latest = {}

            def delivered_view():
                nonlocal latest
                # This test inspects the complete evidence, not the brief Host view.
                code, latest = self.cli("get", json.dumps({"runId": run_id, "output": "full"}), env=self.env())
                self.assertEqual(code, 0, latest)
                if latest.get("state") in ("failed", "cancelled", "awaiting-host"):
                    self.fail(self.execution_diagnostics(run_id, latest))
                return latest if latest.get("state") == "delivered" else None

            delivered = wait_for(delivered_view, timeout=90)
            if delivered is None:
                self.fail(self.execution_diagnostics(run_id, latest))
            outputs = [row for row in delivered["artifacts"] if row["kind"] == "output"]
            self.assertEqual(len(outputs), 1)
            self.assertTrue(outputs[0]["manifestSha256"])
            self.assertTrue(outputs[0]["outputCommit"])
            self.assertEqual(delivered["counts"]["turns"], 1)
            turn = delivered["turns"][0]
            self.assertEqual(turn["disposition"], "completed")
            self.assertTrue(turn["sessionId"].startswith("mock-session-"))

            # Git isolation and native session storage are separate facts: the
            # grouped default keeps the owning harness store so membership stays
            # verifiable, and the metadata says exactly that.
            _, receipt = self.cli("result", json.dumps({"runId": run_id, "output": "full"}), env=self.env())
            native = (receipt.get("result") or {}).get("nativeSession") or {}
            self.assertEqual(native.get("storageScope"), "harness-user-store")
            self.assertEqual(native.get("storageOwner"), "harness-user-store")
            self.assertEqual(native.get("nativeAppVisibility"), "user-store")
            self.assertTrue(native.get("captured"))
            self.assertFalse(native.get("resumable"))
            self.assertEqual(native.get("sessionId"), turn["sessionId"])
            self.assertEqual(native.get("sessionIdSource"), "validated-turn")
            self.assertFalse(native.get("sessionIdConflict"))

            # The DSH activity observer's bounded sidecar is attempt-bound and
            # readable by the real helper the owning Worker uses.
            from buddy import activity as activity_module

            attempt_directory = self.directory / "attempts" / run_id / turn["attemptId"]
            sidecar = activity_module.read_sidecar(
                activity_module.sidecar_path(attempt_directory),
                task_id=run_id,
                attempt_id=turn["attemptId"],
                generation=1,
            )
            self.assertIsNotNone(sidecar, "the dsh runner did not leave a bound activity sidecar")
            self.assertEqual(sidecar["phase"], "finishing")
            self.assertEqual(sidecar["nativeSessionId"], turn["sessionId"])
            self.assertEqual(sidecar["counts"], {"modelTurns": 1, "toolCalls": 1})
            activity_meta = (receipt.get("result") or {}).get("nativeActivity") or {}
            self.assertTrue(activity_meta.get("sidecarWritten"))

            # The final acknowledgement is separate from execution and bound to the
            # actual sealed artifact. The Host records its explicit integration
            # decision through the same business method the public operation uses.
            from buddy.store import BoardStore

            control = json.loads(control_path.read_text())
            BoardStore(self.directory).workflow.integration_record({
                "runId": run_id,
                "commandId": "worker-integration-1",
                "expectedRevision": delivered["revision"],
                "artifactId": outputs[0]["artifactId"],
                "notRequired": True,
                "reason": "the mock turn is reviewed without a separate integration target",
                "hostId": control["hostId"],
                "ownerGeneration": control["ownerGeneration"],
                "controlToken": control["controlToken"],
            })
            code, acknowledged = self.cli(
                "acknowledge",
                json.dumps(
                    {
                        "runId": run_id,
                        "artifactId": outputs[0]["artifactId"],
                        "note": "inspected the sealed output",
                        "controlFile": str(control_path),
                    }
                ),
                env=self.env(),
            )
            self.assertEqual(code, 0, acknowledged)
            self.assertEqual(acknowledged["state"], "accepted")
            self.assertEqual(acknowledged["view"], "receipt")
            self.assertEqual(acknowledged["acceptanceVerdict"], "accepted")

    def test_cli_fails_closed_for_an_agent_scoped_caller(self):
        with self.daemon(env=self.env()):
            code, submitted = self.submit("worker-req-2")
            self.assertEqual(code, 0, submitted)
            credential = self.directory / "agent-credential.json"
            credential.write_text(json.dumps({"token": "not-a-real-credential"}))
            os.chmod(credential, 0o600)
            env = {**self.env(), "BUDDY_AGENT_CREDENTIAL_FILE": str(credential)}
            code, refused = self.cli(
                "submit",
                json.dumps({"requestId": "forged", "task": "x", "cwd": str(self.repo)}),
                env=env,
            )
            self.assertEqual(code, 1)
            self.assertEqual(refused["error"]["code"], "UNAUTHORIZED")
            # A scoped caller can never use a Host control file.
            code, refused = self.cli(
                "decide",
                json.dumps(
                    {
                        "runId": submitted["runId"],
                        "requestId": "req-x",
                        "commandId": "forged",
                        "expectedRevision": 1,
                        "decision": "approve",
                        "controlFile": submitted["controlFile"],
                    }
                ),
                env=env,
            )
            self.assertEqual(code, 1)
            self.assertEqual(refused["error"]["code"], "FORBIDDEN")


class ConsoleWorkflowRouteTests(GovernedWorkerTestCase):
    def test_console_command_route_strips_the_capability_and_refuses_override(self):
        board = self.board()
        opened = board.console.start()
        browser = Browser(opened['url'])
        snapshot = browser.bootstrap()
        csrf = snapshot["csrfToken"]
        created = board.call(
            "workflow_submit",
            {
                **CONFIGURATION,
                "requestId": "console-req-1",
                "hostId": "host-1",
                "task": "console governed task",
                "cwd": str(self.repo),
                "executionWorkspace": {"kind": "existing", "access": "write"},
            },
        )
        status, _headers, body = browser.command(
            "workflow_get", {"runId": created["runId"]}, csrf=csrf
        )
        self.assertEqual(status, 200, body)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["runId"], created["runId"])
        # A browser-supplied authority field is refused outright.
        status, _headers, body = browser.command(
            "workflow_get",
            {"runId": created["runId"], "consoleAuthority": {"sessionId": "forged"}},
            csrf=csrf,
        )
        self.assertEqual(status, 400, body)
        self.assertEqual(json.loads(body)["error"]["code"], "INVALID_ARGUMENT")
        # The console persists a newly issued capability to a private file and never
        # returns the raw token.
        status, _headers, body = browser.command(
            "workflow_submit",
            {
                **CONFIGURATION,
                "requestId": "console-req-2",
                "hostId": "host-1",
                "task": "console submission",
                "cwd": str(self.repo),
                "executionWorkspace": {"kind": "worktree", "access": "write"},
            },
            csrf=csrf,
        )
        self.assertEqual(status, 200, body)
        result = json.loads(body)["result"]
        self.assertNotIn("controlToken", json.dumps(result))
        self.assertNotIn("controlToken", json.dumps(result["control"]))
        control_path = Path(result["controlFile"])
        self.assertEqual(stat.S_IMODE(control_path.stat().st_mode), 0o600)
        self.assertEqual(control_path.parent, board.store.directory / "controls")
        board.console.close()


class CompactRouteTests(GovernedWorkerTestCase):
    def test_workflow_get_matches_the_reported_compact_shape(self):
        board = self.board()
        submitted = board.call(
            "workflow_submit",
            {
                **CONFIGURATION,
                "requestId": "shape-req-1",
                "hostId": "host-1",
                "task": "shape",
                "cwd": str(self.repo),
                "executionWorkspace": {"kind": "existing", "access": "write"},
            },
        )
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        expected = {
            "governed",
            "runId",
            "taskId",
            "requestId",
            "hostId",
            "ownerGeneration",
            "state",
            "status",
            "queueReason",
            "awaitingHost",
            "waitReason",
            "goal",
            "revision",
            "continuationCount",
            "workspace",
            "executionWorkspace",
            "currentTurn",
            "turns",
            "activeRequest",
            "requests",
            "children",
            "artifacts",
            "counts",
            "truncated",
            "finalArtifactId",
            "finalAttemptId",
            "createdAt",
            "updatedAt",
            "task",
        }
        self.assertTrue(expected <= set(view), sorted(expected - set(view)))
        self.assertNotIn("controlToken", json.dumps(view))
        self.assertEqual(view["task"]["workflowState"], "executing")
        self.assertTrue(view["task"]["awaitingHost"] is False)
        self.assertEqual(view["goal"]["fingerprint"], submitted["goal"]["fingerprint"])
        self.assertTrue(all(isinstance(value, int) for value in view["truncated"].values()))
        self.assertTrue(all(isinstance(value, int) for value in view["counts"].values()))


class SubmissionPreparationRaceTests(GovernedWorkerTestCase):
    def _race(self, prepare, workers: int = 8):
        import threading

        tokens: list[str] = []
        errors: list[BaseException] = []

        def run() -> None:
            try:
                tokens.append(prepare())
            except BaseException as error:  # noqa: BLE001 - reported by the test
                errors.append(error)

        threads = [threading.Thread(target=run) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        return tokens

    def test_cli_submission_token_creation_is_immutable_and_shared(self):
        from unittest import mock

        from buddy import cli
        from buddy.errors import BoardError

        with mock.patch.dict(os.environ, {"BUDDY_STATE_DIR": str(self.directory)}):
            tokens = self._race(lambda: cli._submission_token("req-race", {}))
            self.assertEqual(len(set(tokens)), 1, tokens)
            path = next((self.directory / "submissions").glob("*.json"))
            original = path.read_text()
            path.write_text("not-json")
            with self.assertRaises(BoardError) as raised:
                cli._submission_token("req-race", {})
            self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
            self.assertEqual(path.read_text(), "not-json", "a malformed record is never overwritten")
            path.write_text(original)

    def test_console_submission_token_creation_is_immutable_and_shared(self):
        from buddy.errors import BoardError

        board = self.board()
        board.console.start()
        try:
            tokens = self._race(lambda: board.console._submission_token("console-race"))
            self.assertEqual(len(set(tokens)), 1, tokens)
            directory = board.store.directory / "submissions"
            path = next(directory.glob("*.json"))
            path.write_text("{")
            with self.assertRaises(BoardError) as raised:
                board.console._submission_token("console-race")
            self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
            self.assertEqual(path.read_text(), "{")
        finally:
            board.console.close()


class DshNativeStorageArgumentsTests(unittest.TestCase):
    """Only the ungrouped session rollout moves; the DSH home and its credentials never do."""

    def arguments(self, workspace: bool, *, governed: bool = False) -> list[str]:
        import tempfile
        from unittest import mock

        from buddy.adapters.base import ExecutionContext
        from buddy.adapters.dsh import DshAdapter

        directory = Path(tempfile.mkdtemp(prefix="buddy-dsh-args-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        turn = None
        if governed:
            turn = {"turnId": "turn-1", "input": {"version": 1, "taskId": "task", "attemptId": "attempt",
                                                  "generation": 1, "turnId": "turn-1", "resumeMode": "initial",
                                                  "previousSessionId": None, "context": {}, "executionWorkspace": {}}}
        context = ExecutionContext(
            task_id="task", attempt_id="attempt", generation=1,
            spec={"cwd": str(directory), "task": "x", "timeoutSeconds": 30, "workspace": workspace},
            directory=directory, runtime={}, environment=dict(os.environ), turn=turn,
        )
        with mock.patch.dict(os.environ, {"BUDDY_RUNNER_PATH": str(RUNNER)}):
            return DshAdapter().arguments(context, {"socketPath": "/tmp/inquiry.sock", "token": "a" * 64,
                                                    "resultsPath": "/tmp/inquiry.jsonl"})

    def test_grouped_runs_keep_the_owning_harness_session_store(self):
        args = self.arguments(workspace=True)
        self.assertNotIn("--no-workspace", args)
        self.assertFalse(any(arg.startswith("--session-root") for arg in args), args)
        self.assertFalse(any(arg.startswith("--dsh-home") for arg in args), args)

    def test_ungrouped_runs_move_only_the_attempt_private_session_root(self):
        args = self.arguments(workspace=False)
        self.assertIn("--no-workspace", args)
        session_root = next((arg for arg in args if arg.startswith("--session-root=")), None)
        self.assertIsNotNone(session_root, args)
        self.assertTrue(session_root.endswith("/sessions"), session_root)
        # The relocated DSH home broke native credential resolution; the owning
        # home must never be moved or simulated again.
        self.assertFalse(any(arg.startswith("--dsh-home") for arg in args), args)

    def test_a_governed_turn_publishes_its_activity_sidecar_in_the_attempt_directory(self):
        plain = self.arguments(workspace=True)
        self.assertFalse(any(arg.startswith("--activity-file") for arg in plain), plain)
        args = self.arguments(workspace=True, governed=True)
        activity = next((arg for arg in args if arg.startswith("--activity-file=")), None)
        self.assertIsNotNone(activity, args)
        self.assertTrue(activity.endswith("/activity.json"), activity)
        turn_input = args[args.index("--turn-input-file") + 1]
        self.assertEqual(activity, "--activity-file=" + str(Path(turn_input).parent / "activity.json"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
