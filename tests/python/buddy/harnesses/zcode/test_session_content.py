"""Offline ZCode display projection; synthetic events do not prove native coverage.

The fixtures copy the shapes the installed ZCode app 0.17.0 really emitted in
the recorded live and resumed turns of the session-content probe
(``tests/probes/zcode_native.py`` against a loopback scripted model): body text
and reasoning stream as ``model.streaming`` ``text_delta``/``reasoning_delta``
frames keyed by ``assistantMessageId``, tool facts keep their established
frames, and the final response arrives in ``turn.completed``.
"""
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
from hey_my_buddy.paths import native_path


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

    def stream(self, kind, delta, *, message_id='msg-1', turn='turn', **fields):
        self.emit('model.streaming', assistantMessageId=message_id, kind=kind, delta=delta,
                  done=False, turn=turn, **fields)

    def test_streamed_text_reasoning_tools_and_response_are_recorded(self):
        self.stream('reasoning_delta', '思考')
        self.stream('reasoning_delta', '更多')
        self.stream('text_delta', '答复')
        self.emit('model.streaming', kind='tool_call', toolCallId='call', toolName='Read',
                  input={'file_path': 'a.py'}, delta='', done=False)
        self.emit('tool.updated', kind='result', toolCallId='call', result={'success': True, 'content': 'file 中文'})
        self.stream('text_delta', '完成', message_id='msg-2')
        self.emit('turn.completed', response='完成')
        events = self.record.events
        self.assertEqual({e['sessionUpdate'] for e in events}, {'user_message_chunk', 'agent_message_chunk',
            'agent_thought_chunk', 'tool_call', 'tool_call_update'})
        thoughts = [e for e in events if e['sessionUpdate'] == 'agent_thought_chunk']
        self.assertEqual(''.join(e['content']['text'] for e in thoughts), '思考更多')
        self.assertEqual(thoughts[0]['_meta']['reasoning'], 'text')
        self.assertEqual([e['_meta']['nativeItemId'] for e in thoughts], ['msg-1', 'msg-1'])
        messages = [e['content']['text'] for e in events if e['sessionUpdate'] == 'agent_message_chunk']
        self.assertEqual(messages, ['答复', '完成'])  # the streamed final body needs no response echo
        call = next(e for e in events if e['sessionUpdate'] == 'tool_call')
        self.assertEqual((call['kind'], call['rawInput']), ('read', {'file_path': 'a.py'}))
        self.assertTrue(any(e.get('rawOutput') == {'success': True, 'content': 'file 中文'} for e in events))

    def test_streamed_tool_input_survives_omitted_scheduled_input(self):
        payload = {'kind': 'tool_call', 'toolCallId': 'call', 'toolName': 'Bash',
                   'input': {'command': 'pwd'}, 'delta': '', 'done': False}
        self.emit('model.streaming', **payload)
        self.emit('tool.updated', kind='scheduled', toolCallId='call', toolName='Bash',
                  inputOmitted=True, inputByteLength=len(json.dumps(payload['input']).encode()))
        self.assertEqual(len(self.record.events), 2)
        call = self.record.events[-1]
        self.assertEqual((call['sessionUpdate'], call['kind'], call['status'], call['rawInput']),
                         ('tool_call', 'execute', 'pending', {'command': 'pwd'}))
        self.emit('tool.updated', kind='started', toolCallId='call')
        self.assertEqual(self.record.events[-1]['status'], 'in_progress')
        self.assertNotIn('rawInput', self.record.events[-1])

    def test_first_tool_input_wins_without_duplicate_writes(self):
        for first, second in (('model.streaming', 'tool.updated'), ('tool.updated', 'model.streaming')):
            with self.subTest(first=first):
                self.setUp()
                for event_type, value in ((first, {}), (second, {'file_path': 'later.py'})):
                    payload = {'kind': 'tool_call' if event_type == 'model.streaming' else 'scheduled',
                               'toolCallId': 'call', 'toolName': 'Read', 'input': value}
                    if event_type == 'model.streaming':
                        payload.update(delta='', done=False)
                    self.emit(event_type, **payload)
                    self.emit(event_type, **payload)
                self.assertEqual(len(self.record.events), 2)
                self.assertEqual(self.record.events[-1]['sessionUpdate'], 'tool_call')
                self.assertEqual(self.record.events[-1]['rawInput'], {})

    def test_completed_response_deduplicates_extends_or_replaces_streamed_text(self):
        for deltas, response, expected in (
                (('ab', 'c'), 'abc', ['ab', 'c']),            # equal final body: no echo
                (('ab',), 'abc', ['ab', 'c']),                # extension: append the suffix
                (('ab',), 'revised', ['ab', 'revised']),      # revision: replace the block
                ((), 'only final', ['only final']),           # unstreamed response: its own segment
                (('ab',), '', ['ab'])):                       # an empty response carries nothing
            with self.subTest(deltas=deltas, response=response):
                self.setUp()
                for piece in deltas:
                    self.stream('text_delta', piece)
                self.emit('turn.completed', response=response)
                written = [e for e in self.record.events[1:] if e['sessionUpdate'] == 'agent_message_chunk']
                self.assertEqual([e['content']['text'] for e in written], expected)
        self.setUp()
        self.stream('text_delta', 'one')
        self.stream('text_delta', 'two', message_id='msg-2')
        count = len(self.record.events)
        self.emit('turn.completed', response='two')  # a multi-round turn echoes only its final body
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

    def test_foreign_child_and_unbound_frames_do_not_write(self):
        before = list(self.record.events)
        self.stream('text_delta', 'foreign', session='foreign')
        self.stream('text_delta', 'history', turn='old')
        self.emit('turn.started', turn='old', inputId='old-input')
        self.stream('text_delta', 'history', turn='old')
        self.stream('text_delta', 'child', source='sub-agent')
        self.emit('tool.updated', kind='scheduled', toolCallId='c', toolName='Read', childSessionId='child')
        for identity in ({'session': 'foreign'}, {'turn': 'old'}, {'source': 'sub-agent'}):
            self.emit('model.streaming', kind='tool_call', toolCallId='c', toolName='Read',
                      input={'file_path': 'secret'}, delta='', done=False, **identity)
        self.assertEqual(self.record.events, before)

    def test_unusable_content_frames_are_markers_without_raw_payload(self):
        self.stream('text_delta', {'private': 'secret'})
        self.stream('reasoning_delta', 5)
        self.stream('text_delta', 'secret', message_id=None)
        count = len(self.record.events)
        self.stream('text_delta', '')  # an empty delta carries nothing, not even a marker
        self.emit('model.streaming', assistantMessageId='msg-1', kind='text_start', delta='', done=False)
        self.emit('model.streaming', kind='tool_input_start', toolCallId='call', toolName='Read',
                  delta='', done=False)
        self.assertEqual(len(self.record.events), count)
        self.emit('tool.updated', kind=[], toolCallId='c')
        self.emit('tool.updated', kind='scheduled', toolCallId=[])
        serialized = json.dumps(self.record.events)
        self.assertIn('[model-streaming-text_delta]', serialized)
        self.assertIn('[unknown-tool-phase]', serialized)
        self.assertIn('[unidentified-tool-call]', serialized)
        self.assertNotIn('secret', serialized)

    def test_pre_model_events_do_not_open_record(self):
        record = MemoryRecord()
        projector = ZcodeSessionProjector(record)
        projector.start_prompt('root', 'input', 'prompt', 'invocation')
        projector.observe_notification({'method': 'session/event', 'params': {'sessionId': 'root',
            'turnId': 'old', 'type': 'model.streaming', 'payload': {'kind': 'text_delta',
                'assistantMessageId': 'msg-old', 'delta': 'old', 'done': False}}})
        self.assertEqual(record.events, [])


class RouterSessionTests(NativeRunCase):
    def test_projector_exceptions_preserve_completed_native_facts(self):
        for method in ("__init__", "start_prompt", "observe_notification", "close"):
            with self.subTest(method=method):
                request = self.fast_request("Return only one JSON value matching this schema: {}")
                with mock.patch.object(ZcodeSessionProjector, method, side_effect=KeyError("display projection")):
                    result = native_run.run(request, observer=lambda _: FEEDBACK_CONTINUE,
                                            services=None, cancelled=lambda: False)
                self.assertEqual(result.end.status, "ok", result.end.message)
                self.assertEqual(result.session_record_failure.params.category, "KeyError")
                self.assertEqual(result.value.raw, '{"choice":"a"}')
                self.assertTrue(result.evidence_refs)
                self.assertEqual(result.stop_evidence.native.group_state, "gone")

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
        self.assertFalse(native_path(self.record(request).path).exists())
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

    def test_write_and_close_failures_keep_completed_native_run_and_both_diagnostics(self):
        request = self.fast_request('Return only one JSON value matching this schema: {}')
        original_write = SessionRecord._write
        original_close = os.close
        writes = 0
        record_fd = None

        def failed_write(fd, line):
            nonlocal writes, record_fd
            writes += 1
            if writes == 2:
                record_fd = fd
                raise OSError('controlled write failure')
            return original_write(fd, line)

        def failed_close(fd):
            if fd == record_fd:
                raise OSError('controlled close failure')
            return original_close(fd)

        try:
            with mock.patch.object(SessionRecord, '_write', side_effect=failed_write), \
                 mock.patch('hey_my_buddy.buddy.harnesses.session_record.os.close', side_effect=failed_close):
                result = native_run.run(request, observer=lambda _: FEEDBACK_CONTINUE,
                                        services=None, cancelled=lambda: False)
        finally:
            if record_fd is not None:
                original_close(record_fd)

        self.assertEqual(result.end.status, 'ok')
        self.assertEqual(result.value.raw, '{"choice":"a"}')
        self.assertEqual(result.stop_evidence.native.group_state, 'gone')
        self.assertIsNotNone(result.native_identity.session_id)
        self.assertEqual(result.session_record_failure.params.category, 'OSError')
        self.assertEqual(result.session_record_failure.params.message, 'controlled write failure')
        self.assertEqual(result.session_record_failure.params.close_failure.category, 'OSError')
        self.assertEqual(result.session_record_failure.params.close_failure.message, 'controlled close failure')

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
        request = self.fast_request('Return only one JSON value matching this schema: {}').model_copy(update={'permission': 'write'})
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

    def test_worker_stream_records_every_content_kind_and_deduplicates_tool_result(self):
        from hey_my_buddy.buddy.roles.run_observers import worker_observer
        request, services = self.worker_request(self.context().turn['input'])
        with mock.patch.dict(os.environ, {**self.environment, 'HMB_ZCODE_TEST_CASE': 'session-content'}, clear=True):
            result = native_run.run(request, observer=worker_observer, services=services, cancelled=lambda: False)
        self.assertEqual(result.end.status, 'ok', result.end.message)
        events = self.events(request)
        self.assertEqual({e['sessionUpdate'] for e in events}, {'user_message_chunk', 'agent_message_chunk',
            'agent_thought_chunk', 'tool_call', 'tool_call_update'})
        self.assertEqual([e['content']['text'] for e in events if e['sessionUpdate'] == 'agent_thought_chunk'],
                         ['fixture reasoning that is never assistant output'])
        self.assertEqual([e['content']['text'] for e in events if e['sessionUpdate'] == 'agent_message_chunk'],
                         ['fixture 正文 before the tools', 'fixture body after the tools',
                          'fixture final text is not the outcome'])
        final = [e for e in events if e['sessionUpdate'] == 'agent_message_chunk'][-1]
        self.assertTrue(final['_meta']['replace'])  # the response revises the final body
        for call_id, expected in (('display-read', {'file_path': '/workspace/file.py'}),
                                  ('display-execute', {'command': 'pwd'})):
            calls = [e for e in events if e.get('toolCallId') == call_id and e['sessionUpdate'] == 'tool_call']
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]['rawInput'], expected)
            self.assertEqual(sum('rawInput' in e for e in events if e.get('toolCallId') == call_id), 1)
        completed = [e for e in events if e.get('toolCallId') == 'display-read' and e.get('status') == 'completed']
        self.assertEqual(len(completed), 1)
        output = [e for e in events if e.get('toolCallId') == 'display-read' and e.get('content')]
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]['content'][0]['content']['text'], 'file 中文')
        self.assertNotIn('secret', json.dumps(events))

    @staticmethod
    def record_end(events):
        return events[-1]

    def test_resumed_worker_records_its_own_stream_in_the_same_attempt_record(self):
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
        self.assertEqual(sum(e['sessionUpdate'] == 'agent_thought_chunk' for e in events), 1)

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
