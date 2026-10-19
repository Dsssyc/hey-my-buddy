"""Offline stream-json display records; synthetic thinking is not native proof."""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from hey_my_buddy.buddy.harnesses.claude.session_content import ClaudeSessionProjector
from hey_my_buddy.buddy.harnesses.session_record import SessionRecord, SessionRecordWriteError
from hey_my_buddy.private_dirs import attempt_root
from buddy.harnesses.claude.test_native_run import NativeRunCase, READ_SCHEMA


class MemoryRecord:
    def __init__(self):
        self.events = []

    def append(self, update):
        self.events.append(update.model_dump(by_alias=True, exclude_none=True))


class ProjectorTests(unittest.TestCase):
    def setUp(self):
        self.record = MemoryRecord()
        self.projector = ClaudeSessionProjector(self.record)
        self.projector.start_prompt("root", "prompt 中文")

    def emit(self, kind, *, session="root", **fields):
        self.projector.observe_frame({"type": kind, "session_id": session, **fields})

    def assistant(self, blocks, *, item="message", **fields):
        self.emit("assistant", message={"id": item, "content": blocks}, **fields)

    def stream(self, kind, **fields):
        self.emit("stream_event", event={"type": kind, **fields})

    def test_complete_blocks_reasoning_tools_and_native_results(self):
        self.assistant([{"type": "thinking", "thinking": "supplied thought 中文"},
                        {"type": "text", "text": "answer"},
                        {"type": "tool_use", "id": "call", "name": "Read",
                         "input": {"file_path": "src/a.py"}}])
        self.emit("user", message={"content": [{"type": "tool_result", "tool_use_id": "call",
            "content": [{"type": "text", "text": "file 中文"}, {"type": "image", "source": {}}]}]})
        events = self.record.events
        self.assertEqual({e["sessionUpdate"] for e in events}, {"user_message_chunk", "agent_message_chunk",
            "agent_thought_chunk", "tool_call", "tool_call_update"})
        thought = events[1]
        self.assertEqual((thought["content"]["text"], thought["_meta"]["reasoning"]),
                         ("supplied thought 中文", "text"))
        call = next(e for e in events if e["sessionUpdate"] == "tool_call")
        self.assertEqual((call["kind"], call["rawInput"]), ("read", {"file_path": "src/a.py"}))
        self.assertTrue(any(e.get("status") == "completed" and "rawOutput" in e for e in events))
        self.assertEqual(events[-1]["content"][0]["content"]["text"], "[image]")

    def test_native_categories_and_tool_result_failure(self):
        for index, (name, category) in enumerate((("Read", "read"), ("Glob", "search"),
                ("Bash", "execute"), ("Write", "edit"), ("WebFetch", "fetch"), ("mcp__read", "other"))):
            self.assistant([{"type": "tool_use", "id": f"call-{index}", "name": name, "input": {}}],
                           item=f"message-{index}")
            self.assertEqual(self.record.events[-1]["kind"], category)
        self.emit("user", message={"content": [{"type": "tool_result", "tool_use_id": "call-0",
                                                "is_error": True, "content": "failure"}]})
        self.assertEqual(self.record.events[-2]["status"], "failed")

    def test_stream_and_completed_snapshots_share_keys_and_do_not_duplicate(self):
        self.stream("message_start", message={"id": "message"})
        self.stream("content_block_start", index=0, content_block={"type": "text", "text": ""})
        self.stream("content_block_delta", index=0, delta={"type": "text_delta", "text": "ab"})
        self.stream("content_block_delta", index=0, delta={"type": "text_delta", "text": "c"})
        self.stream("content_block_start", index=1, content_block={"type": "thinking", "thinking": ""})
        self.stream("content_block_delta", index=1, delta={"type": "thinking_delta", "thinking": "thought"})
        self.stream("content_block_delta", index=1, delta={"type": "signature_delta", "signature": "opaque"})
        self.stream("message_stop")
        blocks = [{"type": "text", "text": "abc"}, {"type": "thinking", "thinking": "thought"}]
        self.assistant(blocks)
        count = len(self.record.events)
        self.assistant(blocks)
        self.assertEqual(len(self.record.events), count)
        self.assertEqual([e["content"]["text"] for e in self.record.events], ["prompt 中文", "ab", "c", "thought"])
        self.assistant([{"type": "text", "text": "revised"}])
        self.assertTrue(self.record.events[-1]["_meta"]["replace"])
        self.assertEqual(self.record.events[-1]["content"]["text"], "")  # Removed thought segment.
        self.emit("result", result="not display text", structured_output={"outcome": "not text"})
        self.assistant([{"type": "text", "text": "unadmitted followup"}])
        self.assertEqual(len(self.record.events), count + 2)

    def test_stream_tool_start_and_complete_input_deduplicate(self):
        block = {"type": "tool_use", "id": "call", "name": "Read", "input": {}}
        self.stream("message_start", message={"id": "message"})
        self.stream("content_block_start", index=0, content_block=block)
        self.stream("content_block_delta", index=0, delta={"type": "input_json_delta", "partial_json": '{"path":'})
        self.assistant([{**block, "input": {"path": "a.py"}}])
        count = len(self.record.events)
        self.assistant([{**block, "input": {"path": "a.py"}}])
        self.assertEqual(len(self.record.events), count)
        self.assertEqual(sum(e["sessionUpdate"] == "tool_call" for e in self.record.events), 1)
        self.assertEqual(self.record.events[-1]["rawInput"], {"path": "a.py"})

    def test_unknown_content_is_data_and_no_plan_is_invented(self):
        for index, block in enumerate((None, {"type": ["bad"]}, {"type": "redacted_thinking", "data": "secret"},
                {"type": "tool_use", "id": [], "name": "Read"}, {"type": "plan", "entries": []})):
            self.assistant([block], item=f"message-{index}")
        markers = self.record.events[1:]
        self.assertTrue(all(e["_meta"]["marker"] for e in markers))
        self.assertNotIn("secret", json.dumps(markers))
        self.assertFalse(any(e["sessionUpdate"] == "plan" for e in markers))

    def test_sent_inquiry_is_recorded_once_and_unrelated_native_input_is_kept(self):
        self.projector.record_input("early inquiry")
        self.assertEqual(self.record.events, [])
        self.assistant([{"type": "text", "text": "working"}])
        self.projector.record_input("live inquiry")
        for text in ("prompt 中文", "early inquiry", "live inquiry", "native input"):
            self.emit("user", message={"content": [{"type": "text", "text": text}]})
        self.assertEqual([e["content"]["text"] for e in self.record.events
                          if e["sessionUpdate"] == "user_message_chunk"],
                         ["prompt 中文", "early inquiry", "live inquiry", "native input"])

    def test_tool_output_snapshots_append_replace_remove_and_deduplicate(self):
        self.assistant([{"type": "tool_use", "id": "call", "name": "Read", "input": {}}])
        def result(content):
            self.emit("user", message={"content": [{"type": "tool_result", "tool_use_id": "call",
                                                    "content": content}]})
        result("ab")
        result("abc")
        self.assertEqual(self.record.events[-1]["content"][0]["content"]["text"], "c")
        count = len(self.record.events)
        result("abc")
        self.assertEqual(len(self.record.events), count)
        result([{"type": "text", "text": "revised"}, {"type": "text", "text": "second"}])
        self.assertTrue(self.record.events[-2]["_meta"]["replace"])
        result("revised")
        self.assertEqual(self.record.events[-1]["content"][0]["content"]["text"], "")
        self.assertTrue(self.record.events[-1]["_meta"]["replace"])

    def test_scope_and_start_boundary_filter_foreign_history_and_child_streams(self):
        self.projector = ClaudeSessionProjector(self.record)
        self.assistant([{"type": "text", "text": "before send"}])
        self.projector.start_prompt("root", "prompt 中文")
        self.emit("system", subtype="init")
        self.emit("user", message={"content": [{"type": "text", "text": "prompt 中文"}]})
        self.assistant([{"type": "text", "text": "foreign"}], session="other")
        self.assistant([{"type": "text", "text": "child"}], parent_tool_use_id="child-call")
        self.assertEqual(self.record.events, [])
        self.assistant([{"type": "text", "text": "admitted"}])
        self.assertEqual(len(self.record.events), 2)

    def test_versioned_redacted_sample_proves_text_only_not_thinking_absence(self):
        sample = json.loads((Path(__file__).with_name("fixtures") / "native-usage-claude.json").read_text())
        self.assertEqual(sample["harnessVersion"], "2.1.284")
        self.projector.start_prompt("session-fixture-0001", "input")
        for frame in sample["assistantFrames"]:
            self.projector.observe_frame(frame)
        rendered = {}
        for event in self.record.events:
            if event["sessionUpdate"] != "agent_message_chunk":
                continue
            meta = event["_meta"]
            key = (meta["nativeItemId"], meta["contentSegment"])
            rendered[key] = ("" if meta.get("replace") else rendered.get(key, "")) + event["content"]["text"]
        self.assertEqual([value for value in rendered.values() if value and value != "[reasoning]"],
                         ["Read the fixture file first.", "Fixture final answer: the bounded work is done."])
        self.assertFalse(any(e["sessionUpdate"] == "agent_thought_chunk" for e in self.record.events))
        from hey_my_buddy.buddy.harnesses.claude.session_omitted import OMITTED
        self.assertEqual(OMITTED, ())


class RunRecordTests(NativeRunCase):
    def path(self, request):
        return attempt_root(Path(os.environ["HMB_STATE_DIR"]), "claude",
                            request.identity.task_id, request.identity.attempt_id) / "session.jsonl"

    def events(self, request):
        lines = [json.loads(line) for line in self.path(request).read_text().splitlines()]
        self.assertEqual(lines[0], {"protocol": "acp", "protocolVersion": 1, "projectionVersion": 1})
        self.assertTrue(all(line["sessionId"] == request.identity.attempt_id for line in lines[1:]))
        return [line["update"] for line in lines[1:]]

    def test_worker_and_router_share_the_stream_projection(self):
        for scope, schema in (("write", None), ("read", READ_SCHEMA)):
            with self.subTest(scope=scope):
                self.fixture_case("session-content")
                request = self.request(scope=scope, schema=schema)
                result = self.execute(request)
                self.assertEqual(result.end.status, "ok", result.end.message)
                events = self.events(request)
                self.assertEqual(events[0]["content"]["text"], request.input_text)
                self.assertTrue(any(e["sessionUpdate"] == "agent_message_chunk" for e in events))
                self.assertNotIn("fixture final text", json.dumps(events))
                self.assertEqual(sum(e["sessionUpdate"] == "tool_call" for e in events), 1)
                self.assertTrue(any(e["sessionUpdate"] == "agent_thought_chunk" for e in events))
                self.assertTrue(any(e.get("rawOutput") == "fixture file 中文" for e in events))

    def test_same_attempt_run_appends_with_a_distinct_native_session_segment(self):
        request = self.request()
        first = self.execute(request)
        before = self.path(request).read_bytes()
        next_run = self.request()
        next_run = next_run.model_copy(update={"identity": next_run.identity.model_copy(update={
            "task_id": request.identity.task_id, "attempt_id": request.identity.attempt_id})})
        second = self.execute(next_run)
        self.assertEqual((first.end.status, second.end.status), ("ok", "ok"))
        self.assertTrue(self.path(request).read_bytes().startswith(before))
        prompts = [e for e in self.events(request) if e["sessionUpdate"] == "user_message_chunk"]
        self.assertEqual(len(prompts), 2)
        self.assertNotEqual(prompts[0]["_meta"]["contentSegment"], prompts[1]["_meta"]["contentSegment"])

    def test_startup_failures_and_rejected_pre_user_output_leave_no_record(self):
        for case in ("initialize-error", "early-result", "empty-catalog"):
            with self.subTest(case=case):
                self.fixture_case(case)
                request = self.request()
                result = self.execute(request)
                self.assertEqual(result.end.status, "error")
                self.assertFalse(self.path(request).exists())

    def test_write_failure_keeps_observed_frame_identity_and_actual_stop_evidence(self):
        request = self.request()
        with mock.patch.object(SessionRecord, "append", side_effect=SessionRecordWriteError()):
            result = self.execute(request)
        self.assertEqual((result.end.status, result.end.reason_code), ("error", "session-record-write-failed"))
        self.assertEqual(result.identity, request.identity)
        self.assertTrue(result.model_started)
        self.assertIsNotNone(result.native_identity.session_id)
        self.assertIsNotNone(result.last_assistant_message)
        self.assertEqual(result.stop_evidence.native.group_state, "gone")

    def test_flush_and_close_errors_are_run_failures_with_complete_results(self):
        for method, error in (("_write", OSError("private source path")),
                              ("close", SessionRecordWriteError())):
            with self.subTest(method=method):
                request = self.request()
                original_close = SessionRecord.close
                if method == "close":
                    def close(record):
                        original_close(record)
                        raise error
                    patch = mock.patch.object(SessionRecord, "close", close)
                else:
                    patch = mock.patch.object(SessionRecord, method, side_effect=error)
                with patch:
                    result = self.execute(request)
                self.assertEqual((result.end.status, result.end.reason_code), ("error", "session-record-write-failed"))
                self.assertEqual(result.stop_evidence.native.group_state, "gone")
                self.assertNotIn("private source path", result.end.message)
                if method == "close":
                    self.assertIsNotNone(result.value)
                    self.assertIsNotNone(result.tool_evidence)
                    self.assertTrue(result.evidence_refs)
