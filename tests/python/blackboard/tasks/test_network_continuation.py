"""A private offline DSH CLI network error retains work for explicit continuation."""
from __future__ import annotations

import json

from blackboard.tasks.test_workflow_worker import BUDDY, MicroTaskWorkerTestCase


class NetworkErrorContinuationTests(MicroTaskWorkerTestCase):
    def setUp(self):
        super().setUp()
        from hey_my_buddy.blackboard.store.store import BoardStore
        from support import enable_fixture_buddy
        store = BoardStore(self.directory)
        store.initialize()
        enable_fixture_buddy(store, BUDDY)

    def test_native_network_error_ends_turn_then_explicit_continue_finishes(self):
        with self.daemon(env=self.env()):
            code, submitted = self.cli("submit", json.dumps({
                **BUDDY, "requestId": "network-recovery", "hostId": "host-1",
                "task": "Exercise the offline network-error fixture", "cwd": str(self.repo),
                "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["."]},
            }), env=self.env())
            self.assertEqual(code, 0, submitted)
            self.cli("await", json.dumps({"runId": submitted["runId"], "waitSeconds": 25}),
                     env=self.env(), timeout=45)
            code, failed = self.cli("get", json.dumps({"runId": submitted["runId"], "output": "full"}), env=self.env())
            self.assertEqual(code, 0, failed)
            self.assertEqual(failed["state"], "failed")
            records = list(self.directory.rglob("dsh-session-record.json"))
            self.assertEqual(len(records), 1, {"failed": failed,
                                               "result": self.cli("result", json.dumps({"runId": submitted["runId"]}),
                                                                  env=self.env())})
            self.assertEqual(json.loads(records[0].read_text())["failure"]["code"], "NETWORK_UNAVAILABLE")
            partial = next(item for item in failed["artifacts"] if item["kind"] == "partial-output")
            self.assertFalse(partial["verified"])
            code, continued = self.cli("continue", json.dumps({
                "runId": submitted["runId"], "commandId": "continue-after-network",
                "expectedRevision": failed["revision"], "controlFile": submitted["controlFile"],
                "input": "Finish and verify the saved edit after the connection recovers.",
            }), env=self.env())
            self.assertEqual(code, 0, continued)
            self.cli("await", json.dumps({"runId": submitted["runId"], "waitSeconds": 90}),
                     env=self.env(), timeout=110)
            code, delivered = self.cli("get", json.dumps({"runId": submitted["runId"],
                                                           "includeAudit": True, "output": "full"}), env=self.env())
            self.assertEqual(code, 0, delivered)
            self.assertEqual(delivered["state"], "delivered")
            turn = delivered["audit"]["turns"][-1]
            self.assertEqual(turn["input"]["executionWorkspace"]["inputCommit"], partial["outputCommit"])
            self.assertIn("connection dropped", turn["input"]["context"]["lastAssistantMessage"]["text"])
