"""Offline ZCode display projection; synthetic events do not prove native coverage."""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from hey_my_buddy.buddy.harnesses.zcode import native_run
from hey_my_buddy.buddy.harnesses.zcode.session_content import ZcodeSessionProjector
from hey_my_buddy.buddy.harnesses.session_record import SessionRecord
from buddy.harnesses.session_record_support import notifications, updates
from hey_my_buddy.buddy.harnesses.run_contract import FEEDBACK_CONTINUE
from buddy.harnesses.zcode.test_native_run import NativeRunCase, MockNativeCase


class MemoryRecord:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(event.model_dump(by_alias=True, exclude_none=True))


class ProjectorTests(unittest.TestCase):
    def setUp(self):
        self.record = MemoryRecord()
        self.projector = ZcodeSessionProjector(self.record)
        self.projector.start_prompt('root', 'input', 'prompt 中文', 'invocation')
        self.emit('turn.started', inputId='input')

    def emit(self, event_type, *, session='root', turn='turn', **payload):
        self.projector.observe_notification({'method': 'session/event', 'params': {
            'sessionId': session, 'turnId': turn, 'type': event_type, 'payload': payload}})

    def part(self, text, *, kind='text', part_id='p', delta=False, **fields):
        self.emit('part.delta' if delta else 'part.upserted', **{
            'delta' if delta else 'part': {'type': kind, 'partId': part_id, 'text': text, **fields}})

    def test_six_updates_text_reasoning_input_output_and_plan(self):
        self.part('思考', kind='reasoning', part_id='r')
        self.part('更多', kind='reasoning', part_id='r', delta=True)
        self.part('答复')
        self.emit('tool.updated', kind='scheduled', toolCallId='call', toolName='Read', arguments={'file_path': 'a.py'})
        self.emit('tool.updated', kind='result', toolCallId='call', result={'success': True, 'content': 'file 中文'})
        self.emit('part.upserted', part={'type': 'plan', 'partId': 'plan', 'entries': [
            {'content': 'finish', 'status': 'pending', 'priority': 'high'}]})
        events = self.record.events
        self.assertEqual({e['sessionUpdate'] for e in events}, {'user_message_chunk', 'agent_message_chunk',
            'agent_thought_chunk', 'tool_call', 'tool_call_update', 'plan'})
        thoughts = [e for e in events if e['sessionUpdate'] == 'agent_thought_chunk']
        self.assertEqual(''.join(e['content']['text'] for e in thoughts), '思考更多')
        self.assertEqual(thoughts[0]['_meta']['reasoning'], 'text')
        call = next(e for e in events if e['sessionUpdate'] == 'tool_call')
        self.assertEqual((call['kind'], call['rawInput']), ('read', {'file_path': 'a.py'}))
        self.assertTrue(any(e.get('rawOutput') == {'success': True, 'content': 'file 中文'} for e in events))

    def test_versioned_message_parts_preserve_exact_supplied_reasoning(self):
        sample = json.loads((Path(__file__).with_name('fixtures') / 'native-usage-zcode.json').read_text())
        self.assertEqual(sample['harnessVersion'], '0.16.9')
        expected = []
        for item in sample['finalMessages']['messages']:
            if item['info']['role'] != 'assistant':
                continue
            item = {**item, 'info': {**item['info'], 'sessionId': 'root'},
                    'parts': [{**p, 'sessionId': 'root'} for p in item['parts']]}
            expected.extend(p['text'] for p in item['parts'] if p['type'] == 'reasoning')
            self.emit('message.upserted', message=item)
        self.assertEqual([e['content']['text'] for e in self.record.events
                          if e['sessionUpdate'] == 'agent_thought_chunk'], expected)
        from hey_my_buddy.buddy.harnesses.zcode.session_omitted import OMITTED
        self.assertEqual(OMITTED, ())  # This sample cannot prove a missing field.

    def test_completed_snapshots_deduplicate_and_revisions_replace(self):
        self.part('ab', delta=True)
        self.part('c', delta=True)
        self.part('abc')
        self.emit('turn.completed', response='abc')
        self.assertEqual([e['content']['text'] for e in self.record.events[1:]], ['ab', 'c'])
        self.part('revised')
        self.assertTrue(self.record.events[-1]['_meta']['replace'])
        self.assertEqual(self.record.events[-1]['content']['text'], 'revised')
        self.emit('turn.completed', response='revised final')
        self.assertEqual(self.record.events[-1]['content']['text'], ' final')

    def test_completed_only_response_and_multisegment_snapshot(self):
        self.emit('turn.completed', response='only final')
        self.assertEqual(self.record.events[-1]['content']['text'], 'only final')
        self.part('one', part_id='p1', messageId='m')
        self.part('two', part_id='p2', messageId='m')
        count = len(self.record.events)
        self.emit('turn.completed', response='onetwo')
        self.assertEqual(len(self.record.events), count)

    def test_tool_categories_and_repeated_terminal_output(self):
        for i, (name, expected) in enumerate((('Read', 'read'), ('Grep', 'search'), ('Bash', 'execute'),
                                              ('Edit', 'edit'), ('mcp__test__tool', 'other'))):
            self.emit('tool.updated', kind='scheduled', toolCallId=str(i), toolName=name)
            self.assertEqual(self.record.events[-1]['kind'], expected)
        self.emit('tool.updated', kind='result', toolCallId='0', result={'success': True, 'content': 'ab'})
        count = len(self.record.events)
        self.emit('tool.updated', kind='result', toolCallId='0', result={'success': True, 'content': 'ab'})
        self.assertEqual(len(self.record.events), count)
        self.emit('tool.updated', kind='result', toolCallId='0', result={'success': True, 'content': 'abc'})
        self.assertEqual(self.record.events[-1]['content'][0]['content']['text'], 'c')
        self.emit('tool.updated', kind='error', toolCallId='0', error='revised')
        self.assertTrue(self.record.events[-1]['_meta']['replace'])

    def test_foreign_history_child_and_unbound_turn_do_not_write(self):
        before = list(self.record.events)
        self.emit('part.upserted', session='foreign', part={'type': 'text', 'partId': 'p', 'text': 'foreign'})
        self.emit('part.upserted', turn='old', part={'type': 'text', 'partId': 'p', 'text': 'history'})
        self.emit('turn.started', turn='old', inputId='old-input')
        self.emit('part.upserted', turn='old', part={'type': 'text', 'partId': 'p', 'text': 'history'})
        self.part('child', part_id='child', source='sub-agent')
        self.emit('tool.updated', kind='scheduled', toolCallId='c', toolName='Read', childSessionId='child')
        self.emit('message.upserted', message={'info': {'role': 'assistant', 'parentToolCallId': 'child'},
                                             'parts': [{'type': 'text', 'partId': 'p', 'text': 'child'}]})
        self.assertEqual(self.record.events, before)

    def test_unknown_native_content_is_a_marker_without_raw_payload(self):
        self.emit('part.upserted', part={'type': 'image', 'partId': 'image', 'data': 'secret'})
        self.emit('part.upserted', part={'type': 'text', 'text': {'private': 'secret'}})
        self.emit('part.upserted', part={'type': 'plan', 'partId': 'plan', 'entries': [{'status': []}]})
        self.emit('tool.updated', kind=[], toolCallId='c')
        self.emit('tool.updated', kind='scheduled', toolCallId=[])
        serialized = json.dumps(self.record.events)
        self.assertIn('[image]', serialized)
        self.assertIn('[plan]', serialized)
        self.assertNotIn('secret', serialized)

    def test_existing_idless_native_stream_keeps_deltas_and_deduplicates_snapshot(self):
        self.emit('part.started', part={'type': 'text', 'text': ''})
        self.emit('part.delta', delta={'type': 'text', 'text': '{'})
        self.emit('message.upserted', message={'parts': [{'type': 'text', 'text': '{"choice":"a"}'}]})
        self.emit('turn.completed', response='{"choice":"a"}')
        events = self.record.events[1:]
        self.assertEqual(''.join(e['content']['text'] for e in events), '{"choice":"a"}')
        self.assertEqual(len({(e['_meta']['nativeItemId'], e['_meta']['contentSegment']) for e in events}), 1)

    def test_pre_model_events_do_not_open_record(self):
        record = MemoryRecord()
        projector = ZcodeSessionProjector(record)
        projector.start_prompt('root', 'input', 'prompt', 'invocation')
        projector.observe_notification({'method': 'session/event', 'params': {'sessionId': 'root',
            'turnId': 'old', 'type': 'part.upserted', 'payload': {'part': {'type': 'text', 'text': 'old'}}}})
        self.assertEqual(record.events, [])


class RouterSessionTests(NativeRunCase):
    def test_no_record_path_skips_projection_and_keeps_result_and_stop(self):
        request = self.fast_request('Return only one JSON value matching this schema: {}').model_copy(
            update={'session_record_path': None})
        with mock.patch.object(native_run, 'ZcodeSessionProjector') as projector:
            result = native_run.run(request, observer=lambda _: FEEDBACK_CONTINUE,
                                    services=None, cancelled=lambda: False)
        projector.assert_not_called()
        self.assertEqual(result.end.status, 'ok', result.end.message)
        self.assertEqual(result.value.raw, '{"choice":"a"}')
        self.assertIsNone(result.session_record_failure)
        self.assertEqual(result.stop_evidence.native.group_state, 'gone')

    def record(self, request):
        return SessionRecord(request.session_record_path, request.identity.attempt_id)

    def events(self, request):
        return updates(self.record(request).path)

    def test_router_runs_append_to_one_attempt_with_one_header(self):
        request = self.fast_request('Return only one JSON value matching this schema: {}')
        first = native_run.run(request, observer=lambda _: FEEDBACK_CONTINUE, services=None, cancelled=lambda: False)
        self.assertEqual(first.end.status, 'ok', first.end.message)
        path = self.record(request).path
        before = notifications(path)
        second = request.model_copy(update={
            'identity': request.identity.model_copy(update={'invocation_id': 'second-invocation'}),
            'private_state': request.private_state.model_copy(update={'invocation_root': str(self.base / 'second')})})
        result = native_run.run(second, observer=lambda _: FEEDBACK_CONTINUE, services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'ok', result.end.message)
        after = notifications(path)
        self.assertEqual(after[:len(before)], before)
        events = self.events(request)
        self.assertEqual([e['sessionUpdate'] for e in events], ['user_message_chunk', 'agent_message_chunk'] * 2)
        self.assertNotEqual(events[1]['_meta']['contentSegment'], events[3]['_meta']['contentSegment'])

    def test_pre_model_failure_leaves_no_record(self):
        request = self.fast_request('never sent')
        with mock.patch.object(native_run, 'configure_session', side_effect=native_run.NativeError('fixture-startup', 'failed')):
            result = native_run.run(request, observer=lambda _: FEEDBACK_CONTINUE, services=None, cancelled=lambda: False)
        self.assertEqual(result.end.reason_code, 'fixture-startup')
        self.assertFalse(result.model_started)
        self.assertFalse(self.record(request).path.exists())
        self.assertEqual(result.stop_evidence.native.group_state, 'gone')

    def test_write_failure_preserves_native_identity_stop_and_result(self):
        request = self.fast_request('Return only one JSON value matching this schema: {}')
        with mock.patch.object(SessionRecord, '_open', side_effect=OSError('controlled write failure')):
            result = native_run.run(request, observer=lambda _: FEEDBACK_CONTINUE, services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'ok')
        self.assertEqual(result.session_record_failure.code, 'session-record-write-failed')
        self.assertTrue(result.model_started)
        self.assertIsNotNone(result.native_identity.session_id)
        self.assertEqual(result.stop_evidence.native.group_state, 'gone')
        self.assertTrue(result.evidence_refs)

    def test_close_failure_preserves_completed_value(self):
        request = self.fast_request('Return only one JSON value matching this schema: {}')
        close = SessionRecord.close
        def failed_close(record):
            close(record)
            record._failed(OSError('controlled close failure'))
        with mock.patch.object(SessionRecord, 'close', failed_close):
            result = native_run.run(request, observer=lambda _: FEEDBACK_CONTINUE, services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'ok')
        self.assertEqual(result.session_record_failure.code, 'session-record-write-failed')
        self.assertEqual(result.stop_evidence.native.group_state, 'gone')
        self.assertIsNotNone(result.value)
        self.assertEqual(result.value.raw, '{"choice":"a"}')

    def test_late_write_failure_keeps_eof_stop_and_completed_value(self):
        os.environ['HMB_ZCODE_TEST_CASE'] = 'write-post-close'
        request = self.fast_request('Return only one JSON value matching this schema: {}').model_copy(update={'tool_scope': 'write'})
        append = SessionRecord.append
        def failed_late(record, update):
            if getattr(update, 'tool_call_id', None) == 'c-late':
                record._failed(OSError('late record write failed'))
                return
            return append(record, update)
        with mock.patch.object(SessionRecord, 'append', failed_late):
            result = native_run.run(request, observer=lambda _: FEEDBACK_CONTINUE, services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'ok')
        self.assertEqual(result.session_record_failure.code, 'session-record-write-failed')
        self.assertEqual(result.stop_evidence.native.group_state, 'gone')
        self.assertIsNotNone(result.value)


class WorkerSessionTests(MockNativeCase):
    def events(self, request):
        return updates(Path(request.session_record_path))

    def test_worker_records_native_finish_tool_result(self):
        from hey_my_buddy.buddy.roles.run_observers import worker_observer
        context = self.context()
        request, services = self.worker_request(context.turn['input'])
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = native_run.run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'ok', result.end.message)
        events = self.events(request)
        self.assertEqual(events[0]['content']['text'], request.input_text)
        self.assertTrue(any(e['sessionUpdate'] == 'tool_call' and 'hey_my_buddy_finish_turn' in e['title'] for e in events))
        self.assertTrue(any(e.get('status') == 'completed' and e.get('rawOutput') for e in events))
        self.assertIsNotNone(result.value)

    def test_worker_synthetic_stream_records_six_updates_and_deduplicates_tool_result(self):
        from hey_my_buddy.buddy.roles.run_observers import worker_observer
        request, services = self.worker_request(self.context().turn['input'])
        with mock.patch.dict(os.environ, {**self.environment, 'HMB_ZCODE_TEST_CASE': 'session-content'}, clear=True):
            result = native_run.run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'ok', result.end.message)
        events = self.events(request)
        self.assertEqual({e['sessionUpdate'] for e in events}, {'user_message_chunk', 'agent_message_chunk',
            'agent_thought_chunk', 'tool_call', 'tool_call_update', 'plan'})
        self.assertEqual([e['content']['text'] for e in events if e['sessionUpdate'] == 'agent_thought_chunk'],
                         ['fixture reasoning that is never assistant output'])
        completed = [e for e in events if e.get('toolCallId') == 'display-read' and e.get('status') == 'completed']
        self.assertEqual(len(completed), 1)
        output = [e for e in events if e.get('toolCallId') == 'display-read' and e.get('content')]
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]['content'][0]['content']['text'], 'file 中文')
        self.assertNotIn('secret', json.dumps(events))

    def test_resumed_worker_excludes_replayed_history_and_preserves_same_attempt_record(self):
        from hey_my_buddy.buddy.harnesses.run_contract import RunContinuation
        from hey_my_buddy.buddy.roles.run_observers import worker_observer
        first_input = self.context().turn['input']
        request, services = self.worker_request(first_input)
        with mock.patch.dict(os.environ, self.environment, clear=True):
            first = native_run.run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(first.end.status, 'ok', first.end.message)
        session = first.native_identity.session_id
        original = self.events(request)
        second_input = {**first_input, 'turnId': 'next-turn', 'previousSessionId': session, 'resumeMode': 'native-session'}
        second, services = self.worker_request(second_input,
            continuation=RunContinuation(mode='native-session', previous_session_id=session))
        second = second.model_copy(update={'session_record_path': request.session_record_path})
        with mock.patch.dict(os.environ, {**self.environment, 'HMB_ZCODE_TEST_CASE': 'session-content-resume'}, clear=True):
            result = native_run.run(second, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'ok', result.end.message)
        events = self.events(second)
        self.assertEqual(events[:len(original)], original)
        self.assertNotIn('secret', json.dumps(events))
        self.assertEqual(sum(e['sessionUpdate'] == 'user_message_chunk' for e in events), 2)
        self.assertTrue(any(e['sessionUpdate'] == 'agent_thought_chunk' for e in events))

    def test_worker_write_failure_preserves_completed_receipt_and_stop(self):
        from hey_my_buddy.buddy.roles.run_observers import worker_observer
        request, services = self.worker_request(self.context().turn['input'])
        append = SessionRecord.append
        def failed_terminal(record, update):
            if getattr(update, 'status', None) == 'completed':
                record._failed(OSError('terminal record write failed'))
                return
            return append(record, update)
        with mock.patch.dict(os.environ, self.environment, clear=True), \
             mock.patch.object(SessionRecord, 'append', failed_terminal):
            result = native_run.run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'ok')
        self.assertEqual(result.session_record_failure.code, 'session-record-write-failed')
        self.assertEqual(result.stop_evidence.native.group_state, 'gone')
        self.assertTrue(result.evidence_refs)
        self.assertIsNotNone(result.native_identity.session_id)
