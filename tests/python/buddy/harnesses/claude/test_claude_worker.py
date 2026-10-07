"""Claude role wiring through the real private daemon, Worker and Git seal.

Only the native CLI is simulated. No installed harness or model is contacted.
The daemon-level cases need the Host activation patch that registers the
claude run seam; the catalog rule is pure and runs anywhere.
"""
import json
import unittest
from pathlib import Path

from hey_my_buddy.buddy.harnesses.claude.native_run import _catalog
from hey_my_buddy.buddy.harnesses.registry import adapter
from hey_my_buddy.protocol import schemas
from support import wait_for
from blackboard.tasks.test_workflow_worker import GovernedWorkerTestCase


CONFIGURATION = {"adapter": "claude", "provider": "anthropic",
                 "model": "claude-opus-5-5[1m]", "effort": "high"}
FIXTURE = Path(__file__).parent / "fixtures/fake_claude.py"


def _claude_registered() -> bool:
    from hey_my_buddy.buddy.harnesses.registry import run_seam
    return run_seam("claude") is not None


@unittest.skipUnless(_claude_registered(),
                     "the claude run seam is registered only in the activated verification copy")
class ClaudeWorkerTests(GovernedWorkerTestCase):
    def setUp(self):
        super().setUp()
        self.catalog_fixture({"source": "file:claude-worker-fixture", "providers": [{
            "adapter": "claude", "provider": "anthropic", "displayName": "Anthropic",
            "packageName": "claude-code", "packageVersion": "fixture",
            "models": [{"id": CONFIGURATION["model"], "name": "Opus fixture",
                        "efforts": ["high"], "inputModalities": ["text"]}]}], "warnings": []})
        FIXTURE.chmod(0o755)

    def env(self):
        return {"BUDDY_CLAUDE_CLI": str(FIXTURE), "BUDDY_CLAUDE_SETTINGS_POLICY": "isolated",
                "BUDDY_CLAUDE_FIXTURE_STATE": str(self.directory / "native-fixture.json"),
                "BUDDY_CLAUDE_FIXTURE_CASE": getattr(self, "case", "ok")}

    def execute_fixture(self):
        code, submission = self.cli("submit", json.dumps({
            **CONFIGURATION, "requestId": "claude-private-worker", "hostId": "host-1",
            "task": "Exercise only the mock native protocol", "cwd": str(self.repo),
            "timeoutSeconds": 20,
            "executionWorkspace": {"kind": "worktree", "access": "write",
                                   "base": {"kind": "commit", "ref": self.git("rev-parse", "HEAD").strip()},
                                   "writeScope": ["tracked.txt"]}}), env=self.env())
        self.assertEqual(code, 0, submission)
        run_id = submission["runId"]
        last = {}

        def settled():
            nonlocal last
            # These tests inspect the complete evidence, not the brief Host view.
            code, last = self.cli("get", json.dumps({"runId": run_id, "output": "full"}), env=self.env())
            self.assertEqual(code, 0, last)
            task = last.get("task") or {}
            return last if task.get("resultAvailable") and task.get("shutdownConfirmed") else None

        view = wait_for(settled, timeout=60)
        self.assertIsNotNone(view, last)
        code, receipt = self.cli("result", json.dumps({"runId": run_id, "output": "full"}), env=self.env())
        self.assertEqual(code, 0, receipt)
        return view, receipt

    def test_governed_worker_seals_only_a_proven_claude_result(self):
        self.assertIn("claude", schemas.CODING_ADAPTERS)
        implementation = adapter("claude")
        self.assertFalse(implementation.native_resume)
        self.assertNotIn("inquiry", implementation.capabilities)
        with self.daemon(env=self.env()):
            view, receipt = self.execute_fixture()
            self.assertEqual(view["state"], "delivered", receipt)
            self.assertIsNotNone(view["finalArtifactId"])
            native = receipt["result"]["nativeSession"]
            self.assertFalse(native["resumable"])
            self.assertEqual(native["storageScope"], "harness-user-store")
            self.assertEqual(receipt["result"]["turn"]["provenance"]["adapter"], "claude")
            self.assertEqual((self.repo / "tracked.txt").read_text(), "base\n")

    def test_quota_failure_is_persisted_as_harness_error_without_a_capability_sample(self):
        self.case = "quota-rejected"
        with self.daemon(env=self.env()):
            view, receipt = self.execute_fixture()
            self.assertNotEqual(view["state"], "delivered")
            self.assertIsNone(view["finalArtifactId"])
            meta, result = receipt["resultMeta"], receipt["result"]
            self.assertEqual(meta["terminationReason"], "harness-error", receipt)
            self.assertEqual(result["code"], "quota-rejected")
            self.assertNotIn("turn", result)
            self.assertNotIn("workspaceSeal", result)
            self.assertEqual(view["counts"]["turns"], 1, "No automatic retry is allowed")
            self.assertEqual(set(result["quotaFailure"]), {"rateLimitType", "resetsAt"})
            # Exercise the actual shared evaluator with the persisted failure,
            # not a model's prose or a fabricated quota marker.
            from hey_my_buddy.blackboard.evaluation.evaluation import EvaluationStore
            from hey_my_buddy.blackboard.store.store import BoardStore
            evaluator = EvaluationStore(BoardStore(self.directory))
            counted, basis = evaluator._sample_basis({
                "taskState": view["task"]["state"], "shutdownConfirmed": meta["shutdownConfirmed"],
                "resultStatus": meta["status"], "error": meta["error"]},
                CONFIGURATION, source="host-rejection")
            self.assertFalse(counted)
            self.assertEqual(basis["excluded"], "infrastructure")


class ClaudeCatalogRuleTests(unittest.TestCase):
    def test_effort_directory_never_mixes_default_with_explicit_efforts(self):
        catalog, _ = _catalog({"account": {"apiProvider": "firstParty", "tokenSource": "subscription"},
            "models": [{"value": "bad", "resolvedModel": "bad-model", "supportedEffortLevels": ["default", "high"]},
                       {"value": "haiku", "resolvedModel": "haiku"}]}, "fixture")
        self.assertEqual([m["id"] for m in catalog["providers"][0]["models"]], ["haiku"])
        self.assertEqual(catalog["providers"][0]["models"][0]["efforts"], ["default"])
        self.assertTrue(catalog["warnings"])
