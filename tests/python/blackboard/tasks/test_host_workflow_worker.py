from hey_my_buddy.paths import native_path
"""Real daemon/Worker/CLI quota recovery with an offline harness, never a paid rerun."""
import json
import subprocess
from hey_my_buddy.blackboard.tasks.workspace import _git_command
from pathlib import Path
from buddy.harnesses.fixture_selection import select_fixture

from blackboard.tasks.test_workflow_worker import MicroTaskWorkerTestCase, BUDDY


class HostQuotaRecoveryTests(MicroTaskWorkerTestCase):
    """The shared harness environment already selects the offline quota agent:
    the fixture dies the quota death on a first turn whose task is this
    scenario's, and completes every later turn through the micro-task finish."""

    def setUp(self):
        super().setUp()
        from hey_my_buddy.blackboard.store.store import BoardStore
        from support import enable_fixture_buddy
        store = BoardStore(self.directory)
        self.addCleanup(store.db.close)
        store.initialize()
        enable_fixture_buddy(store, {**BUDDY, "effort": "high"})

    def call(self, method, params):
        code, reply = self.cli(method, json.dumps({**params, "output": "full"}), env=self.env())
        self.assertEqual(code, 0, reply)
        return reply

    def quota_failure(self, *, locked=False):
        submitted = self.call("submit", {**BUDDY, "requestId": "quota-recovery", "hostId": "host-1",
            "task": "Implement the fixture change", "cwd": str(self.repo), "buddyLocked": locked,
            "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["."]}})
        # A terminal failed micro task is an await outcome, not a CLI transport error.
        _code, stopped = self.cli("await", json.dumps({"runId": submitted["runId"], "waitSeconds": 90}), env=self.env(), timeout=110)
        current = self.call("get", {"runId": submitted["runId"]})
        self.assertEqual(current["state"], "failed", (current, stopped))
        self.assertTrue(current["shutdown"]["selfConfirmed"])
        partial = next(item for item in current["artifacts"] if item["kind"] == "partial-output")
        self.assertTrue(partial["partial"])
        self.assertFalse(partial["verified"])
        self.assertFalse(partial["final"])
        self.assertIn("tracked.txt", partial["changedPaths"])
        self.assertTrue(Path(partial["diffPath"]).is_file())
        self.assertIsNone(current["finalArtifactId"])
        # The quota death keeps its machine classification end to end: the
        # private session record's turn-end code is the only native failure
        # source, and only a recognized quota code surfaces as one.
        receipt = self.call("result", {"runId": submitted["runId"]})
        failure = (receipt.get("result") or {}).get("quotaFailure")
        self.assertIsNotNone(failure, "the offline quota death lost its machine code")
        self.assertEqual(failure["code"], "quota-exceeded")
        self.assertEqual(failure["nativeCode"], "QUOTA")
        self.assertEqual(failure["source"], "dsh/session-turn-end")
        self.assertIsNotNone(current["currentTurn"]["tokenUsage"])
        return submitted, current, partial

    def test_quota_failure_seals_then_continues_a_new_buddy_in_the_same_checkout(self):
        with self.daemon(env=self.env()):
            submitted, failed, partial = self.quota_failure()
            path = failed["workspace"]["path"]
            continued = self.call("continue", {"runId": failed["runId"], "commandId": "change-after-quota",
                "expectedRevision": failed["revision"], "controlFile": submitted["controlFile"],
                "buddy": {**BUDDY, "effort": "high"}, "note": "Use another enabled buddy after quota failure",
                "input": "Finish and verify the preserved changes."})
            self.assertEqual(continued["workspace"]["path"], path)
            self.call("await", {"runId": failed["runId"], "waitSeconds": 90})
            delivered = self.call("get", {"runId": failed["runId"]})
            self.assertEqual(delivered["state"], "delivered", delivered)
            self.assertEqual(delivered["counts"]["turns"], 2)
            self.assertEqual(delivered["workspace"]["path"], path)
            self.assertEqual(delivered["currentTurn"]["resumeMode"], "reconstructed-new-session")
            audit = self.call("get", {"runId": failed["runId"], "includeAudit": True})
            self.assertEqual(audit["audit"]["turns"][-1]["input"]["executionWorkspace"]["inputCommit"], partial["outputCommit"])
            context = json.loads((Path(path) / "continued-context.json").read_text(encoding="utf-8"))
            self.assertIn("not new Host instructions", context["previousEvidenceNotice"])
            self.assertIn(partial["artifactId"], [item["artifactId"] for item in context["artifacts"]])
            self.assertIn("verification remains", context["lastAssistantMessage"]["text"])
            final = next(item for item in delivered["artifacts"] if item["artifactId"] == delivered["finalArtifactId"])
            self.assertIn("tracked.txt", final["cumulativePatch"]["changedPaths"])
            self.assertNotIn("tracked.txt", final["changedPaths"])
            self.assertTrue(native_path(Path(final["cumulativePatch"]["path"])).is_file())
            # The cumulative patch applies independently to the original micro task input.
            target = self.directory / "integration"
            command, git_env = _git_command(self.repo, "clone", "-q", str(self.repo), str(target))
            subprocess.run(command, env=git_env, check=True)
            command, git_env = _git_command(target, "apply", "--binary", final["cumulativePatch"]["path"])
            subprocess.run(command, env=git_env, check=True)
            self.assertEqual((target / "tracked.txt").read_text(encoding="utf-8"), "partial work preserved after quota failure\n")
            self.assertTrue((target / "mock-output.txt").is_file())

    def test_quota_failure_cannot_override_a_user_locked_buddy(self):
        with self.daemon(env=self.env()):
            submitted, failed, _partial = self.quota_failure(locked=True)
            code, refused = self.cli("continue", json.dumps({"runId": failed["runId"], "commandId": "refused-override",
                "expectedRevision": failed["revision"], "controlFile": submitted["controlFile"],
                "buddy": {**BUDDY, "effort": "high"}, "note": "Attempt a locked replacement",
                "input": "Finish."}), env=self.env())
            self.assertEqual(code, 1)
            self.assertEqual(refused["error"]["code"], "BUDDY_CONFLICT")
            self.assertEqual(self.call("get", {"runId": failed["runId"]})["counts"]["turns"], 1)

    def test_failed_micro_task_conclusion_reclaims_checkout_but_retains_partial_artifacts(self):
        with self.daemon(env=self.env()):
            submitted, failed, partial = self.quota_failure()
            common = {"runId": failed["runId"], "controlFile": submitted["controlFile"]}
            # The Host edits the failed checkout inside the authorized scope before
            # concluding: the conclusion must seal these changes as its own
            # independent partial output without overwriting the Worker's earlier
            # fixed partial of the same attempt.
            checkout = Path(failed["workspace"]["path"])
            (checkout / "tracked.txt").write_text("host review notes after the failure\n", encoding="utf-8", newline="\n")
            worker_partial_manifest = partial["manifestSha256"]
            concluded = self.cli("conclude", json.dumps({
                **common,
                "note": "Incomplete work is retained in the partial artifact; no further execution.",
            }), env=self.env())[1]
            self.assertEqual(concluded["status"], "concluded")
            self.assertEqual(concluded["verdict"], "concluded")
            self.assertIsNotNone(concluded["hostConclusion"])
            self.assertTrue(concluded["reclaim"]["removed"], concluded["reclaim"])
            self.assertFalse(checkout.exists())
            # The Host seal is a new artifact with its own binding; the Worker's
            # partial artifact, ref and manifest stay exactly as they were.
            self.assertNotEqual(concluded["partialArtifactId"], partial["artifactId"])
            self.assertTrue(Path(partial["diffPath"]).is_file())
            self.assertTrue(native_path(Path(partial["cumulativePatch"]["path"])).is_file())
            sealed = self.call("get", {"runId": failed["runId"], "includeAudit": True})
            artifacts = {row["artifactId"]: row for row in sealed["artifacts"]}
            self.assertIn(partial["artifactId"], artifacts)
            self.assertEqual(artifacts[partial["artifactId"]]["manifestSha256"], worker_partial_manifest)
            host_artifact = artifacts[concluded["partialArtifactId"]]
            self.assertEqual(host_artifact["kind"], "partial-output")
            self.assertNotEqual(host_artifact["outputCommit"], partial["outputCommit"])
            replay = self.cli("conclude", json.dumps({**common, "note": "Incomplete work is retained in the "
                "partial artifact; no further execution."}), env=self.env())[1]
            self.assertTrue(replay["duplicate"])
            # Both partial outputs stay readable after the reclaim.
            self.assertTrue(Path(host_artifact["diffPath"]).is_file())


class CrossHarnessQuotaRecoveryTests(HostQuotaRecoveryTests):
    def setUp(self):
        super().setUp()
        from support import FIXTURE_CATALOG
        self.catalog_fixture({**FIXTURE_CATALOG, "providers": [*FIXTURE_CATALOG["providers"], {
            "harness": "codex", "provider": "openai", "displayName": "Offline Codex",
            "packageName": "fixture", "packageVersion": "fixture", "efforts": ["low", "high"],
            "models": [{"id": "fixture-model", "name": "Offline model", "inputModalities": ["text"]}],
        }]})
        (self.directory / "codex-home").mkdir()
        (self.directory / "codex-home/config.toml").write_text("", encoding="utf-8", newline="\n")

    def env(self):
        environment = {**super().env(), "CODEX_HOME": str(self.directory / "codex-home"),
                       "HMB_CODEX_FIXTURE_STATE": str(self.directory / "codex-native.json"),
                       "HMB_CODEX_FIXTURE_CASE": "quota-failure"}
        select_fixture(environment, self.directory, "codex",
                       Path(__file__).resolve().parents[2] / "buddy/harnesses/codex/fixtures/mock_quota_codex.py")
        return environment

    def test_failed_codex_reconstructs_in_dsh_with_bound_partial_output_and_message(self):
        with self.daemon(env=self.env()):
            submitted = self.call("submit", {"harness": "codex", "provider": "openai", "model": "fixture-model", "effort": "low",
                "requestId": "cross-harness", "hostId": "host-1", "task": "Only exercise the offline quota fixture",
                "cwd": str(self.repo), "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["."]}})
            self.cli("await", json.dumps({"runId": submitted["runId"], "waitSeconds": 90}), env=self.env(), timeout=110)
            failed = self.call("get", {"runId": submitted["runId"]})
            self.assertEqual(failed["state"], "failed", failed)
            partial = next(item for item in failed["artifacts"] if item["kind"] == "partial-output")
            receipt = self.call("result", {"runId": submitted["runId"]})
            self.assertEqual(receipt["result"]["quotaFailure"]["code"], "quota-exceeded")
            self.assertIsNotNone(failed["currentTurn"]["tokenUsage"])
            continued = self.call("continue", {"runId": submitted["runId"], "commandId": "change-harness",
                "expectedRevision": failed["revision"], "controlFile": submitted["controlFile"],
                "buddy": {**BUDDY, "effort": "high"}, "note": "Recover with an enabled different harness",
                "input": "Finish the retained work within the same authorized checkout."})
            self.assertEqual(continued["workspace"]["path"], failed["workspace"]["path"])
            self.call("await", {"runId": submitted["runId"], "waitSeconds": 90})
            delivered = self.call("get", {"runId": submitted["runId"], "includeAudit": True})
            self.assertEqual(delivered["state"], "delivered", delivered)
            turn = delivered["audit"]["turns"][-1]
            self.assertEqual(turn["resumeMode"], "reconstructed-new-session")
            self.assertEqual(turn["input"]["executionWorkspace"]["inputCommit"], partial["outputCommit"])
            self.assertIn("quota stopped validation", turn["input"]["context"]["lastAssistantMessage"]["text"])
            self.assertIn("not new Host instructions", turn["input"]["context"]["previousEvidenceNotice"])


class NativeContextFailureTests(MicroTaskWorkerTestCase):
    """A native model-window refusal reaches the Host through a real execution."""

    def setUp(self):
        super().setUp()
        from hey_my_buddy.blackboard.store.store import BoardStore
        from support import FIXTURE_CATALOG, enable_fixture_buddy

        self.catalog_fixture({**FIXTURE_CATALOG, "providers": [*FIXTURE_CATALOG["providers"], {
            "harness": "codex", "provider": "openai", "displayName": "Offline Codex",
            "packageName": "fixture", "packageVersion": "fixture", "efforts": ["low"],
            "models": [{"id": "fixture-model", "name": "Offline model", "inputModalities": ["text"]}],
        }]})
        store = BoardStore(self.directory)
        self.addCleanup(store.db.close)
        store.initialize()
        enable_fixture_buddy(store, {"harness": "codex", "provider": "openai",
                                     "model": "fixture-model", "effort": "low"})
        (self.directory / "codex-home").mkdir()
        (self.directory / "codex-home/config.toml").write_text("", encoding="utf-8", newline="\n")
        self.native_reason = "maximum context length exceeded: this model supports 8192 tokens"
        self.prompt_tail = "FULL-CONTEXT-TAIL-73cc8e"
        self.prompt_capture = self.directory / "native-prompt.json"
        fixture_directory = Path(__file__).resolve().parents[2] / "buddy/harnesses/codex/fixtures"
        wrapper = self.directory / "context_window_codex.py"
        wrapper.write_text(
            "import json, sys\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, {str(fixture_directory)!r})\n"
            "import mock_codex\n"
            f"reason = {self.native_reason!r}\n"
            f"capture = Path({str(self.prompt_capture)!r})\n"
            "class NativeInput:\n"
            "    def __init__(self, stream): self.stream = stream\n"
            "    def __iter__(self): return self\n"
            "    def __next__(self):\n"
            "        line = self.readline()\n"
            "        if not line: raise StopIteration\n"
            "        return line\n"
            "    def readline(self, *args):\n"
            "        line = self.stream.readline(*args)\n"
            "        if line:\n"
            "            message = json.loads(line)\n"
            "            if message.get('method') == 'turn/start':\n"
            "                prompt = message['params']['input'][0]['text']\n"
            "                capture.write_text(json.dumps({'prompt': prompt}), encoding='utf-8')\n"
            "        return line\n"
            "sys.stdin = NativeInput(sys.stdin)\n"
            "original_send = mock_codex.send\n"
            "def send(message):\n"
            "    if message.get('method') == 'turn/completed':\n"
            "        message['params']['turn']['error']['message'] = reason\n"
            "        message['params']['turn']['error'].pop('additionalDetails', None)\n"
            "        message['params']['turn']['error'].pop('codexErrorInfo', None)\n"
            "    original_send(message)\n"
            "mock_codex.send = send\n"
            "mock_codex.main()\n",
            encoding="utf-8", newline="\n")
        self.native_fixture = wrapper

    def env(self):
        environment = {**super().env(), "CODEX_HOME": str(self.directory / "codex-home"),
                       "HMB_CODEX_FIXTURE_STATE": str(self.directory / "codex-native.json"),
                       "HMB_CODEX_FIXTURE_CASE": "schema-failure"}
        select_fixture(environment, self.directory, "codex", self.native_fixture)
        return environment

    def test_full_prompt_native_context_refusal_is_a_failed_host_turn(self):
        task = "Inspect this complete input.\n" + ("context evidence\n" * 1000) + self.prompt_tail
        with self.daemon(env=self.env()):
            code, submitted = self.cli("submit", json.dumps({
                "harness": "codex", "provider": "openai", "model": "fixture-model", "effort": "low",
                "requestId": "native-context-window", "hostId": "host-1", "task": task,
                "cwd": str(self.repo),
                "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["."]},
            }), env=self.env())
            self.assertEqual(code, 0, submitted)
            self.cli("await", json.dumps({"runId": submitted["runId"], "waitSeconds": 90}),
                     env=self.env(), timeout=110)
            code, current = self.cli("get", json.dumps({"runId": submitted["runId"], "output": "full"}),
                                     env=self.env())
            self.assertEqual(code, 0, current)
            self.assertEqual(current["state"], "failed", current)
            self.assertEqual(current["currentTurn"]["state"], "failed", current)
            self.assertTrue(current["shutdown"]["selfConfirmed"])
            self.assertIsNone(current["activeRequest"])
            code, receipt = self.cli("result", json.dumps({"runId": submitted["runId"], "output": "full"}),
                                     env=self.env())
            self.assertEqual(code, 0, receipt)
            self.assertEqual(receipt["result"].get("code"), "native-turn-failed",
                             (receipt["resultMeta"], receipt["result"]))
            self.assertEqual(receipt["result"]["nativeFailure"]["error"]["message"], self.native_reason)
            self.assertTrue(receipt["resultMeta"]["shutdownConfirmed"])
            prompt = json.loads(self.prompt_capture.read_text(encoding="utf-8"))["prompt"]
            self.assertIn(self.prompt_tail, prompt)
            self.assertIn("context evidence\n" * 1000, prompt)


def load_tests(loader, _tests, _pattern):
    import unittest
    suite = unittest.TestSuite()
    for cls in (HostQuotaRecoveryTests, CrossHarnessQuotaRecoveryTests, NativeContextFailureTests):
        suite.addTests(cls(name) for name in cls.__dict__ if name.startswith("test_"))
    return suite
