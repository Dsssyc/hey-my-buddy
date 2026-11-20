"""Lead stop fixtures keep the service's execution and actor evidence separate."""
from __future__ import annotations

import importlib.util
import http.client
import json
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from hey_my_buddy.blackboard.tasks.macro_tasks import HOST_MARKERS
from hey_my_buddy.protocol.session_content import SessionNotification


PROBE = Path(__file__).resolve().parents[3] / "tests/probes/macro_task_console_preview.py"
SPEC = importlib.util.spec_from_file_location("console_stop_preview", PROBE)
preview = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preview
SPEC.loader.exec_module(preview)


class LeadStopPreviewTests(unittest.TestCase):
    def test_dense_outline_preview_keeps_the_large_tree_shape_and_original_examples(self):
        original = preview.console_snapshot('normal', True)
        dense = preview.console_snapshot('domains', True)
        nodes = dense['domainNodes']
        children = {}
        for node in nodes:
            children.setdefault(node['parentId'], []).append(node)
        roots = children[None]
        def total(node):
            return node['directCount'] + sum(total(child) for child in children.get(node['nodeId'], []))
        self.assertEqual(len(nodes), 81)
        self.assertEqual(len(roots), 41)
        self.assertEqual([total(node) for node in roots],
                         [139, 103, 89, 75, 74, 67, 51, 44, 41, 37, 28, 24, 20, 18,
                          17, 17, 16, 15, 15, 14, 14, 13, 12, 12, 10, 10, 8, 8, 7, 6,
                          5, 5, 3, 3, 3, 3, 2, 1, 1, 1, 1])
        self.assertEqual(sum(node['nodeId'] in children for node in roots), 14)
        self.assertEqual(max(len(children.get(node['nodeId'], [])) for node in roots), 6)
        second_level = [child for root in roots for child in children.get(root['nodeId'], [])]
        third_level_groups = [children[node['nodeId']] for node in second_level if node['nodeId'] in children]
        self.assertEqual([len(group) for group in third_level_groups], [5])
        self.assertTrue(all(node['nodeId'] not in children for node in third_level_groups[0]))
        self.assertTrue(all(node['directCount'] >= 0 and node['mergedIntoId'] is None for node in nodes))
        self.assertEqual(original['kindEvaluations'], dense['kindEvaluations'])
        self.assertTrue(any(node['mergedIntoId'] for node in original['domainNodes']))
        self.assertIn('domains', preview.SCENARIOS)

    def test_catalogue_redo_examples_keep_missing_measurements_and_declared_versions(self):
        snapshot = preview.console_snapshot('normal', True)
        services = {(row['harness'], row['provider'], row['model']) for row in snapshot['modelServiceConcurrency']}
        self.assertNotIn(('codex', 'openai', 'gpt-5-mini'), services)
        self.assertIn(('codex', 'openai', 'gpt-5-mini'),
                      {(row['harness'], row['provider'], row['model']) for row in snapshot['buddies']})
        harnesses = {row['harness']: row for row in snapshot['harnesses']}
        self.assertEqual(harnesses['dsh']['permissionsVerifiedVersion'], '0.2.2-rc.1')
        self.assertIsNone(harnesses['zcode']['permissionsVerifiedVersion'])
        self.assertNotIn('web', harnesses['zcode'].get('permissions', {}))

    def test_model_profile_preview_uses_current_snapshot_and_command_shapes(self):
        snapshot = preview.console_snapshot('normal', True)
        self.assertNotIn('modelServiceNotes', snapshot)
        self.assertEqual(snapshot['configuration']['modelResponseWaitSeconds'], 60)
        self.assertFalse({'routingBudget', 'profileBudget', 'routingBudgetLimits'}
                         & snapshot['configuration'].keys())
        self.assertIn(snapshot['configuration']['profileBuddyId'],
                      {buddy['buddyId'] for buddy in snapshot['buddies']})
        self.assertEqual(snapshot['configuration']['profileUnavailableReason'],
                         {'code': 'profile-web-unsupported', 'params': {}})
        models = {row['modelId']: row for row in snapshot['models']}
        self.assertEqual(len(models), len(preview.MODEL_IDS))
        self.assertEqual(set(row['source'] for row in snapshot['modelCorrespondences']),
                         {'same-name', 'profile', 'user'})
        for row in snapshot['modelCorrespondences']:
            self.assertIn(row['modelId'], models)
            self.assertIn({key: row[key] for key in ('harness', 'provider', 'model')},
                          models[row['modelId']]['names'])
            if row['source'] == 'profile':
                self.assertTrue(row['evidenceUrl'].startswith('https://'))
                self.assertIsNotNone(row['evidenceDate'])
                self.assertTrue(row['canUndo'])
        self.assertNotIn('modelNotes', snapshot)
        profiled = models[preview.MODEL_IDS['gpt-5-codex']]
        self.assertEqual({claim['polarity'] for claim in profiled['profile']['claims']},
                         {'strength', 'weakness'})
        self.assertEqual(profiled['profile']['profileRunId'], profiled['latestProfileRun']['profileRunId'])
        self.assertEqual(profiled['latestProfileRun']['state'], 'completed')
        self.assertEqual(profiled['latestProfileRun']['usage']['attempts'][0]['tokenUsage']['inputTokens'], 4200)
        self.assertEqual(profiled['latestProfileRun']['toolSummary'], [
            {'toolName': 'web_search', 'calls': 2, 'failed': 0, 'unknown': 1},
            {'toolName': 'web_fetch', 'calls': 2, 'failed': 2, 'unknown': 0},
            {'toolName': None, 'calls': 1, 'failed': 0, 'unknown': 1},
        ])
        unprofiled = models[preview.MODEL_IDS['glm-5']]
        self.assertIsNone(unprofiled['profile'])
        self.assertEqual(unprofiled['latestProfileRun']['state'], 'failed')
        self.assertEqual(unprofiled['latestProfileRun']['toolSummary'], [
            {'toolName': 'web_search', 'calls': 6, 'failed': 0, 'unknown': 0},
            {'toolName': 'web_fetch', 'calls': 8, 'failed': 8, 'unknown': 0},
        ])
        self.assertEqual(unprofiled['latestProfileRun']['reason']['code'], 'profile-answer-invalid')
        self.assertEqual(models[preview.MODEL_IDS['gpt-5-mini']]['latestProfileRun']['state'], 'running')
        self.assertIsNone(models[preview.MODEL_IDS['gpt-5-mini']]['latestProfileRun']['finishedAt'])
        page = preview.command_result('normal', 'buddy_list', {})
        for field in ('models', 'modelCorrespondences'):
            self.assertEqual(page[field], snapshot[field])
        self.assertEqual(preview.command_result('normal', 'profile_start',
                         {'modelId': profiled['modelId']}),
                         {'status': 'unavailable', 'reason': snapshot['configuration']['profileUnavailableReason']})
        with self.assertRaises(preview.PreviewError) as missing:
            preview.command_result('normal', 'profile_start', {'modelId': 'missing'})
        self.assertEqual(missing.exception.code, 'NOT_FOUND')

    def test_kind_evaluations_come_from_the_service_projection_of_synthetic_rows(self):
        from unittest.mock import patch
        from hey_my_buddy.console.reads import _kind_evaluations

        with patch('hey_my_buddy.console.reads._kind_evaluations', wraps=_kind_evaluations) as read:
            snapshot = preview.console_snapshot('normal', True)
        read.assert_called_once()
        rows = snapshot['kindEvaluations']
        flash = next(row for row in rows if (row['harness'], row['modelId'], row['effort'])
                     == ('dsh', preview.MODEL_IDS['deepseek-flash'], 'max'))
        self.assertNotIn('model', flash)
        self.assertEqual(flash['modelDisplayName'], 'deepseek-flash')
        self.assertEqual({name['provider'] for name in flash['modelNames']},
                         {'deepseek-official', 'deepseek-partner'})
        self.assertEqual(flash['statistics']['overall']['samples'], 4)
        self.assertEqual(flash['statistics']['byDomain']['preview-domain-ui']['samples'], 4)
        self.assertEqual(flash['statistics']['byDomainTaskType']['preview-domain-routing']['verify']['samples'], 1)
        self.assertEqual(flash['summary'], {'text': '合成样例：界面改造有过一次返工，路由验证一次通过。'})
        self.assertEqual(flash['statistics']['infrastructureFailures'][0]['provider'], 'deepseek-official')
        codex = next(row for row in rows if (row['harness'], row['modelId'], row['effort'])
                     == ('codex', preview.MODEL_IDS['gpt-5-codex'], 'high'))
        self.assertEqual(set(codex['statistics']['byDomain']),
                         {'preview-domain-ui', 'preview-domain-console', 'preview-domain-tokens'})
        current_name = next(row for row in snapshot['modelCorrespondences']
                            if (row['harness'], row['provider'], row['model'])
                            == ('codex', 'openai', 'gpt-5-codex'))
        self.assertEqual(codex['previousGroups'], [{
            'modelId': 'synthetic-previous-codex', 'modelDisplayName': 'Earlier Codex',
            'names': [{'harness': 'codex', 'provider': 'openai', 'model': 'gpt-5-codex',
                       'since': current_name['createdAt']}],
        }])
        # Kinds without samples still use the service's empty-statistics projection.
        empty = next(row for row in rows if row['modelId'] == preview.MODEL_IDS['gpt-5-mini']
                     and row['effort'] == 'low')
        self.assertEqual(empty['statistics']['overall']['samples'], 0)
        self.assertIsNone(empty['summary'])

    def test_router_preview_records_response_wait_without_a_total_budget(self):
        route = preview.ROUTING_A1
        self.assertEqual(route['modelResponseWaitSeconds'], 60)
        self.assertNotIn('budget', route)
        task = preview.task_view(preview.FIXTURES['runs']['preview-run-a1-router'])
        self.assertEqual(task['spec']['modelResponseWaitSeconds'], 60)
        self.assertNotIn('routerBudgetSeconds', task['spec'])
        self.assertNotIn('modelResponseWaitSeconds', task['spec']['decision'])

    def test_unconcluded_failures_and_cancellations_wait_for_host(self):
        page = preview.macro_task_page({'filter': 'host'})
        summary = next(item for group in page['groups'] for item in group['macroTasks']
                       if item['macroTaskId'] == preview.MACRO_C)
        self.assertEqual(summary['state'], 'host')
        self.assertEqual({key: summary['counts'][key] for key in ('host', 'failed', 'ended', 'accepted')},
                         {'host': 2, 'failed': 2, 'ended': 5, 'accepted': 2})
        filtered = preview.macro_task_timeline({'macroTaskId': preview.MACRO_C, 'filter': 'host'}, 'normal')
        self.assertEqual({row['runId'] for row in filtered['rows']}, {'preview-run-d2', 'preview-run-d3'})
        self.assertEqual({row['category'] for row in filtered['rows']}, {'host'})
        for run_id in ('preview-run-d2', 'preview-run-d3'):
            self.assertIsNone(preview.workflow_get({'runId': run_id})['hostConclusion'])
        concluded = preview.workflow_get({'runId': 'preview-run-d4'})
        self.assertEqual(concluded['state'], 'concluded')
        self.assertEqual(concluded['hostConclusion']['executionStatus'], 'failed')
        self.assertEqual(preview.pending_root_count(), 7)
        for run_id in ('preview-run-d5-h1', 'preview-run-d5-h2'):
            self.assertEqual(preview.FIXTURES['runs'][run_id]['category'], 'ended')
            self.assertFalse(preview.matches(preview.FIXTURES['runs'][run_id], {'filter': 'active'}))
        self.assertNotIn('preview-run-b6', {row['runId'] for row in preview.macro_task_timeline(
            {'macroTaskId': preview.MACRO_B, 'filter': 'host'}, 'normal')['rows']})

    def test_input_rejections_retain_the_same_frozen_facts_in_every_projection(self):
        rows = preview.macro_task_timeline({'macroTaskId': preview.MACRO_INPUT}, 'normal')['rows']
        self.assertEqual({row['runId'] for row in rows},
                         {'preview-run-input-preflight', 'preview-run-input-native'})
        for suffix, stage in (('input-preflight', 'preflight'), ('input-native', 'native-admission')):
            run_id = f'preview-run-{suffix}'
            entry = preview.FIXTURES['runs'][run_id]
            view = preview.task_view(entry)
            failure = view['inputFailure']
            self.assertEqual(failure['stage'], stage)
            self.assertEqual(view['workflow']['requestReason'], {'code': 'workflow.input-rejected',
                                                               'params': {'harness': failure['harness']['name']}})
            record = preview.workflow_get({'runId': run_id, 'includeAudit': True})
            for item in (record['turns'][0], record['currentTurn'], record['activeRequest'],
                         record['requests'][0], record['audit']['turns'][0], record['audit']['requests'][0]):
                self.assertEqual(item['inputFailure'], failure)
            self.assertEqual(record['state'], 'awaiting-host')
            self.assertEqual(record['macroTaskId'], preview.MACRO_INPUT)
            self.assertEqual(entry['group'], preview.MACRO_INPUT)

    def test_preview_root_stop_states_match_the_board_host_filter(self):
        from hey_my_buddy.blackboard.tasks.macro_tasks import _state
        for owned in (False, True):
            for run_state in ('awaiting-host', 'failed', 'cancelled'):
                for status in ('queued', 'running', 'cancelling', 'reconciliation-needed', 'failed', 'cancelled'):
                    root = preview.run('matrix-root', project='alpha', title='root', title_source='title', task_text='root',
                                       created=preview.instant(1, 0), updated=preview.instant(1, 1), state=run_state,
                                       status=status, category='ended', buddy=None,
                                       lead_owner=('lead', 'lead-turn') if owned else None)
                    entry = preview.index_runs([root])['runs']['matrix-root']
                    self.assertEqual(entry['lineage']['rootLeadRunId'], 'lead' if owned else None)
                    expected = _state({'run_state': run_state, 'state': status,
                                       'root_lead_run_id': 'lead' if owned else None,
                                       'accepted_at': None, 'decision_id': None, 'adapter': 'codex',
                                       'micro_task_run_id': entry['runId'], 'review_ready': 0})
                    self.assertEqual(entry['category'], expected, (owned, run_state, status))

    def test_lead_stop_is_attributed_in_the_execution_event_and_cancellation(self):
        view = preview.workflow_get({"runId": "preview-run-b6"})
        task = view["task"]
        actor = {"role": "lead", "id": "preview-run-b3"}
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
        self.assertEqual("host", timeline["macroTask"]["state"])
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

    def test_timeline_response_clock_does_not_advance_with_server_uptime(self):
        for request in range(2):
            if request:
                # Cross the HTTP Date's one-second resolution without restarting
                # the preview: a later browser shard must see the same clock.
                time.sleep(1.1)
            connection = http.client.HTTPConnection(*self.server.server_address, timeout=10)
            try:
                connection.request("GET", f"/api/macro-tasks/{preview.MACRO_A}/timeline")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                served = parsedate_to_datetime(response.getheader("Date"))
                timeline = json.loads(response.read())
                self.assertEqual(served, datetime.fromisoformat(timeline["observedAt"].replace("Z", "+00:00")))
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
        self.assertEqual({"status": "unavailable", "reason": {"code": "session.not-recorded", "params": {}}}, latest["coverage"]["reasoning"])
        self.assertEqual({"status": "evidenced", "version": "fixture CLI 1"}, latest["coverage"]["messages"])
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
