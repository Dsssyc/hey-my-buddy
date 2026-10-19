"""Display projection of this invocation's Claude Code stream-json frames.

Complete assistant blocks are snapshots. Optional native stream events append
text to those same message/block keys; enabling partial output is not our job.
Result summaries and structured outcomes are never a second source of text.
"""
from __future__ import annotations

from ....protocol.session_content import (
    MessageChunk, NativeMeta, TextContent, ToolCall, ToolCallUpdate, ToolContent,
)
from ....protocol.tool_evidence import normalize_tool_event
from ..session_record import SessionRecord


def _identity(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 512


class ClaudeSessionProjector:
    def __init__(self, record: SessionRecord):
        self.record = record
        self.session_id: str | None = None
        self.inputs: list[str] = []
        self.echoes: list[str] = []
        self.started = False
        self.finished = False
        self.ordinal = 0
        self.stream_item: str | None = None
        self.text: dict[tuple[str, str], str] = {}
        self.tools: dict[str, dict] = {}

    def start_prompt(self, session_id: str, input_text: str) -> None:
        self.session_id = session_id
        self.record_input(input_text)

    def record_input(self, input_text: str) -> None:
        """Record an input only after its native transport send succeeded."""
        self.inputs.append(input_text)
        self.echoes.append(input_text)
        if self.started:
            self._input(len(self.inputs) - 1, input_text)

    def _input(self, index: int, text: str) -> None:
        self.record.append(MessageChunk(session_update="user_message_chunk",
            content=TextContent(text=text), meta=self._meta(f"prompt-{index}", "input")))

    def _start(self) -> None:
        if not self.started:
            for index, text in enumerate(self.inputs):
                self._input(index, text)
            self.started = True

    def _meta(self, item: str, segment: str, **fields) -> NativeMeta:
        return NativeMeta(native_item_id=item, content_segment=f"{self.session_id}/{segment}", **fields)

    def observe_frame(self, frame: dict) -> None:
        if self.session_id is None or self.finished or frame.get("parent_tool_use_id"):
            return
        session = frame.get("session_id")
        if session is not None and session != self.session_id:
            return
        kind = frame.get("type")
        if kind == "result":
            self.finished = True
            return
        if kind == "stream_event":
            event = frame.get("event")
            if isinstance(event, dict):
                self._stream(event)
            return
        if kind not in ("assistant", "user"):
            return
        message = frame.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            if kind == "assistant":
                self._marker(self._item(frame, message), "content", "unknown-content")
            return
        # A native user frame can be a tool result or a real input echo; a
        # startup echo alone must not create a record for a failed model start.
        if kind == "user" and not self.started:
            return
        item = self._item(frame, message)
        for index, block in enumerate(content):
            self._block(item, index, block, kind)
        if kind == "assistant":
            for native_item, segment in list(self.text):
                if native_item == item and segment.startswith("block/") and int(segment[6:]) >= len(content):
                    self._text(item, segment, "", "agent_message_chunk")

    def _item(self, frame: dict, message: object) -> str:
        item = message.get("id") if isinstance(message, dict) else None
        if _identity(item):
            return item
        if _identity(frame.get("uuid")):
            return frame["uuid"]
        self.ordinal += 1
        return f"frame-{self.ordinal}"

    def _text(self, item: str, segment: str, value: str, kind: str,
              *, snapshot: bool = True, marker: bool = False) -> None:
        key = (item, segment)
        before = self.text.get(key, "")
        after = value if snapshot else before + value
        if after == before:
            return
        self._start()
        replace = snapshot and not after.startswith(before)
        self.record.append(MessageChunk(session_update=kind,
            content=TextContent(text=after if replace else after[len(before):]),
            meta=self._meta(item, segment, replace=True if replace else None,
                marker=True if marker else None,
                reasoning="text" if kind == "agent_thought_chunk" else None)))
        self.text[key] = after

    def _marker(self, item: str, segment: str, label: str) -> None:
        self._text(item, segment, f"[{label}]", "agent_message_chunk", marker=True)

    def _block(self, item: str, index: int, block: object, role: str) -> None:
        segment = f"block/{index}"
        if not isinstance(block, dict):
            self._marker(item, segment, "unknown-content")
            return
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            # The original input is already recorded at the admitted boundary.
            if role == "user" and block["text"] in self.echoes:
                self.echoes.remove(block["text"])
                return
            self._text(item, segment, block["text"],
                       "user_message_chunk" if role == "user" else "agent_message_chunk")
        elif role == "assistant" and kind == "thinking" and isinstance(block.get("thinking"), str):
            self._text(item, segment, block["thinking"], "agent_thought_chunk")
        elif role == "assistant" and kind == "tool_use":
            self._tool(item, segment, block)
        elif role == "user" and kind == "tool_result":
            self._result(item, segment, block)
        else:
            self._marker(item, segment, kind if isinstance(kind, str) and kind else "unknown-content")

    def _tool(self, item: str, segment: str, block: dict) -> None:
        call_id, name = block.get("id"), block.get("name")
        if not _identity(call_id) or not _identity(name):
            self._marker(item, segment, "unidentified-tool-call")
            return
        self._start()
        event = normalize_tool_event("claude", {"nativeIdentity": {"sessionId": self.session_id},
            "callId": call_id, "toolName": name, "phase": "start"})
        before = self.tools.get(call_id, {})
        payload = {"title": name, "kind": event["category"],
                   "status": before.get("status", "in_progress")}
        if "input" in block:
            payload["raw_input"] = block["input"]
        changed = {key: value for key, value in payload.items() if key not in before or before[key] != value}
        if changed:
            update = ToolCall if not before else ToolCallUpdate
            self.record.append(update(tool_call_id=call_id, meta=self._meta(call_id, "tool"), **changed))
        self.tools[call_id] = {**before, **payload}

    def _result(self, item: str, segment: str, block: dict) -> None:
        call_id = block.get("tool_use_id")
        if not _identity(call_id):
            self._marker(item, segment, "unidentified-tool-result")
            return
        before = self.tools.get(call_id, {})
        payload = {"status": "failed" if block.get("is_error") is True else "completed"}
        if "content" in block:
            payload["raw_output"] = block["content"]
        changed = {key: value for key, value in payload.items() if key not in before or before[key] != value}
        if changed:
            self.record.append(ToolCallUpdate(tool_call_id=call_id, meta=self._meta(call_id, "tool"), **changed))
        self.tools[call_id] = {**before, **payload}
        content = block.get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        if not isinstance(blocks, list):
            blocks = [{"type": "unknown-tool-content"}]
        indices = {int(segment[7:]) for item_id, segment in self.text
                   if item_id == call_id and segment.startswith("output/")}
        for index in sorted(indices | set(range(len(blocks)))):
            value = self._tool_text(blocks[index]) if index < len(blocks) else ""
            key = (call_id, f"output/{index}")
            previous = self.text.get(key, "")
            if value != previous:
                replace = not value.startswith(previous)
                self.record.append(ToolCallUpdate(tool_call_id=call_id,
                    content=[ToolContent(content=TextContent(text=value if replace else value[len(previous):]))],
                    meta=self._meta(*key, replace=True if replace else None)))
                self.text[key] = value

    @staticmethod
    def _tool_text(part: object) -> str:
        if isinstance(part, dict):
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                return part["text"]
            label = part.get("type")
            if isinstance(label, str) and label:
                return f"[{label}]"
        return "[unknown-tool-content]"

    def _stream(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "message_start":
            message = event.get("message")
            self.stream_item = message.get("id") if isinstance(message, dict) and _identity(message.get("id")) else None
        elif kind == "message_stop":
            self.stream_item = None
        elif self.stream_item is not None:
            index = event.get("index")
            if type(index) is not int or index < 0:
                return
            if kind == "content_block_start":
                block = event.get("content_block")
                self._block(self.stream_item, index, block, "assistant")
            elif kind == "content_block_delta":
                delta = event.get("delta")
                if not isinstance(delta, dict):
                    self._marker(self.stream_item, f"block/{index}", "unknown-delta")
                    return
                delta_kind = delta.get("type")
                for native, field, update in (("text_delta", "text", "agent_message_chunk"),
                                               ("thinking_delta", "thinking", "agent_thought_chunk")):
                    if delta_kind == native and isinstance(delta.get(field), str):
                        self._text(self.stream_item, f"block/{index}", delta[field], update, snapshot=False)
                        return
                # Tool input is supplied by the complete assistant snapshot;
                # signatures carry no display text.
                if delta_kind not in ("input_json_delta", "signature_delta"):
                    self._marker(self.stream_item, f"block/{index}",
                                 delta_kind if isinstance(delta_kind, str) and delta_kind else "unknown-delta")

    def close(self) -> None:
        self.record.close()
