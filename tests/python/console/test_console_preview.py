"""Lead stop fixtures keep the service's execution and actor evidence separate."""
from __future__ import annotations

import importlib.util
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from hey_my_buddy.blackboard.tasks.macro_tasks import HOST_MARKERS
from hey_my_buddy.protocol.session_content import SessionNotification


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
        self.assertEqual("cancelled", task["state"])
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
        self.assertEqual("cancelling", owned["task"]["state"])
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
        self.assertEqual(["lead"], [event["actor"]["role"] for event in events])
        self.assertEqual(attempt["cancelRequestedAt"], events[0]["at"])
        self.assertTrue(all(event["eventKind"] in HOST_MARKERS for event in timeline["events"] if event["kind"] == "cancel"))
        lead_stops = [event for event in timeline["events"] if event["runId"] == lead["runId"] and event["kind"] == "cancel"]
        self.assertEqual(["host"], [event["actor"]["role"] for event in lead_stops])
        self.assertGreater(lead_stops[0]["at"], attempt["cancelRequestedAt"])
        files = preview.fixture_payloads()
        for run_id in ("preview-run-b6", "preview-run-b7", "preview-run-b8"):
            self.assertIn(f"normal/workflow-{run_id}.json", files)


class SessionPreviewTests(unittest.TestCase):
    def setUp(self):
        self.assets = tempfile.TemporaryDirectory(prefix="synthetic-session-assets-")
        self.server = preview.PreviewHTTPServer(("127.0.0.1", 0), assets=Path(self.assets.name),
                                                scenario="normal", verbose=False)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assets.cleanup()
        self.assertFalse(self.thread.is_alive())

    def get(self, pair, query=""):
        path = f"/api/tasks/{pair[0]}/attempts/{pair[1]}/session{query}"
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            self.assertEqual("no-store", response.getheader("Cache-Control"))
            self.assertEqual(preview.PREVIEW_VALUE, response.getheader(preview.PREVIEW_HEADER))
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_ended_long_session_pages_by_bytes_and_carries_full_content(self):
        pair = preview.SESSION_LONG
        events = preview.session_events(pair)
        self.assertGreater(len(events), 100)
        for event in events:
            SessionNotification.model_validate(event)
        status, latest = self.get(pair)
        self.assertEqual(200, status)
        self.assertEqual(("ended", 100, True), (latest["state"], len(latest["events"]), latest["hasEarlier"]))
        self.assertEqual([{"code": "session.codex-internal-reasoning-omitted", "params": {}}], latest["omitted"])
        status, earlier = self.get(pair, f'?before={latest["before"]}&limit=100')
        self.assertEqual(200, status)
        self.assertEqual(events, earlier["events"] + latest["events"])
        self.assertFalse(earlier["hasEarlier"])
        self.assertEqual(len(preview.session_line(preview.SESSION_HEADER)), earlier["before"])
        status, empty = self.get(pair, f'?after={latest["after"]}')
        self.assertEqual((200, [], latest["after"]), (status, empty["events"], empty["after"]))
        updates = [event["update"] for event in events]
        self.assertTrue(any(update["sessionUpdate"] == "agent_thought_chunk" for update in updates))
        self.assertTrue(any(update["sessionUpdate"] == "plan" for update in updates))
        self.assertTrue(any(update.get("_meta", {}).get("marker") for update in updates))
        self.assertTrue(any(update.get("_meta", {}).get("replace") for update in updates))
        self.assertGreater(max(len(update.get("content", [{}])[0].get("content", {}).get("text", ""))
                               for update in updates if update["sessionUpdate"] == "tool_call_update"), 10000)
        self.assertEqual(latest["after"], len(preview.session_line(preview.SESSION_HEADER))
                         + sum(len(preview.session_line(event)) for event in events))

    def test_zero_cursor_can_poll_after_an_initial_empty_file(self):
        status, page = self.get(preview.SESSION_LONG, '?after=0&limit=2')
        self.assertEqual(status, 200)
        self.assertEqual(page['events'], preview.session_events(preview.SESSION_LONG)[:2])
        status, page = self.get(preview.SESSION_LONG, '?before=0')
        self.assertEqual((status, page['events'], page['after']), (200, [], 0))

    def test_running_session_appends_on_later_after_polls(self):
        pair = preview.SESSION_RUNNING
        status, initial = self.get(pair)
        self.assertEqual((200, "running"), (status, initial["state"]))
        cursor = initial["after"]
        status, empty = self.get(pair, f"?after={cursor}")
        self.assertEqual((200, [], cursor), (status, empty["events"], empty["after"]))
        status, appended = self.get(pair, f"?after={cursor}")
        self.assertEqual((200, 1, "running"), (status, len(appended["events"]), appended["state"]))
        self.assertGreater(appended["after"], cursor)
        cursor = appended["after"]
        self.assertEqual([], self.get(pair, f"?after={cursor}")[1]["events"])
        status, later = self.get(pair, f"?after={cursor}")
        self.assertEqual((200, 1), (status, len(later["events"])))
        self.assertNotEqual(appended["events"], later["events"])

    def test_router_session_and_explicit_error_codes(self):
        status, router = self.get(preview.SESSION_ROUTER)
        self.assertEqual((200, "ended", "att-router-a1"),
                         (status, router["state"], router["events"][0]["sessionId"]))
        for pair, (expected_status, code, reason_code, _) in preview.SESSION_ERRORS.items():
            with self.subTest(pair=pair):
                status, body = self.get(pair)
                self.assertEqual(expected_status, status)
                self.assertEqual(code, body["error"]["code"])
                self.assertEqual(reason_code, body["error"]["details"]["reason"]["code"])
        for query in ("?before=1&after=2", "?after=1", "?limit=257", "?after=abc", "?after=1&after=2"):
            with self.subTest(query=query):
                status, body = self.get(preview.SESSION_LONG, query)
                self.assertEqual((400, "INVALID_ARGUMENT"), (status, body["error"]["code"]))
        self.assertEqual(404, self.get((preview.SESSION_LONG[0], "unknown-attempt"))[0])
