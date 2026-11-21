"""Byte pages and bounded historical queries over the real ACP record format."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hey_my_buddy.errors import BoardError
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.protocol import session_reader as reader
from hey_my_buddy.protocol.session_content import SESSION_FORMAT


def line(value):
    return (canonical_json(value) + '\n').encode('utf-8')


def event(update, **fields):
    return {'sessionId': 'attempt-one', 'update': {'sessionUpdate': update, **fields}}


def message(text, *, kind='agent_message_chunk', meta=None):
    return event(kind, content={'type': 'text', 'text': text}, **({'_meta': meta} if meta else {}))


def tool_text(text):
    return [{'type': 'content', 'content': {'type': 'text', 'text': text}}]


HEADER = line(SESSION_FORMAT.to_payload())
META = {'nativeItemId': 'shared', 'contentSegment': 'text'}


class SessionReaderTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(TemporaryDirectory())) / 'record.jsonl'

    def record(self, *updates):
        self.path.write_bytes(HEADER + b''.join(map(line, updates)))

    def session(self, **fields):
        item = dict.fromkeys(reader._SESSION_FIELDS)
        return {**item, 'turnIndex': 1, 'turnId': 'turn-one', 'attemptId': 'attempt-one',
                'turnState': 'returned', 'harness': 'codex', 'recordAvailable': True,
                'recordPath': str(self.path), **fields}

    def assert_error(self, code, call, *args):
        with self.assertRaises(reader.PriorSessionError) as caught:
            call(*args)
        self.assertEqual(caught.exception.code, code)
        return caught.exception.message

    def assert_bounded(self, result):
        self.assertLessEqual(len(canonical_json(result).encode('utf-8')), reader.MAX_PRIOR_SESSION_RESULT_BYTES)

    def test_raw_pages_keep_byte_boundaries_alignment_and_complete_lines(self):
        updates = [message('早期 🌊'), message('first', meta={**META, 'replace': True}), message('second', meta=META)]
        self.record(*updates)
        last = self.path.stat().st_size
        with self.path.open('ab') as stream:
            stream.write(b'{"partial"')
        start = len(HEADER)
        middle = start + len(line(updates[0]))
        self.assertEqual(reader.read_raw_page(self.path, {'limit': 1}), {
            'events': updates[1:], 'before': middle, 'after': last, 'hasEarlier': True,
        })
        self.assertEqual(reader.read_raw_page(self.path, {'before': middle, 'limit': 1}), {
            'events': updates[:1], 'before': start, 'after': middle, 'hasEarlier': False,
        })
        self.assertEqual(reader.read_raw_page(self.path, {'after': 0, 'limit': 1}), {
            'events': updates[:1], 'before': start, 'after': middle, 'hasEarlier': False,
        })
        self.assertEqual(reader.read_raw_page(self.path, {'after': last}), {
            'events': [], 'before': last, 'after': last, 'hasEarlier': True,
        })
        with self.assertRaises(BoardError) as caught:
            reader.read_raw_page(self.path, {'after': middle - 1})
        self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')
        self.path.write_bytes(HEADER[:-1])
        self.assertEqual(reader.read_raw_page(self.path, {'after': 0}), reader.empty_raw_page({'after': 0}))

    def test_raw_page_excludes_appends_after_snapshot_size_is_observed(self):
        first, second = message('first'), message('second')
        self.record(first)
        end = self.path.stat().st_size
        original = reader._last_newline

        def append_after_size(stream, position, floor):
            if self.path.stat().st_size == end:
                with self.path.open('ab') as output:
                    output.write(line(second))
            return original(stream, position, floor)

        with patch.object(reader, '_last_newline', side_effect=append_after_size):
            page = reader.read_raw_page(self.path, {})
        self.assertEqual((page['events'], page['after']), ([first], end))
        self.assertEqual(reader.read_raw_page(self.path, {'after': end})['events'], [second])

    def test_coalesces_messages_tools_and_plan_in_opening_order(self):
        output_meta = {'nativeItemId': 'tool', 'contentSegment': 'stdout'}
        other_meta = {**output_meta, 'contentSegment': 'stderr'}
        plans = [{'content': 'inspect result', 'priority': 'high', 'status': 'pending'}]
        self.record(
            message('obsolete', meta=META),
            message('check it ', kind='user_message_chunk', meta=META),
            message('hidden', kind='agent_thought_chunk', meta=META),
            event('tool_call_update', toolCallId='t', title='Opening', rawInput={'command': 'test'},
                  content=tool_text('old'), _meta=output_meta, locations=[{'path': 'a'}]),
            message('replacement', meta={**META, 'replace': True}),
            message('first independent'),
            event('tool_call', toolCallId='t', title='Run test', kind='execute', status='in_progress',
                  rawInput={'cwd': '/checkout'}, content=tool_text('standard'), _meta={**output_meta, 'replace': True},
                  locations=[{'path': 'a'}, {'path': 'b'}]),
            event('tool_call_update', toolCallId='t', title='', rawOutput={'aggregatedOutput': 'standard output'},
                  content=tool_text(' output'), _meta=output_meta),
            message('again', kind='user_message_chunk', meta=META),
            event('tool_call_update', toolCallId='t', status='failed', rawOutput={'exitCode': 1},
                  content=tool_text('diagnostic'), _meta=other_meta),
            message(' continued', meta=META),
            message('second independent'),
            event('plan', entries=plans),
            event('tool_call', toolCallId='raw', title='Raw result', rawInput='plain', rawOutput=['value']),
        )
        entries = reader.coalesce_entries(self.path)
        self.assertEqual([entry['entryIndex'] for entry in entries], list(range(7)))
        self.assertEqual([entry['kind'] for entry in entries],
                         ['message', 'user_message', 'tool_call', 'message', 'message', 'plan', 'tool_call'])
        self.assertEqual([entries[index]['text'] for index in (0, 1, 3, 4)],
                         ['replacement continued', 'check it again', 'first independent', 'second independent'])
        self.assertEqual(entries[2], {'entryIndex': 2, 'kind': 'tool_call', 'toolKind': 'execute',
                                      'title': 'Run test', 'status': 'failed', 'locations': ['a', 'b'],
                                      'input': '{"command":"test","cwd":"/checkout"}',
                                      'output': 'standard outputdiagnostic\n{"exitCode":1}'})
        self.assertEqual(entries[5]['entries'], plans)
        self.assertEqual(entries[5]['text'], '[pending/high] inspect result')
        self.assertEqual((entries[6]['input'], entries[6]['output']), ('plain', '["value"]'))
        thoughts = reader.coalesce_entries(self.path, include_thoughts=True)
        self.assertEqual(thoughts[2], {'entryIndex': 2, 'kind': 'thought', 'text': 'hidden'})

    def test_tool_output_omits_mirrored_text_but_keeps_other_raw_blocks(self):
        image = {'type': 'image', 'source': 'opaque image evidence'}
        self.record(event('tool_call', toolCallId='mixed', title='Mixed',
                          rawOutput=[{'type': 'text', 'text': 'shown'}, image],
                          content=tool_text('shown')))
        self.assertEqual(reader.coalesce_entries(self.path)[0]['output'],
                         'shown\n' + canonical_json([image]))

    def test_all_tools_validate_args_and_keep_attempt_selection_private(self):
        self.record(message('result'))
        manifest = [self.session(attemptId='gone', recordAvailable=False, unavailableReason='stopped', recordPath=None),
                    self.session()]
        names = reader.PRIOR_SESSION_TOOL_NAMES
        for name, arguments in (
            (names[0], None), (names[0], {'cursor': True}), (names[0], {'cursor': -1}),
            (names[0], {'limit': 51}), (names[0], {'recordPath': str(self.path)}),
            (names[1], {'kinds': ['thought']}), (names[1], {'failedOnly': 1}),
            (names[1], {'reverse': 'true'}), (names[1], {'limit': 0}),
            (names[1], {'attemptId': None}), (names[2], {'entryIndex': 0}),
            (names[2], {'entryIndex': False, 'attemptId': 'attempt-one'}),
            (names[2], {'entryIndex': 0, 'attemptId': 'attempt-one', 'limit': 20001}),
            (names[2], {'entryIndex': 0, 'attemptId': 'attempt-one', 'offset': 7}),
            (names[2], {'entryIndex': 0, 'attemptId': 'attempt-one', 'field': 'output'}),
        ):
            with self.subTest(name=name, arguments=arguments):
                self.assert_error('invalid-argument', reader.call_prior_session_tool, name, arguments, manifest)
        with patch.object(reader, 'coalesce_entries', side_effect=AssertionError('must not open a file')):
            for name in names[1:]:
                for attempt_id, code in ((str(self.path), 'unknown-attempt'), ('gone', 'session-unavailable')):
                    args = {'attemptId': attempt_id, **({'entryIndex': 0} if name == names[2] else {})}
                    self.assert_error(code, reader.call_prior_session_tool, name, args, manifest)
        detail = self.assert_error('session-unavailable', reader.session_entries_page, manifest, {'attemptId': 'gone'})
        self.assertIn('stopped', detail)
        page = reader.call_prior_session_tool(names[0], {'limit': 1}, manifest)
        self.assertEqual((page['totalSessions'], page['nextCursor']), (2, 1))
        self.assertEqual([item['attemptId'] for item in page['items']], ['gone'])
        second = reader.prior_sessions_page(manifest, {'cursor': 1})
        self.assertEqual(second['items'][0]['attemptId'], 'attempt-one')
        self.assertNotIn(str(self.path), canonical_json(second))
        self.assertNotIn('recordPath', second['items'][0])
        self.assertEqual(reader.session_entries_page(manifest, {})['attemptId'], 'attempt-one')
        self.assert_error('unknown-tool', reader.call_prior_session_tool, 'other', {}, manifest)
        for spec in reader.prior_session_tool_specs():
            self.assertIn(reader.PRIOR_SESSION_NOTICE, spec['description'])
            self.assertIn('Reasoning segments are omitted.', spec['description'])
            self.assertFalse(spec['inputSchema']['additionalProperties'])

    def test_filtered_order_keeps_canonical_entry_indexes(self):
        self.record(message('asked', kind='user_message_chunk'),
                    event('tool_call', toolCallId='first', title='earlier', status='failed', rawInput='x' * 241,
                          locations=[{'path': str(index)} for index in range(9)]),
                    event('tool_call', toolCallId='pass', title='success', status='completed'),
                    message('answer'),
                    event('tool_call', toolCallId='last', title='later', status='failed', rawOutput='y' * 401))
        manifest = [self.session()]
        args = {'failedOnly': True, 'reverse': True, 'kinds': ['tool_call'], 'limit': 1}
        latest = reader.session_entries_page(manifest, args)
        self.assertEqual((latest['totalEntries'], latest['matchedEntries'], latest['nextCursor']), (5, 2, 1))
        self.assertEqual(latest['items'][0]['entryIndex'], 4)
        self.assertEqual(latest['items'][0]['outputPreview'], 'y' * 400)
        self.assertTrue(latest['items'][0]['truncated'])
        earlier = reader.session_entries_page(manifest, {**args, 'cursor': 1})['items'][0]
        self.assertEqual((earlier['entryIndex'], earlier['locationsTotal'], len(earlier['locations'])), (1, 9, 8))
        self.assertEqual((earlier['inputPreview'], earlier['inputChars']), ('x' * 240, 241))
        self.assertEqual([row['entryIndex'] for row in reader.session_entries_page(
            manifest, {'kinds': ['message', 'user_message']})['items']], [0, 3])
        self.assertEqual(reader.session_entry_detail(manifest, {'attemptId': 'attempt-one', 'entryIndex': 1})['field'],
                         'input')
        self.assertEqual(reader.session_entry_detail(manifest, {'attemptId': 'attempt-one', 'entryIndex': 4})['field'],
                         'output')

    def test_pages_are_byte_bounded_and_advance_through_large_entries(self):
        manifest = [self.session(attemptId=f'attempt-{index}', provider='🧪' * 256) for index in range(50)]
        page = reader.prior_sessions_page(manifest, {'limit': 50})
        self.assert_bounded(page)
        self.assertEqual(page['nextCursor'], len(page['items']))
        self.assertLess(len(page['items']), 50)
        self.record(*(message('中文🌊' * 500) for _ in range(50)))
        page = reader.session_entries_page([self.session()], {'limit': 50})
        self.assert_bounded(page)
        self.assertEqual(page['nextCursor'], len(page['items']))
        self.assertLess(len(page['items']), 50)
        self.record(event('tool_call', toolCallId='huge', title='🌊' * 30000, rawOutput='a🌊' * 10000,
                          locations=[{'path': '地' * 30000}]), message('after'))
        page = reader.session_entries_page([self.session()], {})
        self.assert_bounded(page)
        self.assertTrue(page['items'][0]['truncated'])
        self.assertEqual(page['nextCursor'], 1)
        detail = reader.session_entry_detail([self.session()], {'attemptId': 'attempt-one', 'entryIndex': 0})
        self.assert_bounded(detail)
        self.assertGreater(detail['endOffset'], 0)

    def test_oversized_message_preview_is_shortened_to_fit_the_page(self):
        body = '🌊' * 400
        self.record(message(body), message('next'))
        with patch.object(reader, 'MAX_PRIOR_SESSION_RESULT_BYTES', 1600):
            page = reader.session_entries_page([self.session()], {})
            self.assert_bounded(page)
        preview, = page['items']
        self.assertEqual(page['nextCursor'], 1)
        self.assertEqual(preview['textChars'], len(body))
        self.assertTrue(preview['truncated'])
        self.assertTrue(0 < len(preview['textPreview']) < len(body))
        self.assertTrue(body.startswith(preview['textPreview']))

    def test_incomplete_header_has_no_coalesced_entries(self):
        for header in (b'', HEADER[:-1]):
            with self.subTest(header=header):
                self.path.write_bytes(header)
                self.assertEqual(reader.coalesce_entries(self.path), [])

    def test_detail_reassembles_mixed_unicode_without_losing_code_points(self):
        text = '中文\N{WATER WAVE}\N{COMBINING ACUTE ACCENT}\u0001' * 5000
        self.record(message(text))
        args = {'attemptId': 'attempt-one', 'entryIndex': 0, 'limit': 20000}
        pieces = []
        offset = 0
        while offset < len(text):
            part = reader.session_entry_detail([self.session()], {**args, 'offset': offset})
            self.assert_bounded(part)
            self.assertEqual((part['offset'], part['field'], part['totalChars']), (offset, 'text', len(text)))
            self.assertGreater(part['endOffset'], offset)
            self.assertEqual(part['nextOffset'], part['endOffset'] if part['hasMore'] else None)
            pieces.append(part['text'])
            offset = part['endOffset']
        self.assertGreater(len(pieces), 1)
        self.assertEqual(''.join(pieces), text)
        self.assertEqual(reader.session_entry_detail([self.session()], {**args, 'offset': len(text)})['text'], '')

    def test_missing_unlinked_and_corrupt_records(self):
        manifest = [self.session()]
        with self.assertRaises(FileNotFoundError) as missing:
            reader.read_raw_page(self.path, {})
        self.assertNotIsInstance(missing.exception, reader.SessionRecordDeleted)
        self.assertIn('not-recorded', self.assert_error('session-unavailable', reader.session_entries_page, manifest, {}))
        for data in (b'{"protocol":"invalid"}\n', HEADER + b'{"broken":}\n'):
            self.path.write_bytes(data)
            with self.assertRaises(ValueError):
                reader.read_raw_page(self.path, {})
            self.assert_error('invalid-record', reader.session_entries_page, manifest, {})
        self.path.write_bytes(HEADER + b'{"sessionId":"a","update":{"sessionUpdate":"agent_message_chunk",'
                              b'"content":{"type":"text","text":"\\ud800"}}}\n')
        self.assert_error('invalid-record', reader.session_entries_page, manifest, {})
        self.record(message('still in the descriptor'))
        original = reader._event

        def remove(raw):
            self.path.unlink()
            return original(raw)

        with patch.object(reader, '_event', side_effect=remove):
            with self.assertRaises(reader.SessionRecordDeleted):
                reader.read_raw_page(self.path, {})
            self.record(message('still in the descriptor'))
            self.assertIn('deleted', self.assert_error('session-unavailable', reader.session_entries_page, manifest, {}))
