"""Project the admitted ZCode app-server event stream, never native storage.

The versioned 0.16.9 usage fixture documents message parts; the mock server
exercises event envelopes. Neither establishes complete reasoning coverage.
"""
from __future__ import annotations

from ....protocol.session_content import (
    MessageChunk, NativeMeta, Plan, PlanEntry, TextContent,
    ToolCall, ToolCallUpdate, ToolContent,
)
from ....protocol.tool_evidence import normalize_tool_event
from ..session_record import SessionRecord

_CHILD_FIELDS = ("source", "parentToolCallId", "childSessionId", "childToolCallId", "agentId", "background")


class ZcodeSessionProjector:
    def __init__(self, record: SessionRecord):
        self.record = record
        self.prompts: dict[str, tuple[str, str, str]] = {}
        self.roots: dict[tuple[str, str], str] = {}
        self.text: dict[tuple[str, str, str], str] = {}
        self.tools: dict[tuple[str, str, str], dict] = {}
        self.messages: dict[tuple[str, str], str] = {}
        self.assistant: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
        self.plans: dict[tuple[str, str], list] = {}
        self.active_parts: dict[tuple[str, str], tuple[str, str]] = {}
        self.ordinal = 0

    def start_prompt(self, session_id: str, input_id: str, input_text: str, invocation_id: str) -> None:
        self.prompts[session_id] = (input_id, input_text, invocation_id)

    def observe_notification(self, message: dict) -> None:
        if not isinstance(message, dict) or message.get("method") != "session/event":
            return
        params = message.get("params")
        if not isinstance(params, dict):
            return
        session, turn = params.get("sessionId"), params.get("turnId")
        if not isinstance(session, str) or session not in self.prompts or not isinstance(turn, str) or not turn:
            return
        data = params.get("payload")
        if not isinstance(data, dict) or any(params.get(k) or data.get(k) for k in _CHILD_FIELDS):
            return
        root = (session, turn)
        kind = params.get("type")
        if kind == "turn.started":
            input_id, input_text, invocation = self.prompts[session]
            if data.get("inputId") == input_id and root not in self.roots:
                self.roots[root] = f"{invocation}/{session}/{turn}"
                self.record.append(MessageChunk(session_update="user_message_chunk", content=TextContent(text=input_text),
                    meta=NativeMeta(native_item_id=input_id, content_segment=self.roots[root] + "/input")))
            return
        if root not in self.roots:
            return
        if kind == "message.upserted":
            item = data.get("message")
            if not isinstance(item, dict):
                self._marker(root, "message.upserted")
                return
            info = item.get("info", item)
            if not isinstance(info, dict) or any(info.get(k) for k in _CHILD_FIELDS):
                return
            if info.get("sessionId", session) != session or info.get("turnId", turn) != turn:
                return
            role = info.get("role", "assistant")
            if role not in ("user", "assistant"):
                return
            parts = item.get("parts")
            if not isinstance(parts, list):
                self._marker(root, "message.upserted")
                return
            for index, part in enumerate(parts):
                self._part(root, part, delta=False, role=role, new_segment=index > 0)
        elif kind in ("part.started", "part.upserted", "part.delta"):
            self._part(root, data.get("delta") if kind == "part.delta" else data.get("part"),
                       delta=kind == "part.delta", new_segment=kind == "part.started")
        elif kind == "tool.updated":
            self.active_parts.pop(root, None)
            self._tool(root, data)
        elif kind == "turn.completed" and isinstance(data.get("response"), str):
            response = data["response"]
            keys = self.assistant.get(root, [])
            if response == "".join(self.text[key] for key in keys):
                return
            key = keys[0] if len(keys) == 1 else (self.roots[root], "turn-response", "text")
            self._text(key, response, "agent_message_chunk", snapshot=True)

    def _part(self, root: tuple[str, str], part: object, *, delta: bool, role: str = "assistant",
              new_segment: bool = False) -> None:
        if not isinstance(part, dict):
            self._marker(root, "unknown-part")
            return
        if any(part.get(k) for k in _CHILD_FIELDS):
            return
        if part.get("sessionId", root[0]) != root[0] or part.get("turnId", root[1]) != root[1]:
            return
        native_type = part.get("type")
        part_id = part.get("partId")
        if native_type == "plan":
            self._plan(root, part.get("entries"))
            return
        if native_type not in ("text", "reasoning", "thinking") or not isinstance(part.get("text"), str):
            self._marker(root, native_type if isinstance(native_type, str) else "unknown-part",
                         part_id if isinstance(part_id, str) and part_id else "unknown")
            return
        if not isinstance(part_id, str) or not part_id:
            # The existing minimal stream fixture has typed text deltas with
            # no part identity. As with ACP chunks, consecutive same-kind
            # fragments share a display item scoped to this invocation/root.
            content_kind = f"{role}/{native_type}"
            active = self.active_parts.get(root)
            if new_segment or active is None or active[0] != content_kind:
                self.ordinal += 1
                active = (content_kind, f"stream-{self.ordinal}")
                self.active_parts[root] = active
            part_id = active[1]
        kind = ("user_message_chunk" if role == "user" else
                "agent_message_chunk" if native_type == "text" else "agent_thought_chunk")
        key = (self.roots[root], part_id, native_type)
        if kind == "agent_message_chunk":
            message_id = part.get("messageId")
            if isinstance(message_id, str) and self.messages.get(root) != message_id:
                self.messages[root] = message_id
                self.assistant[root] = []
            keys = self.assistant.setdefault(root, [])
            if key not in keys:
                keys.append(key)
        # The root's sent prompt is already recorded once. An exact native echo
        # is redundant; other native user text remains display content.
        if role == "user" and part["text"] == self.prompts[root[0]][1]:
            return
        self._text(key, part["text"], kind, snapshot=not delta, native_type=native_type)

    def _text(self, key: tuple[str, str, str], value: str, kind: str, *, snapshot: bool,
              native_type: str | None = None, marker: bool = False) -> None:
        before = self.text.get(key, "")
        if snapshot and value == before:
            return
        replace = snapshot and not value.startswith(before)
        chunk = value if not snapshot or replace else value[len(before):]
        self.record.append(MessageChunk(session_update=kind, content=TextContent(text=chunk), meta=NativeMeta(
            native_item_id=key[1], content_segment=f"{key[0]}/{key[2]}",
            native_type=native_type, replace=True if replace else None, marker=True if marker else None,
            reasoning="text" if kind == "agent_thought_chunk" else None)))
        self.text[key] = value if snapshot else before + value

    def _marker(self, root: tuple[str, str], label: str, item_id: str = "unknown") -> None:
        self.active_parts.pop(root, None)
        self._text((self.roots[root], item_id, "marker"), f"[{label}]", "agent_message_chunk",
                   snapshot=True, native_type=label, marker=True)

    def _tool(self, root: tuple[str, str], data: dict) -> None:
        call_id, phase = data.get("toolCallId"), data.get("kind")
        if phase == "batch":
            return  # Acknowledgment of calls already projected individually.
        if not isinstance(call_id, str) or not call_id:
            self._marker(root, "unidentified-tool-call")
            return
        key = (self.roots[root], call_id, "tool")
        before = self.tools.get(key, {})
        payload = {}
        name = data.get("toolName") if phase == "scheduled" else before.get("title")
        if isinstance(name, str) and name:
            event = normalize_tool_event("zcode", {"nativeIdentity": {"sessionId": root[0], "turnId": root[1]},
                "callId": call_id, "toolName": name, "phase": "start"})
            payload.update(title=name, kind=event["category"])
        statuses = {"scheduled": "pending", "started": "in_progress", "progress": "in_progress",
                    "result": "completed", "error": "failed"}
        if not isinstance(phase, str) or phase not in statuses:
            self._marker(root, "unknown-tool-phase", call_id)
            return
        payload["status"] = statuses[phase]
        if "arguments" in data:
            payload["raw_input"] = data["arguments"]
        if phase == "result":
            payload["raw_output"] = data.get("result")
            if isinstance(data.get("result"), dict) and data["result"].get("success") is False:
                payload["status"] = "failed"
        elif phase == "error":
            payload["raw_output"] = data.get("error")
        metadata = NativeMeta(native_item_id=call_id, content_segment=key[0] + "/tool")
        if not before and "title" in payload:
            self.record.append(ToolCall(tool_call_id=call_id, meta=metadata, **payload))
        else:
            changed = {k: v for k, v in payload.items() if k not in before or before[k] != v}
            if changed:
                self.record.append(ToolCallUpdate(tool_call_id=call_id, meta=metadata, **changed))
        self.tools[key] = {**before, **payload}
        result = data.get("result")
        content = result.get("content") if isinstance(result, dict) else data.get("error")
        if isinstance(content, str):
            output_key = (*key[:2], "output")
            previous = self.text.get(output_key, "")
            if content != previous:
                replace = not content.startswith(previous)
                self.record.append(ToolCallUpdate(tool_call_id=call_id,
                    content=[ToolContent(content=TextContent(text=content if replace else content[len(previous):]))],
                    meta=NativeMeta(native_item_id=call_id, content_segment=key[0] + "/output",
                                    replace=True if replace else None)))
                self.text[output_key] = content

    def _plan(self, root: tuple[str, str], entries: object) -> None:
        if not isinstance(entries, list) or any(not isinstance(entry, dict)
                or not isinstance(entry.get("content"), str)
                or not isinstance(entry.get("status"), str) or entry["status"] not in ("pending", "in_progress", "completed")
                or not isinstance(entry.get("priority"), str) or entry["priority"] not in ("high", "medium", "low")
                for entry in entries):
            self._marker(root, "plan")
        elif self.plans.get(root) != entries:
            self.record.append(Plan(entries=[PlanEntry(**{k: entry[k] for k in ("content", "status", "priority")})
                                            for entry in entries]))
            self.plans[root] = entries

    def close(self) -> None:
        self.record.close()
