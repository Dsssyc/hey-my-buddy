"""Lead stop fixtures keep the service's execution and actor evidence separate."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


PROBE = Path(__file__).resolve().parents[3] / "tests/probes/macro_task_console_preview.py"
SPEC = importlib.util.spec_from_file_location("console_stop_preview", PROBE)
preview = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preview
SPEC.loader.exec_module(preview)


class LeadStopPreviewTests(unittest.TestCase):
    def test_lead_stop_is_attributed_in_the_execution_event_and_cancellation(self):
        view = preview.workflow_get({"runId": "preview-run-b6"})
        task = view["task"]
        actor = {"role": "lead", "id": "preview-run-b3"}
        self.assertIsNone(view["hostId"])
        self.assertEqual(actor["id"], task["lineage"]["leadOwnership"]["leadRunId"])
        self.assertEqual("cancelled", view["state"])
        self.assertEqual("cancelled", task["status"])
        self.assertIsNone(view["finalAttemptId"])
        self.assertEqual("lead", task["stopInitiator"])
        self.assertEqual(actor, task["cancellation"]["actor"])
        attempt = task["selectedAttempt"]
        self.assertEqual("lead", attempt["stopInitiator"])
        self.assertTrue(attempt["cancelRequested"])
        self.assertEqual(preview.instant(7, 18, 30), attempt["cancelRequestedAt"])
        self.assertLess(attempt["cancelRequestedAt"], attempt["finishedAt"])
        self.assertLess(attempt["finishedAt"], attempt["graceDeadline"])
        self.assertTrue(attempt["shutdownConfirmed"])
        self.assertEqual("cancelled", attempt["result"]["status"])
        self.assertEqual("wrap-up", attempt["result"]["terminationReason"])
        timeline = preview.macro_task_timeline({"macroTaskId": preview.MACRO_B}, "normal")
        events = [event for event in timeline["events"] if event["runId"] == view["runId"] and event["kind"] == "cancel"]
        self.assertEqual(1, len(events))
        self.assertEqual(actor, events[0]["actor"])
        self.assertEqual("lead", events[0]["stopInitiator"])
        self.assertEqual(attempt["cancelRequestedAt"], events[0]["at"])

    def test_an_unconfirmed_owned_stop_keeps_the_lead_shutdown_open(self):
        lead = preview.workflow_get({"runId": "preview-run-b7"})
        owned = preview.workflow_get({"runId": "preview-run-b8"})
        attempt = owned["task"]["selectedAttempt"]
        self.assertEqual(lead["runId"], owned["task"]["lineage"]["leadOwnership"]["leadRunId"])
        self.assertEqual("turn-att-b7-1", owned["task"]["lineage"]["leadOwnership"]["leadTurnId"])
        self.assertEqual("cancelled", owned["state"])
        self.assertEqual("cancelling", owned["task"]["status"])
        self.assertEqual("executing", attempt["executionState"])
        self.assertEqual("lead", attempt["stopInitiator"])
        self.assertTrue(attempt["cancelRequested"])
        self.assertFalse(attempt["shutdownConfirmed"])
        self.assertIsNone(attempt["finishedAt"])
        self.assertGreater(attempt["graceDeadline"], preview.OBSERVED_AT)
        self.assertEqual({"role": "lead", "id": lead["runId"]}, owned["task"]["cancellation"]["actor"])
        self.assertEqual({"selfConfirmed": True, "descendantsConfirmed": False,
                          "unconfirmedRunIds": [owned["runId"]], "unconfirmedCount": 1, "truncated": False},
                         lead["shutdown"])
        self.assertEqual(lead["shutdown"], lead["task"]["workflowShutdown"])
        self.assertIsNone(lead["finalAttemptId"])
        timeline = preview.macro_task_timeline({"macroTaskId": preview.MACRO_B}, "normal")
        row = next(row for row in timeline["rows"] if row["runId"] == owned["runId"])
        self.assertEqual(("cancelled", "cancelling", "active", False),
                         (row["state"], row["status"], row["category"], row["shutdownConfirmed"]))
        self.assertEqual("active", timeline["macroTask"]["state"])
        events = [event for event in timeline["events"] if event["runId"] == owned["runId"] and event["kind"] == "cancel"]
        self.assertEqual(["lead", "host"], [event["actor"]["role"] for event in events])
        self.assertEqual(attempt["cancelRequestedAt"], events[0]["at"])
        self.assertGreater(events[1]["at"], attempt["cancelRequestedAt"])
        files = preview.fixture_payloads()
        for run_id in ("preview-run-b6", "preview-run-b7", "preview-run-b8"):
            self.assertIn(f"normal/workflow-{run_id}.json", files)
