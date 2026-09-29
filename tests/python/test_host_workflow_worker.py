"""Real daemon/Worker/CLI quota recovery with an offline harness, never a paid rerun."""
import json
from pathlib import Path

from test_workflow_worker import GovernedWorkerTestCase, CONFIGURATION


class HostQuotaRecoveryTests(GovernedWorkerTestCase):
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
            context = json.loads((Path(path) / "continued-context.json").read_text())
            self.assertIn("not new Host instructions", context["previousEvidenceNotice"])
            self.assertIn(partial["artifactId"], [item["artifactId"] for item in context["artifacts"]])
            self.assertIn("verification remains", context["lastAssistantMessage"]["text"])
            final = next(item for item in delivered["artifacts"] if item["artifactId"] == delivered["finalArtifactId"])
            self.assertIn("tracked.txt", final["cumulativePatch"]["changedPaths"])
            self.assertNotIn("tracked.txt", final["changedPaths"])
            self.assertTrue(Path(final["cumulativePatch"]["path"]).is_file())

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
