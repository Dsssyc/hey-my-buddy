"""Project the admitted ZCode app-server event stream, never native storage.

The projected event vocabulary is the one the installed ZCode app 0.17.0
really emitted in the recorded live and resumed turns of the session-content
probe (a loopback scripted model; ``tests/probes/zcode_native.py``): body text
streams as ``model.streaming`` ``text_delta`` frames and reasoning as
``reasoning_delta`` frames, both keyed by ``assistantMessageId``; tool input
travels in the ``model.streaming`` ``tool_call`` frame — the scheduled
``tool.updated`` repeats only ``inputOmitted`` — tool output in ``tool.updated``
``result`` frames, and the final response in ``turn.completed``.
``message.upserted`` and ``part.*`` events never appeared in those streams, so
neither is projected; the kinds no evidence shows stay named in this package's
``session_coverage.py``.
"""
from __future__ import annotations

from ....protocol.session_content import (
    MessageChunk, NativeMeta, TextContent, ToolCall, ToolCallUpdate, ToolContent,
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
        self.assistant: dict[tuple[str, str], list[tuple[str, str, str]]] = {}

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
        if kind == "model.streaming":
            if data.get("kind") == "tool_call":
                self._tool(root, data)
            else:
                self._stream(root, data)
        elif kind == "tool.updated":
            self._tool(root, data)
        elif kind == "turn.completed" and isinstance(data.get("response"), str):
            response = data["response"]
            keys = self.assistant.get(root, [])
            # The response is the final assistant message's own text: a streamed
            # final body is already recorded and an empty response carries none.
            if not response or keys and response == self.text[keys[-1]]:
                return
            self._text(keys[-1] if keys else (self.roots[root], "turn-response", "text"),
                       response, "agent_message_chunk", snapshot=True)

    def _stream(self, root: tuple[str, str], data: dict) -> None:
        """One streamed content frame of the model's current assistant message."""
        kind = data.get("kind")
        if kind not in ("text_delta", "reasoning_delta"):
            return  # Envelope and tool-input streaming frames carry no display content.
        delta = data.get("delta")
        if delta == "":
            return
        message_id = data.get("assistantMessageId")
        if not isinstance(message_id, str) or not message_id or not isinstance(delta, str):
            self._marker(root, f"model-streaming-{kind}")
            return
        native_type = "text" if kind == "text_delta" else "reasoning"
        key = (self.roots[root], message_id, native_type)
        if native_type == "text":
            keys = self.assistant.setdefault(root, [])
            if key not in keys:
                keys.append(key)
        # One assistant message streams all its reasoning into one native part
        # and all its body text into another, so both append per message id.
        self._text(key, delta, "agent_message_chunk" if native_type == "text" else "agent_thought_chunk",
                   snapshot=False, native_type=native_type)

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
        name = data.get("toolName") if phase in ("tool_call", "scheduled") else before.get("title")
        if isinstance(name, str) and name:
            event = normalize_tool_event("zcode", {"nativeIdentity": {"sessionId": root[0], "turnId": root[1]},
                "callId": call_id, "toolName": name, "phase": "start"})
            payload.update(title=name, kind=event["category"])
        statuses = {"tool_call": "pending", "scheduled": "pending", "started": "in_progress", "progress": "in_progress",
                    "result": "completed", "error": "failed"}
        if not isinstance(phase, str) or phase not in statuses:
            self._marker(root, "unknown-tool-phase", call_id)
            return
        payload["status"] = statuses[phase]
        if phase in ("tool_call", "scheduled") and "input" in data and "raw_input" not in before:
            payload["raw_input"] = data["input"]
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

    def close(self) -> None:
        self.record.close()
