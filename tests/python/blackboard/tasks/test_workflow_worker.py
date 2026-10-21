"""Real cross-process micro-task workflow: daemon, execution, workspace and CLI routes.

The DSH harness runs through its registered run module against an offline
Python micro-task ACP agent (``buddy/harnesses/dsh/fixtures/workflow_agent.py``,
selected as a direct executable command through ``HMB_DSH_CLI``); everything
else is the real service: real daemon, execution process, the merged
Git-backed workspace module, the real C-Two/CLI path and the real console
command route.
"""
from __future__ import annotations
from admission import admit_board, admit_store

import json
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path

from support import BoardTestCase, enable_fixture_buddy, wait_for
from fake_executable import make_fake_executable
from hey_my_buddy.cli.checks import prepare_test_home
from console.test_console import Browser

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Buddy Test",
    "GIT_AUTHOR_EMAIL": "buddy@example.invalid",
    "GIT_COMMITTER_NAME": "Buddy Test",
    "GIT_COMMITTER_EMAIL": "buddy@example.invalid",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}
FIXTURE = Path(__file__).resolve().parents[2] / "buddy/harnesses/dsh/fixtures/workflow_agent.py"
BUDDY = {"harness": "dsh", "provider": "deepseek-official", "model": "deepseek-flash", "effort": "off"}


def harness_environment(directory: Path, entry: Path) -> dict:
    """Select the offline micro-task agent as the harness's CLI entry.

    The fixture is a generated native entry on Windows and an executable
    script on POSIX, so the daemon and execution process use the same shared
    CLI resolver. HOME stays inside the test's private root for the agent's
    forced-private-home tripwire.
    """
    home = directory / "home"
    prepare_test_home(home)
    return {"HOME": str(home), "HMB_DSH_CLI": str(entry)}


class MicroTaskWorkerTestCase(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.catalog_fixture()
        self.harness_cli = make_fake_executable(self.directory / "harness-bin", "dsh", source=FIXTURE)
        self.repo = self.directory / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "buddy@example.invalid")
        self.git("config", "user.name", "Buddy Test")
        (self.repo / "tracked.txt").write_text("base\n", encoding="utf-8", newline="\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")

    def git(self, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments], cwd=self.repo, env={**os.environ, **GIT_ENV}, capture_output=True, text=True, encoding="utf-8"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout

    def env(self) -> dict:
        return harness_environment(self.directory, self.harness_cli)

    def submit(self, request_id: str = "worker-req-1") -> tuple[int, dict]:
        return self.cli(
            "submit",
            json.dumps(
                {
                    **BUDDY,
                    "requestId": request_id,
                    "hostId": "host-1",
                    "task": "run the mock turn",
                    "cwd": str(self.repo),
                    "executionWorkspace": {"kind": "existing", "access": "write"},
                }
            ),
            env=self.env(),
        )


class RealWorkerTurnTests(MicroTaskWorkerTestCase):
    def test_execution_record_fields_are_rejected_by_micro_task_submission(self):
        with self.daemon(env=self.env()):
            for index, fields in enumerate(({'adapter': 'command', 'argv': ['true']},
                                             {'adapter': 'external'}, {'argv': ['true']})):
                with self.subTest(fields=fields):
                    code, response = self.cli('submit', json.dumps({
                        'requestId': f'execution-record-{index}', 'hostId': 'host-1', 'task': 'separate execution',
                        'cwd': str(self.repo), 'executionWorkspace': {'kind': 'existing', 'access': 'write'},
                        **fields,
                    }), env=self.env())
                    self.assertNotEqual(code, 0, response)
                    self.assertEqual(response['error']['code'], 'INVALID_ARGUMENT')
                    self.assertIn('Micro tasks do not accept', response['error']['message'])
            code, listing = self.cli('list', '{}', env=self.env())
            self.assertEqual(code, 0, listing)
            self.assertEqual(listing['runs'], [])

    def execution_diagnostics(self, run_id: str, view: dict) -> str:
        code, receipt = self.cli("result", json.dumps({"runId": run_id}), env=self.env())
        meta = receipt.get("resultMeta") or {}
        result = receipt.get("result") or {}
        details = {
            "microTaskState": view.get("state"),
            "taskState": (view.get("task") or {}).get("state"),
            "resultExitCode": code,
            "resultError": receipt.get("error"),
            "resultMeta": {key: meta.get(key) for key in ("status", "error", "exitCode", "shutdownConfirmed")},
            "turnError": result.get("turnError"),
            "microTaskError": result.get("microTaskError"),
            "logs": {},
        }
        for name, location in (receipt.get("logPaths") or {}).items():
            if name not in ("stdout", "stderr") or not location:
                continue
            path = Path(location).resolve()
            if path.is_relative_to(self.directory.resolve()) and path.is_file():
                details["logs"][name] = path.read_text(errors="replace", encoding="utf-8")[-4000:]
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
            # run's rollout lands under the task's shared private sessions
            # root, the owning credentials store keeps resolving by path, and
            # the installed app lists none of it. The session identity comes
            # only from the imported, validated turn record — sessionIdSource
            # names that source, and no conflict fact exists to clear (the
            # unvalidated side of this rule is the Host's own
            # DshPublishedReceiptTests witness). The retained native result
            # supplies its activity facts after the short live endpoint ends.
            _, receipt = self.cli("result", json.dumps({"runId": run_id, "output": "full"}), env=self.env())
            native = (receipt.get("result") or {}).get("nativeSession") or {}
            self.assertEqual(native.get("storageScope"), "task-shared-sessions")
            self.assertEqual(native.get("storageOwner"), "hey-my-buddy-micro-task")
            self.assertEqual(native.get("nativeAppVisibility"), "not-listed-in-native-app")
            self.assertEqual(native.get("credentialsStore"), "harness-user-store")
            self.assertTrue(native.get("bindingPresent"))
            self.assertTrue(native.get("captured"))
            self.assertTrue(native.get("resumable"))
            self.assertEqual(native.get("sessionId"), turn["sessionId"])
            self.assertEqual(native.get("sessionIdSource"), "validated-turn")
            self.assertFalse(native.get("sessionIdConflict"))

            # The execution publishes the final native activity before its
            # result. No controller stdout/result frame exists in M1.
            self.assertEqual(receipt["attemptId"], turn["attemptId"])
            from hey_my_buddy.protocol.client import BoardClient
            activity = BoardClient(self.directory, autostart=False).get(runId=run_id)["activity"]
            self.assertEqual(activity["phase"], "finishing")
            self.assertEqual(activity["nativeSessionId"], turn["sessionId"])
            self.assertEqual(activity["counts"], {"modelTurns": 1, "toolCalls": 1})
            activity_meta = (receipt.get("result") or {}).get("activity") or {}
            self.assertEqual(activity_meta["phase"], activity["phase"])
            self.assertEqual(activity_meta["eventSeq"], activity["eventSeq"])
            self.assertNotIn("sidecarWritten", activity_meta)
            attempt_directory = self.directory / "attempts" / run_id / turn["attemptId"]
            self.assertFalse((attempt_directory / "activity.json").exists())
            bindings = [json.loads(line) for log in self.directory.rglob("fixture-agent.log")
                        for line in log.read_text(encoding="utf-8").splitlines() if '"workflow-binding"' in line]
            self.assertEqual(len(bindings), 1)
            self.assertEqual(bindings[0]["launcherEnvKeys"], [])

            # The final acceptance is separate from execution and bound to the
            # actual sealed artifact, with its explicit integration decision.
            control = json.loads(control_path.read_text(encoding="utf-8"))
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
            self.assertIsNotNone(acknowledged["acceptedAt"])
            self.assertNotIn("acceptanceVerdict", acknowledged)

    def test_cli_fails_closed_for_an_agent_scoped_caller(self):
        with self.daemon(env=self.env()):
            code, submitted = self.submit("worker-req-2")
            self.assertEqual(code, 0, submitted)
            credential = self.directory / "agent-credential.json"
            credential.write_text(json.dumps({"token": "not-a-real-credential", "stateDir": str(self.directory)}), encoding="utf-8", newline="\n")
            os.chmod(credential, 0o600)
            env = {**self.env(), "HMB_AGENT_CREDENTIAL_FILE": str(credential)}
            code, refused = self.cli(
                "submit",
                json.dumps({"requestId": "forged", "task": "x", "cwd": str(self.repo)}),
                env=env,
            )
            self.assertEqual(code, 1)
            self.assertEqual(refused["error"]["code"], "UNAUTHORIZED")
            # A control file cannot make an invalid execution credential valid.
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
            self.assertEqual(refused["error"]["code"], "UNAUTHORIZED")


    def test_ordinary_harness_launcher_has_no_board_or_agent_file(self):
        from hey_my_buddy.buddy.harnesses.runtime_selection import controller_environment
        from hey_my_buddy.buddy.harnesses.dsh.acp.launch import child_environment
        board = self.board()
        enable_fixture_buddy(board.store, BUDDY)
        submitted = board.call("workflow_submit", {
            **BUDDY, "requestId": "ordinary-launcher", "hostId": "host-1", "task": "run the mock turn",
            "cwd": str(self.repo), "executionWorkspace": {"kind": "existing", "access": "write"},
        })
        response = admit_board(board, {'workerId': 'ordinary-worker', 'claimRequestId': 'ordinary-claim', 'runId': submitted['runId'], 'nonce': 'o' * 16})
        credential = self.directory / "ordinary-credential.json"
        credential.write_text(json.dumps({"token": response["claim"]["agentCredential"]}), encoding="utf-8", newline="\n")
        credential.chmod(0o600)
        incoming = {**self.child_environment(self.env()), "HMB_AGENT_CREDENTIAL_FILE": str(credential)}
        controller = controller_environment(self.directory, incoming)
        child = child_environment(source=controller)
        self.assertNotIn("HMB_STATE_DIR", child)
        self.assertNotIn("HMB_AGENT_CREDENTIAL_FILE", child)

    def test_lead_harness_launcher_reaches_the_private_issuing_board(self):
        from unittest import mock
        from hey_my_buddy.buddy.harnesses.runtime_selection import controller_environment
        from hey_my_buddy.buddy.harnesses.dsh.acp.launch import child_environment
        from hey_my_buddy.buddy.roles.prompts import worker_prompt
        from hey_my_buddy.protocol import capabilities as execution_capabilities
        board = self.board()
        enable_fixture_buddy(board.store, BUDDY)
        with self.daemon(env=self.env()):
            # The extra capability leaves this test's Lead turn queued for the
            # in-process admission while the daemon serves its CLI calls.
            code, submitted = self.cli("submit", json.dumps({
                **BUDDY, "requestId": "launcher-private", "hostId": "host-1", "task": "run the mock turn",
                "cwd": str(self.repo), "submissionToken": "l" * 32, "delegationAuthority": True,
                "requiredCapabilities": ["launcher-private"],
                "executionWorkspace": {"kind": "existing", "access": "full", "accessNote": "Lead tasks"},
            }), env=self.env())
            self.assertEqual(code, 0, submitted)
            with mock.patch.dict(execution_capabilities.ADAPTER_CAPABILITIES,
                                 dsh=(*execution_capabilities.ADAPTER_CAPABILITIES["dsh"], "launcher-private")):
                response = admit_board(board, {'runId': submitted['runId'], 'nonce': 'n' * 16})
            self.assertIsNotNone(response["claim"], response)
            credential = self.directory / "launcher-credential.json"
            credential.write_text(json.dumps({"token": response["claim"]["agentCredential"],
                                              "stateDir": str(self.directory)}), encoding="utf-8", newline="\n")
            credential.chmod(0o600)
            incoming = {**self.child_environment(self.env()), "HMB_AGENT_CREDENTIAL_FILE": str(credential)}
            controller = controller_environment(self.directory, incoming)
            environment = child_environment(source=controller)
            self.assertNotIn("HMB_STATE_DIR", environment)
            self.assertNotIn("HMB_AGENT_CREDENTIAL_FILE", environment)
            prompt = worker_prompt("Hand out a bounded task", response["claim"]["turn"]["input"],
                                   delivery=(), credential_file=str(credential))
            path_text = prompt.split("Credential file path: ", 1)[1].split("\n", 1)[0]
            prompt_path = json.loads(path_text)
            self.assertEqual(prompt_path, str(credential))
            self.assertNotIn(response["claim"]["agentCredential"], prompt)
            # The model-facing native command starts without any Buddy environment.
            # It sets only the prompt's credential-file path for this launcher call.
            child_repo = self.directory / "owned-repo"
            child_repo.mkdir()
            for arguments in (("init", "-q", "-b", "main"), ("config", "user.email", "buddy@example.invalid"),
                              ("config", "user.name", "Buddy Test"), ("add", "."),
                              ("commit", "-q", "-m", "base", "--allow-empty")):
                prepared = subprocess.run(["git", *arguments], cwd=child_repo, env={**os.environ, **GIT_ENV},
                                          capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(prepared.returncode, 0, prepared.stderr)
            child_request = {**BUDDY, "requestId": "launcher-owned", "task": "Do bounded work",
                             "output": "full",
                             "cwd": str(child_repo), "submissionToken": "p" * 32,
                             "executionWorkspace": {"kind": "existing", "access": "write"}}
            # Source-check fixture bypasses only installed-runtime selection;
            # the launcher and CLI run in their own process with no inherited
            # HMB state, runtime or role variables.
            bootstrap = ("import json,sys; from unittest import mock; "
                         "from hey_my_buddy.install import launcher; "
                         "guard=mock.patch.object(launcher,'selected_runtime',return_value=None); "
                         "guard.start(); sys.exit(launcher.main(['submit',sys.argv[1]]))")
            # A fixture harness reads the role prompt and launches its command
            # with a fresh environment, as a native harness may do.
            fixture_harness = (
                "import json,subprocess,sys; prompt=sys.stdin.read(); "
                "path=json.loads(prompt.split('Credential file path: ',1)[1]"
                ".split('\\n',1)[0]); "
                "command=subprocess.run([sys.executable,'-c',sys.argv[1],sys.argv[2]],"
                "env={'HMB_AGENT_CREDENTIAL_FILE':path},capture_output=True,text=True,encoding='utf-8'); "
                "sys.stdout.write(command.stdout); sys.stderr.write(command.stderr); "
                "sys.exit(command.returncode)"
            )
            command = subprocess.run([sys.executable, "-c", fixture_harness, bootstrap, json.dumps(child_request)],
                                     cwd=Path(__file__).resolve().parents[4] / "src", env=environment,
                                     input=prompt, capture_output=True, text=True, timeout=30, encoding="utf-8")
            self.assertEqual(command.returncode, 0, command.stdout + command.stderr)
            owned = json.loads(command.stdout)
            self.assertIsNone(owned["hostId"])
            self.assertEqual(owned["leadOwnership"]["leadRunId"], submitted["runId"])
            self.assertEqual(owned["leadOwnership"]["leadTurnId"], response["claim"]["turn"]["turnId"])


class ConsoleWorkflowRouteTests(MicroTaskWorkerTestCase):
    def test_console_command_route_strips_the_capability_and_refuses_override(self):
        board = self.board()
        opened = board.console.start()
        browser = Browser(opened['url'])
        snapshot = browser.bootstrap()
        csrf = snapshot["csrfToken"]
        created = board.call(
            "workflow_submit",
            {
                **BUDDY,
                "requestId": "console-req-1",
                "hostId": "host-1",
                "task": "console micro-task task",
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
                **BUDDY,
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


class CompactRouteTests(MicroTaskWorkerTestCase):
    def test_workflow_get_matches_the_reported_compact_shape(self):
        board = self.board()
        submitted = board.call(
            "workflow_submit",
            {
                **BUDDY,
                "requestId": "shape-req-1",
                "hostId": "host-1",
                "task": "shape",
                "cwd": str(self.repo),
                "executionWorkspace": {"kind": "existing", "access": "write"},
            },
        )
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        expected = {
            "microTask",
            "runId",
            "taskId",
            "requestId",
            "hostId",
            "ownerGeneration",
            "state",
            "status",
            "queueReason",
            "awaitingHost",
            "microTask",
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
        self.assertEqual(view["microTask"]["fingerprint"], submitted["microTask"]["fingerprint"])
        self.assertTrue(all(isinstance(value, int) for value in view["truncated"].values()))
        self.assertTrue(all(isinstance(value, int) for value in view["counts"].values()))


class SubmissionPreparationRaceTests(MicroTaskWorkerTestCase):
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

        with mock.patch.dict(os.environ, {"HMB_STATE_DIR": str(self.directory)}):
            tokens = self._race(lambda: cli._submission_token("req-race", {}, self.directory))
            self.assertEqual(len(set(tokens)), 1, tokens)
            path = next((self.directory / "submissions").glob("*.json"))
            original = path.read_text(encoding="utf-8")
            path.write_text("not-json", encoding="utf-8", newline="\n")
            with self.assertRaises(BoardError) as raised:
                cli._submission_token("req-race", {}, self.directory)
            self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
            self.assertEqual(path.read_text(encoding="utf-8"), "not-json", "a malformed record is never overwritten")
            path.write_text(original, encoding="utf-8", newline="\n")



if __name__ == "__main__":  # pragma: no cover
    unittest.main()
