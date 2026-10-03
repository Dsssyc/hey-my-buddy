"""Real daemon/Worker/CLI quota recovery with an offline harness, never a paid rerun."""
import json
import subprocess
from pathlib import Path

from test_workflow_worker import GovernedWorkerTestCase, CONFIGURATION


class HostQuotaRecoveryTests(GovernedWorkerTestCase):
    def setUp(self):
        super().setUp()
        from hey_my_buddy.blackboard.store.store import BoardStore
        from support import enable_fixture_configuration
        store = BoardStore(self.directory)
        store.initialize()
        enable_fixture_configuration(store, {**CONFIGURATION, "effort": "high"})

    def env(self):
        return {"BUDDY_RUNNER_PATH": str(Path(__file__).parent / "fixtures/mock_quota_turn_runner.mjs")}

    def call(self, method, params):
        code, reply = self.cli(method, json.dumps({**params, "output": "full"}), env=self.env())
        self.assertEqual(code, 0, reply)
        return reply

    def quota_failure(self, *, locked=False):
        submitted = self.call("submit", {**CONFIGURATION, "requestId": "quota-recovery", "hostId": "host-1",
            "task": "Implement the fixture change", "cwd": str(self.repo), "configurationLocked": locked,
            "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["."]}})
        # A terminal failed goal is an await outcome, not a CLI transport error.
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
        return submitted, current, partial

    def test_quota_failure_seals_then_continues_a_new_configuration_in_the_same_checkout(self):
        with self.daemon(env=self.env()):
            submitted, failed, partial = self.quota_failure()
            path = failed["workspace"]["path"]
            continued = self.call("continue", {"runId": failed["runId"], "commandId": "change-after-quota",
                "expectedRevision": failed["revision"], "controlFile": submitted["controlFile"],
                "configuration": {**CONFIGURATION, "effort": "high"}, "reason": "Use another enabled configuration after quota failure",
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
            context = json.loads((Path(path) / "continued-context.json").read_text())
            self.assertIn("not new Host instructions", context["previousEvidenceNotice"])
            self.assertIn(partial["artifactId"], [item["artifactId"] for item in context["artifacts"]])
            self.assertIn("verification remains", context["lastAssistantMessage"]["text"])
            final = next(item for item in delivered["artifacts"] if item["artifactId"] == delivered["finalArtifactId"])
            self.assertIn("tracked.txt", final["cumulativePatch"]["changedPaths"])
            self.assertNotIn("tracked.txt", final["changedPaths"])
            self.assertTrue(Path(final["cumulativePatch"]["path"]).is_file())
            # The cumulative patch applies independently to the original goal input.
            target = self.directory / "integration"
            subprocess.run(["git", "clone", "-q", str(self.repo), str(target)], check=True)
            subprocess.run(["git", "-C", str(target), "apply", "--binary", final["cumulativePatch"]["path"]], check=True)
            self.assertEqual((target / "tracked.txt").read_text(), "partial work preserved after quota failure\n")
            self.assertTrue((target / "mock-output.txt").is_file())

    def test_quota_failure_cannot_override_a_user_locked_configuration(self):
        with self.daemon(env=self.env()):
            submitted, failed, _partial = self.quota_failure(locked=True)
            code, refused = self.cli("continue", json.dumps({"runId": failed["runId"], "commandId": "refused-override",
                "expectedRevision": failed["revision"], "controlFile": submitted["controlFile"],
                "configuration": {**CONFIGURATION, "effort": "high"}, "reason": "Attempt a locked replacement",
                "input": "Finish."}), env=self.env())
            self.assertEqual(code, 1)
            self.assertEqual(refused["error"]["code"], "CONFIGURATION_CONFLICT")
            self.assertEqual(self.call("get", {"runId": failed["runId"]})["counts"]["turns"], 1)

    def test_failed_goal_conclusion_reclaims_checkout_but_retains_partial_artifacts(self):
        with self.daemon(env=self.env()):
            submitted, failed, partial = self.quota_failure()
            common = {"runId": failed["runId"], "controlFile": submitted["controlFile"]}
            # The Host edits the failed checkout inside the authorized scope before
            # concluding: the conclusion must seal these changes as its own
            # independent partial output without overwriting the Worker's earlier
            # fixed partial of the same attempt.
            checkout = Path(failed["workspace"]["path"])
            (checkout / "tracked.txt").write_text("host review notes after the failure\n")
            worker_partial_manifest = partial["manifestSha256"]
            concluded = self.cli("conclude", json.dumps({
                **common,
                "note": "Incomplete work is retained in the partial artifact; no further execution.",
            }), env=self.env())[1]
            self.assertEqual(concluded["status"], "failed")
            self.assertEqual(concluded["verdict"], "concluded")
            self.assertIsNotNone(concluded["hostConclusion"])
            self.assertTrue(concluded["reclaim"]["removed"], concluded["reclaim"])
            self.assertFalse(checkout.exists())
            # The Host seal is a new artifact with its own binding; the Worker's
            # partial artifact, ref and manifest stay exactly as they were.
            self.assertNotEqual(concluded["partialArtifactId"], partial["artifactId"])
            self.assertTrue(Path(partial["diffPath"]).is_file())
            self.assertTrue(Path(partial["cumulativePatch"]["path"]).is_file())
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
            "adapter": "codex", "provider": "openai", "displayName": "Offline Codex",
            "packageName": "fixture", "packageVersion": "fixture", "efforts": ["low", "high"],
            "models": [{"id": "fixture-model", "name": "Offline model", "inputModalities": ["text"]}],
        }]})
        (self.directory / "codex-home").mkdir()
        (self.directory / "codex-home/config.toml").write_text("")

    def env(self):
        return {**super().env(), "CODEX_HOME": str(self.directory / "codex-home"),
                "BUDDY_CODEX_CLI": str(Path(__file__).parent / "fixtures/mock_quota_codex.py"),
                "BUDDY_CODEX_FIXTURE_STATE": str(self.directory / "codex-native.json"),
                "BUDDY_CODEX_FIXTURE_CASE": "quota-failure"}

    def test_failed_codex_reconstructs_in_dsh_with_bound_partial_output_and_message(self):
        with self.daemon(env=self.env()):
            submitted = self.call("submit", {"adapter": "codex", "provider": "openai", "model": "fixture-model", "effort": "low",
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
                "configuration": {**CONFIGURATION, "effort": "high"}, "reason": "Recover with an enabled different harness",
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


def load_tests(loader, _tests, _pattern):
    import unittest
    suite = unittest.TestSuite()
    for cls in (HostQuotaRecoveryTests, CrossHarnessQuotaRecoveryTests):
        suite.addTests(cls(name) for name in cls.__dict__ if name.startswith("test_"))
    return suite
