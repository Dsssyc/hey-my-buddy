"""Project this attempt's typed Codex items into the ACP v1 display record.

Codex CLI 0.162.1's offline item samples are the baseline. Raw response,
account and usage notifications are never display carriers. No native thread
or native storage is read by this projector.
"""
from __future__ import annotations

from ....protocol.session_content import (
    MessageChunk, NativeMeta, TextContent,
    ToolCall, ToolCallUpdate, ToolContent,
)
from ....protocol.tool_evidence import normalize_tool_event
from ..session_record import SessionRecord
from .tool_evidence import TYPED_TOOL_ITEMS, TYPED_TOOL_OUTPUTS, native_identity


class CodexSessionProjector:
    def __init__(self, record: SessionRecord):
        self.record = record
        self.roots: set[tuple[str, str]] = set()
        self.sent_inputs: dict[tuple[str, str], list[str]] = {}
        self.items: dict[tuple, dict] = {}
        self.text: dict[tuple, str] = {}
        self.snapshots: dict[tuple, str] = {}
        self.tools: dict[tuple, dict] = {}
        self.blocks: set[tuple] = set()
        self.pending_key: tuple | None = None
        self.pending: list[str] = []
        self.pending_update: tuple | None = None
        self.awaiting_root = False
        self.unbound: list[dict] = []
        self.late: list[dict] = []

    def observe_root(self, thread_id: str, turn_id: str, input_text: str, items: list) -> None:
        self.flush()
        root = (thread_id, turn_id)
        self.roots.add(root)
        self.sent_inputs[root] = [input_text]
        self.awaiting_root = False
        unbound, self.unbound = self.unbound, []
        for message in unbound:
            self.observe_notification(message)
        for item in items:
            self.observe_notification({"method": "item/started", "params": {
                "threadId": thread_id, "turnId": turn_id, "item": item}})

    def record_input(self, input_text: str, root: tuple[str, str] | None = None) -> None:
        """Called only after the native transport has sent this input."""
        self.flush()
        self.record.append(MessageChunk(session_update="user_message_chunk", content=TextContent(text=input_text)))
        if root is not None:
            self.sent_inputs[root].append(input_text)
        else:
            self.awaiting_root = True

    def observe_notification(self, message: dict) -> None:
        method, params = message.get("method"), message.get("params")
        if self.awaiting_root:
            # The receipt is the authority for identity. Keep only display
            # carriers until it arrives; the role's bounded startup buffer is
            # independent and must not impose a display retention limit.
            if method in ("item/started", "item/updated", "item/completed", "item/agentMessage/delta",
                          "item/reasoning/summaryTextDelta", "item/reasoning/textDelta",
                          "item/reasoning/summaryPartAdded", "item/commandExecution/outputDelta",
                          "turn/diff/updated", "turn/completed"):
                self.unbound.append(message)
            return
        identity = native_identity(params)
        if identity is None or (identity.get("sessionId"), identity.get("turnId")) not in self.roots:
            self.flush()
            return
        root = (identity["sessionId"], identity["turnId"])
        if method in ("item/started", "item/updated", "item/completed"):
            item = params.get("item")
            if (isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
                    and isinstance(item.get("type"), str)):
                self._item(root, method, item, identity)
        elif method in ("item/agentMessage/delta", "item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
            item_id, delta = params.get("itemId"), params.get("delta")
            if not isinstance(item_id, str) or not item_id or not isinstance(delta, str):
                return
            thought = method != "item/agentMessage/delta"
            segment = "summary" if method == "item/reasoning/summaryTextDelta" else "text"
            index = params.get("summaryIndex", 0) if segment == "summary" else params.get("contentIndex", 0)
            item = self.items.get((*root, item_id), {"id": item_id})
            meta = self._meta(item, reasoning=segment, part_index=index) if thought else self._meta(item)
            self._delta((*root, item_id, "reasoning-" + segment if thought else "message", index), delta,
                        "agent_thought_chunk" if thought else "agent_message_chunk", meta)
        elif method == "item/reasoning/summaryPartAdded":
            self.flush()
        elif method == "item/commandExecution/outputDelta":
            key = (*root, params.get("itemId"))
            if key in self.tools and isinstance(params.get("delta"), str):
                self._delta((*key, "output", 0), params["delta"], "tool", key[-1])
        elif method == "turn/diff/updated" and isinstance(params.get("diff"), str):
            key = (*root, params.get("itemId"))
            if key in self.tools:
                self._changed_content((*key, "diff", 0), params["diff"], "tool", key[-1])
            else:
                item_id = "turn/diff/updated:" + root[1]
                self._changed_content((*root, item_id, "diff", 0), "[turn/diff/updated]\n" + params["diff"],
                                      "agent_message_chunk", {"native_item_id": item_id,
                                      "native_type": "turn/diff/updated", "marker": True})
        elif method == "turn/completed":
            for item in params.get("turn", {}).get("items", []):
                self.observe_notification({"method": "item/completed", "params": {
                    "threadId": root[0], "turnId": root[1], "item": item}})
            self.flush()
        else:
            self.flush()

    @staticmethod
    def _meta(item: dict, **extra) -> dict:
        return {"native_item_id": item["id"], "native_type": item.get("type"),
                "phase": item.get("phase") if isinstance(item.get("phase"), str) else None, **extra}

    def _item(self, root: tuple, method: str, item: dict, identity: dict) -> None:
        key = (*root, item["id"])
        if self.pending_key is not None and self.pending_key[:3] != key:
            self.flush()
        self.items[key] = {**self.items.get(key, {}), **item}
        kind = item["type"]
        if kind == "userMessage":
            contents = item.get("content", [])
            # Native turn input is echoed as one userMessage. Consume the
            # exact sent text once; other native user input remains visible.
            text = "".join(block["text"] for block in contents if block.get("type") == "text")
            sent = self.sent_inputs[root]
            if text in sent:
                sent.remove(text)
                self.blocks.add((*key, "sent-input"))
            sent_echo = (*key, "sent-input") in self.blocks
            for index, block in enumerate(contents):
                block_key = (*key, "user", index)
                if block.get("type") == "text":
                    if not sent_echo:
                        self._snapshot(block_key, block["text"], "user_message_chunk", self._meta(item))
                elif block_key not in self.blocks:
                    self._marker((*key, "input", index), item, block.get("type", "unknown"))
                    self.blocks.add(block_key)
        elif kind == "agentMessage":
            if isinstance(item.get("text"), str):
                self._snapshot((*key, "message", 0), item["text"], "agent_message_chunk", self._meta(item))
        elif kind == "reasoning":
            for field, segment in (("summary", "summary"), ("content", "text")):
                for index, part in enumerate(item.get(field, [])):
                    text = part if isinstance(part, str) else part.get("text")
                    if isinstance(text, str):
                        self._snapshot((*key, "reasoning-" + segment, index), text, "agent_thought_chunk",
                                       self._meta(item, reasoning=segment, part_index=index))
        elif kind in TYPED_TOOL_ITEMS:
            self._tool(key, method, item, identity)
        elif kind in TYPED_TOOL_OUTPUTS and key in self.tools:
            self.flush()
            if "output" in item:
                previous = self.tools[key].get("rawOutput", {})
                output = {**previous, "output": item["output"]}
                if output != previous:
                    self.record.append(ToolCallUpdate(tool_call_id=item["id"], raw_output=output))
                    self.tools[key]["rawOutput"] = output
                if isinstance(item["output"], str):
                    self._snapshot((*key, "output", 0), item["output"], "tool", item["id"])
        else:
            self._marker(key, item, kind)

    def _marker(self, key: tuple, item: dict, label: str) -> None:
        text = item.get("text")
        if item["type"] in TYPED_TOOL_OUTPUTS:
            text = item.get("output")
        elif item["type"] == "hookPrompt":
            text = "\n".join(fragment["text"] for fragment in item.get("fragments", []))
        elif item["type"] in ("enteredReviewMode", "exitedReviewMode"):
            text = item.get("review")
        value = f"[{label}]" + ("\n" + text if isinstance(text, str) and text else "")
        self._changed_content((*key, "marker", 0), value, "agent_message_chunk", self._meta(item, marker=True))

    def _tool(self, key: tuple, method: str, item: dict, identity: dict) -> None:
        self.flush()
        call_id, kind = item["id"], item["type"]
        details = self.items[key]
        event = normalize_tool_event("codex", {"nativeIdentity": identity, "callId": call_id,
                                    "toolName": kind, "phase": "end" if method == "item/completed" else "start"})
        status = {"inProgress": "in_progress", "completed": "completed", "failed": "failed",
                  "declined": "failed", "interrupted": "failed", "cancelled": "failed"}.get(item.get("status"))
        if status is None and "status" not in item:
            status = "completed" if method == "item/completed" else "in_progress"
        if item.get("exitCode") is not None and item["exitCode"] != 0:
            status = "failed"
        raw_input = {name: details[name] for name in ("command", "cwd", "commandActions", "server", "tool", "arguments",
                                                "query", "path") if name in details}
        raw_output = {name: details[name] for name in ("aggregatedOutput", "output", "result", "error", "exitCode", "changes", "durationMs")
                      if name in details}
        payload = {"title": details.get("command") or details.get("tool") or kind, "kind": event["category"],
                   **({"status": status} if status is not None else {}),
                   **({"rawInput": raw_input} if raw_input else {}),
                   **({"rawOutput": raw_output} if raw_output else {})}
        changes = item.get("changes", [])
        if changes:
            payload["locations"] = [{"path": change["path"]} for change in changes]
        previous = self.tools.get(key)
        if previous is None:
            self.record.append(ToolCall(tool_call_id=call_id, **payload))
            self.tools[key] = payload
        else:
            changed = {name: value for name, value in payload.items() if previous.get(name) != value}
            if changed:
                self.record.append(ToolCallUpdate(tool_call_id=call_id, **changed))
                self.tools[key] = {**previous, **changed}
        if isinstance(item.get("aggregatedOutput"), str):
            self._snapshot((*key, "output", 0), item["aggregatedOutput"], "tool", call_id)
        if isinstance(item.get("output"), str):
            self._snapshot((*key, "output", 0), item["output"], "tool", call_id)
        for change in changes:
            if isinstance(change.get("diff"), str):
                self._changed_content((*key, "change", change["path"]), change["diff"], "tool", call_id)
        if item.get("status") in ("declined", "interrupted", "cancelled"):
            self._changed_content((*key, "status", 0), f"Codex: {item['status']}", "tool", call_id)

    def _changed_content(self, key: tuple, text: str, kind: str, meta) -> None:
        """A diff is a replacement snapshot, not an append-only text stream."""
        if self.snapshots.get(key) == text:
            return
        self.flush()
        self.snapshots[key] = text
        self._write_content(key, kind, meta, text, replace=True)

    def _snapshot(self, key: tuple, text: str, kind: str, meta) -> None:
        if self.pending_key != key:
            self.flush()
        previous = self.text.get(key, "")
        if not text.startswith(previous):
            self.flush()
            self.text[key] = text
            self._write_content(key, kind, meta, text, replace=True)
        else:
            self._delta(key, text[len(previous):], kind, meta)

    def _delta(self, key: tuple, text: str, kind: str, meta) -> None:
        if self.pending_key != key:
            self.flush()
        self.text[key] = self.text.get(key, "") + text
        if text:
            self.pending_key = key
            self.pending_update = (kind, meta)
            self.pending.append(text)

    def flush(self) -> None:
        if self.pending_key is None:
            return
        kind, meta = self.pending_update
        self._write_content(self.pending_key, kind, meta, "".join(self.pending))
        self.pending_key, self.pending_update, self.pending = None, None, []

    def _write_content(self, key: tuple, kind: str, meta, value: str, *, replace: bool = False) -> None:
        text = TextContent(text=value)
        segment = "/".join(str(part) for part in (key[1], *key[3:]))
        fields = ({"native_item_id": meta, "native_type": self.items[key[:3]]["type"]}
                  if kind == "tool" else meta)
        metadata = NativeMeta(**fields, content_segment=segment, replace=True if replace else None)
        if kind == "tool":
            self.record.append(ToolCallUpdate(tool_call_id=meta, content=[ToolContent(content=text)], meta=metadata))
        else:
            self.record.append(MessageChunk(session_update=kind, content=text, meta=metadata))

    def defer_notification(self, message: dict) -> None:
        self.late.append(message)

    def close(self, *, flush: bool = True) -> None:
        try:
            if flush:
                for message in self.late:
                    self.observe_notification(message)
                self.late.clear()
                self.flush()
        finally:
            self.record.close()
