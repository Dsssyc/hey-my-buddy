"""Real cross-process governed workflow: daemon, worker, workspace and CLI routes.

The DSH harness runs through its registered run module against an offline
Python governed ACP agent (``buddy/harnesses/dsh/fixtures/workflow_agent.py``,
selected as a direct executable command through ``BUDDY_DSH_CLI``); everything
else is the real service: real daemon, real supervisor/worker, the merged
Git-backed workspace module, the real C-Two/CLI path and the real console
command route.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import unittest
from pathlib import Path

from support import BoardTestCase, wait_for
from console.test_console import Browser

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Buddy Test",
    "GIT_AUTHOR_EMAIL": "buddy@example.invalid",
    "GIT_COMMITTER_NAME": "Buddy Test",
    "GIT_COMMITTER_EMAIL": "buddy@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}
FIXTURE = Path(__file__).resolve().parents[2] / "buddy/harnesses/dsh/fixtures/workflow_agent.py"
CONFIGURATION = {"adapter": "dsh", "provider": "deepseek-official", "model": "deepseek-flash", "effort": "off"}


def harness_environment(directory: Path) -> dict:
    """Select the offline governed agent as the harness's CLI entry.

    The daemon chain runs the service-selected health record, and the catalog
    fixture daemon composes every harness's command from ``BUDDY_<X>_CLI`` —
    a ``.py`` entry launches under the daemon's own interpreter, anything else
    runs directly, with no wrapper script and no production compatibility
    variable. The selection is this module's governed fixture file. HOME is
    pinned into the test's private root so the agent's forced-private-home
    tripwire holds off the invoking shell for the whole chain.
    """
    home = directory / "home"
    home.mkdir(mode=0o700, exist_ok=True)
    return {"HOME": str(home), "BUDDY_DSH_CLI": str(FIXTURE)}


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
        return harness_environment(self.directory)

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
            # The session id is the root session the run module itself opened
            # through the ACP handshake, not a fixture-chosen label.
            self.assertTrue(turn["sessionId"].startswith("fake-session-"))

            # Git isolation and native session storage are separate facts: this
            # run's rollout lands under the attempt-private DSH_HOME sessions
            # root, the owning credentials store keeps resolving by path, and
            # the installed app lists none of it. The session identity comes
            # only from the imported, validated turn record — sessionIdSource
            # names that source, and no conflict fact exists to clear (the
            # unvalidated side of this rule is the Host's own
            # DshPublishedReceiptTests witness). The retained native result
            # supplies its activity facts after the short live endpoint ends.
            _, receipt = self.cli("result", json.dumps({"runId": run_id, "output": "full"}), env=self.env())
            native = (receipt.get("result") or {}).get("nativeSession") or {}
            self.assertEqual(native.get("storageScope"), "attempt-private-sessions")
            self.assertEqual(native.get("storageOwner"), "buddy-attempt")
            self.assertEqual(native.get("nativeAppVisibility"), "not-listed-in-native-app")
            self.assertEqual(native.get("credentialsStore"), "harness-user-store")
            self.assertTrue(native.get("bindingPresent"))
            self.assertTrue(native.get("captured"))
            self.assertTrue(native.get("resumable"))
            self.assertEqual(native.get("sessionId"), turn["sessionId"])
            self.assertEqual(native.get("sessionIdSource"), "validated-turn")
            self.assertFalse(native.get("sessionIdConflict"))

            # This is the retained final native fact, not a claim that a live
            # observer polled before the short fixture turn ended.
            from hey_my_buddy.buddy.harnesses.run_contract import decode_run_result
            native_result = decode_run_result(Path(receipt["logPaths"]["stdout"]).read_bytes())
            self.assertEqual(native_result.identity.task_id, run_id)
            self.assertEqual(native_result.identity.attempt_id, turn["attemptId"])
            activity = native_result.activity.value
            self.assertEqual(activity["phase"], "finishing")
            self.assertEqual(activity["nativeSessionId"], turn["sessionId"])
            self.assertEqual(activity["counts"], {"modelTurns": 1, "toolCalls": 1})
            activity_meta = (receipt.get("result") or {}).get("activity") or {}
            self.assertEqual(activity_meta["phase"], activity["phase"])
            self.assertEqual(activity_meta["eventSeq"], activity["eventSeq"])
            self.assertNotIn("sidecarWritten", activity_meta)
            attempt_directory = self.directory / "attempts" / run_id / turn["attemptId"]
            self.assertFalse((attempt_directory / "activity.json").exists())

            # The final acceptance is separate from execution and bound to the
            # actual sealed artifact, with its explicit integration decision.
            control = json.loads(control_path.read_text())
            code, acknowledged = self.cli(
                "accept",
                json.dumps(
                    {
                        "runId": run_id,
                        "artifactId": outputs[0]["artifactId"],
                        "note": "inspected the sealed output",
                        "notRequired": "the mock turn is reviewed without a separate integration target",
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
        # The console cannot submit work or issue a Host capability.
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
        self.assertEqual(status, 404, body)
        self.assertEqual(json.loads(body)["error"]["code"], "METHOD_NOT_FOUND")
        self.assertNotIn("controlFile", json.dumps(json.loads(body)))
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

        from hey_my_buddy.cli import main as cli
        from hey_my_buddy.errors import BoardError

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



if __name__ == "__main__":  # pragma: no cover
    unittest.main()
