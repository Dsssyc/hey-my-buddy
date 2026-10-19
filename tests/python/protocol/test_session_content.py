"""The exact six ACP v1 display updates, without body-size limits."""
import unittest

from hey_my_buddy.errors import BoardError
from hey_my_buddy.json_codec import decode_strict_json
from hey_my_buddy.protocol.session_content import SESSION_FORMAT, SessionFormat, SessionNotification


class SessionContentTests(unittest.TestCase):
    def test_format_is_exact_and_versions_are_strict(self):
        self.assertEqual(SESSION_FORMAT.to_payload(), {"protocol": "acp", "protocolVersion": 1, "projectionVersion": 1})
        for field, value in (("protocol", "other"), ("protocolVersion", 2),
                             ("protocolVersion", True), ("projectionVersion", 1.0)):
            with self.subTest(field=field, value=value), self.assertRaises(BoardError):
                SessionFormat.from_payload({**SESSION_FORMAT.to_payload(), field: value})
        for field in SESSION_FORMAT.to_payload():
            payload = SESSION_FORMAT.to_payload()
            del payload[field]
            with self.subTest(missing=field), self.assertRaises(BoardError):
                SessionFormat.from_payload(payload)

    def test_six_updates_round_trip(self):
        updates = [
            *({"sessionUpdate": kind, "content": {"type": "text", "text": "正文𝄞"},
               "_meta": {"nativeItemId": "item", "reasoning": "summary", "partIndex": 0}}
              for kind in ("user_message_chunk", "agent_message_chunk", "agent_thought_chunk")),
            {"sessionUpdate": "tool_call", "toolCallId": "call", "title": "command", "kind": "execute",
             "status": "in_progress", "rawInput": {"command": "pwd"}},
            {"sessionUpdate": "tool_call_update", "toolCallId": "call", "status": "completed",
             "rawOutput": {"exitCode": 0}, "content": [{"type": "content", "content": {"type": "text", "text": "out"}},
                 {"type": "diff", "path": "/fixture/file", "oldText": "before", "newText": "after"}],
             "locations": [{"path": "/fixture/file", "line": 2}]},
            {"sessionUpdate": "plan", "entries": [{"content": "step", "priority": "medium", "status": "pending"}]},
        ]
        for update in updates:
            payload = {"sessionId": "attempt", "update": update}
            with self.subTest(kind=update["sessionUpdate"]):
                parsed = SessionNotification.from_payload(payload)
                self.assertEqual(parsed.model_dump(mode="json", by_alias=True, exclude_none=True), payload)

    def test_long_multibyte_content_and_raw_bodies_are_not_bounded(self):
        text = "中文𝄞\n" * 100000
        for update in ({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}},
                       {"sessionUpdate": "tool_call_update", "toolCallId": "call", "rawOutput": {"body": text}}):
            parsed = SessionNotification.from_payload({"sessionId": "attempt", "update": update})
            self.assertEqual(parsed.model_dump(mode="json", by_alias=True, exclude_none=True)["update"], update)

    def test_unknown_fields_v2_updates_and_non_acp_statuses_are_refused(self):
        for update in ({"sessionUpdate": "agent_message", "content": {"type": "text", "text": "v2"}},
                       {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "x", "secret": 1}},
                       {"sessionUpdate": "tool_call_update", "toolCallId": "call", "status": "cancelled"},
                       {"sessionUpdate": "tool_call_update", "toolCallId": "call", "kind": "shell"}):
            with self.subTest(update=update), self.assertRaises(BoardError):
                SessionNotification.from_payload({"sessionId": "attempt", "update": update})

    def test_strict_json_decode_precedes_model_validation(self):
        for raw in ('{"sessionId":"a","sessionId":"b","update":{}}',
                    '{"sessionId":"a","update":{"rawOutput":NaN}}', '{"x":1e999}'):
            with self.assertRaises(ValueError):
                decode_strict_json(raw)
