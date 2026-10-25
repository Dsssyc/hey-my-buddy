"""Offline ACP projection, streaming visibility and append-only session storage."""
import json
import os
from pathlib import Path
from temporary import TemporaryDirectory
import unittest
from unittest.mock import patch

from hey_my_buddy.buddy.harnesses.codex.session_content import CodexSessionProjector
from hey_my_buddy.buddy.harnesses.session_record import SessionRecord
from hey_my_buddy.protocol.session_content import MessageChunk, SessionNotification, TextContent
from buddy.harnesses.session_record_support import notifications
from buddy.harnesses.codex.fixtures.protocol_items import item_frames
from private_acl import assert_private_path


class SessionProjectionTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name)
        self.record = SessionRecord(self.state / "attempt.jsonl", "attempt")
        self.projector = CodexSessionProjector(self.record)
        self.addCleanup(self.projector.close)
        self.projector.record_input("sent input")
        self.projector.observe_root("thread", "turn", "sent input", [])

    def frame(self, method, **params):
        self.projector.observe_notification({"method": method, "params": {
            "threadId": "thread", "turnId": "turn", **params}})

    def item(self, method, kind="agentMessage", item_id="message", **fields):
        self.frame(method, item={"type": kind, "id": item_id, **fields})

    def updates(self):
        lines = notifications(self.record.path)
        for line in lines:
            SessionNotification.from_payload(line)
            self.assertEqual(line["sessionId"], "attempt")
        return [line["update"] for line in lines]

    @staticmethod
    def displayed_blocks(updates):
        """Consume the documented projection format without native knowledge."""
        blocks = {}
        for update in updates:
            content = update.get('content')
            if not content or '_meta' not in update:
                continue
            text = content['text'] if isinstance(content, dict) else ''.join(part['content']['text'] for part in content)
            meta = update['_meta']
            key = (meta['nativeItemId'], meta['contentSegment'])
            blocks[key] = text if meta.get('replace') else blocks.get(key, '') + text
        return blocks

    def test_record_is_not_opened_until_the_first_write(self):
        record = SessionRecord(self.state / 'other' / 'unstarted.jsonl', 'unstarted')
        record.close()
        self.assertFalse(record.path.parent.exists())

    def test_nonextending_completed_text_replaces_the_streamed_block_even_when_shorter(self):
        for item_id, completed in (('revised', 'different'), ('shorter', 'old'), ('empty', '')):
            self.frame('item/agentMessage/delta', itemId=item_id, delta='old streamed text')
            self.projector.flush()
            self.item('item/completed', item_id=item_id, text=completed)
            self.item('item/completed', item_id=item_id, text=completed)
            rows = [row for row in self.updates()[1:] if row['_meta']['nativeItemId'] == item_id]
            self.assertEqual(len(rows), 2)
            self.assertTrue(rows[-1]['_meta']['replace'])
            self.assertEqual(rows[0]['_meta']['contentSegment'], rows[1]['_meta']['contentSegment'])
            self.assertEqual(list(self.displayed_blocks(rows).values()), [completed])

    def test_empty_native_item_ids_are_rejected_at_the_projection_entry(self):
        self.item('item/started', 'commandExecution', '', command='must not normalize')
        self.item('item/completed', 'agentMessage', '', text='must not display')
        self.frame('item/agentMessage/delta', itemId='', delta='must not display')
        self.frame('turn/completed', turn={'id': 'turn', 'items': [
            {'id': '', 'type': 'commandExecution', 'command': 'must not normalize'}]})
        self.assertEqual(len(self.updates()), 1)

    def test_attempt_path_private_permissions_and_jsonl_header(self):
        self.assertEqual(self.record.path, self.state / "attempt.jsonl")
        if os.name != "nt":
            assert_private_path(self, self.record.path, 0o600)
        self.assertEqual(json.loads(self.record.path.read_text(encoding="utf-8").splitlines()[0]),
                         {"protocol": "acp", "protocolVersion": 1, "projectionVersion": 1})
        with patch("os.fsync", side_effect=AssertionError("display writes never fsync")):
            self.item("item/completed", text="visible now")
            self.projector.flush()
        self.assertEqual(self.updates()[-1]["content"]["text"], "visible now")

    def test_contiguous_deltas_coalesce_and_completed_only_adds_tail(self):
        self.item("item/started", text="", phase="commentary")
        for delta in ("你", "好𝄞"):
            self.frame("item/agentMessage/delta", itemId="message", delta=delta)
        self.projector.flush()  # native queue drained, before item completion
        self.assertEqual(self.updates()[-1]["content"]["text"], "你好𝄞")
        self.frame("item/agentMessage/delta", itemId="message", delta="，")
        self.item("item/completed", text="你好𝄞，结束", phase="commentary")
        self.item("item/completed", text="你好𝄞，结束", phase="commentary")
        self.projector.flush()
        chunks = self.updates()[1:]
        self.assertEqual([row["content"]["text"] for row in chunks], ["你好𝄞", "，结束"])
        self.assertEqual(chunks[0]["_meta"]["phase"], "commentary")

    def test_item_switch_preserves_order_and_distinct_segments(self):
        self.frame("item/agentMessage/delta", itemId="a", delta="first")
        self.frame("item/agentMessage/delta", itemId="b", delta="second")
        self.frame("item/agentMessage/delta", itemId="a", delta="third")
        self.item("item/completed", item_id="b", text="second")
        self.frame("item/agentMessage/delta", itemId="a", delta="fourth")
        self.projector.flush()
        self.assertEqual([row["content"]["text"] for row in self.updates()[1:]], ["first", "second", "third", "fourth"])

    def test_completed_only_and_turn_completed_snapshot_are_not_duplicated(self):
        item = {"type": "agentMessage", "id": "only", "text": "complete once"}
        self.frame("item/completed", item=item)
        self.frame("turn/completed", turn={"id": "turn", "items": [item]})
        self.assertEqual([row["content"]["text"] for row in self.updates()[1:]], ["complete once"])

    def test_pre_receipt_content_has_no_role_buffer_limit_and_uses_the_receipts_identity(self):
        from hey_my_buddy.buddy.harnesses.codex.native_run import _Classifier, _EventChain, _RunFacts

        self.projector.record_input('second turn')
        facts = _RunFacts()
        chain = _EventChain(facts, _Classifier(facts), None, self.projector, overflow_raises=False)
        for index in range(300):
            chain.observe({'method': 'item/agentMessage/delta', 'params': {
                'threadId': 'thread', 'turnId': 'second', 'itemId': 'early', 'delta': '字'}})
        self.frame('item/completed', threadId='foreign', turnId='second',
                   item={'id': 'hidden', 'type': 'agentMessage', 'text': 'foreign content'})
        self.projector.observe_root('thread', 'second', 'second turn', [
            {'id': 'early', 'type': 'agentMessage', 'text': '字' * 300}])
        chain.bind(lambda _message: None)
        self.projector.flush()
        self.assertTrue(chain.truncated, 'the role buffer keeps its own existing limit')
        self.assertEqual([row['content']['text'] for row in self.updates()], ['sent input', 'second turn', '字' * 300])

    def test_sent_input_echo_is_deduplicated_by_root_and_item(self):
        self.item("item/started", "userMessage", "user", content=[{"type": "text", "text": "sent input"}])
        self.item("item/completed", "userMessage", "user", content=[{"type": "text", "text": "sent input"}])
        self.projector.flush()
        self.assertEqual(len(self.updates()), 1)
        self.item("item/completed", "userMessage", "other-user", content=[{"type": "text", "text": "other input"}])
        self.projector.flush()
        self.assertEqual(self.updates()[-1]["content"]["text"], "other input")

    def test_steered_input_is_recorded_once_and_its_native_echo_is_deduplicated(self):
        self.projector.record_input('Host inquiry', ('thread', 'turn'))
        self.item('item/completed', 'userMessage', 'inquiry', content=[{'type': 'text', 'text': 'Host inquiry'}])
        self.projector.flush()
        self.assertEqual([row['content']['text'] for row in self.updates()], ['sent input', 'Host inquiry'])

    def test_nontext_blocks_are_type_markers_without_reading_or_parsing_images(self):
        self.item("item/completed", "userMessage", "images", content=[
            {"type": "image", "url": "data:image/png;base64,aGVsbG8="},
            {"type": "image", "url": "https://fixture.invalid/image.png"},
            {"type": "localImage", "path": "/fixture/never-read.png"}])
        self.projector.flush()
        rows = self.updates()[1:]
        self.assertEqual([row['content']['text'] for row in rows], ['[image]', '[image]', '[localImage]'])
        self.assertTrue(all(row['_meta']['nativeItemId'] == 'images' for row in rows))
        self.assertNotIn('base64', json.dumps(rows))

    def test_reasoning_summary_and_text_parts_deduplicate_independently(self):
        self.item("item/started", "reasoning", "reason", summary=[], content=[])
        self.frame("item/reasoning/summaryPartAdded", itemId="reason", summaryIndex=0)
        self.frame("item/reasoning/summaryTextDelta", itemId="reason", summaryIndex=0, delta="summary ")
        self.frame("item/reasoning/summaryTextDelta", itemId="reason", summaryIndex=0, delta="one")
        self.frame("item/reasoning/textDelta", itemId="reason", contentIndex=0, delta="supplied text")
        self.item("item/completed", "reasoning", "reason", summary=["summary one", "summary two"], content=["supplied text"])
        self.projector.flush()
        rows = self.updates()[1:]
        self.assertEqual([row["content"]["text"] for row in rows], ["summary one", "supplied text", "summary two"])
        self.assertEqual([row["_meta"]["reasoning"] for row in rows], ["summary", "text", "summary"])
        self.assertEqual([row["_meta"]["partIndex"] for row in rows], [0, 0, 1])

    def test_only_exact_native_root_identity_is_projected(self):
        for params in ({"threadId": "other"}, {"turnId": "old"}, {"turnId": None}, {"threadId": None}):
            self.frame("item/completed", item={"id": "foreign", "type": "agentMessage", "text": "hidden"}, **params)
        self.projector.flush()
        self.assertEqual(len(self.updates()), 1)

    def test_raw_account_quota_usage_and_unknown_notifications_are_excluded(self):
        for method in ("rawResponse/completed", "rawResponseItem/completed", "account/updated", "account/rateLimits/updated",
                       "thread/tokenUsage/updated", "unknown/notification"):
            self.frame(method, item={"id": "raw", "type": "agentMessage", "text": "secret raw"}, secret="account")
        self.projector.flush()
        self.assertEqual(len(self.updates()), 1)

    def test_offline_plan_subagent_and_unknown_items_are_typed_markers(self):
        for name in ("plan", "subAgentActivity", "futureThreadItem"):
            for method, item in item_frames(name):
                self.frame(method, item={**item, "id": name})
        self.projector.flush()
        rows = self.updates()[1:]
        self.assertEqual([row["content"]["text"] for row in rows],
                         ["[plan]\nproposed plan", "[subAgentActivity]", "[futureThreadItem]"])
        self.assertTrue(all(row["_meta"]["marker"] for row in rows))
        self.assertTrue(all(row["sessionUpdate"] == "agent_message_chunk" for row in rows))

    def test_unknown_item_keeps_only_readable_text_and_native_type(self):
        self.item("item/completed", "future", "future", text="visible", encrypted="secret", config={"key": "secret"})
        self.projector.flush()
        row = self.updates()[-1]
        self.assertEqual(row["content"]["text"], "[future]\nvisible")
        self.assertEqual(row["_meta"]["nativeType"], "future")
        self.assertNotIn("secret", json.dumps(row))

    def test_recorded_output_hook_and_review_samples_retain_their_readable_bodies(self):
        for name in ('functionCallOutput', 'hookPrompt', 'enteredReviewMode', 'exitedReviewMode'):
            for method, item in item_frames(name):
                self.frame(method, item={**item, 'id': name})
        self.assertEqual([row['content']['text'] for row in self.updates()[1:]],
                         ['[functionCallOutput]\ndone', '[hookPrompt]\nhook prompt',
                          '[enteredReviewMode]\nreview task', '[exitedReviewMode]\nreview done'])

    def test_typed_function_output_uses_only_a_native_correlated_call(self):
        self.item('item/started', 'commandExecution', 'call', command='pwd')
        self.frame('item/commandExecution/outputDelta', itemId='call', delta='done')
        self.item('item/completed', 'functionCallOutput', 'call', name='shell', output='done')
        self.projector.flush()
        rows = self.updates()[1:]
        self.assertEqual(''.join(part['content']['text'] for row in rows for part in row.get('content', [])), 'done')
        self.assertEqual(rows[-1]['rawOutput'], {'output': 'done'})
        self.assertEqual(rows[-1]['toolCallId'], 'call')

    def test_revised_native_plan_text_remains_a_marker_without_inventing_plan_entries(self):
        self.item('item/started', 'plan', 'plan', text='first proposal')
        self.item('item/updated', 'plan', 'plan', text='revised proposal')
        self.item('item/completed', 'plan', 'plan', text='revised proposal')
        rows = self.updates()[1:]
        self.assertTrue(all(row['_meta']['replace'] for row in rows))
        self.assertEqual(list(self.displayed_blocks(rows).values()), ['[plan]\nrevised proposal'])

    def test_command_output_deltas_and_completed_cumulative_output_render_once(self):
        self.item("item/started", "commandExecution", "command", command="pwd", cwd="/fixture", status="inProgress")
        for delta in ("/fi", "xture\n"):
            self.frame("item/commandExecution/outputDelta", itemId="command", delta=delta)
        self.projector.flush()
        self.item("item/completed", "commandExecution", "command", command="pwd", cwd="/fixture", status="completed",
                  aggregatedOutput="/fixture\nend", exitCode=0)
        self.projector.flush()
        rows = self.updates()[1:]
        self.assertEqual(rows[0]["kind"], "execute")
        self.assertEqual(rows[0]["rawInput"], {"command": "pwd", "cwd": "/fixture"})
        self.assertEqual(list(self.displayed_blocks(rows).values()), ["/fixture\nend"])
        self.assertTrue(all(not row['_meta'].get('replace') for row in rows if row.get('content')))
        self.assertTrue(any(row.get("status") == "completed" for row in rows))

    def test_file_changes_keep_locations_diff_and_input_output(self):
        self.item("item/completed", "fileChange", "edit", status="completed",
                  changes=[{"path": "/fixture/file", "kind": "update", "diff": "@@\n-old\n+new"}])
        self.projector.flush()
        rows = self.updates()[1:]
        self.assertEqual(rows[0]["kind"], "edit")
        self.assertEqual(rows[0]["locations"], [{"path": "/fixture/file"}])
        self.assertEqual(rows[1]["content"][0]["content"]["text"], "@@\n-old\n+new")
        self.assertEqual(rows[0]["rawOutput"]["changes"][0]["path"], "/fixture/file")

    def test_mcp_tool_kind_uses_l5_and_retains_arguments_and_result(self):
        self.item("item/started", "mcpToolCall", "mcp", server="server", tool="read", arguments={"path": "file"})
        self.item("item/completed", "mcpToolCall", "mcp", tool="read", status="completed", result={"content": ["result"]})
        rows = self.updates()[1:]
        self.assertEqual(rows[0]["kind"], "other")
        self.assertEqual(rows[0]["rawInput"]["arguments"], {"path": "file"})
        self.assertEqual(rows[-1]["rawOutput"]["result"], {"content": ["result"]})

    def test_cancelled_and_unrecognized_native_status_do_not_claim_completion(self):
        self.item("item/completed", "commandExecution", "cancel", status="interrupted")
        self.item("item/completed", "commandExecution", "unknown", status="future")
        self.projector.flush()
        rows = self.updates()[1:]
        self.assertEqual(rows[0]["status"], "failed")
        self.assertEqual(rows[1]["content"][0]["content"]["text"], "Codex: interrupted")
        self.assertNotIn("status", rows[2])

    def test_unassociated_diff_is_a_marker_and_associated_diff_uses_native_call(self):
        self.frame("turn/diff/updated", diff="standalone diff")
        self.item("item/started", "fileChange", "edit")
        self.frame("turn/diff/updated", itemId="edit", diff="associated diff")
        self.projector.flush()
        rows = self.updates()[1:]
        self.assertEqual(rows[0]["_meta"]["nativeType"], "turn/diff/updated")
        self.assertEqual(rows[0]['_meta']['nativeItemId'], 'turn/diff/updated:turn')
        self.assertTrue(rows[0]["_meta"]["marker"])
        self.assertEqual(rows[-1]["toolCallId"], "edit")

    def test_changed_diff_snapshots_are_preserved_without_treating_them_as_text_deltas(self):
        for diff in ('-old\n+first', '-old\n+second', '-old\n+second'):
            self.frame('turn/diff/updated', diff=diff)
        self.item('item/started', 'fileChange', 'edit', changes=[{'path': '/fixture/a', 'diff': '-a\n+b'}])
        self.item('item/completed', 'fileChange', 'edit', changes=[{'path': '/fixture/a', 'diff': '-a\n+c'}])
        self.projector.flush()
        rows = self.updates()[1:]
        self.assertTrue(all(row['_meta']['replace'] for row in rows if row.get('content')))
        self.assertEqual(list(self.displayed_blocks(rows).values()),
                         ['[turn/diff/updated]\n-old\n+second', '-a\n+c'])

    def test_duration_is_a_result_fact(self):
        self.item('item/completed', 'sleep', 'sleep', durationMs=17)
        row = self.updates()[-1]
        self.assertNotIn('rawInput', row)
        self.assertEqual(row['rawOutput'], {'durationMs': 17})

    def test_long_multibyte_content_survives_close(self):
        text = "中文𝄞\n" * 100000
        self.frame("item/agentMessage/delta", itemId="long", delta=text)
        self.projector.close()
        rows = self.updates()
        self.assertEqual(rows[-1]["content"]["text"], text)
        self.assertEqual(len(notifications(self.record.path)), 2)

    def test_new_attempt_on_same_native_thread_has_a_separate_record(self):
        second = SessionRecord(self.state / "next-attempt.jsonl", "next-attempt")
        projector = CodexSessionProjector(second)
        projector.record_input("new input")
        projector.observe_root("thread", "next-turn", "new input", [])
        projector.close()
        lines = notifications(second.path)
        self.assertEqual(lines[0]["sessionId"], "next-attempt")
        self.assertNotEqual(second.path, self.record.path)
        self.assertEqual(len(self.updates()), 1)

    def test_reopened_record_appends_and_refuses_links(self):
        original = notifications(self.record.path)
        reopened = SessionRecord(self.record.path, "attempt")
        try:
            reopened.append(MessageChunk(session_update="user_message_chunk", content=TextContent(text="next run")))
        finally:
            reopened.close()
        self.assertEqual(notifications(self.record.path)[:len(original)], original)
        self.assertEqual(self.updates()[-1]["content"]["text"], "next run")
        linked_path = self.state / "linked.jsonl"
        linked_path.symlink_to(self.record.path)
        linked = SessionRecord(linked_path, "linked")
        linked.append(MessageChunk(session_update='user_message_chunk', content=TextContent(text='input')))
        self.assertEqual(linked.failure['code'], 'session-record-write-failed')
        self.assertEqual(notifications(self.record.path)[-1]['sessionId'], 'attempt')

    def test_io_failure_disables_future_writes_and_explicitly_continued_writer(self):
        self.frame("item/agentMessage/delta", itemId="message", delta="pending")
        with patch.object(self.record, '_open', side_effect=OSError('controlled write failure')):
            self.record.close()
            self.projector.flush()
        self.assertEqual(self.record.failure['params']['message'], 'controlled write failure')
        count = len(notifications(self.record.path))
        self.record.append(MessageChunk(session_update='user_message_chunk', content=TextContent(text='ignored')))
        self.assertEqual(len(notifications(self.record.path)), count)
        reopened = SessionRecord(self.record.path, 'attempt', failure=self.record.failure)
        reopened.append(MessageChunk(session_update='user_message_chunk', content=TextContent(text='ignored')))
        self.assertEqual(reopened.failure, self.record.failure)
        self.assertIsNone(reopened._fd)
        self.assertEqual(len(notifications(self.record.path)), count)
        independent = SessionRecord(self.record.path, 'attempt')
        independent.append(MessageChunk(session_update='user_message_chunk', content=TextContent(text='fresh run')))
        independent.close()
        self.assertIsNone(independent.failure)
        self.assertEqual(len(notifications(self.record.path)), count + 1)


class SessionRecordRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    @staticmethod
    def _update(text):
        return MessageChunk(session_update="user_message_chunk", content=TextContent(text=text))

    def test_reopen_discards_crash_tail_and_appends_readable_line(self):
        path = self.root / "attempt.jsonl"
        first = SessionRecord(path, "attempt")
        first.append(self._update("first"))
        first.close()
        with path.open("ab") as file:
            file.write(b'{"sessionId":"attempt","update":')
        resumed = SessionRecord(path, "attempt")
        resumed.append(self._update("second"))
        resumed.close()
        self.assertIsNone(resumed.failure)
        self.assertEqual([row["update"]["content"]["text"] for row in notifications(path)],
                         ["first", "second"])
        self.assertTrue(path.read_bytes().endswith(b"\n"))

    def test_leaf_link_is_refused_and_private_parent_alias_is_accepted(self):
        outside = self.root / "outside"
        outside.mkdir()
        target = outside / "sentinel.jsonl"
        target.write_text("sentinel", encoding="utf-8", newline="\n")
        linked_file = self.root / "linked.jsonl"
        linked_file.symlink_to(target)
        record = SessionRecord(linked_file, "attempt")
        record.append(self._update("ignored"))
        self.assertEqual(record.failure["code"], "session-record-write-failed")
        linked_parent = self.root / "linked-parent"
        linked_parent.symlink_to(outside, target_is_directory=True)
        record = SessionRecord(linked_parent / "attempt.jsonl", "attempt")
        record.append(self._update("through parent alias"))
        record.close()
        self.assertIsNone(record.failure)
        self.assertEqual(notifications(outside / "attempt.jsonl")[-1]["update"]["content"]["text"], "through parent alias")
        self.assertEqual(target.read_text(encoding="utf-8"), "sentinel")
