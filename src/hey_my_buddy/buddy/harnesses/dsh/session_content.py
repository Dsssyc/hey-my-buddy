"""Project only admitted prompt notifications, without reading native history.

DSH ACP text chunks have no item id. Consecutive chunks of the same kind
therefore share a display item, scoped by the invocation and prompt ordinal.
Tool content is an ACP snapshot; only its extension is appended, and revisions
replace the same content segment. Unrecognized native content is a type marker.
"""
from __future__ import annotations

from ....protocol.session_content import (
    MessageChunk, NativeMeta, Plan, PlanEntry, TextContent,
    ToolCall, ToolCallUpdate, ToolContent, ToolLocation,
)
from ....protocol.tool_evidence import normalize_tool_event
from ..session_record import SessionRecord

_MESSAGES = {"user_message_chunk", "agent_message_chunk", "agent_thought_chunk"}
_STATUSES = {"pending", "in_progress", "completed", "failed"}
_PRIORITIES = {"high", "medium", "low"}


class DshSessionProjector:
    def __init__(self, record: SessionRecord):
        self.record = record
        self.prompts: dict[str, str] = {}
        self.inputs: dict[str, str] = {}
        self.started: set[str] = set()
        self.user_chunks: dict[str, list] = {}
        self.items: dict[str, tuple[str, str]] = {}
        self.ordinal = 0
        self.tools: dict[tuple[str, str], dict] = {}
        self.snapshots: dict[tuple[str, str, int], str] = {}
        self.plans: dict[str, list] = {}

    def start_prompt(self, session_id: str, input_text: str, prompt_id: str) -> None:
        self.prompts[session_id] = prompt_id
        self.inputs[session_id] = input_text

    def observe_notification(self, message: dict) -> None:
        if not isinstance(message, dict) or message.get("method") != "session/update":
            return
        params = message.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("sessionId"), str):
            return
        session = params["sessionId"]
        if session not in self.prompts:
            return
        update = params.get("update")
        if not isinstance(update, dict):
            return
        kind = update.get("sessionUpdate")
        if not isinstance(kind, str) or kind not in (*_MESSAGES, "tool_call", "tool_call_update", "plan"):
            return
        # Opening/configuration and a failed prompt send never create a record.
        # Keep the sent input until the model first supplies display content.
        if session not in self.started:
            if kind == "user_message_chunk":
                self.user_chunks.setdefault(session, []).append(update.get("content"))
                return
            self.started.add(session)
            self.record.append(MessageChunk(session_update="user_message_chunk",
                content=TextContent(text=self.inputs[session]), meta=NativeMeta(
                    native_item_id="prompt", content_segment=self.prompts[session] + "/input")))
            chunks = self.user_chunks.pop(session, [])
            echoed = all(isinstance(chunk, dict) and chunk.get("type") == "text"
                         and isinstance(chunk.get("text"), str) for chunk in chunks)
            if not echoed or "".join(chunk["text"] for chunk in chunks) != self.inputs[session]:
                for chunk in chunks:
                    self._message(session, "user_message_chunk", chunk)
        if kind in _MESSAGES:
            self._message(session, kind, update.get("content"))
        elif kind in ("tool_call", "tool_call_update"):
            self.items.pop(session, None)
            self._tool(session, update)
        else:
            self.items.pop(session, None)
            entries = update.get("entries")
            if not isinstance(entries, list) or any(
                not isinstance(entry, dict) or not isinstance(entry.get("content"), str)
                or not isinstance(entry.get("status"), str) or entry["status"] not in _STATUSES - {"failed"}
                or not isinstance(entry.get("priority"), str) or entry["priority"] not in _PRIORITIES for entry in entries
            ):
                self._marker(session, "plan")
            elif self.plans.get(session) != entries:
                self.record.append(Plan(entries=[PlanEntry(content=entry["content"],
                    status=entry["status"], priority=entry["priority"]) for entry in entries]))
                self.plans[session] = entries

    def _message(self, session: str, kind: str, content: object, *, marker: bool = False) -> None:
        native_type = content.get("type") if isinstance(content, dict) else None
        if native_type != "text" or not isinstance(content.get("text"), str):
            self._marker(session, native_type if isinstance(native_type, str) else "unknown-content")
            return
        previous = self.items.get(session)
        if previous is None or previous[0] != kind or marker:
            self.ordinal += 1
            previous = (kind, f"chunk-{self.ordinal}")
            self.items[session] = previous
        self.record.append(MessageChunk(session_update=kind, content=TextContent(text=content["text"]),
            meta=NativeMeta(native_item_id=previous[1], content_segment=self.prompts[session] + "/text",
                native_type=native_type, marker=True if marker else None,
                reasoning="text" if kind == "agent_thought_chunk" else None)))
        if marker:
            self.items.pop(session, None)

    def _marker(self, session: str, label: str) -> None:
        self._message(session, "agent_message_chunk", {"type": "text", "text": f"[{label}]"}, marker=True)

    def _tool(self, session: str, update: dict) -> None:
        call_id = update.get("toolCallId")
        if not isinstance(call_id, str) or not call_id or len(session) > 512:
            self._marker(session, "unidentified-tool-call")
            return
        key = (session, call_id)
        previous = self.tools.get(key, {})
        title = update.get("title", previous.get("title"))
        payload = {}
        if isinstance(title, str) and title:
            event = normalize_tool_event("dsh", {"nativeIdentity": {"sessionId": session},
                "callId": call_id, "toolName": title, "phase": "start"})
            payload.update(title=title, kind=event["category"])
        if isinstance(update.get("status"), str) and update["status"] in _STATUSES:
            payload["status"] = update["status"]
        for native, field in (("rawInput", "raw_input"), ("rawOutput", "raw_output")):
            if native in update:
                payload[field] = update[native]
        locations = update.get("locations")
        if isinstance(locations, list):
            payload["locations"] = [ToolLocation(path=location["path"]) for location in locations
                if isinstance(location, dict) and isinstance(location.get("path"), str)]
        metadata = NativeMeta(native_item_id=call_id, content_segment=self.prompts[session] + "/tool")
        if not previous and "title" in payload:
            self.record.append(ToolCall(tool_call_id=call_id, meta=metadata, **payload))
        else:
            changed = {name: value for name, value in payload.items() if previous.get(name) != value}
            if changed:
                self.record.append(ToolCallUpdate(tool_call_id=call_id, meta=metadata, **changed))
        self.tools[key] = {**previous, **payload}
        content = update.get("content")
        if not isinstance(content, list):
            return
        # ACP replaces the whole collection, including removing old segments.
        indices = {index for native_session, native_call, index in self.snapshots
                   if (native_session, native_call) == key}
        for index in sorted(indices | set(range(len(content)))):
            block = content[index] if index < len(content) else None
            snapshot_key = (*key, index)
            text = self._tool_text(block) if block is not None else ""
            before = self.snapshots.get(snapshot_key, "")
            if text == before:
                continue
            replace = (isinstance(block, dict) and block.get("type") == "diff") or not text.startswith(before)
            value = text if replace else text[len(before):]
            self.record.append(ToolCallUpdate(tool_call_id=call_id,
                content=[ToolContent(content=TextContent(text=value))], meta=NativeMeta(
                    native_item_id=call_id, content_segment=f"{self.prompts[session]}/content/{index}",
                    replace=True if replace else None)))
            self.snapshots[snapshot_key] = text

    @staticmethod
    def _tool_text(block: object) -> str:
        if not isinstance(block, dict):
            return "[unknown-tool-content]"
        if block.get("type") == "content":
            content = block.get("content")
            if isinstance(content, dict) and content.get("type") == "text" and isinstance(content.get("text"), str):
                return content["text"]
        if block.get("type") == "diff" and isinstance(block.get("path"), str) \
                and isinstance(block.get("newText"), str) \
                and (block.get("oldText") is None or isinstance(block["oldText"], str)):
            old_text = block.get("oldText") or ""
            return f'--- {block["path"]}\n{old_text}\n+++ {block["path"]}\n{block["newText"]}'
        label = block.get("type")
        return f'[{label if isinstance(label, str) else "unknown-tool-content"}]'

    def close(self) -> None:
        self.record.close()
