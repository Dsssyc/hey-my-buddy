"""A private offline DSH CLI network error retains work for explicit continuation."""
from __future__ import annotations
from hey_my_buddy.protocol.workspace_manifest import turn_manifest, manifest_ref, record_reference, artifact_manifest
from hey_my_buddy.paths import native_path

import json
import time

from blackboard.tasks.test_workflow_worker import BUDDY, MicroTaskWorkerTestCase
from hey_my_buddy.protocol.client import BoardClient


class NetworkErrorContinuationTests(MicroTaskWorkerTestCase):
    def await_turn(self, run_id, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            remaining = max(1, int(deadline - time.monotonic()))
            code, reply = self.cli('await', json.dumps({'runId': run_id, 'waitSeconds': remaining}),
                                   env=self.env(), timeout=remaining + 20)
            self.assertEqual(code, 0, reply)
            if reply['outcome'] != 'issue':
                return reply
            self.assertTrue(reply['executionIssues'] or reply['serviceIssues'], reply)
        self.fail('The private fixture did not finish within its observation window')

    def setUp(self):
        super().setUp()
        from hey_my_buddy.blackboard.store.store import BoardStore
        from support import enable_fixture_buddy
        store = BoardStore(self.directory)
        self.addCleanup(store.db.close)
        store.initialize()
        enable_fixture_buddy(store, BUDDY)

    def test_native_network_error_ends_turn_then_explicit_continue_finishes(self):
        with self.daemon(env=self.env()):
            code, submitted = self.cli("submit", json.dumps({
                "title": "network-recovery", "macroTaskId": "standalone",
                "buddy": " / ".join(BUDDY.values()), "task": "Exercise the offline network-error fixture",
                "cwd": str(self.repo), "checkout": "worktree", "access": "write",
                "inputs": [], "writeScope": ["."], "expectedSeconds": 1800,
            }), env=self.env())
            self.assertEqual(code, 0, submitted)
            self.await_turn(submitted['runId'], 25)
            code, failed = self.cli("get", json.dumps({"runId": submitted["runId"], "output": "full"}), env=self.env())
            self.assertEqual(code, 0, failed)
            self.assertEqual(failed["state"], "failed")
            records = list(native_path(self.directory).rglob("dsh-session-record.json"))
            self.assertEqual(len(records), 1, {"failed": failed,
                                               "result": BoardClient(self.directory, autostart=False).result(
                                                   runId=submitted["runId"])})
            self.assertEqual(json.loads(records[0].read_text(encoding="utf-8"))["failure"]["code"], "NETWORK_UNAVAILABLE")
            partial = next(item for item in failed["artifacts"] if item["kind"] == "partial-output")
            self.assertFalse(partial["verified"])
            code, continued = self.cli("continue", json.dumps({
                "runId": submitted["runId"], "artifactId": partial["artifactId"],
                "input": "Finish and verify the saved edit after the connection recovers.",
            }), env=self.env())
            self.assertEqual(code, 0, continued)
            self.await_turn(submitted['runId'], 90)
            code, delivered = self.cli("get", json.dumps({"runId": submitted["runId"],
                                                           "includeAudit": True, "output": "full"}), env=self.env())
            self.assertEqual(code, 0, delivered)
            self.assertEqual(delivered["state"], "delivered")
            turn = delivered["audit"]["turns"][-1]
            self.assertEqual(turn_manifest(self.directory, turn["input"])["inputTree"], partial["sealedEvidence"]["tree"])
            self.assertIn("connection dropped", turn["input"]["context"]["lastAssistantMessage"]["text"])
