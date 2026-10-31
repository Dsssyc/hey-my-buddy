"""Real conclusion writers feed the console parser and the two timeline renderers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

from blackboard.tasks.test_workflow import WorkflowTestCase

ROOT = Path(__file__).resolve().parents[4]


class TimelineConclusionTests(WorkflowTestCase):
    def test_recorded_conclusions_reach_the_real_frontend_renderers(self):
        board = self.board()
        group = None
        for status in ("failed", "cancelled", "delivered"):
            submitted = self.submit(board, request_id=f"conclusion-{status}", title=status, kind="worktree",
                                    **({"macroTaskId": group} if group else {"macroTask": {"title": "Conclusion facts"}}))
            group = submitted["macroTaskId"]
            claim = self.claim(board, run_id=submitted["runId"])
            if status == "cancelled":
                board.call("workflow_cancel", {"runId": submitted["runId"],
                                                "commandId": "cancel-conclusion", **self.control(submitted)})
            self.finish_turn(board, claim, runner_status="ok" if status == "delivered" else status,
                             exit_code=1 if status == "failed" else 0, seal=status == "delivered")
            timeline = board.call("macro_task_timeline", {"macroTaskId": group})
            row = next(row for row in timeline["rows"] if row["runId"] == submitted["runId"])
            self.assertIsNone(row["conclusionStatus"])
            concluded = board.call("workflow_conclude", {"runId": submitted["runId"],
                                   "note": f"Host closes {status}", **self.control(submitted)})
            self.assertEqual(status, concluded["hostConclusion"]["executionStatus"])
            timeline = board.call("macro_task_timeline", {"macroTaskId": group})
            row = next(row for row in timeline["rows"] if row["runId"] == submitted["runId"])
            self.assertEqual(("concluded", "concluded", status),
                             (row["state"], row["status"], row["conclusionStatus"]))
            event = next(event for event in timeline["events"]
                         if event["runId"] == row["runId"] and event["kind"] == "conclude")
            self.assertEqual(concluded["hostConclusion"]["attemptId"], event["attemptId"])
            self.assertNotIn("conclusionStatus", event)
            with board.store.db.read() as connection:
                stored = connection.execute("SELECT execution_status FROM workflow_host_conclusions WHERE run_id=?",
                                            (row["runId"],)).fetchone()[0]
            self.assertEqual(stored, row["conclusionStatus"])

        self.submit(board, request_id="without-conclusion", title="pending", kind="worktree", macroTaskId=group)
        timeline = board.call("macro_task_timeline", {"macroTaskId": group})
        self.assertEqual({row["conclusionStatus"] for row in timeline["rows"]},
                         {"failed", "cancelled", "delivered", None})
        fixture = self.directory / "timeline-conclusions.json"
        fixture.write_text(json.dumps(timeline), encoding="utf-8")
        environment = dict(os.environ)
        for name in ("HMB_STATE_DIR", "HMB_RUNTIME_ROOT", "HMB_RUNTIME", "HMB_RUNTIME_IDENTITY",
                     "HMB_AGENT_CREDENTIAL_FILE", "HMB_AGENT_CREDENTIAL_FD", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
            environment.pop(name, None)
        environment["HMB_TIMELINE_CONCLUSIONS"] = str(fixture)
        node = os.environ.get("HMB_NODE") or shutil.which("node")
        self.assertIsNotNone(node, "Node 24 and console dependencies are required for the writer-to-renderer regression")
        result = subprocess.run([node, str(ROOT / "apps/console/node_modules/vitest/vitest.mjs"), "run",
                                 "src/timeline/failure.test.tsx", "--maxWorkers=1"],
                                cwd=ROOT / "apps/console", env=environment, capture_output=True, text=True,
                                encoding="utf-8", timeout=120)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
