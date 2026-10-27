"""ACP projection and real fake-agent runs; no native history or model calls."""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from hey_my_buddy.buddy.harnesses.dsh import native_run
from hey_my_buddy.buddy.harnesses.dsh.session_content import DshSessionProjector
from hey_my_buddy.buddy.harnesses.session_record import SessionRecord
from buddy.harnesses.session_record_support import notifications, updates
from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
from hey_my_buddy.buddy.roles.run_observers import worker_observer
from buddy.harnesses.dsh.test_native_run import NativeRunCase
from hey_my_buddy.paths import native_path


class MemoryRecord:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(event.model_dump(by_alias=True, exclude_none=True))


class ProjectorTests(unittest.TestCase):
    def setUp(self):
        self.record = MemoryRecord()
        self.projector = DshSessionProjector(self.record)
        self.projector.start_prompt('root', 'prompt 中文', 'run-1/prompt-1')

    def emit(self, update_kind, *, session='root', **fields):
        self.projector.observe_notification({'method': 'session/update', 'params': {
            'sessionId': session, 'update': {'sessionUpdate': update_kind, **fields}}})

    def text(self, kind, text, **fields):
        self.emit(kind, content={'type': 'text', 'text': text}, **fields)

    def content(self, value):
        return [{'type': 'content', 'content': {'type': 'text', 'text': value}}]

    def test_six_update_kinds_and_consecutive_message_segments(self):
        self.text('user_message_chunk', 'prompt 中文')
        self.text('agent_thought_chunk', '推理一')
        self.text('agent_thought_chunk', '推理二')
        self.text('agent_message_chunk', 'answer')
        self.text('user_message_chunk', 'native follow-up')
        self.emit('tool_call', toolCallId='c1', title='read', kind='other', rawInput={'path': 'a'})
        self.emit('tool_call_update', toolCallId='c1', status='completed', rawOutput={'ok': True},
                  content=self.content('file text'))
        self.emit('plan', entries=[{'content': 'finish', 'status': 'pending', 'priority': 'high'}])
        events = self.record.events
        self.assertEqual({event['sessionUpdate'] for event in events}, {
            'user_message_chunk', 'agent_message_chunk', 'agent_thought_chunk',
            'tool_call', 'tool_call_update', 'plan'})
        thoughts = [event for event in events if event['sessionUpdate'] == 'agent_thought_chunk']
        self.assertEqual(''.join(event['content']['text'] for event in thoughts), '推理一推理二')
        self.assertEqual(thoughts[0]['_meta'], thoughts[1]['_meta'])
        self.assertEqual(thoughts[0]['_meta']['reasoning'], 'text')
        self.assertEqual(events[0]['content']['text'], 'prompt 中文')
        self.assertEqual(sum(event.get('content', {}).get('text') == 'prompt 中文'
                             for event in events if isinstance(event.get('content'), dict)), 1)

    def test_pre_model_user_chunks_are_deferred_and_only_the_exact_echo_is_removed(self):
        self.text('user_message_chunk', 'prompt ')
        self.text('user_message_chunk', '中文')
        self.assertEqual(self.record.events, [])
        self.text('agent_message_chunk', 'started')
        self.assertEqual([event['sessionUpdate'] for event in self.record.events],
                         ['user_message_chunk', 'agent_message_chunk'])
        self.projector.start_prompt('extra', 'sent', 'run-1/prompt-2')
        self.text('user_message_chunk', 'native supplied context', session='extra')
        self.text('agent_message_chunk', 'started', session='extra')
        self.assertIn('native supplied context', json.dumps(self.record.events))

    def test_tool_kinds_use_the_shared_native_name_table(self):
        for index, (title, category) in enumerate((('read', 'read'), ('grep', 'search'),
                ('glob', 'search'), ('bash', 'execute'), ('mcp__server__tool', 'other'),
                ('write', 'edit'), ('edit', 'edit'), ('unknown-' * 1000, 'other'))):
            self.emit('tool_call', toolCallId=str(index), title=title, kind='edit')
            self.assertEqual(self.record.events[-1]['kind'], category)
            self.assertEqual(self.record.events[-1]['title'], title)

    def test_completed_snapshot_dedup_extensions_revisions_and_removed_blocks(self):
        self.emit('tool_call', toolCallId='c', title='bash', content=self.content('ab'))
        self.emit('tool_call_update', toolCallId='c', content=self.content('abc'))
        self.emit('tool_call_update', toolCallId='c', status='completed', content=self.content('abc'))
        completed = len(self.record.events)
        self.emit('tool_call_update', toolCallId='c', status='completed', content=self.content('abc'))
        self.assertEqual(len(self.record.events), completed)
        self.emit('tool_call_update', toolCallId='c', content=self.content('revised'))
        revised = self.record.events[-1]
        self.assertTrue(revised['_meta']['replace'])
        self.assertEqual(revised['content'][0]['content']['text'], 'revised')
        self.emit('tool_call_update', toolCallId='c', content=[])
        self.assertTrue(self.record.events[-1]['_meta']['replace'])
        self.assertEqual(self.record.events[-1]['content'][0]['content']['text'], '')
        chunks = [event['content'][0]['content']['text'] for event in self.record.events
                  if isinstance(event.get('content'), list)]
        self.assertEqual(chunks[:2], ['ab', 'c'])

    def test_completed_only_and_diff_snapshot_preserve_paths(self):
        self.emit('tool_call_update', toolCallId='c', status='completed',
                  content=[{'type': 'diff', 'path': '/workspace/a.py', 'oldText': 'a', 'newText': 'b'}])
        event = self.record.events[-1]
        self.assertTrue(event['_meta']['replace'])
        self.assertIn('/workspace/a.py', event['content'][0]['content']['text'])
        self.emit('tool_call_update', toolCallId='new', content=[
            {'type': 'diff', 'path': '/workspace/new.py', 'oldText': None, 'newText': 'new file'}])
        self.assertIn('new file', self.record.events[-1]['content'][0]['content']['text'])
        self.emit('tool_call', toolCallId='d', title='read', status='completed', rawOutput='full')
        self.assertEqual(self.record.events[-1]['status'], 'completed')

    def test_foreign_unbound_and_nondisplay_notifications_never_write(self):
        self.text('agent_message_chunk', 'foreign', session='other')
        self.emit('usage_update', outputTokens=30, secret='not display')
        self.emit('available_commands_update', availableCommands=[])
        self.emit('current_mode_update', mode='plan')
        self.emit('future-update', private='secret')
        self.assertEqual(self.record.events, [])

    def test_unrecognized_native_content_is_data_and_has_no_raw_payload(self):
        self.emit('agent_message_chunk', content={'type': 'image', 'data': 'secret'})
        self.emit('tool_call', toolCallId=[], title={})
        self.emit('plan', entries=[{'status': [], 'priority': {}}])
        self.emit([], content={})
        serialized = json.dumps(self.record.events)
        self.assertIn('[image]', serialized)
        self.assertIn('unidentified-tool-call', serialized)
        self.assertNotIn('secret', serialized)

    def test_prompt_identity_keeps_a_repeated_run_distinct(self):
        self.text('agent_message_chunk', 'one')
        first = self.record.events[-1]['_meta']
        self.projector.start_prompt('other', 'next', 'run-2/prompt-1')
        self.text('agent_message_chunk', 'two', session='other')
        self.assertNotEqual(first['contentSegment'], self.record.events[-1]['_meta']['contentSegment'])


class SessionRunTests(NativeRunCase):
    def test_projector_exceptions_preserve_completed_native_facts(self):
        self.extra_agent_args = ["--prompt-mode", "final"]
        for method in ("__init__", "start_prompt", "observe_notification", "close"):
            with self.subTest(method=method):
                with mock.patch.object(DshSessionProjector, method, side_effect=KeyError("display projection")):
                    result = native_run.run(self.fast_request("prompt"), observer=lambda _: FEEDBACK_CONTINUE,
                                            services=None, cancelled=lambda: False)
                self.assertEqual(result.end.status, "ok", result.end.message)
                self.assertEqual(result.session_record_failure.params.category, "KeyError")
                self.assertIsNotNone(result.value)
                self.assertTrue(result.evidence_refs)
                self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_no_record_path_skips_projection_and_keeps_result_and_stop(self):
        request = self.fast_request('router input').model_copy(update={'session_record_path': None})
        with mock.patch.object(native_run, 'DshSessionProjector') as projector:
            result = native_run.run(request, observer=lambda _: FEEDBACK_CONTINUE,
                                    services=None, cancelled=lambda: False)
        projector.assert_not_called()
        self.assertEqual(result.end.status, 'ok', result.end.message)
        self.assertIsNotNone(result.value)
        self.assertIsNone(result.session_record_failure)
        self.assertEqual(result.stop_evidence.native.group_state, 'gone')

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {'HMB_STATE_DIR': str(self.base / 'state')})
        patcher.start()
        self.addCleanup(patcher.stop)

    def record(self, request):
        return SessionRecord(request.session_record_path, request.identity.attempt_id)

    def events(self, request):
        return updates(self.record(request).path)

    def test_router_records_only_its_root_and_same_attempt_runs_append(self):
        self.extra_agent_args = ['--prompt-mode', 'final', '--emit-foreign-chunk']
        request = self.fast_request('router input')
        result = native_run.run(request, observer=lambda _f: FEEDBACK_CONTINUE, services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'ok', result.end.message)
        path = self.record(request).path
        before = notifications(path)
        self.assertIn('router input', json.dumps(before))
        self.assertNotIn('foreign', json.dumps(before))
        second = request.model_copy(update={'private_state': request.private_state.model_copy(update={
            'invocation_root': str(self.base / 'second-invocation')})})
        result = native_run.run(second, observer=lambda _f: FEEDBACK_CONTINUE, services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'ok', result.end.message)
        after = notifications(path)
        self.assertEqual(after[:len(before)], before)
        self.assertGreater(len(after), len(before))
        events = self.events(request)
        self.assertEqual([event['sessionUpdate'] for event in events],
                         ['user_message_chunk', 'agent_message_chunk'] * 2)
        self.assertNotEqual(events[1]['_meta']['contentSegment'], events[3]['_meta']['contentSegment'])

    def test_fake_acp_stream_projects_reasoning_tools_and_plan(self):
        fixture = Path(__file__).with_name('fixtures') / 'session-updates-dsh.json'
        native = json.loads((fixture.parent / 'native-usage-dsh.json').read_text(encoding="utf-8"))
        expected = ''.join(part['text'] for event in native['events'] if event['type'] == 'assistant/message'
                           for part in event['data']['message']['content'] if part['type'] == 'reasoning')
        self.extra_agent_args = ['--prompt-mode', 'final', '--session-updates-fixture', str(fixture)]
        request = self.fast_request('fixture prompt', scope='write')
        result = native_run.run(request, observer=lambda _f: FEEDBACK_CONTINUE, services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'ok', result.end.message)
        events = self.events(request)
        self.assertEqual(''.join(event['content']['text'] for event in events
                               if event['sessionUpdate'] == 'agent_thought_chunk'), expected)
        self.assertEqual({event['sessionUpdate'] for event in events}, {
            'user_message_chunk', 'agent_message_chunk', 'agent_thought_chunk',
            'tool_call', 'tool_call_update', 'plan'})
        tool = next(event for event in events if event['sessionUpdate'] == 'tool_call')
        self.assertEqual(tool['kind'], 'read')
        completed = [event for event in events if event.get('status') == 'completed']
        self.assertEqual(len(completed), 1)
        output = [event for event in events if isinstance(event.get('content'), list)]
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]['content'][0]['content']['text'], 'file text 中文')

    def test_record_write_failure_during_eof_drain_keeps_stop_evidence(self):
        self.extra_agent_args = ['--prompt-mode', 'final', '--late-tool-update']
        append = SessionRecord.append
        def fail_late(record, update):
            if getattr(update, 'tool_call_id', None) == 'call_late_1':
                record._failed(OSError('late record write failed'))
                return
            return append(record, update)
        with mock.patch.object(SessionRecord, 'append', fail_late):
            result = native_run.run(self.fast_request('prompt'), observer=lambda _f: FEEDBACK_CONTINUE,
                                    services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'ok')
        self.assertEqual(result.session_record_failure.code, 'session-record-write-failed')
        self.assertEqual(result.stop_evidence.native.group_state, 'gone')
        self.assertIsNotNone(result.value)
        self.assertTrue(result.evidence_refs)

    def test_worker_resume_records_only_the_current_prompt_after_history_replay(self):
        from hey_my_buddy.buddy.harnesses.run_contract import RunContinuation
        first, bound, mount = self.worker_request()
        self.micro_task_agent_args(mount)
        self.extra_agent_args += ['--session-record', 'usage']
        result = native_run.run(first, observer=worker_observer, services=bound.services, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'ok', result.end.message)
        second, bound, mount = self.worker_request(continuation=RunContinuation(
            mode='native-session', previous_session_id=result.native_identity.session_id))
        self.micro_task_agent_args(mount)
        self.extra_agent_args += ['--session-record', 'resume', '--replay-history']
        result = native_run.run(second, observer=worker_observer, services=bound.services, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'ok', result.end.message)
        content = json.dumps(notifications(self.record(second).path))
        self.assertNotIn("the previous turn's message", content)
        self.assertNotIn('call_old_1', content)
        self.assertIn('hey_my_buddy_finish_turn', content)

    def test_worker_records_the_existing_session_service_run(self):
        request, bound, mount = self.worker_request()
        self.micro_task_agent_args(mount)
        result = native_run.run(request, observer=worker_observer, services=bound.services, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'ok', result.end.message)
        events = self.events(request)
        self.assertEqual(events[0]['content']['text'], request.input_text)
        self.assertTrue(any(event['sessionUpdate'] == 'tool_call' for event in events))
        self.assertTrue(any(event.get('status') == 'completed' for event in events))

    def test_pre_model_startup_failure_leaves_no_record(self):
        self.extra_agent_args = ['--die-before-answer', 'initialize']
        request = self.fast_request('never sent')
        result = native_run.run(request, observer=lambda _f: FEEDBACK_CONTINUE, services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'error')
        self.assertFalse(result.model_started)
        self.assertFalse(native_path(self.record(request).path).exists())
        self.assertEqual(result.stop_evidence.native.group_state, 'gone')

    def test_invalid_receipt_is_still_present_in_the_display_record(self):
        request, bound, mount = self.worker_request()
        self.micro_task_agent_args(mount, forge_receipt=True)
        result = native_run.run(request, observer=worker_observer, services=bound.services, cancelled=lambda: False)
        self.assertEqual(result.end.reason_code, 'invalid-finish')
        calls = [event['toolCallId'] for event in self.events(request)
                 if event['sessionUpdate'] == 'tool_call' and event['title'] == mount.finish_tool]
        results = [event for event in self.events(request)
                   if event.get('toolCallId') in calls and event.get('status') == 'completed']
        self.assertEqual(len(results), 1)
        self.assertTrue(any(event.get('toolCallId') in calls and event.get('content')
                            for event in self.events(request)))

    def test_write_failure_still_builds_the_run_and_stop_result(self):
        self.extra_agent_args = ['--prompt-mode', 'final']
        request = self.fast_request('prompt')
        with mock.patch.object(SessionRecord, '_open', side_effect=OSError('controlled write failure')):
            result = native_run.run(request, observer=lambda _f: FEEDBACK_CONTINUE, services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'ok')
        self.assertEqual(result.session_record_failure.code, 'session-record-write-failed')
        self.assertTrue(result.model_started)
        self.assertEqual(result.stop_evidence.native.group_state, 'gone')
        self.assertIsNotNone(result.native_identity.session_id)
        self.assertTrue(result.evidence_refs)

    def test_close_failure_also_preserves_completed_run_facts(self):
        self.extra_agent_args = ['--prompt-mode', 'final']
        close = SessionRecord.close
        def fail_close(record):
            close(record)
            record._failed(OSError('controlled close failure'))
        with mock.patch.object(SessionRecord, 'close', fail_close):
            result = native_run.run(self.fast_request('prompt'), observer=lambda _f: FEEDBACK_CONTINUE,
                                    services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'ok')
        self.assertEqual(result.session_record_failure.code, 'session-record-write-failed')
        self.assertEqual(result.stop_evidence.native.group_state, 'gone')
        self.assertIsNotNone(result.value)

    def test_record_has_no_final_answer_size_limit(self):
        self.extra_agent_args = ['--prompt-mode', 'final', '--final-answer', '文' * 30000]
        request = self.fast_request('prompt')
        result = native_run.run(request, observer=lambda _f: FEEDBACK_CONTINUE, services=None, cancelled=lambda: False)
        self.assertEqual(result.end.reason_code, 'answer-too-large')
        self.assertEqual(self.events(request)[-1]['content']['text'], '文' * 30000)
